"""Live writes for the firm API.

Every statement carries the key's ``tenant_id``. A check does not read or
create an account. Custodian ids are written to ``platform.links``
(introduced as ``platform.platform_links`` and renamed in migration 0043).

This module does not open a connection and does not import the portal database
module. Callers pass a session; the production wrapper lives in ``external.db``.

A firm's uploaded file is kept in object storage (the firm's document bucket, under the client's
``files/clients/{contact uid}/`` prefix), and its ``compliance.documents`` row holds the
``object_key`` and the file's name, type and size. When an identity check is submitted, the
provider's document and live photo are recorded with the same object keys, so the operations
console shows the images the firm sent. Those provider rows (``provider_document_id`` set) are
never read as the firm's own documents.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

import httpx
from sqlalchemy import text

from app import storage, tenant_storage
from app.complycube.client import (
    PROVIDER_DOCUMENT_TYPES,
    ComplyCubeNotConfigured,
    ScreeningClient,
    address_payload,
    client_metadata,
    client_payload,
    environment,
    monitoring_of,
)
from app.complycube.outcomes import partner_status
from app.complycube.webhook_url import Resolver, UnsafeWebhookUrl, check_url
from app.contacts import validate_contact_phone
from app.crypto import encrypt
from app.ids import new_id
from external.models import (
    ID_DOCUMENT_TYPES,
    TWO_SIDED_TYPES,
    AddressIn,
    AddressOut,
    CheckCreate,
    CheckOut,
    ChecksOut,
    CheckStatus,
    ClientCreate,
    ClientOut,
    ClientPatch,
    ClientType,
    CompanyOut,
    CustodianPut,
    DocumentOut,
    HeldDocument,
    Identity,
    WebhookCreated,
    WebhookOut,
    apply_patch,
    checks_overview,
    identity_details,
    screened_name,
    to_client_out,
    utc_now,
)
from external.store import ADDRESS_COUNTRY_REQUIRED, DOCUMENTS_REQUIRED, Rejected

_JSON = json.dumps
logger = logging.getLogger(__name__)


class Session(Protocol):
    async def execute(self, statement: Any, params: Mapping[str, Any] | None = None) -> Any: ...


def _get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _loads(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        return json.loads(value)
    return value


def _dump(value: Any) -> str:
    return _JSON(value, default=_json_default)


def _json_default(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"cannot encode {type(value).__name__}")


def _identities_of(body: ClientCreate | ClientOut | ClientPatch) -> list[Identity]:
    return list(body.identities or [])


def _identifier(body: ClientCreate | ClientOut | ClientPatch) -> tuple[str | None, str | None]:
    items = _identities_of(body)
    if not items:
        return None, None
    return items[0].identifier, items[0].type


def _contact_type(kind: ClientType) -> str:
    return "individual" if kind == "person" else "organisation"


def _public_type(stored: str | None) -> ClientType:
    if stored == "organisation":
        return "company"
    return "person"


def _display_name(body: ClientCreate | ClientOut) -> str | None:
    if body.type == "company" and body.company and body.company.name:
        return body.company.name
    parts = [part for part in (body.first_name, body.middle_name, body.last_name) if part]
    return " ".join(parts) or None


def _firm_data(body: ClientCreate | ClientOut) -> dict[str, Any]:
    company = body.company.model_dump(mode="json") if body.company else None
    return {
        "gender": body.gender,
        "birth_country": body.birth_country,
        "identities": [item.model_dump(mode="json") for item in (body.identities or [])] or None,
        "reference": body.reference,
        "metadata": body.metadata,
        "company": company,
    }


def _address_body(address: AddressIn) -> dict[str, str]:
    extra: dict[str, str] = {}
    if address.property_number:
        extra["property_number"] = address.property_number
    if address.building_name:
        extra["building_name"] = address.building_name
    return extra


INSERT_CONTACT = text(
    """
    INSERT INTO crm.contacts (
      tenant_id, uid, type, sub_type, name, first_name, middle_name, surname, dob, email,
      nationality, tax_residency, mobile, phone, national_identifier,
      national_identifier_type, data
    ) VALUES (
      :tenant_id, :uid, CAST(:contact_type AS crm.contact_type),
      CAST(:sub_type AS crm.contact_sub_type), :name, :first_name, :middle_name, :surname,
      :dob, :email, :nationality, :tax_residency, :mobile, :phone,
      :national_identifier, :national_identifier_type, CAST(:data AS jsonb)
    )
    RETURNING id
    """
)

INSERT_CLIENT = text(
    """
    INSERT INTO compliance.clients (tenant_id, uid, contact_id)
    VALUES (:tenant_id, :uid, :contact_id)
    RETURNING id
    """
)

INSERT_ADDRESS = text(
    """
    INSERT INTO compliance.addresses (
      tenant_id, client_id, line, city, state, postal_code, country, from_date, body
    ) VALUES (
      :tenant_id, :client_id, :line, :city, :state, :postal_code, :country,
      :from_date, CAST(:body AS jsonb)
    )
    """
)

UPDATE_CONTACT = text(
    """
    UPDATE crm.contacts
       SET type = CAST(:contact_type AS crm.contact_type),
           sub_type = CAST(:sub_type AS crm.contact_sub_type),
           name = :name,
           first_name = :first_name,
           middle_name = :middle_name,
           surname = :surname,
           dob = :dob,
           email = :email,
           nationality = :nationality,
           tax_residency = :tax_residency,
           mobile = :mobile,
           phone = :phone,
           national_identifier = :national_identifier,
           national_identifier_type = :national_identifier_type,
           data = CAST(:data AS jsonb),
           updated_at = now()
     WHERE id = :contact_id AND tenant_id = :tenant_id
    """
)

DELETE_ADDRESSES = text(
    """
    DELETE FROM compliance.addresses
     WHERE client_id = :client_id AND tenant_id = :tenant_id
    """
)

# The firm's uid and name are stamped on the provider client a check creates.
SELECT_CLIENT = text(
    """
    SELECT cl.uid, cl.id AS client_id, cl.contact_id, c.uid AS contact_uid,
           c.type AS contact_type, c.email, c.first_name, c.middle_name, c.surname AS last_name,
           c.dob, c.nationality, c.tax_residency, c.mobile, c.phone, c.data,
           cl.provider_client_id, t.uid AS tenant_uid, t.name AS tenant_name,
           (
             SELECT json_build_object(
               'line', a.line, 'city', a.city, 'state', a.state,
               'postal_code', a.postal_code, 'country', a.country,
               'from_date', a.from_date, 'body', a.body
             )
               FROM compliance.addresses a
              WHERE a.client_id = cl.id AND a.tenant_id = cl.tenant_id
           ) AS address,
           COALESCE((
             SELECT json_agg(json_build_object(
               'provider', l.provider::text, 'external_id', l.external_id
             ))
               FROM platform.links l
              WHERE l.tenant_id = cl.tenant_id
                AND l.entity_type = 'contact'
                AND l.entity_id = cl.contact_id
                AND l.provider IN (
                  CAST('seccl' AS platform.provider_code),
                  CAST('sei' AS platform.provider_code)
                )
           ), '[]'::json) AS custodians
      FROM compliance.clients cl
      JOIN crm.contacts c ON c.id = cl.contact_id AND c.tenant_id = cl.tenant_id
      JOIN auth.tenants t ON t.id = cl.tenant_id
     WHERE cl.tenant_id = :tenant_id AND cl.uid = :uid
    """
)

SELECT_CLIENTS = text(
    """
    SELECT cl.uid, cl.id AS client_id, cl.contact_id, c.uid AS contact_uid,
           c.type AS contact_type, c.email, c.first_name, c.middle_name, c.surname AS last_name,
           c.dob, c.nationality, c.tax_residency, c.mobile, c.phone, c.data,
           cl.provider_client_id,
           (
             SELECT json_build_object(
               'line', a.line, 'city', a.city, 'state', a.state,
               'postal_code', a.postal_code, 'country', a.country,
               'from_date', a.from_date, 'body', a.body
             )
               FROM compliance.addresses a
              WHERE a.client_id = cl.id AND a.tenant_id = cl.tenant_id
           ) AS address,
           COALESCE((
             SELECT json_agg(json_build_object(
               'provider', l.provider::text, 'external_id', l.external_id
             ))
               FROM platform.links l
              WHERE l.tenant_id = cl.tenant_id
                AND l.entity_type = 'contact'
                AND l.entity_id = cl.contact_id
                AND l.provider IN (
                  CAST('seccl' AS platform.provider_code),
                  CAST('sei' AS platform.provider_code)
                )
           ), '[]'::json) AS custodians
      FROM compliance.clients cl
      JOIN crm.contacts c ON c.id = cl.contact_id AND c.tenant_id = cl.tenant_id
     WHERE cl.tenant_id = :tenant_id
     ORDER BY cl.id
     LIMIT :limit OFFSET :offset
    """
)

SELECT_CLIENT_KEY = text(
    """
    SELECT cl.id, cl.contact_id, c.uid AS contact_uid
      FROM compliance.clients cl
      JOIN crm.contacts c ON c.id = cl.contact_id AND c.tenant_id = cl.tenant_id
     WHERE cl.uid = :uid AND cl.tenant_id = :tenant_id
    """
)

INSERT_DOCUMENT = text(
    """
    INSERT INTO compliance.documents (
      tenant_id, uid, client_id, issuing_country, classification, side, object_key, body
    ) VALUES (
      :tenant_id, :uid, :client_id, :issuing_country, :classification, :side, :object_key,
      CAST(:body AS jsonb)
    )
    RETURNING uid
    """
)

# The firm's own uploads. A row with a provider document id is the provider's copy, recorded by
# the event consumer or by an identity submission, and is not one of the firm's documents.
SELECT_DOCUMENTS = text(
    """
    SELECT uid, classification, issuing_country, side, object_key, body
      FROM compliance.documents
     WHERE tenant_id = :tenant_id AND client_id = :client_id
       AND provider_document_id IS NULL
     ORDER BY id
    """
)

# The provider's document and live photo for a submitted identity check, holding the object keys
# of the files the firm sent: the front, and the back when one was sent. The event consumer may
# store the same provider ids first; it never writes either key, so both are filled in here.
UPSERT_PROVIDER_DOCUMENT = text(
    """
    INSERT INTO compliance.documents (
      tenant_id, client_id, provider_document_id, document_type, issuing_country,
      classification, object_key, back_object_key
    ) VALUES (
      :tenant_id, :client_id, :provider_document_id, :document_type, :issuing_country,
      'proof_of_identity', :object_key, :back_object_key
    )
    ON CONFLICT (provider_document_id) WHERE provider_document_id IS NOT NULL
    DO UPDATE SET object_key = COALESCE(compliance.documents.object_key, EXCLUDED.object_key),
                  back_object_key = COALESCE(
                    compliance.documents.back_object_key, EXCLUDED.back_object_key
                  ),
                  updated_at = now()
     WHERE compliance.documents.tenant_id IS NULL
        OR compliance.documents.tenant_id = EXCLUDED.tenant_id
    """
)

UPSERT_PROVIDER_PHOTO = text(
    """
    INSERT INTO compliance.live_photos (
      tenant_id, client_id, provider_photo_id, content_type, size_bytes, object_key
    ) VALUES (
      :tenant_id, :client_id, :provider_photo_id, :content_type, :size_bytes, :object_key
    )
    ON CONFLICT (provider_photo_id) WHERE provider_photo_id IS NOT NULL
    DO UPDATE SET object_key = COALESCE(compliance.live_photos.object_key, EXCLUDED.object_key),
                  updated_at = now()
     WHERE compliance.live_photos.tenant_id IS NULL
        OR compliance.live_photos.tenant_id = EXCLUDED.tenant_id
    """
)

# The provider's webhook can land before this insert commits, and the consumer then stores the
# check first. The conflict keeps that row, its uid and the status the provider already reported,
# and binds it to this firm's client. Another firm's row is never claimed.
INSERT_CHECK = text(
    """
    INSERT INTO compliance.checks (
      tenant_id, uid, client_id, kind, status, provider_check_id, monitoring, enable_monitoring
    ) VALUES (
      :tenant_id, :uid, :client_id, :kind, 'pending', :provider_check_id,
      :monitoring, :monitoring
    )
    ON CONFLICT (provider_check_id) WHERE provider_check_id IS NOT NULL
    DO UPDATE SET tenant_id = EXCLUDED.tenant_id,
                  client_id = EXCLUDED.client_id,
                  kind = EXCLUDED.kind,
                  updated_at = now()
     WHERE compliance.checks.tenant_id IS NULL
        OR compliance.checks.tenant_id = EXCLUDED.tenant_id
    RETURNING uid, status, created_at, updated_at
    """
)

SELECT_CHECK = text(
    """
    SELECT ch.uid, ch.kind, ch.status, ch.created_at, ch.updated_at,
           c.name, c.first_name, c.middle_name, c.surname AS last_name, c.dob,
           (
             SELECT a.country
               FROM compliance.addresses a
              WHERE a.client_id = ch.client_id AND a.tenant_id = ch.tenant_id
           ) AS country,
           (
             SELECT COALESCE(json_agg(json_build_object(
                      'uid', d.uid,
                      'type', d.classification,
                      'issuing_country', d.issuing_country
                    ) ORDER BY d.id), '[]'::json)
               FROM compliance.documents d
              WHERE d.client_id = ch.client_id
                AND d.tenant_id = ch.tenant_id
                AND d.provider_document_id IS NULL
                AND d.created_at <= ch.created_at
           ) AS documents
      FROM compliance.checks ch
      JOIN compliance.clients cl ON cl.id = ch.client_id AND cl.tenant_id = ch.tenant_id
      JOIN crm.contacts c ON c.id = cl.contact_id AND c.tenant_id = cl.tenant_id
     WHERE ch.tenant_id = :tenant_id AND ch.uid = :uid
    """
)

SELECT_CHECKS = text(
    """
    SELECT ch.uid, ch.kind, ch.status, ch.created_at, ch.updated_at,
           c.name, c.first_name, c.middle_name, c.surname AS last_name, c.dob,
           (
             SELECT a.country
               FROM compliance.addresses a
              WHERE a.client_id = ch.client_id AND a.tenant_id = ch.tenant_id
           ) AS country,
           (
             SELECT COALESCE(json_agg(json_build_object(
                      'uid', d.uid,
                      'type', d.classification,
                      'issuing_country', d.issuing_country
                    ) ORDER BY d.id), '[]'::json)
               FROM compliance.documents d
              WHERE d.client_id = ch.client_id
                AND d.tenant_id = ch.tenant_id
                AND d.provider_document_id IS NULL
                AND d.created_at <= ch.created_at
           ) AS documents
      FROM compliance.checks ch
      JOIN compliance.clients cl ON cl.id = ch.client_id AND cl.tenant_id = ch.tenant_id
      JOIN crm.contacts c ON c.id = cl.contact_id AND c.tenant_id = cl.tenant_id
     WHERE ch.tenant_id = :tenant_id AND cl.uid = :client_uid
     ORDER BY ch.created_at DESC, ch.id DESC
    """
)

# Custodian ids live on platform.links, the rename of platform.platform_links.
UPSERT_CUSTODIAN = text(
    """
    INSERT INTO platform.links (
      tenant_id, entity_type, entity_id, provider, link_type, external_id
    ) VALUES (
      :tenant_id, 'contact', :entity_id, CAST(:provider AS platform.provider_code),
      'primary', :external_id
    )
    ON CONFLICT ON CONSTRAINT links_unique_link
    DO UPDATE SET external_id = EXCLUDED.external_id, updated_at = now()
    """
)

UPDATE_PROVIDER_CLIENT = text(
    """
    UPDATE compliance.clients
       SET provider_client_id = :provider_client_id, updated_at = now()
     WHERE id = :client_id AND tenant_id = :tenant_id
    """
)

UPSERT_WEBHOOK = text(
    """
    INSERT INTO compliance.webhooks (tenant_id, url, secret_ct)
    VALUES (:tenant_id, :url, :secret_ct)
    ON CONFLICT (tenant_id)
    DO UPDATE SET url = EXCLUDED.url, secret_ct = EXCLUDED.secret_ct, updated_at = now()
    """
)

SELECT_WEBHOOK = text(
    """
    SELECT url FROM compliance.webhooks WHERE tenant_id = :tenant_id
    """
)

DELETE_WEBHOOK = text(
    """
    DELETE FROM compliance.webhooks WHERE tenant_id = :tenant_id
    """
)


def _contact_params(body: ClientOut, *, tenant_id: int, contact_uid: str) -> dict[str, Any]:
    identifier, identifier_type = _identifier(body)
    company = body.type == "company"
    return {
        "tenant_id": tenant_id,
        "uid": contact_uid,
        "contact_type": _contact_type(body.type),
        "sub_type": "company" if company else None,
        "name": _display_name(body),
        "first_name": body.first_name,
        "middle_name": body.middle_name,
        "surname": body.last_name,
        "dob": body.dob,
        "email": body.email,
        "nationality": body.nationality,
        "tax_residency": body.tax_residency,
        "mobile": validate_contact_phone(body.mobile),
        "phone": validate_contact_phone(body.telephone),
        "national_identifier": identifier,
        "national_identifier_type": identifier_type,
        "data": _dump(_firm_data(body)),
    }


def _address_params(address: AddressIn, *, tenant_id: int, client_id: int) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "client_id": client_id,
        "line": address.line,
        "city": address.city,
        "state": address.state,
        "postal_code": address.postal_code,
        "country": address.country,
        "from_date": address.from_date,
        "body": _dump(_address_body(address)),
    }


def _address(value: Any) -> AddressOut | None:
    item = _loads(value, None)
    if not isinstance(item, dict):
        return None
    extra = _loads(item.get("body"), {})
    if not isinstance(extra, dict):
        extra = {}
    country = item.get("country")
    if not isinstance(country, str) or not country:
        return None
    return AddressOut(
        property_number=extra.get("property_number"),
        building_name=extra.get("building_name"),
        line=item.get("line"),
        city=item.get("city"),
        state=item.get("state"),
        postal_code=item.get("postal_code"),
        country=country,
        from_date=item.get("from_date"),
    )


def _custodians(value: Any) -> list[CustodianPut]:
    rows = _loads(value, [])
    if not isinstance(rows, list):
        return []
    built: list[CustodianPut] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        provider = item.get("provider")
        external_id = item.get("external_id")
        if provider in {"seccl", "sei"} and isinstance(external_id, str) and external_id:
            built.append(CustodianPut(provider=provider, external_id=external_id))
    return built


def _identities_from_data(data: dict[str, Any]) -> list[Identity] | None:
    raw = data.get("identities")
    items: list[Identity] = []
    if isinstance(raw, list):
        items = [Identity.model_validate(item) for item in raw if isinstance(item, dict)]
    else:
        identity_raw = data.get("identity")
        if isinstance(identity_raw, dict):
            items = [Identity.model_validate(identity_raw)]
    return items or None


def client_from_row(row: Any) -> ClientOut:
    data = _loads(_get(row, "data"), {})
    if not isinstance(data, dict):
        data = {}
    company_raw = data.get("company")
    company = CompanyOut.model_validate(company_raw) if isinstance(company_raw, dict) else None
    kind = _public_type(_get(row, "contact_type"))
    return ClientOut(
        uid=str(_get(row, "uid")),
        type=kind,
        email=str(_get(row, "email") or ""),
        first_name=_get(row, "first_name"),
        middle_name=_get(row, "middle_name"),
        last_name=_get(row, "last_name"),
        dob=_get(row, "dob"),
        gender=data.get("gender"),
        nationality=_get(row, "nationality"),
        birth_country=data.get("birth_country"),
        identities=_identities_from_data(data),
        mobile=_get(row, "mobile"),
        telephone=_get(row, "phone"),
        company=company,
        address=_address(_get(row, "address")),
        tax_residency=_get(row, "tax_residency"),
        reference=data.get("reference"),
        metadata=data.get("metadata"),
        custodians=_custodians(_get(row, "custodians")),
    )


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return utc_now()


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    return None


def _row_name(row: Any) -> str | None:
    stored = _get(row, "name")
    if isinstance(stored, str) and stored:
        return stored
    parts = [
        part
        for part in (_get(row, "first_name"), _get(row, "middle_name"), _get(row, "last_name"))
        if isinstance(part, str) and part
    ]
    return " ".join(parts) or None


def _documents_from_row(row: Any) -> tuple[list[str], str | None]:
    raw = _loads(_get(row, "documents"), [])
    if not isinstance(raw, list):
        return [], None
    parsed: list[tuple[str, str, str | None]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        uid = item.get("uid")
        kind = item.get("type")
        if isinstance(uid, str) and isinstance(kind, str):
            country = item.get("issuing_country")
            parsed.append((uid, kind, country if isinstance(country, str) else None))
    return identity_details(parsed)


def _public_status(stored: Any) -> CheckStatus:
    """The firm's status vocabulary. A check the provider could not run reads as pending."""
    mapped = partner_status(stored) if isinstance(stored, str) else "pending"
    if mapped == "clear":
        return "clear"
    if mapped == "refer":
        return "refer"
    if mapped == "rejected":
        return "rejected"
    return "pending"


