"""Turn a ComplyCube webhook into typed ``compliance.*`` upserts.

The receiver only stores the event (``accept_event``): one ``compliance.events`` row per provider
event id, so a redelivery is never queued twice. An event without an id is refused. The worker's
consumer (``app.complycube.consumer``) applies the stored rows later.

The webhook body is not trusted for a status. The consumer fetches the check, the client, and any
address, document, or live photo the check points at, and records what the provider says now —
so events applied out of order still land on the provider's current state. We do not list the
provider's whole account: that listing mixes tenants.

An event is taken in only for a client this database holds, and only when ComplyCube's
``externalId`` for it is the contact we hold it for: the ComplyCube client id and the external id
must both match. One account serves several deployments, every sandbox webhook sees every sandbox
event, and ``externalId`` is not unique at ComplyCube, so anything else is another deployment's.
It is marked processed and nothing of it is stored. A check we start is stored in the same
transaction that starts it, and the poller asks about it, so an event that outruns that
transaction loses nothing.

``record_event`` returns the check's ``(old_status, new_status)`` so the consumer routes a change
once: the same status twice is not a change.

A reviewer's decision stands against later events. Once ``decided_at`` is set, the provider's
status does not overwrite it, unless the provider now reports a match we have not seen or the
event is a monitoring alert. Then the check goes back to ``refer`` and the decision is cleared.

pgmq is the wake-up when the extension is installed. The inbox row is the durable record either
way, so a consumer can also ``FOR UPDATE SKIP LOCKED``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, NamedTuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.complycube.client import ScreeningClient
from app.complycube.outcomes import DECIDED, provider_outcome, public_status

logger = logging.getLogger(__name__)

CHECK_EVENTS = frozenset(
    {
        "check.pending",
        "check.completed",
        "check.completed.clear",
        "check.completed.attention",
        "check.completed.rejected",
        "check.completed.match_confirmed",
        "check.failed",
        "check.updated",
        "check.monitoring.attention",
    }
)
CLIENT_EVENTS = frozenset({"client.updated"})
#: Events that are an ongoing-monitoring alert on a check that already finished.
MONITORING_EVENTS = frozenset({"check.monitoring.attention"})


class MissingEventId(ValueError):
    """The event has no id, so it cannot be deduplicated. It is refused."""

    def __init__(self) -> None:
        super().__init__("A screening event must carry an id.")


def event_id(event: dict[str, Any]) -> str | None:
    """The provider's event id, or None when the event has none."""
    value = event.get("id")
    if isinstance(value, str) and value.strip():
        return value
    return None


class StatusChange(NamedTuple):
    """The check's status before and after one event. ``old`` is None for a check this event
    created; both are None when the event carried no check."""

    old: str | None
    new: str | None


@dataclass
class SyncPlan:
    """Rows to write. ``tenant_id`` is None when the provider id is unknown."""

    provider_event_id: str
    event_type: str
    payload: dict[str, Any]
    tenant_id: int | None = None
    client: dict[str, Any] | None = None
    addresses: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    live_photos: list[dict[str, Any]] = field(default_factory=list)
    check: dict[str, Any] | None = None
    #: ``compliance.checks.id`` once ``record_event`` has written the check.
    check_pk: int | None = None


def _country(document: dict[str, Any] | None) -> str | None:
    if not document:
        return None
    value = document.get("issuingCountry")
    return str(value) if value else None


async def build_plan(client: ScreeningClient, event: dict[str, Any]) -> SyncPlan:
    """Fetch what the event points at. Raises ``MissingEventId`` for an event without an id."""
    provider_event_id = event_id(event)
    if provider_event_id is None:
        raise MissingEventId()
    event_type = str(event.get("type") or "")
    payload = event.get("payload")
    body = payload if isinstance(payload, dict) else {}
    plan = SyncPlan(provider_event_id=provider_event_id, event_type=event_type, payload=event)
    if event_type in CLIENT_EVENTS:
        client_id = str(body.get("id") or "")
        if client_id:
            fetched = await client.get_client(client_id)
            plan.client = fetched if isinstance(fetched, dict) else None
        return plan
    if event_type not in CHECK_EVENTS:
        return plan
    check_id = str(body.get("id") or "")
    if not check_id:
        return plan
    check = await client.get_check(check_id)
    if not isinstance(check, dict):
        return plan
    plan.check = check
    linked_client = check.get("clientId")
    if isinstance(linked_client, str) and linked_client:
        fetched_client = await client.get_client(linked_client)
        if isinstance(fetched_client, dict):
            plan.client = fetched_client
    for key, bucket, getter in (
        ("addressId", plan.addresses, client.get_address),
        ("documentId", plan.documents, client.get_document),
        ("livePhotoId", plan.live_photos, client.get_live_photo),
    ):
        resource_id = check.get(key)
        if isinstance(resource_id, str) and resource_id:
            fetched = await getter(resource_id)
            if isinstance(fetched, dict):
                bucket.append(fetched)
    return plan


