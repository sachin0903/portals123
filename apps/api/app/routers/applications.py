"""The orchestration HTTP surface — start and drive a tiered *application* (an execution over the
generic engine).

An application is an existing ``crm.applications`` the adviser has assembled (accounts +
participants via ``crm.account_contacts``). ``POST /applications/{uid}/start`` runs a published flow
over it; the engine fans out the profile (KYC) + account cases with the cross-case gate. Staff view
progress and action the manual steps (identity/AML review, a staff-assigned fact-find or compliance
review, a task, opening the account, cancelling the journey); the client-facing steps are fulfilled
through the existing document endpoints (which trip the orchestrator hook).

This is the ONLY engine — there is no v1 fallback (its ``/cases*`` surface was removed), so these
routes are unconditional. The old ``FLOWS_ORCHESTRATOR_V2`` flag is gone: off, it took the whole
domain dark rather than falling back to anything.

Staff-only, ``firm_session`` (service_role + tenant-locked), gated ``cases:*``.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any, Literal, cast

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import catalogue, crypto, environments, providers, regular_movements, storage
from app import contacts as contacts_mod
from app.auth import Principal, get_current_principal, require_permission
from app.complycube import clients as complycube_clients
from app.complycube import identity as complycube_identity
from app.config import settings
from app.contacts import normalise_phone, phone_expr
from app.content import sanitise_html
from app.db import (
    FirmContext,
    firm_book_session,
    holds_workspace_permission,
    portal_session,
)
from app.db import (
    target_firm_session as firm_session,
)
from app.flows import form_draft, form_validation, notifications, orchestrator
from app.flows.application_pack import load_application_pack, render_pack_pdf
from app.flows.domain_events import StepSignal
from app.flows.mapping import TRANSFORMS
from app.flows.planning import AccountSpec, ApplicationGraph, Participant
from app.ids import new_id
from app.idv import capture as idv_capture
from app.idv import held as idv_held
from app.idv import manual as idv_manual
from app.idv import phone as idv_phone
from app.journey_scope import IN_BOOK_SQL, sees_whole_firm
from app.permissions import (
    CASES_READ,
    CASES_WRITE,
    TENANT_PORTAL_READ,
)
from app.routers import documents as documents_router
from app.tasks import service as tasks_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/applications", tags=["applications"])
_CONTRIBUTION_FREQUENCIES = frozenset({"single", "monthly", "quarterly", "half_yearly", "annually"})


def _as_json_obj(value: object) -> dict[str, Any] | None:
    """Normalise a jsonb column that may arrive as a dict or a JSON string (or None)."""
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else None


async def _actor_uuid(session: Any) -> str | None:
    """The caller's INTERNAL user uuid (auth.users.id) — for adviser_id / created_by columns.
    ``principal.user_id`` is the WorkOS id (a string), NOT this uuid."""
    val = (await session.execute(text("SELECT auth.uid()"))).scalar_one_or_none()
    return str(val) if val is not None else None


async def _clients_adviser(session: Any, tenant_id: int, contact_ids: Sequence[int]) -> str | None:
    """The adviser the applicants already have — ``crm.client_advisers`` in the ``adviser`` role
    (one per client, 0245) — taken from the first applicant, in the order given, who has one.

    This is what a journey defaults its adviser AND its assignee to when the wizard names nobody.
    It used to be the signed-in user: an administrator starting onboarding for an adviser's
    client became that journey's adviser, and every staff task of it landed on the administrator.
    """
    ids = [int(c) for c in contact_ids]
    if not ids:
        return None
    rows = (
        (
            await session.execute(
                text(
                    "SELECT ca.contact_id, ca.adviser_id::text AS adviser_id "
                    "FROM crm.client_advisers ca "
                    "JOIN auth.users u ON u.id = ca.adviser_id "
                    "WHERE ca.tenant_id = :t AND ca.contact_id = ANY(:ids) "
                    "  AND ca.role = :role AND u.deleted_at IS NULL AND u.is_adviser IS TRUE"
                ),
                {"t": tenant_id, "ids": ids, "role": contacts_mod.ADVISER_ROLE},
            )
        )
        .mappings()
        .all()
    )
    by_contact = {int(r["contact_id"]): str(r["adviser_id"]) for r in rows}
    return next((by_contact[c] for c in ids if c in by_contact), None)


_ADVISER_REQUIRED = "Choose an adviser of this firm — that person must be marked as an adviser."
_NO_ADVISER = (
    "Choose an adviser of this firm. These clients have none, and you are not marked as an adviser."
)


def _adviser_uuid(raw: str) -> str:
    """A staff id as a uuid, before it reaches ``CAST(:u AS uuid)``.

    A non-uuid used to raise a psycopg ``DataError`` and the kickoff wizard reported that as a 500.
    """
    try:
        return str(uuid.UUID(raw.strip()))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _ADVISER_REQUIRED) from exc


async def _firm_adviser(session: Any, tenant_id: int, user_id: str) -> str | None:
    """The user's id when they are a live adviser of this firm."""
    named = (
        await session.execute(
            text(
                "SELECT id::text FROM auth.users "
                "WHERE id = CAST(:u AS uuid) AND tenant_id = :t "
                "  AND deleted_at IS NULL AND is_adviser IS TRUE"
            ),
            {"u": user_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    return str(named) if named is not None else None


async def _resolve_journey_adviser(
    session: Any,
    tenant_id: int,
    contact_ids: Sequence[int],
    requested: str | None,
    actor: str | None,
) -> str | None:
    """Who owns this journey.

    An explicit id must be an adviser of the firm. Left blank, the first applicant who already
    has an adviser keeps them — an administrator starting a journey for that client must not
    become the adviser. With nobody on the book, the signed-in user is used only when they
    themselves are an adviser. Recording a colleague who is not, then asking ``assign_adviser``
    to put them on the book, raised ``LookupError`` and the wizard showed it as a 500.
    """
    chosen = (requested or "").strip()
    if chosen:
        named = await _firm_adviser(session, tenant_id, _adviser_uuid(chosen))
        if named is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _ADVISER_REQUIRED)
        return named
    existing = await _clients_adviser(session, tenant_id, contact_ids)
    if existing is not None:
        return existing
    if actor is not None and await _firm_adviser(session, tenant_id, actor):
        return actor
    if actor is None:
        return None
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _NO_ADVISER)


async def _assign_journey_adviser(
    session: Any, tenant_id: int, contact_ids: Sequence[int], adviser_id: str
) -> None:
    """Give the journey's adviser to each party who does not already have one."""
    for contact_id in sorted({int(item) for item in contact_ids}):
        if await _clients_adviser(session, tenant_id, [contact_id]) is None:
            await contacts_mod.assign_adviser(
                session,
                tenant_id=tenant_id,
                contact_id=contact_id,
                adviser_id=adviser_id,
                role=contacts_mod.ADVISER_ROLE,
            )


async def _record_journey_adviser(
    session: Any,
    tenant_id: int,
    contact_ids: Sequence[int],
    adviser_id: str,
    *,
    replace: bool,
) -> None:
    """Write the journey adviser onto the client book.

    ``replace`` is the explicit picker: every party joins that book, and a previous adviser
    moves to support. The default only fills parties who have nobody, so a joint applicant
    who already has an adviser is left as the firm set them.
    """
    try:
        if replace:
            for contact_id in sorted({int(item) for item in contact_ids}):
                await contacts_mod.assign_adviser(
                    session,
                    tenant_id=tenant_id,
                    contact_id=contact_id,
                    adviser_id=adviser_id,
                    role=contacts_mod.ADVISER_ROLE,
                )
        else:
            await _assign_journey_adviser(session, tenant_id, contact_ids, adviser_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


def _live_journey_conflict(exc: orchestrator.ActiveExecutionExists) -> HTTPException:
    """The 409 for a client already on a live journey of this flow, naming that journey when the
    engine could see it — so the portal can open it or stop it instead of only repeating the
    sentence. ``message`` keeps the wording the plain-string detail carried."""
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail={"message": str(exc), "execution_uid": exc.execution_uid},
    )


async def _graph(session: Any, tenant_id: int, application_id: int) -> ApplicationGraph:
    """Build the engine's ApplicationGraph from the application's accounts + participants —
    including the facts a step's ``visibility`` keys on: application custody, each account's
    product (GIA/ISA/SIPP/…) and whether it carries an inbound transfer. These drive the conditional
    offshore/pension/ISA/transfer branches at planning time."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT ac.account_id, ac.contact_id, ac.role::text AS role, ct.type, ct.dob, "
                    "       p.product_type::text AS product, acc.custody::text AS acc_custody, "
                    "       (acc.transfer IS NOT NULL) AS has_transfer "
                    "FROM crm.account_contacts ac "
                    "JOIN crm.contacts ct ON ct.id = ac.contact_id "
                    # A party on a person-subject journey has no account. LEFT JOIN keeps that
                    # contact as a participant; the account spec is added only when account_id
                    # is set.
                    "LEFT JOIN platform.accounts acc ON acc.id = ac.account_id "
                    "LEFT JOIN platform.products p ON p.id = acc.product_id "
                    "WHERE ac.application_id = :a AND ac.tenant_id = :t"
                ),
                {"a": application_id, "t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    custody = (
        await session.execute(
            text("SELECT custody::text FROM crm.applications WHERE id = :a"), {"a": application_id}
        )
    ).scalar_one_or_none()
    roles: dict[int, set[str]] = {}
    types: dict[int, str] = {}
    dobs: dict[int, date | None] = {}
    accounts: dict[int, list[int]] = {}
    registered_contacts: dict[int, list[int]] = {}
    acc_meta: dict[int, dict[str, Any]] = {}
    for r in rows:
        cid = int(r["contact_id"])
        roles.setdefault(cid, set()).add(str(r["role"] or ""))
        types[cid] = str(r["type"] or "individual")
        dobs[cid] = _parse_dob(r["dob"])
        if r["account_id"] is None:
            continue
        aid = int(r["account_id"])
        accounts.setdefault(aid, []).append(cid)
        if str(r["role"] or "") == "registered_contact":
            registered_contacts.setdefault(aid, []).append(cid)
        acc_meta[aid] = {
            "product": r["product"],
            "custody": r["acc_custody"] or custody,
            "has_transfer": bool(r["has_transfer"]),
        }
    participants = tuple(
        Participant(
            contact_id=cid,
            type=types[cid],
            roles=frozenset(roles[cid]),
            dob=dobs[cid],
        )
        for cid in roles
    )
    account_specs = tuple(
        AccountSpec(
            account_id=aid,
            participant_contact_ids=tuple(cids),
            product=acc_meta[aid]["product"],
            custody=acc_meta[aid]["custody"],
            has_transfer=acc_meta[aid]["has_transfer"],
            registered_contact_ids=tuple(registered_contacts.get(aid, [])),
        )
        for aid, cids in accounts.items()
    )
    return ApplicationGraph(participants=participants, accounts=account_specs, custody=custody)


def _person_name(o: OwnerRef) -> tuple[str | None, str | None, str | None, str]:
    """An individual's name parts, and the display name built from them.

    Kickoff captures the parts (``first_name`` / ``middle_name`` / ``surname``) because
    ``crm.contacts`` holds them separately and one string cannot be split back reliably: the old
    rule took the first token as the forename and EVERYTHING ELSE as the surname, so "Mary Jane
    Watson" was filed under the surname "Jane Watson".

    A payload carrying only ``name`` is still accepted and split on the English convention — first
    token the forename, LAST token the surname, anything between them a middle name. That is a
    guess where the parts are a statement, which is why the UI sends the parts.
    """
    first = (o.first_name or "").strip() or None
    middle = (o.middle_name or "").strip() or None
    surname = (o.surname or "").strip() or None
    if first is None and surname is None:
        parts = (o.name or "").strip().split()
        if parts:
            first = parts[0]
            surname = parts[-1] if len(parts) > 1 else None
            middle = " ".join(parts[1:-1]) or None
    display = " ".join(p for p in (first, middle, surname) if p) or (o.name or "").strip()
    return first, middle, surname, display


async def _refuse_duplicate_client(
    ctx: FirmContext, email: str, first_name: str | None, surname: str | None
) -> None:
    """Refuse a new party who is already on the firm's book.

    Onboarding someone already a client silently created a twin — two contacts, two KYC trails,
    two sets of accounts for one person. A half-onboarded person is caught by the same check,
    because kickoff writes the contact on day one, so the refusal says which of the two cases it is
    and names the journey already running.

    **The match is the email AND the name, not the email alone.** A couple sharing one address is
    two clients with one login — the case ``OwnerRef``'s own docstring names as supported — and
    refusing the second of them would make that impossible to onboard, with no override anywhere.
    Two different people never share both; the same person's second record always does. An email
    match on its own is a WARNING the adviser is shown before they submit (the kickoff page offers
    the existing client), never a refusal here.
    """
    if not (first_name or surname):
        return
    row = (
        (
            await ctx.session.execute(
                text(
                    "SELECT c.id, c.name, c.first_name, c.surname, "
                    "       (c.data ->> 'created_via' = 'intake') AS from_intake, "
                    "       (SELECT a.uid FROM crm.account_contacts ac "
                    "          JOIN crm.applications a ON a.id = ac.application_id "
                    "         WHERE ac.contact_id = c.id AND a.deleted_at IS NULL "
                    "           AND COALESCE(a.status, '') NOT IN ('completed', 'cancelled') "
                    "         ORDER BY a.id DESC LIMIT 1) AS open_journey "
                    "FROM crm.contacts c "
                    "WHERE c.tenant_id = :t AND c.deleted_at IS NULL "
                    "  AND lower(c.email) = :e "
                    "  AND lower(coalesce(c.first_name, '')) = :f "
                    "  AND lower(coalesce(c.surname, '')) = :s LIMIT 1"
                ),
                {
                    "t": ctx.tenant_id,
                    "e": email,
                    "f": (first_name or "").strip().lower(),
                    "s": (surname or "").strip().lower(),
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return
    who = " ".join(p for p in (row["first_name"], row["surname"]) if p) or row["name"] or "A client"
    if row["open_journey"] and row["from_intake"]:
        message = f"{who} ({email}) is already being onboarded — open that journey instead."
    elif row["open_journey"]:
        message = f"{who} ({email}) already has a journey in progress — open that one instead."
    else:
        message = f"{who} ({email}) is already a client — add them with the existing-client picker."
    raise HTTPException(status.HTTP_409_CONFLICT, message)


async def _resolve_owner(ctx: FirmContext, o: OwnerRef, cache: dict[str, int]) -> int:
    """Return a contact id for an account owner — validating an existing client, or creating a new
    one (a client, tagged ``created_via=intake``) from the name/email captured at kickoff. A login
    is NOT created here; that happens at invite, where one login can serve several clients."""
    if o.contact_id is not None:
        ok = (
            await ctx.session.execute(
                text("SELECT 1 FROM crm.contacts WHERE id = :i AND tenant_id = :t"),
                {"i": o.contact_id, "t": ctx.tenant_id},
            )
        ).scalar_one_or_none()
        if ok is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown client selected.")
        return o.contact_id
    ctype = "organisation" if o.type == "organisation" else "individual"
    first: str | None = None
    middle: str | None = None
    surname: str | None = None
    if ctype == "organisation":
        name = (o.name or "").strip()
    else:
        first, middle, surname, name = _person_name(o)
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A new client needs a name.")
    email = (o.email or "").strip().lower() or None
    key = f"{name}|{email or ''}".lower()
    if key in cache:
        return cache[key]
    if email is not None:
        await _refuse_duplicate_client(ctx, email, first, surname)
    cid = int(
        (
            await ctx.session.execute(
                text(
                    "INSERT INTO crm.contacts "
                    "  (tenant_id, uid, type, name, first_name, middle_name, surname, email, "
                    "   dob, data) "
                    "VALUES (:t, :u, CAST(:ty AS crm.contact_type), :n, :f, :m, :s, :e, "
                    "  CAST(:dob AS date), CAST(:d AS jsonb)) RETURNING id"
                ),
                {
                    "t": ctx.tenant_id,
                    "u": new_id(),
                    "ty": ctype,
                    "n": name,
                    "f": first,
                    "m": middle,
                    "s": surname,
                    "e": email,
                    "dob": (o.dob or "").strip() or None,
                    "d": json.dumps({"created_via": "intake"}),
                },
            )
        ).scalar_one()
    )
    cache[key] = cid
    return cid


# Kickoff may record attorney/other as interested parties and registered_contact as the adult
# acting for a child; the KYC invite cohort is ``flows/planning.KYC_ROLES`` minus a child owner
# whose account names that adult.
_KICKOFF_ACCOUNT_ROLES = frozenset(
    {"owner", "decision_maker", "trustee", "attorney", "other", "registered_contact"}
)


class OwnerRef(BaseModel):
    """An account party at kickoff: either an existing client (``contact_id``) or a NEW client the
    adviser creates inline. ``role`` is ``owner`` / ``decision_maker`` / ``trustee`` /
    ``attorney`` / ``other`` (not ``beneficiary`` — expression-of-wish is separate). New people are
    created as clients tagged ``created_via=intake``; their login is provisioned later at invite —
    a client is not a user (a shared-email couple = one login → two clients; a business = many
    logins → one org).

    An individual is named in PARTS (``first_name`` / ``middle_name`` / ``surname``), which is how
    ``crm.contacts`` holds them; ``name`` is the organisation's name, and remains accepted for an
    individual so an older client still works (see ``_person_name`` for how it is split). Every
    individual owner needs ``dob``; an adult owner needs an email, while a child instead needs an
    adult registered contact on the account. Registered contacts need a date of birth and email
    because they are the portal/KYC actor.
    """

    contact_id: int | None = None
    name: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    surname: str | None = None
    email: str | None = None
    type: str = "individual"  # individual | organisation
    role: str = "owner"  # owner | decision_maker | trustee | attorney | other | registered_contact
    dob: str | None = None
    identity_mode: str | None = None
    # Request-local identity used only to preserve which adult was chosen for which child when an
    # account has more than one of either. It is never stored as a platform identifier.
    party_key: str | None = None
    registered_contact_for: list[str] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def _owner_type(cls, value: str) -> str:
        v = (value or "individual").strip().lower()
        if v not in ("individual", "organisation"):
            raise ValueError("type must be individual or organisation")
        return v

    @field_validator("role")
    @classmethod
    def _owner_role(cls, value: str) -> str:
        v = (value or "owner").strip().lower().replace("-", "_")
        if v not in _KICKOFF_ACCOUNT_ROLES:
            raise ValueError(
                "role must be owner, decision_maker, trustee, attorney, other, "
                "or registered_contact"
            )
        return v

    @field_validator("identity_mode")
    @classmethod
    def _identity_mode(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        mode = value.strip().lower()
        if mode not in {"default", "manual"}:
            raise ValueError("identity_mode must be default or manual")
        return mode

    @model_validator(mode="after")
    def _organisation_is_owner(self) -> OwnerRef:
        # Org/trust accounts name the organisation as the account owner; trustees,
        # decision-makers, attorneys and other parties are individuals. Marking an organisation
        # as trustee would put it into the KYC invite cohort incorrectly.
        if self.type == "organisation" and self.role != "owner":
            raise ValueError(
                "An organisation must have role owner — trustees, decision makers, "
                "attorneys and other parties are individuals."
            )
        return self


class BeneficiaryIn(BaseModel):
    """Deprecated import compatibility; beneficiaries are collected by Account details."""

    first_name: str = Field(min_length=1)
    surname: str = Field(min_length=1)
    portion: float = Field(gt=0, le=100)


#: Every member of ``platform.product_type`` an adviser may raise a journey for. The three TP*
#: wrappers were refused, which made the Account details form's third-party-provider section
#: unreachable: it is conditioned on exactly those products, and no kickoff could produce one — so
#: a section that used to be asked of every GIA and ISA became a section asked of nothing.
_KICKOFF_WRAPPERS = frozenset({"GIA", "JISA", "ISA", "SIPP", "TPONBOND", "TPOFFBOND", "TPPENSION"})


class NewAccount(BaseModel):
    # The kickoff always offers the four wrappers the product supports, even when the firm's richer
    # product shelf has not been configured yet. `create_application` resolves this to (or creates)
    # the tenant's canonical `platform.products` row before the account is inserted.
    product_type: str | None = None
    product_id: int | None = None
    provider: str | None = None
    # Omitted is not "onshore". The kickoff picks onshore when that location is open,
    # and the only open location when it is not — an offshore-only firm must not 422.
    custody: str | None = None
    illustration_uid: str | None = None
    owners: list[OwnerRef]
    # Deprecated sidecar — keep accepting so an older payload does not 422, but a JISA child is a
    # real owner contact. Prefer ``owners`` with role owner (child) + registered_contact (adult).
    child_name: str | None = None
    child_dob: str | None = None

    @field_validator("product_type")
    @classmethod
    def _product_type(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        wrapper = str(value).strip().upper()
        if wrapper not in _KICKOFF_WRAPPERS:
            raise ValueError(f"product_type must be one of {', '.join(sorted(_KICKOFF_WRAPPERS))}")
        return wrapper

    @model_validator(mode="after")
    def _require_owner_role(self) -> NewAccount:
        if self.owners and not any(o.role == "owner" for o in self.owners):
            raise ValueError(
                "At least one account party must have role owner "
                "(organisations and trusts need an owner; trustees alone are not enough)."
            )
        wrapper = (self.product_type or "").strip().upper()
        if wrapper == "JISA":
            err = _jisa_party_error(self)
            if err:
                raise ValueError(err)
        return self


def _jisa_party_error(acc: NewAccount) -> str | None:
    owners = [o for o in acc.owners if o.role == "owner"]
    registered = [o for o in acc.owners if o.role == "registered_contact"]
    if len(owners) != 1:
        return "A JISA needs exactly one owner — the child."
    if not registered:
        return (
            "A JISA needs a registered contact — the parent or guardian we KYC "
            "and invite to the portal."
        )
    for adult in registered:
        if adult.contact_id is None and not (adult.email or "").strip():
            return (
                "The registered contact needs an email — that is who the portal "
                "invite and terms go to."
            )
    return None


def _parse_dob(value: object) -> date | None:
    if value in {None, ""}:
        return None
    if isinstance(value, date):
        return date(value.year, value.month, value.day)
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


async def _owner_age_error(ctx: FirmContext, acc: NewAccount, product: Any) -> str | None:
    # The EFFECTIVE bounds, not the row's: a statutory ceiling applies whether or not the product
    # carries one (`catalogue.STATUTORY_MAX_AGE`). Reading the row alone meant a JISA created after
    # `0162_product_ages` backfilled the existing ones — by an operator, a later seeding pass, a
    # custodian-discovered shelf item — had NULL bounds, and this refusal never fired.
    min_age, max_age = catalogue.effective_age_bounds(
        str(product.get("product_type") or ""), product.get("min_age"), product.get("max_age")
    )
    owners = [o for o in acc.owners if o.role == "owner"]
    label = str(product.get("name") or product.get("product_type") or "This product")
    for owner in owners:
        owner_type, dob, _email = await _party_facts(ctx, owner)
        if owner_type == "organisation":
            continue
        error = catalogue.product_age_error(
            name=label,
            min_age=int(min_age) if min_age is not None else None,
            max_age=int(max_age) if max_age is not None else None,
            dob=dob,
        )
        if error:
            return error
    return None


async def _party_facts(ctx: FirmContext, party: OwnerRef) -> tuple[str, date | None, str | None]:
    """Resolve the three facts kickoff safety depends on without creating a contact."""
    if party.contact_id is None:
        return party.type, _parse_dob(party.dob), (party.email or "").strip().lower() or None
    row = (
        (
            await ctx.session.execute(
                text(
                    "SELECT type::text AS type, dob, email FROM crm.contacts "
                    "WHERE id = :c AND tenant_id = :t AND deleted_at IS NULL"
                ),
                {"c": party.contact_id, "t": ctx.tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown client selected.")
    return str(row["type"]), _parse_dob(row["dob"]), (row["email"] or "").strip().lower() or None


async def _kickoff_party_error(ctx: FirmContext, acc: NewAccount) -> str | None:
    """Validate adult/child composition before any intake contact is created."""
    facts: list[tuple[OwnerRef, str, date | None, str | None]] = []
    for party in acc.owners:
        party_type, dob, email = await _party_facts(ctx, party)
        facts.append((party, party_type, dob, email))
    party_keys = [party.party_key for party in acc.owners if party.party_key]
    if len(party_keys) != len(set(party_keys)):
        return "Each account party must have one unique request key."

    valid_registered_contacts = [
        party
        for party, party_type, dob, email in facts
        if party.role == "registered_contact"
        and party_type == "individual"
        and dob is not None
        and catalogue.completed_age(dob) >= 18
        and email is not None
    ]
    keyed_children = {
        party.party_key
        for party, party_type, dob, _email in facts
        if party.role == "owner"
        and party_type == "individual"
        and dob is not None
        and catalogue.completed_age(dob) < 18
        and party.party_key
    }
    targeted_children = {
        child_key
        for party in valid_registered_contacts
        for child_key in party.registered_contact_for
    }
    if targeted_children - keyed_children:
        return "A registered contact refers to a child who is not an owner of this account."
    # The registered contact is checked FIRST, in its own pass. Interleaved with the owner rules it
    # was reached only after them, so a child whose named adult is 16 got the composition refusal
    # ("needs an adult registered contact with an email") — true, and silent about the one thing the
    # adviser has to change. A refusal that names the person and the reason is the point.
    for party, party_type, dob, email in facts:
        if party.role != "registered_contact":
            continue
        if party_type != "individual":
            return "A registered contact must be an individual."
        if dob is None:
            return "The registered contact needs a date of birth to confirm they are an adult."
        if catalogue.completed_age(dob) < 18:
            return "A registered contact must be aged 18 or over."
        if email is None:
            return (
                "The registered contact needs an email — that is who the portal "
                "invite and terms go to."
            )
    for party, party_type, dob, email in facts:
        if party.role != "owner" or party_type != "individual":
            continue
        if dob is None:
            return "Every individual owner needs a date of birth."
        age = catalogue.completed_age(dob)
        if age < 0:
            return "Date of birth cannot be in the future."
        if age >= 18 and email is None:
            return "Every adult individual owner needs an email."
        has_registered_contact = bool(valid_registered_contacts)
        if party.party_key:
            # New clients name the exact relationship. Older callers carry no request-local keys;
            # their one child/registered-contact pair remains accepted by the account-level rule.
            has_registered_contact = party.party_key in targeted_children
        if age < 18 and not has_registered_contact:
            return (
                "Every child owner needs an adult registered contact with an email "
                "on the same account."
            )
    return None


async def _owner_type_error(ctx: FirmContext, acc: NewAccount, product: Any) -> str | None:
    """Refuse an account whose owner contact type is outside the product's shelf rules."""
    allowed = set(
        catalogue.effective_client_types(
            str(product.get("product_type") or ""), product.get("allowed_client_types")
        )
    )
    for owner in (party for party in acc.owners if party.role == "owner"):
        owner_type = owner.type
        if owner.contact_id is not None:
            stored = (
                await ctx.session.execute(
                    text("SELECT type::text FROM crm.contacts WHERE id = :c AND tenant_id = :t"),
                    {"c": owner.contact_id, "t": ctx.tenant_id},
                )
            ).scalar_one_or_none()
            if stored is None:
                continue  # _resolve_owner gives the existing-client refusal after shelf validation
            owner_type = str(stored)
        if owner_type not in allowed:
            label = str(product.get("name") or product.get("product_type") or "This product")
            if allowed == {"individual"}:
                return f"{label} can only be opened for an individual."
            if allowed == {"organisation"}:
                return f"{label} can only be opened for an organisation."
            return f"{label} is not available to this type of client."
    return None


class NewApplication(BaseModel):
    flow_code: str = "onboarding"
    #: The custodian the application is filed with, by its code (0138). It was a
    #: `platform.platforms` id — a serial the caller had to resolve first, for a value that is a
    #: closed vocabulary.
    provider: str | None = None
    custody: str | None = None
    accounts: list[NewAccount] = []
    #: Existing clients for a person-subject journey (``intake.subject = person``). One or two
    #: contact ids, and no accounts. Ignored by a journey that opens accounts.
    clients: list[int] = []

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, value: str | None) -> str | None:
        """Refused HERE, so a stale or mistyped code is a 422 naming it.

        `platform.provider_code` has three members and this value reaches
        `CAST(:p AS platform.provider_code)` two statements later, where an unrecognised one raises
        `invalid input value for enum` — a 500 that also aborts the transaction for the rest of the
        request. The value an older admin client sends is `morgan_lloyd`, the spelling this estate
        used until 0138. Validated as a field rather than in the handler because it is a fact about
        the request body, which is what pydantic answers 422 for.
        """
        if value is not None and value not in providers.PROVIDERS:
            raise ValueError(
                f"unknown provider '{value}'; expected one of "
                + ", ".join(sorted(providers.PROVIDERS))
            )
        return value

    # Values for the flow's custom intake fields (key → value), captured at kickoff.
    intake: dict[str, Any] = {}
    #: Staff uuid of the assigned adviser. Omitted, the applicants' own adviser is used, then
    #: the signed-in user when they are marked an adviser. An explicit id must be one.
    adviser_id: str | None = None