def check_from_row(row: Any) -> CheckOut:
    public = _public_status(_get(row, "status"))
    kind = _get(row, "kind")
    created_at = _as_datetime(_get(row, "created_at"))
    updated_at = _as_datetime(_get(row, "updated_at"))
    if kind == "identity":
        document_uids, issuing_country = _documents_from_row(row)
        return CheckOut(
            uid=str(_get(row, "uid")),
            kind="identity",
            status=public,
            created_at=created_at,
            updated_at=updated_at,
            document_uids=document_uids,
            issuing_country=issuing_country,
        )
    country = _get(row, "country")
    return CheckOut(
        uid=str(_get(row, "uid")),
        kind="aml",
        status=public,
        created_at=created_at,
        updated_at=updated_at,
        name=_row_name(row),
        dob=_as_date(_get(row, "dob")),
        country=country if isinstance(country, str) and country else None,
    )


def _draft(body: ClientCreate) -> ClientOut:
    return to_client_out(body, uid=new_id())


@dataclass(frozen=True)
class HeldFile:
    """One of the firm's uploaded documents: what it is, and where its bytes are."""

    uid: str
    kind: str
    side: str
    issuing_country: str | None
    object_key: str | None
    body: Any


@dataclass(frozen=True)
class IdentitySet:
    """The files an identity check sends: the newest selfie, the newest front of an identity
    document, and the newest back of that same document when there is one."""

    selfie: HeldFile
    front: HeldFile
    back: HeldFile | None


