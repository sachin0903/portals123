"""Seccl calls for the domain actions, read off the book.

The client, account, contribution, instruction, and transfer screens write the
Graphene row. These functions send that row. They do not invent an amount, a
scheme product, or an advice flag the book does not hold.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import regular_movements
from app.actions.attempt import OperationError
from app.integrations.morgan_lloyd.contacts import client_columns
from app.integrations.seccl.connection import configured, seccl_settings, why_not_configured
from app.integrations.seccl.enrol import _created_id, _iso


def _refused(exc: Exception) -> OperationError:
    """Map a Seccl refusal onto baton's retry decision.

    A 4xx (bar 429) is a refusal the request itself caused — a wrong or missing field — so another
    try with the same body will be refused again and the run parks for a person. A 5xx, a 429, or a
    lost answer (no status) is transient and worth another try. The status rides on the exception
    now; the message carries Seccl's own account of the refusal (``_explain``).
    """
    status = getattr(exc, "status", None)
    code = status if isinstance(status, int) else None
    permanent = code is not None and 400 <= code < 500 and code != 429
    return OperationError(str(exc), permanent=permanent, status_code=code)


_DRAWDOWN = (
    "A Seccl drawdown is a benefit crystallisation, and it needs advised, guidance, "
    "an FCA individual reference, benefits taken before 2006, and a protected retirement "
    "age or lump sum. None of those is on the book."
)
_TRADE = "A buy or sell needs an order on the account. This book does not hold one."
_MODEL_REMOVE = (
    "A Seccl model is set when the account is opened. Removing it from a live account "
    "would rebalance the holdings, and this book has no cash model to rebalance to."
)

# `client_columns` is a literal of our own column names.
_CLIENT_SQL = (
    "SELECT " + client_columns("c") + ", "  # noqa: S608
    "       c.vulnerability_is_temporary, c.bank_account, "
    "       pl.external_id AS seccl_id "
    "FROM crm.contacts c "
    "LEFT JOIN platform.links pl "
    "  ON pl.entity_type = 'contact' AND pl.entity_id = c.id "
    " AND pl.provider = 'seccl' AND pl.link_type = 'primary' AND pl.tenant_id = c.tenant_id "
    "WHERE c.id = :cid AND c.tenant_id = :t AND c.deleted_at IS NULL"
)

_ACCOUNT_LINK_SQL = """
SELECT a.id, a.name, a.payments, a.withdrawals, pl.external_id AS seccl_id,
       p.product_type::text AS product_type
FROM platform.accounts a
LEFT JOIN platform.products p ON p.id = a.product_id
LEFT JOIN platform.links pl
  ON pl.entity_type = 'account' AND pl.entity_id = a.id
 AND pl.provider = 'seccl' AND pl.link_type = 'primary' AND pl.tenant_id = a.tenant_id
WHERE a.id = :a AND a.tenant_id = :t
"""

_FEE_SQL = """
SELECT fp.external_id
FROM platform.account_fee_packages afp
JOIN platform.fee_packages fp
  ON fp.id = afp.fee_package_id AND fp.tenant_id = afp.tenant_id
WHERE afp.account_id = :a AND afp.tenant_id = :t
  AND fp.category = 'adviser' AND fp.external_id IS NOT NULL
ORDER BY afp.sort_order, afp.id
LIMIT 1
"""

_MODEL_SQL = """
SELECT mp.external_id
FROM platform.accounts a
JOIN platform.model_platforms mp
  ON mp.model_id = a.model_id AND mp.tenant_id = a.tenant_id
WHERE a.id = :a AND a.tenant_id = :t AND mp.provider::text = 'seccl'
LIMIT 1
"""

_OPEN_MONEY_SQL = """
SELECT data->>'origin' AS origin, data->>'origin_uid' AS origin_uid,
       data->>'origin_kind' AS kind, data->>'companion_of' AS companion_of
FROM platform.transactions
WHERE tenant_id = :t AND account_id = :a AND source_system = 'graphene'
  AND status IN ('pending', 'failed')
