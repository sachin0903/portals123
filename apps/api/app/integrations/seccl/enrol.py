"""Open a client and an account at Seccl during onboarding.

Seccl will not create an account without a client id, so ``client.setup`` runs
``client.create`` and then ``account.create``. A GIA, ISA, or JISA is the wrapper
type. A pension needs a scheme product id Seccl issues, and this path refuses it
rather than inventing one.

A new client is opened under the lead adviser's Seccl node. A client already on the
platform keeps the node on their record. A firm with one enabled node uses that when
the adviser has not chosen. The client id and the account id are written to
``platform.links``.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.attempt import OperationError
from app.adviser_orgs import remember_seccl_node, seccl_node_for_create
from app.integrations.morgan_lloyd.contacts import client_columns
from app.integrations.seccl.connection import configured, seccl_settings, why_not_configured

_WRAPPERS = {"GIA": "GIA", "ISA": "ISA", "JISA": "JISA"}
_GENDER = {"male": "Male", "female": "Female", "m": "Male", "f": "Female"}
# Seccl's create-individual enum. The book stores Prof; the enum word is Professor.
# A title outside the enum is omitted rather than sent as a word Seccl will reject.
_SECCL_TITLES = {
    "mr": "Mr",
    "mrs": "Mrs",
    "miss": "Miss",
    "ms": "Ms",
    "dr": "Dr",
    "prof": "Professor",
    "professor": "Professor",
    "mx": "Mx",
}


def seccl_title(value: object) -> str | None:
    key = str(value or "").strip().rstrip(".").lower()
    return _SECCL_TITLES.get(key)


_COUNTRIES = {
    "uk": "GB",
    "united kingdom": "GB",
    "great britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "northern ireland": "GB",
}

# `client_columns` is a literal of our own column names. Same posture as the Morgan Lloyd
# projection: no request value reaches the SQL.
_CLIENT_SQL = (
    "SELECT " + client_columns("c") + ", "  # noqa: S608
    "       c.tax_residency, c.address->>'country' AS address_country, "
    "       pl.external_id AS seccl_id "
    "FROM crm.contacts c "
    "LEFT JOIN platform.links pl "
    "  ON pl.entity_type = 'contact' AND pl.entity_id = c.id "
    " AND pl.provider = 'seccl' AND pl.link_type = 'primary' "
    "WHERE c.id = :cid AND c.tenant_id = :t AND c.deleted_at IS NULL"
)

_ACCOUNT_SQL = """
SELECT a.id, a.uid, a.name, a.currency, a.contact_id,
       p.product_type::text AS product_type,
       pl.external_id AS seccl_id,
       scheme.account_open_reference AS scheme_product_id,
       model.external_id AS model_external_id
FROM platform.accounts a
LEFT JOIN platform.products p ON p.id = a.product_id
LEFT JOIN platform.links pl
  ON pl.entity_type = 'account' AND pl.entity_id = a.id
 AND pl.provider = 'seccl' AND pl.link_type = 'primary' AND pl.tenant_id = a.tenant_id
LEFT JOIN LATERAL (
  SELECT pp.account_open_reference
  FROM platform.product_platforms pp
  WHERE pp.product_id = a.product_id AND pp.tenant_id = a.tenant_id
    AND pp.provider::text = 'seccl'
  LIMIT 1
) scheme ON true
LEFT JOIN LATERAL (
  SELECT mp.external_id
  FROM platform.model_platforms mp
  WHERE mp.model_id = a.model_id AND mp.tenant_id = a.tenant_id
    AND mp.provider::text = 'seccl'
  LIMIT 1
) model ON true
WHERE a.id = :a AND a.tenant_id = :t
"""

_LINK_SQL = """
INSERT INTO platform.links
  (tenant_id, entity_type, entity_id, provider, link_type, external_id)