def _intake_subject(definition: object) -> str:
    """``person`` when the published flow gathers people and opens no account. Anything else,
    including a missing intake, is the account-opening journey."""
    if isinstance(definition, str):
        try:
            definition = json.loads(definition)
        except json.JSONDecodeError:
            return "accounts"
    if isinstance(definition, dict):
        intake = definition.get("intake")
        if isinstance(intake, dict) and intake.get("subject") == "person":
            return "person"
    return "accounts"


async def _published_flow(session: Any, tenant_id: int, code: str) -> Any:
    return (
        (
            await session.execute(
                text(
                    "SELECT f.id AS flow_id, f.current_published_version_id AS vid, v.definition "
                    "FROM flows.flows f "
                    "JOIN flows.flow_versions v ON v.id = f.current_published_version_id "
                    "WHERE f.tenant_id = :t AND f.code = :c AND f.status = 'published'"
                ),
                {"t": tenant_id, "c": code},
            )
        )
        .mappings()
        .one_or_none()
    )


async def _start_person_journey(
    body: NewApplication, ctx: FirmContext, flow: Any
) -> dict[str, object]:
    """One or two existing clients, no account, each recorded as a ``party``.

    ``party`` is not an owner and not KYC-relevant. ``_graph`` turns the rows into participants
    and adds no account spec, so planning makes a profile case per person and no account case.
    """
    if body.accounts:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "This journey does not open an account."
        )
    if len(body.clients) not in (1, 2) or len(set(body.clients)) != len(body.clients):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose one or two existing clients."
        )
    found = (
        (
            await ctx.session.execute(
                text(
                    "SELECT id FROM crm.contacts "
                    "WHERE tenant_id = :t AND deleted_at IS NULL AND id = ANY(:ids)"
                ),
                {"t": ctx.tenant_id, "ids": list(body.clients)},
            )
        )
        .scalars()
        .all()
    )
    if len(found) != len(body.clients):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Choose one or two existing clients of this firm.",
        )
    actor = await _actor_uuid(ctx.session)
    adviser = await _resolve_journey_adviser(
        ctx.session, ctx.tenant_id, body.clients, body.adviser_id, actor
    )
    application_id = int(
        (
            await ctx.session.execute(
                text(
                    "INSERT INTO crm.applications (tenant_id, provider, uid, status, custody, "
                    "  adviser_id, adviser_declaration) "
                    "VALUES (:t, NULL, :u, 'draft', NULL, CAST(:adv AS uuid), false) RETURNING id"
                ),
                {"t": ctx.tenant_id, "u": new_id(), "adv": adviser},
            )
        ).scalar_one()
    )
    for contact_id in body.clients:
        await ctx.session.execute(
            text(
                "INSERT INTO crm.account_contacts "
                "  (tenant_id, application_id, account_id, contact_id, role) "
                "VALUES (:t, :a, NULL, :c, CAST('party' AS crm.account_role))"
            ),
            {"t": ctx.tenant_id, "a": application_id, "c": contact_id},
        )
    if adviser is not None:
        await _record_journey_adviser(
            ctx.session,
            ctx.tenant_id,
            body.clients,
            adviser,
            replace=bool((body.adviser_id or "").strip()),
        )
    definition = flow["definition"]
    if isinstance(definition, str):
        definition = json.loads(definition)
    graph = await _graph(ctx.session, ctx.tenant_id, application_id)
    try:
        created = await orchestrator.start_execution(
            ctx.session,
            tenant_id=ctx.tenant_id,
            flow_id=int(flow["flow_id"]),
            flow_version_id=int(flow["vid"]),
            definition=definition,
            graph=graph,
            application_id=application_id,
            created_by=actor,
            assignee_user_id=adviser,
            data={"adviser_declared": True, "intake": dict(body.intake)},
        )
    except orchestrator.ActiveExecutionExists as exc:
        raise _live_journey_conflict(exc) from exc
    return {"execution_uid": created["uid"]}