ORDER BY id
"""

_SEVERAL = "More than one movement is booked on this account. This action sends one row."


def initial_cash_amount(data: Any, payments: Any = None) -> float | None:
    """The journey's opening cash, when the application recorded one and nobody booked it."""
    deposit = regular_movements.initial_cash_deposit_of(data, payments)
    if deposit is None:
        return None
    raw = deposit.get("investment_amount")
    if raw is None or raw == "":
        return None
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return amount


def _obj(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return value if isinstance(value, dict) else {}


def _vulnerability(client: dict[str, Any]) -> dict[str, Any]:
    if client.get("is_vulnerable") is not True:
        return {"isVulnerable": False}
    reasons = client.get("vulnerability_reasons") or []
    if isinstance(reasons, str):
        reasons = [reasons]
    block: dict[str, Any] = {"isVulnerable": True}
    if reasons:
        block["reason"] = [str(reason) for reason in reasons]
    if client.get("vulnerability_is_temporary") is not None:
        block["isSupportTemporary"] = bool(client["vulnerability_is_temporary"])
    return block


def _bank(client: dict[str, Any]) -> dict[str, str] | None:
    bank = _obj(client.get("bank_account"))
    sort_code = str(bank.get("sort_code") or "").replace("-", "").strip()
    number = str(bank.get("account_number") or "").strip()
    if sort_code and number:
        return {"sortCode": sort_code, "accountNumber": number}
    return None


# Seccl's pension individual is the same ``/client``. These are required to open a
# pension, and "Update a client / Individual clients" is the PATCH that changes them.
# The labels are Seccl's own enum, and ours is the snake_case form of that list.
_EMPLOYMENT = {
    "employed": "Employed",
    "self_employed": "Self Employed",
    "retired": "Retired",
    "unemployed": "Unemployed",
    "education": "Education",
    "care_under_16s": "Care Under 16s",
    "care_over_16": "Care Over 16",
    "other": "Other",
}
_MARITAL = {
    "single": "Single",
    "married": "Married",
    "divorced": "Divorced",
    "widowed": "Widowed",
}
# Seccl's list splits Fixed and Individual by the tax year. The book stores the
# bare word, so those two are not sent. Certificate numbers are not on the book either.
_PROTECTION = {"primary": "Primary", "enhanced": "Enhanced"}
_AMBIGUOUS_PROTECTION = frozenset({"fixed", "individual"})


def circumstances_fields(client: dict[str, Any]) -> dict[str, Any]:
    """Employment, marital status, retirement age, and employer, when Seccl has a value."""
    body: dict[str, Any] = {}
    employment = _EMPLOYMENT.get(str(client.get("employment_status") or "").strip().lower())
    if employment:
        body["employmentStatus"] = employment
    marital = _MARITAL.get(str(client.get("marital_status") or "").strip().lower())
    if marital:
        body["maritalStatus"] = marital
    age = client.get("retirement_age")
    if age is not None and str(age).strip() != "":
        try:
            body["retirementAge"] = int(age)
        except (TypeError, ValueError):
            pass
    employer = _employer(client)
    if employer is not None:
        body["employers"] = [employer]
    return body


def _employer(client: dict[str, Any]) -> dict[str, Any] | None:
    name = str(client.get("employer_name") or "").strip()
    if not name:
        return None
    block: dict[str, Any] = {"companyName": name}
    number = str(client.get("employer_registration_number") or "").strip()
    if number:
        block["companyRegNo"] = number
    address = _obj(client.get("employer_address"))
    line1 = str(address.get("line1") or "").strip()
    if line1:
        detail: dict[str, str] = {
            "address1": line1,
            "country": _iso(address.get("country")) or "GB",
        }
        city = str(address.get("city") or "").strip()
        if city:
            detail["address2"] = city
        postcode = str(address.get("postcode") or "").strip()
        if postcode:
            detail["postCode"] = postcode
        block["addressDetail"] = detail
    return block


def product_detail_fields(client: dict[str, Any]) -> dict[str, Any]:
    """Flexi-access and the protections Seccl can name from this client."""
    detail: dict[str, Any] = {}
    taken = client.get("flexi_access_taken")
    if taken is not None:
        detail["flexibleBenefitsAccessed"] = bool(taken)
    when = client.get("flexi_access_date")
    if isinstance(when, date):
        detail["flexiAccessDate"] = f"{when.isoformat()}T00:00:00.000Z"
    elif isinstance(when, str) and len(when.strip()) >= 10:
        detail["flexiAccessDate"] = f"{when.strip()[:10]}T00:00:00.000Z"
    names = client.get("pension_protection") or []
    if isinstance(names, str):
        names = [names]
    items = [
        {"protectionType": mapped}
        for name in names
        if (mapped := _PROTECTION.get(str(name).strip().lower()))
    ]
    if items:
        detail["protection"] = items
    return detail


def ambiguous_protection(client: dict[str, Any]) -> str | None:
    names = client.get("pension_protection") or []
    if isinstance(names, str):
        names = [names]
    if any(str(name).strip().lower() in _AMBIGUOUS_PROTECTION for name in names):
        return (
            "Seccl records Fixed 2012, Fixed 2014, Fixed 2016, Individual 2014, "
            "and Individual 2016. This record does not name the year, and it holds "
            "no certificate reference."
        )
    return None


def client_patch(client: dict[str, Any], *, part: str) -> dict[str, Any]:
    """One slice of ``PATCH /client``: email, bank, vulnerability, or circumstances."""
    if part == "details":
        email = client.get("email")
        if not isinstance(email, str) or not email.strip():
            raise OperationError(
                "Seccl can update the email on an existing client. Address, date of birth, "
                "and nationality are set when the client is created.",
                permanent=True,
            )
        return {"email": email.strip()}
    if part == "bank":
        bank = _bank(client)
        if bank is None:
            raise OperationError(
                "This client has no sort code and account number to send.", permanent=True
            )
        return {"bankDetails": bank}
    if part == "vulnerability":
        return {"vulnerability": _vulnerability(client)}
    if part == "circumstances":
        fields = circumstances_fields(client)
        if fields:
            return fields
        marital = str(client.get("marital_status") or "").strip().lower()
        if marital and marital not in _MARITAL:
            raise OperationError(
                "Seccl records marital status as Single, Married, Divorced, or Widowed.",
                permanent=True,
            )
        raise OperationError(
            "This client has no employment status, marital status, or retirement age to send. "
            "Source of wealth and tax relief are not fields on a Seccl individual.",
            permanent=True,
        )
    raise OperationError("Seccl has no update for that part of the client.", permanent=True)


def account_name_patch(name: object) -> dict[str, str]:
    text_value = str(name or "").strip()
    if not text_value:
        raise OperationError("This account has no name to send.", permanent=True)
    return {"name": text_value}


def fee_patch(adviser_fee_id: str) -> dict[str, Any]:
    if not adviser_fee_id:
        raise OperationError("This account has no adviser fee with a Seccl id.", permanent=True)
    return {"feeStructures": {"adviserFeeId": adviser_fee_id}}


def _day(value: object) -> int | None:
    if isinstance(value, date):
        return value.day
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10]).day
        except ValueError:
            return None
    return None