@dataclass(frozen=True)
class _Image:
    name: str
    content: bytes


@dataclass(frozen=True)
class _Submitted:
    """What the provider created for an identity check."""

    check: Any
    document_id: str
    photo_id: str


def identity_set(files: list[HeldFile]) -> IdentitySet:
    """Choose the files for an identity check. ``files`` is oldest first.

    Raises ``documents_required`` without a selfie and the front of an identity document. A back
    goes with the front when it is the same type of document from the same country.
    """
    newest = list(reversed(files))
    selfie = next((item for item in newest if item.kind == "selfie"), None)
    front = next(
        (item for item in newest if item.kind in ID_DOCUMENT_TYPES and item.side == "front"),
        None,
    )
    if selfie is None or front is None:
        raise Rejected("documents_required", DOCUMENTS_REQUIRED, 422)
    back = None
    if front.kind in TWO_SIDED_TYPES:
        back = next(
            (
                item
                for item in newest
                if item.kind == front.kind
                and item.side == "back"
                and item.issuing_country == front.issuing_country
            ),
            None,
        )
    return IdentitySet(selfie=selfie, front=front, back=back)


@dataclass
class _LoadedCheck:
    row: Any
    client: ClientOut
    client_id: int
    identity: IdentitySet | None
    document_uids: list[str] | None
    issuing_country: str | None