VALUES (:t, :kind, :eid, 'seccl', 'primary', :ext)
ON CONFLICT (tenant_id, provider, entity_type, entity_id, link_type)
DO UPDATE SET external_id = EXCLUDED.external_id
"""


def _iso(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text_value = value.strip()
    if len(text_value) == 2 and text_value.isalpha():
        return text_value.upper()
    return _COUNTRIES.get(text_value.lower())


def _domicile(client: dict[str, Any]) -> str | None:
    found = _iso(client.get("nationality"))
    if found:
        return found
    residencies = client.get("tax_residency") or []
    if isinstance(residencies, str):
        residencies = [residencies]
    for code in residencies:
        found = _iso(code)
        if found:
            return found
    return None


def _day(value: object) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()[:10]
    return None


def client_body(client: dict[str, Any], *, node_id: str) -> dict[str, Any]:
    """The ``POST /client`` body, or a refusal naming the fields still empty."""
    missing: list[str] = []
    if not node_id:
        missing.append("a Seccl node on the firm")
    if not client.get("first_name"):
        missing.append("first name")
    if not client.get("surname"):
        missing.append("surname")
    if not client.get("email"):
        missing.append("email address")
    if not _day(client.get("dob")):
        missing.append("date of birth")
    if not client.get("address_line1"):
        missing.append("address line 1")
    if not client.get("address_postcode"):
        missing.append("postcode")
    country = _iso(client.get("address_country"))
    if not country:
        missing.append("country")
    domicile = _domicile(client)
    if not domicile:
        missing.append("nationality")
    if missing:
        raise OperationError("Seccl still needs " + ", ".join(missing) + ".", permanent=True)
    body: dict[str, Any] = {
        "nodeId": [node_id],
        "clientType": "Individual",
        "firstName": client["first_name"],
        "surname": client["surname"],
        "email": client["email"],
        "currency": "GBP",
        "taxDomicile": domicile,
        "dateOfBirth": _day(client.get("dob")),
        "addressDetail": {
            "address1": client["address_line1"],
            "country": country,
            "postCode": client["address_postcode"],
        },
    }
    if client.get("address_city"):
        body["addressDetail"]["address2"] = client["address_city"]
    title = seccl_title(client.get("title"))
    if title:
        body["title"] = title
    gender = _GENDER.get(str(client.get("gender") or "").strip().lower())
    if gender:
        body["gender"] = gender
    ni = client.get("national_identifier")
    if isinstance(ni, str) and ni.strip():
        body["nationalInsuranceNo"] = ni.strip()
    if client.get("is_vulnerable") is True:
        reasons = client.get("vulnerability_reasons") or []
        if isinstance(reasons, str):
            reasons = [reasons]
        block: dict[str, Any] = {"isVulnerable": True}
        if reasons:
            block["reason"] = [str(reason) for reason in reasons]
        if client.get("vulnerability_is_temporary") is not None:
            block["isSupportTemporary"] = bool(client["vulnerability_is_temporary"])
        body["vulnerability"] = block
    bank = client.get("bank_account")
    if isinstance(bank, str):
        bank = json.loads(bank)
    if isinstance(bank, dict):
        sort_code = str(bank.get("sort_code") or "").replace("-", "").strip()
        number = str(bank.get("account_number") or "").strip()
        if sort_code and number:
            body["bankDetails"] = {"sortCode": sort_code, "accountNumber": number}
    from app.integrations.seccl.instruct import circumstances_fields

    body.update(circumstances_fields(client))
    return body


def account_body(account: dict[str, Any], *, client_id: str, node_id: str) -> dict[str, Any]:
    """The ``POST /account`` body.

    A pension is sent only when the product names a scheme product id. A model is
    ``wrapperDetail.assetAllocationId``, which Seccl accepts on create and not on update.
    """
    product = str(account.get("product_type") or "").upper()
    scheme = str(account.get("scheme_product_id") or "").strip()
    wrapper = _WRAPPERS.get(product)
    if wrapper is None and product in {"SIPP", "PENSION", "SSAS"} and scheme:
        wrapper = "PENSION"
    if wrapper is None:
        if product in {"SIPP", "PENSION", "SSAS"}:
            raise OperationError(
                "A Seccl pension needs a scheme product, which this product does not name.",
                permanent=True,
            )
        raise OperationError(
            f"Seccl has no wrapper for {product or 'this product'}.", permanent=True
        )
    if not node_id:
        raise OperationError("Seccl still needs a Seccl node on the firm.", permanent=True)
    if not client_id:
        raise OperationError("Create the Seccl client before the account.", permanent=True)
    detail: dict[str, Any] = {"wrapperType": wrapper}
    if scheme:
        detail["schemeProductId"] = scheme
    model = str(account.get("model_external_id") or "").strip()
    if model:
        detail["assetAllocationId"] = model
    currency = str(account.get("currency") or "").strip() or "GBP"
    return {
        "nodeId": node_id,
        "clientId": client_id,
        "accountType": "Wrapper",
        "name": account.get("name") or wrapper,
        "currency": currency,
        "status": "Pending",
        "wrapperDetail": detail,
    }


def _created_id(record: dict[str, Any]) -> str:
    for key in ("id", "_id"):
        value = record.get(key)
        if value:
            return str(value)
    raise OperationError("Seccl did not return an id.")


def client_product_of(record: dict[str, Any]) -> tuple[str, str] | None:
    """``(wrapper, clientProductId)`` from a Seccl account.

    The client product is the tax wrapper the account sits in. A client has one
    per wrapper, and every account of that wrapper points at the same id.
    """
    wrapper = record.get("wrapperDetail")
    if isinstance(wrapper, str):
        wrapper = json.loads(wrapper)
    if not isinstance(wrapper, dict):
        return None
    kind = str(wrapper.get("wrapperType") or "").strip()
    product_id = str(wrapper.get("clientProductId") or "").strip()
    if not kind or not product_id:
        return None
    return kind, product_id


def client_product_link_type(wrapper: str) -> str:
    return f"client_product:{wrapper}"


async def store_client_product(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    account_external_id: str,
) -> str | None:
    """Read the account's client product and keep it on the client, keyed by wrapper."""
    if not configured() or not account_external_id:
        return None
    from seccl import SecclApiError, SecclClient

    try:
        async with SecclClient(seccl_settings()) as api:
            record = await api.account(account_external_id)
    except SecclApiError:
        return None
    found = client_product_of(record)
    if found is None:
        return None
    kind, product_id = found
    await session.execute(
        text(
            "INSERT INTO platform.links "
            "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
            "VALUES (:t, 'contact', :eid, 'seccl', :lt, :ext) "
            "ON CONFLICT (tenant_id, provider, entity_type, entity_id, link_type) "
            "DO UPDATE SET external_id = EXCLUDED.external_id"
        ),
        {
            "t": tenant_id,
            "eid": contact_id,
            "lt": client_product_link_type(kind),
            "ext": product_id,
        },
    )
    return product_id