def standing_entry(
    *, movement: str, amount: object, on: object, method: str | None = None
) -> dict[str, Any]:
    """One ``recurringPayments[]`` member. Seccl stores both directions in that array."""
    try:
        figure = float(amount)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        figure = 0
    day = _day(on)
    if figure <= 0 or day is None:
        raise OperationError(
            "A standing payment needs an amount and a day of the month.", permanent=True
        )
    entry: dict[str, Any] = {
        "paymentDay": day,
        "amount": figure,
        "movementType": movement,
    }
    if movement == "In":
        entry["method"] = method or "Bank Transfer"
        entry["autoInvest"] = True
    else:
        entry["method"] = method or "BACS Credit"
    return entry


def merge_standing(existing: list[Any], entry: dict[str, Any]) -> list[Any]:
    """Replace one direction and keep the other.

    A PATCH of ``recurringPayments`` replaces the whole array. Sending only the
    withdrawal would cancel the payment in.
    """
    movement = entry["movementType"]
    kept = [
        row
        for row in existing
        if isinstance(row, dict) and str(row.get("movementType") or "") != movement
    ]
    return [*kept, entry]


def standing_from_book(blob: Any, *, movement: str) -> dict[str, Any]:
    """The standing order captured on the account, or a refusal naming what is missing."""
    rows = regular_movements.regular_movements_of(blob)
    if len(rows) > 1:
        raise OperationError(
            "This account has more than one standing amount that way. Seccl takes one.",
            permanent=True,
        )
    row = rows[0] if rows else {}
    if not row.get("amount"):
        raise OperationError(
            "This account has no standing amount. Record it on the account first.",
            permanent=True,
        )
    frequency = str(row.get("frequency") or "monthly")
    if frequency == "single":
        raise OperationError(
            "That amount is a single payment, not a standing order.", permanent=True
        )
    if frequency != "monthly":
        raise OperationError(
            "Seccl standing payments are a day of the month. "
            f"{frequency.replace('_', ' ')} is not a day.",
            permanent=True,
        )
    # The register's own method codes (`direct_debit`) are not Seccl's words, so those take
    # Seccl's default rather than being sent as they are.
    method = row.get("method")
    return standing_entry(
        movement=movement,
        amount=row.get("amount"),
        on=row.get("next_date"),
        method=str(method) if method and method not in regular_movements.METHODS else None,
    )