def status_for(plan: SyncPlan, nationalities: list[str]) -> str | None:
    if plan.check is None:
        return None
    document = plan.documents[0] if plan.documents else None
    provider_status = plan.check.get("status")
    return public_status(
        provider_status if isinstance(provider_status, str) else None,
        provider_outcome(plan.check),
        issuing_country=_country(document),
        nationalities=nationalities,
    )


# ---- matches ------------------------------------------------------------------------------------


def _first_list(source: dict[str, Any], *keys: str) -> list[Any] | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, list):
            return value
    return None


def _strings(value: object) -> list[str]:
    """A list of names from a list of strings, a list of ``{"name": ...}``, or one string."""
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            out.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("title") or item.get("type")
            if isinstance(name, str) and name:
                out.append(name)
    return out


def _score(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _match_id(item: dict[str, Any]) -> str:
    for key in ("id", "matchId", "entityId"):
        value = item.get(key)
        if isinstance(value, str | int) and not isinstance(value, bool) and str(value):
            return str(value)
    canonical = json.dumps(item, sort_keys=True, default=str)
    return "h_" + hashlib.sha256(canonical.encode()).hexdigest()[:32]


#: ``details.classifications`` keys, as the match types the console shows.
_CLASSIFICATIONS = {
    "pep": "pep",
    "watchlist": "watchlist",
    "adverseMedia": "adverse_media",
    "otherLists": "other_lists",
}
_BIRTH_DATE_TYPES = {"birth", "date_of_birth", "dateofbirth", "dob"}


def _date_text(value: object) -> str | None:
    """A ComplyCube ``{day, month, year}`` as ``YYYY-MM-DD``, or as much of it as is known."""
    if isinstance(value, str) and value:
        return value
    if not isinstance(value, dict):
        return None
    year, month, day = (str(value.get(k) or "").strip() for k in ("year", "month", "day"))
    if not year:
        return None
    parts = [year]
    if month:
        parts.append(month.zfill(2))
        if day:
            parts.append(day.zfill(2))
    return "-".join(parts)


def _details_name(details: dict[str, Any]) -> str | None:
    name = details.get("entityName")
    if isinstance(name, str) and name:
        return name
    for entry in _first_list(details, "names") or []:
        if not isinstance(entry, dict) or entry.get("type") not in (None, "primary"):
            continue
        joined = " ".join(
            str(entry[k]) for k in ("firstName", "middleName", "lastName") if entry.get(k)
        )
        if joined or entry.get("entityName"):
            return joined or str(entry["entityName"])
    return None


def _details_birth_date(details: dict[str, Any]) -> str | None:
    for entry in _first_list(details, "dates") or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type") or "").replace(" ", "_").lower()
        if kind in _BIRTH_DATE_TYPES:
            return _date_text(entry.get("date"))
    return None


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def matches_from_check(check: dict[str, Any]) -> list[dict[str, Any]]:
    """The screening matches on a check, as ``compliance.matches`` columns.

    Read from ``result.breakdown.matches`` (falling back to ``result.matches``, ``matches`` and
    ``hits``). Each item follows ComplyCube's AML screening check shape (docs.complycube.com, AML
    Screening Check): ``id`` and ``score`` at the top, the rest under ``details`` — ``entityName``
    (or ``names``), ``classifications`` (pep / watchlist / adverseMedia / otherLists), ``countries``
    (``[{type, country}]``), ``dates`` (a birth date as ``{day, month, year}``) and ``references``
    (``[{source, …}]``). Top-level fields are read as a fallback; ``raw`` keeps the whole item. An
    item with no provider id gets a stable content hash, so the same item fetched twice is one row.
    """
    result = check.get("result")
    result = result if isinstance(result, dict) else {}
    breakdown = result.get("breakdown")
    breakdown = breakdown if isinstance(breakdown, dict) else {}
    items = (
        _first_list(breakdown, "matches")
        or _first_list(result, "matches")
        or _first_list(check, "matches", "hits")
        or []
    )
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        details = item.get("details")
        details = details if isinstance(details, dict) else {}
        name = (
            _details_name(details)
            or item.get("name")
            or item.get("entityName")
            or item.get("fullName")
        )
        dob_value: object = (
            _details_birth_date(details) or item.get("dob") or item.get("dateOfBirth")
        )
        if dob_value is None:
            dobs = _first_list(item, "dobs", "datesOfBirth")
            dob_value = dobs[0] if dobs else None
        classifications = details.get("classifications")
        classifications = classifications if isinstance(classifications, dict) else {}
        match_types = [
            label for key, label in _CLASSIFICATIONS.items() if classifications.get(key)
        ] or (
            _strings(item.get("matchTypes") or item.get("types") or item.get("categories"))
            or _strings(item.get("matchType"))
        )
        countries = _unique(
            [
                str(entry.get("country"))
                for entry in _first_list(details, "countries") or []
                if isinstance(entry, dict) and entry.get("country")
            ]
        ) or (
            _strings(item.get("countries") or item.get("nationalities"))
            or _strings(item.get("country"))
        )
        sources = _unique(
            [
                str(entry.get("source"))
                for entry in _first_list(details, "references") or []
                if isinstance(entry, dict) and entry.get("source")
            ]
        ) or _strings(item.get("sources") or item.get("lists") or item.get("datasets"))
        out.append(
            {
                "provider_match_id": _match_id(item),
                "name": name if isinstance(name, str) and name else None,
                "match_types": match_types,
                "score": _score(item.get("score", item.get("matchScore"))),
                "dob": str(dob_value) if isinstance(dob_value, str | int) and dob_value else None,
                "countries": countries,
                "sources": sources,
                "raw": item,
            }
        )
    return out


# ---- SQL ----------------------------------------------------------------------------------------

CLAIM_SQL = text(
    """
    SELECT id, payload
      FROM compliance.events
     WHERE processed_at IS NULL AND attempts < :max_attempts
     ORDER BY id
     FOR UPDATE SKIP LOCKED
     LIMIT :n
    """
)

ENQUEUE_SQL = text("SELECT pgmq.send('compliance_sync', CAST(:payload AS jsonb))")

PGMQ_PRESENT = text("SELECT 1 FROM pg_extension WHERE extname = 'pgmq'")

# A client we hold, with the contact we hold it for: the uid ComplyCube's ``externalId`` must match.
_LOOKUP_CLIENT = text(
    """
    SELECT cl.id, cl.tenant_id, c.uid AS contact_uid
      FROM compliance.clients cl
      LEFT JOIN crm.contacts c ON c.id = cl.contact_id AND c.tenant_id = cl.tenant_id
     WHERE cl.provider_client_id = :provider_client_id
    """
)

_INSERT_EVENT = text(
    """
    INSERT INTO compliance.events (tenant_id, provider_event_id, event_type, payload)
    VALUES (:tenant_id, :provider_event_id, :event_type, CAST(:payload AS jsonb))
    ON CONFLICT (provider_event_id) DO NOTHING
    RETURNING id
    """
)

_UPSERT_ADDRESS = text(
    """
    INSERT INTO compliance.addresses (
      tenant_id, client_id, provider_address_id, address_type, property_number,
      building_name, line, city, state, postal_code, country, from_date, to_date, body
    ) VALUES (
      :tenant_id, :client_id, :provider_address_id, :address_type, :property_number,
      :building_name, :line, :city, :state, :postal_code, :country, :from_date, :to_date,
      CAST(:body AS jsonb)
    )
    ON CONFLICT (client_id)
    DO UPDATE SET provider_address_id = EXCLUDED.provider_address_id,
                  address_type = EXCLUDED.address_type,
                  property_number = EXCLUDED.property_number,
                  building_name = EXCLUDED.building_name,
                  line = EXCLUDED.line, city = EXCLUDED.city, state = EXCLUDED.state,
                  postal_code = EXCLUDED.postal_code, country = EXCLUDED.country,
                  from_date = EXCLUDED.from_date, to_date = EXCLUDED.to_date,
                  body = EXCLUDED.body, updated_at = now()
    """
)

_UPSERT_DOCUMENT = text(
    """
    INSERT INTO compliance.documents (
      tenant_id, client_id, provider_document_id, document_type, document_number,
      issuing_country, issuing_state, classification, object_key, body
    ) VALUES (
      :tenant_id, :client_id, :provider_document_id, :document_type, :document_number,
      :issuing_country, :issuing_state, :classification, :object_key, CAST(:body AS jsonb)
    )
    ON CONFLICT (provider_document_id) WHERE provider_document_id IS NOT NULL
    DO UPDATE SET document_type = EXCLUDED.document_type,
                  document_number = EXCLUDED.document_number,
                  issuing_country = EXCLUDED.issuing_country,
                  issuing_state = EXCLUDED.issuing_state,
                  classification = EXCLUDED.classification,
                  body = EXCLUDED.body,
                  updated_at = now()
    RETURNING id
    """
)

_UPSERT_PHOTO = text(
    """
    INSERT INTO compliance.live_photos (
      tenant_id, client_id, provider_photo_id, perform_liveness_check, content_type,
      size_bytes, download_link, object_key, body
    ) VALUES (
      :tenant_id, :client_id, :provider_photo_id, :perform_liveness_check, :content_type,
      :size_bytes, :download_link, :object_key, CAST(:body AS jsonb)
    )
    ON CONFLICT (provider_photo_id) WHERE provider_photo_id IS NOT NULL
    DO UPDATE SET perform_liveness_check = EXCLUDED.perform_liveness_check,
                  content_type = EXCLUDED.content_type,
                  size_bytes = EXCLUDED.size_bytes,
                  download_link = EXCLUDED.download_link,
                  body = EXCLUDED.body, updated_at = now()
    """
)

# The row lock makes the (old, new) pair exact: a second consumer applying the same check waits
# here and then reads the status the first one wrote.
_LOCK_CHECK = text(
    """
    SELECT id, status, decided_at, monitoring
      FROM compliance.checks
     WHERE provider_check_id = :provider_check_id
       FOR UPDATE
    """
)

_UPSERT_CHECK = text(
    """
    INSERT INTO compliance.checks (
      tenant_id, client_id, document_id, kind, status, provider_check_id,
      provider_type, provider_status, outcome, entity_name, enable_monitoring, monitoring,
      client_consent, provider_document_id, provider_address_id,
      provider_live_photo_id, provider_live_video_id, session_id, redirect_url,
      result, body
    ) VALUES (
      :tenant_id, :client_id, :document_id, :kind, :status, :provider_check_id,
      :provider_type, :provider_status, :outcome, :entity_name, :enable_monitoring,
      COALESCE(:enable_monitoring, false),
      :client_consent, :provider_document_id, :provider_address_id,
      :provider_live_photo_id, :provider_live_video_id, :session_id, :redirect_url,
      CAST(:result AS jsonb), CAST(:body AS jsonb)
    )
    ON CONFLICT (provider_check_id) WHERE provider_check_id IS NOT NULL
    DO UPDATE SET status = EXCLUDED.status,
                  document_id = COALESCE(EXCLUDED.document_id, compliance.checks.document_id),
                  provider_type = EXCLUDED.provider_type,
                  provider_status = EXCLUDED.provider_status,
                  outcome = EXCLUDED.outcome,
                  entity_name = EXCLUDED.entity_name,
                  enable_monitoring = EXCLUDED.enable_monitoring,
                  monitoring = COALESCE(EXCLUDED.enable_monitoring, compliance.checks.monitoring),
                  client_consent = EXCLUDED.client_consent,
                  provider_document_id = EXCLUDED.provider_document_id,
                  provider_address_id = EXCLUDED.provider_address_id,
                  provider_live_photo_id = EXCLUDED.provider_live_photo_id,
                  provider_live_video_id = EXCLUDED.provider_live_video_id,
                  result = EXCLUDED.result,
                  body = EXCLUDED.body,
                  session_id = COALESCE(EXCLUDED.session_id, compliance.checks.session_id),
                  decided_by = CASE WHEN :reopen THEN NULL ELSE compliance.checks.decided_by END,
                  decided_at = CASE WHEN :reopen THEN NULL ELSE compliance.checks.decided_at END,
                  decision_comment = CASE WHEN :reopen THEN NULL
                                          ELSE compliance.checks.decision_comment END,
                  alerted_at = CASE WHEN :alerted THEN now()
                                    ELSE compliance.checks.alerted_at END,
                  updated_at = now()
    RETURNING id
    """
)

_UPSERT_MATCH = text(
    """
    INSERT INTO compliance.matches (
      tenant_id, check_id, provider_match_id, name, match_types, score, dob, countries,
      sources, raw
    ) VALUES (
      :tenant_id, :check_id, :provider_match_id, :name, CAST(:match_types AS text[]), :score,
      :dob, CAST(:countries AS text[]), CAST(:sources AS text[]), CAST(:raw AS jsonb)
    )
    ON CONFLICT (check_id, provider_match_id)
    DO UPDATE SET name = EXCLUDED.name, match_types = EXCLUDED.match_types,
                  score = EXCLUDED.score, dob = EXCLUDED.dob, countries = EXCLUDED.countries,
                  sources = EXCLUDED.sources, raw = EXCLUDED.raw, updated_at = now()
    RETURNING (xmax = 0) AS inserted
    """
)

_UPDATE_CLIENT = text(
    """
    UPDATE compliance.clients
       SET provider_client_id = COALESCE(:provider_client_id, provider_client_id),
           entity_name = :entity_name, email = :email, mobile = :mobile,
           telephone = :telephone, external_id = :external_id, joined_date = :joined_date,
           client_type = :client_type, first_name = :first_name, middle_name = :middle_name,
           last_name = :last_name, dob = :dob, gender = :gender, nationality = :nationality,
           birth_country = :birth_country, ssn = :ssn,
           social_insurance_number = :social_insurance_number,
           national_identity_number = :national_identity_number,
           tax_identification_number = :tax_identification_number,
           company_name = :company_name, website = :website,
           registration_number = :registration_number,
           incorporation_country = :incorporation_country,
           incorporation_type = :incorporation_type,
           body = CAST(:body AS jsonb),
           updated_at = now()
     WHERE id = :client_id AND tenant_id IS NOT DISTINCT FROM :tenant_id
    """
)

_NATIONALITIES = text(
    """
    SELECT c.nationality
      FROM compliance.clients cl
      JOIN crm.contacts c ON c.id = cl.contact_id AND c.tenant_id = cl.tenant_id
     WHERE cl.id = :client_id AND cl.tenant_id = :tenant_id
    """
)

_EVENT_ID = text(
    """
    SELECT id FROM compliance.events WHERE provider_event_id = :provider_event_id
    """
)

_MARK_PROCESSED = text(
    """
    UPDATE compliance.events
       SET processed_at = now(), tenant_id = COALESCE(:tenant_id, tenant_id), last_error = NULL
     WHERE id = :id AND processed_at IS NULL
    """
)

_MARK_FAILED = text(
    """
    UPDATE compliance.events
       SET attempts = attempts + 1, last_error = :error
     WHERE id = :id AND processed_at IS NULL
    """
)


# ---- helpers ------------------------------------------------------------------------------------


def _blank(value: object) -> object:
    if value is None or value == "":
        return None
    return value


def _nested(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(source: dict[str, Any], key: str) -> str | None:
    value = source.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _whole(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


async def store_client(
    session: AsyncSession, client: dict[str, Any], *, client_pk: int, tenant_id: int | None
) -> None:
    """Record a ComplyCube client body as fetched or answered: its columns and ``body``."""
    provider_id = client.get("id")
    await session.execute(
        _UPDATE_CLIENT,
        {
            "tenant_id": tenant_id,
            "client_id": client_pk,
            "provider_client_id": provider_id if isinstance(provider_id, str) else None,
            "body": json.dumps(client),
            **client_columns(client),
        },
    )


def client_columns(client: dict[str, Any]) -> dict[str, str | None]:
    """Queryable provider client fields. Metadata stays in ``body`` only."""
    person = _nested(client.get("personDetails"))
    company = _nested(client.get("companyDetails"))
    return {
        "entity_name": _text(client, "entityName"),
        "email": _text(client, "email"),
        "mobile": _text(client, "mobile"),
        "telephone": _text(client, "telephone"),
        "external_id": _text(client, "externalId"),
        "joined_date": _text(client, "joinedDate"),
        "client_type": _text(client, "type"),
        "first_name": _text(person, "firstName"),
        "middle_name": _text(person, "middleName"),
        "last_name": _text(person, "lastName"),
        "dob": _text(person, "dob"),
        "gender": _text(person, "gender"),
        "nationality": _text(person, "nationality"),
        "birth_country": _text(person, "birthCountry"),
        "ssn": _text(person, "ssn"),
        "social_insurance_number": _text(person, "socialInsuranceNumber"),
        "national_identity_number": _text(person, "nationalIdentityNumber"),
        "tax_identification_number": _text(person, "taxIdentificationNumber"),
        "company_name": _text(company, "name"),
        "website": _text(company, "website"),
        "registration_number": _text(company, "registrationNumber"),
        "incorporation_country": _text(company, "incorporationCountry"),
        "incorporation_type": _text(company, "incorporationType"),
    }


def check_kind(payload: dict[str, Any]) -> str:
    raw = str(payload.get("type") or "")
    if "screening" in raw:
        return "aml"
    return "identity"


def _plan_nationalities(plan: SyncPlan) -> list[str]:
    client = plan.client or {}
    raw = client.get("nationality")
    if raw is None:
        details = client.get("personDetails")
        if isinstance(details, dict):
            raw = details.get("nationality")
    if isinstance(raw, str) and raw:
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw if item]
    return []


async def _nationalities(
    session: AsyncSession, client_pk: int, tenant_id: int, plan: SyncPlan
) -> list[str]:
    found = await session.execute(_NATIONALITIES, {"client_id": client_pk, "tenant_id": tenant_id})
    row = found.first()
    stored = None if row is None else getattr(row, "nationality", None)
    if stored:
        return [str(item) for item in stored]
    return _plan_nationalities(plan)


def event_body(value: object) -> dict[str, Any]:
    """An inbox row's payload, whichever way the driver hands jsonb back."""
    loaded: object = value
    if isinstance(value, str):
        loaded = json.loads(value)
    return loaded if isinstance(loaded, dict) else {}


def settle_status(
    *,
    old_status: str | None,
    decided: bool,
    mapped: str,
    new_matches: int,
    monitoring_event: bool,
) -> tuple[str, bool]:
    """The status to store, and whether a reviewer's decision is cleared.

    A decided check keeps its decision, unless the provider refers it again with a match we had
    not seen, or the event is a monitoring alert. Then it reopens to ``refer``.
    """
    if not decided or old_status is None:
        return mapped, False
    if mapped == "refer" and (new_matches > 0 or monitoring_event):
        return "refer", True
    return old_status, False


def is_alert(
    *, old_status: str | None, status: str, reopened: bool, monitoring_event: bool, monitored: bool
) -> bool:
    """Whether storing ``status`` is a monitoring alert: a finished check referred again. That is
    a monitoring event, or a monitored check that was decided, going to ``refer``; or a settled
    refer reopened (``settle_status``), which stays ``refer`` and so is no status change."""
    if status != "refer" or old_status is None:
        return False
    if old_status == "refer":
        return reopened
    return monitoring_event or (monitored and old_status in DECIDED)


# ---- the inbox ----------------------------------------------------------------------------------


async def enqueue(session: AsyncSession, event_pk: int) -> bool:
    """Wake a pgmq consumer when the extension is installed. False when it is not."""
    present = await session.scalar(PGMQ_PRESENT)
    if not present:
        return False
    await session.execute(ENQUEUE_SQL, {"payload": json.dumps({"event_id": event_pk})})
    return True


async def accept_event(session: AsyncSession, event: dict[str, Any]) -> int | None:
    """Store one webhook event in the inbox and wake pgmq when it is installed.

    Returns the inbox id, or None when this event id is already stored (a redelivery). Raises
    ``MissingEventId`` for an event without an id. The worker's consumer applies the row.
    """
    provider_event_id = event_id(event)
    if provider_event_id is None:
        raise MissingEventId()
    inserted = await session.execute(
        _INSERT_EVENT,
        {
            "tenant_id": None,
            "provider_event_id": provider_event_id,
            "event_type": str(event.get("type") or ""),
            "payload": json.dumps(event),
        },
    )
    event_pk = inserted.scalar()
    if event_pk is None:
        return None
    await enqueue(session, int(event_pk))
    return int(event_pk)


async def claim_events(
    session: AsyncSession, *, limit: int, max_attempts: int
) -> list[tuple[int, dict[str, Any]]]:
    """The oldest unprocessed inbox rows, locked for this transaction."""
    claimed = await session.execute(CLAIM_SQL, {"n": limit, "max_attempts": max_attempts})
    return [(int(row.id), event_body(row.payload)) for row in claimed.all()]


async def mark_failed(session: AsyncSession, event_pk: int, error: str) -> None:
    """Count a failed apply. A row that has failed ``max_attempts`` times is no longer claimed."""
    await session.execute(_MARK_FAILED, {"id": event_pk, "error": error[:500]})


async def _ensure_event(session: AsyncSession, plan: SyncPlan, tenant_id: int | None) -> int | None:
    inserted = await session.execute(
        _INSERT_EVENT,
        {
            "tenant_id": tenant_id,
            "provider_event_id": plan.provider_event_id,
            "event_type": plan.event_type,
            "payload": json.dumps(plan.payload),
        },
    )
    event_pk = inserted.scalar()
    if event_pk is None:
        existing = await session.execute(_EVENT_ID, {"provider_event_id": plan.provider_event_id})
        found = existing.first()
        event_pk = None if found is None else found.id
    return int(event_pk) if event_pk is not None else None


async def _upsert_matches(
    session: AsyncSession, check_pk: int, tenant_id: int | None, check: dict[str, Any]
) -> int:
    """Write the check's matches. Returns how many were new. A reviewer's decision on a match is
    never overwritten here."""
    added = 0
    for match in matches_from_check(check):
        saved = await session.execute(
            _UPSERT_MATCH,
            {
                "tenant_id": tenant_id,
                "check_id": check_pk,
                "provider_match_id": match["provider_match_id"],
                "name": match["name"],
                "match_types": match["match_types"],
                "score": match["score"],
                "dob": match["dob"],
                "countries": match["countries"],
                "sources": match["sources"],
                "raw": json.dumps(match["raw"], default=str),
            },
        )
        if saved.scalar():
            added += 1
    return added


async def record_event(
    session: AsyncSession, plan: SyncPlan, *, event_pk: int | None = None
) -> StatusChange:
    """Apply one event's fetched objects. Marks the inbox row processed.

    ``event_pk`` is the inbox row the consumer claimed; without it the row is found by provider
    event id, or inserted. Returns the check's ``(old_status, new_status)`` and sets
    ``plan.check_pk``.

    Only a client we hold is taken in (the module docstring): its ComplyCube id must be one of
    ours, held for a contact, and ComplyCube's ``externalId`` for it must be that contact's uid.
    Anything else is marked processed with nothing stored, and the change is ``(None, None)``.
    """
    provider_client_id = None
    if plan.client:
        provider_client_id = plan.client.get("id")
    elif plan.check:
        provider_client_id = plan.check.get("clientId")
    row = None
    if isinstance(provider_client_id, str):
        found = await session.execute(_LOOKUP_CLIENT, {"provider_client_id": provider_client_id})
        row = found.first()
    held_for = row.contact_uid if row is not None and row.tenant_id is not None else None
    ours = held_for is not None
    if ours and plan.client is not None and plan.client.get("externalId") != held_for:
        # Our client id, someone else's external id: the binding has gone wrong, or the id is
        # another deployment's. Either way none of it is applied here.
        logger.warning(
            "complycube: event %s names client %s, held for another contact; not applied",
            plan.provider_event_id,
            provider_client_id,
            extra={"event": "complycube_event_client_mismatch"},
        )
        ours = False
    if not ours or row is None:
        if event_pk is None:
            event_pk = await _ensure_event(session, plan, None)
        await _mark_processed(session, event_pk, None)
        return StatusChange(None, None)
    tenant_id = int(row.tenant_id)
    plan.tenant_id = tenant_id
    if event_pk is None:
        event_pk = await _ensure_event(session, plan, tenant_id)
    change = StatusChange(None, None)
    client_pk = int(row.id)
    if plan.client is not None:
        await store_client(session, plan.client, client_pk=client_pk, tenant_id=tenant_id)
    nationalities = await _nationalities(session, client_pk, tenant_id, plan)
    for address in plan.addresses:
        if not address.get("id"):
            continue
        await session.execute(
            _UPSERT_ADDRESS,
            {
                "tenant_id": tenant_id,
                "client_id": client_pk,
                "provider_address_id": address.get("id"),
                "address_type": _text(address, "type"),
                "property_number": _text(address, "propertyNumber"),
                "building_name": _text(address, "buildingName"),
                "line": _blank(address.get("line")),
                "city": _blank(address.get("city")),
                "state": _blank(address.get("state")),
                "postal_code": _blank(address.get("postalCode")),
                "country": _blank(address.get("country")),
                "from_date": _blank(address.get("fromDate")),
                "to_date": _text(address, "toDate"),
                "body": json.dumps(address),
            },
        )
    document_ids: dict[str, int] = {}
    for document in plan.documents:
        provider_document_id = document.get("id")
        if not isinstance(provider_document_id, str) or not provider_document_id:
            continue
        classification = document.get("classification") or document.get("type")
        saved = await session.execute(
            _UPSERT_DOCUMENT,
            {
                "tenant_id": tenant_id,
                "client_id": client_pk,
                "provider_document_id": provider_document_id,
                "document_type": _text(document, "type"),
                "document_number": _text(document, "documentNumber"),
                "issuing_country": _blank(document.get("issuingCountry")),
                "issuing_state": _text(document, "issuingState"),
                "classification": _blank(classification),
                "object_key": _blank(document.get("objectKey")),
                "body": json.dumps(document),
            },
        )
        saved_id = saved.scalar()
        if saved_id is not None:
            document_ids[provider_document_id] = int(saved_id)
    for photo in plan.live_photos:
        provider_photo_id = photo.get("id")
        if not isinstance(provider_photo_id, str) or not provider_photo_id:
            continue
        await session.execute(
            _UPSERT_PHOTO,
            {
                "tenant_id": tenant_id,
                "client_id": client_pk,
                "provider_photo_id": provider_photo_id,
                "perform_liveness_check": _flag(photo.get("performLivenessCheck")),
                "content_type": _text(photo, "contentType"),
                "size_bytes": _whole(photo.get("size")),
                "download_link": _text(photo, "downloadLink"),
                "object_key": _blank(photo.get("objectKey")),
                "body": json.dumps(photo),
            },
        )
    if plan.check is not None and plan.check.get("id"):
        change = await _record_check(
            session,
            plan,
            tenant_id=tenant_id,
            client_pk=client_pk,
            document_ids=document_ids,
            nationalities=nationalities,
        )
    await _mark_processed(session, event_pk, tenant_id)
    return change


async def _record_check(
    session: AsyncSession,
    plan: SyncPlan,
    *,
    tenant_id: int | None,
    client_pk: int,
    document_ids: dict[str, int],
    nationalities: list[str],
) -> StatusChange:
    check = plan.check or {}
    provider_check_id = str(check.get("id"))
    locked = (await session.execute(_LOCK_CHECK, {"provider_check_id": provider_check_id})).first()
    old_status = None if locked is None else str(locked.status)
    mapped = status_for(plan, nationalities) or "pending"
    new_matches = 0
    if locked is not None:
        new_matches = await _upsert_matches(session, int(locked.id), tenant_id, check)
    monitoring_event = plan.event_type in MONITORING_EVENTS
    status, reopen = settle_status(
        old_status=old_status,
        decided=locked is not None and locked.decided_at is not None,
        mapped=mapped,
        new_matches=new_matches,
        monitoring_event=monitoring_event,
    )
    alerted = is_alert(
        old_status=old_status,
        status=status,
        reopened=reopen,
        monitoring_event=monitoring_event,
        monitored=locked is not None and bool(locked.monitoring),
    )
    provider_document = check.get("documentId")
    document_id = (
        document_ids.get(provider_document) if isinstance(provider_document, str) else None
    )
    provider_status = check.get("status") if isinstance(check.get("status"), str) else None
    outcome = provider_outcome(check)
    session_id = check.get("sessionId")
    raw_result = check.get("result")
    stored_result = (
        raw_result
        if isinstance(raw_result, dict)
        else {"status": provider_status, "outcome": outcome}
    )
    saved = await session.execute(
        _UPSERT_CHECK,
        {
            "tenant_id": tenant_id,
            "client_id": client_pk,
            "document_id": document_id,
            "kind": check_kind(check),
            "status": status,
            "provider_check_id": provider_check_id,
            "provider_type": _text(check, "type"),
            "provider_status": _text(check, "status"),
            "outcome": outcome,
            "entity_name": _text(check, "entityName"),
            "enable_monitoring": _flag(check.get("enableMonitoring")),
            "client_consent": _flag(check.get("clientConsent")),
            "provider_document_id": _text(check, "documentId"),
            "provider_address_id": _text(check, "addressId"),
            "provider_live_photo_id": _text(check, "livePhotoId"),
            "provider_live_video_id": _text(check, "liveVideoId"),
            "session_id": session_id if isinstance(session_id, str) else None,
            "redirect_url": _blank(check.get("redirectUrl")),
            "result": json.dumps(stored_result),
            "body": json.dumps(check),
            "reopen": reopen,
            "alerted": alerted,
        },
    )
    saved_id = saved.scalar()
    check_pk = int(saved_id) if saved_id is not None else None
    if locked is None and check_pk is not None:
        await _upsert_matches(session, check_pk, tenant_id, check)
    plan.check_pk = check_pk
    return StatusChange(old_status, status)


async def _mark_processed(
    session: AsyncSession, event_pk: int | None, tenant_id: int | None
) -> None:
    if event_pk is not None:
        await session.execute(_MARK_PROCESSED, {"id": int(event_pk), "tenant_id": tenant_id})