async def _record_link(
    session: AsyncSession, *, tenant_id: int, kind: str, entity_id: int, external_id: str
) -> None:
    await session.execute(
        text(_LINK_SQL),
        {"t": tenant_id, "kind": kind, "eid": entity_id, "ext": external_id},
    )


async def create_client(
    session: AsyncSession, *, tenant_id: int, contact_id: int
) -> dict[str, Any]:
    """Create the Seccl client, or return the id already on the link."""
    row = (
        (await session.execute(text(_CLIENT_SQL), {"cid": contact_id, "t": tenant_id}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise OperationError("That client was not found.", permanent=True)
    client = dict(row)
    if client.get("seccl_id"):
        return {"skipped": "already_linked", "id": client["seccl_id"]}
    if not configured():
        raise OperationError(why_not_configured(), permanent=True)
    node_id = await seccl_node_for_create(session, tenant_id=tenant_id, contact_id=contact_id)
    body = client_body(client, node_id=node_id)
    from seccl import SecclApiError, SecclClient

    try:
        async with SecclClient(seccl_settings()) as api:
            created = await api.create_client(body)
    except SecclApiError as exc:
        raise OperationError(str(exc)) from exc
    external_id = _created_id(created)
    await _record_link(
        session,
        tenant_id=tenant_id,
        kind="contact",
        entity_id=contact_id,
        external_id=external_id,
    )
    await remember_seccl_node(
        session, tenant_id=tenant_id, kind="contact", entity_id=contact_id, key=node_id
    )
    return {"id": external_id, "request": body}


async def create_account(
    session: AsyncSession, *, tenant_id: int, account_id: int
) -> dict[str, Any]:
    """Open the Seccl wrapper for an account whose client is already linked."""
    row = (
        (await session.execute(text(_ACCOUNT_SQL), {"a": account_id, "t": tenant_id}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise OperationError("That account was not found.", permanent=True)
    account = dict(row)
    if account.get("seccl_id"):
        contact_id = account.get("contact_id")
        client_product_id = None
        if contact_id is not None:
            client_product_id = await store_client_product(
                session,
                tenant_id=tenant_id,
                contact_id=int(contact_id),
                account_external_id=str(account["seccl_id"]),
            )
        return {
            "skipped": "already_linked",
            "id": account["seccl_id"],
            "client_product_id": client_product_id,
        }
    contact_id = account.get("contact_id")
    if contact_id is None:
        raise OperationError("That account has no client.", permanent=True)
    client = (
        (await session.execute(text(_CLIENT_SQL), {"cid": int(contact_id), "t": tenant_id}))
        .mappings()
        .one_or_none()
    )
    client_id = str(client["seccl_id"]) if client and client.get("seccl_id") else ""
    if not configured():
        raise OperationError(why_not_configured(), permanent=True)
    node_id = await seccl_node_for_create(session, tenant_id=tenant_id, contact_id=int(contact_id))
    body = account_body(account, client_id=client_id, node_id=node_id)
    from seccl import SecclApiError, SecclClient

    try:
        async with SecclClient(seccl_settings()) as api:
            created = await api.create_account(body)
    except SecclApiError as exc:
        raise OperationError(str(exc)) from exc
    external_id = _created_id(created)
    await _record_link(
        session,
        tenant_id=tenant_id,
        kind="account",
        entity_id=account_id,
        external_id=external_id,
    )
    client_product_id = await store_client_product(
        session,
        tenant_id=tenant_id,
        contact_id=int(contact_id),
        account_external_id=external_id,
    )
    await remember_seccl_node(
        session, tenant_id=tenant_id, kind="account", entity_id=account_id, key=node_id
    )
    await remember_seccl_node(
        session, tenant_id=tenant_id, kind="contact", entity_id=int(contact_id), key=node_id
    )
    return {"id": external_id, "request": body, "client_product_id": client_product_id}