STORAGE_UNAVAILABLE = "The document could not be stored. Try again later."


class LiveStore:
    """Firm-API persistence. The session is supplied by the caller."""

    def __init__(
        self,
        session: Session,
        screening: ScreeningClient | None = None,
        *,
        resolver: Resolver | None = None,
    ) -> None:
        self._session = session
        self._screening = screening
        self._resolver = resolver

    async def create_client(self, body: ClientCreate, *, tenant_id: int) -> ClientOut:
        draft = _draft(body)
        contact = await self._session.execute(
            INSERT_CONTACT,
            _contact_params(draft, tenant_id=tenant_id, contact_uid=new_id()),
        )
        contact_id = contact.scalar()
        client = await self._session.execute(
            INSERT_CLIENT,
            {"tenant_id": tenant_id, "uid": draft.uid, "contact_id": contact_id},
        )
        client_id = client.scalar()
        if draft.address is not None:
            await self._session.execute(
                INSERT_ADDRESS,
                _address_params(draft.address, tenant_id=tenant_id, client_id=int(client_id)),
            )
        return draft

    async def get_client(self, uid: str, *, tenant_id: int) -> ClientOut | None:
        result = await self._session.execute(SELECT_CLIENT, {"tenant_id": tenant_id, "uid": uid})
        row = result.first()
        if row is None:
            return None
        return client_from_row(row)

    async def list_clients(self, *, tenant_id: int, limit: int, offset: int) -> list[ClientOut]:
        result = await self._session.execute(
            SELECT_CLIENTS, {"tenant_id": tenant_id, "limit": limit, "offset": offset}
        )
        return [client_from_row(row) for row in result.all()]

    async def patch_client(
        self, uid: str, body: ClientPatch, *, tenant_id: int
    ) -> ClientOut | None:
        result = await self._session.execute(SELECT_CLIENT, {"tenant_id": tenant_id, "uid": uid})
        row = result.first()
        if row is None:
            return None
        current = client_from_row(row)
        updates = body.model_dump(exclude_unset=True)
        updated = apply_patch(current, body)
        params = _contact_params(
            updated, tenant_id=tenant_id, contact_uid=str(_get(row, "contact_uid"))
        )
        params["contact_id"] = _get(row, "contact_id")
        await self._session.execute(UPDATE_CONTACT, params)
        if "address" in updates:
            client_id = int(_get(row, "client_id"))
            await self._session.execute(
                DELETE_ADDRESSES, {"client_id": client_id, "tenant_id": tenant_id}
            )
            if updated.address is not None:
                await self._session.execute(
                    INSERT_ADDRESS,
                    _address_params(updated.address, tenant_id=tenant_id, client_id=client_id),
                )
        return updated

    async def create_document(
        self, client_uid: str, body: HeldDocument, *, tenant_id: int
    ) -> DocumentOut:
        found = await self._session.execute(
            SELECT_CLIENT_KEY, {"uid": client_uid, "tenant_id": tenant_id}
        )
        client = found.first()
        if client is None:
            raise Rejected("not_found", "No client with that id.", 404)
        uid = new_id()
        ext = body.file_name.rsplit(".", 1)[-1]
        key = storage.tenant_upload_key(uid, ext, contact_uid=str(_get(client, "contact_uid")))
        bucket = await self._bucket(tenant_id)
        # The row first, then the object: a failed upload rolls the row back with it.
        await self._session.execute(
            INSERT_DOCUMENT,
            {
                "tenant_id": tenant_id,
                "uid": uid,
                "client_id": int(_get(client, "id")),
                "issuing_country": body.issuing_country,
                "classification": body.type,
                "side": body.side,
                "object_key": key,
                "body": _dump(
                    {
                        "file_name": body.file_name,
                        "content_type": body.content_type,
                        "size_bytes": len(body.content),
                        "bucket": bucket or None,
                    }
                ),
            },
        )
        try:
            await storage.put_document(key, body.content, body.content_type, bucket=bucket or None)
        except Exception as exc:  # noqa: BLE001 - any store failure keeps nothing and says so
            logger.warning(
                "a firm document could not be stored: %s",
                type(exc).__name__,
                extra={"event": "partner_document_store_failed", "tenant_id": tenant_id},
            )
            raise Rejected("storage_unavailable", STORAGE_UNAVAILABLE, 503) from exc
        return DocumentOut(
            uid=uid, type=body.type, side=body.side, issuing_country=body.issuing_country
        )

    async def _bucket(self, tenant_id: int) -> str:
        """The firm's document bucket, or the shared one; blank when storage is not set up."""
        return await tenant_storage.document_bucket(self._session, tenant_id)  # type: ignore[arg-type]

    async def bind_provider_client(
        self, client_uid: str, body: CheckCreate, *, tenant_id: int
    ) -> bool:
        """Validate the check and persist the provider client id.

        Returns true when this call stored an id that was not already on the row.
        The address upload, the provider check, and the local insert happen later,
        after that id has committed.
        """
        loaded = await self._load_check(client_uid, body, tenant_id=tenant_id)
        if self._screening is None:
            return False
        _provider_id, newly = await self._provider_client_id(
            self._screening,
            loaded.client,
            loaded.row,
            tenant_id=tenant_id,
            client_id=loaded.client_id,
        )
        return newly

    async def create_check(
        self,
        client_uid: str,
        body: CheckCreate,
        *,
        tenant_id: int,
        upload_address: bool | None = None,
        already_bound: bool = False,
    ) -> CheckOut:
        loaded = await self._load_check(client_uid, body, tenant_id=tenant_id)
        started = await self._provider_check_id(
            loaded.client,
            loaded.row,
            body,
            tenant_id=tenant_id,
            client_id=loaded.client_id,
            identity=loaded.identity,
            upload_address=upload_address,
            already_bound=already_bound,
        )
        provider_check_id, monitored = started if started is not None else (None, False)
        uid = new_id()
        inserted = await self._session.execute(
            INSERT_CHECK,
            {
                "tenant_id": tenant_id,
                "uid": uid,
                "client_id": loaded.client_id,
                "kind": body.kind,
                "provider_check_id": provider_check_id,
                "monitoring": monitored,
            },
        )
        stamp = inserted.first()
        if stamp is None and provider_check_id is not None:
            raise Rejected("check_unavailable", "The check could not be started.", 502)
        uid = str(_get(stamp, "uid") or uid)
        status = _public_status(_get(stamp, "status") or "pending")
        created_at = _as_datetime(_get(stamp, "created_at")) if stamp is not None else utc_now()
        updated_at = _as_datetime(_get(stamp, "updated_at")) if stamp is not None else created_at
        if body.kind == "identity":
            return CheckOut(
                uid=uid,
                kind="identity",
                status=status,
                created_at=created_at,
                updated_at=updated_at,
                document_uids=loaded.document_uids or [],
                issuing_country=loaded.issuing_country,
            )
        return CheckOut(
            uid=uid,
            kind="aml",
            status=status,
            created_at=created_at,
            updated_at=updated_at,
            name=screened_name(loaded.client),
            dob=loaded.client.dob,
            country=loaded.client.address.country if loaded.client.address is not None else None,
        )

    async def _load_check(
        self, client_uid: str, body: CheckCreate, *, tenant_id: int
    ) -> _LoadedCheck:
        found = await self._session.execute(
            SELECT_CLIENT, {"uid": client_uid, "tenant_id": tenant_id}
        )
        row = found.first()
        if row is None:
            raise Rejected("not_found", "No client with that id.", 404)
        client = client_from_row(row)
        client_id = int(_get(row, "client_id"))
        document_uids: list[str] | None = None
        issuing_country: str | None = None
        identity: IdentitySet | None = None
        if body.kind == "aml" and client.address is None:
            raise Rejected("address_country_required", ADDRESS_COUNTRY_REQUIRED, 422)
        if body.kind == "identity":
            stored = await self._session.execute(
                SELECT_DOCUMENTS, {"tenant_id": tenant_id, "client_id": client_id}
            )
            files = [_held_file(item) for item in stored.all()]
            identity = identity_set(files)
            document_uids, issuing_country = identity_details(
                [(item.uid, item.kind, item.issuing_country) for item in files]
            )
        return _LoadedCheck(
            row=row,
            client=client,
            client_id=client_id,
            identity=identity,
            document_uids=document_uids,
            issuing_country=issuing_country,
        )

    async def get_check(self, uid: str, *, tenant_id: int) -> CheckOut | None:
        result = await self._session.execute(SELECT_CHECK, {"tenant_id": tenant_id, "uid": uid})
        row = result.first()
        if row is None:
            return None
        return check_from_row(row)

    async def checks_for(self, client_uid: str, *, tenant_id: int) -> ChecksOut:
        result = await self._session.execute(
            SELECT_CHECKS, {"tenant_id": tenant_id, "client_uid": client_uid}
        )
        return checks_overview([check_from_row(row) for row in result.all()])

    async def put_custodian(
        self, uid: str, body: CustodianPut, *, tenant_id: int
    ) -> ClientOut | None:
        result = await self._session.execute(SELECT_CLIENT, {"tenant_id": tenant_id, "uid": uid})
        row = result.first()
        if row is None:
            return None
        await self._session.execute(
            UPSERT_CUSTODIAN,
            {
                "tenant_id": tenant_id,
                "entity_id": int(_get(row, "contact_id")),
                "provider": body.provider,
                "external_id": body.external_id,
            },
        )
        current = client_from_row(row)
        kept = [item for item in current.custodians if item.provider != body.provider]
        kept.append(body)
        return current.model_copy(update={"custodians": kept})

    async def get_webhook(self, *, tenant_id: int) -> WebhookOut:
        result = await self._session.execute(SELECT_WEBHOOK, {"tenant_id": tenant_id})
        row = result.first()
        if row is None:
            return WebhookOut(url=None)
        url = _get(row, "url")
        return WebhookOut(url=url if isinstance(url, str) else None)

    async def put_webhook(self, url: str, *, tenant_id: int) -> WebhookCreated:
        try:
            url = await check_url(url, resolver=self._resolver)
        except UnsafeWebhookUrl as exc:
            raise Rejected("invalid_webhook_url", str(exc), 422) from exc
        secret = secrets.token_urlsafe(24)
        await self._session.execute(
            UPSERT_WEBHOOK,
            {"tenant_id": tenant_id, "url": url, "secret_ct": encrypt(secret)},
        )
        return WebhookCreated(url=url, secret=secret)

    async def delete_webhook(self, *, tenant_id: int) -> WebhookOut:
        await self._session.execute(DELETE_WEBHOOK, {"tenant_id": tenant_id})
        return WebhookOut(url=None)

    async def _provider_check_id(
        self,
        client: ClientOut,
        row: Any,
        body: CheckCreate,
        *,
        tenant_id: int,
        client_id: int,
        identity: IdentitySet | None,
        upload_address: bool | None = None,
        already_bound: bool = False,
    ) -> tuple[str, bool] | None:
        """Start the check at ComplyCube: its id, and whether ComplyCube monitors it (always asked
        for an AML screen; a plan without monitoring screens without it). None with no provider."""
        screening = self._screening
        if screening is None:
            return None
        existing = _get(row, "provider_client_id")
        if already_bound and isinstance(existing, str) and existing:
            provider_client_id = existing
        else:
            provider_client_id, _newly = await self._provider_client_id(
                screening, client, row, tenant_id=tenant_id, client_id=client_id
            )
        # A saved client id used to skip the address. A retry still uploads when
        # line, city, and country are all present. A country on its own is enough
        # to start AML. ``upload_address`` no longer gates that.
        del upload_address
        await self._ensure_provider_address(screening, provider_client_id, client)
        submitted: _Submitted | None = None
        monitored = False
        if body.kind == "identity" and identity is None:
            raise Rejected("documents_required", DOCUMENTS_REQUIRED, 422)
        try:
            if identity is not None and body.kind == "identity":
                submitted = await _submit_identity(
                    screening,
                    provider_client_id,
                    identity,
                    selfie=await self._image(identity.selfie, tenant_id=tenant_id),
                    front=await self._image(identity.front, tenant_id=tenant_id),
                    back=(
                        await self._image(identity.back, tenant_id=tenant_id)
                        if identity.back is not None
                        else None
                    ),
                )
                created = submitted.check
            else:
                # Ongoing monitoring is always on (docs/aml.md §8).
                created = await screening.create_screening(provider_client_id, monitoring=True)
                monitored = monitoring_of(created, True)
        except (httpx.HTTPError, ComplyCubeNotConfigured, KeyError, TypeError) as exc:
            raise Rejected("check_unavailable", "The check could not be started.", 502) from exc
        check_id = created.get("id") if isinstance(created, dict) else None
        if not isinstance(check_id, str) or not check_id:
            raise Rejected("check_unavailable", "The check could not be started.", 502)
        if submitted is not None and identity is not None:
            await self._record_provider_files(
                submitted, identity, tenant_id=tenant_id, client_id=client_id
            )
        return check_id, monitored

    async def _image(self, held: HeldFile, *, tenant_id: int) -> _Image:
        """The bytes of one held file: from object storage, or, for a row written before files
        moved there, from the base64 in ``body``."""
        if held.object_key is None:
            legacy_name, content = _decode_file(held.body)
            return _Image(name=legacy_name, content=content)
        meta = _metadata(held.body)
        bucket = meta.get("bucket")
        if not isinstance(bucket, str) or not bucket:
            bucket = await self._bucket(tenant_id)
        try:
            content, _content_type = await storage.get_document(
                held.object_key, bucket=bucket or None
            )
        except Exception as exc:  # noqa: BLE001 - any storage failure fails the check closed
            raise Rejected("check_unavailable", "The check could not be started.", 502) from exc
        name = meta.get("file_name")
        fallback = "back.jpg" if held.side == "back" else "front.jpg"
        return _Image(name=name if isinstance(name, str) and name else fallback, content=content)

    async def _record_provider_files(
        self, submitted: _Submitted, identity: IdentitySet, *, tenant_id: int, client_id: int
    ) -> None:
        """Point the provider's document and live photo at the files the firm sent."""
        await self._session.execute(
            UPSERT_PROVIDER_DOCUMENT,
            {
                "tenant_id": tenant_id,
                "client_id": client_id,
                "provider_document_id": submitted.document_id,
                "document_type": PROVIDER_DOCUMENT_TYPES.get(identity.front.kind),
                "issuing_country": identity.front.issuing_country,
                "object_key": identity.front.object_key,
                "back_object_key": identity.back.object_key if identity.back else None,
            },
        )
        selfie = _metadata(identity.selfie.body)
        size = selfie.get("size_bytes")
        content_type = selfie.get("content_type")
        await self._session.execute(
            UPSERT_PROVIDER_PHOTO,
            {
                "tenant_id": tenant_id,
                "client_id": client_id,
                "provider_photo_id": submitted.photo_id,
                "content_type": content_type if isinstance(content_type, str) else None,
                "size_bytes": size if isinstance(size, int) else None,
                "object_key": identity.selfie.object_key,
            },
        )

    async def _provider_client_id(
        self,
        screening: ScreeningClient,
        client: ClientOut,
        row: Any,
        *,
        tenant_id: int,
        client_id: int,
    ) -> tuple[str, bool]:
        """Store the provider client id. The address is uploaded by the caller."""
        existing = _get(row, "provider_client_id")
        if isinstance(existing, str) and existing:
            return existing, False
        external_id = str(_get(row, "contact_uid"))
        try:
            # Only this deployment's clients count: another one on the same key can hold the uid.
            found = await screening.find_client_by_external_id(
                external_id, env=environment() or None
            )
            if found:
                provider_client_id = found
            else:
                # Our ids and the firm's, never the firm's own ``metadata``.
                metadata = client_metadata(
                    origin="partner",
                    firm_uid=_get(row, "tenant_uid"),
                    firm=_get(row, "tenant_name"),
                    contact_uid=external_id,
                    client_uid=client.uid,
                    reference=client.reference,
                )
                payload = client_payload(
                    client.model_dump(mode="json"), external_id=external_id, metadata=metadata
                )
                created = await screening.create_client(payload)
                provider_client_id = str(created["id"])
            await self._session.execute(
                UPDATE_PROVIDER_CLIENT,
                {
                    "tenant_id": tenant_id,
                    "client_id": client_id,
                    "provider_client_id": provider_client_id,
                },
            )
        except (httpx.HTTPError, ComplyCubeNotConfigured) as exc:
            raise Rejected("check_unavailable", "The check could not be started.", 502) from exc
        except (KeyError, TypeError) as exc:
            raise Rejected("check_unavailable", "The check could not be started.", 502) from exc
        return provider_client_id, True

    async def _ensure_provider_address(
        self, screening: ScreeningClient, provider_client_id: str, client: ClientOut
    ) -> bool:
        """Upload an address when the provider client has none.

        Nothing is sent unless line, city, and country are all present. A country
        on its own does not fail the check.
        """
        try:
            existing = await screening.list_addresses(provider_client_id)
        except (httpx.HTTPError, ComplyCubeNotConfigured) as exc:
            raise Rejected("check_unavailable", "The check could not be started.", 502) from exc
        if existing:
            return True
        address = client.address.model_dump(mode="json") if client.address is not None else {}
        sent = address_payload(provider_client_id, address)
        if sent is None:
            return False
        try:
            await screening.create_address(sent)
        except (httpx.HTTPError, ComplyCubeNotConfigured) as exc:
            raise Rejected("check_unavailable", "The check could not be started.", 502) from exc
        return True


