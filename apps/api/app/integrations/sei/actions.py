"""SEI steps for a firm action. The registry wires these; it does not own them."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.attempt import Attempt, Handler, OperationError
from app.actions.money import (
    PAYMENT_MISSING,
    PAYMENT_SEVERAL,
    TRANSFER_MISSING,
    TRANSFER_SEVERAL,
    UNSENT_PAYMENTS,
    _booked_uid,
    select_named,
)
from app.actions.payload import _ids, transfer_attempt
from app.integrations.sei.cash_receipts import TREATMENT_CODE_FOR

WITHDRAWAL_MISSING = "Book the withdrawal on the account first. This action sends that row."
WITHDRAWAL_SEVERAL = (
    "More than one withdrawal is booked on this account. This action sends one row."
)
FEE_MISSING = "Book the fee on the account first. This action sends that row."
FEE_SEVERAL = "More than one fee is booked on this account. This action sends one row."
_UNSENT_WITHDRAWALS = """
FROM platform.instructions i
JOIN platform.accounts a ON a.id = i.account_id AND a.tenant_id = i.tenant_id
WHERE i.tenant_id = :t AND i.account_id = :a
  AND i.kind IN ('withdrawal', 'model_withdrawal') AND i.status = 'pending'
  AND NOT EXISTS (
    SELECT 1 FROM platform.transactions tx
    WHERE tx.tenant_id = i.tenant_id
      AND tx.source_system = 'graphene'
      AND tx.source_transaction_id = i.uid
      AND tx.data ->> 'origin' = 'instruction'
      AND tx.status IN ('submitted', 'posted')
  )
"""


async def _unsent_payments(session: AsyncSession, *, tenant_id: int, account_id: int) -> list[Any]:
    return list(
        (
            await session.execute(
                text(
                    "SELECT i.uid, i.amount::float8 AS amount, i.due_date, i.category, "
                    "       i.adviser_fee::float8 AS adviser_fee, i.adviser_fee_package_id, "
                    "       a.currency " + UNSENT_PAYMENTS + " ORDER BY i.id"
                ),
                {"t": tenant_id, "a": account_id},
            )
        )
        .mappings()
        .all()
    )


async def _book_opening_cash(session: AsyncSession, *, tenant_id: int, account_id: int) -> None:
    """The journey records an opening amount and does not book it. Book that one payment."""
    from app import instructions
    from app.integrations.seccl.instruct import initial_cash_amount

    stored = (
        (
            await session.execute(
                text(
                    "SELECT data, payments FROM platform.accounts WHERE id = :a AND tenant_id = :t"
                ),
                {"a": account_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    amount = initial_cash_amount(stored["data"], stored["payments"]) if stored else None
    if amount is None:
        return
    already = (
        await session.execute(
            text(
                "SELECT 1 FROM platform.transactions "
                "WHERE tenant_id = :t AND account_id = :a AND source_system = 'graphene' "
                "  AND data->>'origin_kind' = 'expected_in' AND status <> 'cancelled' "
                "LIMIT 1"
            ),
            {"t": tenant_id, "a": account_id},
        )
    ).first()
    if already is not None:
        return
    try:
        await instructions.create_instruction(
            session,
            tenant_id=tenant_id,
            created_by_workos=None,
            kind="expected_in",
            account_id=account_id,
            amount=amount,
            category="personal_contribution",
        )
    except (ValueError, LookupError) as exc:
        raise OperationError(str(exc), permanent=True) from exc


def select_booked_payment(
    rows: Sequence[Mapping[Any, Any]], *, instruction_uid: str | None
) -> Mapping[Any, Any]:
    """The instruction this run sends. A run with no uid sends the only pending payment."""
    return select_named(
        rows,
        named=instruction_uid,
        missing=PAYMENT_MISSING,
        several=PAYMENT_SEVERAL,
    )


def _attach_adviser_fee(
    context: dict[str, Any],
    adviser_fee: object,
    *,
    movement: str = "payment",
    fee_id: str | None = None,
) -> None:
    """Put the instruction's adviser fee on the receipt or disbursement, or refuse the send.

    The fee is part of this movement. Sending without it would move the money and leave the
    charge behind. SEI charges every fee against a fee TYPE: the adviser picks one (its code
    arrives as ``fee_id``), and only when none is chosen do we fall back to the firm's single
    initial advice fee.
    """
    if isinstance(adviser_fee, bool) or not isinstance(adviser_fee, int | float | str):
        charged = 0.0
    else:
        try:
            charged = float(adviser_fee)
        except ValueError:
            charged = 0.0
    if charged <= 0:
        return
    if not fee_id:
        from app.integrations.sei.fees import initial_advice_fee_id
        from app.lake.core import CoreUnavailable

        firm_id = str(context.get("firm_id") or "")
        try:
            fee_id = initial_advice_fee_id(firm_id) if firm_id else None
        except CoreUnavailable as exc:
            raise OperationError(
                f"This {movement} has an adviser fee, and the SEI catalogue could not be read "
                "to find the fee type.",
                permanent=False,
            ) from exc
    if not fee_id:
        raise OperationError(
            f"This {movement} has an adviser fee. Choose the fee type it is charged under — SEI "
            "charges a fee against one of the firm's fee types, not a bare amount.",
            permanent=True,
        )
    context["adviser_fee"] = charged
    context["fee_description_id"] = str(fee_id)


async def _adviser_fee_type_id(
    session: AsyncSession, *, tenant_id: int, fee_package_id: object
) -> str | None:
    """The SEI fee type the chosen adviser-fee package names — its ``external_id`` (the fee code the
    firm set in admin). None when no package is chosen, or the chosen one has no code set."""
    if fee_package_id is None:
        return None
    row = (
        (
            await session.execute(
                text(
                    "SELECT external_id FROM platform.fee_packages "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {"t": tenant_id, "id": fee_package_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise OperationError("That fee type is not on this firm's register.", permanent=True)
    external = str(row["external_id"] or "").strip()
    return external or None


def payment_category(value: object) -> str:
    """A contribution category the firm's treatment map understands. Anything else is absent."""
    category = str(value or "")
    if category in TREATMENT_CODE_FOR:
        return category
    return ""