@router.post("", name="create-application", status_code=status.HTTP_201_CREATED)
async def create_application(
    body: NewApplication,
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Compose an application from existing clients + accounts and start its onboarding flow in one
    call — the adviser's "start onboarding" action. Creates the crm.applications record, a
    platform.accounts row per account with its owners linked (crm.account_contacts), then runs the
    published flow over the assembled graph.

    A published flow whose ``intake.subject`` is ``person`` takes the other door: one or two
    existing clients, no account, each stored as a ``party``."""
    require_permission(principal, CASES_WRITE)
    published = await _published_flow(ctx.session, ctx.tenant_id, body.flow_code)
    if published is not None and _intake_subject(published["definition"]) == "person":
        return await _start_person_journey(body, ctx, published)
    if body.clients and not body.accounts:
        if published is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No published flow with that code.")
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add an account with owners.")
    if not body.accounts or any(not a.owners for a in body.accounts):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add an account with owners.")

    # Product configuration is the authority for all three kickoff choices. Validate it before
    # creating contacts, so a stale shelf/provider choice cannot leave orphan intake people.
    onboarding_illustrations: dict[int, int] = {}
    kickoff_transfers: dict[int, dict[str, Any]] = {}
    selected_illustrations: set[str] = set()
    provider_configs = {
        str(row["provider"]): dict(row["config"] or {})
        for row in (
            await ctx.session.execute(
                text(
                    "SELECT provider::text AS provider, config "
                    "FROM platform.tenant_platforms "
                    "WHERE tenant_id = :t AND enabled"
                ),
                {"t": ctx.tenant_id},
            )
        )
        .mappings()
        .all()
    }
    for ai, acc in enumerate(body.accounts):
        product = (
            (
                await ctx.session.execute(
                    text(
                        "SELECT id, product_type::text AS product_type, name, "
                        "       allowed_custodies::text[] AS allowed_custodies, max_parties, "
                        "       allowed_client_types::text[] AS allowed_client_types, "
                        "       min_age, max_age "
                        "FROM platform.products "
                        "WHERE tenant_id = :t AND enabled "
                        "  AND ((CAST(:id AS int) IS NOT NULL AND id = CAST(:id AS int)) "
                        "    OR (CAST(:id AS int) IS NULL "
                        "        AND product_type::text = CAST(:pt AS text))) "
                        "LIMIT 1"
                    ),
                    {"t": ctx.tenant_id, "id": acc.product_id, "pt": acc.product_type},
                )
            )
            .mappings()
            .one_or_none()
        )
        if product is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Choose an account type this firm currently offers.",
            )
        acc.product_id = int(product["id"])
        acc.product_type = str(product["product_type"])
        if len(acc.owners) > int(product["max_parties"]):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{acc.product_type} allows at most {product['max_parties']} parties per account.",
            )

        # The custodians this firm may open this product with. A shelf item no provider is mapped
        # to yet is an ordinary state — 0128's two third-party bonds are in it on every book — so
        # an empty list raises the account with NO custodian (the column is nullable) rather than
        # refusing the journey for a mapping only an operator can make. What is still refused is a
        # custodian this product does not offer, which is a wrong answer rather than a missing one.
        offered = {
            str(code)
            for code in (
                (
                    await ctx.session.execute(
                        text(
                            "SELECT pp.provider::text FROM platform.product_platforms pp "
                            "JOIN platform.tenant_platforms tp "
                            "  ON tp.tenant_id = :t AND tp.provider = pp.provider AND tp.enabled "
                            "WHERE pp.tenant_id = :t AND pp.product_id = :p AND pp.enabled "
                            "  AND pp.role = 'custodian'"
                        ),
                        {"t": ctx.tenant_id, "p": acc.product_id},
                    )
                )
                .scalars()
                .all()
            )
        }
        account_provider = acc.provider or body.provider
        if account_provider is None and not offered:
            acc.provider = None
        elif account_provider not in providers.PROVIDERS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Choose a recognised custodian for every account.",
            )
        elif account_provider not in offered:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{account_provider} is not an enabled custodian for {acc.product_type}.",
            )
        else:
            acc.provider = account_provider

        allowed_custodies = {str(value) for value in product["allowed_custodies"]}
        openable = providers.openable_custodies(
            acc.provider, provider_configs.get(acc.provider) if acc.provider else None
        )
        offered_custodies = allowed_custodies & set(openable)
        account_custody = (acc.custody or body.custody or "").strip().lower()
        if not account_custody:
            if "onshore" in offered_custodies:
                account_custody = "onshore"
            elif len(offered_custodies) == 1:
                account_custody = next(iter(offered_custodies))
        if not offered_custodies:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{acc.product_type} cannot be opened at "
                f"{acc.provider or 'this custodian'} with the custody locations it allows.",
            )
        if account_custody not in offered_custodies:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{acc.product_type} is not available with {account_custody or 'that custody'}"
                + (f" at {acc.provider}" if acc.provider else "")
                + ".",
            )
        acc.custody = account_custody

        wrapper = acc.product_type
        if wrapper == "JISA":
            err = _jisa_party_error(acc)
            if err:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, err)
        party_err = await _kickoff_party_error(ctx, acc)
        if party_err:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, party_err)
        client_type_err = await _owner_type_error(ctx, acc, product)
        if client_type_err:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, client_type_err)
        age_err = await _owner_age_error(ctx, acc, product)
        if age_err:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, age_err)
        illustration_uid = (acc.illustration_uid or "").strip()
        if acc.product_type != "SIPP":
            if illustration_uid:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "Only a SIPP account can be opened against a pension illustration.",
                )
            continue
        if not illustration_uid:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Choose a successful SIPP illustration before starting onboarding.",
            )
        if illustration_uid in selected_illustrations:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Each SIPP account needs its own illustration.",
            )
        selected_illustrations.add(illustration_uid)
        owner_ids = {
            int(owner.contact_id)
            for owner in acc.owners
            if owner.role == "owner" and owner.contact_id is not None
        }
        if not owner_ids:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "A SIPP owner must already be a client with a successful illustration.",
            )
        illustration = (
            (
                await ctx.session.execute(
                    text(
                        "SELECT id, contact_id, account_id, product_type, placeholder, inputs "
                        "FROM platform.illustrations "
                        "WHERE tenant_id = :t AND uid = :u FOR UPDATE"
                    ),
                    {"t": ctx.tenant_id, "u": illustration_uid},
                )
            )
            .mappings()
            .one_or_none()
        )
        inputs = _as_json_obj(illustration["inputs"]) if illustration else None
        errors = (inputs or {}).get("engine_errors")
        if (
            illustration is None
            or int(illustration["contact_id"]) not in owner_ids
            or str(illustration["product_type"] or "").upper() != "SIPP"
            or illustration["account_id"] is not None
            or bool(illustration["placeholder"])
            or (isinstance(errors, list) and bool(errors))
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Choose an unused, successful SIPP illustration for one of this account's owners.",
            )
        onboarding_illustrations[ai] = int(illustration["id"])
        transfer_value = (inputs or {}).get("transfer_value")
        if _positive_number(transfer_value):
            # The terms gate reads accounts.transfer, which account preferences writes later.
            # A transfer already chosen on the illustration has to be on the account at kickoff
            # or the transfer declaration is planned out.
            kickoff_transfers[ai] = {"intent": True, "amount": transfer_value}
    # Resolve each party to a contact id up front (validating existing ones, creating new clients),
    # so the account-composition below works in plain contact ids + roles. Cache by identity so the
    # same new person listed on two accounts is created once.
    resolved: dict[int, list[tuple[int, str]]] = {}
    registered_relationships: dict[int, set[tuple[int, int]]] = {}
    new_cache: dict[str, int] = {}
    identity_modes: dict[int, str] = {}
    for ai, acc in enumerate(body.accounts):
        parties: list[tuple[int, str]] = []
        keyed_contacts: dict[str, int] = {}
        registered_refs: list[tuple[int, OwnerRef]] = []
        for o in acc.owners:
            contact_id = await _resolve_owner(ctx, o, new_cache)
            parties.append((contact_id, o.role))
            if o.party_key:
                keyed_contacts[o.party_key] = contact_id
            if o.role == "registered_contact":
                registered_refs.append((contact_id, o))
            if o.identity_mode is not None:
                previous = identity_modes.get(contact_id)
                if previous is not None and previous != o.identity_mode:
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_CONTENT,
                        "The same person cannot use different identity methods "
                        "on different accounts.",
                    )
                identity_modes[contact_id] = o.identity_mode
        resolved[ai] = parties
        registered_relationships[ai] = {
            (registered_contact_id, keyed_contacts[child_key])
            for registered_contact_id, party in registered_refs
            for child_key in party.registered_contact_for
        }
    flow = published
    if flow is None or flow["vid"] is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No published flow with that code.")

    actor = await _actor_uuid(ctx.session)
    # Every party, in account order, so the first account's owner is the first asked. A contact
    # created inline a moment ago has no adviser yet and simply does not answer.
    all_parties = [cid for ai in sorted(resolved) for cid, _role in resolved[ai]]
    adviser = await _resolve_journey_adviser(
        ctx.session, ctx.tenant_id, all_parties, body.adviser_id, actor
    )
    account_providers = {acc.provider for acc in body.accounts}
    account_custodies = {acc.custody for acc in body.accounts}
    application_provider = next(iter(account_providers)) if len(account_providers) == 1 else None
    application_custody = next(iter(account_custodies)) if len(account_custodies) == 1 else None
    application_id = int(
        (
            await ctx.session.execute(
                text(
                    "INSERT INTO crm.applications (tenant_id, provider, uid, status, custody, "
                    "  adviser_id, adviser_declaration) "
                    "VALUES (:t, CAST(:p AS platform.provider_code), :u, 'draft', "
                    "        CAST(:cust AS platform.custody_type), "
                    "  CAST(:adv AS uuid), false) RETURNING id"
                ),
                {
                    "t": ctx.tenant_id,
                    "p": application_provider,
                    "u": new_id(),
                    "cust": application_custody,
                    "adv": adviser,
                },
            )
        ).scalar_one()
    )
    for ai, acc in enumerate(body.accounts):
        parties = resolved[ai]
        # Primary contact: first party with role=owner, else first party (back-compat).
        # On a JISA that owner is the child.
        primary_contact_id = next(
            (cid for cid, role in parties if role == "owner"),
            parties[0][0],
        )
        owner_row = (
            (
                await ctx.session.execute(
                    text(
                        "SELECT first_name, surname, name, dob::text AS dob "
                        "FROM crm.contacts WHERE id = :c AND tenant_id = :t"
                    ),
                    {"c": primary_contact_id, "t": ctx.tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        data_payload: dict[str, Any] = {}
        wrapper = (acc.product_type or "").strip().upper()
        if not wrapper and acc.product_id is not None:
            wrapper = (
                str(
                    (
                        await ctx.session.execute(
                            text("SELECT product_type::text FROM platform.products WHERE id = :i"),
                            {"i": acc.product_id},
                        )
                    ).scalar_one_or_none()
                    or ""
                )
                .strip()
                .upper()
            )
        if wrapper == "JISA" and owner_row is not None:
            child_name = (
                " ".join(p for p in (owner_row["first_name"], owner_row["surname"]) if p)
                or owner_row["name"]
            )
            data_payload["jisa_child"] = {
                "name": child_name,
                "dob": owner_row["dob"],
                "contact_id": primary_contact_id,
            }
        elif (acc.child_name or "").strip() or (acc.child_dob or "").strip():
            # Older clients still send the sidecar; keep a snapshot only, never invent a contact.
            data_payload["jisa_child"] = {
                "name": (acc.child_name or "").strip() or None,
                "dob": (acc.child_dob or "").strip() or None,
            }
        # currency is deliberately left NULL here: the account_preferences step asks for it, and
        # a stored default would print on the card as a decision nobody made.
        account_id = int(
            (
                await ctx.session.execute(
                    text(
                        "INSERT INTO platform.accounts "
                        "  (tenant_id, contact_id, application_id, product_id, custodian, custody, "
                        "   onboarding_illustration_id, status, uid, data, transfer) "
                        "VALUES (:t, :c, :a, :prod, "
                        "        CAST(:platform AS platform.provider_code), "
                        "        CAST(:custody AS platform.custody_type), :illustration, "
                        "        'draft', :u, "
                        "        CAST(:data AS jsonb), CAST(:transfer AS jsonb)) "
                        "RETURNING id"
                    ),
                    {
                        "t": ctx.tenant_id,
                        "c": primary_contact_id,
                        "a": application_id,
                        "prod": acc.product_id,
                        "platform": acc.provider,
                        "custody": acc.custody,
                        "illustration": onboarding_illustrations.get(ai),
                        "u": new_id(),
                        "data": json.dumps(data_payload) if data_payload else None,
                        "transfer": (
                            json.dumps(kickoff_transfers[ai]) if ai in kickoff_transfers else None
                        ),
                    },
                )
            ).scalar_one()
        )
        illustration_id = onboarding_illustrations.get(ai)
        if illustration_id is not None:
            attached = await ctx.session.execute(
                text(
                    "UPDATE platform.illustrations SET account_id = :a "
                    "WHERE id = :i AND tenant_id = :t AND account_id IS NULL"
                ),
                {"a": account_id, "i": illustration_id, "t": ctx.tenant_id},
            )
            if int(getattr(attached, "rowcount", 0)) != 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "That illustration has already been used for another SIPP account.",
                )
        for cid, role in parties:
            await ctx.session.execute(
                text(
                    "INSERT INTO crm.account_contacts "
                    "  (tenant_id, application_id, account_id, contact_id, role) "
                    "VALUES (:t, :a, :acc, :c, CAST(:role AS crm.account_role)) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "t": ctx.tenant_id,
                    "a": application_id,
                    "acc": account_id,
                    "c": cid,
                    "role": role,
                },
            )
        relationships = registered_relationships[ai]
        if acc.product_type == "JISA" and not relationships:
            # Compatibility for pre-owner-first callers, whose payload says the two roles but has
            # no request-local keys linking them. A JISA has exactly one child owner.
            child_id = next(cid for cid, role in parties if role == "owner")
            relationships = {
                (registered_contact_id, child_id)
                for registered_contact_id, role in parties
                if role == "registered_contact"
            }
        for registered_contact_id, child_id in relationships:
            # The account role says what the adult may DO. The relationship says who the two
            # people ARE to one another, permanently and on both CRM records. Parent/guardian is
            # not inferred: the request names the exact adult for each child.
            await ctx.session.execute(
                text(
                    "INSERT INTO crm.relationships "
                    "  (tenant_id, from_contact_id, to_contact_id, relationship_type) "
                    "VALUES (:t, :adult, :child, 'registered_contact') "
                    "ON CONFLICT (from_contact_id, to_contact_id, relationship_type) "
                    "DO NOTHING"
                ),
                {
                    "t": ctx.tenant_id,
                    "adult": registered_contact_id,
                    "child": child_id,
                },
            )
    if adviser is not None:
        # ``crm.applications.adviser_id`` says who owns this application; the client record and
        # every scoped book read use the separate ``crm.client_advisers`` relationship. An
        # explicit picker replaces each party's adviser. The default only fills parties who
        # have nobody, so a client who already has an adviser stays with them.
        await _record_journey_adviser(
            ctx.session,
            ctx.tenant_id,
            [cid for parties in resolved.values() for cid, _role in parties],
            adviser,
            replace=bool((body.adviser_id or "").strip()),
        )
    definition = flow["definition"]
    if isinstance(definition, str):
        definition = json.loads(definition)
    graph = await _graph(ctx.session, ctx.tenant_id, application_id)
    try:
        created = await orchestrator.start_execution(
            ctx.session,
            tenant_id=ctx.tenant_id,
            flow_id=int(flow["flow_id"]),
            flow_version_id=int(flow["vid"]),
            definition=definition,
            graph=graph,
            application_id=application_id,
            created_by=actor,
            assignee_user_id=adviser,
            data={
                "adviser_declared": True,
                "intake": {
                    **body.intake,
                    "identity_modes": {
                        str(contact_id): mode for contact_id, mode in identity_modes.items()
                    },
                },
            },
        )
    except orchestrator.ActiveExecutionExists as exc:
        # One live journey per (flow, subject) is a deliberate guard — say so, instead of a 500.
        raise _live_journey_conflict(exc) from exc
    return {"execution_uid": created["uid"]}


class StartBody(BaseModel):
    flow_code: str = "onboarding"


@router.post("/{app_uid}/start", name="start-application", status_code=status.HTTP_201_CREATED)
async def start_application(
    app_uid: str,
    body: StartBody,
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Start a published flow over an existing application (adviser-assembled)."""
    require_permission(principal, CASES_WRITE)
    app = (
        (
            await ctx.session.execute(
                text(
                    "SELECT id, provider, adviser_id::text AS adviser_id "
                    "FROM crm.applications WHERE uid = :u AND tenant_id = :t"
                ),
                {"u": app_uid, "t": ctx.tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found.")
    flow = (
        (
            await ctx.session.execute(
                text(
                    "SELECT f.id AS flow_id, f.current_published_version_id AS vid, v.definition "
                    "FROM flows.flows f "
                    "JOIN flows.flow_versions v ON v.id = f.current_published_version_id "
                    "WHERE f.tenant_id = :t AND f.code = :c AND f.status = 'published'"
                ),
                {"t": ctx.tenant_id, "c": body.flow_code},
            )
        )
        .mappings()
        .one_or_none()
    )
    if flow is None or flow["vid"] is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No published flow with that code.")
    definition = flow["definition"]
    if isinstance(definition, str):
        definition = json.loads(definition)
    graph = await _graph(ctx.session, ctx.tenant_id, int(app["id"]))
    try:
        created = await orchestrator.start_execution(
            ctx.session,
            tenant_id=ctx.tenant_id,
            flow_id=int(flow["flow_id"]),
            flow_version_id=int(flow["vid"]),
            definition=definition,
            graph=graph,
            application_id=int(app["id"]),
            created_by=await _actor_uuid(ctx.session),
            # The application already names its adviser; the work starts with them.
            assignee_user_id=app["adviser_id"],
            data={"adviser_declared": True},
        )
    except orchestrator.ActiveExecutionExists as exc:
        raise _live_journey_conflict(exc) from exc
    return {"execution_uid": created["uid"]}


#: The onboarding spine (``onboarding_seed.py``) grouped into stages a CLIENT can make sense of.
#: Order matches the seeded spine: fact-find → terms (client) → identity/AML → account setup.
#: Putting Verification before Review & sign made the milestone strip look ahead of the real phase
#: (terms-before-checks) and told the client they were "verifying" while still waiting to sign.
#:
#: Most of the spine is staff-only fact-find/verification the client never acts on and never sees
#: the detail of (in particular "aml"/"idv" — no title or step_type for a staff step ever reaches
#: the response below, only the bucket label, so this stays clear of the AML tipping-off rule
#: docs/aml.md already draws around a client's own screening position: a generic "Verification"
#: stage tells them nothing an adverse party could act on). A step key absent from every bucket
#: (a gate/condition id, or a step this firm's flow doesn't define) simply never counts against
#: any stage rather than raising — the map is a classification aid, not a schema.
_ONBOARDING_STAGES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "details",
        "Getting your details",
        (
            "core",
            "org",
            "additional_info",
            # `offshore_kyc` is `additional_info`'s FORMER key, kept because a running journey pins
            # its own flow version: the seed publishes a new version and repoints the flow, but an
            # execution already in flight keeps the definition — and the `flows.case_steps` rows —
            # it started with. This map classifies by key and an unknown key counts toward no
            # stage at all, so dropping the old spelling would quietly take that step out of the
            # milestone strip for every client mid-application. It costs one tuple entry and it
            # can be deleted once no execution older than the rename is live.
            "offshore_kyc",
            "pension",
            "employer",
            "activity",
            "account_preferences",
        ),
    ),
    (
        "sign",
        "Review & sign",
        (
            "declaration",
            # The signed W-9 / W-8 the client is asked for beside the terms.
            "fatca_form",
            "terms_general",
            "terms_isa",
            "terms_transfer",
            # `terms_dfm` is the former always-on Discretionary management terms step, kept because
            # a running journey pins its own flow version. Dropping it here would take that step
            # out of the milestone strip for every client mid-application.
            "terms_dfm",
            "terms_platform",
        ),
    ),
    ("verification", "Verification", ("idv", "aml")),
    ("setup", "Account setup", ("offshore_review", "setup")),
)


def _humanise_milestone(slug: str) -> str:
    """``fact_find`` → ``Fact find``. Milestone definitions store a status slug, not a label."""
    text_ = slug.replace("_", " ").strip()
    if not text_:
        return slug
    return text_[:1].upper() + text_[1:]


def _roll_stages(
    rows: list[dict[str, object]],
    specs: list[tuple[str, str, tuple[str, ...], str | None]],
    *,
    complete: bool,
) -> list[dict[str, object]]:
    """First outstanding stage is current; a later stage still waiting is upcoming.

    A stage whose own steps are all fulfilled is done even when an earlier stage is still open.
    Advice journeys run the adviser's notes and the client's declaration side by side, so marking
    everything after the first gap as upcoming told a client their declaration was still to come
    after they had already signed it. A stage none of whose steps were instantiated (a conditional
    this journey did not take, or a later onboarding stage not opened yet) stays upcoming once
    something earlier is current, and counts as satisfied only while the journey has reached it
    with nothing left open in front. ``complete`` appends the onboarding closer; other flows stop
    at the milestones they authored."""
    if not rows:
        return []
    seen_current = False
    stages: list[dict[str, object]] = []
    for key, label, step_keys, summary in specs:
        matching = [r for r in rows if r["step_key"] in step_keys]
        outstanding = any(r["status"] != "fulfilled" for r in matching)
        if matching and not outstanding:
            status_ = "done"
        elif seen_current or not matching:
            # Nothing instantiated yet, behind an open stage: still to come. With nothing open
            # in front, a stage the journey never took is satisfied.
            status_ = "upcoming" if seen_current else "done"
        else:
            status_ = "current"
            seen_current = True
        stage: dict[str, object] = {"key": key, "label": label, "status": status_}
        if summary:
            stage["summary"] = summary
        stages.append(stage)
    if complete:
        stages.append(
            {
                "key": "complete",
                "label": "Complete",
                "status": "upcoming" if seen_current else "done",
            }
        )
    return stages


def _milestone_specs(
    milestones: list[object] | None,
) -> list[tuple[str, str, tuple[str, ...], str | None]]:
    specs: list[tuple[str, str, tuple[str, ...], str | None]] = []
    for item in milestones or []:
        if not isinstance(item, dict):
            continue
        status_key = item.get("status")
        reached = item.get("reached_when")
        if not isinstance(status_key, str) or not status_key:
            continue
        keys = (
            tuple(key for key in reached if isinstance(key, str))
            if isinstance(reached, list)
            else ()
        )
        label = item.get("label")
        shown = (
            label.strip()
            if isinstance(label, str) and label.strip()
            else _humanise_milestone(status_key)
        )
        summary = item.get("summary")
        blurb = summary.strip() if isinstance(summary, str) and summary.strip() else None
        specs.append((status_key, shown, keys, blurb))
    return specs


def _stage_summary(
    rows: list[dict[str, object]],
    *,
    flow_code: str | None = None,
    milestones: list[object] | None = None,
) -> list[dict[str, object]]:
    """Roll the caller's full step list (every assignee) up into the client-facing milestones.

    The onboarding flow keeps ``_ONBOARDING_STAGES`` (details → sign → verification → setup,
    then Complete), including when its definition also carries milestones. Any other flow uses
    the milestones on its own definition, so an advice journey does not show every onboarding
    stage as already complete. The first stage with an outstanding step is current. A later
    stage is done when its own steps are fulfilled, and upcoming while they are still open or
    not yet instantiated. A stage none of whose steps exist counts as satisfied only once the
    journey has reached it."""
    if flow_code is None or flow_code == "onboarding":
        return _roll_stages(
            rows,
            [(key, label, steps, None) for key, label, steps in _ONBOARDING_STAGES],
            complete=True,
        )
    return _roll_stages(rows, _milestone_specs(milestones), complete=False)


def _client_stages(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """One strip per execution. A single journey keeps its stage keys.

    Several journeys prefix each key with that execution's public id. The flow code is not
    enough: two onboardings are both ``onboarding``, and a repeated key throws inside the
    client portal's ``{#each}`` and blanks the page on login."""
    if not rows:
        return []
    grouped: dict[int, list[dict[str, object]]] = {}
    order: list[int] = []
    for row in rows:
        execution_id = int(str(row["execution_id"]))
        if execution_id not in grouped:
            grouped[execution_id] = []
            order.append(execution_id)
        grouped[execution_id].append(row)
    prefix = len(order) > 1
    stages: list[dict[str, object]] = []
    for execution_id in order:
        group = grouped[execution_id]
        head = group[0]
        code = str(head.get("flow_code") or "onboarding")
        raw = head.get("definition")
        if isinstance(raw, str):
            raw = json.loads(raw)
        milestones = raw.get("milestones") if isinstance(raw, dict) else []
        if not isinstance(milestones, list):
            milestones = []
        marker = str(head.get("execution_uid") or execution_id)
        for stage in _stage_summary(group, flow_code=code, milestones=milestones):
            item = dict(stage)
            if prefix:
                item["key"] = f"{marker}:{item['key']}"
            stages.append(item)
    return stages


#: Step types completed BY PRODUCING SOMETHING (mirrors `orchestrator._REQUEST_BACKED`). Until the
#: backing `documents.request_items` row exists there is nothing for the client to open, and
#: `_assert_fulfilment_supported` would refuse the submission anyway.
_CLIENT_REQUEST_BACKED = frozenset({"fill_form", "accept_terms", "upload_document"})


def _client_can_act(step: dict[str, object]) -> bool:
    """Whether this step is the client's to do RIGHT NOW.

    The client portal used to offer every pending step of theirs as a live control, which meant the
    five terms declarations appeared — each with a "Review & agree" button — while the adviser's
    fact-find, identity and AML were all still outstanding. The first of them is held on a STAFF
    step, so the flow itself already said it was not the client's turn; only the surface disagreed.
    Pressing one produced "please try again shortly", framing a deliberate ordering as a glitch.

    Four things must all hold, and each is the same rule the engine enforces server-side:

    * the step is still open (a settled step is nobody's to do);
    * nothing is holding it — `depends_on` is real ordering, not decoration (`_mark_awaiting`);
    * its artifact exists, for the types whose completion IS the artifact
      (`orchestrator._assert_fulfilment_supported` refuses these without one);
    * an identity step's photos are not with the check (``capture_status`` ``submitted`` or
      ``review``): the step stays ``waiting`` until the verdict, and the submit refuses a second
      send. A ``retake`` is theirs again.
    """
    if step.get("status") not in ("pending", "waiting"):
        return False
    if step.get("awaiting"):
        return False
    if step.get("step_type") == "verify_identity" and step.get("capture_status") in (
        idv_capture.SUBMITTED,
        idv_capture.REVIEW,
    ):
        return False
    return not (step.get("step_type") in _CLIENT_REQUEST_BACKED and step.get("item_uid") is None)


def _contact_display_name(row: dict[str, Any]) -> str:
    """Individual → ``first surname``; organisation (or blank names) → ``name``; else email."""
    parts = " ".join(
        p
        for p in (str(row.get("first_name") or "").strip(), str(row.get("surname") or "").strip())
        if p
    )
    return (
        parts
        or str(row.get("name") or "").strip()
        or str(row.get("email") or "").strip()
        or "Unknown"
    )


def _pick_role(roles: list[str]) -> str:
    """One role for the dossier card. Prefer ``owner`` when present; otherwise the first sorted."""
    cleaned = sorted({r for r in roles if r})
    if "owner" in cleaned:
        return "owner"
    return cleaned[0] if cleaned else "owner"


def _address_out(raw: object) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    keys = ("line1", "line2", "city", "region", "postcode", "country")
    out = {
        k: cleaned
        for k in keys
        if (cleaned := str(raw[k]).strip() if raw.get(k) is not None else "")
    }
    return out or None


def _bank_out(raw: object) -> dict[str, str] | None:
    """Only the fields a client should verify — never the free-form ``bank`` label alone."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, str] = {}
    for k in ("name", "sort_code", "account_number"):
        v = raw.get(k)
        if v not in (None, ""):
            out[k] = str(v).strip()
    return out or None


def _text_list(raw: object) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        vals = [str(v).strip() for v in raw if v not in (None, "")]
        return vals or None
    s = str(raw).strip()
    return [s] if s else None


def _transfer_summary(raw: object) -> dict[str, object] | None:
    """Safe kickoff transfer fields only — never provider_id / free-form extras."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, object] = {}
    t = raw.get("transfer_type")
    if t not in (None, ""):
        out["type"] = str(t).strip().lower().replace("-", "_")
    amt = raw.get("amount")
    if amt is not None and amt != "":
        try:
            out["amount"] = float(amt)
        except (TypeError, ValueError):
            pass
    name = raw.get("provider_name")
    if name not in (None, ""):
        cleaned = str(name).strip()
        if cleaned:
            out["provider_name"] = cleaned
    extent = str(raw.get("extent") or "").strip().lower()
    if extent in {"full", "partial"}:
        out["extent"] = extent
    if "safeguarded_benefits" in raw:
        out["safeguarded_benefits"] = raw.get("safeguarded_benefits") is True
    fca = raw.get("fca_reference")
    if fca not in (None, ""):
        cleaned = str(fca).strip()
        if cleaned:
            out["fca_reference"] = cleaned
    return out or None


def _jisa_child_out(data: object) -> dict[str, str] | None:
    if not isinstance(data, dict):
        return None
    child = data.get("jisa_child")
    if not isinstance(child, dict):
        return None
    name = str(child["name"]).strip() if child.get("name") not in (None, "") else ""
    dob_raw = child.get("dob")
    isoformat = getattr(dob_raw, "isoformat", None)
    dob = (
        str(isoformat())
        if callable(isoformat)
        else (str(dob_raw).strip() if dob_raw not in (None, "") else "")
    )
    if not name and not dob:
        return None
    out: dict[str, str] = {}
    if name:
        out["name"] = name
    if dob:
        out["dob"] = dob
    return out


def _beneficiaries_out(data: object) -> list[dict[str, object]] | None:
    if not isinstance(data, dict):
        return None
    raw = data.get("beneficiaries")
    if not isinstance(raw, list) or not raw:
        return None
    out: list[dict[str, object]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        first = str(row["first_name"]).strip() if row.get("first_name") not in (None, "") else ""
        surname = str(row["surname"]).strip() if row.get("surname") not in (None, "") else ""
        if not first and not surname:
            continue
        entry: dict[str, object] = {"first_name": first, "surname": surname}
        portion = row.get("portion")
        if portion is not None and portion != "":
            try:
                entry["portion"] = float(portion)
            except (TypeError, ValueError):
                pass
        out.append(entry)
    return out or None


def _fee_number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _fee_rule_out(raw: object) -> dict[str, object] | None:
    """The charge itself — ``amount`` / ``rate`` / ``tiers`` and nothing else.

    A firm's ``rule`` is jsonb, so it can hold whatever a provider's register published beside
    the price. The client dossier states what they will be charged, so the three keys that are
    the price are projected and the rest is dropped rather than passed through.
    """
    if not isinstance(raw, dict):
        return None
    out: dict[str, object] = {}
    amount = _fee_number(raw.get("amount"))
    if amount is not None:
        out["amount"] = amount
    rate = _fee_number(raw.get("rate"))
    if rate is not None:
        out["rate"] = rate
    tiers = raw.get("tiers")
    if isinstance(tiers, list):
        bands: list[dict[str, object]] = []
        for tier in tiers:
            if not isinstance(tier, dict):
                continue
            band: dict[str, object] = {"from": _fee_number(tier.get("from")) or 0.0}
            upper = _fee_number(tier.get("to"))
            band["to"] = upper
            tier_rate = _fee_number(tier.get("rate"))
            if tier_rate is not None:
                band["rate"] = tier_rate
            tier_amount = _fee_number(tier.get("amount"))
            if tier_amount is not None:
                band["amount"] = tier_amount
            bands.append(band)
        if bands:
            out["tiers"] = bands
    return out or None


def _fee_out(row: dict[str, object]) -> dict[str, object]:
    """One charge as the client reads it: what it is, how much, and how often.

    The dossier used to carry the fee packages' NAMES alone, which names the firm's own pricing
    label ("Standard ongoing") and states neither the figure nor the frequency — so a client
    agreeing to the application could not see what they were agreeing to pay. Everything the
    catalogue holds about the price travels; ids, provider references and the register's own
    bookkeeping do not.
    """
    out: dict[str, object] = {"name": str(row.get("name") or "").strip()}
    for key in ("fee_type", "frequency", "charge_basis", "payer", "description", "currency"):
        value = row.get(key)
        if value in (None, ""):
            continue
        cleaned = str(value).strip()
        if cleaned:
            out[key] = cleaned
    rule = _fee_rule_out(row.get("rule"))
    if rule is not None:
        out["rule"] = rule
    for key in ("minimum_amount", "maximum_amount"):
        number = _fee_number(row.get(key))
        if number is not None:
            out[key] = number
    if row.get("vatable") is not None:
        out["vatable"] = bool(row.get("vatable"))
    return out


def _regular_contribution_out(data: object) -> dict[str, object] | None:
    """Kickoff funding intent from ``accounts.data.regular_contribution`` (or None)."""
    if not isinstance(data, dict):
        return None
    raw = data.get("regular_contribution")
    if not isinstance(raw, dict) or not raw.get("intent"):
        return None
    out: dict[str, object] = {"intent": True}
    amt = raw.get("amount")
    if amt is not None and amt != "":
        try:
            out["amount"] = float(amt)
        except (TypeError, ValueError):
            pass
    freq = raw.get("frequency")
    if freq not in (None, ""):
        cleaned = str(freq).strip().lower().replace("-", "_")
        if cleaned in _CONTRIBUTION_FREQUENCIES:
            out["frequency"] = cleaned
    expected = raw.get("expected_by")
    if expected not in (None, ""):
        if hasattr(expected, "isoformat"):
            out["expected_by"] = expected.isoformat()
        else:
            cleaned = str(expected).strip()
            if cleaned:
                out["expected_by"] = cleaned
    else:
        out["expected_by"] = None
    return out


# ---- the client portal's ACTIVE contact ------------------------------------------------------
#
# ``auth.contact_scope()`` is EVERY ``crm.contacts`` row this login may act for. A login linked to
# more than one of them — a guardian, a delegate, a couple sharing a login — has exactly ONE of
# them active at a time: the portal's switcher picks it and sends it as ``?contact_id=``, the same
# selector ``/notifications``, ``/documents`` and ``/crm/context`` already take.
#
# Reading the whole scope here made ONE contact's live journey the whole LOGIN's state. The client
# shell collapses its navigation to "Onboarding" alone while a journey runs and bounces ``/``
# there, so a second contact still onboarding locked every other client behind that login onto
# somebody else's application — and the switcher's own destination is ``/``, which redirected
# straight back. There was no way out of it short of signing out.
#
# NARROWS, NEVER WIDENS. ``:cid`` is honoured only when it genuinely is in the caller's scope, so
# a stale cookie or a tampered value cannot reach another login's contact; it falls back to the
# whole scope, which is both the previous behaviour and the right answer for the single-contact
# login that is the overwhelming majority.
#
# Written as a bare CASE rather than a `WITH scope AS (...)` the predicates then select from:
# `= ANY(<subquery>)` is the SQL *subquery* form of ANY, so Postgres reads `id = ANY((SELECT ids
# ...))` as comparing an integer against a set whose one row is an int[] and refuses it with
# "operator does not exist: integer = integer[]". An array-valued EXPRESSION takes the array form,
# which is the one meant. `auth.contact_scope()` is STABLE, so repeating it costs nothing.
_ACTIVE_SCOPE = (
    "(CASE WHEN CAST(:cid AS int) = ANY(auth.contact_scope()) "
    "THEN ARRAY[CAST(:cid AS int)] ELSE auth.contact_scope() END)"
)


async def _client_review_dossier(
    session: Any, tenant_id: int, contact_id: int | None = None
) -> dict[str, object] | None:
    """Read-only review of what the adviser entered, for the client's primary open journey.

    Parity with the legacy overview / applicant / accounts steps — WITHOUT the legacy defect that
    showed every co-applicant's AML and identity ticks. Scoped by ``auth.contact_scope()``, and
    narrowed to ``contact_id`` when the caller named one they really hold (``_ACTIVE_SCOPE``):
    only applications the ACTIVE contact is a party to. ``you`` is that contact's own row (full
    details to verify); other applicants get name + type + role only.

    ``you`` also depends on the narrowing. ``is_you`` is a scope test, so with two of a login's
    contacts named on one application it picked whichever the party ordering reached first — the
    review could show the other person's details under "Your details".

    Returns ``None`` only when there is no journey at all — a CANCELLED one is excluded, a
    COMPLETED one is not.

    **A completed journey used to return ``None``, and that is what the client was shown.** The
    step query two functions down excludes `cancelled` alone, so the moment the journey finished
    the portal still rendered the page — every step ticked — beside a "Your details" card reading
    "Your adviser has not recorded personal details yet". The details had been entered; the
    dossier had simply stopped being willing to read them, and the empty state blamed the adviser
    for it. Nothing here is time-limited: `you` is the caller's own record and the other
    applicants are name/type/role, which is no more sensitive the day after completion than the
    day before. The two filters now agree, which is the actual fix — one surface answering from
    two queries that disagreed about which journeys exist.
    """
    # Most recently started execution the caller is on. Same party test as /me's step query
    # (profile subject OR a fanned participant step), so a joint account's second owner still sees
    # the dossier even when their profile case is not the subject's.
    head = (
        (
            await session.execute(
                text(
                    "SELECT e.id AS execution_id, a.id AS application_id, "  # noqa: S608 - a module constant; the caller's value binds as :cid
                    "       a.uid AS application_uid, a.custody::text AS custody, "
                    "       t.name AS firm_name "
                    "FROM flows.executions e "
                    "JOIN crm.applications a ON a.id = e.crm_application_id "
                    "JOIN auth.tenants t ON t.id = e.tenant_id "
                    "WHERE e.tenant_id = :t "
                    # `<> 'cancelled'`, matching the step query: a finished journey's details
                    # are still the client's details. See the docstring.
                    "  AND e.status <> 'cancelled' "
                    "  AND EXISTS ("
                    "    SELECT 1 FROM flows.cases c "
                    "    LEFT JOIN flows.case_steps cs ON cs.case_id = c.id "
                    "    WHERE c.execution_id = e.id "
                    f"      AND (c.subject_contact_id = ANY({_ACTIVE_SCOPE}) "
                    f"           OR cs.participant_subject_id = ANY({_ACTIVE_SCOPE}))"
                    "  ) "
                    "ORDER BY e.created_at DESC "
                    "LIMIT 1"
                ),
                {"t": tenant_id, "cid": contact_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if head is None:
        return None
    application_id = int(head["application_id"])

    party_rows = (
        (
            await session.execute(
                text(
                    # S608: only the module constant below is interpolated; :cid remains bound.
                    "SELECT ct.id, ct.uid, ct.type::text AS type, ct.first_name, "  # noqa: S608
                    "       ct.surname, ct.name, ct.email, ac.role::text AS role, "
                    f"       (ct.id = ANY({_ACTIVE_SCOPE})) AS is_you "
                    "FROM crm.account_contacts ac "
                    "JOIN crm.contacts ct ON ct.id = ac.contact_id "
                    "WHERE ac.application_id = :a AND ac.tenant_id = :t "
                    "ORDER BY ct.id, ac.role"
                ),
                {"a": application_id, "t": tenant_id, "cid": contact_id},
            )
        )
        .mappings()
        .all()
    )
    # Collapse multi-account role rows into one applicant card per contact.
    by_contact: dict[int, dict[str, Any]] = {}
    for r in party_rows:
        cid = int(r["id"])
        entry = by_contact.get(cid)
        if entry is None:
            by_contact[cid] = {
                "id": cid,
                "uid": r["uid"],
                "name": _contact_display_name(dict(r)),
                "type": str(r["type"] or "individual"),
                "roles": [str(r["role"] or "")],
                "email": r["email"],
                "is_you": bool(r["is_you"]),
            }
        else:
            entry["roles"].append(str(r["role"] or ""))
            entry["is_you"] = entry["is_you"] or bool(r["is_you"])

    applicants: list[dict[str, object]] = []
    you_id: int | None = None
    for cid, entry in by_contact.items():
        role = _pick_role(entry["roles"])
        applicant_card: dict[str, object] = {
            "uid": entry["uid"],
            "name": entry["name"],
            "type": entry["type"],
            "role": role,
            "is_you": entry["is_you"],
        }
        # Co-applicants: name + type + role only. Legacy showed emails AND compliance
        # ticks; omitting both for others is safer (email is verified under `you`).
        if entry["is_you"]:
            you_id = cid
            if entry["email"]:
                applicant_card["email"] = entry["email"]
        applicants.append(applicant_card)

    account_rows = (
        (
            await session.execute(
                text(
                    "SELECT acc.id, acc.uid, acc.name, acc.currency, "
                    "       COALESCE(acc.custody::text, ("
                    "         SELECT custody::text FROM crm.applications WHERE id = :a"
                    "       )) AS custody, "
                    "       COALESCE(p.name, p.product_type::text) AS product, "
                    "       p.product_type::text AS product_type, "
                    "       (acc.transfer IS NOT NULL) AS has_transfer, "
                    "       acc.transfer AS transfer, "
                    "       acc.data AS data, "
                    "       acc.investment_capacity, "
                    "       acc.dividend_treatment, "
                    "       m.name AS model, "
                    "       COALESCE((SELECT jsonb_agg(jsonb_build_object("
                    "             'name', fp.name, 'fee_type', fp.fee_type, 'rule', fp.rule, "
                    "             'frequency', fp.frequency, 'currency', fp.currency, "
                    "             'charge_basis', fp.charge_basis, 'payer', fp.payer, "
                    "             'vatable', fp.vatable, 'minimum_amount', fp.minimum_amount, "
                    "             'maximum_amount', fp.maximum_amount, "
                    "             'description', fp.description) "
                    "           ORDER BY afp.sort_order, fp.name) "
                    "         FROM platform.account_fee_packages afp "
                    "         JOIN platform.fee_packages fp ON fp.id = afp.fee_package_id "
                    "          AND fp.tenant_id = afp.tenant_id "
                    "        WHERE afp.account_id = acc.id), '[]'::jsonb) AS fees, "
                    "       ac.role::text AS party_role, "
                    "       COALESCE(ct.first_name || ' ' || ct.surname, ct.name, "
                    "                ct.email) AS owner_name "
                    "FROM platform.accounts acc "
                    "JOIN crm.account_contacts ac ON ac.account_id = acc.id "
                    "  AND ac.application_id = :a AND ac.tenant_id = :t "
                    "LEFT JOIN platform.products p ON p.id = acc.product_id "
                    "LEFT JOIN platform.models m ON m.id = acc.model_id "
                    "LEFT JOIN crm.contacts ct ON ct.id = ac.contact_id "
                    "WHERE acc.application_id = :a AND acc.tenant_id = :t "
                    "ORDER BY acc.id, ac.role, ct.id"
                ),
                {"a": application_id, "t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    accounts_by_id: dict[int, dict[str, Any]] = {}
    for r in account_rows:
        aid = int(r["id"])
        acc = accounts_by_id.get(aid)
        owner = str(r["owner_name"] or "").strip()
        party_role = str(r["party_role"] or "").strip()
        if acc is None:
            product = r["product"]
            name = (
                str(r["name"] or "").strip()
                or (str(product).strip() if product else None)
                or "Account"
            )
            has_transfer = bool(r["has_transfer"])
            card: dict[str, Any] = {
                "uid": r["uid"],
                "name": name,
                "product": str(product).strip() if product else None,
                "product_type": str(r["product_type"] or "").strip() or None,
                "currency": str(r["currency"] or "").strip() or None,
                "custody": r["custody"],
                "has_transfer": has_transfer,
                # `owners` is the OWNERS, not every party. A JISA names the child as owner and the
                # parent as `registered_contact`, and listing both under one "Owners" label told a
                # parent reading the dossier that they own their child's account — which is the one
                # thing a JISA's structure turns on. `parties` carries the roles so a surface can
                # say who is who without re-deriving it from a flat list of names.
                "owners": [owner] if owner and party_role == "owner" else [],
                "parties": [{"name": owner, "role": party_role}] if owner else [],
            }
            model = str(r["model"] or "").strip()
            if model:
                card["model"] = model
            fees = r["fees"]
            if isinstance(fees, str):
                fees = json.loads(fees)
            fees = [f for f in (fees or []) if isinstance(f, dict)]
            card["fees"] = [_fee_out(f) for f in fees]
            # Names alone, kept beside the detail: an older portal build reads this key.
            card["fee_packages"] = [
                str(f.get("name") or "").strip() for f in fees if str(f.get("name") or "").strip()
            ]
            icap = str(r["investment_capacity"] or "").strip()
            if icap:
                card["investment_capacity"] = icap
            div = str(r["dividend_treatment"] or "").strip()
            if div:
                card["dividend_treatment"] = div
            if has_transfer:
                xfer = _transfer_summary(_as_json_obj(r["transfer"]))
                if xfer is not None:
                    card["transfer"] = xfer
            data = _as_json_obj(r["data"])
            jisa = _jisa_child_out(data)
            if jisa is not None:
                card["jisa_child"] = jisa
            bens = _beneficiaries_out(data)
            if bens is not None:
                card["beneficiaries"] = bens
            funding = _regular_contribution_out(data)
            if funding is not None:
                card["regular_contribution"] = funding
            accounts_by_id[aid] = card
        elif owner:
            if party_role == "owner" and owner not in acc["owners"]:
                acc["owners"].append(owner)
            if not any(p["name"] == owner and p["role"] == party_role for p in acc["parties"]):
                acc["parties"].append({"name": owner, "role": party_role})

    you: dict[str, object] = {"name": "", "email": None}
    if you_id is not None:
        # Explicit column list — never SELECT * — so AML/KYC/screening columns cannot leak even if
        # someone later joins a mirror table. Identity/AML status lives in crm.kyc_checks / aml.*,
        # not on contacts, but the allowlist is the tripwire the test asserts against.
        you_row = (
            (
                await session.execute(
                    text(
                        "SELECT first_name, surname, name, email, mobile, phone, dob, "
                        "       nationality, tax_residency, national_identifier, "
                        "       national_identifier_type, address, bank_account, "
                        "       flexi_access_taken "
                        "FROM crm.contacts WHERE id = :c AND tenant_id = :t"
                    ),
                    {"c": you_id, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if you_row is not None:
            dob = you_row["dob"]
            you = {
                "name": _contact_display_name(dict(you_row)),
                "email": you_row["email"],
                "dob": dob.isoformat()
                if hasattr(dob, "isoformat")
                else (str(dob) if dob else None),
                "nationality": _text_list(you_row["nationality"]),
                "tax_residency": _text_list(you_row["tax_residency"]),
                "national_identifier": you_row["national_identifier"],
                "national_identifier_type": you_row["national_identifier_type"],
                # `normalise_phone`, not the raw column: a book merged from Seccl before the
                # conformed model started extracting `->> 'number'` holds the provider's whole
                # OBJECT in the text column. A number that is already a number is unchanged.
                "mobile": normalise_phone(you_row["mobile"]),
                "phone": normalise_phone(you_row["phone"]),
                "address": _address_out(you_row["address"]),
                "bank_account": _bank_out(you_row["bank_account"]),
            }
            if you_row["flexi_access_taken"]:
                you["flexi_access_taken"] = True

    return {
        "application_uid": head["application_uid"],
        "custody": head["custody"],
        "firm_name": head["firm_name"],
        "applicants": applicants,
        "accounts": list(accounts_by_id.values()),
        "you": you,
    }


@router.get("/me", name="my-onboarding")
async def my_onboarding(
    contact_id: int | None = None,
    principal: Principal = Depends(get_current_principal),
    # portal_session, not firm_session: this is the CLIENT's own view (scoped to contact_scope),
    # and firm_session's platform/operations whitelist would 403 every client-portal session.
    ctx: FirmContext = Depends(portal_session),
) -> dict[str, object]:
    """The signed-in client's own outstanding onboarding — the steps assigned to them across any
    running application, scoped by ``auth.contact_scope()`` (their own party). No permission gate:
    it only ever returns the caller's own tasks. Powers the client portal's onboarding view.

    ``contact_id`` is the portal's active-contact selector, honoured only when it really is one
    of the caller's own (``_ACTIVE_SCOPE``) and ignored otherwise. Without it a login linked to
    two clients answered for BOTH at once, and one of them still onboarding was enough to hold
    the whole login on the onboarding page — see that constant for the full account.

    ``stages`` is additive (``_stage_summary``): the whole-journey milestone view, computed from
    EVERY step regardless of assignee so a staff-only tier the client cannot act on still shows as
    "with your adviser" rather than a gap in the picture — but never carrying a staff step's own
    title/type, only which bucket it falls in and whether that bucket has cleared.

    ``review`` is the read-only dossier of adviser-entered detail (null when no active journey) —
    what the client checks before agreeing terms, without co-applicant AML/identity status."""
    rows = (
        (
            await ctx.session.execute(
                text(
                    "SELECT cs.step_key, cs.step_type, cs.title, cs.status, cs.assignee, "  # noqa: S608 - a module constant; the caller's value binds as :cid
                    # Which CASE this step belongs to. Without it the portal could only name a
                    # step by its key, and a client with two journeys in flight has the same
                    # `idv` key in both — so an action taken on one could land on the other.
                    "       c.uid AS case_uid, "
                    "       ri.uid AS item_uid, r.uid AS request_uid, e.uid AS execution_uid, "
                    # The catalogue term an accept_terms step pins, when it pins one. The portal
                    # lists the firm's other outstanding terms beside the steps; this is how it
                    # tells a term a step already collects from one nothing collects.
                    "       td.metadata ->> 'term_uid' AS term_uid, "
                    "       f.name AS flow_name, f.code AS flow_code, "
                    "       COALESCE(ct.first_name || ' ' || ct.surname, ct.name) AS subject_name, "
                    "       cs.data -> 'awaiting' AS awaiting, "
                    "       cs.data -> 'identity_capture' ->> 'status' AS capture_status, "
                    "       e.id AS execution_id, v.definition "
                    "FROM flows.case_steps cs "
                    "JOIN flows.cases c ON c.id = cs.case_id "
                    "JOIN flows.executions e ON e.id = c.execution_id "
                    "LEFT JOIN flows.flow_versions v ON v.id = e.flow_version_id "
                    "LEFT JOIN flows.flows f ON f.id = e.flow_id "
                    "LEFT JOIN crm.contacts ct ON ct.id = c.subject_contact_id "
                    "LEFT JOIN documents.request_items ri ON ri.id = cs.request_item_id "
                    "LEFT JOIN documents.requests r ON r.id = ri.request_id "
                    "LEFT JOIN documents.documents td ON td.id = ri.target_document_id "
                    # SKIPPED is excluded HERE, not by the page, because this endpoint computes
                    # `total`/`available` and the stage rollup off these rows. A step a condition
                    # ruled out was counting toward the client's total, and `_stage_summary` reads
                    # anything not `fulfilled` as outstanding — so a skipped step held its whole
                    # stage open and the milestone stepper stuck on a phase that was finished.
                    "WHERE cs.status NOT IN ('cancelled', 'skipped') "
                    "  AND e.status <> 'cancelled' "
                    f"  AND (c.subject_contact_id = ANY({_ACTIVE_SCOPE}) "
                    f"       OR cs.participant_subject_id = ANY({_ACTIVE_SCOPE})) "
                    "ORDER BY (cs.status = 'fulfilled'), cs.id"
                ),
                {"cid": contact_id},
            )
        )
        .mappings()
        .all()
    )
    all_steps = [dict(r) for r in rows]
    # In the order the journey RUNS, exactly as the staff view does — the row id cannot say it,
    # because a client's steps are spread across cases (their own profile, each account they hold)
    # and ids interleave by case rather than by sequence. The definition is the only place the
    # order lives, and it is per EXECUTION: a client with two journeys in flight has two of them.
    order_by_exec: dict[int, dict[str, int]] = {}
    for row in all_steps:
        exec_id = int(row["execution_id"])
        if exec_id not in order_by_exec:
            raw = row.get("definition")
            if isinstance(raw, str):
                raw = json.loads(raw)
            specs = raw.get("steps") or [] if isinstance(raw, dict) else []
            order_by_exec[exec_id] = {
                spec["id"]: i
                for i, spec in enumerate(specs)
                if isinstance(spec, dict) and isinstance(spec.get("id"), str)
            }
        known = order_by_exec[exec_id]
        # A step the definition no longer names sorts LAST, not first (where a 0 would put it).
        row["order"] = known.get(str(row["step_key"]), len(known))
    # Settled work still sinks below outstanding work — that grouping is what makes this a
    # to-do list rather than a transcript — but WITHIN each group the journey's own order holds.
    all_steps.sort(key=lambda r: (r["status"] == "fulfilled", r["order"]))
    stages = _client_stages(all_steps)
    flow_codes = {str(row["flow_code"]) for row in all_steps if row.get("flow_code")}
    flow_name = next((str(row["flow_name"]) for row in all_steps if row.get("flow_name")), None)
    for row in all_steps:
        row.pop("definition", None)
        row.pop("execution_id", None)
        row.pop("flow_code", None)
        row.pop("flow_name", None)
    steps = [
        {k: v for k, v in s.items() if k not in ("assignee", "capture_status")}
        | {"available": _client_can_act(s)}
        for s in all_steps
        if s["assignee"] == "client"
    ]
    done = sum(1 for s in steps if s["status"] == "fulfilled")
    review = await _client_review_dossier(ctx.session, ctx.tenant_id, contact_id)
    return {
        "steps": steps,
        "done": done,
        "total": len(steps),
        # What is genuinely theirs to do RIGHT NOW. `total` is the whole client journey (the honest
        # denominator for a progress bar); this is the honest numerator for "you have things to do".
        "available": sum(1 for s in steps if s["available"]),
        "stages": stages,
        "review": review,
        # One journey: its name, so the client portal does not title every journey "Onboarding".
        # Several at once have no single name.
        "flow_name": flow_name if len(flow_codes) == 1 else None,
        "flow_code": next(iter(flow_codes)) if len(flow_codes) == 1 else None,
    }


# The caller's own outstanding identity step. Deliberately NOT narrowed to `assignee = 'client'`:
# recording a manual check REASSIGNS the step to staff, so a read that required the client
# assignment could never see a step whose documents had just been asked for — the reload came back
# empty and the second POST answered 404 where its own contract says 409.
_IDENTITY_STEP_SQL = (
    "SELECT cs.case_id, cs.step_key, cs.participant_subject_id, c.uid AS case_uid, "
    "       c.execution_id, "
    "       cs.data ->> 'kyc_check_id' AS kyc_check_id, "
    # What the CLIENT chose, when they have chosen. Absent means they have not been asked yet.
    "       cs.data ->> 'identity_choice' AS identity_choice, "
    "       cs.data -> 'identity_documents' AS identity_documents, "
    "       cs.data -> 'identity_capture' AS identity_capture, "
    "       k.status AS check_status, k.provider AS provider "
    "FROM flows.case_steps cs "
    "JOIN flows.cases c ON c.id = cs.case_id "
    "JOIN flows.executions e ON e.id = c.execution_id "
    # Only ever the check THIS step opened, and only its status/provider: an identity check's
    # detail is no more the client's to read than a screening's (docs/aml.md, tipping-off).
    "LEFT JOIN crm.kyc_checks k "
    "  ON k.id = CAST(NULLIF(cs.data ->> 'kyc_check_id', '') AS bigint) "
    "WHERE cs.step_type = 'verify_identity' "
    "  AND cs.status NOT IN ('cancelled', 'skipped', 'fulfilled') "
    "  AND e.status <> 'cancelled' "
    "  AND cs.participant_subject_id = ANY(auth.contact_scope()) "
    "  AND (CAST(:sk AS text) IS NULL OR cs.step_key = CAST(:sk AS text)) "
    "  AND (CAST(:cu AS text) IS NULL OR c.uid = CAST(:cu AS text)) "
    "ORDER BY cs.id DESC LIMIT 1"
)


class IdentityChoice(BaseModel):
    """Which of the caller's identity steps they are choosing for, when they hold more than one.
    Both absent answers the most recent."""

    case_uid: str | None = None
    step_key: str | None = None


def _case_uid_of(body: IdentityChoice | None) -> str | None:
    return body.case_uid if body else None


def _step_key_of(body: IdentityChoice | None) -> str | None:
    return body.step_key if body else None


_NO_IDENTITY_STEP = "No identity verification is outstanding for you"
_NO_CAPTURE = "Your identity is not checked from photos here"


async def _capture_row(
    ctx: FirmContext, *, step_key: str | None, case_uid: str | None, lock: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The caller's identity step and its photo capture. 404 when there is no step, or the step is
    not a photo capture (a manual check, or one that never asked for photos). ``lock`` holds the
    step for a send, so two sends of one try cannot both reach ComplyCube."""
    sql = _IDENTITY_STEP_SQL + (" FOR UPDATE OF cs" if lock else "")
    row = (
        (await ctx.session.execute(text(sql), {"sk": step_key, "cu": case_uid}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_IDENTITY_STEP)
    state = idv_capture.state_of({idv_capture.CAPTURE_KEY: row["identity_capture"]})
    if state is None or not idv_capture.item_uids(state) or row["identity_choice"] is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_CAPTURE)
    return dict(row), state


def _capture_status(state: dict[str, Any], check_status: object) -> str:
    """Where the capture stands for the client. ``passed`` comes from the check; the rest is the
    capture's own record (``app/idv/capture.py``)."""
    if check_status == "passed":
        return idv_capture.PASSED
    stored = state.get("status")
    if stored in (
        idv_capture.CAPTURING,
        idv_capture.SUBMITTED,
        idv_capture.RETAKE,
        idv_capture.REVIEW,
    ):
        return str(stored)
    return idv_capture.CAPTURING


async def _capture_body(
    ctx: FirmContext, row: dict[str, Any], state: dict[str, Any]
) -> dict[str, object]:
    copies = await idv_held.keys_held(ctx.session, slots=idv_capture.item_uids(state))
    attempt = state.get("attempt")
    reason = state.get("last_reason")
    document_type = state.get("document_type")
    # Named field by field: the capture also holds operations' own record of the provider
    # (`last_provider_error`, `provider_progress`), which is never the client's to read.
    return {
        "items": idv_capture.items_of(state, copies),
        "status": _capture_status(state, row["check_status"]),
        "attempt": int(attempt) if isinstance(attempt, int) and attempt >= 1 else 1,
        # The client-safe reason an operator chose (`complycube.identity.PHOTO_REQUEST_REASONS`).
        "last_reason": reason if isinstance(reason, str) and reason else None,
        # The document the photos are of, once sent. A retake that kept the document's photos
        # keeps it, so the client is asked only for what is open.
        "document_type": document_type if document_type in idv_capture.DOCUMENT_TYPES else None,
        # The digital check is on offer, so the manual one is a real alternative the client may
        # take (`POST …/identity-manual`). Stated by the API rather than assumed by the portal:
        # offering "a person can do this instead" where nobody is assigned to do it is a control
        # that cannot act.
        "manual_offered": row["identity_choice"] is None,
        # What the client has taken on a phone and not sent yet: the computer waiting on the phone
        # shows each photo arrive. Never the photos themselves.
        "phone_progress": idv_capture.phone_progress(state),
    }


@router.get("/me/identity-capture", name="my-identity-capture")
async def my_identity_capture(
    step_key: str | None = None,
    case_uid: str | None = None,
    principal: Principal = Depends(get_current_principal),
    # portal_session for the reason `/me` uses it: this is the CLIENT's own view.
    ctx: FirmContext = Depends(portal_session),
) -> dict[str, object]:
    """The photos the caller's identity check asks for, and where the check stands.

    Each photo is an item: ``open`` while the client is to send it, ``fulfilled`` once ComplyCube
    or our held copy has it for this try. ``attempt`` is the try the client is on (from 1); there
    is no limit on tries. ``last_reason`` is the client-safe reason given when new photos were
    asked for, or null. Nothing here says what the check found.

    Scoped by ``auth.contact_scope()``; ``case_uid``/``step_key`` narrow it when the client holds
    more than one identity step, and without them it answers the most recent. 404 when there is no
    such step, or the step is not checked from photos (``app/idv/capture.py``).
    """
    row, state = await _capture_row(ctx, step_key=step_key, case_uid=case_uid)
    return await _capture_body(ctx, row, state)


#: Why the check cannot start on a different document from the one whose photos were kept.
SAME_DOCUMENT_NEEDED = "Please send the same document as before."


def _kept_document_type(state: dict[str, Any]) -> str | None:
    """The document type a retake is held to: the one ComplyCube holds photos of for this try.
    None when it holds no photo of the document."""
    if not idv_capture.held_keys(state) & {idv_capture.FRONT, idv_capture.BACK}:
        return None
    document_type = idv_capture.progress_of(state).get("document_type")
    return str(document_type) if document_type in idv_capture.DOCUMENT_TYPES else None


#: Why the check cannot start for a contact with no nationality or address country: ComplyCube needs
#: the document's issuing country. Shown to the client as it stands, so it says what to do.
NATIONALITY_NEEDED = (
    "We need your nationality before we can check your ID. Please ask your adviser to add it."
)

#: What the client is told when their photos cannot be taken now. Nothing is lost.
SEND_AGAIN = "Your photos could not be sent. Please try again."


async def _photo(key: str, file: UploadFile) -> idv_held.Photo:
    """One photo the client sent, checked against what ComplyCube takes for it. 422 with the
    client-safe reason otherwise."""
    shot = idv_capture.SHOT_BY_KEY[key]
    try:
        data = await storage.read_capped(file, limit=idv_capture.MAX_PHOTO_BYTES)
    except storage.PayloadTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=shot.size_refusal
        ) from exc
    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=shot.size_refusal
        )
    try:
        content_type = storage.sniff_document_type(data[:4096])
    except ValueError:
        content_type = ""
    refusal = shot.refusal_for(content_type, len(data))
    if refusal is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=refusal)
    return idv_held.Photo(data=data, content_type=content_type)


@router.post(
    "/me/identity-capture/submit",
    name="submit-my-identity-capture",
    response_model=dict[str, object],
)
async def submit_my_identity_capture(
    document_type: Literal["passport", "driving_licence", "national_id"] = Form(...),
    step_key: str | None = Form(None),
    case_uid: str | None = Form(None),
    idv_document_front: UploadFile | None = File(None),
    idv_document_back: UploadFile | None = File(None),
    idv_selfie: UploadFile | None = File(None),
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(portal_session),
) -> dict[str, object]:
    """The client sends their photos, naming the document they show: start the check.

    Each file is named by its item key, and the request carries every photo the document needs
    that ComplyCube does not hold yet (a passport has no back). A retake that kept photos of the
    document must name that document. The photos are held encrypted until the identity step
    finishes (``app/idv/held.py``), the capture becomes ``submitted``, and
    ``identity.check.requested`` is enqueued: the worker sends them to ComplyCube
    (``app.complycube.identity.submit``) and tries again while ComplyCube is down.

    Answers the capture. 409 when the photos were already sent, a needed photo is missing, the
    document differs from the kept one, or the client has no nationality to name the document's
    issuer; 422 when a photo is not one ComplyCube takes; 503 when the photos cannot be held now.
    """
    row, state = await _capture_row(ctx, step_key=step_key, case_uid=case_uid, lock=True)
    if _capture_status(state, row["check_status"]) not in (
        idv_capture.CAPTURING,
        idv_capture.RETAKE,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="These photos have already been sent"
        )
    kyc_check_id = row["kyc_check_id"]
    if not kyc_check_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This identity check cannot be started yet",
        )
    kept_type = _kept_document_type(state)
    if kept_type is not None and document_type != kept_type:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=SAME_DOCUMENT_NEEDED)
    files = {
        idv_capture.FRONT: idv_document_front,
        idv_capture.BACK: idv_document_back,
        idv_capture.SELFIE: idv_selfie,
    }
    # A photo ComplyCube holds, or we hold a copy of, is not sent again.
    copies = await idv_held.keys_held(ctx.session, slots=idv_capture.item_uids(state))
    ready = idv_capture.held_keys(state) | copies
    wanted = [key for key in idv_capture.needed_keys(document_type) if key not in ready]
    if any(files[key] is None for key in wanted):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Send every photo before you start the check",
        )
    contact_id = int(row["participant_subject_id"])
    contact = (
        (
            await ctx.session.execute(
                text(
                    f"SELECT {complycube_clients.CONTACT_COLUMNS} FROM crm.contacts "  # noqa: S608 - a module constant
                    "WHERE id = :c AND tenant_id = :t AND deleted_at IS NULL"
                ),
                {"c": contact_id, "t": ctx.tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if contact is None or complycube_identity.issuing_country(dict(contact)) is None:
        # The check names the document's issuing country from the client's nationality.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NATIONALITY_NEEDED)
    photos = {key: await _photo(key, cast(UploadFile, files[key])) for key in wanted}
    step = idv_capture.CaptureStep(
        tenant_id=ctx.tenant_id,
        case_id=int(row["case_id"]),
        step_key=str(row["step_key"]),
        contact_id=contact_id,
        state=state,
        manual=False,
    )
    try:
        await idv_held.hold(
            ctx.session,
            idv_held.StepRef(
                tenant_id=step.tenant_id,
                case_id=step.case_id,
                step_key=step.step_key,
                contact_id=step.contact_id,
            ),
            idv_capture.item_uids(state),
            photos,
        )
    except crypto.SecretsNotConfigured as exc:
        logger.error(
            "identity photos cannot be held: %s",
            exc,
            extra={"event": "identity_photos_unsealed"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=SEND_AGAIN
        ) from exc
    submitted = await idv_capture.mark_submitted(
        ctx.session,
        step,
        document_type=document_type,
        progress=idv_capture.progress_of(state),
    )
    await notifications.enqueue(
        ctx.session,
        tenant_id=ctx.tenant_id,
        event_type=complycube_identity.EVENT,
        payload={
            "contact_id": contact_id,
            "case_id": step.case_id,
            "step_key": step.step_key,
            "kyc_check_id": int(kyc_check_id),
            "attempt": int(submitted.get("attempt") or 1),
            "document_type": document_type,
            "items": idv_capture.item_uids(submitted),
        },
    )
    # No explicit commit: `portal_session` commits on a clean exit.
    return await _capture_body(ctx, row, submitted)


class IdentityPhoneLinkOut(BaseModel):
    """The short link, and a QR code of it. The code inside the link is shown once."""

    url: str
    qr_svg: str
    expires_at: str


async def _phone_portal_url(session: AsyncSession, tenant_id: int) -> str | None:
    """Where the phone link opens. A configured base URL (``identity_phone_base_url``, then
    ``client_portal_base_url``; dev: the local client portal) beats the tenant's registered
    host, the same precedence the other portal links use."""
    configured = settings.identity_phone_base_url.strip() or settings.client_portal_base_url.strip()
    if configured:
        return idv_phone.reachable_base(configured)
    try:
        found = await environments.portal_base_url(session, tenant_id, "client")
    except LookupError:
        return None
    return idv_phone.reachable_base(found) if found else None


async def _phone_step(
    ctx: FirmContext, body: IdentityChoice | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The caller's photo capture, while it is still theirs to take. 409 once the photos
    have been sent: a link for a check that has already started would have nothing to do."""
    row, state = await _capture_row(ctx, step_key=_step_key_of(body), case_uid=_case_uid_of(body))
    status_now = _capture_status(state, row["check_status"])
    if status_now not in (idv_capture.CAPTURING, idv_capture.RETAKE):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="These photos have already been sent"
        )
    return row, state


async def _clear_phone_progress(
    ctx: FirmContext, row: dict[str, Any], state: dict[str, Any]
) -> None:
    """Forget what an earlier phone took: a new link, or none, starts the photos afresh."""
    await idv_capture.record_phone_progress(
        ctx.session,
        idv_capture.CaptureStep(
            tenant_id=ctx.tenant_id,
            case_id=int(row["case_id"]),
            step_key=str(row["step_key"]),
            contact_id=int(row["participant_subject_id"]),
            state=state,
            manual=False,
        ),
        keys=(),
        document_type=None,
    )


async def _phone_user(ctx: FirmContext) -> str:
    user_id = (
        await ctx.session.execute(
            text(
                "SELECT id FROM auth.users "
                " WHERE workos_user_id = :s AND tenant_id = :t AND deleted_at IS NULL"
            ),
            {"s": ctx.principal.user_id, "t": ctx.tenant_id},
        )
    ).scalar_one_or_none()
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No resolvable identity for this login",
        )
    return str(user_id)


@router.post(
    "/me/identity-capture/phone",
    name="create-my-identity-phone-link",
    status_code=status.HTTP_201_CREATED,
)
async def create_my_identity_phone_link(
    body: IdentityChoice | None = None,
    ctx: FirmContext = Depends(portal_session),
) -> IdentityPhoneLinkOut:
    """A short link the client can open on their phone to take the identity photos.

    No sign-in on the phone: opening the link spends it, and the page it opens can send
    these photos and nothing else. The link is returned once. Asking again withdraws the
    previous one, so a QR code left on screen stops working when a new one is made. 409
    when the photos have already been sent, and when the firm has no client portal to open
    the link on.
    """
    row, state = await _phone_step(ctx, body)
    portal = await _phone_portal_url(ctx.session, ctx.tenant_id)
    if portal is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This firm has no client portal to open the link on",
        )
    minted = await idv_phone.mint(
        ctx.session,
        tenant_id=ctx.tenant_id,
        case_id=int(row["case_id"]),
        step_key=str(row["step_key"]),
        contact_id=int(row["participant_subject_id"]),
        user_id=await _phone_user(ctx),
    )
    await _clear_phone_progress(ctx, row, state)
    url = f"{portal}/i/{minted.code}"
    return IdentityPhoneLinkOut(
        url=url,
        qr_svg=idv_phone.qr_svg(url),
        expires_at=minted.expires_at.isoformat(),
    )


@router.post("/me/identity-capture/phone/cancel", name="cancel-my-identity-phone-link")
async def cancel_my_identity_phone_link(
    body: IdentityChoice | None = None,
    ctx: FirmContext = Depends(portal_session),
) -> dict[str, bool]:
    """Withdraw the phone link, so the photos are taken on this device instead.

    A link that was never made, or has already been spent, is not an error: the desktop
    is simply no longer waiting on a phone.
    """
    row, state = await _phone_step(ctx, body)
    await _clear_phone_progress(ctx, row, state)
    withdrawn = await idv_phone.withdraw(
        ctx.session,
        tenant_id=ctx.tenant_id,
        case_id=int(row["case_id"]),
        step_key=str(row["step_key"]),
        contact_id=int(row["participant_subject_id"]),
    )
    return {"withdrawn": withdrawn}


@router.get("/me/identity-documents", name="my-identity-documents")
async def my_identity_documents(
    case_uid: str | None = None,
    step_key: str | None = None,
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(portal_session),
) -> dict[str, object]:
    """The two documents a manual identity check asked this client for, and whether each has landed.

    A separate read from the choice that raised them, because the client closes the dialog and
    comes back: without it a reload showed nothing outstanding, and the only record of what was
    asked for was in a response nobody had kept. Answers `chosen: false` and an empty list while
    the digital path is still the one in play, and 404 when the caller has no identity step at all.
    """
    row = (
        (await ctx.session.execute(text(_IDENTITY_STEP_SQL), {"sk": step_key, "cu": case_uid}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No identity verification is outstanding for you",
        )
    if row["identity_choice"] is None:
        return {"chosen": False, "documents": []}
    return {
        "chosen": True,
        "documents": await idv_manual.documents_for(
            ctx.session, tenant_id=ctx.tenant_id, recorded=row["identity_documents"]
        ),
    }


@router.post("/me/identity-manual", name="my-identity-manual")
async def my_identity_manual(
    body: IdentityChoice | None = None,
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(portal_session),
) -> dict[str, object]:
    """The client chooses to have a person check their documents instead of doing it digitally.

    Whether an identity check is automatic or manual is the CLIENT's choice — they are the one who
    holds the document and knows whether they can photograph it. The seed used to pin the provider
    into the published definition, so changing it needed a re-seed, and the step was assigned to
    staff regardless of what the client would have preferred.

    What it does: records the choice on the step, points the pending ``crm.kyc_checks`` row at
    ``manual``, asks the client for the two documents a manual check is MADE of (a government-
    issued photo ID and a proof of address — see ``app/idv/manual.py``), and reassigns the step to
    STAFF so the review appears in the adviser's own work rather than sitting in a client queue
    nobody can clear. The client can upload through their request, or the adviser can file either
    item from the journey; both create the same identity evidence. The outcome is then recorded
    separately through the ordinary manual-step action. Any photo held for the digital check is
    deleted (``app/idv/held.py``).

    It does not decide the outcome, and it is not reversible from here: a client who then wants the
    digital check asks their adviser, who can retry the step. Reversing it in the portal would mean
    asking for fresh photos against a check a person may already be reviewing.
    """
    row = (
        (
            await ctx.session.execute(
                text(_IDENTITY_STEP_SQL), {"sk": _step_key_of(body), "cu": _case_uid_of(body)}
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No identity verification is outstanding for you",
        )
    if row["identity_choice"] is not None:
        # Already chosen. 409 rather than a silent second write: the first choice may already have
        # moved the step into someone's queue.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This identity check is already being handled by your adviser",
        )
    documents = await idv_manual.hand_to_adviser(
        ctx.session,
        tenant_id=ctx.tenant_id,
        execution_id=int(row["execution_id"]),
        case_id=int(row["case_id"]),
        step_key=str(row["step_key"]),
        contact_id=int(row["participant_subject_id"]),
        kyc_check_id=int(row["kyc_check_id"]) if row["kyc_check_id"] else None,
    )
    # No explicit commit: `portal_session` holds the request in `session.begin()`, which commits
    # on a clean exit — the convention every other router here follows.
    return {
        "case_uid": row["case_uid"],
        "step_key": row["step_key"],
        "provider": "manual",
        "documents": documents,
    }


# A digital identity check Graphene operations are reviewing (`app/idv/capture.py` REVIEW). The
# step stays the client's (`assignee = 'client'`), but nothing on it is theirs or the adviser's to
# do, so it is not counted as an open client step and the firm sees it with Graphene. Status
# only: what the check found is never read here (docs/aml.md §5).
_IDENTITY_REVIEW_SQL = (
    "COALESCE(cs.step_type = 'verify_identity' "
    "  AND cs.data -> 'identity_capture' ->> 'status' = 'review' "
    "  AND cs.data ->> 'identity_choice' IS NULL, false)"
)


# The staff member whose move a journey is: its assignee, or — with nobody assigned — its
# adviser. The same person the listing's "Waiting on" column names when the ball is the firm's.
# `auth.uid()` is the internal user id both columns store; the portal only knows the caller's
# WorkOS id, so "is this me" can only be answered here.
_OWNER_IS_ME_SQL = (
    "(e.assignee_user_id = auth.uid() "
    " OR (e.assignee_user_id IS NULL AND COALESCE(a.adviser_id, e.created_by) = auth.uid()))"
)


async def _require_in_book(ctx: FirmContext, principal: Principal, exec_uid: str) -> None:
    """404 unless this journey is in the caller's book.

    The same rule as the list (``IN_BOOK_SQL``). A miss is "not found", not forbidden, so a
    uid the caller should not have is not an oracle. Read-all and system return without a
    query: they see the firm.
    """
    if sees_whole_firm(principal):
        return
    # The only interpolation is ``IN_BOOK_SQL``, a module constant. No request value reaches it.
    row = (
        await ctx.session.execute(
            text(
                "SELECT 1 FROM flows.executions e "  # noqa: S608
                "LEFT JOIN crm.applications a ON a.id = e.crm_application_id "
                "WHERE e.uid = :u AND e.tenant_id = :t AND " + IN_BOOK_SQL
            ),
            {"u": exec_uid, "t": ctx.tenant_id, "see_all": False},
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found.")


@router.get("", name="list-applications")
async def list_applications(
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_book_session),
    mine: bool = False,
) -> list[dict[str, object]]:
    """The journeys the caller may see. Without ``contacts:read_all`` that is their own book: a
    journey assigned to them, one they are the adviser on, or one for a client on their team
    (``crm.client_advisers``) — the same rule that scopes the clients list. Read-all staff, and
    operators on the ``?tenant=`` path (granted it by ``_grant_book_visibility``), see the firm's.

    ``mine`` keeps only the caller's own — the dashboard's "needs you" rail. A journey is the
    caller's when it is assigned to them, or when nobody has been assigned and they are its
    adviser: an unassigned journey is realistically the adviser's. ``owner_is_me`` says the same
    per row so the listing can print "Me"."""
    require_permission(principal, CASES_READ)
    see_all = sees_whole_firm(principal)
    rows = (
        (
            await ctx.session.execute(
                text(
                    "SELECT e.uid, e.status, e.created_at, a.status AS application_status, "  # noqa: S608 - a module constant
                    "  f.name AS flow_name, f.code AS flow_code, "
                    "  COALESCE(adv.name, adv.email) AS adviser_name, "
                    "  e.assignee_user_id::text AS assignee_user_id, "
                    "  COALESCE(asn.name, asn.email) AS assignee_name, "
                    f"  COALESCE({_OWNER_IS_ME_SQL}, false) AS owner_is_me, "
                    "  (SELECT count(*) FROM flows.cases c "
                    "     WHERE c.execution_id = e.id AND c.kind = 'profile') AS clients, "
                    "  (SELECT count(*) FROM flows.cases c "
                    "     WHERE c.execution_id = e.id AND c.kind = 'account') AS accounts, "
                    "  (SELECT count(*) FROM flows.case_steps cs JOIN flows.cases c "
                    "     ON c.id = cs.case_id WHERE c.execution_id = e.id "
                    "     AND cs.status NOT IN ('skipped', 'cancelled')) AS steps_total, "
                    "  (SELECT count(*) FROM flows.case_steps cs JOIN flows.cases c "
                    "     ON c.id = cs.case_id WHERE c.execution_id = e.id "
                    "     AND cs.status = 'fulfilled') AS steps_done, "
                    # Whose ball it is, counted rather than inferred. The board used to guess this
                    # from the projected status alone, and the seeded onboarding assigns EVERY
                    # fact-find to staff — so a journey the adviser had to act on was filed under
                    # "With client" while its own card read "needs you".
                    "  (SELECT count(*) FROM flows.case_steps cs JOIN flows.cases c "
                    "     ON c.id = cs.case_id WHERE c.execution_id = e.id "
                    "     AND cs.assignee = 'client' "
                    "     AND cs.status IN ('pending', 'waiting') "
                    f"     AND NOT {_IDENTITY_REVIEW_SQL}) AS open_client_steps, "
                    "  (SELECT string_agg(DISTINCT "
                    "     COALESCE(ct.first_name || ' ' || ct.surname, ct.name), ', ') "
                    "     FROM flows.cases c JOIN crm.contacts ct ON ct.id = c.subject_id "
                    "     WHERE c.execution_id = e.id AND c.kind = 'profile') AS client_names "
                    "FROM flows.executions e "
                    "LEFT JOIN crm.applications a ON a.id = e.crm_application_id "
                    "LEFT JOIN flows.flows f ON f.id = e.flow_id "
                    "LEFT JOIN auth.users adv ON adv.id = COALESCE(a.adviser_id, e.created_by) "
                    "LEFT JOIN auth.users asn ON asn.id = e.assignee_user_id "
                    "WHERE e.tenant_id = :t "
                    # The caller's book, unless they read the whole firm. Same predicate as
                    # the detail, the writes and staff search (`journey_scope.IN_BOOK_SQL`).
                    f"AND {IN_BOOK_SQL} "
                    # `mine` is the caller's OWN journeys: assigned to them, or unassigned and
                    # theirs as the adviser.
                    f"AND (NOT :mine OR {_OWNER_IS_ME_SQL}) "
                    "ORDER BY e.created_at DESC"
                ),
                {"t": ctx.tenant_id, "mine": mine, "see_all": see_all},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


_PORTAL_STATE_RANK = {"active": 3, "invited": 2, "revoked": 1, "disabled": 0, "none": -1}


async def _portal_status_by_contact(
    session: Any, tenant_id: int, contact_ids: list[int]
) -> dict[int, dict[str, object]]:
    """Client-portal login state per contact, for the journey's profile cards — the same question
    Andy's case answered by hand: a login can exist with no ``crm.contact_users`` bridge, or a
    bridge can exist to a login that never got (or lost) client-portal access, and neither reads
    as broken from the adviser side without this. One row per (contact, login); a contact linked
    to more than one login (rare) reports its most-advanced state rather than an arbitrary one."""
    if not contact_ids:
        return {}
    rows = (
        (
            await session.execute(
                text(
                    "SELECT cu.contact_id, u.email AS login_email, u.enabled, "
                    "       m.status AS membership_status "
                    "FROM crm.contact_users cu "
                    "JOIN auth.users u ON u.id = cu.user_id "
                    "LEFT JOIN auth.organisation_memberships m "
                    "  ON m.user_id = u.id AND m.tenant_id = cu.tenant_id AND m.portal = 'client' "
                    "WHERE cu.tenant_id = :t AND cu.contact_id = ANY(:ids)"
                ),
                {"t": tenant_id, "ids": contact_ids},
            )
        )
        .mappings()
        .all()
    )
    best: dict[int, dict[str, object]] = {}
    for r in rows:
        # `membership_status` decides FIRST: `enabled` is keyed off the SAME email-verified signal
        # (grant_portal_access sets both together), so a freshly-invited, not-yet-activated login
        # is 'pending' + disabled=true by design — checking `enabled` first would report every
        # invite that hasn't been opened yet as 'disabled', which is a different and alarming claim.
        # A genuine suspension (`deactivate_member`) sets BOTH flags, landing on 'revoked' below;
        # 'disabled' is reserved for the otherwise-unreachable case a login is off despite an
        # 'active' membership having been recorded.
        if r["membership_status"] == "pending":
            state = "invited"
        elif r["membership_status"] == "inactive":
            state = "revoked"
        elif r["membership_status"] == "active" and r["enabled"]:
            state = "active"
        elif not r["enabled"]:
            state = "disabled"
        else:
            # A contact_users row to a login with no client-portal membership at all (e.g. linked
            # for another purpose) — reads the same as no login for THIS portal's purposes.
            state = "none"
        cid = int(r["contact_id"])
        current = best.get(cid)
        current_state = str(current["state"]) if current is not None else None
        if current_state is None or _PORTAL_STATE_RANK[state] > _PORTAL_STATE_RANK[current_state]:
            best[cid] = {"state": state, "email": r["login_email"]}
    return best


def _answer_reference_id(value: object) -> int | None:
    """A form select posts catalogue ids as strings; malformed/boolean values are not ids."""
    if isinstance(value, bool):
        return None
    text_value = str(value).strip()
    if not text_value.isdigit():
        return None
    parsed = int(text_value)
    return parsed if parsed > 0 else None


async def _answer_reference_names(
    session: AsyncSession, tenant_id: int, steps: Sequence[Any]
) -> tuple[dict[int, str], dict[int, str]]:
    """Resolve dynamic form-option ids without changing the answers stored as execution evidence."""
    model_ids: set[int] = set()
    fee_ids: set[int] = set()
    for step in steps:
        answers = _as_json_obj(step["answers"]) or {}
        model_id = _answer_reference_id(answers.get("model_id"))
        if model_id is not None:
            model_ids.add(model_id)
        fees = answers.get("fee_package_ids")
        if isinstance(fees, list):
            fee_ids.update(
                parsed for value in fees if (parsed := _answer_reference_id(value)) is not None
            )

    if not model_ids and not fee_ids:
        return {}, {}
    rows = (
        (
            await session.execute(
                text(
                    "SELECT 'model' AS kind, id, name FROM platform.models "
                    "WHERE tenant_id = :t AND id = ANY(CAST(:models AS int[])) "
                    "UNION ALL "
                    "SELECT 'fee', id, name FROM platform.fee_packages "
                    "WHERE tenant_id = :t AND id = ANY(CAST(:fees AS int[]))"
                ),
                {"t": tenant_id, "models": sorted(model_ids), "fees": sorted(fee_ids)},
            )
        )
        .mappings()
        .all()
    )
    return (
        {int(row["id"]): str(row["name"]) for row in rows if row["kind"] == "model"},
        {int(row["id"]): str(row["name"]) for row in rows if row["kind"] == "fee"},
    )


async def _timeline_with_actors(session: AsyncSession, rows: Sequence[Any]) -> list[dict[str, Any]]:
    """Name the person who carried each fact out.

    The event stores ``payload.actor`` as ``auth.uid()`` at the moment of the transition: the
    adviser who filled the form, or the client who accepted the terms. A uuid is not a name, and
    the journey log was printing the step's subject (whose form it was) in that place instead.
    """
    events = [dict(row) for row in rows]
    actor_ids: list[str] = []
    for event in events:
        payload = event.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
            event["payload"] = payload
        actor = payload.get("actor") if isinstance(payload, dict) else None
        if isinstance(actor, str) and len(actor) == 36:
            actor_ids.append(actor)
    if not actor_ids:
        return events
    found = (
        (
            await session.execute(
                text(
                    "SELECT id::text AS id, COALESCE(name, email) AS name "
                    "FROM auth.users WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": sorted(set(actor_ids))},
            )
        )
        .mappings()
        .all()
    )
    names = {str(row["id"]): row["name"] for row in found if row["name"]}
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        actor = payload.get("actor")
        name = names.get(actor) if isinstance(actor, str) else None
        if name:
            event["payload"] = {**payload, "actor_name": name}
    return events


@router.get("/assignees", name="list-application-assignees")
async def list_application_assignees(
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_book_session),
) -> list[dict[str, object]]:
    """Active platform members of this firm — the people a journey can be assigned to.

    Not limited to advisers. The adviser on a journey is a separate choice, made at kickoff
    and stored on the application. This list is who can own the platform-side work."""
    require_permission(principal, CASES_READ)
    rows = (
        (
            await ctx.session.execute(
                text(
                    "SELECT DISTINCT u.id::text AS user_id, "
                    "  COALESCE(u.name, u.email) AS name, u.email "
                    "FROM auth.users u "
                    "JOIN auth.organisation_memberships m "
                    "  ON m.user_id = u.id AND m.tenant_id = u.tenant_id "
                    "WHERE u.tenant_id = :t AND u.deleted_at IS NULL "
                    "  AND m.portal = 'platform' AND m.status = 'active' "
                    "ORDER BY 2"
                ),
                {"t": ctx.tenant_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


# The lock on amending a completed onboarding form: the declaration step has been sent.
# `crm.applications.adviser_declaration` is NOT that fact. Every application created before
# mid-September 2026 had the boolean written true at kickoff — the same instant as `created_at`,
# with no `adviser_declaration_by` — so reading it hid Amend on journeys whose "Declare and send"
# step is still waiting. The executions alias in each query below is `e`.
_DECLARATION_SENT_SQL = (
    "EXISTS (SELECT 1 FROM flows.cases dc "
    "JOIN flows.case_steps ds ON ds.case_id = dc.id "
    "WHERE dc.execution_id = e.id AND ds.step_key = 'declaration' "
    "AND ds.status = 'fulfilled')"
)


async def _annotate_setup_runs(
    session: AsyncSession, steps_by_case: dict[int, list[dict[str, object]]]
) -> None:
    """Say whether a waiting account opening is held, and whether this caller can release it.

    The step only stores the run uid. Without the run's status the journey keeps offering
    Record hand-off, and each click settles the same held plan and toasts success.
    """
    uids = sorted(
        {
            str(row["action_run_uid"])
            for rows in steps_by_case.values()
            for row in rows
            if row.get("action_run_uid")
        }
    )
    if not uids:
        return
    caller = (
        (
            await session.execute(
                text("SELECT auth.jwt() ->> 'user_uuid' AS user_uuid, auth.role_ids() AS role_ids")
            )
        )
        .mappings()
        .one()
    )
    raw_roles = caller["role_ids"] or []
    role_ids = {int(role) for role in raw_roles}
    caller_id = caller["user_uuid"]
    runs = (
        (
            await session.execute(
                text(
                    "SELECT r.uid, "
                    "       CASE WHEN r.state_type = 'paused' THEN 'awaiting_approval' "
                    "            WHEN r.state_type = 'completed' THEN 'succeeded' "
                    "            WHEN r.state_type = 'failed' AND r.state_name = 'NeedsAttention' "
                    "                 THEN 'needs_attention' "
                    "            WHEN r.state_type = 'failed' THEN 'failed' "
                    "            WHEN r.state_type = 'cancelled' THEN 'cancelled' "
                    "            ELSE 'running' END AS status, "
                    "       r.requested_by::text AS requested_by, "
                    "       r.approver_role_ids[1] AS approval_role_id, "
                    "       COALESCE(r.approver_role_ids, ARRAY[]::int[]) AS approver_role_ids "
                    "FROM baton.runs r "
                    "WHERE r.uid = ANY(CAST(:uids AS text[]))"
                ),
                {"uids": uids},
            )
        )
        .mappings()
        .all()
    )
    by_uid = {str(run["uid"]): run for run in runs}
    for rows in steps_by_case.values():
        for row in rows:
            uid = row.get("action_run_uid")
            if not uid:
                continue
            run = by_uid.get(str(uid))
            if run is None:
                continue
            roles = [int(role) for role in (run["approver_role_ids"] or [])]
            if not roles and run["approval_role_id"] is not None:
                roles = [int(run["approval_role_id"])]
            requested = run["requested_by"]
            row["action_run_status"] = run["status"]
            row["action_can_decide"] = bool(
                run["status"] == "awaiting_approval"
                and caller_id
                and requested != caller_id
                and (not roles or any(role in role_ids for role in roles))
            )


@router.get("/{exec_uid}", name="get-application")
async def get_application(
    exec_uid: str,
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_book_session),
) -> dict[str, object]:
    """The execution + its tiered cases + steps + the semantic event timeline."""
    require_permission(principal, CASES_READ)
    await _require_in_book(ctx, principal, exec_uid)
    execu = (
        (
            await ctx.session.execute(
                text(
                    "SELECT e.id, e.uid, e.status, e.created_at, e.crm_application_id AS app_id, "  # noqa: S608
                    "       e.assignee_user_id::text AS assignee_user_id, "
                    "       a.status AS application_status, a.custody, f.name AS flow_name, "
                    "       f.code AS flow_code, "
                    f"       {_DECLARATION_SENT_SQL} AS adviser_declared, "
                    "       a.provider, a.adviser_id::text AS adviser_id, "
                    "       COALESCE(adv.name, adv.email) AS adviser_name, "
                    "       COALESCE(asn.name, asn.email) AS assignee_name, "
                    "       v.definition "
                    "FROM flows.executions e "
                    "LEFT JOIN crm.applications a ON a.id = e.crm_application_id "
                    "LEFT JOIN flows.flows f ON f.id = e.flow_id "
                    "LEFT JOIN flows.flow_versions v ON v.id = e.flow_version_id "
                    "LEFT JOIN auth.users adv ON adv.id = a.adviser_id "
                    "LEFT JOIN auth.users asn ON asn.id = e.assignee_user_id "
                    "WHERE e.uid = :u AND e.tenant_id = :t"
                ),
                {"u": exec_uid, "t": ctx.tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if execu is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found.")
    # A fork can choose a step the plan left out (a transfer recorded on Account details).
    # Create it before this read, or the page shows no further step while opening the account
    # refuses the term.
    await orchestrator.catch_up_omitted_steps(
        ctx.session, execution_id=int(execu["id"]), tenant_id=ctx.tenant_id
    )
    cases = (
        (
            await ctx.session.execute(
                text(
                    # noqa on the next line: the only interpolation is `phone_expr`, a literal built
                    # in app.contacts from a column name of ours -- no request value reaches it.
                    "SELECT c.id, c.uid, c.kind, c.subject_type, c.subject_id, c.status, "  # noqa: S608
                    # The subject's OWN uid, beside its serial: `subject_id` is what the roles and
                    # portal-status maps key on here, and the uid is what a path takes -- the portal
                    # invites a client with `POST /firm/contacts/{contact_uid}/portal-invite`.
                    "       ct.uid AS subject_uid, "
                    "       COALESCE(ct.first_name || ' ' || ct.surname, ct.name) AS subject_name, "
                    "       ct.type AS contact_type, ct.email AS contact_email, "
                    # The applicant card states who this person is: their date of birth (which is
                    # also what marks a child) and the number staff ring.
                    "       ct.dob, "
                    "       " + phone_expr("ct.mobile") + " AS contact_phone, "
                    "       acct.uid AS account_ref, "
                    "       acct.currency AS currency, "
                    "       (acct.transfer IS NOT NULL) AS has_transfer, "
                    "       acct.transfer AS transfer, "
                    "       acct.data -> 'jisa_child' AS jisa_child, "
                    "       acct.data -> 'regular_contribution' AS regular_contribution, "
                    "       acct.investment_capacity, "
                    "       acct.dividend_treatment, "
                    "       COALESCE((SELECT jsonb_agg(jsonb_build_object("
                    "           'name', COALESCE(ap.first_name || ' ' || ap.surname, ap.name), "
                    "           'role', ac.role::text"
                    "         ) ORDER BY ac.role::text, ap.name) "
                    "         FROM crm.account_contacts ac "
                    "         JOIN crm.contacts ap ON ap.id = ac.contact_id "
                    "        WHERE ac.account_id = acct.id AND ac.application_id = :a "
                    "          AND ac.tenant_id = acct.tenant_id), "
                    "       '[]'::jsonb) AS account_parties, "
                    "       p.name AS product_name, "
                    "       p.product_type::text AS product_type, "
                    "       m.name AS model_name, "
                    "       COALESCE((SELECT jsonb_agg(fp.name ORDER BY afp.sort_order, fp.name) "
                    "         FROM platform.account_fee_packages afp "
                    "         JOIN platform.fee_packages fp ON fp.id = afp.fee_package_id "
                    "          AND fp.tenant_id = afp.tenant_id "
                    "        WHERE afp.account_id = acct.id), '[]'::jsonb) AS fee_package_names "
                    "FROM flows.cases c "
                    "LEFT JOIN crm.contacts ct "
                    "  ON ct.id = c.subject_id AND c.subject_type = 'contact' "
                    "LEFT JOIN platform.accounts acct "
                    "  ON acct.id = c.subject_id AND c.subject_type = 'account' "
                    "LEFT JOIN platform.products p ON p.id = acct.product_id "
                    "LEFT JOIN platform.models m ON m.id = acct.model_id "
                    "WHERE c.execution_id = :e ORDER BY c.kind, c.subject_id"
                ),
                {"e": execu["id"], "a": execu["app_id"]},
            )
        )
        .mappings()
        .all()
    )
    # Roles per participant contact (across the application's accounts) — for the profile cards.
    role_rows = (
        (
            await ctx.session.execute(
                text(
                    "SELECT contact_id, array_agg(DISTINCT role::text) AS roles "
                    "FROM crm.account_contacts WHERE application_id = :a GROUP BY contact_id"
                ),
                {"a": execu["app_id"]},
            )
        )
        .mappings()
        .all()
    )
    roles_by_contact = {int(r["contact_id"]): list(r["roles"]) for r in role_rows}
    portal_by_contact = await _portal_status_by_contact(
        ctx.session,
        ctx.tenant_id,
        [int(c["subject_id"]) for c in cases if c["subject_id"] is not None],
    )
    steps = (
        (
            await ctx.session.execute(
                text(
                    "SELECT cs.case_id, cs.step_key, cs.step_type, cs.title, cs.assignee, "  # noqa: S608 - a module constant
                    "       cs.status, cs.participant_subject_id, cs.request_item_id, "
                    "       cs.data -> 'awaiting' AS awaiting, "
                    "       cs.data ->> 'last_event' AS last_event, "
                    "       cs.data ->> 'action_run_uid' AS action_run_uid, "
                    "       cs.data ->> 'note' AS note, "
                    "       cs.data -> 'local_activated' AS local_activated, "
                    "       cs.data ->> 'custodian_open' AS custodian_open, "
                    "       cs.data ->> 'document_id' AS document_id, "
                    "       cs.data ->> 'identity_choice' AS identity_choice, "
                    "       cs.data -> 'identity_documents' AS identity_documents_recorded, "
                    f"       {_IDENTITY_REVIEW_SQL} AS identity_review, "
                    "       cs.data -> 'identity_capture' -> 'last_provider_error' "
                    "         AS identity_provider_error, "
                    # Whose instance this is. A fanned step inside an ACCOUNT case (joint terms) is
                    # otherwise unattributable in the UI: two owners produce two identical rows, and
                    # the adviser cannot tell which one they are about to action.
                    "       COALESCE(pc.first_name || ' ' || pc.surname, pc.name) "
                    "         AS participant_name, "
                    "       ri.uid AS request_item_uid, r.uid AS request_uid, "
                    "       (SELECT fr.answers FROM documents.form_responses fr "
                    "          WHERE fr.request_item_id = cs.request_item_id "
                    "          ORDER BY fr.id DESC LIMIT 1) AS answers, "
                    "       doc.uid AS document_uid, doc.name AS document_name, "
                    "       doc.status::text AS document_status "
                    "FROM flows.case_steps cs "
                    "LEFT JOIN crm.contacts pc ON pc.id = cs.participant_subject_id "
                    "LEFT JOIN documents.request_items ri ON ri.id = cs.request_item_id "
                    "LEFT JOIN documents.requests r ON r.id = ri.request_id "
                    "LEFT JOIN documents.documents doc "
                    "  ON doc.id = NULLIF(cs.data ->> 'document_id', '')::uuid "
                    "WHERE cs.case_id = ANY(:ids) ORDER BY cs.case_id, cs.id"
                ),
                {"ids": [int(c["id"]) for c in cases]},
            )
        )
        .mappings()
        .all()
    )
    model_names, fee_names = await _answer_reference_names(ctx.session, ctx.tenant_id, steps)
    # WHERE each step sits in the journey, so a surface can show the work in the order it happens.
    # The step rows cannot answer this: they are grouped into cases (one per person, one per
    # account), and a person's own steps now sit at BOTH ends of the spine — their fact-find first,
    # their identity and AML after the client's account-tier terms. Ordering by case, or by row id
    # within a case, therefore cannot produce the run order however it is sorted.
    #
    # `case_steps.sort_order` exists and is NOT the answer: nothing sets it, so it is 0 on every
    # row.
    # The order lives in the definition, which is the only place it has ever lived.
    order_of: dict[str, int] = {}
    mode_of: dict[str, str] = {}
    raw_definition = execu["definition"]
    if isinstance(raw_definition, str):
        raw_definition = json.loads(raw_definition)
    if isinstance(raw_definition, dict):
        for i, spec in enumerate(raw_definition.get("steps") or []):
            key = spec.get("id") if isinstance(spec, dict) else None
            if isinstance(key, str) and key not in order_of:
                order_of[key] = i
            config = spec.get("config") if isinstance(spec, dict) else None
            mode = config.get("mode") if isinstance(config, dict) else None
            if isinstance(key, str) and isinstance(mode, str):
                mode_of[key] = mode

    # Why ComplyCube last refused a client's identity upload is for Graphene operations only: an
    # operator in the firm's workspace (`tenant_portal:read`, an operations-only grant) sees it, the
    # firm's own staff never do.
    operator = holds_workspace_permission(principal, TENANT_PORTAL_READ)
    steps_by_case: dict[int, list[dict[str, object]]] = {}
    for stp in steps:
        row = dict(stp)
        provider_error = _as_json_obj(row.pop("identity_provider_error", None))
        if operator and provider_error:
            row["identity_provider_error"] = {
                "message": provider_error.get("message"),
                "status_code": provider_error.get("status_code"),
                "at": provider_error.get("at"),
            }
        answers = _as_json_obj(row.get("answers")) or {}
        answer_labels: dict[str, object] = {}
        model_id = _answer_reference_id(answers.get("model_id"))
        if model_id is not None and model_id in model_names:
            answer_labels["model_id"] = model_names[model_id]
        fees = answers.get("fee_package_ids")
        if isinstance(fees, list):
            labelled_fees: list[str] = []
            for value in fees:
                if value is None or str(value).strip() == "":
                    continue
                parsed = _answer_reference_id(value)
                labelled_fees.append(
                    fee_names.get(parsed, str(value)) if parsed is not None else str(value)
                )
            if labelled_fees:
                answer_labels["fee_package_ids"] = labelled_fees
        row["answer_labels"] = answer_labels
        row["identity_documents"] = await idv_manual.documents_for(
            ctx.session,
            tenant_id=ctx.tenant_id,
            recorded=row.pop("identity_documents_recorded", None),
        )
        # A step the definition no longer names (an in-flight case whose flow was re-published)
        # sorts to the END rather than to the front, where a 0 would put it.
        row["order"] = order_of.get(str(row["step_key"]), len(order_of))
        row["mode"] = mode_of.get(str(row["step_key"]))
        steps_by_case.setdefault(int(stp["case_id"]), []).append(row)
    await _annotate_setup_runs(ctx.session, steps_by_case)
    timeline = (
        (
            await ctx.session.execute(
                text(
                    "SELECT id, event_type, payload, created_at FROM flows.domain_events "
                    "WHERE execution_id = :e ORDER BY id DESC LIMIT 50"
                ),
                {"e": execu["id"]},
            )
        )
        .mappings()
        .all()
    )
    named_timeline = await _timeline_with_actors(ctx.session, timeline)
    return {
        "uid": execu["uid"],
        "status": execu["status"],
        "application_status": execu["application_status"],
        "flow_name": execu["flow_name"],
        "flow_code": execu["flow_code"],
        # Completed staff forms stay reopenable until Declare and send, and only on the onboarding
        # flow — the same rule `submit_step_form` enforces. The journey page reads this rather
        # than re-deriving it, so the Amend control and the refusal cannot drift.
        "forms_amendable": execu["flow_code"] == "onboarding"
        and not bool(execu["adviser_declared"]),
        "custody": execu["custody"],
        "created_at": execu["created_at"],
        "adviser_id": execu["adviser_id"],
        "adviser_name": execu["adviser_name"],
        "assignee_user_id": execu["assignee_user_id"],
        "assignee_name": execu["assignee_name"],
        "cases": [
            {
                **{
                    k: c[k]
                    for k in (
                        "uid",
                        "kind",
                        "subject_type",
                        "subject_id",
                        "subject_uid",
                        "subject_name",
                        "status",
                    )
                },
                "contact_type": c["contact_type"],
                "contact_email": c["contact_email"] if c["subject_type"] == "contact" else None,
                "contact_phone": c["contact_phone"] if c["subject_type"] == "contact" else None,
                "dob": c["dob"] if c["subject_type"] == "contact" else None,
                "roles": roles_by_contact.get(int(c["subject_id"]), [])
                if c["subject_id"] is not None
                else [],
                "portal_status": (
                    portal_by_contact.get(int(c["subject_id"]), {"state": "none"})["state"]
                    if c["subject_type"] == "contact" and c["subject_id"] is not None
                    else None
                ),
                "portal_login_email": (
                    portal_by_contact.get(int(c["subject_id"]), {}).get("email")
                    if c["subject_type"] == "contact" and c["subject_id"] is not None
                    else None
                ),
                "account_ref": c["account_ref"],
                # Named from the registry, not from a joined table (0138): `providers.meta()` is
                # the one place a custodian's display name lives, so a journey card and an
                # account record cannot render it differently.
                "platform_name": (
                    providers.meta(str(execu["provider"]))["name"]
                    if c["kind"] == "account" and execu["provider"]
                    else None
                ),
                # Kickoff catalogue — what the adviser captured when raising the journey.
                # Profile / application cases leave these null so the shape stays uniform.
                "product_name": c["product_name"] if c["kind"] == "account" else None,
                "product_type": c["product_type"] if c["kind"] == "account" else None,
                "model_name": c["model_name"] if c["kind"] == "account" else None,
                "fee_package_names": c["fee_package_names"] if c["kind"] == "account" else [],
                "currency": c["currency"] if c["kind"] == "account" else None,
                "has_transfer": (bool(c["has_transfer"]) if c["kind"] == "account" else None),
                "transfer": (_as_json_obj(c["transfer"]) if c["kind"] == "account" else None),
                "jisa_child": (_as_json_obj(c["jisa_child"]) if c["kind"] == "account" else None),
                "regular_contribution": (
                    _regular_contribution_out(
                        {"regular_contribution": _as_json_obj(c["regular_contribution"])}
                    )
                    if c["kind"] == "account"
                    else None
                ),
                "investment_capacity": (
                    c["investment_capacity"] if c["kind"] == "account" else None
                ),
                "dividend_treatment": (c["dividend_treatment"] if c["kind"] == "account" else None),
                "account_parties": c["account_parties"] if c["kind"] == "account" else [],
                "steps": steps_by_case.get(int(c["id"]), []),
            }
            for c in cases
        ],
        "timeline": named_timeline,
    }


class TakeIdentityManual(BaseModel):
    """The identity step the adviser is taking onto documents."""

    case_uid: str
    step_key: str
    participant_contact_id: int


@router.post("/{exec_uid}/identity-manual", name="take-identity-manual")
async def take_identity_manual(
    exec_uid: str,
    body: TakeIdentityManual,
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """The adviser files this person's identity from documents instead of waiting for photos.

    The same write the client makes when they ask for a person to check their documents: the step
    moves to staff, the two evidence items are raised, and the journey's upload controls appear.
    A check Graphene is already reviewing is left where it is.
    """
    require_permission(principal, CASES_WRITE)
    row = (
        (
            await ctx.session.execute(
                text(
                    "SELECT cs.case_id, cs.step_key, cs.status, cs.participant_subject_id, "
                    "       c.execution_id, "
                    "       cs.data ->> 'kyc_check_id' AS kyc_check_id, "
                    "       cs.data ->> 'identity_choice' AS identity_choice, "
                    "       cs.data -> 'identity_documents' AS identity_documents, "
                    "       cs.data -> 'identity_capture' ->> 'status' AS capture_status "
                    "FROM flows.executions e "
                    "JOIN flows.cases c ON c.execution_id = e.id AND c.uid = :cu "
                    "JOIN flows.case_steps cs ON cs.case_id = c.id AND cs.step_key = :sk "
                    "  AND cs.participant_subject_id = :pc "
                    "WHERE e.uid = :eu AND e.tenant_id = :t AND cs.step_type = 'verify_identity'"
                ),
                {
                    "eu": exec_uid,
                    "cu": body.case_uid,
                    "sk": body.step_key,
                    "pc": body.participant_contact_id,
                    "t": ctx.tenant_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Identity step not found")
    if row["status"] not in ("pending", "waiting"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This identity check is no longer open",
        )
    recorded = row["identity_documents"]
    if isinstance(recorded, str):
        recorded = json.loads(recorded)
    if row["identity_choice"] is not None or recorded:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This identity check is already being handled from documents",
        )
    if row["capture_status"] == "review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Graphene is reviewing this identity check",
        )
    documents = await idv_manual.hand_to_adviser(
        ctx.session,
        tenant_id=ctx.tenant_id,
        execution_id=int(row["execution_id"]),
        case_id=int(row["case_id"]),
        step_key=str(row["step_key"]),
        contact_id=int(row["participant_subject_id"]),
        kyc_check_id=int(row["kyc_check_id"]) if row["kyc_check_id"] else None,
    )
    return {
        "case_uid": body.case_uid,
        "step_key": body.step_key,
        "provider": "manual",
        "documents": documents,
    }


async def fulfil_identity_document(
    exec_uid: str,
    item_uid: str,
    case_uid: str = Form(...),
    step_key: str = Form(...),
    participant_contact_id: int = Form(...),
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Upload one piece of manual-IDV evidence on the client's behalf.

    The evidence item must be one this exact participant's identity step raised. Filing the
    SECOND of the two settles the identity step (``orchestrator._settle_identity_from_evidence``);
    there is no separate Pass / Refer.
    """
    require_permission(principal, CASES_WRITE)
    await _require_in_book(ctx, principal, exec_uid)
    row = (
        (
            await ctx.session.execute(
                text(
                    "SELECT ri.id, ri.request_id, ri.tenant_id, ri.action, ri.title, ri.category, "
                    "       r.contact_id, cs.data -> 'identity_documents' AS recorded "
                    "FROM flows.executions e "
                    "JOIN flows.cases c ON c.execution_id = e.id AND c.uid = :cu "
                    "JOIN flows.case_steps cs ON cs.case_id = c.id AND cs.step_key = :sk "
                    "  AND cs.participant_subject_id = :pc "
                    "JOIN documents.request_items ri "
                    "  ON ri.uid = :iu AND ri.tenant_id = e.tenant_id "
                    "JOIN documents.requests r ON r.id = ri.request_id "
                    "WHERE e.uid = :eu AND e.tenant_id = :t AND cs.step_type = 'verify_identity' "
                    "  AND r.contact_id = :pc AND ri.status = 'pending'"
                ),
                {
                    "eu": exec_uid,
                    "cu": case_uid,
                    "sk": step_key,
                    "pc": participant_contact_id,
                    "iu": item_uid,
                    "t": ctx.tenant_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence item not found")
    recorded = row["recorded"]
    if isinstance(recorded, str):
        recorded = json.loads(recorded)
    recorded_uids = (
        list(recorded.values())
        if isinstance(recorded, dict)
        else list(recorded)
        if isinstance(recorded, list)
        else []
    )
    if item_uid not in {str(uid) for uid in recorded_uids}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence item not found")
    doc_id, document_uid = await documents_router.store_request_upload(
        ctx.session, item=row, item_uid=item_uid, file=file
    )
    await ctx.session.execute(
        text(
            "UPDATE documents.request_items SET status = 'fulfilled', "
            " fulfilled_document_id = :doc, fulfilled_at = now() WHERE id = :id"
        ),
        {"doc": doc_id, "id": row["id"]},
    )
    await orchestrator.on_item_fulfilled(ctx.session, int(row["id"]))
    await ctx.session.execute(
        text(
            "UPDATE documents.requests SET status = 'fulfilled', completed_at = now() "
            "WHERE id = :rid AND NOT EXISTS (SELECT 1 FROM documents.request_items "
            " WHERE request_id = :rid AND status = 'pending')"
        ),
        {"rid": row["request_id"]},
    )
    return {"ok": True, "document_uid": document_uid}


router.add_api_route(
    "/{exec_uid}/identity-documents/{item_uid}/fulfill",
    fulfil_identity_document,
    methods=["POST"],
    name="fulfil-identity-document",
    status_code=status.HTTP_201_CREATED,
    route_class_override=documents_router.SingleUploadRoute,
)


class ActionBody(BaseModel):
    case_uid: str
    step_key: str
    participant_contact_id: int | None = None
    outcome: str = "passed"  # for review steps; ignored by task/setup_account
    # A compliance decision's recorded rationale (the decider is stamped too) — kept on the
    # step's data and on the domain event, so a pass/refer is never a bare flag.
    note: str | None = None
    # Required by the seeded invitation handoff. A generic task ignores it.
    adviser_confirmed: bool = False


async def _case_id(ctx: FirmContext, exec_uid: str, case_uid: str) -> int:
    case = (
        await ctx.session.execute(
            text(
                "SELECT c.id FROM flows.cases c JOIN flows.executions e ON e.id = c.execution_id "
                "WHERE c.uid = :cu AND e.uid = :eu AND c.tenant_id = :t"
            ),
            {"cu": case_uid, "eu": exec_uid, "t": ctx.tenant_id},
        )
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found.")
    return int(case)


async def _execution_id(ctx: FirmContext, exec_uid: str) -> int:
    row = (
        await ctx.session.execute(
            text("SELECT id FROM flows.executions WHERE uid = :u AND tenant_id = :t"),
            {"u": exec_uid, "t": ctx.tenant_id},
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found.")
    return int(row)


async def _require_participant_for_fanned(
    ctx: FirmContext, case_id: int, step_key: str, participant_contact_id: int | None
) -> None:
    """422 unless a per-participant (fanned) step names the participant being acted on.

    Without it every write against that step key lands on EVERY participant's instance at once —
    one adviser click marking both owners' identity checks passed.
    """
    fanned = (
        await ctx.session.execute(
            text(
                "SELECT count(*) FROM flows.case_steps "
                "WHERE case_id = :c AND step_key = :k AND participant_subject_id IS NOT NULL"
            ),
            {"c": case_id, "k": step_key},
        )
    ).scalar_one()
    if int(fanned) > 0 and participant_contact_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This step runs once per person — say which participant you are actioning.",
        )


@router.post("/{exec_uid}/action", name="action-application-step")
async def action_step(
    exec_uid: str,
    body: ActionBody,
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Staff action a MANUAL step (identity/AML review outcome, a compliance review, a task, opening
    the account). A fanned step REQUIRES its participant: omitted, the write would land on every
    participant instance of that step key at once.

    It cannot complete a step whose completion IS an artifact — a form, an acknowledgement, a file.
    That is not a check written here: ``orchestrator.submit_step`` refuses any fulfilment claim the
    step's evidence does not support (422), so this route and the client's own fulfilment endpoints
    inherit the same invariant instead of one of them being the only door with a lock on it.
    """
    require_permission(principal, CASES_WRITE)
    await _require_in_book(ctx, principal, exec_uid)
    case_id = await _case_id(ctx, exec_uid, body.case_uid)
    await _require_participant_for_fanned(ctx, case_id, body.step_key, body.participant_contact_id)
    detail: dict[str, Any] = {
        "outcome": body.outcome,
        "adviser_confirmed": body.adviser_confirmed,
    }
    if body.note and body.note.strip():
        detail["note"] = body.note.strip()
    event = await orchestrator.submit_step(
        ctx.session,
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        step_key=body.step_key,
        detail=detail,
        participant_subject_id=body.participant_contact_id,
    )
    out: dict[str, object] = {"ok": True}
    # setup_account (and any future hand-off) may stamp these on the event — surface them so the
    # staff UI knows local activation succeeded while custodian open is still pending ops.
    if event is not None:
        for key in ("local_activated", "custodian_open"):
            if key in event.detail:
                out[key] = event.detail[key]
    return out


class RetryBody(BaseModel):
    case_uid: str
    step_key: str
    participant_contact_id: int | None = None


@router.post("/{exec_uid}/retry", name="retry-application-step")
async def retry_step(
    exec_uid: str,
    body: RetryBody,
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Re-run a failed step's kick-off — a render that errored, a check that never started. Without
    this a failed required step pins the case with no way out but SQL."""
    require_permission(principal, CASES_WRITE)
    await _require_in_book(ctx, principal, exec_uid)
    case_id = await _case_id(ctx, exec_uid, body.case_uid)
    await _require_participant_for_fanned(ctx, case_id, body.step_key, body.participant_contact_id)
    ok = await orchestrator.retry_step(
        ctx.session,
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        step_key=body.step_key,
        participant_subject_id=body.participant_contact_id,
    )
    if not ok:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "That step is not in a state that can be retried.",
        )
    return {"ok": True}


class CancelBody(BaseModel):
    reason: str | None = None


@router.post("/{exec_uid}/cancel", name="cancel-application")
async def cancel_application(
    exec_uid: str,
    body: CancelBody,
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Abandon a journey started in error: outstanding steps are compensated and cancelled, the
    client's outstanding requests withdrawn, the journey's open tasks cancelled.

    This is also the ONLY thing that releases ``cases_one_active_per_subject`` — without it a
    mistaken journey locks that client out of ever being re-onboarded on this flow."""
    require_permission(principal, CASES_WRITE)
    await _require_in_book(ctx, principal, exec_uid)
    execution_id = await _execution_id(ctx, exec_uid)
    cancelled = await orchestrator.cancel_execution(
        ctx.session,
        tenant_id=ctx.tenant_id,
        execution_id=execution_id,
        reason=(body.reason or "").strip() or None,
        cancelled_by=await _actor_uuid(ctx.session),
    )
    if not cancelled:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This journey has already finished or been stopped.",
        )
    return {"ok": True}


class AssignBody(BaseModel):
    #: Platform user uuid. Empty, omitted, or ``"none"`` clears the assignee.
    user_id: str | None = None


def _assignee_uuid(raw: str | None) -> str | None:
    text_id = (raw or "").strip()
    if not text_id or text_id == "none":
        return None
    try:
        return str(uuid.UUID(text_id))
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Choose a member of this firm's platform staff.",
        ) from exc


@router.post("/{exec_uid}/assignee", name="assign-application")
async def assign_application(
    exec_uid: str,
    body: AssignBody,
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Point this journey's platform-side work at a staff member, or clear that choice.

    Any active platform member of the firm — not only an adviser. Open staff tasks already
    raised against the journey move with them. A task for the client stays with the client.
    Clearing the assignee leaves existing tasks where they are; later staff tasks fall back
    to whoever the step was authored to raise them for."""
    require_permission(principal, CASES_WRITE)
    await _require_in_book(ctx, principal, exec_uid)
    user_id = _assignee_uuid(body.user_id)
    await _execution_id(ctx, exec_uid)
    if user_id is not None:
        member = (
            await ctx.session.execute(
                text(
                    "SELECT 1 FROM auth.organisation_memberships "
                    "WHERE user_id = CAST(:u AS uuid) AND tenant_id = :t "
                    "  AND portal = 'platform' AND status = 'active'"
                ),
                {"u": user_id, "t": ctx.tenant_id},
            )
        ).scalar_one_or_none()
        if member is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Choose a member of this firm's platform staff.",
            )
    await ctx.session.execute(
        text(
            "UPDATE flows.executions "
            "SET assignee_user_id = CAST(:u AS uuid), updated_at = now() "
            "WHERE uid = :e AND tenant_id = :t"
        ),
        {"u": user_id, "e": exec_uid, "t": ctx.tenant_id},
    )
    moved = 0
    if user_id is not None:
        open_staff = (
            await ctx.session.execute(
                text(
                    "SELECT uid FROM tasks.tasks "
                    "WHERE tenant_id = :t AND subject_type = 'execution' AND subject_id = :e "
                    "  AND status IN ('open', 'in_progress') "
                    "  AND assignee_kind IN ('user', 'role') "
                    "  AND assignee_user_id IS DISTINCT FROM CAST(:u AS uuid)"
                ),
                {"t": ctx.tenant_id, "e": exec_uid, "u": user_id},
            )
        ).scalars()
        for task_uid in open_staff:
            await tasks_service.reassign_task(
                ctx.session,
                str(task_uid),
                tenant_id=ctx.tenant_id,
                assignee_kind="user",
                assignee_user_id=user_id,
            )
            moved += 1
    return {"ok": True, "assignee_user_id": user_id, "tasks_moved": moved}


# ============================================================================
# Staff fulfilment of a staff-assigned step
#
# A flow may assign a `fill_form` to STAFF (the seeded onboarding does exactly this for six
# fact-find steps): the adviser or paraplanner completes it on the client's behalf. The client
# fulfilment path runs through the document-request endpoints, which authorise on
# ``auth.contact_scope()`` — a staff member has none, so they cannot use it. These two endpoints are
# the staff-side equivalent: the same request item, the same form, the same
# ``on_item_fulfilled`` → handler → mapping → recompute loop, authorised on ``cases:write`` and
# forced to the caller's tenant.
# ============================================================================
class StepFormField(BaseModel):
    uid: str
    field_key: str
    field_type: str
    label: str
    section: str | None
    description: str | None
    required: bool
    conditions: Any | None
    validation: Any | None
    options: Any | None
    sort_order: int


class StepFormOut(BaseModel):
    step_key: str
    title: str | None
    status: str
    form_uid: str
    name: str
    description: str | None
    fields: list[StepFormField]
    editable: bool = False
    # What was submitted already (a `review` mode step, or a re-open) — keyed by field_key.
    answers: dict[str, Any] | None = None
    context: dict[str, Any] = Field(default_factory=dict)


def _positive_number(value: object) -> bool:
    if not isinstance(value, (str, int, float, Decimal)):
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _form_choice(stored: object, options: Sequence[str]) -> str | None:
    """CRM value → the option the fact-find field stores.

    The marital and employment fields store the book slug (``single``, ``self_employed``).
    An older fact-find stored the display label (``Single``, ``Self-Employed``); ``snake``
    folds that label onto the slug so a prefill still lands on an option the field offers.
    """
    if stored is None or stored == "":
        return None
    wanted = TRANSFORMS["snake"](str(stored))
    for option in options:
        if TRANSFORMS["snake"](option) == wanted:
            return option
    return None


def _nested_answer(data: dict[str, Any], key: str) -> Any:
    """A dotted form key, stored flat or nested the way the contact mapper writes ``data``."""
    if key in data:
        return data[key]
    current: Any = data
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _illustration_contact_gaps(
    inputs: dict[str, Any] | None, filled: dict[str, Any]
) -> dict[str, Any]:
    """Facts a linked illustration holds, only where the form does not already have them.

    The illustration request stores pension facts (``exit_age``, ``marital_status``) and, when a
    caller has put them on the stored inputs, bank details and source of wealth. A CRM value
    already in ``filled`` wins.
    """
    if not inputs:
        return {}

    def blank(key: str) -> bool:
        current = filled.get(key)
        return current is None or current == "" or current == [] or current == {}

    gaps: dict[str, Any] = {}
    age = inputs.get("exit_age", inputs.get("retirement_age"))
    if age is not None and blank("pension.retirementAge"):
        gaps["pension.retirementAge"] = age
    marital = inputs.get("marital_status")
    if marital not in (None, "") and blank("pension.maritalStatus"):
        gaps["pension.maritalStatus"] = marital
    employment = inputs.get("employment_status")
    if employment not in (None, "") and blank("pension.employmentStatus"):
        gaps["pension.employmentStatus"] = employment
    wealth = inputs.get("source_of_wealth")
    if wealth not in (None, "") and blank("source_of_wealth"):
        gaps["source_of_wealth"] = wealth
    bank = inputs.get("bank_account")
    if isinstance(bank, dict):
        mapping = {
            "bank_account.account_name": bank.get("account_name") or bank.get("name"),
            "bank_account.account_number": bank.get("account_number"),
            "bank_account.sort_code": bank.get("sort_code"),
            "bank_account.iban": bank.get("iban"),
            "bank_account.bic": bank.get("bic"),
        }
        for key, value in mapping.items():
            if value not in (None, "", [], {}) and blank(key):
                gaps[key] = value
    return gaps


def _employer_form_prefill(row: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Employer facts from the client record, keyed the way the employer form asks them."""
    values: dict[str, Any] = {}
    name = row.get("employer_name")
    number = row.get("employer_registration_number")
    address = row.get("employer_address")
    if isinstance(address, str):
        address = _as_json_obj(address)
    if name not in (None, ""):
        values["pension.employerContributions.name"] = name
    if number not in (None, ""):
        values["pension.employerContributions.registrationNumber"] = number
    if isinstance(address, dict) and any(address.values()):
        values["employer_address"] = address
    for key in (
        "pension.employerContributions.accountNumber",
        "pension.employerContributions.sortCode",
    ):
        found = _nested_answer(data, key)
        if found not in (None, "", [], {}):
            values[key] = found
    contributes = row.get("employer_contributions")
    if values or contributes is True:
        values["pension.hasEmployerContributions"] = True
    return values


def _illustration_account_prefill(inputs: dict[str, Any] | None) -> dict[str, Any]:
    """Translate only assumptions with a semantically equivalent onboarding question."""
    if not inputs:
        return {}

    def frequency(value: object, *, withdrawal: bool = False) -> str | None:
        normalised = str(value or "").strip().lower().replace("-", " ").replace(" ", "_")
        allowed = {"monthly", "quarterly", "half_yearly", "annually"}
        if not withdrawal:
            allowed.add("single")
        return normalised if normalised in allowed else None

    values: dict[str, Any] = {}
    model_id = inputs.get("model_id")
    if model_id is None and isinstance(inputs.get("model"), dict):
        model_id = inputs["model"].get("id")
    if model_id is not None:
        values["model_id"] = str(model_id)
    quotation = str(inputs.get("quotation_type") or "").strip().lower()
    drawdown = str(inputs.get("drawdown_type") or "").strip().lower()
    if quotation == "accumulation":
        values["crystallisation_status"] = "accumulation"
    elif drawdown == "ufpls":
        values["crystallisation_status"] = "drawdown"
        values["access_option"] = "ufpls"
    elif quotation == "flexi access drawdown" or drawdown == "flexible":
        values["crystallisation_status"] = "drawdown"
        values["access_option"] = "fad"

    # Prospective SIPP: the one-off payment is the cash going into the plan. The existing
    # fund is the client's other SIPPs, summed for the quotation — it is not money being
    # opened here, so it must not prefill as initial cash. An existing-account illustration
    # keeps account_id set and is left alone.
    if inputs.get("account_id") is None and _positive_number(inputs.get("initial_payment")):
        values.update(
            {
                "has_initial_cash": True,
                "initial_cash_amount": inputs["initial_payment"],
            }
        )

    transfer = inputs.get("transfer_value")
    if _positive_number(transfer):
        values.update({"has_transfer": True, "transfer_amount": transfer})

    member = inputs.get("member_contribution")
    employer = inputs.get("employer_contribution")
    if _positive_number(member) != _positive_number(employer):
        amount = member if _positive_number(member) else employer
        raw_frequency = (
            inputs.get("member_contribution_frequency")
            if _positive_number(member)
            else inputs.get("employer_contribution_frequency")
        )
        values.update({"has_contribution": True, "contribution_amount": amount})
        if mapped := frequency(raw_frequency):
            values["contribution_frequency"] = mapped

    withdrawal = inputs.get("withdrawal_amount")
    if _positive_number(withdrawal):
        values.update({"has_regular_withdrawal": True, "withdrawal_amount": withdrawal})
        if mapped := frequency(inputs.get("withdrawal_frequency"), withdrawal=True):
            values["withdrawal_frequency"] = mapped
        if inputs.get("withdrawal_start_date"):
            values["withdrawal_start_date"] = inputs["withdrawal_start_date"]
    return values


async def _contact_form_prefill(
    ctx: FirmContext,
    contact_id: int | None,
    field_keys: set[str],
    *,
    execution_uid: str | None = None,
) -> dict[str, Any] | None:
    """Seed a later fact-find from facts the firm already holds about this person.

    Kickoff creates (or selects) the contact before the first staff form opens. Returning a blank
    Applicant details form made the adviser type the same name and email twice. This is
    intentionally filtered to the form's own field keys: it is a convenience layer over CRM, not
    a second dossier.
    A submitted response still wins wholesale in ``get_step_form`` below.
    """
    if contact_id is None:
        return None
    row = (
        (
            await ctx.session.execute(
                text(
                    "SELECT type::text AS type, name, title, first_name, middle_name, surname, "
                    "       email, mobile, phone, dob, gender, nationality, "
                    "       tax_residency, "
                    "       national_identifier, national_identifier_type, address, bank_account, "
                    "       marital_status, retirement_age, employment_status, source_of_wealth, "
                    "       is_vulnerable, vulnerability_reasons, vulnerability_characteristics, "
                    "       vulnerability_notes, employer_name, employer_registration_number, "
                    "       employer_address, employer_contributions, data "
                    "FROM crm.contacts WHERE id = :c AND tenant_id = :t"
                ),
                {"c": contact_id, "t": ctx.tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None

    def present(value: Any) -> bool:
        return value is not None and value != "" and value != [] and value != {}

    def holder_name() -> str | None:
        if str(row["type"] or "") == "organisation":
            name = (row["name"] or "").strip()
            return name or None
        parts = [
            str(part).strip()
            for part in (row["first_name"], row["middle_name"], row["surname"])
            if part and str(part).strip()
        ]
        joined = " ".join(parts)
        return joined or None

    values: dict[str, Any] = {}
    data = _as_json_obj(row["data"]) or {}
    # Overflow answers from earlier forms retain their authored field keys. Dotted keys may
    # be nested (`pension.employerContributions.accountNumber`) rather than stored flat.
    for key in field_keys:
        found = _nested_answer(data, key)
        if present(found):
            values[key] = found

    direct = {
        "name": row["name"],
        "title": row["title"],
        "first_name": row["first_name"],
        "middle_name": row["middle_name"],
        "surname": row["surname"],
        "email": row["email"],
        # Same unwrapping as the client's review dossier (`normalise_phone`) — this seeds an
        # adviser's form field, so a provider object here is typed straight back into the book.
        #
        # The KEYS here are the onboarding form's field keys, not the column names. `phone` is
        # the mobile (the key stayed so in-flight answers still resolve). `other_numbers` is the
        # other number, one string.
        "phone": normalise_phone(row["mobile"]),
        "other_numbers": normalise_phone(row["phone"]),
        "dob": row["dob"].isoformat() if hasattr(row["dob"], "isoformat") else row["dob"],
        "gender": row["gender"],
        "nationality": _text_list(row["nationality"]),
        "national_identifier": row["national_identifier"],
        "national_identifier_type": row["national_identifier_type"],
        "pension.maritalStatus": _form_choice(row["marital_status"], contacts_mod.MARITAL_STATUSES),
        "pension.retirementAge": row["retirement_age"],
        "pension.employmentStatus": _form_choice(
            row["employment_status"], contacts_mod.EMPLOYMENT_STATUSES
        ),
        "source_of_wealth": row["source_of_wealth"],
        "is_vulnerable": True if row["is_vulnerable"] else None,
        "vulnerability_reasons": _text_list(row["vulnerability_reasons"]) or None,
        "vulnerability_characteristics": _text_list(row["vulnerability_characteristics"]) or None,
        "vulnerability_notes": row["vulnerability_notes"],
    }
    tax_residencies = _text_list(row["tax_residency"])
    direct["tax_residency"] = tax_residencies[0] if tax_residencies else None
    for key, value in direct.items():
        if key in field_keys and present(value):
            values[key] = value

    address = _as_json_obj(row["address"])
    if "address" in field_keys and address:
        values["address"] = address
    bank = _as_json_obj(row["bank_account"]) or {}
    # The fact-find's switch is `uk` / `international`. The record stores no scheme: an IBAN
    # with no sort code is the international rail, and anything else with UK digits is UK.
    international = bool(bank.get("iban")) and not bank.get("sort_code")
    bank_values = {
        "bank_account.account_name": bank.get("account_name") or bank.get("name"),
        "bank_account.account_number": bank.get("account_number"),
        "bank_account.sort_code": bank.get("sort_code"),
        "bank_account.iban": bank.get("iban"),
        "bank_account.bic": bank.get("bic"),
    }
    if international:
        bank_values["bank_style"] = "international"
    elif bank.get("sort_code") or bank.get("account_number"):
        bank_values["bank_style"] = "uk"
        method = str(bank.get("method") or "").strip().lower()
        bank_values["bank_account.method"] = (
            "faster_payment" if method == "faster_payment" else "bacs"
        )
    for key, value in bank_values.items():
        if key in field_keys and present(value):
            values[key] = value
    if "bank_account.account_name" in field_keys and not present(
        values.get("bank_account.account_name")
    ):
        derived = holder_name()
        if derived:
            values["bank_account.account_name"] = derived
    for key, value in _employer_form_prefill(dict(row), data).items():
        if key in field_keys and present(value) and not present(values.get(key)):
            values[key] = value
    if execution_uid:
        raw = (
            await ctx.session.execute(
                text(
                    "SELECT i.inputs FROM flows.executions e "
                    "JOIN platform.accounts a ON a.application_id = e.crm_application_id "
                    "JOIN crm.account_contacts ac ON ac.account_id = a.id AND ac.contact_id = :c "
                    "JOIN platform.illustrations i ON i.id = a.onboarding_illustration_id "
                    "WHERE e.uid = :e AND e.tenant_id = :t AND ac.role = 'owner' "
                    "ORDER BY a.id LIMIT 1"
                ),
                {"e": execution_uid, "t": ctx.tenant_id, "c": contact_id},
            )
        ).scalar_one_or_none()
        inputs = _as_json_obj(raw)
        if (
            inputs
            and _positive_number(inputs.get("employer_contribution"))
            and "pension.hasEmployerContributions" in field_keys
        ):
            values["pension.hasEmployerContributions"] = True
        for key, value in _illustration_contact_gaps(inputs, values).items():
            if key in field_keys and not present(values.get(key)):
                values[key] = value
    return values or None


async def _account_form_prefill(
    ctx: FirmContext, account_id: int | None, field_keys: set[str]
) -> dict[str, Any] | None:
    """Re-open Account details from the draft account, not a stale copy of the seed."""
    if account_id is None:
        return None
    row = (
        (
            await ctx.session.execute(
                text(
                    "SELECT a.name, a.currency, a.investment_capacity, a.model_id, "
                    "       a.dividend_treatment, a.payments, a.withdrawals, a.transfer, a.data, "
                    "       COALESCE(NULLIF(trim(concat_ws(' ', ct.first_name, ct.surname)), ''), "
                    "                ct.name) AS client_name, "
                    "       p.product_type::text AS product_type, "
                    "       i.inputs AS illustration_inputs, "
                    "       COALESCE(array_agg(afp.fee_package_id ORDER BY afp.sort_order) "
                    "         FILTER (WHERE afp.fee_package_id IS NOT NULL), '{}') "
                    "         AS fee_package_ids "
                    "FROM platform.accounts a "
                    "LEFT JOIN crm.contacts ct ON ct.id = a.contact_id "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "LEFT JOIN platform.illustrations i ON i.id = a.onboarding_illustration_id "
                    "LEFT JOIN platform.account_fee_packages afp ON afp.account_id = a.id "
                    "WHERE a.id = :a AND a.tenant_id = :t "
                    "GROUP BY a.id, i.inputs, ct.id, p.id"
                ),
                {"a": account_id, "t": ctx.tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    data = _as_json_obj(row["data"]) or {}
    transfer = _as_json_obj(row["transfer"]) or {}
    initial = regular_movements.initial_cash_deposit_of(data, row["payments"]) or {}
    regular = next(iter(regular_movements.regular_movements_of(row["payments"])), {})
    withdrawal = next(iter(regular_movements.regular_movements_of(row["withdrawals"])), {})
    third_party = _as_json_obj(data.get("third_party_provider_bank_details")) or {}
    activity = _as_json_obj(data.get("account_activity")) or {}
    values = _illustration_account_prefill(_as_json_obj(row["illustration_inputs"]))
    default_name = " ".join(
        part for part in (row["client_name"], row["product_type"]) if part
    ).strip()
    persisted: dict[str, Any] = {
        "account_name": row["name"] or default_name or None,
        "currency": row["currency"],
        "investment_capacity": row["investment_capacity"],
        "model_id": str(row["model_id"]) if row["model_id"] is not None else None,
        "fee_package_ids": [str(value) for value in row["fee_package_ids"]],
        "dividend_treatment": row["dividend_treatment"],
        "dividend_settlement_currency": data.get("dividend_settlement_currency"),
        "crystallisation_status": data.get("crystallisation_status"),
        "money_purchase_trigger": data.get("money_purchase_trigger"),
        "money_purchase_trigger_date": data.get("money_purchase_trigger_date"),
        "sipp_employer_scheme": data.get("sipp_employer_scheme"),
        "has_initial_cash": True if initial else None,
        "initial_cash_amount": initial.get("investment_amount"),
        "has_transfer": True if transfer else None,
        "transfer_type": transfer.get("transfer_type"),
        "transfer_extent": transfer.get("extent"),
        "transfer_amount": transfer.get("amount"),
        "transfer_provider": transfer.get("provider_id"),
        "transfer_reference": transfer.get("provider_reference"),
        "safeguarded_benefits": (
            transfer.get("safeguarded_benefits") if "safeguarded_benefits" in transfer else None
        ),
        "fca_reference": transfer.get("fca_reference"),
        "has_contribution": True if regular or data.get("regular_contribution") else None,
        "contribution_amount": regular.get("amount"),
        "contribution_frequency": regular.get("frequency"),
        "contribution_expected_by": regular.get("next_date"),
        "has_regular_withdrawal": True if withdrawal else None,
        "withdrawal_amount": withdrawal.get("amount"),
        "withdrawal_frequency": withdrawal.get("frequency"),
        "withdrawal_start_date": withdrawal.get("next_date"),
        "has_third_party_provider": bool(third_party),
        "third_party_provider": third_party.get("provider"),
        "third_party_account_name": third_party.get("account_name"),
        "third_party_account_number": third_party.get("account_number"),
        "third_party_sort_code": third_party.get("sort_code"),
        "beneficiaries": data.get("beneficiaries"),
        "account_activity_nature": activity.get("nature"),
        "account_activity_standing_orders": activity.get("standing_orders"),
        "account_activity_funds_in": activity.get("funds_in"),
        "account_activity_funds_out": activity.get("funds_out"),
        "account_activity_max_turnover": activity.get("max_turnover"),
        "account_activity_transaction_scale": activity.get("transaction_scale"),
    }
    values.update({key: value for key, value in persisted.items() if value is not None})
    return {key: value for key, value in values.items() if key in field_keys and value is not None}


async def _applicant_wrapper_flags(
    ctx: FirmContext, exec_uid: str, contact_id: int
) -> dict[str, bool]:
    """ISA/pension and GIA flags for the owner whose fact-find this step is.

    The National Insurance question reads them. An account the person does not
    own does not change what they are asked.
    """
    from app.integrations.sei.enrol import wrapper_flags

    products = (
        (
            await ctx.session.execute(
                text(
                    "SELECT DISTINCT upper(p.product_type::text) AS product "
                    "FROM flows.executions e "
                    "JOIN platform.accounts a ON a.application_id = e.crm_application_id "
                    "  AND a.tenant_id = e.tenant_id "
                    "JOIN crm.account_contacts ac "
                    "  ON ac.account_id = a.id AND ac.contact_id = :c AND ac.role = 'owner' "
                    "JOIN platform.products p ON p.id = a.product_id "
                    "WHERE e.uid = :e AND e.tenant_id = :t"
                ),
                {"e": exec_uid, "c": contact_id, "t": ctx.tenant_id},
            )
        )
        .scalars()
        .all()
    )
    return wrapper_flags(products)


@router.get("/{exec_uid}/cases/{case_uid}/steps/{step_key}/form", name="get-application-step-form")
async def get_step_form(
    exec_uid: str,
    case_uid: str,
    step_key: str,
    participant_contact_id: int | None = None,
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_session),
) -> StepFormOut:
    """The form behind a staff-assigned ``fill_form`` step, plus anything already submitted."""
    require_permission(principal, CASES_READ)
    await _require_in_book(ctx, principal, exec_uid)
    step = (
        (
            await ctx.session.execute(
                text(
                    "SELECT cs.step_key, cs.step_type, cs.title, cs.status, cs.request_item_id, "  # noqa: S608
                    "       cs.participant_subject_id, c.kind, c.subject_id, ri.form_uid, "
                    "       f.code AS flow_code, "
                    f"       {_DECLARATION_SENT_SQL} AS adviser_declared "
                    "FROM flows.case_steps cs "
                    "JOIN flows.cases c ON c.id = cs.case_id "
                    "JOIN flows.executions e ON e.id = c.execution_id "
                    "JOIN flows.flows f ON f.id = e.flow_id "
                    "LEFT JOIN documents.request_items ri ON ri.id = cs.request_item_id "
                    "WHERE e.uid = :eu AND c.uid = :cu AND cs.step_key = :k AND cs.tenant_id = :t "
                    "  AND (CAST(:psid AS int) IS NULL "
                    "       OR cs.participant_subject_id = CAST(:psid AS int)) LIMIT 1"
                ),
                {
                    "eu": exec_uid,
                    "cu": case_uid,
                    "k": step_key,
                    "t": ctx.tenant_id,
                    "psid": participant_contact_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if step is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Step not found.")
    if step["step_type"] != "fill_form":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That step is not a form.")
    if not step["form_uid"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This step has no form attached yet — it may still be waiting on an earlier step.",
        )
    form = (
        (
            await ctx.session.execute(
                text(
                    "SELECT id, uid, name, description FROM documents.forms "
                    "WHERE uid = :u AND tenant_id = :t"
                ),
                {"u": step["form_uid"], "t": ctx.tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if form is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The step's form is not available.")
    fields = (
        (
            await ctx.session.execute(
                text(
                    "SELECT uid, field_key, field_type, label, section, description, required, "
                    "       conditions, validation, options, sort_order "
                    "FROM documents.form_fields WHERE form_id = :f ORDER BY sort_order, id"
                ),
                {"f": form["id"]},
            )
        )
        .mappings()
        .all()
    )
    answers = (
        await ctx.session.execute(
            text(
                "SELECT answers FROM documents.form_responses WHERE request_item_id = :i "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"i": step["request_item_id"]},
        )
    ).scalar_one_or_none()
    field_keys = {str(field["field_key"]) for field in fields}
    contact_id = (
        participant_contact_id
        or step["participant_subject_id"]
        or (step["subject_id"] if step["kind"] == "profile" else None)
    )
    prefill = (
        await _account_form_prefill(ctx, int(step["subject_id"]), field_keys)
        if step["kind"] == "account" and step["subject_id"] is not None
        else await _contact_form_prefill(
            ctx,
            int(contact_id) if contact_id is not None else None,
            field_keys,
            execution_uid=exec_uid,
        )
    )
    context: dict[str, Any] = {}
    if step["kind"] == "account" and step["subject_id"] is not None:
        account_context = (
            (
                await ctx.session.execute(
                    text(
                        "SELECT COALESCE(a.custody::text, app.custody::text) AS custody, "
                        "       p.product_type::text AS product_type, p.allowed_currencies, "
                        "       COALESCE(p.is_pension, false) AS is_pension, "
                        "       a.product_id, a.model_id, "
                        "       COALESCE(a.custodian, app.provider) AS custodian, "
                        "       i.uid AS illustration_uid, i.name AS illustration_name, "
                        "       i.output_reference AS illustration_reference, "
                        "       i.created_at AS illustration_created_at "
                        "FROM platform.accounts a "
                        "LEFT JOIN crm.applications app ON app.id = a.application_id "
                        "LEFT JOIN platform.products p ON p.id = a.product_id "
                        "LEFT JOIN platform.illustrations i "
                        "  ON i.id = a.onboarding_illustration_id "
                        "WHERE a.id = :a AND a.tenant_id = :t"
                    ),
                    {"a": int(step["subject_id"]), "t": ctx.tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if account_context:
            account_values = dict(account_context)
            illustration_uid = account_values.pop("illustration_uid", None)
            illustration_name = account_values.pop("illustration_name", None)
            illustration_reference = account_values.pop("illustration_reference", None)
            illustration_created_at = account_values.pop("illustration_created_at", None)
            context["account"] = account_values
            if illustration_uid:
                context["illustration"] = {
                    "uid": illustration_uid,
                    "name": illustration_name,
                    "output_reference": illustration_reference,
                    "created_at": illustration_created_at,
                }
    elif contact_id is not None:
        illustration_context = (
            (
                await ctx.session.execute(
                    text(
                        "SELECT i.uid, i.name, i.output_reference, i.created_at "
                        "FROM flows.executions e "
                        "JOIN platform.accounts a ON a.application_id = e.crm_application_id "
                        "JOIN crm.account_contacts ac "
                        "  ON ac.account_id = a.id AND ac.contact_id = :c AND ac.role = 'owner' "
                        "JOIN platform.illustrations i ON i.id = a.onboarding_illustration_id "
                        "WHERE e.uid = :e AND e.tenant_id = :t ORDER BY a.id LIMIT 1"
                    ),
                    {"e": exec_uid, "t": ctx.tenant_id, "c": contact_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if illustration_context:
            context["illustration"] = dict(illustration_context)
        context["applicant"] = await _applicant_wrapper_flags(ctx, exec_uid, int(contact_id))
    return StepFormOut(
        step_key=str(step["step_key"]),
        title=step["title"],
        status=str(step["status"]),
        form_uid=str(form["uid"]),
        name=str(form["name"]),
        description=form["description"],
        fields=[StepFormField(**dict(f)) for f in fields],
        editable=(
            str(step["status"]) != "fulfilled"
            or (str(step["flow_code"]) == "onboarding" and not bool(step["adviser_declared"]))
        ),
        answers=(answers if isinstance(answers, dict) else json.loads(answers))
        if answers is not None
        else prefill,
        context=context,
    )


class StepFormSubmit(BaseModel):
    answers: dict[str, Any]
    participant_contact_id: int | None = None


async def _missing_required(ctx: FirmContext, form_uid: str, answers: dict[str, Any]) -> list[str]:
    """Required fields of the step's form that this submission does not answer.

    Checked HERE so an incomplete submission is refused *before* anything is stored (the caller gets
    the list of questions, and no half-empty ``documents.form_responses`` row is written). The same
    rule is re-asserted at the transition itself (``orchestrator.submit_step``), which is what makes
    it hold for the client's own fulfilment endpoint and for ``/action`` too; this is the friendly
    early copy of a single implementation (``app/flows/form_validation.py``), not a second one.
    """
    return await form_validation.missing_required_answers(
        ctx.session, tenant_id=ctx.tenant_id, form_uid=form_uid, answers=answers
    )


def _form_mode(definition: object, step_key: str) -> str | None:
    """``config.mode`` of one step on the pinned definition (``gather``, ``review``, …)."""
    if isinstance(definition, str):
        try:
            definition = json.loads(definition)
        except json.JSONDecodeError:
            return None
    if not isinstance(definition, dict):
        return None
    for spec in definition.get("steps") or []:
        if not isinstance(spec, dict) or spec.get("id") != step_key:
            continue
        config = spec.get("config")
        if isinstance(config, dict) and isinstance(config.get("mode"), str):
            return str(config["mode"])
        return None
    return None


def _staff_may_complete_form(assignee: str, mode: str | None) -> bool:
    """Staff complete their own forms, and a client form whose mode is ``gather``.

    A confirmation or review step stays the client's: completing it here would record their
    agreement without them. A client step with no mode is treated the same way.
    """
    if assignee == "staff":
        return True
    return assignee == "client" and mode == "gather"


_FORM_STEP_SQL = (
    "SELECT cs.step_type, cs.status, cs.assignee, cs.request_item_id, ri.form_uid, "  # noqa: S608
    "       ri.request_id, ri.status AS item_status, r.contact_id, "
    "       f.code AS flow_code, fv.definition, "
    f"       {_DECLARATION_SENT_SQL} AS adviser_declared "
    "FROM flows.case_steps cs "
    "JOIN flows.cases c ON c.id = cs.case_id "
    "JOIN flows.executions e ON e.id = c.execution_id "
    "JOIN flows.flows f ON f.id = e.flow_id "
    "JOIN flows.flow_versions fv ON fv.id = e.flow_version_id "
    "LEFT JOIN documents.request_items ri ON ri.id = cs.request_item_id "
    "LEFT JOIN documents.requests r ON r.id = ri.request_id "
    "WHERE e.uid = :eu AND c.uid = :cu AND cs.step_key = :k AND cs.tenant_id = :t "
    "  AND (CAST(:psid AS int) IS NULL OR cs.participant_subject_id = CAST(:psid AS int)) "
    "LIMIT 1"
)


@router.post(
    "/{exec_uid}/cases/{case_uid}/steps/{step_key}/form", name="submit-application-step-form"
)
async def submit_step_form(
    exec_uid: str,
    case_uid: str,
    step_key: str,
    body: StepFormSubmit,
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Staff complete a staff-assigned ``fill_form`` step on the client's behalf.

    Stores the answers against the step's request item and fulfils it, then routes through the same
    ``on_item_fulfilled`` hook the client path uses — so the CRM write-back mapping runs and the
    execution recomputes identically, whoever filled the form in."""
    require_permission(principal, CASES_WRITE)
    await _require_in_book(ctx, principal, exec_uid)
    step = (
        (
            await ctx.session.execute(
                text(_FORM_STEP_SQL),  # noqa: S608 - a module constant
                {
                    "eu": exec_uid,
                    "cu": case_uid,
                    "k": step_key,
                    "t": ctx.tenant_id,
                    "psid": body.participant_contact_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if step is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Step not found.")
    if step["step_type"] != "fill_form":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That step is not a form.")
    # Staff complete their own forms, and a client-assigned ``gather`` form (a fact find the
    # adviser may fill). A confirmation or review step stays the client's: completing it here
    # would record their agreement without them.
    if not _staff_may_complete_form(
        str(step["assignee"]), _form_mode(step["definition"], step_key)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This step is assigned to the client — only they can complete it.",
        )
    if step["request_item_id"] is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This step has no form to complete yet — it may still be waiting on an earlier step.",
        )
    amendment = str(step["status"]) == "fulfilled" and str(step["item_status"]) == "fulfilled"
    # A handler that refused its write-back leaves the step `blocked` with its item already
    # fulfilled — the answers are stored and one of them is the problem. That is a CORRECTION, not
    # an amendment: the step never completed, so the transition still has to run and advance the
    # graph. Neither branch below admitted the shape, so a journey that hit (say) a fee package the
    # account cannot take could not be resubmitted from any door.
    correction = str(step["status"]) == "blocked" and str(step["item_status"]) == "fulfilled"
    adviser_declared = bool(step["adviser_declared"])
    if amendment and str(step["flow_code"]) == "onboarding":
        # Serialise against the declaration handler. Without the row lock, an amendment and the
        # invitation handoff could both read "not declared" and commit in the opposite order.
        adviser_declared = bool(
            (
                await ctx.session.execute(
                    text(
                        # FOR UPDATE of the application row is what the declaration handler writes,
                        # so this waits out a handoff already in flight and then reads the step.
                        f"SELECT {_DECLARATION_SENT_SQL} FROM crm.applications a "  # noqa: S608
                        "JOIN flows.executions e ON e.crm_application_id = a.id "
                        "WHERE e.uid = :e AND a.tenant_id = :t FOR UPDATE OF a"
                    ),
                    {"e": exec_uid, "t": ctx.tenant_id},
                )
            ).scalar_one()
        )
    if amendment and (str(step["flow_code"]) != "onboarding" or adviser_declared):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This form is locked because the application has been sent to the client.",
        )
    if (
        not amendment
        and not correction
        and (
            str(step["status"]) not in {"pending", "waiting"}
            or str(step["item_status"]) != "pending"
        )
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This form cannot be submitted in its current state.",
        )
    missing = await _missing_required(ctx, str(step["form_uid"] or ""), body.answers)
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "These answers are required: " + ", ".join(missing[:8]) + ".",
        )
    await ctx.session.execute(
        text(
            "INSERT INTO documents.form_responses "
            "  (uid, tenant_id, request_item_id, form_uid, contact_id, answers, submitted_by) "
            "VALUES (:uid, :t, :iid, :form, :cid, CAST(:answers AS jsonb), :by)"
        ),
        {
            "uid": new_id(),
            "t": ctx.tenant_id,
            "iid": int(step["request_item_id"]),
            "form": step["form_uid"] or "",
            "cid": step["contact_id"],
            "answers": json.dumps(body.answers),
            "by": await _actor_uuid(ctx.session),
        },
    )
    if amendment:
        case_id = await _case_id(ctx, exec_uid, case_uid)
        await orchestrator.amend_completed_form(
            ctx.session,
            tenant_id=ctx.tenant_id,
            case_id=case_id,
            step_key=step_key,
            answers=body.answers,
            participant_subject_id=body.participant_contact_id,
        )
    else:
        if not correction:
            # Already fulfilled on a correction — re-stamping would move `fulfilled_at` to the
            # retry and lose when the answers were actually first given.
            await ctx.session.execute(
                text(
                    "UPDATE documents.request_items SET status = 'fulfilled', "
                    "  fulfilled_at = now() WHERE id = :i"
                ),
                {"i": int(step["request_item_id"])},
            )
            await ctx.session.execute(
                text(
                    "UPDATE documents.requests SET status = 'fulfilled', completed_at = now() "
                    "WHERE id = :rid AND NOT EXISTS (SELECT 1 FROM documents.request_items "
                    "  WHERE request_id = :rid AND status = 'pending')"
                ),
                {"rid": int(step["request_id"])},
            )
        event = await orchestrator.on_item_fulfilled(ctx.session, int(step["request_item_id"]))
        # A handler that refuses its write-back blocks the step and the FULFILMENT still succeeded,
        # so answering a bare 200 told the adviser their answers were saved and sent them to the
        # next form while the journey sat blocked behind them. Report the refusal with the reason;
        # the answers stay stored, because they are the draft that needs correcting.
        if event is not None and event.signal is StepSignal.BLOCKED:
            return {
                "ok": False,
                "amended": False,
                "corrected": correction,
                "blocked": True,
                "reason": event.reason,
            }
    return {"ok": True, "amended": amendment, "corrected": correction, "blocked": False}


@router.post(
    "/{exec_uid}/cases/{case_uid}/steps/{step_key}/form/save",
    name="save-application-step-form",
)
async def save_step_form(
    exec_uid: str,
    case_uid: str,
    step_key: str,
    body: StepFormSubmit,
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Store answers on a form that is still open, without fulfilling the step.

    The same assignee rule as submit: staff forms, and a client ``gather`` form. Required
    fields are not checked — a fact find is saved half-finished. Scoring, mapping and
    ``remember`` run on submit, which still appends its own response and fulfils the item.
    """
    require_permission(principal, CASES_WRITE)
    await _require_in_book(ctx, principal, exec_uid)
    step = (
        (
            await ctx.session.execute(
                text(_FORM_STEP_SQL),  # noqa: S608 - a module constant
                {
                    "eu": exec_uid,
                    "cu": case_uid,
                    "k": step_key,
                    "t": ctx.tenant_id,
                    "psid": body.participant_contact_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if step is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Step not found.")
    if step["step_type"] != "fill_form":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That step is not a form.")
    if not _staff_may_complete_form(
        str(step["assignee"]), _form_mode(step["definition"], step_key)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This step is assigned to the client — only they can complete it.",
        )
    if step["request_item_id"] is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This step has no form to complete yet — it may still be waiting on an earlier step.",
        )
    if str(step["status"]) == "fulfilled" or str(step["item_status"]) != "pending":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This form has already been submitted.",
        )
    if str(step["status"]) not in {"pending", "waiting"}:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This form cannot be saved in its current state.",
        )
    await form_draft.upsert_draft(
        ctx.session,
        tenant_id=ctx.tenant_id,
        request_item_id=int(step["request_item_id"]),
        form_uid=str(step["form_uid"] or ""),
        contact_id=int(step["contact_id"]) if step["contact_id"] is not None else None,
        answers=body.answers,
        submitted_by=await _actor_uuid(ctx.session),
    )
    return {"saved": True}


# ============================================================================
# In-person acceptance of a client's terms
#
# ``accept_terms`` is genuinely the CLIENT's act — the legacy app's only mutating public endpoint
# besides identity was ``POST /api/a/applications/{uid}/terms`` — and
# ``orchestrator._assert_fulfilment_supported`` rightly refuses to let ``cases:write`` tick the step
# off with nothing recorded, because the acknowledgement IS the evidence.
#
# But plenty of onboarding happens with the client in the room (or on the phone), and there the
# adviser is the one at a keyboard. This endpoint is that case: it fulfils the acknowledgement item
# for real, and stamps WHO attested to it. It is deliberately NOT a way to skip the step — the
# artifact is created, the document is filed to the client's record, and the audit entry says the
# acceptance was taken in person rather than clicked by the client. Read the trail and you can tell
# the two apart, which is the whole point.
#
# The acceptance is recorded against a SIGNED COPY, and only against one. A portal acceptance is
# its own evidence — the client's session, the item, the audit row. An adviser's attestation is
# not: it is one person's word that another agreed. So the upload is required, not offered: a scan
# or photograph of the terms the client signed, filed on the client's record as
# ``document_type = 'signed_terms'``, linked from the acknowledgement item
# (``fulfilled_document_id``), from the step (``case_steps.data.document_id``, which is what the
# journey row renders) and — for a library term — from the register row itself
# (``terms.acceptances.evidence_document_id``).
# ============================================================================

#: ``documents.documents.document_type`` for the signed copy behind an in-person acceptance.
SIGNED_TERMS_DOCUMENT_TYPE = "signed_terms"


async def acknowledge_terms(
    exec_uid: str,
    case_uid: str = Form(...),
    step_key: str = Form(...),
    participant_contact_id: int | None = Form(None),
    # Explicit, not inferred: the caller is asserting the client accepted these terms. A bare POST
    # with no attestation is refused, so this can never be reached by an accidental submit.
    attested: bool = Form(False),
    note: str | None = Form(None),
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Record that the client accepted an ``accept_terms`` step's declaration in the adviser's
    presence, attributed to the adviser and evidenced by the uploaded signed copy.

    Fulfils the step's acknowledgement item through the same
    ``on_item_fulfilled`` → handler → recompute path the client's own endpoint uses, so the journey
    advances identically — but the document audit trail carries ``in_person: true``, the attesting
    staff member and the signed copy's uid, so an in-person acceptance is never indistinguishable
    from one the client clicked. Multipart: ``file`` is the signed terms (PDF or image) and is
    required — the endpoint refuses to record an acceptance with nothing behind it.
    """
    require_permission(principal, CASES_WRITE)
    await _require_in_book(ctx, principal, exec_uid)
    if not attested:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Confirm that the client accepted these terms before recording it.",
        )
    case_id = await _case_id(ctx, exec_uid, case_uid)
    await _require_participant_for_fanned(ctx, case_id, step_key, participant_contact_id)
    step = (
        (
            await ctx.session.execute(
                text(
                    "SELECT cs.step_type, cs.status, cs.request_item_id, ri.request_id, "
                    "       ri.uid AS item_uid, ri.status AS item_status, "
                    "       ri.target_document_id, td.uid AS target_uid, r.contact_id "
                    "FROM flows.case_steps cs "
                    "LEFT JOIN documents.request_items ri ON ri.id = cs.request_item_id "
                    "LEFT JOIN documents.requests r ON r.id = ri.request_id "
                    "LEFT JOIN documents.documents td ON td.id = ri.target_document_id "
                    "WHERE cs.case_id = :c AND cs.step_key = :k AND cs.tenant_id = :t "
                    "  AND (CAST(:psid AS int) IS NULL "
                    "       OR cs.participant_subject_id = CAST(:psid AS int)) LIMIT 1"
                ),
                {
                    "c": case_id,
                    "k": step_key,
                    "t": ctx.tenant_id,
                    "psid": participant_contact_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if step is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Step not found.")
    if step["step_type"] != "accept_terms":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "That step is not a terms acknowledgement."
        )
    if step["request_item_id"] is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This step has nothing to accept yet — it may still be waiting on an earlier step.",
        )
    if str(step["item_status"]) != "pending":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "These terms have already been accepted."
        )
    if step["contact_id"] is None:
        # The acknowledgement item hangs off the client's request, so this cannot happen for a
        # step the handler raised — but the signed copy is filed on a CONTACT, and filing it on
        # nobody would leave evidence no record can show.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "This declaration is not attached to a client."
        )
    note = (note or "").strip() or None
    attested_by = await _actor_uuid(ctx.session)
    # The signed copy FIRST: it is validated (type, size) and stored before anything is recorded,
    # so a refused upload leaves the acceptance exactly where it was. The upload is what makes
    # this an acceptance rather than an assertion — see the section comment.
    signed_id, signed_uid = await documents_router.store_contact_upload(
        ctx.session,
        tenant_id=ctx.tenant_id,
        contact_id=int(step["contact_id"]),
        file=file,
        document_type=SIGNED_TERMS_DOCUMENT_TYPE,
        accept=documents_router.signed_copy_only,
        audit_action="document.uploaded",
        audit_detail={
            "signed_copy_of": step["target_uid"],
            "request_item": str(step["item_uid"]),
            "in_person": True,
            "attested_by": attested_by,
        },
    )
    detail = {
        "request_item": str(step["item_uid"]),
        "in_person": True,
        "attested_by": attested_by,
        "signed_copy": signed_uid,
        **({"note": note} if note else {}),
    }
    if step["target_document_id"] is not None:
        # Same two writes the client's own path makes: the client keeps sight of what they accepted,
        # and the acceptance is recorded against that document.
        await ctx.session.execute(
            text(
                "INSERT INTO documents.document_contacts "
                "  (document_id, tenant_id, contact_id, role, added_by) "
                "VALUES (:doc, :t, :cid, 'recipient', auth.uid()) "
                "ON CONFLICT (document_id, contact_id, role) DO NOTHING"
            ),
            {"doc": step["target_document_id"], "t": ctx.tenant_id, "cid": step["contact_id"]},
        )
        # `tenant_id` comes from the CONTEXT, not `auth.tenant_id()`. Under `firm_session` the
        # session runs as service_role and the claims are published for `audit.record()`, but the
        # tenant this endpoint is authorised for is `ctx.tenant_id` — using the claim helper here
        # made the insert depend on a second source of truth, and it wrote NULL wherever that source
        # was absent.
        await ctx.session.execute(
            text(
                "INSERT INTO documents.audit_log "
                "  (tenant_id, document_id, actor_id, action, detail) "
                "SELECT :t, d.id, auth.uid(), 'document.acknowledged', CAST(:detail AS jsonb) "
                "FROM documents.documents d WHERE d.uid = :uid AND d.tenant_id = :t"
            ),
            {"t": ctx.tenant_id, "detail": json.dumps(detail), "uid": step["target_uid"]},
        )
    # The acknowledgement item points at the signed copy the way an upload item points at its
    # file, so "what was this acceptance recorded against" is one join, not a search.
    await ctx.session.execute(
        text(
            "UPDATE documents.request_items SET status = 'fulfilled', fulfilled_at = now(), "
            "  fulfilled_document_id = :doc WHERE id = :i"
        ),
        {"i": int(step["request_item_id"]), "doc": signed_id},
    )
    await ctx.session.execute(
        text(
            "UPDATE documents.requests SET status = 'fulfilled', completed_at = now() "
            "WHERE id = :rid AND NOT EXISTS (SELECT 1 FROM documents.request_items "
            "  WHERE request_id = :rid AND status = 'pending')"
        ),
        {"rid": int(step["request_id"])},
    )
    # The step renders `data.document_id` as its document link (the same key generate_document
    # uses), so the journey row shows the signed copy beside the recorded acceptance.
    await ctx.session.execute(
        text(
            "UPDATE flows.case_steps SET data = COALESCE(data, '{}'::jsonb) || CAST(:d AS jsonb) "
            "WHERE case_id = :c AND step_key = :k AND tenant_id = :t "
            "  AND (CAST(:psid AS int) IS NULL "
            "       OR participant_subject_id = CAST(:psid AS int))"
        ),
        {
            "d": json.dumps({"document_id": str(signed_id)}),
            "c": case_id,
            "k": step_key,
            "t": ctx.tenant_id,
            "psid": participant_contact_id,
        },
    )
    # The step's own note lands on the case step + domain event through the handler path, so the
    # journey timeline reads "accepted in person" without anyone opening the document trail. The
    # signed copy rides along so a library term's register row can point at it.
    await orchestrator.submit_step(
        ctx.session,
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        step_key=step_key,
        detail={
            "note": note or "Accepted in person, recorded by the adviser.",
            "in_person": True,
            "attested_by": attested_by,
            "evidence_document_id": str(signed_id),
        },
        participant_subject_id=participant_contact_id,
    )
    return {"ok": True, "document_uid": signed_uid}


router.add_api_route(
    "/{exec_uid}/acknowledge",
    acknowledge_terms,
    methods=["POST"],
    name="acknowledge-application-terms",
    route_class_override=documents_router.SingleUploadRoute,
)


class TermsCopyOut(BaseModel):
    """What an ``accept_terms`` step asks the client to agree to, for the adviser to PRINT.

    An in-person acceptance is recorded against a signed copy (``/acknowledge`` above), and the
    client can only sign a copy somebody handed them. One of the two bodies is set: ``content`` is
    the declaration's sanitised HTML when it was authored inline (or pinned to an HTML library
    term); ``document_uid`` names the stored file — a PDF client agreement — when it was not.
    Neither ⇒ a bare acknowledgement with nothing to print."""

    name: str | None
    content: str | None
    document_uid: str | None
    content_type: str | None
    client_name: str | None


@router.get("/{exec_uid}/terms", name="application-terms-copy", response_model=TermsCopyOut)
async def terms_copy(
    exec_uid: str,
    case_uid: str,
    step_key: str,
    participant_contact_id: int | None = None,
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_session),
) -> TermsCopyOut:
    """The declaration behind a terms step, so staff can print a copy for the client to sign.

    Reads the SAME target the client would be shown — the request item's ``target_document_id``,
    snapshotted at instantiate — not the flow definition, so a later edit of the flow cannot put a
    different text in front of the client than the one their acceptance is recorded against. The
    participant only picks an instance of a fanned step; every instance shares the one target, so
    it is optional here where ``/acknowledge`` requires it."""
    require_permission(principal, CASES_READ)
    await _require_in_book(ctx, principal, exec_uid)
    case_id = await _case_id(ctx, exec_uid, case_uid)
    row = (
        (
            await ctx.session.execute(
                text(
                    "SELECT cs.step_type, cs.request_item_id, cs.title, "
                    "       td.uid AS target_uid, td.name AS target_name, td.document_type, "
                    "       td.metadata ->> 'content' AS content, f.content_type, "
                    "       COALESCE(ct.first_name || ' ' || ct.surname, ct.name) AS client_name "
                    "FROM flows.case_steps cs "
                    "LEFT JOIN documents.request_items ri ON ri.id = cs.request_item_id "
                    "LEFT JOIN documents.requests r ON r.id = ri.request_id "
                    "LEFT JOIN crm.contacts ct ON ct.id = r.contact_id "
                    "LEFT JOIN documents.documents td ON td.id = ri.target_document_id "
                    "LEFT JOIN documents.files f "
                    "  ON f.id = COALESCE(td.original_file_id, td.pdf_file_id) "
                    "WHERE cs.case_id = :c AND cs.step_key = :k AND cs.tenant_id = :t "
                    "  AND (CAST(:psid AS int) IS NULL "
                    "       OR cs.participant_subject_id = CAST(:psid AS int)) "
                    "ORDER BY cs.id LIMIT 1"
                ),
                {"c": case_id, "k": step_key, "t": ctx.tenant_id, "psid": participant_contact_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Step not found.")
    if row["step_type"] != "accept_terms":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "That step is not a terms acknowledgement."
        )
    if row["request_item_id"] is None or row["target_uid"] is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "This step has no declaration attached yet — it may still be waiting on an earlier "
            "step.",
        )
    content = (row["content"] or "").strip() if row["document_type"] == "terms" else ""
    has_file = row["content_type"] is not None
    return TermsCopyOut(
        name=row["target_name"] or row["title"],
        content=sanitise_html(content) if content else None,
        document_uid=str(row["target_uid"]) if has_file and not content else None,
        content_type=row["content_type"] if has_file and not content else None,
        client_name=row["client_name"],
    )


@router.get("/{exec_uid}/application.pdf", name="application-pack-pdf")
async def application_pack_pdf(
    exec_uid: str,
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_book_session),
) -> Response:
    """Branded audit PDF of this journey: questions, answers, the terms agreed, and the signature.

    Read-only. Generated on download from the stored answers and the declaration snapshot, so it
    can be fetched again later without becoming a second copy of the record."""
    require_permission(principal, CASES_READ)
    await _require_in_book(ctx, principal, exec_uid)
    pack = await load_application_pack(ctx.session, ctx.tenant_id, exec_uid)
    if pack is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found.")
    try:
        pdf = await render_pack_pdf(pack)
    except Exception:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The application PDF could not be rendered.",
        ) from None
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": storage.content_disposition(
                "attachment", f"application_{pack.uid}.pdf"
            ),
            "Cache-Control": "private, no-store",
        },
    )