def _held_file(row: Any) -> HeldFile:
    country = _get(row, "issuing_country")
    key = _get(row, "object_key")
    return HeldFile(
        uid=str(_get(row, "uid")),
        kind=str(_get(row, "classification")),
        side="back" if _get(row, "side") == "back" else "front",
        issuing_country=country if isinstance(country, str) else None,
        object_key=key if isinstance(key, str) and key else None,
        body=_get(row, "body"),
    )


def _metadata(body: Any) -> dict[str, Any]:
    meta = _loads(body, {})
    return meta if isinstance(meta, dict) else {}


def _decode_file(value: Any) -> tuple[str, bytes]:
    raw = _loads(value, {})
    if not isinstance(raw, dict):
        raise Rejected("documents_required", DOCUMENTS_REQUIRED, 422)
    data = raw.get("data")
    if not isinstance(data, str) or not data:
        raise Rejected("documents_required", DOCUMENTS_REQUIRED, 422)
    name = raw.get("file_name")
    try:
        content = base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise Rejected("documents_required", DOCUMENTS_REQUIRED, 422) from exc
    return (name if isinstance(name, str) and name else "front.jpg", content)


_PROVIDER_SUFFIXES = (".jpg", ".jpeg", ".png", ".pdf")


def _provider_name(name: str, fallback: str) -> str:
    """The provider takes an image name ending in jpg, jpeg, png, or pdf."""
    return name if name.lower().endswith(_PROVIDER_SUFFIXES) else fallback


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


async def _submit_identity(
    screening: ScreeningClient,
    provider_client_id: str,
    identity: IdentitySet,
    *,
    selfie: _Image,
    front: _Image,
    back: _Image | None,
) -> _Submitted:
    """Create the provider document, upload its front and its back when there is one, upload the
    selfie as a live photo, and start the identity check. Each image is sent as base64 JSON."""
    created = await screening.create_document(
        provider_client_id,
        document_type=PROVIDER_DOCUMENT_TYPES[identity.front.kind],
        issuing_country=identity.front.issuing_country or "",
    )
    document_id = str(created["id"])
    await screening.upload_document_front(
        document_id, file_name=_provider_name(front.name, "front.jpg"), data=_b64(front.content)
    )
    if back is not None:
        await screening.upload_document_back(
            document_id, file_name=_provider_name(back.name, "back.jpg"), data=_b64(back.content)
        )
    photo = await screening.create_live_photo(provider_client_id, data=_b64(selfie.content))
    photo_id = str(photo["id"])
    check = await screening.create_identity_check(
        provider_client_id, document_id=document_id, live_photo_id=photo_id
    )
    return _Submitted(check=check, document_id=document_id, photo_id=photo_id)