async def sei_payment(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """Lodge the booked payment as an SEI anticipated receipt. Does not book a second one."""
    del subjects
    from app import contributions, outbound
    from app.integrations.sei.cash_receipts import (
        SeiNotConfigured,
        SeiUnavailable,
        create_anticipated_receipt,
    )

    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError(PAYMENT_MISSING, permanent=True)
    named = payload.get("instruction_uid")
    instruction_uid = named if isinstance(named, str) and named else None
    rows = await _unsent_payments(session, tenant_id=tenant_id, account_id=account_id)
    if not rows and instruction_uid is None:
        await _book_opening_cash(session, tenant_id=tenant_id, account_id=account_id)
        rows = await _unsent_payments(session, tenant_id=tenant_id, account_id=account_id)
    if instruction_uid and not any(str(item["uid"]) == instruction_uid for item in rows):
        lodged = (
            await session.execute(
                text(
                    "SELECT 1 FROM platform.transactions "
                    "WHERE tenant_id = :t AND source_system = 'graphene' "
                    "  AND source_transaction_id = :sid "
                    "  AND data ->> 'origin' = 'instruction' "
                    "  AND status IN ('submitted', 'posted')"
                ),
                {"t": tenant_id, "sid": instruction_uid},
            )
        ).first()
        if lodged is not None:
            return Attempt(
                ok=True,
                skipped=True,
                method="POST",
                url="/currencyreceiptservices/createcurrencyreceipt",
                response={"skipped": "Already instructed"},
            )
    row = select_booked_payment(rows, instruction_uid=instruction_uid)
    current = await outbound.get_by_origin(
        session, tenant_id=tenant_id, origin="instruction", origin_uid=str(row["uid"])
    )
    if current is not None:
        already = await outbound.sent_id(
            session,
            tenant_id=tenant_id,
            transaction_id=int(current["id"]),
            provider="sei",
            data=current.get("data") if isinstance(current.get("data"), dict) else None,
        )
        if already:
            return Attempt(
                ok=True,
                skipped=True,
                method="POST",
                url="/currencyreceiptservices/createcurrencyreceipt",
                response={"skipped": "Already instructed"},
            )
    category = payment_category(row.get("category"))
    context = await contributions.sei_context(
        session, tenant_id=tenant_id, account_id=account_id, category=category
    )
    due = row["due_date"]
    product = str(context.get("product_type") or "").upper()
    context.update(
        amount=float(row["amount"]),
        currency=str(row["currency"] or "GBP"),
        due_date=due,
        reference=str(row["uid"]),
        category="" if product in {"ISA", "JISA", "GIA"} else category,
    )
    fee_id = await _adviser_fee_type_id(
        session, tenant_id=tenant_id, fee_package_id=row.get("adviser_fee_package_id")
    )
    _attach_adviser_fee(context, row.get("adviser_fee"), fee_id=fee_id)
    if product in {"ISA", "JISA"} and not context.get("tax_year"):
        on = due if isinstance(due, date) else date.today()
        context["tax_year"] = contributions.tax_year_of(on)
    try:
        result = await create_anticipated_receipt(context)
    except ValueError as exc:
        raise OperationError(str(exc), permanent=True) from exc
    except SeiNotConfigured as exc:
        raise OperationError(str(exc), permanent=True) from exc
    except SeiUnavailable as exc:
        # A definite refusal (4xx: a wrong field, a bad reference) will not fix itself, so it parks.
        # A lost or 5xx answer is transient; the receipt is keyed by the instruction reference, so a
        # retry that re-lodges is safe. ``ambiguous`` carries that distinction from the HTTP status.
        # The call's evidence rides on the error so the attempt shows what was sent and refused.
        raise OperationError(
            str(exc),
            permanent=not exc.ambiguous,
            status_code=exc.status_code,
            response=exc.response,
            method="POST",
            url=exc.url,
            request=exc.request,
        ) from exc
    activity = result.get("activity_id")
    await outbound.mark_with_companion(
        session,
        tenant_id=tenant_id,
        origin="instruction",
        origin_uid=str(row["uid"]),
        current=current,
        status="submitted",
        provider="sei" if activity else None,
        external_id=str(activity) if activity else None,
    )
    result = await _note_activity(session, activity=activity, result=result)
    return Attempt(
        ok=True,
        method="POST",
        url="/currencyreceiptservices/createcurrencyreceipt",
        request=result.get("request"),
        response=result,
    )


_MONEY_HELD = "SEI may already have this payment. It was not sent again."
_MONEY_UNCONFIRMED = "SEI did not confirm this payment. It was not sent again."
_CLAIM_INFLIGHT = (
    "UPDATE platform.transactions "
    "SET data = jsonb_set(COALESCE(data, '{}'::jsonb), '{sei_inflight}', 'true'::jsonb), "
    "    updated_at = now() "
    "WHERE id = :i AND tenant_id = :t "
    "  AND status IN ('pending', 'failed') "
    "  AND COALESCE(data->>'sei_inflight', '') <> 'true' "
    "RETURNING id"
)
_RELEASE_INFLIGHT = (
    "UPDATE platform.transactions "
    "SET data = COALESCE(data, '{}'::jsonb) - 'sei_inflight', updated_at = now() "
    "WHERE id = :i AND tenant_id = :t AND status IN ('pending', 'failed') "
    "  AND data ? 'sei_inflight' "
    "RETURNING id"
)


def _already_with_sei(row: dict[str, Any]) -> bool:
    from app.integrations.sei.cash_receipts import money_out_held

    return row.get("status") in {"submitted", "posted"} or money_out_held(row.get("data"))


async def _claim_money_out(
    session: AsyncSession,
    *,
    tenant_id: int,
    current: dict[str, Any] | None,
    origin_uid: str,
    kind: str,
    account_id: int,
    amount: float,
    currency: str,
) -> dict[str, Any]:
    """Mark this instruction in flight before the money leaves.

    A lost answer used to look like a failed step, and the retry sent the
    payment again. The open-activities list does not carry our reference, so a
    second POST is refused until the flag is cleared by a definite refusal.

    The booking inserts the pending row in the request transaction, which has
    not committed yet. A second connection cannot see that row, and it cannot
    copy it either: the request still holds the unique key. Treating the miss
    as "already sent" refused every new withdrawal. The hold is a separate
    row, committed before the money call. A retry after this request rolls
    back mints a new uid; that uid is refused while the hold stands.
    """
    from app import outbound
    from app.db import system_transaction

    if current is not None and _already_with_sei(current):
        raise OperationError(_MONEY_HELD, permanent=True)
    row = current
    won = False
    on_caller = False
    async with system_transaction() as side:
        await side.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"sei-money-out:{tenant_id}:{account_id}:{kind}"},
        )
        held = (
            (
                await side.execute(
                    text(
                        "SELECT origin_uid FROM platform.sei_money_holds "
                        "WHERE tenant_id = :t AND account_id = :a AND kind = :k"
                    ),
                    {"t": tenant_id, "a": account_id, "k": kind},
                )
            )
            .mappings()
            .one_or_none()
        )
        if held is not None:
            same = str(held["origin_uid"]) == origin_uid
            done = None
            if not same:
                done = (
                    await side.execute(
                        text(
                            "SELECT 1 FROM platform.transactions "
                            "WHERE tenant_id = :t AND source_system = 'graphene' "
                            "  AND source_transaction_id = :sid "
                            "  AND status IN ('submitted', 'posted')"
                        ),
                        {"t": tenant_id, "sid": str(held["origin_uid"])},
                    )
                ).first()
            if same or done is None:
                raise OperationError(_MONEY_HELD, permanent=True)
            await side.execute(
                text(
                    "DELETE FROM platform.sei_money_holds "
                    "WHERE tenant_id = :t AND account_id = :a AND kind = :k"
                ),
                {"t": tenant_id, "a": account_id, "k": kind},
            )
        await side.execute(
            text(
                "INSERT INTO platform.sei_money_holds "
                "  (tenant_id, account_id, kind, origin_uid) "
                "VALUES (:t, :a, :k, :uid)"
            ),
            {"t": tenant_id, "a": account_id, "k": kind, "uid": origin_uid},
        )
        if row is None:
            row = await outbound.ensure_pending(
                side,
                tenant_id=tenant_id,
                origin="instruction",
                origin_uid=origin_uid,
                kind=kind,
                account_id=account_id,
                contact_id=None,
                amount=amount,
                currency=currency,
            )
        else:
            visible = (
                (
                    await side.execute(
                        text(
                            "SELECT status, data FROM platform.transactions "
                            "WHERE id = :i AND tenant_id = :t"
                        ),
                        {"i": row["id"], "t": tenant_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if visible is None:
                on_caller = True
            else:
                row = {**row, "status": visible["status"], "data": visible["data"]}
                if _already_with_sei(row):
                    raise OperationError(_MONEY_HELD, permanent=True)
        if row is not None and not on_caller:
            won = (
                await side.execute(
                    text(_CLAIM_INFLIGHT),
                    {"i": row["id"], "t": tenant_id},
                )
            ).first() is not None
        if not on_caller and (not won or row is None):
            raise OperationError(_MONEY_HELD, permanent=True)
    if on_caller and current is not None:
        row = current
        won = (
            await session.execute(
                text(_CLAIM_INFLIGHT),
                {"i": current["id"], "t": tenant_id},
            )
        ).first() is not None
        if not won:
            await _release_money_out(session, tenant_id=tenant_id, current=current)
            raise OperationError(_MONEY_HELD, permanent=True)
    if row is None:
        raise OperationError(_MONEY_HELD, permanent=True)
    data = dict(row.get("data") or {}) if isinstance(row.get("data"), dict) else {}
    data["sei_inflight"] = True
    data["origin_uid"] = origin_uid
    data["origin_kind"] = kind
    return {**row, "account_id": account_id, "data": data}


async def _release_money_out(
    session: AsyncSession, *, tenant_id: int, current: dict[str, Any] | None
) -> None:
    """SEI refused the payment, so a corrected instruction may be sent.

    A committed hold is cleared on its own connection: the request that heard
    the refusal is about to roll back. A hold that lives only on this
    session is cleared here, or the refusal would stay stuck on the booking.
    """
    if current is None:
        return
    from app.db import system_transaction

    raw = current.get("data")
    data = raw if isinstance(raw, dict) else {}
    origin_uid = str(data.get("origin_uid") or "")
    kind = str(data.get("origin_kind") or "")
    async with system_transaction() as side:
        await side.execute(
            text(
                "DELETE FROM platform.sei_money_holds "
                "WHERE tenant_id = :t AND ("
                "  origin_uid = :uid "
                "  OR (account_id = CAST(:a AS int) AND kind = :kind AND :kind <> '')"
                ")"
            ),
            {
                "t": tenant_id,
                "uid": origin_uid,
                "a": current.get("account_id"),
                "kind": kind,
            },
        )
        cleared = (
            await side.execute(
                text(_RELEASE_INFLIGHT),
                {"i": current["id"], "t": tenant_id},
            )
        ).first()
    if cleared is not None:
        return
    await session.execute(
        text(_RELEASE_INFLIGHT),
        {"i": current["id"], "t": tenant_id},
    )


async def sei_withdrawal(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """Pay the booked withdrawal to the account's SEI pay-to profile, on that profile's rail."""
    del subjects
    from app import bank_accounts, contributions, outbound
    from app.bank_accounts import sei_payment_method
    from app.integrations.sei.cash_receipts import (
        SeiNotConfigured,
        SeiUnavailable,
        create_cash_disbursement,
        resolve_disbursement_codes,
    )

    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError(WITHDRAWAL_MISSING, permanent=True)
    named = payload.get("instruction_uid")
    instruction_uid = named if isinstance(named, str) and named else None
    rows = list(
        (
            await session.execute(
                text(
                    "SELECT i.uid, i.kind, i.amount::float8 AS amount, "
                    "       i.adviser_fee::float8 AS adviser_fee, i.adviser_fee_package_id, "
                    "       i.auto_disinvest, a.currency " + _UNSENT_WITHDRAWALS + " ORDER BY i.id"
                ),
                {"t": tenant_id, "a": account_id},
            )
        )
        .mappings()
        .all()
    )
    row = select_named(
        rows,
        named=instruction_uid,
        missing=WITHDRAWAL_MISSING,
        several=WITHDRAWAL_SEVERAL,
    )
    current = await outbound.get_by_origin(
        session, tenant_id=tenant_id, origin="instruction", origin_uid=str(row["uid"])
    )
    if current is not None:
        already = await outbound.sent_id(
            session,
            tenant_id=tenant_id,
            transaction_id=int(current["id"]),
            provider="sei",
            data=current.get("data") if isinstance(current.get("data"), dict) else None,
        )
        if already:
            return Attempt(
                ok=True,
                skipped=True,
                method="POST",
                url="/currencydisbursementservices/createcashdisbursement",
                response={"skipped": "Already instructed"},
            )
    context = await contributions.sei_context(
        session, tenant_id=tenant_id, account_id=account_id, category=""
    )
    context.update(
        resolve_disbursement_codes(
            {"firm_id": context.get("firm_id")},
            product_type=context.get("product_type"),
        )
    )
    entry, _source = await bank_accounts.resolve_for_account(
        session, account_id=account_id, direction="pay_to"
    )
    profile = str((entry or {}).get("provider_ref") or "").strip()
    context.update(
        amount=float(row["amount"]),
        currency=str(row["currency"] or "GBP"),
        reference=str(row["uid"]),
        pay_to_profile_id=profile,
        payment_method=sei_payment_method(entry) if entry else "BACS",
    )
    # An adviser fee rides the disbursement (v12 feesAndChargesDetails), the same way it rides a
    # receipt on a payment — resolved before any money is claimed, so a fee we cannot place parks
    # the withdrawal instead of sending the cash and dropping the charge. Auto-disinvest maps to
    # raise-cash on the movement.
    fee_id = await _adviser_fee_type_id(
        session, tenant_id=tenant_id, fee_package_id=row.get("adviser_fee_package_id")
    )
    _attach_adviser_fee(context, row.get("adviser_fee"), movement="withdrawal", fee_id=fee_id)
    context["raise_cash"] = bool(row.get("auto_disinvest"))
    current = await _claim_money_out(
        session,
        tenant_id=tenant_id,
        current=current,
        origin_uid=str(row["uid"]),
        kind=str(row["kind"]),
        account_id=account_id,
        amount=float(row["amount"]),
        currency=str(row["currency"] or "GBP"),
    )
    try:
        result = await create_cash_disbursement(context)
    except ValueError as exc:
        await _release_money_out(session, tenant_id=tenant_id, current=current)
        raise OperationError(str(exc), permanent=True) from exc
    except SeiNotConfigured as exc:
        await _release_money_out(session, tenant_id=tenant_id, current=current)
        raise OperationError(str(exc), permanent=True) from exc
    except SeiUnavailable as exc:
        # Money out always parks (never auto-resend), but still record what was sent and refused.
        if exc.ambiguous:
            raise OperationError(
                _MONEY_UNCONFIRMED,
                permanent=True,
                status_code=exc.status_code,
                response=exc.response,
                method="POST",
                url=exc.url,
                request=exc.request,
            ) from exc
        await _release_money_out(session, tenant_id=tenant_id, current=current)
        raise OperationError(
            str(exc),
            permanent=True,
            status_code=exc.status_code,
            response=exc.response,
            method="POST",
            url=exc.url,
            request=exc.request,
        ) from exc
    activity = result.get("activity_id")
    await outbound.mark_with_companion(
        session,
        tenant_id=tenant_id,
        origin="instruction",
        origin_uid=str(row["uid"]),
        current=current,
        status="submitted",
        provider="sei" if activity else None,
        external_id=str(activity) if activity else None,
    )
    result = await _note_activity(session, activity=activity, result=result)
    return Attempt(
        ok=True,
        method="POST",
        url="/currencydisbursementservices/createcashdisbursement",
        request=result.get("request"),
        response=result,
    )


_UNSENT_FEES = """
FROM platform.instructions i
JOIN platform.accounts a ON a.id = i.account_id AND a.tenant_id = i.tenant_id
WHERE i.tenant_id = :t AND i.account_id = :a
  AND i.kind = 'adhoc_fee' AND i.status = 'pending'
  AND NOT EXISTS (
    SELECT 1 FROM platform.transactions tx
    WHERE tx.tenant_id = i.tenant_id
      AND tx.source_system = 'graphene'
      AND tx.source_transaction_id = i.uid
      AND tx.data ->> 'origin' = 'instruction'
      AND tx.status IN ('submitted', 'posted')
  )
"""


async def _one_time_fee_type(
    session: AsyncSession,
    *,
    tenant_id: int,
    firm_id: str,
    fee_package_id: object,
) -> str:
    """The one-time type for this charge. A named package is not replaced by the default."""
    from app.integrations.sei.fees import one_time_fee_id
    from app.lake.core import CoreUnavailable

    description: str | None = None
    code: str | None = None
    named = fee_package_id is not None
    if named:
        package = (
            (
                await session.execute(
                    text(
                        "SELECT name, external_id FROM platform.fee_packages "
                        "WHERE tenant_id = :t AND id = :id"
                    ),
                    {"t": tenant_id, "id": fee_package_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if package is None:
            raise OperationError(
                "That fee type is not on this firm's register.",
                permanent=True,
            )
        description = str(package["name"] or "")
        external = str(package["external_id"] or "").strip()
        code = external or None
    try:
        fee_id = one_time_fee_id(firm_id, description=description, code=code, named=named)
    except CoreUnavailable as exc:
        raise OperationError(
            "The SEI catalogue could not be read to find the fee type.",
            permanent=False,
        ) from exc
    if fee_id:
        return fee_id
    if named:
        raise OperationError(
            "This fee is not a one-time charge on this firm's SEI book.",
            permanent=True,
        )
    raise OperationError(
        "This firm's SEI book has no single one-time initial advice fee to charge.",
        permanent=True,
    )


async def sei_adhoc_fee(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """Charge the booked ad hoc fee as a one-time direct charge. Does not charge it twice."""
    del subjects
    from app import contributions, outbound
    from app.integrations.sei.cash_receipts import SeiNotConfigured, SeiUnavailable
    from app.integrations.sei.fees import ONE_TIME_FEE_URL, create_one_time_fee

    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError(FEE_MISSING, permanent=True)
    named = payload.get("instruction_uid")
    instruction_uid = named if isinstance(named, str) and named else None
    rows = list(
        (
            await session.execute(
                text(
                    "SELECT i.uid, i.kind, i.amount::float8 AS amount, i.due_date, "
                    "i.fee_package_id, a.currency " + _UNSENT_FEES + " ORDER BY i.id"
                ),
                {"t": tenant_id, "a": account_id},
            )
        )
        .mappings()
        .all()
    )
    if instruction_uid and not any(str(item["uid"]) == instruction_uid for item in rows):
        lodged = (
            await session.execute(
                text(
                    "SELECT 1 FROM platform.transactions "
                    "WHERE tenant_id = :t AND source_system = 'graphene' "
                    "  AND source_transaction_id = :sid "
                    "  AND data ->> 'origin' = 'instruction' "
                    "  AND status IN ('submitted', 'posted')"
                ),
                {"t": tenant_id, "sid": instruction_uid},
            )
        ).first()
        if lodged is not None:
            return Attempt(
                ok=True,
                skipped=True,
                method="POST",
                url=ONE_TIME_FEE_URL,
                response={"skipped": "Already instructed"},
            )
    row = select_named(rows, named=instruction_uid, missing=FEE_MISSING, several=FEE_SEVERAL)
    current = await outbound.get_by_origin(
        session, tenant_id=tenant_id, origin="instruction", origin_uid=str(row["uid"])
    )
    if current is not None:
        already = await outbound.sent_id(
            session,
            tenant_id=tenant_id,
            transaction_id=int(current["id"]),
            provider="sei",
            data=current.get("data") if isinstance(current.get("data"), dict) else None,
        )
        if already:
            return Attempt(
                ok=True,
                skipped=True,
                method="POST",
                url=ONE_TIME_FEE_URL,
                response={"skipped": "Already instructed"},
            )
    context = await contributions.sei_context(
        session, tenant_id=tenant_id, account_id=account_id, category=""
    )
    firm_id = str(context.get("firm_id") or "")
    context.update(
        amount=float(row["amount"]),
        currency=str(row["currency"] or "GBP"),
        due_date=row["due_date"],
    )
    if firm_id:
        context["fee_type"] = await _one_time_fee_type(
            session,
            tenant_id=tenant_id,
            firm_id=firm_id,
            fee_package_id=row.get("fee_package_id"),
        )
    current = await _claim_money_out(
        session,
        tenant_id=tenant_id,
        current=current,
        origin_uid=str(row["uid"]),
        kind=str(row["kind"]),
        account_id=account_id,
        amount=float(row["amount"]),
        currency=str(row["currency"] or "GBP"),
    )
    try:
        result = await create_one_time_fee(context)
    except ValueError as exc:
        await _release_money_out(session, tenant_id=tenant_id, current=current)
        raise OperationError(str(exc), permanent=True) from exc
    except SeiNotConfigured as exc:
        await _release_money_out(session, tenant_id=tenant_id, current=current)
        raise OperationError(str(exc), permanent=True) from exc
    except SeiUnavailable as exc:
        # Money out always parks (never auto-resend), but still record what was sent and refused.
        if exc.ambiguous:
            raise OperationError(
                _MONEY_UNCONFIRMED,
                permanent=True,
                status_code=exc.status_code,
                response=exc.response,
                method="POST",
                url=exc.url,
                request=exc.request,
            ) from exc
        await _release_money_out(session, tenant_id=tenant_id, current=current)
        raise OperationError(
            str(exc),
            permanent=True,
            status_code=exc.status_code,
            response=exc.response,
            method="POST",
            url=exc.url,
            request=exc.request,
        ) from exc
    activity = result.get("activity_id")
    await outbound.mark_with_companion(
        session,
        tenant_id=tenant_id,
        origin="instruction",
        origin_uid=str(row["uid"]),
        current=current,
        status="submitted",
        provider="sei" if activity else None,
        external_id=str(activity) if activity else None,
    )
    result = await _note_activity(session, activity=activity, result=result)
    return Attempt(
        ok=True,
        method="POST",
        url=ONE_TIME_FEE_URL,
        request=result.get("request"),
        response=result,
    )


_FEE_PACKAGE_SQL = """
SELECT fp.external_id, fp.name
FROM platform.account_fee_packages afp
JOIN platform.fee_packages fp
  ON fp.id = afp.fee_package_id AND fp.tenant_id = afp.tenant_id
WHERE afp.account_id = :a AND afp.tenant_id = :t
  AND fp.category = 'adviser' AND fp.external_id IS NOT NULL
ORDER BY afp.sort_order, afp.id
LIMIT 1
"""

FEE_ATTACH_URL = "/feepackageassignmentaccount/feePackageAssignmentAccount"


def _fee_profile_assigned(chars: dict[str, Any], profile_id: str) -> bool:
    """True when the account already carries this fee package profile at SEI — do not re-assign."""
    info = chars.get("accountFeeInfoDetails") if isinstance(chars, dict) else None
    packages = info.get("accountFeePackageInfoList") if isinstance(info, dict) else None
    if not isinstance(packages, list):
        return False
    return any(
        str(item.get("feePackageProfileId") or "") == profile_id
        for item in packages
        if isinstance(item, dict)
    )


async def sei_attach_fee(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """Put the account's ongoing (adviser) fee package on it at SEI.

    The package is the one assigned on the account (``platform.account_fee_packages``); its
    ``external_id`` is the SEI ``feePackageProfileId`` (from the conformed fee-package sync). SEI's
    assign takes the numeric ``accountId`` — NOT the account number — which is read from
    ``retrieveAccountDetails``; an account already on the package is skipped, not re-assigned."""
    del subjects
    from app import contributions
    from app.integrations.sei.cash_receipts import (
        _api_error_body,
        _settings,
        configured,
        response_was_lost,
    )

    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError("Attaching a SEI fee needs the account.", permanent=True)
    pkg = (
        (await session.execute(text(_FEE_PACKAGE_SQL), {"a": account_id, "t": tenant_id}))
        .mappings()
        .one_or_none()
    )
    if pkg is None or not pkg["external_id"]:
        return Attempt(
            ok=True,
            skipped=True,
            method="POST",
            url=FEE_ATTACH_URL,
            response={"skipped": "No SEI fee package is assigned to this account."},
        )
    profile_id = str(pkg["external_id"])
    ctx = await contributions.sei_context(
        session, tenant_id=tenant_id, account_id=account_id, category=""
    )
    firm_id = str(ctx.get("firm_id") or "")
    number = str(ctx.get("sei_account_number") or "")
    portfolio = ctx.get("sei_portfolio_id")
    if not firm_id:
        raise OperationError("SEI still needs the firm's SEI firm id.", permanent=True)
    if not number:
        raise OperationError("This account has no SEI account number yet.", permanent=True)
    if not configured():
        raise OperationError("SEI is unavailable for this firm. Contact support.", permanent=True)
    from sei import SeiApiError, SeiAuthError, SeiClient, SeiConfigError

    account_entity: Any = None
    try:
        async with SeiClient(_settings()) as api:
            chars = await api.account_characteristics(firm_id, number)
            if _fee_profile_assigned(chars, profile_id):
                return Attempt(
                    ok=True,
                    skipped=True,
                    method="POST",
                    url=FEE_ATTACH_URL,
                    response={"skipped": "This fee package is already on the account at SEI."},
                )
            details = await api.account_details(
                firm_id,
                number,
                include_relationships=False,
                include_pay_to=False,
                include_pay_for=False,
                include_payers=False,
            )
            overview = details.get("accountOverviewDetail")
            account_entity = overview.get("accountId") if isinstance(overview, dict) else None
            if not account_entity:
                # The SFTP account is known but SEI has not returned its id — retry once it is open.
                raise OperationError(
                    "SEI has not returned this account's id yet. Try again once it is open.",
                    permanent=False,
                )
            result = await api.assign_package_to_account(
                account_entity, profile_id, collection_portfolio_id=portfolio or None
            )
    except SeiConfigError as exc:
        raise OperationError(str(exc), permanent=True) from exc
    except SeiAuthError as exc:
        raise OperationError(
            "SEI could not accept this request.",
            permanent=True,
            method="POST",
            url=FEE_ATTACH_URL,
        ) from exc
    except SeiApiError as exc:
        status = getattr(exc, "status", None)
        code = status if isinstance(status, int) else None
        raise OperationError(
            f"SEI could not attach the fee (HTTP {code})."
            if code
            else "SEI did not confirm the fee attach.",
            permanent=not response_was_lost(code),
            status_code=code,
            method="POST",
            url=FEE_ATTACH_URL,
            request={"accountId": account_entity, "feePackageProfileId": profile_id},
            response=_api_error_body(exc),
        ) from exc
    return Attempt(
        ok=True,
        method="POST",
        url=FEE_ATTACH_URL,
        request={
            "accountId": account_entity,
            "feePackageProfileId": profile_id,
            "collectionPortfolioId": portfolio,
        },
        response=result,
    )


async def _note_activity(
    session: AsyncSession, *, activity: Any, result: dict[str, Any]
) -> dict[str, Any]:
    """Read the activity SEI just created and attach its state to the lodge result.

    The money is already accepted. A status read that fails is logged inside
    ``note_lodged`` and the lodge stands.
    """
    if not activity:
        return result
    from app.integrations.sei.receipt_status import note_lodged

    noted = await note_lodged(
        session,
        activity_id=str(activity),
        event_id=str(result["event_id"]) if result.get("event_id") else None,
    )
    if noted:
        return {**result, "sei": noted}
    return result


async def _sei_account_details(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del session, payload, subjects
    raise OperationError(
        "SEI sets the account name when the client and account are enrolled together. "
        "There is no later rename on this path.",
        permanent=True,
    )


async def _sei_account_bank(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del session, payload, subjects
    raise OperationError(
        "SEI pays from a pay-to profile on the account. The sync copies profiles SEI "
        "already holds onto this account. This action does not create one.",
        permanent=True,
    )


def _sei_client(part: str) -> Handler:
    async def handler(
        session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
    ) -> Attempt:
        del subjects
        from app.integrations.seccl.instruct import _client_row
        from app.integrations.sei.cash_receipts import _settings, configured
        from app.integrations.sei.enrol import profile_update

        tenant_id, account_id, contact_id = await _ids(payload)
        if contact_id is None and account_id is not None:
            contact_id = (
                await session.execute(
                    text(
                        "SELECT contact_id FROM platform.accounts WHERE id = :a AND tenant_id = :t"
                    ),
                    {"a": account_id, "t": tenant_id},
                )
            ).scalar_one_or_none()
        if contact_id is None:
            raise OperationError("Updating a SEI client needs the client.", permanent=True)
        linked = (
            await session.execute(
                text(
                    "SELECT 1 FROM platform.links "
                    "WHERE tenant_id = :t AND provider = 'sei' AND entity_type = 'contact' "
                    "  AND entity_id = :id AND link_type = 'primary'"
                ),
                {"t": tenant_id, "id": int(contact_id)},
            )
        ).first()
        if linked is None:
            raise OperationError("Enrol the client at SEI before updating them.", permanent=True)
        client = await _client_row(session, tenant_id=tenant_id, contact_id=int(contact_id))
        body = profile_update(client, part=part)
        config = (
            await session.execute(
                text(
                    "SELECT config FROM platform.tenant_platforms "
                    "WHERE tenant_id = :t AND provider = 'sei'"
                ),
                {"t": tenant_id},
            )
        ).scalar_one_or_none()
        firm_id = config.get("firm_id") if isinstance(config, dict) else None
        if not firm_id:
            raise OperationError("SEI still needs the firm's SEI firm id.", permanent=True)
        if not configured():
            raise OperationError("SEI is not configured.", permanent=True)
        from sei import SeiApiError, SeiClient

        settings = _settings()
        try:
            async with SeiClient(settings) as api:
                updated = await api.update_client_profile(
                    firm_id,
                    body,
                    external_party_id=str(client.get("uid") or ""),
                    platform_user="Graphene",
                )
        except SeiApiError as exc:
            raise OperationError(str(exc)) from exc
        return Attempt(
            ok=True,
            method="POST",
            url="/updateclientbasicsprofileservice/updateClientBasicProfile",
            request=body,
            response=updated,
        )

    return handler


async def _align_opened(
    api: Any,
    firm_id: str,
    status: dict[str, Any],
    names: list[str],
) -> list[dict[str, Any]] | None:
    """Read each new account and pair it by the name SEI stored.

    The identifier list does not follow the request, so position is not a join.
    A failed read leaves the numbers unstored rather than attached to the wrong account.
    """
    from sei import SeiApiError

    from app.integrations.sei.enrol import opened_identifiers, pair_by_name

    _client_id, opened = opened_identifiers(status)
    if not opened:
        return None
    named: list[tuple[str, dict[str, Any]]] = []
    for item in opened:
        number = str(item.get("accountNumber") or "")
        if not number:
            return None
        try:
            details = await api.account_details(
                firm_id,
                number,
                include_relationships=False,
                include_pay_to=False,
                include_pay_for=False,
                include_payers=False,
            )
        except SeiApiError:
            return None
        overview = details.get("accountOverviewDetail")
        short = str(overview.get("accountShortName") or "") if isinstance(overview, dict) else ""
        named.append((short, item))
    paired = pair_by_name(names, named)
    return paired or None


async def _permit_tenant_currencies(
    session: AsyncSession,
    api: Any,
    *,
    tenant_id: int,
    firm_id: str,
    account_ids: list[int],
    accounts: dict[int, dict[str, Any]],
    opened: list[dict[str, Any]] | None,
) -> tuple[list[str], list[str]]:
    """Add the firm's other currencies once the account number exists.

    Returns ``(added, omitted)``. One currency is already the account base.
    Each further code is its own call: SEI rejects the whole request when any
    code is not a currency it will add, and those codes are omitted. Any other
    refusal is raised for this step alone.
    """
    from sei import SeiApiError

    from app.integrations.sei.enrol import allowable_currencies, currency_unavailable

    chosen = opened or []
    if not firm_id or len(chosen) != len(account_ids):
        return [], []
    allowed = (
        await session.execute(
            text("SELECT allowed_currencies FROM auth.tenants WHERE id = :t"),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()
    added: list[str] = []
    omitted: list[str] = []
    for account_id, item in zip(account_ids, chosen, strict=True):
        number = str(item.get("accountNumber") or "")
        codes = allowable_currencies(allowed, accounts[account_id].get("currency"))
        if not number or not codes:
            continue
        for code in codes:
            try:
                await api.add_allowable_currencies(firm_id, number, [code])
                added.append(code)
            except SeiApiError as exc:
                detail = str(exc).lower()
                if "already part of" in detail and "allowable currency" in detail:
                    continue
                if currency_unavailable(detail):
                    omitted.append(code)
                    continue
                raise OperationError(
                    f"SEI could not add {code} as an allowable currency: {exc}",
                    permanent=False,
                ) from exc
    return added, omitted


_CURRENCY_URL = "/addallowablecurrencyservice/allowableCurrencies/addAllowableCurrencyToAccount"
_PAY_TO_URL = "/currencyrecipientforaccountservicesv2service/createpaytorecipientforaccount2"
# SEI mints the account number while the party is still inside enrolment. A pay-to
# sent then is refused (field 1193, account status Work in Progress). This waits
# about two minutes, then the step fails on its own. A retry polls again and does
# not enrol again. Longer than that holds the request that is still committing
# the enrolment step.
PAY_TO_READY_POLLS = 24
PAY_TO_READY_PAUSE_SECONDS = 5


async def sei_account_currencies(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """Permit the firm's other currencies on accounts this enrolment has opened.

    The enrolment step writes the SEI account number. This step reads it. A
    currency SEI will not add is recorded and left off; it does not reopen the
    enrolment.
    """
    del subjects
    import json

    from sei import SeiClient

    from app.integrations.sei.cash_receipts import _settings, configured

    tenant_id = payload.get("tenant_id")
    if tenant_id is None:
        raise OperationError("This action needs the firm.", permanent=True)
    tenant_id = int(tenant_id)
    raw_ids = payload.get("account_ids")
    if isinstance(raw_ids, list) and raw_ids:
        account_ids = [int(item) for item in raw_ids]
    else:
        _tenant, account_id, _contact = await _ids(payload)
        if account_id is None:
            raise OperationError("This action needs the account.", permanent=True)
        account_ids = [account_id]
    if not configured():
        raise OperationError("SEI is not configured.", permanent=True)
    rows = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.currency, pl.external_id AS sei_account "
                    "FROM platform.accounts a "
                    "LEFT JOIN platform.links pl "
                    "  ON pl.entity_type = 'account' AND pl.entity_id = a.id "
                    " AND pl.provider = 'sei' AND pl.link_type = 'primary' "
                    " AND pl.tenant_id = a.tenant_id "
                    "WHERE a.tenant_id = :t AND a.id = ANY(:ids)"
                ),
                {"t": tenant_id, "ids": account_ids},
            )
        )
        .mappings()
        .all()
    )
    by_id = {int(row["id"]): row for row in rows}
    opened: list[dict[str, Any]] = []
    accounts: dict[int, dict[str, Any]] = {}
    for account_id in account_ids:
        row = by_id.get(account_id)
        number = str(row["sei_account"] or "") if row is not None else ""
        if not number:
            raise OperationError(
                "SEI has not opened this account yet, so its currencies cannot be added.",
                permanent=False,
            )
        opened.append({"accountNumber": number})
        accounts[account_id] = {"currency": row["currency"] if row else None}
    raw = (
        await session.execute(
            text(
                "SELECT config FROM platform.tenant_platforms "
                "WHERE tenant_id = :t AND provider = 'sei'"
            ),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()
    config = json.loads(raw) if isinstance(raw, str) else raw
    firm = str(config.get("firm_id") or "") if isinstance(config, dict) else ""
    if not firm:
        raise OperationError("SEI still needs the firm's id.", permanent=True)
    async with SeiClient(_settings()) as api:
        added, omitted = await _permit_tenant_currencies(
            session,
            api,
            tenant_id=tenant_id,
            firm_id=firm,
            account_ids=account_ids,
            accounts=accounts,
            opened=opened,
        )
    if not added and not omitted:
        return Attempt(
            ok=True,
            skipped=True,
            method="POST",
            url=_CURRENCY_URL,
            response={"skipped": "No further currencies"},
        )
    return Attempt(
        ok=True,
        method="POST",
        url=_CURRENCY_URL,
        response={"added": added, "currencies_unavailable": omitted},
    )


async def wait_until_accounts_open(
    api: Any,
    firm_id: str,
    account_numbers: Sequence[str],
    *,
    polls: int = PAY_TO_READY_POLLS,
    pause_seconds: float = PAY_TO_READY_PAUSE_SECONDS,
    sleep: Any = asyncio.sleep,
    what: str = "pay-to",
) -> None:
    """Read each account until SEI reports it Open, then return.

    A Work in Progress account, including WIP Submitted, or a read refused
    because the party is still being enrolled, waits and is read again. A
    status that will not become Open fails at once. The budget running out
    fails this step only: the enrolment is already stored.
    """
    from sei import SeiApiError

    from app.integrations.sei.enrol import (
        account_open_for_pay_to,
        account_status,
        pay_to_should_wait,
        still_inside_enrolment,
    )

    pending = [number for number in account_numbers if number]
    last = "Work in Progress"
    for attempt in range(polls):
        still: list[str] = []
        for number in pending:
            try:
                details = await api.account_details(
                    firm_id,
                    number,
                    include_relationships=False,
                    include_pay_to=False,
                    include_pay_for=False,
                    include_payers=False,
                )
            except SeiApiError as exc:
                if not still_inside_enrolment(str(exc)):
                    raise OperationError(
                        f"SEI could not read account {number} before the {what}: {exc}",
                        permanent=False,
                    ) from exc
                still.append(number)
                continue
            if account_open_for_pay_to(details):
                continue
            status = account_status(details)
            if pay_to_should_wait(status):
                still.append(number)
                if status:
                    last = status
                continue
            raise OperationError(
                f"SEI account {number} is {status}. The {what} was not sent.",
                permanent=True,
            )
        pending = still
        if not pending:
            return
        if attempt + 1 < polls:
            await sleep(pause_seconds)
    listed = ", ".join(pending)
    raise OperationError(
        f"SEI is still enrolling {listed} ({last}). The {what} was not sent. "
        "Retry this step; it will not open another account.",
        permanent=False,
    )


async def _register_pay_tos(
    session: AsyncSession,
    api: Any,
    *,
    tenant_id: int,
    firm_id: str,
    handle: str,
    account_ids: list[int],
    enrolled: list[dict[str, Any]],
    opened: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """Create one pay-to profile per opened account from the bank details on file.

    No bank details means there is nothing to register. A profile SEI already
    holds is read back rather than created again. The profile id is written onto
    the account so a withdrawal can name it before the next PaidTo file.
    """
    from sei import SeiApiError

    from app import bank_accounts
    from app.integrations.sei.pay_to import (
        already_a_pay_to,
        build_pay_to,
        matching_profile,
        pay_to_entry,
        profile_id_of,
    )

    chosen = opened or []
    if not firm_id or len(chosen) != len(account_ids):
        return []
    by_id = {int(account["id"]): account for account in enrolled}
    recorded: list[dict[str, str]] = []
    for account_id, item in zip(account_ids, chosen, strict=True):
        number = str(item.get("accountNumber") or "")
        account = by_id.get(account_id) or {}
        owners = account.get("owners") or []
        contact_id = account.get("contact_id")
        owner = next(
            (person for person in owners if person.get("id") == contact_id),
            owners[0] if owners else {},
        )
        entry, _source = await bank_accounts.resolve_for_account(session, account_id=account_id)
        if not number or not entry:
            continue
        party = str(owner.get("sei_id") or "")
        try:
            request = build_pay_to(
                firm_id=firm_id,
                account_number=number,
                bank=entry,
                external_party_id=str(owner.get("uid") or "") or None,
                party_id=party if party.isdigit() else None,
            )
        except ValueError as exc:
            raise OperationError(
                f"SEI opened the account but could not create the pay-to profile ({handle}): {exc}",
                permanent=True,
                response={"businessKey": handle},
            ) from exc
        if "partyId" not in request and "externalPartyId" not in request:
            continue
        try:
            created = await api.create_pay_to_recipient(request)
        except SeiApiError as exc:
            if not already_a_pay_to(exc):
                raise OperationError(
                    f"SEI opened the account but could not create the pay-to "
                    f"profile ({handle}): {exc}",
                    permanent=False,
                    response={"businessKey": handle},
                ) from exc
            details = await api.account_details(
                firm_id,
                number,
                include_relationships=False,
                include_pay_for=False,
                include_payers=False,
            )
            created = {"profileId": matching_profile(details, entry)}
        profile = profile_id_of(created)
        if not profile:
            continue
        stored = pay_to_entry(entry, profile)
        await _remember_pay_to(session, tenant_id, account_id, stored)
        recorded.append({"account_number": number, "profile_id": profile})
    return recorded


async def _remember_pay_to(
    session: AsyncSession, tenant_id: int, account_id: int, entry: dict[str, str]
) -> None:
    """Record the profile on the bank row it belongs to."""
    import json

    from app import bank_accounts
    from app.integrations.sei.pay_to import remembered_entries

    raw = (
        await session.execute(
            text("SELECT bank_accounts FROM platform.accounts WHERE id = :a AND tenant_id = :t"),
            {"a": account_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    current = bank_accounts.stored_entries(raw) or []
    if any(str(item.get("provider_ref") or "") == entry["provider_ref"] for item in current):
        return
    await session.execute(
        text(
            "UPDATE platform.accounts SET bank_accounts = CAST(:b AS jsonb) "
            "WHERE id = :a AND tenant_id = :t"
        ),
        {"b": json.dumps(remembered_entries(current, entry)), "a": account_id, "t": tenant_id},
    )


async def sei_account_pay_to(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """Create the pay-to profile once SEI will accept a call outside enrolment.

    The enrolment step has already stored the account number. This step reads
    the account until its status is Open, then creates one profile from the bank
    details on file. No bank details skips the step. A Work in Progress account
    fails this step alone, and a retry polls the same account.
    """
    del subjects
    import json

    from sei import SeiClient

    from app import bank_accounts
    from app.integrations.sei.cash_receipts import _settings, configured

    tenant_id = payload.get("tenant_id")
    if tenant_id is None:
        raise OperationError("This action needs the firm.", permanent=True)
    tenant_id = int(tenant_id)
    raw_ids = payload.get("account_ids")
    if isinstance(raw_ids, list) and raw_ids:
        account_ids = [int(item) for item in raw_ids]
    else:
        _tenant, account_id, _contact = await _ids(payload)
        if account_id is None:
            raise OperationError("This action needs the account.", permanent=True)
        account_ids = [account_id]
    if not configured():
        raise OperationError("SEI is not configured.", permanent=True)
    rows = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.contact_id, "
                    "       pl.external_id AS sei_account, "
                    "       en.external_id AS enrollment_key, "
                    "       c.uid AS owner_uid, "
                    "       cp.external_id AS owner_sei_id "
                    "FROM platform.accounts a "
                    "LEFT JOIN platform.links pl "
                    "  ON pl.entity_type = 'account' AND pl.entity_id = a.id "
                    " AND pl.provider = 'sei' AND pl.link_type = 'primary' "
                    " AND pl.tenant_id = a.tenant_id "
                    "LEFT JOIN platform.links en "
                    "  ON en.entity_type = 'account' AND en.entity_id = a.id "
                    " AND en.provider = 'sei' AND en.link_type = 'enrollment' "
                    " AND en.tenant_id = a.tenant_id "
                    "LEFT JOIN crm.contacts c "
                    "  ON c.id = a.contact_id AND c.tenant_id = a.tenant_id "
                    "LEFT JOIN platform.links cp "
                    "  ON cp.entity_type = 'contact' AND cp.entity_id = c.id "
                    " AND cp.provider = 'sei' AND cp.link_type = 'primary' "
                    " AND cp.tenant_id = a.tenant_id "
                    "WHERE a.tenant_id = :t AND a.id = ANY(:ids)"
                ),
                {"t": tenant_id, "ids": account_ids},
            )
        )
        .mappings()
        .all()
    )
    by_id = {int(row["id"]): row for row in rows}
    with_bank: list[int] = []
    for account_id in account_ids:
        if account_id not in by_id:
            raise OperationError("That account was not found.", permanent=True)
        entry, _source = await bank_accounts.resolve_for_account(session, account_id=account_id)
        if entry:
            with_bank.append(account_id)
    if not with_bank:
        return Attempt(
            ok=True,
            skipped=True,
            method="POST",
            url=_PAY_TO_URL,
            response={"skipped": "No bank details"},
        )
    missing = [
        account_id for account_id in with_bank if not str(by_id[account_id]["sei_account"] or "")
    ]
    if missing:
        raise OperationError(
            "SEI has not opened this account yet, so the pay-to cannot be created.",
            permanent=False,
        )
    opened = [
        {"accountNumber": str(by_id[account_id]["sei_account"])} for account_id in account_ids
    ]
    enrolled = []
    for account_id in account_ids:
        row = by_id[account_id]
        party = str(row["owner_sei_id"] or "")
        enrolled.append(
            {
                "id": account_id,
                "contact_id": row["contact_id"],
                "owners": [
                    {
                        "id": row["contact_id"],
                        "uid": row["owner_uid"],
                        "sei_id": party if party.isdigit() else "",
                    }
                ],
            }
        )
    handle = next(
        (
            str(by_id[account_id]["enrollment_key"])
            for account_id in with_bank
            if by_id[account_id]["enrollment_key"]
        ),
        str(by_id[with_bank[0]]["sei_account"]),
    )
    raw = (
        await session.execute(
            text(
                "SELECT config FROM platform.tenant_platforms "
                "WHERE tenant_id = :t AND provider = 'sei'"
            ),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()
    config = json.loads(raw) if isinstance(raw, str) else raw
    firm = str(config.get("firm_id") or "") if isinstance(config, dict) else ""
    if not firm:
        raise OperationError("SEI still needs the firm's id.", permanent=True)
    async with SeiClient(_settings()) as api:
        await wait_until_accounts_open(
            api,
            firm,
            [str(by_id[account_id]["sei_account"]) for account_id in with_bank],
        )
        recorded = await _register_pay_tos(
            session,
            api,
            tenant_id=tenant_id,
            firm_id=firm,
            handle=handle,
            account_ids=account_ids,
            enrolled=enrolled,
            opened=opened,
        )
    if len(recorded) != len(with_bank):
        raise OperationError(
            "SEI did not return a pay-to profile. Nothing further was sent.",
            permanent=False,
        )
    return Attempt(ok=True, method="POST", url=_PAY_TO_URL, response={"pay_to": recorded})


async def _record_opened_ids(
    session: AsyncSession,
    tenant_id: int,
    account_ids: list[int],
    people: list[dict[str, Any]],
    status: dict[str, Any],
    opened: list[dict[str, Any]] | None = None,
) -> None:
    """Write the SEI client id and account numbers once the process has minted them."""
    from app.integrations.sei.enrol import opened_identifiers
    from app.integrations.sei.reference import portfolio_entity_id

    client_id, _listed = opened_identifiers(status)
    chosen = opened or []

    async def _link(entity_type: str, entity_id: int, link_type: str, external_id: str) -> None:
        await session.execute(
            text(
                "INSERT INTO platform.links "
                "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                "VALUES (:t, :entity, :id, 'sei', :kind, :ext) "
                "ON CONFLICT (tenant_id, provider, entity_type, entity_id, link_type) "
                "DO UPDATE SET external_id = EXCLUDED.external_id"
            ),
            {
                "t": tenant_id,
                "entity": entity_type,
                "id": entity_id,
                "kind": link_type,
                "ext": external_id,
            },
        )

    if client_id:
        for person in people:
            await _link("contact", int(person["id"]), "primary", client_id)
    if len(chosen) != len(account_ids):
        return
    for account_id, item in zip(account_ids, chosen, strict=True):
        number = item.get("accountNumber")
        if number:
            await _link("account", account_id, "primary", str(number))
        portfolios = item.get("globalIdOfPortfolios") or []
        portfolio = None
        if portfolios and isinstance(portfolios[0], dict):
            portfolio = portfolios[0].get("globalIdOfPortfolio")
        elif portfolios and isinstance(portfolios[0], str):
            portfolio = portfolios[0]
        if portfolio:
            # The status reports a global id. Cash calls want the trailing entity id.
            await _link(
                "account",
                account_id,
                "portfolio",
                portfolio_entity_id(portfolio) or str(portfolio),
            )


async def _resume_enrolment(session: AsyncSession, payload: dict[str, Any]) -> str | None:
    """The enrolment a previous attempt left open, so this try polls it instead of posting.

    baton hands the business key it kept on the previous attempt, so a retry resumes rather than
    enrolling a second time."""
    del session
    handed = payload.get("resume_business_key")
    return str(handed) if handed else None


async def _record_client_facts(api: Any, firm: str, people: list[dict[str, Any]]) -> list[str]:
    """Store gender, occupation and source of funds after the enrolment has completed.

    Gender is also on the enrolment as ``genderId``. SEI documents that field as
    no longer shown, so the profile update is what stores it. A refusal here does
    not fail the enrolment: the client and the account are already open, and a
    failed step would send a second enrolment on retry.
    """
    from sei import SeiApiError

    from app.integrations.sei.enrol import client_profile_facts

    errors: list[str] = []
    for person in people:
        facts = client_profile_facts(person)
        uid = str(person.get("uid") or "").strip()
        if not facts or not uid:
            continue
        try:
            updated = await api.update_client_profile(
                firm, facts, external_party_id=uid, platform_user="Graphene"
            )
        except SeiApiError as exc:
            errors.append(str(exc))
            continue
        status_word = ""
        if isinstance(updated, dict):
            status_word = str(updated.get("updateStatus") or "").upper()
        if status_word and status_word != "SUCCESS":
            errors.append("SEI did not store the client's gender, occupation or source of funds.")
    return errors


async def create_sei_account(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """Enrol the client and the account together. SEI has no separate client create.

    The pay-to profile and the other currencies are the following steps. This one
    finishes when the client and the account numbers are stored.
    """
    del subjects
    import json

    from app.integrations.seccl.enrol import _CLIENT_SQL
    from app.integrations.sei.cash_receipts import _settings, configured
    from app.integrations.sei.enrol import (
        enrolment_attributes_for,
        enrolment_outcome,
        enrolment_refusal,
        enrolment_settled,
        fill_from_catalogue,
        without_firm_aml,
    )
    from app.providers import account_org_id

    tenant_id = payload.get("tenant_id")
    if tenant_id is None:
        raise OperationError("This action needs the firm.", permanent=True)
    tenant_id = int(tenant_id)
    raw_ids = payload.get("account_ids")
    if isinstance(raw_ids, list) and raw_ids:
        account_ids = [int(item) for item in raw_ids]
    else:
        _tenant, account_id, _contact = await _ids(payload)
        if account_id is None:
            raise OperationError(
                "SEI opens the client and the account together. Run it from the account.",
                permanent=True,
            )
        account_ids = [account_id]
    rows = (
        (
            await session.execute(
                text(
                    "SELECT DISTINCT ON (a.id) a.id, a.uid, a.name, a.currency, a.contact_id, "
                    "       a.custody::text AS custody, a.investment_capacity, "
                    "       p.product_type::text AS product_type, "
                    "       pp.account_open_reference AS scheme_product_id, "
                    "       m.uid AS model_uid "
                    "FROM platform.accounts a "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "LEFT JOIN platform.product_platforms pp "
                    "  ON pp.product_id = a.product_id AND pp.tenant_id = a.tenant_id "
                    " AND pp.provider::text = 'sei' "
                    "LEFT JOIN platform.models m "
                    "  ON m.id = a.model_id AND m.tenant_id = a.tenant_id "
                    "WHERE a.tenant_id = :t AND a.id = ANY(:ids) "
                    "ORDER BY a.id, pp.id"
                ),
                {"t": tenant_id, "ids": account_ids},
            )
        )
        .mappings()
        .all()
    )
    by_id = {int(row["id"]): dict(row) for row in rows}
    if len(by_id) != len(set(account_ids)):
        raise OperationError("That account was not found.", permanent=True)
    memberships = (
        (
            await session.execute(
                text(
                    "SELECT account_id, contact_id, role::text AS role "
                    "FROM crm.account_contacts "
                    "WHERE tenant_id = :t AND account_id = ANY(:ids) "
                    "  AND role::text = ANY(:roles)"
                ),
                {
                    "t": tenant_id,
                    "ids": account_ids,
                    "roles": ["owner", "registered_contact", "decision_maker", "trustee"],
                },
            )
        )
        .mappings()
        .all()
    )
    for account in by_id.values():
        account["owners"] = []
        account["registered_contacts"] = []
        account["party_ids"] = []
        account["application_uid"] = payload.get("application_uid")
    for row in memberships:
        account = by_id[int(row["account_id"])]
        contact_id = int(row["contact_id"])
        account["party_ids"].append(contact_id)
        if row["role"] == "owner":
            account["owners"].append(contact_id)
        elif row["role"] == "registered_contact":
            account["registered_contacts"].append(contact_id)
    for account in by_id.values():
        if account["owners"]:
            primary = account.get("contact_id")
            account["owners"].sort(key=lambda item: (item != primary, item))
        elif account.get("contact_id") is not None:
            account["owners"] = [int(account["contact_id"])]
            account["party_ids"].append(int(account["contact_id"]))
    contact_ids = sorted({item for account in by_id.values() for item in account["party_ids"]})
    if not contact_ids:
        raise OperationError("That account has no client.", permanent=True)
    people: dict[int, dict[str, Any]] = {}
    for contact_id in contact_ids:
        client = (
            (await session.execute(text(_CLIENT_SQL), {"cid": contact_id, "t": tenant_id}))
            .mappings()
            .one_or_none()
        )
        if client is None:
            raise OperationError("That client was not found.", permanent=True)
        person = dict(client)
        from app.adviser_orgs import enrolment_employee_id

        employee_id = await enrolment_employee_id(
            session,
            tenant_id=tenant_id,
            contact_id=contact_id,
            application_uid=str(payload.get("application_uid") or "") or None,
        )
        if employee_id:
            person["sei_employee_id"] = employee_id
        identity = (
            (
                await session.execute(
                    text(
                        "SELECT us_person, fatca_status, country_of_birth, nationality, "
                        "       passport_country, "
                        "       national_identifier, national_identifier_type, tax_residency, "
                        "       type::text AS type, name, sub_type::text AS sub_type, "
                        "       data->>'uk_national_insurance' AS uk_national_insurance "
                        "FROM crm.contacts WHERE id = :id AND tenant_id = :t"
                    ),
                    {"id": contact_id, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if identity is not None:
            person.update(dict(identity))
        risk = (
            (
                await session.execute(
                    text(
                        "SELECT r.overall, "
                        "       to_char("
                        "         COALESCE(r.provider_updated_at, r.updated_at) "
                        "           AT TIME ZONE 'UTC', 'YYYY-MM-DD'"
                        "       ) AS reviewed "
                        "FROM compliance.clients cl "
                        "JOIN compliance.client_risk r ON r.client_id = cl.id "
                        "WHERE cl.tenant_id = :t AND cl.contact_id = :id"
                    ),
                    {"id": contact_id, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if risk is not None:
            person["aml_risk"] = risk["overall"]
            if risk["reviewed"]:
                person["aml_risk_reviewed"] = str(risk["reviewed"])
        links = (
            (
                await session.execute(
                    text(
                        "SELECT link_type, external_id FROM platform.links "
                        "WHERE tenant_id = :t AND provider = 'sei' AND entity_type = 'contact' "
                        "  AND entity_id = :id AND link_type IN ('primary', 'external_party')"
                    ),
                    {"t": tenant_id, "id": contact_id},
                )
            )
            .mappings()
            .all()
        )
        primary = next(
            (
                str(row["external_id"])
                for row in links
                if row["link_type"] == "primary" and str(row["external_id"] or "").isdigit()
            ),
            None,
        )
        # A primary link is SEI's client id. An external_party link means a previous
        # enrolment already introduced them by our uid, so the next call must not
        # create them again.
        if primary:
            person["sei_id"] = primary
        elif links:
            person["sei_id"] = "known"
        people[contact_id] = person
    raw = (
        await session.execute(
            text(
                "SELECT config FROM platform.tenant_platforms "
                "WHERE tenant_id = :t AND provider = 'sei'"
            ),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()
    config = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(config, dict):
        config = {}
    config, teams = fill_from_catalogue(config)
    accounts = []
    for account_id in account_ids:
        account = by_id[account_id]
        accounts.append(
            {
                **account,
                "residing_org_id": account_org_id(
                    "sei", config, account.get("custody") or "onshore"
                ),
                "owners": [people[item] for item in account["owners"]],
                "registered_contacts": [people[item] for item in account["registered_contacts"]],
            }
        )
    ordered_people = []
    seen: set[int] = set()
    for account in accounts:
        for person in account["owners"] + account["registered_contacts"]:
            if person["id"] in seen:
                continue
            seen.add(int(person["id"]))
            ordered_people.append(person)
        for contact_id in by_id[int(account["id"])]["party_ids"]:
            if contact_id in seen:
                continue
            seen.add(contact_id)
            ordered_people.append(people[contact_id])
    fresh = [person for person in ordered_people if not person.get("sei_id")]
    attributes = enrolment_attributes_for(ordered_people, accounts, config, teams)
    if not configured():
        raise OperationError("SEI is not configured.", permanent=True)
    from sei import SeiApiError, SeiClient

    status: dict[str, Any] = {}
    aligned: list[dict[str, Any]] | None = None
    created: Any = None
    fact_errors: list[str] = []
    handle = await _resume_enrolment(session, payload)
    try:
        async with SeiClient(_settings()) as api:
            if not handle:
                created = await api.create_enrollment(attributes)
                handle = created.get("businessKey") if isinstance(created, dict) else None
            if handle:
                # The POST is accepted before the BPM validates. A refusal lands
                # a few seconds later, and recording the party before that makes
                # the next attempt address a client SEI does not have.
                firm = str(config.get("firm_id") or "")
                for _ in range(10):
                    status = await api.enrollment_status(firm, handle)
                    if enrolment_settled(status):
                        break
                    await asyncio.sleep(2)
                outcome = enrolment_outcome(status)
                if outcome:
                    # An open process keeps its handle on the attempt. A retry polls
                    # that enrolment. A refusal does not: the next send is a new one.
                    unfinished = enrolment_refusal(status) is None
                    body = created if isinstance(created, dict) else {"businessKey": handle}
                    raise OperationError(
                        f"{outcome} ({handle})",
                        permanent=not unfinished,
                        response=body if unfinished else None,
                    )
                aligned = await _align_opened(
                    api,
                    firm,
                    status,
                    [str(by_id[account_id].get("name") or "") for account_id in account_ids],
                )
                fact_errors = await _record_client_facts(api, firm, ordered_people)
    except SeiApiError as exc:
        raise OperationError(str(exc)) from exc
    if not handle and isinstance(created, dict):
        handle = created.get("businessKey")
    if handle:
        for account_id in account_ids:
            await session.execute(
                text(
                    "INSERT INTO platform.links "
                    "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                    "VALUES (:t, 'account', :id, 'sei', 'enrollment', :ext) "
                    "ON CONFLICT (tenant_id, provider, entity_type, entity_id, link_type) "
                    "DO UPDATE SET external_id = EXCLUDED.external_id"
                ),
                {"t": tenant_id, "id": account_id, "ext": str(handle)},
            )
        for person in fresh:
            uid = person.get("uid")
            if not uid:
                continue
            await session.execute(
                text(
                    "INSERT INTO platform.links "
                    "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                    "VALUES (:t, 'contact', :id, 'sei', 'external_party', :ext) "
                    "ON CONFLICT (tenant_id, provider, entity_type, entity_id, link_type) "
                    "DO NOTHING"
                ),
                {"t": tenant_id, "id": int(person["id"]), "ext": str(uid)},
            )
        await _record_opened_ids(session, tenant_id, account_ids, fresh, status, opened=aligned)
    response = created if created is not None else status
    if fact_errors and isinstance(response, dict):
        response = {**response, "client_facts_error": fact_errors}
    return Attempt(
        ok=True,
        method="POST",
        url="/accountservice/accounts",
        request=without_firm_aml(attributes),
        response=without_firm_aml(response),
    )


_OPEN_TRANSFERS = """
SELECT uid FROM platform.transfers
WHERE tenant_id = :t AND account_id = :a
  AND status NOT IN ('cancelled', 'completed')
ORDER BY id
"""


_TRANSFER_IN_URL = "/accountcustodytransferservice/accountCustodyTransfer/transferIn"
_TRANSFER_CASH_URL = "/currencyreceiptservices/createcurrencyreceipt"
_FREE_RECEIPT_URL = "/transfersfreereceiptservice/freereceipt"


def _transfer_url(record: dict[str, Any]) -> str:
    """The SEI address this transfer would post to. Cash and in-specie are different calls."""
    if record.get("route") == "ml_sei" and record.get("basis") == "in_specie":
        return _FREE_RECEIPT_URL
    if record.get("basis") == "in_specie":
        return _TRANSFER_IN_URL
    return _TRANSFER_CASH_URL


async def sei_transfer(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """Send the transfer already raised on the account. The workbench reads that row's status.

    Cash is an anticipated receipt. In-specie is a transfer-in. A SEI SIPP is recorded at
    Morgan Lloyd first, so this step waits until that saga is ``funds_sent``. The receipt
    then is cash, or a free receipt when the assets were already asked for — never a
    second transfer-in.
    """
    del subjects
    from app import transfers

    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError(TRANSFER_MISSING, permanent=True)
    uid = await _booked_uid(
        session,
        payload={**payload, "tenant_id": tenant_id, "account_id": account_id},
        sql=_OPEN_TRANSFERS,
        missing=TRANSFER_MISSING,
        several=TRANSFER_SEVERAL,
    )
    record = await transfers.get_transfer(session, tenant_id=tenant_id, uid=uid)
    if record is None:
        raise OperationError("Transfer not found.", permanent=True)
    url = _transfer_url(record)
    if record.get("route") == "ml_sei" and record.get("status") != "funds_sent":
        return Attempt(
            ok=True,
            skipped=True,
            method="POST",
            url=url,
            response={
                "skipped": (
                    "Morgan Lloyd records this SIPP transfer. The SEI receipt is raised "
                    "from the transfer once Morgan Lloyd has completed it."
                )
            },
        )
    if record.get("route") not in {"sei", "ml_sei"}:
        raise OperationError("This transfer's custodian is not SEI.", permanent=True)
    if record.get("sei_activity_id") or record.get("sei_accepted"):
        return Attempt(
            ok=True,
            skipped=True,
            method="POST",
            url=url,
            response={"skipped": "Already instructed"},
        )
    if record.get("route") == "sei" and record.get("status") not in {
        "requested",
        "manual_intervention",
    }:
        return Attempt(
            ok=True,
            skipped=True,
            method="POST",
            url=url,
            response={"skipped": "Already instructed"},
        )
    updated = await transfers.send_sei(session, tenant_id=tenant_id, record=record)
    return transfer_attempt(updated, method="POST", url=url)


_MODEL_SQL = """
SELECT a.model_id, mp.external_id AS sei_model,
       pl.external_id AS sei_account, pf.external_id AS sei_portfolio
FROM platform.accounts a
LEFT JOIN platform.model_platforms mp
  ON mp.model_id = a.model_id AND mp.tenant_id = a.tenant_id AND mp.provider::text = 'sei'
LEFT JOIN platform.links pl
  ON pl.entity_type = 'account' AND pl.entity_id = a.id AND pl.tenant_id = a.tenant_id
 AND pl.provider = 'sei' AND pl.link_type = 'primary'
LEFT JOIN platform.links pf
  ON pf.entity_type = 'account' AND pf.entity_id = a.id AND pf.tenant_id = a.tenant_id
 AND pf.provider = 'sei' AND pf.link_type = 'portfolio'
WHERE a.id = :a AND a.tenant_id = :t
"""


async def sei_attach_model(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """Assign the book's model to the account's principal portfolio.

    The account has to be Open first. WIP Submitted is still inside enrolment,
    so this step reads again rather than posting into that status.
    """
    del subjects
    import json

    from sei import SeiApiError, SeiClient

    from app.integrations.sei.assignment import PATHS, assignment_request, published_model
    from app.integrations.sei.cash_receipts import _settings, configured
    from app.integrations.sei.reference import portfolio_entity_id

    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError("Attaching a model needs the account.", permanent=True)
    if not configured():
        raise OperationError("SEI is not configured.", permanent=True)
    row = (
        (await session.execute(text(_MODEL_SQL), {"a": account_id, "t": tenant_id}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise OperationError("That account was not found.", permanent=True)
    if row["model_id"] is None:
        raise OperationError("This account has no model.", permanent=True)
    model_id = str(row["sei_model"] or "").strip()
    if not model_id:
        raise OperationError(
            "This account's model has no SEI id. The model was not sent.",
            permanent=True,
        )
    number = str(row["sei_account"] or "").strip()
    if not number:
        raise OperationError(
            "SEI has not opened this account yet, so the model was not sent.",
            permanent=True,
        )
    portfolio = portfolio_entity_id(row["sei_portfolio"])
    if not portfolio:
        raise OperationError(
            "SEI has not returned this account's portfolio yet, so the model was not sent.",
            permanent=True,
        )
    raw = (
        await session.execute(
            text(
                "SELECT config FROM platform.tenant_platforms "
                "WHERE tenant_id = :t AND provider = 'sei'"
            ),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()
    config = json.loads(raw) if isinstance(raw, str) else raw
    firm = str(config.get("firm_id") or "") if isinstance(config, dict) else ""
    if not firm:
        raise OperationError("SEI still needs the firm's id.", permanent=True)
    facts = published_model(firm, model_id)
    if facts is None:
        raise OperationError(
            f"SEI model {model_id} is not in the assignment catalogue. The model was not sent.",
            permanent=True,
        )
    path, request = assignment_request(
        firm_id=firm,
        account_number=number,
        portfolio_id=portfolio,
        model_id=model_id,
        model_type=facts.get("model_type_code"),
        tolerance=facts.get("tolerance_multiplier"),
        schedule_id=facts.get("schedule_id"),
    )
    sender = {
        PATHS["8"]: "assign_investment_model",
        PATHS["4"]: "assign_implementation_model",
        PATHS["3"]: "assign_allocation_model",
    }[path]
    async with SeiClient(_settings()) as api:
        # A timeout is the account still inside enrolment. Pay-to leaves that
        # retryable; this step does too, so the next drain polls again.
        await wait_until_accounts_open(api, firm, [number], what="model")
        try:
            created = await getattr(api, sender)(request)
        except SeiApiError as exc:
            raise OperationError(f"SEI did not assign the model: {exc}", permanent=True) from exc
    return Attempt(ok=True, method="POST", url=path, request=request, response=created)


async def refuse_sei_client_create(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del session, payload, subjects
    raise OperationError(
        "SEI opens the client and the account together. Run it from the account.",
        permanent=True,
    )