def illustration_body(*, client_id: str, wrapper: str, node_id: str) -> dict[str, Any]:
    """``POST /illustration/product`` for a client who already exists."""
    if not client_id:
        raise OperationError("Create the Seccl client before illustrating.", permanent=True)
    if wrapper not in {"GIA", "ISA", "JISA", "PENSION"}:
        raise OperationError(
            f"Seccl cannot illustrate {wrapper or 'this product'}.", permanent=True
        )
    body: dict[str, Any] = {
        "calculating": "ASYNCHRONOUS",
        "wrapperType": wrapper,
        "clientInfo": {"id": client_id},
    }
    if node_id:
        body["nodeId"] = node_id
    return body


def _require_configured() -> None:
    if not configured():
        raise OperationError(why_not_configured(), permanent=True)


async def _client_row(session: AsyncSession, *, tenant_id: int, contact_id: int) -> dict[str, Any]:
    row = (
        (await session.execute(text(_CLIENT_SQL), {"cid": contact_id, "t": tenant_id}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise OperationError("That client was not found.", permanent=True)
    return dict(row)


async def _account_row(session: AsyncSession, *, tenant_id: int, account_id: int) -> dict[str, Any]:
    row = (
        (await session.execute(text(_ACCOUNT_LINK_SQL), {"a": account_id, "t": tenant_id}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise OperationError("That account was not found.", permanent=True)
    return dict(row)


async def _update_id(record: dict[str, Any]) -> str:
    update_id = str(record.get("updateId") or "")
    if not update_id:
        raise OperationError("Seccl did not return an update id.")
    return update_id


async def _pension_client_product(
    session: AsyncSession, *, tenant_id: int, contact_id: int
) -> str | None:
    """The pension client product stored on the client, reading it off an account if needed."""
    from app.integrations.seccl.enrol import client_product_link_type, store_client_product

    link_type = client_product_link_type("PENSION")
    found = (
        await session.execute(
            text(
                "SELECT external_id FROM platform.links "
                "WHERE tenant_id = :t AND provider = 'seccl' AND entity_type = 'contact' "
                "  AND entity_id = :id AND link_type = :lt"
            ),
            {"t": tenant_id, "id": contact_id, "lt": link_type},
        )
    ).scalar_one_or_none()
    if found:
        return str(found)
    account_external_id = (
        await session.execute(
            text(
                "SELECT pl.external_id FROM platform.accounts a "
                "JOIN platform.products p ON p.id = a.product_id "
                "JOIN platform.links pl "
                "  ON pl.entity_type = 'account' AND pl.entity_id = a.id "
                " AND pl.provider = 'seccl' AND pl.link_type = 'primary' "
                " AND pl.tenant_id = a.tenant_id "
                "WHERE a.tenant_id = :t AND a.contact_id = :c "
                "  AND p.product_type::text IN ('SIPP', 'PENSION', 'SSAS') "
                "ORDER BY a.id LIMIT 1"
            ),
            {"t": tenant_id, "c": contact_id},
        )
    ).scalar_one_or_none()
    if not account_external_id:
        return None
    await store_client_product(
        session,
        tenant_id=tenant_id,
        contact_id=contact_id,
        account_external_id=str(account_external_id),
    )
    found = (
        await session.execute(
            text(
                "SELECT external_id FROM platform.links "
                "WHERE tenant_id = :t AND provider = 'seccl' AND entity_type = 'contact' "
                "  AND entity_id = :id AND link_type = :lt"
            ),
            {"t": tenant_id, "id": contact_id, "lt": link_type},
        )
    ).scalar_one_or_none()
    return str(found) if found else None


async def update_circumstances(
    session: AsyncSession, *, tenant_id: int, contact_id: int
) -> dict[str, Any]:
    """PATCH the individual, and the pension client product when this client holds one."""
    client = await _client_row(session, tenant_id=tenant_id, contact_id=contact_id)
    fields = circumstances_fields(client)
    detail = product_detail_fields(client)
    if not fields and not detail:
        note = ambiguous_protection(client)
        if note:
            raise OperationError(note, permanent=True)
        client_patch(client, part="circumstances")
    seccl_id = str(client.get("seccl_id") or "")
    if fields and not seccl_id:
        raise OperationError("Create the Seccl client before updating it.", permanent=True)
    product_id = None
    if detail:
        product_id = await _pension_client_product(
            session, tenant_id=tenant_id, contact_id=contact_id
        )
        if not product_id:
            raise OperationError(
                "Open a Seccl pension for this client first. The client product id "
                "is kept on the client, one per wrapper.",
                permanent=True,
            )
    _require_configured()
    from seccl import SecclApiError, SecclClient

    request: dict[str, Any] = {}
    response: dict[str, Any] = {}
    try:
        async with SecclClient(seccl_settings()) as api:
            if fields:
                current = await api.client(seccl_id)
                request["client"] = fields
                response["client"] = await api.update_client(
                    seccl_id, await _update_id(current), fields
                )
            if detail and product_id:
                current = await api.client_product(product_id)
                token = await _update_id(current)
                body = {"productDetail": {**detail, "updateId": token}}
                request["clientProduct"] = body
                response["clientProduct"] = await api.update_client_product(product_id, token, body)
    except SecclApiError as exc:
        raise _refused(exc) from exc
    return {
        "id": product_id or seccl_id,
        "url": "/clientproduct" if detail else "/client",
        "request": request,
        "response": response,
    }


async def update_client(
    session: AsyncSession, *, tenant_id: int, contact_id: int, part: str
) -> dict[str, Any]:
    client = await _client_row(session, tenant_id=tenant_id, contact_id=contact_id)
    seccl_id = str(client.get("seccl_id") or "")
    if not seccl_id:
        raise OperationError("Create the Seccl client before updating it.", permanent=True)
    body = client_patch(client, part=part)
    _require_configured()
    from seccl import SecclApiError, SecclClient

    try:
        async with SecclClient(seccl_settings()) as api:
            current = await api.client(seccl_id)
            updated = await api.update_client(seccl_id, await _update_id(current), body)
    except SecclApiError as exc:
        raise _refused(exc) from exc
    return {"id": seccl_id, "request": body, "response": updated}


async def update_account(
    session: AsyncSession, *, tenant_id: int, account_id: int
) -> dict[str, Any]:
    account = await _account_row(session, tenant_id=tenant_id, account_id=account_id)
    seccl_id = str(account.get("seccl_id") or "")
    if not seccl_id:
        raise OperationError("Create the Seccl account before updating it.", permanent=True)
    body = account_name_patch(account.get("name"))
    _require_configured()
    from seccl import SecclApiError, SecclClient

    try:
        async with SecclClient(seccl_settings()) as api:
            current = await api.account(seccl_id)
            updated = await api.update_account(seccl_id, await _update_id(current), body)
    except SecclApiError as exc:
        raise _refused(exc) from exc
    return {"id": seccl_id, "request": body, "response": updated}


async def set_account_bank(
    session: AsyncSession, *, tenant_id: int, account_id: int
) -> dict[str, Any]:
    """Write the account's own bank account at Seccl (``PATCH /account`` with ``bankDetails``).

    Seccl holds account-level bank details in the same shape it holds the client's; the account's
    own entry is used when it differs from the client's. No bank on the account skips the send.

    ``PATCH /account`` takes a partial dict, so the send is just ``{"bankDetails": …}`` — it is
    not on the generated ``AccountUpdate`` model because Seccl's published spec omits it, though
    the API accepts it.
    """
    from app import bank_accounts

    account = await _account_row(session, tenant_id=tenant_id, account_id=account_id)
    seccl_id = str(account.get("seccl_id") or "")
    if not seccl_id:
        raise OperationError("Create the Seccl account before setting its bank.", permanent=True)
    entry, _source = await bank_accounts.resolve_for_account(session, account_id=account_id)
    bank = _bank({"bank_account": entry}) if entry else None
    if bank is None:
        return {"id": seccl_id, "skipped": "No bank details on the account."}
    _require_configured()
    from seccl import SecclApiError, SecclClient

    body = {"bankDetails": bank}
    try:
        async with SecclClient(seccl_settings()) as api:
            current = await api.account(seccl_id)
            updated = await api.update_account(seccl_id, await _update_id(current), body)
    except SecclApiError as exc:
        raise _refused(exc) from exc
    return {"id": seccl_id, "request": body, "response": updated}


async def _patch_account(
    session: AsyncSession, *, tenant_id: int, account_id: int, changes: dict[str, Any]
) -> dict[str, Any]:
    account = await _account_row(session, tenant_id=tenant_id, account_id=account_id)
    seccl_id = str(account.get("seccl_id") or "")
    if not seccl_id:
        raise OperationError("Create the Seccl account before changing it.", permanent=True)
    _require_configured()
    from seccl import SecclApiError, SecclClient

    try:
        async with SecclClient(seccl_settings()) as api:
            current = await api.account(seccl_id)
            if "recurringPayments" in changes:
                changes = {
                    **changes,
                    "recurringPayments": merge_standing(
                        current.get("recurringPayments") or [], changes["recurringPayments"][0]
                    ),
                }
            updated = await api.update_account(seccl_id, await _update_id(current), changes)
    except SecclApiError as exc:
        raise _refused(exc) from exc
    return {"id": seccl_id, "request": changes, "response": updated}


async def set_standing(
    session: AsyncSession, *, tenant_id: int, account_id: int, movement: str
) -> dict[str, Any]:
    account = await _account_row(session, tenant_id=tenant_id, account_id=account_id)
    blob = account["payments"] if movement == "In" else account["withdrawals"]
    entry = standing_from_book(blob, movement=movement)
    changes: dict[str, Any] = {"recurringPayments": [entry]}
    if movement == "Out":
        changes["autoDisinvest"] = "Proportional"
    return await _patch_account(
        session, tenant_id=tenant_id, account_id=account_id, changes=changes
    )


async def attach_fee(session: AsyncSession, *, tenant_id: int, account_id: int) -> dict[str, Any]:
    external_id = (
        await session.execute(text(_FEE_SQL), {"a": account_id, "t": tenant_id})
    ).scalar_one_or_none()
    return await _patch_account(
        session,
        tenant_id=tenant_id,
        account_id=account_id,
        changes=fee_patch(str(external_id or "")),
    )


async def attach_model(session: AsyncSession, *, tenant_id: int, account_id: int) -> dict[str, Any]:
    """Rebalance one account onto the model the book already names.

    The model id itself is sent on account create. This call moves holdings that
    are already there.
    """
    account = await _account_row(session, tenant_id=tenant_id, account_id=account_id)
    seccl_id = str(account.get("seccl_id") or "")
    if not seccl_id:
        raise OperationError(
            "Open the Seccl account first. The model is set on that create.", permanent=True
        )
    model_id = (
        await session.execute(text(_MODEL_SQL), {"a": account_id, "t": tenant_id})
    ).scalar_one_or_none()
    if not model_id:
        raise OperationError("This account's model has no Seccl id.", permanent=True)
    _require_configured()
    from seccl import SecclApiError, SecclClient

    try:
        async with SecclClient(seccl_settings()) as api:
            created = await api.rebalance(accountId=seccl_id)
    except SecclApiError as exc:
        raise _refused(exc) from exc
    identifier = None
    if isinstance(created, dict):
        identifier = created.get("id") or created.get("_id")
    return {"id": identifier, "response": created}


async def illustrate(session: AsyncSession, *, tenant_id: int, account_id: int) -> dict[str, Any]:
    account = await _account_row(session, tenant_id=tenant_id, account_id=account_id)
    contact_id = (
        await session.execute(
            text("SELECT contact_id FROM platform.accounts WHERE id = :a AND tenant_id = :t"),
            {"a": account_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if contact_id is None:
        raise OperationError("That account has no client.", permanent=True)
    client = await _client_row(session, tenant_id=tenant_id, contact_id=int(contact_id))
    from app.adviser_orgs import chosen_seccl_node

    product = str(account.get("product_type") or "").upper()
    wrapper = {"SIPP": "PENSION", "SSAS": "PENSION"}.get(product, product)
    node_id, _ = await chosen_seccl_node(session, tenant_id=tenant_id, contact_id=int(contact_id))
    body = illustration_body(
        client_id=str(client.get("seccl_id") or ""),
        wrapper=wrapper,
        node_id=node_id,
    )
    _require_configured()
    from seccl import SecclApiError, SecclClient

    try:
        async with SecclClient(seccl_settings()) as api:
            created = await api.product_illustration(body)
    except SecclApiError as exc:
        raise _refused(exc) from exc
    return {"id": _created_id(created), "request": body, "response": created}


def _row_matches(row: Any, kinds: set[str]) -> bool:
    """True when this open row is one of ``kinds`` and is not a fee taken off a payment."""
    if row["companion_of"]:
        return False
    origin = str(row["origin"] or "")
    kind = str(row["kind"] or "")
    if origin == "contribution" and "contribution" in kinds:
        return True
    if origin == "transfer" and "transfer" in kinds:
        return True
    return kind in kinds


def _money_candidates(rows: list[Any], kinds: set[str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for row in rows:
        origin = str(row["origin"] or "")
        uid = str(row["origin_uid"] or "")
        if not origin or not uid or not _row_matches(row, kinds):
            continue
        found.append((origin, uid))
    return found


async def send_booked(
    session: AsyncSession,
    *,
    tenant_id: int,
    account_id: int,
    kinds: set[str],
    missing: str,
    instruction_uid: str | None = None,
) -> dict[str, Any]:
    """Send one pending Graphene money row. Does not create one.

    ``instruction_uid`` is the row the door just booked. Without it, the only open row of
    ``kinds`` is sent, and more than one is refused — the newest row is not a guess.
    A fee booked as the sibling of a payment (``companion_of``) is not an ad hoc fee.
    """
    rows = list(
        (await session.execute(text(_OPEN_MONEY_SQL), {"t": tenant_id, "a": account_id}))
        .mappings()
        .all()
    )
    candidates = _money_candidates(rows, kinds)
    named = instruction_uid or ""
    if named:
        matched = next((item for item in candidates if item[1] == named), None)
        if matched is None:
            from app import outbound

            current = await outbound.get_by_origin(
                session, tenant_id=tenant_id, origin="instruction", origin_uid=named
            )
            if current is not None and str(current["status"]) in {"submitted", "posted"}:
                return {"status": "skipped", "detail": "Already instructed."}
            raise OperationError(missing, permanent=True)
    elif len(candidates) > 1:
        raise OperationError(_SEVERAL, permanent=True)
    elif len(candidates) == 1:
        matched = candidates[0]
    else:
        raise OperationError(missing, permanent=True)
    origin, uid = matched
    if origin == "contribution":
        from app import contributions

        record = await contributions.get_contribution(session, tenant_id=tenant_id, uid=uid)
        if record is None:
            raise OperationError("Contribution not found.", permanent=True)
        return await contributions.lodge_expectation(
            session, tenant_id=tenant_id, contribution=record
        )
    if origin == "transfer":
        from app import transfers

        record = await transfers.get_transfer(session, tenant_id=tenant_id, uid=uid)
        if record is None:
            raise OperationError("Transfer not found.", permanent=True)
        return await transfers.instruct_raised(session, tenant_id=tenant_id, record=record)
    from app import outbound

    return await outbound.dispatch(
        session, tenant_id=tenant_id, origin=origin, origin_uid=uid, account_id=account_id
    )
