"""The internal-operations AML console: every client Graphene screens, one client's page, one
check, and the acts an operator takes on them.

Reads come from ``compliance.*`` (migrations 0186, 0221), which the worker's ComplyCube consumer
keeps in step with the provider, plus the portal's ``crm.kyc_checks`` for clients still waiting on
a first screen. Writes go to ComplyCube first and are then stored, so a refused provider call
leaves nothing half-recorded here. A missing ComplyCube key is 503 on every write that calls
ComplyCube.

A client's identity images are read from ComplyCube: a check lists the ones ComplyCube holds, and
``GET /aml/checks/{uid}/images/{part}`` reads one each time it is shown.

**Why the whole router is gated on ``aml:read`` rather than on tenancy.** ``aml:read`` is
registered against the ``operations`` portal (``app.permissions``), which puts it in
``rbac.OPERATIONS_ONLY`` — no firm-scoped role can be granted it and no firm's WorkOS project
ever learns the slug. The permission therefore *is* the tenant control: holding it means being
Graphene, and Graphene's remit is cross-firm. A firm's own window onto screening is
``screening:read`` on the platform surface (``crm.get_contact_screening``): a status and a date.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit_actions as actions
from app import branding, listing, storage
from app.auth import (
    Principal,
    build_jwt_claims,
    get_current_principal,
    require_permission,
)
from app.complycube import (
    attention,
    clients,
    decisions,
    identity,
    routing,
    screen,
    tasks,
    uploads,
)
from app.complycube import images as complycube_images
from app.complycube import reports as complycube_reports
from app.complycube.client import (
    ComplyCubeNotConfigured,
    ScreeningClient,
    environment,
    environment_of,
    monitoring_of,
    require_client,
)
from app.complycube.outcomes import TECHNICAL_FAILURE, next_attempt, to_kyc_status
from app.complycube.poller import GIVE_UP
from app.contacts import phone_expr
from app.db import SessionLocal
from app.idv import capture
from app.listing import DEFAULT_LIMIT
from app.permissions import AML_ADJUDICATE, AML_READ, AML_WRITE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/aml", tags=["aml"])

#: Rows per page on the client list: the product-wide page size. Fixed rather than caller-supplied:
#: an unbounded page over every firm's screening book is not something a query string should be
#: able to ask for.
PAGE_SIZE = DEFAULT_LIMIT


@dataclass
class AmlContext:
    """An AML request context: an elevated session, the resolved TARGET tenant, and the caller.

    ``targeted`` records whether the caller actually passed ``?tenant=``; ``foreign`` whether that
    target differs from the caller's own tenant. The two are distinct because the list is
    cross-tenant by default and only narrows when a firm was explicitly named — without
    ``targeted`` an un-narrowed request would be indistinguishable from one narrowed to the
    caller's own tenant, and would silently return nothing.
    """

    session: AsyncSession
    tenant_id: int
    targeted: bool
    foreign: bool
    principal: Principal
    user_id: str  # the caller's internal auth.users id
    own_tenant_id: int  # the caller's own tenant (operations, for Graphene staff)


async def aml_session(
    # STAYS SINGLE: this is an AUTH-CONTEXT dependency that gates a `require_permission` call, so
    # widening it is a security change, not a filter change.
    tenant: str | None = None,
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[AmlContext]:
    """Mirror of ``storage.py::storage_session``: service_role, identity resolved in the DB, an
    optional target tenant by uid.

    Two things worth being explicit about.

    1. **The foreign branch is the normal branch here.** Graphene ops staff resolve to the
       operations tenant, so any firm's AML data is *always* foreign and the ``aml:read`` gate
       below always fires. That is intended: there is no such thing as an ops principal reading
       "its own" screening data.

    2. **There is no RLS backstop under service_role** — the role is BYPASSRLS, so the
       ``compliance.*`` policies do not apply to anything this session runs. Every scoped query
       below therefore carries its tenant predicate by hand, and the endpoints that are
       deliberately cross-tenant say so and gate on ``aml:read`` unconditionally.

    service_role rather than ``authenticated`` because the console is cross-tenant by design and
    an ops principal's lineage covers only the operations tenant; claims are still published to
    ``request.jwt.claims`` so the audit triggers on these tables stamp the real actor.
    """
    if principal.organisation_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organisation context for this principal",
        )
    async with SessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        # Identity comes from the DB's own resolver against the verified token, never from a
        # client-supplied id: `?tenant=` selects what to LOOK at, never who the caller is.
        resolved = (
            await session.execute(
                text("SELECT auth.resolve_claims(:wos_user, :wos_org, :client_id)"),
                {
                    "wos_user": principal.user_id,
                    "wos_org": principal.organisation_id,
                    "client_id": principal.client_id,
                },
            )
        ).scalar_one()
        if not resolved or resolved.get("tenant_id") is None or not resolved.get("user_uuid"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No resolvable tenant/identity context for this principal",
            )
        own_tenant_id = int(resolved["tenant_id"])
        target_tenant_id = own_tenant_id
        foreign = False
        if tenant is not None:
            row = (
                await session.execute(
                    text("SELECT id FROM auth.tenants WHERE uid = :uid"), {"uid": tenant}
                )
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown tenant")
            target_tenant_id = int(row)
            foreign = target_tenant_id != own_tenant_id
            if foreign:
                # Reaching into another firm's screening position is an operations act.
                require_permission(principal, AML_READ)
        claims = {**build_jwt_claims(principal), **resolved}
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps(claims)},
        )
        yield AmlContext(
            session=session,
            tenant_id=target_tenant_id,
            targeted=tenant is not None,
            foreign=foreign,
            principal=principal,
            user_id=str(resolved["user_uuid"]),
            own_tenant_id=own_tenant_id,
        )


def _scope(ctx: AmlContext) -> int | None:
    """The tenant a ``?tenant=``-narrowed request is held to, or None for the cross-firm view."""
    return ctx.tenant_id if ctx.targeted else None


# ── The firm's icon ───────────────────────────────────────────────────────────────────────────

#: An icon longer than this is withheld (None) rather than repeated on every row of a page: an
#: inline ``data:`` URI (the no-bucket fallback in ``app/storage.py``) may run to ~1.4M characters.
#: The same limit as the audit stream's firm chip (``audit_stream._icon_sql``).
_ICON_MAX_CHARS = 65536


def _platform_icon(theme: object) -> str | None:
    """A firm's icon from its platform-portal branding, as ``GET /admin/tenants/{uid}/branding
    ?portal=platform`` serves it (``branding.get_branding``): the stored value through
    ``storage.public_asset_src``. None when the firm set none."""
    try:
        stored = _jsonb(theme)
    except ValueError:
        stored = {}
    icon = branding.parse_lenient(stored or None, "platform").icon_url
    if not icon:
        return None
    src = storage.public_asset_src(icon)
    return src if len(src) <= _ICON_MAX_CHARS else None


async def _tenant_icons(session: AsyncSession, tenant_ids: Iterable[int]) -> dict[int, str | None]:
    """Each named firm's icon (``_platform_icon``), in one read for a whole page."""
    ids = sorted(set(tenant_ids))
    if not ids:
        return {}
    rows = (
        await session.execute(
            text("SELECT id, theme FROM auth.tenants WHERE id = ANY(:ids)"), {"ids": ids}
        )
    ).all()
    return {int(row.id): _platform_icon(row.theme) for row in rows}


# ── The client list ───────────────────────────────────────────────────────────────────────────

#: The legacy key of a portal client due a first screen: ``contact-<contact uid>``. No answer
#: carries it any more; ``GET /aml/clients/{key}`` still takes it, so an old link still opens.
CONTACT_KEY_PREFIX = "contact-"

#: Risk levels in order, for the risk sort. ``not_set`` and no profile sort lowest.
_RISK_RANK = (
    "CASE q.risk WHEN 'very_high' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 "
    "WHEN 'low' THEN 1 ELSE 0 END"
)

# The columns the list may be ORDERED by, keyed as the admin table keys them. A CLOSED SET: `sort`
# arrives from a query string, so anything not named here is ignored.
_CLIENT_SORTS: dict[str, str] = {
    "client": "q.client_name",
    "firm": "q.tenant_name",
    "risk": _RISK_RANK,
    "updated": "q.updated_at",
}

# The narrowings the client list and the work queue share, over a subquery `q` with `tenant_id` and
# `client_name`. ESCAPE names the escape character explicitly; without it the backslashes added by
# `listing.escape_like` are ordinary characters to Postgres.
_TENANT_NARROWING = "(CAST(:tenant_id AS int) IS NULL OR q.tenant_id = CAST(:tenant_id AS int))"
_NAME_NARROWING = "(CAST(:q AS text) IS NULL OR q.client_name ILIKE :q ESCAPE '\\')"

# Every narrowing the list takes.
_CLIENT_NARROWING = " AND ".join(
    [
        _TENANT_NARROWING,
        _NAME_NARROWING,
        "(CAST(:contact AS text) IS NULL OR q.contact_uid = CAST(:contact AS text))",
    ]
)


def _client_item(row: Any, icons: dict[int, str | None]) -> dict[str, object]:
    """One list row: the client, the latest check of each kind, the risk level and whether anybody
    needs to act. A client due a screen reads ``awaiting_screen`` for AML, whatever came before.
    ``icons`` is the page's firm icons (``_tenant_icons``)."""
    identity: dict[str, object] | None = None
    if row["identity_uid"]:
        identity = {
            "uid": row["identity_uid"],
            "status": row["identity_status"],
            "attempt": row["identity_attempt"],
            "decided": bool(row["identity_decided"]),
            "updated_at": row["identity_updated_at"],
        }
    aml: dict[str, object] | None = None
    if row["awaiting"]:
        aml = {
            "uid": None,
            "status": "awaiting_screen",
            "decided": False,
            "monitoring": False,
            "updated_at": row["awaiting_since"],
            # The worker has the screen queued: it starts by itself, nobody needs to act.
            "queued": bool(row["screen_queued"]),
        }
    elif row["aml_uid"]:
        aml = {
            "uid": row["aml_uid"],
            "status": row["aml_status"],
            "decided": bool(row["aml_decided"]),
            "monitoring": bool(row["aml_monitoring"]),
            "updated_at": row["aml_updated_at"],
        }
    return {
        "key": row["key"],
        "client_name": row["client_name"],
        "contact_uid": row["contact_uid"],
        "tenant_uid": row["tenant_uid"],
        "tenant_name": row["tenant_name"],
        "tenant_icon_url": icons.get(int(row["tenant_id"])),
        "origin": "portal" if row["portal"] else "partner",
        "identity": identity,
        "aml": aml,
        "stale": bool(row["stale"]),
        "risk": row["risk"],
        "needs_attention": bool(row["needs_attention"]),
        "updated_at": row["updated_at"],
    }


@router.get("/clients", name="list-aml-clients")
async def list_clients(
    ctx: AmlContext = Depends(aml_session),
    # Annotated rather than `page: int = Query(1, ge=1)`: the latter makes the default the
    # `Query(...)` sentinel object, so any direct call to this coroutine (tests) gets a Query where
    # it expects an int.
    page: Annotated[int, Query(ge=1)] = 1,
    q: str | None = None,
    contact: str | None = None,
    sort: str | None = None,
    dir: str | None = None,  # noqa: A002 - the wire name the portal sends
) -> dict[str, object]:
    """Every client Graphene screens, one row each.

    Each row's ``key`` opens the client page: the client's contact uid, or the compliance client
    uid of a partner client with no contact (``attention.CLIENT_KEY``).

    Deliberately cross-tenant, so it is gated on ``aml:read`` unconditionally — ``?tenant=<uid>``
    narrows it to one firm, and its absence means every firm. ``q`` searches the client's name;
    ``contact`` narrows to one firm client, which is the admin client record's Compliance tab.

    Clients that need a human come first, then the most recently updated. ``sort``/``dir`` order the
    whole match set against the closed ``_CLIENT_SORTS`` map instead; anything else is ignored.
    """
    require_permission(ctx.principal, AML_READ)
    needle = listing.clean(q)
    params: dict[str, object] = {
        "tenant_id": _scope(ctx),
        "limit": PAGE_SIZE,
        "offset": (page - 1) * PAGE_SIZE,
        # The wildcards are added HERE so the value stays a bound parameter. The escaping runs
        # first, or a client literally called "100%" would match every row.
        "q": f"%{listing.escape_like(needle)}%" if needle else None,
        "contact": listing.clean(contact),
        "give_up": GIVE_UP.total_seconds(),
    }
    source = f"FROM ({attention.CLIENT_ROWS}) q WHERE {_CLIENT_NARROWING}"  # noqa: S608 - fixed SQL only
    total = int(
        (await ctx.session.execute(text(f"SELECT count(*) {source}"), params)).scalar_one()  # noqa: S608
    )
    sort_column = _CLIENT_SORTS.get(sort or "")
    if sort_column:
        heading = "DESC" if (dir or "").lower() == "desc" else "ASC"
        order = f"{sort_column} {heading} NULLS LAST, q.key"
    else:
        order = "q.needs_attention DESC, q.updated_at DESC NULLS LAST, q.key"
    rows = (
        (
            await ctx.session.execute(
                text(f"SELECT q.* {source} ORDER BY {order} LIMIT :limit OFFSET :offset"),  # noqa: S608
                params,
            )
        )
        .mappings()
        .all()
    )
    icons = await _tenant_icons(ctx.session, (int(row["tenant_id"]) for row in rows))
    return {
        "items": [_client_item(row, icons) for row in rows],
        "total": total,
        "page_size": PAGE_SIZE,
    }


# ── The work queue ────────────────────────────────────────────────────────────────────────────

#: The most clients one read of the queue returns. The queue is worked from the front, and the page
#: moves through what it holds, so a longer queue says it was cut (``truncated``) rather than
#: paging.
WORK_LIMIT = 500


def _work_narrowing(reason: str | None) -> str:
    """The queue's narrowings over ``q``: the firm, the name, the ``attention.WORK_FILTERS`` key
    (anything not in that closed map is ignored), and only clients that need a person."""
    parts = [_TENANT_NARROWING, _NAME_NARROWING, "q.needs_attention"]
    if reason in attention.WORK_FILTERS:
        parts.append(attention.work_filter(reason))
    return " AND ".join(parts)


def _work_params(ctx: AmlContext, q: str | None) -> dict[str, object]:
    needle = listing.clean(q)
    return {
        "tenant_id": _scope(ctx),
        "q": f"%{listing.escape_like(needle)}%" if needle else None,
        "give_up": GIVE_UP.total_seconds(),
    }


def _work_item(row: Any, icons: dict[int, str | None], now: datetime) -> dict[str, object]:
    """One client in the queue: who, which firm, how long they have waited, and why."""
    reasons = attention.reasons_of(row)
    return {
        "key": row["key"],
        "name": row["client_name"],
        "type": row["client_type"],
        "firm": {
            "uid": row["tenant_uid"],
            "name": row["tenant_name"],
            "icon_url": icons.get(int(row["tenant_id"])),
        },
        "origin": "portal" if row["portal"] else "partner",
        "waiting_since": row["waiting_since"],
        "age": attention.age_of(row["waiting_since"], now),
        "reasons": reasons,
        "steps": attention.steps_of(row, reasons),
    }


@router.get("/work", name="list-aml-work")
async def list_work(
    ctx: AmlContext = Depends(aml_session),
    reason: str | None = None,
    q: str | None = None,
) -> dict[str, object]:
    """Every client that needs a person, the longest wait first (``attention``): the same clients
    the list flags as needing attention, each with its reasons, steps and age.

    Cross-tenant like the list, and gated on ``aml:read``. ``?tenant=<uid>`` narrows it to one
    firm, ``q`` searches the client's name, and ``reason`` keeps one ``attention.WORK_FILTERS``
    key. At most ``WORK_LIMIT`` clients; ``total`` counts them all and ``truncated`` says the
    answer was cut.
    """
    require_permission(ctx.principal, AML_READ)
    params = {**_work_params(ctx, q), "limit": WORK_LIMIT}
    source = f"FROM ({attention.CLIENT_ROWS}) q WHERE {_work_narrowing(reason)}"  # noqa: S608
    total = int(
        (await ctx.session.execute(text(f"SELECT count(*) {source}"), params)).scalar_one()  # noqa: S608
    )
    rows = (
        (
            await ctx.session.execute(
                text(f"SELECT q.* {source} ORDER BY q.waiting_since, q.key LIMIT :limit"),  # noqa: S608
                params,
            )
        )
        .mappings()
        .all()
    )
    icons = await _tenant_icons(ctx.session, (int(row["tenant_id"]) for row in rows))
    now = datetime.now(UTC)
    items = [_work_item(row, icons, now) for row in rows]
    return {"items": items, "total": total, "truncated": total > len(items)}


@router.get("/work/summary", name="get-aml-work-summary")
async def work_summary(
    ctx: AmlContext = Depends(aml_session),
    q: str | None = None,
) -> dict[str, object]:
    """How much work there is, for the queue's filter counts: every client that needs a person
    (``total``), how many fall under each ``attention.WORK_FILTERS`` key (``by_reason``; a client
    can fall under more than one), and how many have waited past ``attention.OVERDUE_AFTER``
    working days (``overdue``). Narrowed as ``GET /aml/work`` is, by ``?tenant=`` and ``q``."""
    require_permission(ctx.principal, AML_READ)
    counts = ", ".join(f"q.{code}_count" for code in attention.REASONS)
    rows = (
        (
            await ctx.session.execute(
                text(
                    f"SELECT q.waiting_since, {counts} "  # noqa: S608 - fixed SQL only
                    f"FROM ({attention.CLIENT_ROWS}) q WHERE {_work_narrowing(None)}"
                ),
                _work_params(ctx, q),
            )
        )
        .mappings()
        .all()
    )
    now = datetime.now(UTC)
    by_reason = dict.fromkeys(attention.WORK_FILTERS, 0)
    overdue = 0
    for row in rows:
        for key in attention.filters_of(row):
            by_reason[key] += 1
        age = attention.age_of(row["waiting_since"], now)
        if age is not None and age["band"] == "overdue":
            overdue += 1
    return {"total": len(rows), "by_reason": by_reason, "overdue": overdue}


# ── One check ─────────────────────────────────────────────────────────────────────────────────

# What `_client_block` reads, over `cl` (compliance.clients), `c` (crm.contacts),
# `a` (compliance.addresses) and `t` (auth.tenants). The adviser is the contact's adviser
# (`crm.client_advisers.role = 'adviser'`, unique per contact since 0245 and the one presented as
# primary throughout the portals).
_CLIENT_BLOCK_COLUMNS = f"""
       cl.entity_name AS cl_entity_name, cl.first_name AS cl_first_name,
       cl.last_name AS cl_last_name, cl.company_name AS cl_company_name, cl.dob AS cl_dob,
       cl.nationality AS cl_nationality, cl.email AS cl_email, cl.mobile AS cl_mobile,
       cl.telephone AS cl_telephone, cl.gender AS cl_gender, cl.joined_date AS cl_joined_date,
       cl.created_at AS cl_created_at, {attention.CLIENT_TYPE} AS client_type,
       c.uid AS contact_uid, {tasks.CONTACT_NAME} AS c_name,
       c.dob AS c_dob, c.nationality AS c_nationality, c.address AS c_address,
       c.title AS c_title, c.email AS c_email,
       {phone_expr("c.mobile")} AS c_mobile, {phone_expr("c.phone")} AS c_phone,
       c.gender AS c_gender, c.occupation AS c_occupation,
       c.employment_status AS c_employment_status, c.created_at AS c_created_at,
       (SELECT COALESCE(NULLIF(trim(u.name), ''), u.email)
          FROM crm.client_advisers ca JOIN auth.users u ON u.id = ca.adviser_id
         WHERE ca.contact_id = c.id AND ca.role = 'adviser') AS c_adviser,
       -- The whole team, adviser first: `role = 'adviser'` is unique per contact (0245), the
       -- other roles are not, so the tie-break still matters for everyone after the adviser.
       (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                 'uid', u.uid,
                 'name', COALESCE(NULLIF(trim(u.name), ''),
                                  NULLIF(trim(concat_ws(' ', u.first_name, u.last_name)), ''),
                                  u.email),
                 'email', u.email,
                 'phone', COALESCE(NULLIF(trim(u.direct_phone), ''), NULLIF(trim(u.mobile), '')),
                 'job_title', NULLIF(trim(u.job_title), ''),
                 'fca_reference', NULLIF(trim(u.fca_reference), ''),
                 'photo_url', COALESCE(NULLIF(trim(u.photo_url), ''), NULLIF(trim(u.avatar), '')),
                 'role', ca.role,
                 'is_adviser', u.is_adviser)
               ORDER BY (ca.role = 'adviser') DESC, u.name NULLS LAST, ca.id), '[]')
          FROM crm.client_advisers ca JOIN auth.users u ON u.id = ca.adviser_id
         WHERE ca.contact_id = c.id AND u.deleted_at IS NULL) AS c_advisers,
       a.building_name AS a_building_name, a.property_number AS a_property_number,
       a.line AS a_line, a.city AS a_city, a.postal_code AS a_postal_code,
       a.country AS cl_country,
       t.uid AS tenant_uid, t.name AS tenant_name
"""  # noqa: S608 - the only interpolations are the fixed name and phone expressions

_CHECK = f"""
SELECT ch.id, ch.uid, ch.kind, ch.origin, ch.status, ch.outcome, ch.attempt, ch.monitoring,
       ch.stale_at, ch.decided_by, ch.decided_at, ch.decision_comment, ch.provider_check_id,
       ch.created_at, ch.updated_at, ch.tenant_id, ch.client_id, ch.kyc_check_id,
       ch.document_id, ch.provider_document_id, ch.provider_live_photo_id, ch.result,
       ch.uploaded_by, ch.uploaded_via, ch.upload_comment,
       cl.contact_id, cl.provider_client_id, {_CLIENT_BLOCK_COLUMNS}
  FROM compliance.checks ch
  JOIN compliance.clients cl ON cl.id = ch.client_id
  JOIN auth.tenants t ON t.id = ch.tenant_id
  LEFT JOIN crm.contacts c ON c.id = cl.contact_id
  LEFT JOIN compliance.addresses a ON a.client_id = cl.id
 WHERE ch.uid = :uid
   AND (CAST(:tenant_id AS int) IS NULL OR ch.tenant_id = CAST(:tenant_id AS int))
"""  # noqa: S608 - the only interpolation is the fixed column list above


async def _check_row(ctx: AmlContext, uid: str, *, lock: bool = False) -> dict[str, Any]:
    """The check with its client and firm, or 404. ``lock`` holds the check row for a write.

    Cross-tenant by uid for the same reason the list is (a list row must be openable); a session
    narrowed with ``?tenant=`` stays narrowed.
    """
    sql = _CHECK + (" FOR UPDATE OF ch" if lock else "")
    row = (
        (await ctx.session.execute(text(sql), {"uid": uid, "tenant_id": _scope(ctx)}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown check")
    return dict(row)


def _iso(value: object) -> str | None:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value) if value else None


def _jsonb(value: object) -> dict[str, Any]:
    loaded = json.loads(value) if isinstance(value, str) else value
    return loaded if isinstance(loaded, dict) else {}


def _part(value: object) -> str | None:
    """One stored scalar as display text: trimmed, or None when empty or not a scalar."""
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    return str(value).strip() or None


def _address_line(*parts: object) -> str | None:
    """An address as one display line: its non-empty parts, comma separated."""
    return ", ".join(part for part in map(_part, parts) if part) or None


def _day(value: object) -> str | None:
    """A date or timestamp as ``YYYY-MM-DD``."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat() if isinstance(value, date) else None


def _client_block(row: dict[str, Any], icons: dict[int, str | None]) -> dict[str, object]:
    """Who the check is about. A portal client reads the firm's own record (``crm.contacts``)
    first; a partner client reads what the firm API sent (``compliance.clients`` and
    ``compliance.addresses``) first. Either falls back to the other, and anything neither holds is
    None. ``icons`` holds the firm's icon (``_tenant_icons``) under ``row["tenant_id"]``.

    ``phone`` is the contact's mobile, else their other number; ``address`` is
    one display line; ``occupation`` is the contact's occupation, else its employment status;
    ``client_since`` is the date the contact was created, or for a partner client the date it
    joined the firm, else the date ComplyCube's client was first stored; ``adviser`` is the
    contact's adviser by name, and ``advisers`` everyone on the contact's team, adviser first, with
    how to reach them (name, email, phone, job title, FCA reference, photo, role). These are for
    Graphene's operators only: no firm-facing router reads them.
    """
    portal = row["origin"] == "portal" and row["contact_uid"] is not None
    contact_name = row["c_name"] or ""
    provider_name = (
        (row["cl_entity_name"] or "").strip()
        or " ".join(part for part in (row["cl_first_name"], row["cl_last_name"]) if part)
        or (row["cl_company_name"] or "")
    )
    nationalities = row["c_nationality"] if isinstance(row["c_nationality"], list) else []
    address = _jsonb(row["c_address"])
    contact_address = _address_line(
        address.get("line1"),
        address.get("line2"),
        address.get("city"),
        address.get("postcode") or address.get("postalCode"),
        address.get("country"),
    )
    provider_street = " ".join(
        part for part in (_part(row["a_property_number"]), _part(row["a_line"])) if part
    )
    provider_address = _address_line(
        row["a_building_name"],
        provider_street,
        row["a_city"],
        row["a_postal_code"],
        row["cl_country"],
    )
    contact_phone = _part(row["c_mobile"]) or _part(row["c_phone"])
    provider_phone = _part(row["cl_mobile"]) or _part(row["cl_telephone"])
    contact_since = _day(row["c_created_at"])
    provider_since = _day(row["cl_joined_date"]) or _day(row["cl_created_at"])
    if portal:
        name = contact_name or provider_name
        dob = _iso(row["c_dob"]) or _iso(row["cl_dob"])
        nationality = (
            ", ".join(str(code) for code in nationalities if code) or row["cl_nationality"]
        )
        country = address.get("country") or row["cl_country"]
        email = _part(row["c_email"]) or _part(row["cl_email"])
        phone = contact_phone or provider_phone
        address_line = contact_address or provider_address
        gender = _part(row["c_gender"]) or _part(row["cl_gender"])
        client_since = contact_since or provider_since
    else:
        name = provider_name or contact_name
        dob = _iso(row["cl_dob"])
        nationality = row["cl_nationality"]
        country = row["cl_country"]
        email = _part(row["cl_email"]) or _part(row["c_email"])
        phone = provider_phone or contact_phone
        address_line = provider_address or contact_address
        gender = _part(row["cl_gender"]) or _part(row["c_gender"])
        client_since = provider_since or contact_since
    employment = _part(row["c_employment_status"])
    return {
        "name": name,
        # `individual` or `organisation` (`attention.CLIENT_TYPE`); None when neither record says.
        "type": row.get("client_type"),
        "title": _part(row["c_title"]),
        "dob": dob,
        "nationality": nationality or None,
        "country": str(country) if country else None,
        "email": email,
        "phone": phone,
        "address": address_line,
        "gender": gender,
        "occupation": _part(row["c_occupation"]) or (_humanise(employment) if employment else None),
        "client_since": client_since,
        "adviser": _part(row["c_adviser"]),
        "advisers": _advisers(row.get("c_advisers")),
        "contact_uid": row["contact_uid"] if row["origin"] == "portal" else None,
        "tenant_uid": row["tenant_uid"],
        "tenant_name": row["tenant_name"],
        "tenant_icon_url": icons.get(int(row["tenant_id"])),
    }


# A match's own detail, read from `compliance.matches.raw`. The field names are ComplyCube's, from
# the "matches array" of its AML Screening Check reference
# (https://docs.complycube.com/documentation/api-reference/check-types/aml-screening-check):
#   {id, score, status, details: {entityName, entityType, gender, deceased, names,
#    countries: [{type, country}], dates: [{type, date | data: {day, month, year}}],
#    addresses: [{line, city, postalCode, country}], references: [{source, fromDate, toDate}],
#    classifications: {<summary group>: [<summary value>, ...]}}, breakdown}
# The summary groups are `pep`, `watchlist`, `adverseMedia` and `otherLists`. That reference gives a
# reference no URL; one is passed on only when ComplyCube sends `url`, `link` or `sourceUrl`.

#: How each classification group reads in front of its values.
_CATEGORY_GROUPS: dict[str, str] = {
    "pep": "PEP",
    "watchlist": "Watchlist",
    "adverseMedia": "Adverse media",
    "otherLists": "Other lists",
}

#: Our own `match_types` values that are not plain words.
_MATCH_TYPE_WORDS: dict[str, str] = {"pep": "PEP"}


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _dicts(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def structured_date(value: object) -> str | None:
    """ComplyCube's structured date (``{day, month, year}``, any part may be missing) as
    ``YYYY-MM-DD``, ``YYYY-MM`` or ``YYYY``. A plain string is passed through."""
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None
    parts: list[str] = []
    for key, width in (("year", 4), ("month", 2), ("day", 2)):
        part = value.get(key)
        digits = (
            str(part).strip() if isinstance(part, str | int) and not isinstance(part, bool) else ""
        )
        if not digits.isdigit():
            break
        parts.append(digits.zfill(width))
    return "-".join(parts) or None


def _category_words(group: str, value: str | None) -> str:
    """``pep``/``level1`` → "PEP level 1"; ``watchlist``/``sanctionsLists`` → "Sanctions lists"."""
    group_words = _CATEGORY_GROUPS.get(group) or _label(group)
    if not value:
        return group_words
    words = _label(re.sub(r"(?<=[A-Za-z])(?=\d)", " ", value))
    if group == "pep":
        return f"{group_words} {words.lower()}"
    if group in {"watchlist", "otherLists"}:
        return words
    return f"{group_words}: {words.lower()}"


def _categories(details: dict[str, Any], match_types: list[str]) -> list[str]:
    """The match's categories as words: ComplyCube's classifications, then any of our own
    ``match_types`` they do not already say."""
    out: list[str] = []
    classifications = details.get("classifications")
    if isinstance(classifications, dict):
        for group, values in classifications.items():
            if not isinstance(group, str) or not group:
                continue
            if isinstance(values, str):
                named = [values] if values else []
            elif isinstance(values, list):
                named = [value for value in values if isinstance(value, str) and value]
            else:
                named = []
            if named:
                out.extend(_category_words(group, value) for value in named)
            else:
                out.append(_category_words(group, None))
    for value in match_types:
        out.append(_MATCH_TYPE_WORDS.get(value.lower()) or _label(value))
    return list(dict.fromkeys(word for word in out if word))


def _safe_url(value: object) -> str | None:
    """A link an operator can open: http(s) only, so a ``javascript:`` value never becomes one."""
    url = _text(value)
    return url if url and url.lower().startswith(("https://", "http://")) else None


def match_facts(raw: object, match_types: list[str] | None = None) -> dict[str, Any]:
    """What a match's raw ComplyCube item says beyond our columns. Never fails on a missing or
    malformed key: anything it cannot read is left empty."""
    try:
        item = _jsonb(raw)
    except ValueError:
        item = {}
    details = item.get("details")
    details = details if isinstance(details, dict) else item
    dob = None
    for entry in _dicts(details.get("dates")):
        kind = _text(entry.get("type")) or ""
        if "birth" in kind.lower():
            dob = structured_date(entry.get("date", entry.get("data")))
            if dob:
                break
    countries = list(
        dict.fromkeys(
            code
            for code in (_text(entry.get("country")) for entry in _dicts(details.get("countries")))
            if code
        )
    )
    addresses = []
    for entry in _dicts(details.get("addresses")):
        address = {
            "line": _text(entry.get("line")),
            "city": _text(entry.get("city")),
            "postal_code": _text(entry.get("postalCode")),
            "country": _text(entry.get("country")),
        }
        if any(address.values()):
            addresses.append(address)
    references = []
    for entry in _dicts(details.get("references")):
        source = _text(entry.get("source")) or _text(entry.get("name"))
        url = _safe_url(entry.get("url") or entry.get("link") or entry.get("sourceUrl"))
        if not source and not url:
            continue
        references.append(
            {
                "source": source,
                "date": structured_date(entry.get("fromDate")),
                "to_date": structured_date(entry.get("toDate")),
                "url": url,
            }
        )
    gender = _text(details.get("gender"))
    return {
        "name": _text(details.get("entityName")),
        "gender": gender,
        "dob": dob,
        "countries": countries,
        "addresses": addresses,
        "references": references,
        "categories": _categories(details, match_types or []),
        "score": _score_of(item.get("score")),
    }


#: How many of each list a match carries to the console: every real profile fits, and a
#: pathological one cannot bloat the page.
_PROFILE_LIMITS = {"images": 10, "names": 30, "roles": 50, "associations": 50, "links": 50}

#: The longest narrative passed on, in characters.
_NOTES_LIMIT = 10_000


def _words(value: object) -> str | None:
    """ComplyCube's ``localised_name`` as "Localised name"; a value already in words is kept."""
    found = _text(value)
    if found is None:
        return None
    return _label(found) if "_" in found or found.islower() else found


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _links(value: object, limit: int) -> list[str]:
    """The http(s) URLs in a list, once each, at most ``limit``."""
    if not isinstance(value, list):
        return []
    found = (_safe_url(item) for item in value)
    return list(dict.fromkeys(url for url in found if url))[:limit]


def _listed_categories(value: object) -> list[str]:
    """An associate's classifications (``[{type, subType}]``) as words."""
    words = (
        _category_words(entry["type"], _text(entry.get("subType")))
        for entry in _dicts(value)
        if isinstance(entry.get("type"), str) and entry["type"]
    )
    return list(dict.fromkeys(words))


def match_profile(raw: object) -> dict[str, Any]:
    """Everything else ComplyCube says about who it matched, for the operator deciding the match:
    photos, every name, roles held, where they were born, the countries and dates on record and
    why, associates, the narrative, the source articles, and whether the profile is active.

    The photos and links point at third-party sites; the console opens them without a referrer.
    Never fails on a missing or malformed key: anything it cannot read is left empty.
    """
    try:
        item = _jsonb(raw)
    except ValueError:
        item = {}
    details = item.get("details")
    details = details if isinstance(details, dict) else item
    primary = _text(details.get("entityName"))

    names: list[dict[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()
    for entry in _dicts(details.get("names")):
        parts = (_text(entry.get(key)) for key in ("firstName", "middleName", "lastName"))
        name = _text(entry.get("entityName")) or _text(" ".join(p for p in parts if p))
        kind = _words(entry.get("type"))
        if name and (name, kind) not in seen:
            seen.add((name, kind))
            names.append({"name": name, "type": kind})
    names = names[: _PROFILE_LIMITS["names"]]
    aliases = list(
        dict.fromkeys(
            n["name"]
            for n in names
            if n["name"] and n["name"] != primary and n["type"] != "Primary"
        )
    )

    roles: list[dict[str, Any]] = []
    for entry in _dicts(details.get("occupations")):
        title = _text(entry.get("title"))
        # "See Previous Roles" is ComplyCube's pointer to the list, not a role.
        if not title or title.lower() == "see previous roles":
            continue
        kind = _text(entry.get("type"))
        ended = structured_date(entry.get("toDate"))
        roles.append(
            {
                "title": title,
                "category": _text(entry.get("category")),
                "type": kind,
                "from": structured_date(entry.get("fromDate")),
                "to": ended,
                "current": ended is None and (kind or "").lower() != "previous roles",
            }
        )
    # Current roles first, then the latest start first; a role with no start date last. (Two
    # stable sorts: the later one wins ties of the earlier.)
    roles.sort(key=lambda r: r["from"] or "", reverse=True)
    roles.sort(key=lambda r: not r["current"])
    roles = roles[: _PROFILE_LIMITS["roles"]]

    places = details.get("birthPlaces")
    # ComplyCube writes "Irvine,United Kingdom".
    birth_places = [
        re.sub(r",(?=\S)", ", ", place)
        for place in (map(_text, places) if isinstance(places, list) else [])
        if place
    ]

    country_details = [
        {"country": code, "type": _text(entry.get("type"))}
        for entry in _dicts(details.get("countries"))
        if (code := _text(entry.get("country")))
    ]
    dates = [
        {"type": _text(entry.get("type")) or "Date", "date": when}
        for entry in _dicts(details.get("dates"))
        if (when := structured_date(entry.get("date", entry.get("data"))))
    ]

    associations: list[dict[str, Any]] = []
    for entry in _dicts(details.get("associations")):
        name = _text(entry.get("entityName"))
        if not name:
            continue
        images = _links(entry.get("images"), 1)
        associations.append(
            {
                "name": name,
                "relationship": _text(entry.get("relationship")),
                "current": _flag(entry.get("current")),
                "categories": _listed_categories(entry.get("classifications")),
                "entity_type": _text(entry.get("entityType")),
                "image": images[0] if images else None,
            }
        )
    associations = associations[: _PROFILE_LIMITS["associations"]]

    notes = _text(details.get("notes"))
    types = details.get("types")
    return {
        "images": _links(details.get("images"), _PROFILE_LIMITS["images"]),
        "names": names,
        "aliases": aliases,
        "roles": roles,
        "birth_places": birth_places,
        "country_details": country_details,
        "dates": dates,
        "entity_type": _text(details.get("entityType")),
        "notes": notes[:_NOTES_LIMIT] if notes else None,
        "associations": associations,
        "source_links": _links(details.get("sourceLinks"), _PROFILE_LIMITS["links"]),
        "types": list(dict.fromkeys(t for t in map(_text, types) if t))
        if isinstance(types, list)
        else [],
        "active": _flag(details.get("active")),
        "deceased": _flag(details.get("deceased")),
        "last_reviewed": _text(details.get("lastReviewedDate")),
    }


def _score_of(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Decimal | int | float):
        return None
    return float(value)


#: The ``compliance.matches`` columns ``match_item`` reads, under their own names.
_MATCH_COLUMNS = (
    "uid",
    "name",
    "match_types",
    "score",
    "dob",
    "countries",
    "sources",
    "raw",
    "decision",
    "decided_by",
    "decided_at",
    "comment",
)


def match_item(row: Any) -> dict[str, object]:
    """One match as the console shows it: our columns, with the raw ComplyCube item filling what
    they leave empty."""
    match_types = list(row["match_types"] or [])
    facts = match_facts(row["raw"], match_types)
    score = _score_of(row["score"])
    return {
        "uid": row["uid"],
        "name": row["name"] or facts["name"] or "Unnamed match",
        "match_types": match_types,
        "categories": facts["categories"],
        "score": score if score is not None else facts["score"],
        "dob": row["dob"] or facts["dob"],
        "gender": facts["gender"],
        "countries": list(row["countries"] or []) or facts["countries"],
        "addresses": facts["addresses"],
        "sources": list(row["sources"] or []),
        "references": facts["references"],
        "decision": row["decision"],
        "decided_by": row["decided_by"],
        "decided_at": row["decided_at"],
        "comment": row["comment"],
        **match_profile(row["raw"]),
    }


#: What a firm never reads of a match: Graphene's decision on it, and why.
_DECISION_KEYS = ("decision", "decided_by", "decided_at", "comment")


def firm_match_item(row: Any) -> dict[str, object]:
    """One match as a firm reads it once Graphene let the journey continue past it: the console's
    view (``match_item``), photos, names, roles and sources included, without Graphene's decision
    or its comment."""
    item = match_item(row)
    for key in _DECISION_KEYS:
        item.pop(key, None)
    return item


def match_columns(alias: str) -> str:
    """The ``compliance.matches`` columns one match is built from, under a table alias."""
    return ", ".join(f"{alias}.{column}" for column in _MATCH_COLUMNS)


async def _matches(session: AsyncSession, check_pk: int) -> list[dict[str, object]]:
    rows = (
        (
            await session.execute(
                text(
                    f"SELECT {', '.join(_MATCH_COLUMNS)} "  # noqa: S608 - fixed SQL only
                    "FROM compliance.matches WHERE check_id = :id "
                    # Undecided first, then by match strength: the review order.
                    "ORDER BY (decision IS NULL) DESC, score DESC NULLS LAST, id"
                ),
                {"id": check_pk},
            )
        )
        .mappings()
        .all()
    )
    return [match_item(row) for row in rows]


#: Breakdown keys that carry the client's own data rather than an outcome. Never echoed.
_BREAKDOWN_SKIP = frozenset({"extractedData", "extracted_data", "matches"})


def _label(key: str) -> str:
    """``documentAuthenticity`` / ``face_match`` as words."""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key).replace("_", " ").replace(".", " ")
    words = " ".join(words.split()).lower()
    return words[:1].upper() + words[1:]


def identity_breakdown(result: object) -> list[dict[str, str]]:
    """The identity check's per-part outcomes, from ``result.breakdown``.

    Read tolerantly until the sandbox pins the shape: a mapping of part → outcome string, part →
    ``{"outcome": ...}`` (with an optional nested ``breakdown`` one level down), or a list of
    ``{"key"|"name"|"type", "outcome"}``. Anything without an outcome is left out, and so are the
    parts that carry the client's extracted data.
    """
    root = _jsonb(result)
    breakdown = root.get("breakdown")
    out: list[dict[str, str]] = []

    def add(key: str, value: object, depth: int) -> None:
        if key in _BREAKDOWN_SKIP:
            return
        if isinstance(value, str) and value:
            out.append({"key": key, "label": _label(key.rsplit(".", 1)[-1]), "outcome": value})
            return
        if not isinstance(value, dict):
            return
        outcome = value.get("outcome") or value.get("result")
        if isinstance(outcome, str) and outcome:
            out.append({"key": key, "label": _label(key.rsplit(".", 1)[-1]), "outcome": outcome})
        nested = value.get("breakdown")
        if depth == 0 and isinstance(nested, dict):
            for sub, sub_value in nested.items():
                add(f"{key}.{sub}", sub_value, depth + 1)

    if isinstance(breakdown, dict):
        for key, value in breakdown.items():
            add(str(key), value, 0)
    elif isinstance(breakdown, list):
        for item in breakdown:
            if not isinstance(item, dict):
                continue
            name = item.get("key") or item.get("name") or item.get("type")
            if isinstance(name, str) and name:
                add(name, item, 1)
    return out


def _is_url(value: object) -> bool:
    return isinstance(value, str) and value.startswith(("https://", "http://"))


def _urls(value: object) -> list[str]:
    """URLs in a list of strings or of objects carrying ``url`` / ``link`` / ``source``."""
    if not isinstance(value, list):
        return []
    found: list[str] = []
    for item in value:
        if _is_url(item):
            found.append(str(item))
        elif isinstance(item, dict):
            for key in ("url", "link", "source", "sourceUrl"):
                if _is_url(item.get(key)):
                    found.append(str(item[key]))
                    break
    return found


def identity_analysis(result: object) -> dict[str, list[dict[str, Any]]]:
    """The identity check's reasons, laid out as ComplyCube's own analysis breakdown.

    ``result.breakdown`` holds one part per analysis (``faceAnalysis``, ``authenticityAnalysis``,
    ``integrityAnalysis``…). In each part, a string field is an outcome (``facialSimilarity``:
    ``attention``), and its nested ``breakdown`` holds the detail: a number is a score
    (``facialSimilarityScore``: 1, read as a percentage), a string is a fact
    (``documentSideUsed``: ``front``), and a list of URLs is a set of links (specimen matches).
    The parts that carry the client's extracted data are never echoed.

    Returns ``groups`` (``{key, label, items: [{key, label, outcome}]}``), ``scores``
    (``{key, label, value}``), ``facts`` (``{key, label, value}``) and ``links``
    (``{key, label, urls}``).
    """
    root = _jsonb(result)
    breakdown = root.get("breakdown")
    out: dict[str, list[dict[str, Any]]] = {"groups": [], "scores": [], "facts": [], "links": []}
    if not isinstance(breakdown, dict):
        return out

    def detail(prefix: str, values: dict[str, Any]) -> None:
        for key, value in values.items():
            if key in _BREAKDOWN_SKIP:
                continue
            full = f"{prefix}.{key}"
            if isinstance(value, bool):
                out["facts"].append(
                    {"key": full, "label": _label(key), "value": "Yes" if value else "No"}
                )
            elif isinstance(value, int | float):
                out["scores"].append({"key": full, "label": _label(key), "value": value})
            elif isinstance(value, str) and value:
                if _is_url(value):
                    out["links"].append({"key": full, "label": _label(key), "urls": [value]})
                else:
                    out["facts"].append({"key": full, "label": _label(key), "value": value})
            elif isinstance(value, list) and _urls(value):
                out["links"].append({"key": full, "label": _label(key), "urls": _urls(value)})

    loose: list[dict[str, str]] = []
    for part, value in breakdown.items():
        part = str(part)
        if part in _BREAKDOWN_SKIP:
            continue
        if isinstance(value, str) and value:
            loose.append({"key": part, "label": _label(part), "outcome": value})
            continue
        if not isinstance(value, dict):
            continue
        items: list[dict[str, str]] = []
        for key, outcome in value.items():
            if key == "breakdown" or key in _BREAKDOWN_SKIP:
                continue
            if isinstance(outcome, str) and outcome:
                items.append({"key": f"{part}.{key}", "label": _label(key), "outcome": outcome})
            elif isinstance(outcome, list) and _urls(outcome):
                out["links"].append(
                    {"key": f"{part}.{key}", "label": _label(key), "urls": _urls(outcome)}
                )
        if items:
            out["groups"].append({"key": part, "label": _label(part), "items": items})
        nested = value.get("breakdown")
        if isinstance(nested, dict):
            detail(part, nested)
    if loose:
        out["groups"].append({"key": "other", "label": "Other", "items": loose})
    return out


def _held(row: dict[str, Any]) -> decisions.HeldCheck:
    """A check row as the shared identity decisions read it."""
    return decisions.HeldCheck(
        id=int(row["id"]),
        uid=str(row["uid"]),
        tenant_id=int(row["tenant_id"]),
        client_id=int(row["client_id"]),
        status=str(row["status"]),
        origin=str(row["origin"]),
        kind=str(row["kind"]),
        decided_at=row["decided_at"],
        provider_check_id=row["provider_check_id"],
        document_id=row["document_id"],
        provider_document_id=row["provider_document_id"],
        provider_live_photo_id=row["provider_live_photo_id"],
    )


async def _identity(
    ctx: AmlContext, row: dict[str, Any], *, images: bool = True
) -> dict[str, object] | None:
    """The identity block: the document type, the per-part outcomes, and the images.

    ``images`` lists the check's images ComplyCube holds (``decisions.identity_images``): each
    one's ``part``, label and ``content_type`` (a document side may be a PDF). The bytes are read
    from ComplyCube by ``GET /aml/checks/{uid}/images/{part}``. An image ComplyCube does not hold
    is not listed: an empty list is the honest answer. With ``images`` false nothing is listed:
    ``images`` and ``images_missing`` are None.
    """
    if row["kind"] != "identity":
        return None
    document = (
        (
            await ctx.session.execute(
                text(
                    "SELECT document_type, classification FROM compliance.documents "
                    "WHERE client_id = :client AND (id = :doc OR (provider_document_id = :pdoc "
                    "  AND CAST(:pdoc AS text) IS NOT NULL)) "
                    "ORDER BY (id = :doc) DESC LIMIT 1"
                ),
                {
                    "client": row["client_id"],
                    "doc": row["document_id"] or 0,
                    "pdoc": row["provider_document_id"],
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    listing = await decisions.identity_images(ctx.session, _held(row)) if images else None
    document_type = None
    if document:
        document_type = document["document_type"] or document["classification"]
    return {
        "document_type": document_type,
        "breakdown": identity_breakdown(row["result"]),
        "analysis": identity_analysis(row["result"]),
        "images": listing.images if listing else None,
        "images_missing": listing.missing if listing else None,
    }


_HISTORY_LABELS: dict[str, str] = {
    "aml.match_decided": "Match decided",
    "aml.check_decided": "Check decided",
    "aml.monitoring_updated": "Monitoring changed",
    "aml.check_rerun": "Run again",
    "aml.photos_requested": "New photos requested",
    "aml.journey_allowed": "Journey allowed to continue",
    actions.AML_IMAGES_VIEW_EVENT: "Identity images viewed",
}


def _humanise(value: str) -> str:
    words = value.replace(".", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def _photos_requested_detail(subject: dict[str, Any]) -> str | None:
    """Which photos an operator asked for, and what the client was told."""
    photos = subject.get("photos")
    named = (
        [str(photo) for photo in photos if photo in PHOTO_LABELS]
        if isinstance(photos, list)
        else []
    )
    parts: list[str] = []
    if named:
        parts.append(_humanise(" and ".join(PHOTO_LABELS[photo] for photo in named)))
    reason = subject.get("reason")
    if reason in identity.PHOTO_REQUEST_REASONS:
        told = identity.PHOTO_REQUEST_REASONS[reason]
        parts.append(f"Told: {told}" if told else "No reason given")
    return " · ".join(parts) or None


async def _history(ctx: AmlContext, row: dict[str, Any]) -> list[dict[str, object]]:
    """The check's own trail: when it started, every console act on it, and every provider event
    received for it. Oldest first, so the newest is last."""
    session = ctx.session
    entries: list[tuple[datetime, int, dict[str, object]]] = []
    started = (
        (
            await session.execute(
                text(
                    "SELECT l.created_at, COALESCE(NULLIF(trim(u.name), ''), u.email) AS actor "
                    "FROM audit.log l LEFT JOIN auth.users u ON u.id = l.user_id "
                    "WHERE l.entity_type = 'compliance.checks' AND l.entity_id = :id "
                    "  AND l.action = 'compliance.checks.insert' "
                    "ORDER BY l.id LIMIT 1"
                ),
                {"id": row["id"]},
            )
        )
        .mappings()
        .one_or_none()
    )
    started_detail = [f"Attempt {row['attempt']}"] if int(row["attempt"]) > 1 else []
    if row["uploaded_by"]:
        adviser = " (the firm's adviser)" if row["uploaded_via"] == "firm" else ""
        started_detail.append(f"Photos uploaded by {row['uploaded_by']}{adviser}")
    entries.append(
        (
            row["created_at"],
            0,
            {
                "at": row["created_at"],
                "actor": started["actor"] if started else None,
                "action": "Check started",
                "detail": " · ".join(started_detail) or None,
            },
        )
    )
    match_names = {
        str(found.uid): str(found.name or "Unnamed match")
        for found in (
            await session.execute(
                text("SELECT uid, name FROM compliance.matches WHERE check_id = :id"),
                {"id": row["id"]},
            )
        ).all()
    }
    acts = (
        (
            await session.execute(
                text(
                    "SELECT a.created_at, a.event_type, a.path_params, a.metadata, "
                    "       COALESCE(NULLIF(trim(u.name), ''), u.email) AS actor "
                    "FROM audit.actions a LEFT JOIN auth.users u ON u.id = a.actor_id "
                    # By event type first: that is what audit.actions is indexed on.
                    "WHERE a.event_type = ANY(CAST(:types AS text[])) "
                    "  AND a.path_params ->> 'uid' = :uid "
                    "ORDER BY a.id"
                ),
                {"uid": row["uid"], "types": sorted(_HISTORY_LABELS)},
            )
        )
        .mappings()
        .all()
    )
    for act in acts:
        event_type = str(act["event_type"])
        subject = _jsonb(_jsonb(act["metadata"]).get("subject"))
        path = _jsonb(act["path_params"])
        detail: str | None = None
        if event_type in {"aml.match_decided", "aml.check_decided"}:
            decision = subject.get("decision")
            name = match_names.get(str(path.get("match_uid") or ""))
            parts = [part for part in (name, decision) if isinstance(part, str) and part]
            detail = ": ".join(parts) or None
        elif event_type == "aml.monitoring_updated" and isinstance(subject.get("enabled"), bool):
            detail = "On" if subject["enabled"] else "Off"
        elif event_type == "aml.photos_requested":
            detail = _photos_requested_detail(subject)
        entries.append(
            (
                act["created_at"],
                1,
                {
                    "at": act["created_at"],
                    "actor": act["actor"],
                    "action": _HISTORY_LABELS.get(event_type, _humanise(event_type)),
                    "detail": detail,
                },
            )
        )
    if row["provider_check_id"]:
        received = (
            await session.execute(
                text(
                    "SELECT received_at, event_type FROM compliance.events "
                    "WHERE payload -> 'payload' ->> 'id' = :p ORDER BY id"
                ),
                {"p": row["provider_check_id"]},
            )
        ).all()
        for event_row in received:
            entries.append(
                (
                    event_row.received_at,
                    1,
                    {
                        "at": event_row.received_at,
                        "actor": None,
                        "action": f"ComplyCube: {event_row.event_type}",
                        "detail": None,
                    },
                )
            )
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    return [entry for _at, _order, entry in entries]


async def _detail(
    ctx: AmlContext,
    uid: str,
    *,
    method: str = "GET",
    route: str = "/aml/checks/{uid}",
    images: bool = True,
) -> dict[str, object]:
    """The check page's body. Returning identity images records that they were viewed; with
    ``images`` false none is listed and nothing is recorded (``_identity``)."""
    row = await _check_row(ctx, uid)
    identity = await _identity(ctx, row, images=images)
    if identity and identity["images"]:
        await _record_images_view(ctx, row, method=method, route=route, images=identity["images"])
    return {
        "check": {
            "uid": row["uid"],
            "kind": row["kind"],
            "origin": row["origin"],
            "status": row["status"],
            "outcome": row["outcome"],
            "attempt": row["attempt"],
            "monitoring": row["monitoring"],
            "stale_at": row["stale_at"],
            "decided_by": row["decided_by"],
            "decided_at": row["decided_at"],
            "decision_comment": row["decision_comment"],
            "uploaded_by": row["uploaded_by"],
            "uploaded_via": row["uploaded_via"],
            "upload_comment": row["upload_comment"],
            "provider_check_id": row["provider_check_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        },
        "client": _client_block(row, await _tenant_icons(ctx.session, [int(row["tenant_id"])])),
        "matches": await _matches(ctx.session, int(row["id"])),
        "identity": identity,
        "history": await _history(ctx, row),
    }


async def _record_images_view(
    ctx: AmlContext, row: dict[str, Any], *, method: str, route: str, images: object
) -> None:
    """Record that a check's identity images were shown: how many, never which bytes."""
    await actions.record_identity_images_view(
        ctx.session,
        principal=ctx.principal,
        tenant_id=ctx.own_tenant_id,
        target_tenant_id=int(row["tenant_id"]),
        actor_id=ctx.user_id,
        method=method,
        route=route,
        check_uid=str(row["uid"]),
        image_count=len(images) if isinstance(images, list) else 0,
    )


@router.get("/checks/{uid}", name="get-aml-check")
async def get_check(uid: str, ctx: AmlContext = Depends(aml_session)) -> dict[str, object]:
    """One check: what it is, who it is about, its matches, its identity result, its history.

    Gated on ``aml:read`` unconditionally — the list is cross-firm, so its rows must be openable.
    """
    require_permission(ctx.principal, AML_READ)
    return await _detail(ctx, uid)


async def _identity_row(ctx: AmlContext, uid: str) -> dict[str, Any]:
    """An identity check's row, or 404: only an identity check has images."""
    row = await _check_row(ctx, uid)
    if row["kind"] != "identity":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Only an identity check has images.")
    return row


@router.get("/checks/{uid}/images", name="list-aml-check-images")
async def list_check_images(
    uid: str, ctx: AmlContext = Depends(aml_session), record: bool = True
) -> dict[str, object]:
    """One identity check's images, as the check page lists them (``_identity``): for a page that
    read the check without them (``GET /aml/clients/{key}?images=false``).

    Listing them records the view, as the check page does. ``record=false`` lists them without
    one: the workbench reads ahead the images of the clients beside the one on screen, and records
    the view when it shows them (``POST /aml/checks/{uid}/images/viewed``). Gated on
    ``aml:read``; 404 for a check that is not an identity check."""
    require_permission(ctx.principal, AML_READ)
    row = await _identity_row(ctx, uid)
    listing = await decisions.identity_images(ctx.session, _held(row))
    if listing.images and record:
        await _record_images_view(
            ctx, row, method="GET", route="/aml/checks/{uid}/images", images=listing.images
        )
    return {"images": listing.images, "images_missing": listing.missing}


class ImagesViewedRequest(BaseModel):
    """How many of a check's identity images were shown."""

    image_count: int = Field(ge=1, le=3)


@router.post("/checks/{uid}/images/viewed", name="record-aml-check-images-viewed")
async def record_check_images_viewed(
    uid: str, body: ImagesViewedRequest, ctx: AmlContext = Depends(aml_session)
) -> dict[str, bool]:
    """Record that a check's identity images were shown, when a page that read them ahead
    (``GET /aml/checks/{uid}/images?record=false``) shows them. The act is
    ``aml.identity_images_viewed``, as listing them on the check page is, with how many were
    shown. Gated on ``aml:read``; 404 for a check that is not an identity check."""
    require_permission(ctx.principal, AML_READ)
    await _identity_row(ctx, uid)
    return {"recorded": True}


@router.get(
    "/checks/{uid}/images/{part}",
    name="get-aml-check-image",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}, "application/pdf": {}}}},
)
async def get_check_image(
    uid: str, part: complycube_images.ImagePart, ctx: AmlContext = Depends(aml_session)
) -> Response:
    """One identity photo of the check (``front``, ``back`` or ``selfie``), read from ComplyCube
    and never cached.

    The console reaches this through the admin portal's same-origin relay. Gated like the check
    (``aml:read``); the view is recorded when the check page lists its images (``_detail``), not
    per photo. 404 when the check is not an identity check or ComplyCube holds no such image; 502
    or 503 when ComplyCube cannot be asked.
    """
    require_permission(ctx.principal, AML_READ)
    row = await _check_row(ctx, uid)
    if row["kind"] != "identity":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such image.")
    try:
        image = await decisions.identity_image(ctx.session, _held(row), part)
    except complycube_images.ImageMissing as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such image.") from exc
    except decisions.IdentityDecisionError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return complycube_images.response(image, part)


@router.get("/checks/{uid}/client", name="get-aml-check-client")
async def get_check_client(uid: str, ctx: AmlContext = Depends(aml_session)) -> dict[str, str]:
    """The client page a check belongs on: its client's key (``CLIENT_KEY``: the contact uid, or
    the compliance client uid of a partner client with no contact). The console's one-check links
    (``/aml/{uid}``: tasks, bookmarks) redirect through this.

    Gated on ``aml:read`` unconditionally, like the check it names.
    """
    require_permission(ctx.principal, AML_READ)
    key = (
        await ctx.session.execute(
            text(
                f"SELECT {attention.CLIENT_KEY} FROM compliance.checks ch "  # noqa: S608 - fixed SQL only
                "JOIN compliance.clients cl ON cl.id = ch.client_id "
                "JOIN auth.tenants t ON t.id = ch.tenant_id "
                "LEFT JOIN crm.contacts c ON c.id = cl.contact_id "
                "WHERE ch.uid = :uid "
                "  AND (CAST(:tenant_id AS int) IS NULL OR ch.tenant_id = CAST(:tenant_id AS int))"
            ),
            {"uid": uid, "tenant_id": _scope(ctx)},
        )
    ).scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown check")
    return {"key": str(key), "check_uid": uid}


# ── One client ────────────────────────────────────────────────────────────────────────────────


@dataclass
class _ResolvedClient:
    """The client a key names, with its canonical key (``CLIENT_KEY``). ``client_id`` is None for
    a portal client with no compliance client yet; ``contact_id`` is None for a partner client
    with no firm record."""

    key: str
    client_id: int | None
    tenant_id: int
    contact_id: int | None


# A firm client by its contact uid, with its compliance client if it has one. A deleted contact
# still opens while it has a compliance client, because the list still shows that client.
_CLIENT_BY_CONTACT = """
SELECT c.id AS contact_id, c.uid AS contact_uid, c.tenant_id, cl.id AS client_id
  FROM crm.contacts c
  LEFT JOIN compliance.clients cl ON cl.tenant_id = c.tenant_id AND cl.contact_id = c.id
 WHERE c.uid = :uid AND (c.deleted_at IS NULL OR cl.id IS NOT NULL)
   AND (CAST(:tenant_id AS int) IS NULL OR c.tenant_id = CAST(:tenant_id AS int))
"""

# A compliance client by its own uid, with its contact's uid if it has one. A compliance client
# with no firm is left out, as the list leaves it out.
_CLIENT_BY_UID = """
SELECT cl.id AS client_id, cl.uid AS client_uid, cl.tenant_id, cl.contact_id,
       c.uid AS contact_uid
  FROM compliance.clients cl
  LEFT JOIN crm.contacts c ON c.id = cl.contact_id
 WHERE cl.uid = :uid AND cl.tenant_id IS NOT NULL
   AND (CAST(:tenant_id AS int) IS NULL OR cl.tenant_id = CAST(:tenant_id AS int))
"""


async def _resolve_client(ctx: AmlContext, key: str) -> _ResolvedClient:
    """The client a key names, or 404. A ``?tenant=``-narrowed session stays narrowed.

    Tried in order: a contact uid; a ``compliance.clients`` uid; the legacy ``contact-<uid>``.
    Whatever form named it, the answer carries the client's canonical key, so a page opened by a
    compliance uid or a legacy key can move to the contact uid.
    """
    scope = _scope(ctx)

    async def by_contact(uid: str) -> _ResolvedClient | None:
        found = (
            (await ctx.session.execute(text(_CLIENT_BY_CONTACT), {"uid": uid, "tenant_id": scope}))
            .mappings()
            .one_or_none()
        )
        if found is None:
            return None
        return _ResolvedClient(
            key=str(found["contact_uid"]),
            client_id=found["client_id"],
            tenant_id=int(found["tenant_id"]),
            contact_id=int(found["contact_id"]),
        )

    resolved = await by_contact(key)
    if resolved is not None:
        return resolved
    found = (
        (await ctx.session.execute(text(_CLIENT_BY_UID), {"uid": key, "tenant_id": scope}))
        .mappings()
        .one_or_none()
    )
    if found is not None:
        return _ResolvedClient(
            key=str(found["contact_uid"] or found["client_uid"]),
            client_id=int(found["client_id"]),
            tenant_id=int(found["tenant_id"]),
            contact_id=found["contact_id"],
        )
    if key.startswith(CONTACT_KEY_PREFIX):
        resolved = await by_contact(key.removeprefix(CONTACT_KEY_PREFIX))
        if resolved is not None:
            return resolved
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown client")


_CLIENT_FACTS = f"""
SELECT {_CLIENT_BLOCK_COLUMNS}
  FROM auth.tenants t
  LEFT JOIN compliance.clients cl ON cl.id = CAST(:client AS int)
  LEFT JOIN crm.contacts c ON c.id = CAST(:contact AS int)
  LEFT JOIN compliance.addresses a ON a.client_id = cl.id
 WHERE t.id = :tenant
"""  # noqa: S608 - the only interpolation is the fixed column list above

_CLIENT_CHECKS = f"""
SELECT h.uid, h.kind, h.origin, h.status, h.attempt, h.monitoring,
       (h.stale_at IS NOT NULL) AS stale, (h.decided_at IS NOT NULL) AS decided,
       h.created_at, h.updated_at, {attention.NEEDS_HUMAN} AS needs_attention
  FROM compliance.checks h
 WHERE h.client_id = :client
 ORDER BY h.id DESC
"""  # noqa: S608 - the only interpolation is the fixed predicate above

#: The parts of ComplyCube's risk profile, in the order the console shows them
#: (``GET /clients/{id}/riskProfile``, docs.complycube.com "Risk Profile").
_RISK_FACTORS: tuple[tuple[str, str, str], ...] = (
    ("country", "Country", "countryRisk"),
    ("political_exposure", "Political exposure", "politicalExposureRisk"),
    ("occupation", "Occupation", "occupationRisk"),
    ("watchlist", "Watchlist", "watchlistRisk"),
)


def risk_profile(overall: str | None, profile: object, updated_at: object) -> dict[str, object]:
    """A stored risk profile as the client page shows it: the overall level, one row per part with
    its level and what it rests on (the riskiest country; the occupation assessed), and when
    ComplyCube last changed it. A part ComplyCube did not rate reads ``not_set``."""
    data = _jsonb(profile)

    def level(value: object) -> str:
        return _text(value) or "not_set"

    stored = _text(overall)
    overall_level = (
        stored if stored and stored != "not_set" else level(data.get("overallRisk") or stored)
    )
    factors: list[dict[str, object]] = []
    for key, label, field in _RISK_FACTORS:
        part = data.get(field)
        part = part if isinstance(part, dict) else {}
        detail: str | None = None
        if key == "country":
            detail = _text(part.get("country"))
        elif key == "occupation":
            named = [
                text_value
                for text_value in (
                    _text(part.get("occupationTitle")),
                    _text(part.get("occupationCategory")),
                )
                if text_value
            ]
            detail = " · ".join(named) or None
        factors.append(
            {"key": key, "label": label, "risk": level(part.get("risk")), "detail": detail}
        )
    return {
        "overall": overall_level,
        "updated_at": updated_at or _text(data.get("updatedAt")),
        "factors": factors,
    }


# A client's internal notes, newest first: those written under its contact and those written under
# its compliance client, so a note kept on the contact before the compliance client existed still
# shows. Held to the client's firm, as every scoped query here is by hand (no RLS on service_role).
_CLIENT_NOTES = """
SELECT n.uid, n.body, n.author_name, n.created_at
  FROM compliance.client_notes n
 WHERE n.tenant_id = :tenant
   AND (n.contact_id = CAST(:contact AS int) OR n.client_id = CAST(:client AS int))
 ORDER BY n.created_at DESC, n.id DESC
"""


def _note_item(row: Any) -> dict[str, object]:
    """One internal note as the client page shows it."""
    return {
        "uid": str(row["uid"]),
        "body": str(row["body"]),
        "author": str(row["author_name"]),
        "created_at": _iso(row["created_at"]),
    }


async def _client_notes(session: AsyncSession, found: _ResolvedClient) -> list[dict[str, object]]:
    """Every internal note on the client ``found`` names, newest first (``_CLIENT_NOTES``)."""
    rows = (
        await session.execute(
            text(_CLIENT_NOTES),
            {"tenant": found.tenant_id, "contact": found.contact_id, "client": found.client_id},
        )
    ).mappings()
    return [_note_item(row) for row in rows]


@router.get("/clients/{key}", name="get-aml-client")
async def get_client(
    key: str, ctx: AmlContext = Depends(aml_session), images: bool = True
) -> dict[str, object]:
    """One client: who they are, ComplyCube's risk profile, the latest identity check and the
    latest AML screen (each in the shape ``GET /aml/checks/{uid}`` returns), every earlier
    check as a summary, and Graphene's internal notes on them, newest first (``_CLIENT_NOTES``).

    ``key`` is a list row's key (``CLIENT_KEY``): the client's contact uid, or the compliance
    client uid of a partner client with no contact. A compliance client uid of a client that has a
    contact, and the legacy ``contact-<uid>``, open the client too (``_resolve_client``); the
    answer's ``key`` is always the canonical one, so the page can move an old link to it.
    Returning identity images records that they were viewed. ``images=false`` lists none and
    records nothing (each identity check's ``images`` is None): the workbench reads the clients
    beside the one on screen that way, and lists a check's images when it shows it
    (``GET /aml/checks/{uid}/images``). Gated on ``aml:read`` unconditionally, like the list.
    """
    require_permission(ctx.principal, AML_READ)
    found = await _resolve_client(ctx, key)
    session = ctx.session
    checks: list[dict[str, Any]] = []
    if found.client_id is not None:
        checks = [
            dict(row)
            for row in (
                await session.execute(
                    text(_CLIENT_CHECKS),
                    {"client": found.client_id, "give_up": GIVE_UP.total_seconds()},
                )
            )
            .mappings()
            .all()
        ]
    # The client's row as the list and the work queue read it. None for a client with no check
    # and no screen due, which the list leaves out and nobody needs to act on.
    work = (
        (
            await session.execute(
                text(attention.ONE_CLIENT_ROW),
                {
                    "tenant": found.tenant_id,
                    "client": found.client_id,
                    "contact": found.contact_id,
                    "give_up": GIVE_UP.total_seconds(),
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    awaiting = bool(work and work["awaiting"])
    screen_queued = bool(work and work["screen_queued"])
    reasons = attention.reasons_of(work) if work else []
    waiting_since = work["waiting_since"] if work else None
    latest: dict[str, dict[str, Any]] = {}
    for check in checks:
        latest.setdefault(str(check["kind"]), check)
    route = "/aml/clients/{key}"
    identity = (
        await _detail(ctx, str(latest["identity"]["uid"]), route=route, images=images)
        if "identity" in latest
        else None
    )
    aml = await _detail(ctx, str(latest["aml"]["uid"]), route=route) if "aml" in latest else None
    shown = {str(check["uid"]) for check in latest.values()}

    facts = (
        (
            await session.execute(
                text(_CLIENT_FACTS),
                {"client": found.client_id, "contact": found.contact_id, "tenant": found.tenant_id},
            )
        )
        .mappings()
        .one()
    )
    portal = awaiting or not checks or any(check["origin"] == "portal" for check in checks)
    origin = "portal" if portal else "partner"
    icons = await _tenant_icons(session, [found.tenant_id])
    client = {
        **_client_block({**facts, "origin": origin, "tenant_id": found.tenant_id}, icons),
        "origin": origin,
    }

    risk = None
    if found.client_id is not None:
        stored = (
            (
                await session.execute(
                    text(
                        "SELECT overall, profile, provider_updated_at, updated_at "
                        "FROM compliance.client_risk WHERE client_id = :c"
                    ),
                    {"c": found.client_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if stored is not None:
            risk = risk_profile(
                stored["overall"],
                stored["profile"],
                stored["provider_updated_at"] or stored["updated_at"],
            )

    stale = bool(latest.get("aml", {}).get("stale"))
    upload = await uploads.standing(
        session, tenant_id=found.tenant_id, contact_id=found.contact_id, client_pk=found.client_id
    )
    return {
        "key": found.key,
        "client": client,
        "awaiting_screen": awaiting,
        # Due a screen the worker already has queued: it starts by itself in a few seconds, so
        # the console shows it starting and offers no Screen button.
        "screen_queued": screen_queued,
        "stale": stale,
        "needs_attention": bool(work and work["needs_attention"]),
        # Why a person is needed (`attention.REASONS`), the client's steps, and how long the
        # oldest of those reasons has waited.
        "reasons": reasons,
        "steps": attention.steps_of(work or {}, reasons),
        "waiting_since": waiting_since,
        "age": attention.age_of(waiting_since),
        "risk": risk,
        "identity": identity,
        "aml": aml,
        "earlier": [
            {**check, "needs_attention": bool(check["needs_attention"])}
            for check in checks
            if str(check["uid"]) not in shown
        ],
        "notes": await _client_notes(session, found),
        "identity_upload": {
            "allowed": upload.blocked is None,
            "blocked": upload.blocked,
            "document": (
                {"document_type": upload.kept.document_type}
                if upload.kept.document_id is not None
                else None
            ),
            "selfie": upload.kept.live_photo_id is not None,
            "issuing_country": uploads.default_country(upload.contact),
            "journey": upload.waiting is not None,
        },
    }


# ── Writes ────────────────────────────────────────────────────────────────────────────────────


def _screening() -> ScreeningClient:
    """The ComplyCube client, or 503 when no key is set."""
    try:
        return require_client()
    except ComplyCubeNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


def _refusal(response: httpx.Response) -> str:
    """ComplyCube's refusal, with its own message when it gave one. Shown to operators only."""
    message = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        raw = body.get("message") or body.get("error") or ""
        message = str(raw).strip()[:300]
    suffix = f": {message}" if message else "."
    return f"ComplyCube refused the request ({response.status_code}){suffix}"


@asynccontextmanager
async def provider_call() -> AsyncIterator[None]:
    """Map a ComplyCube failure onto the status the caller should see. The firm's client identity
    routes share it (``app.routers.client_identity``)."""
    try:
        yield
    except ComplyCubeNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_refusal(exc.response),
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="ComplyCube did not answer. Try again.",
        ) from exc


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _required_comment(comment: str | None) -> str:
    """A decision's comment, trimmed. Required: the record of WHY is what a regulator asks for."""
    cleaned = (comment or "").strip()
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A comment is required."
        )
    return cleaned


def _advisers(value: object) -> list[dict[str, str | bool | None]]:
    """The contact's advisers as ``_CLIENT_BLOCK_COLUMNS`` aggregates them; [] for none."""
    try:
        items = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return []
    keys = ("uid", "name", "email", "phone", "job_title", "fca_reference", "photo_url", "role")
    return [
        {
            **{key: _part(item.get(key)) for key in keys},
            "is_adviser": bool(item.get("is_adviser")),
        }
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict)
    ]


def _optional_comment(comment: str | None) -> str | None:
    """A decision's comment, trimmed; None when the operator gave none."""
    cleaned = (comment or "").strip()
    return cleaned or None


async def _operator(ctx: AmlContext) -> str:
    """The acting operator as the audit trail names them: display name, else email."""
    found = (
        await ctx.session.execute(
            text(
                "SELECT COALESCE(NULLIF(trim(name), ''), email) FROM auth.users "
                "WHERE id = CAST(:u AS uuid)"
            ),
            {"u": ctx.user_id},
        )
    ).scalar_one_or_none()
    return str(found) if found else ctx.principal.user_id


async def _record(session: AsyncSession, check_pk: int) -> routing.CheckRecord:
    record = await routing.load_check(session, check_pk)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown check")
    return record


class ScreenRequest(BaseModel):
    """Screen one of a firm's clients."""

    contact_uid: str


@router.post("/screen", name="run-aml-screen")
async def run_screen(body: ScreenRequest, ctx: AmlContext = Depends(aml_session)) -> dict[str, str]:
    """Run a ComplyCube AML screen for a portal client, as the journey does by itself
    (``app.complycube.screen.run_portal_screen``): for a client whose screen could not run on its
    own, or whose details changed after a clear.

    Idempotent: a client with an open AML check gets that check back and nothing is paid for. The
    new check settles the client's pending AML kyc row and closes the "Screen {client}" task.
    """
    require_permission(ctx.principal, AML_WRITE)
    screening = _screening()
    contact = await screen.load_contact(
        ctx.session, contact_uid=body.contact_uid, tenant_id=_scope(ctx)
    )
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown client")
    try:
        async with provider_call():
            uid = await screen.run_portal_screen(ctx.session, screening, contact)
    except screen.ScreenConflict as exc:
        raise _conflict(str(exc)) from exc
    return {"check_uid": uid}


class MatchDecisionRequest(BaseModel):
    """Confirm or dismiss one screening match. ``comment`` is optional, as on ComplyCube."""

    decision: Literal["confirmed", "dismissed"]
    comment: str | None = None


#: A decision in the outcome ComplyCube's validate call takes (``POST /checks/{id}/validate``
#: accepts ``confirmed``, ``rejected`` or ``attention``). For a screening match, ``confirmed``
#: makes the check ``match_confirmed`` and ``rejected`` dismisses the match; once every match is
#: rejected ComplyCube clears the check itself.
MATCH_OUTCOMES: dict[str, str] = {"confirmed": "confirmed", "dismissed": "rejected"}
#: For an identity check, ``confirmed`` makes it ``clear`` and ``rejected`` makes it ``rejected``.
CHECK_OUTCOMES: dict[str, str] = {"clear": "confirmed", "rejected": "rejected"}


@router.post("/checks/{uid}/matches/{match_uid}/decision", name="decide-aml-match")
async def decide_match(
    uid: str,
    match_uid: str,
    body: MatchDecisionRequest,
    ctx: AmlContext = Depends(aml_session),
) -> dict[str, object]:
    """Confirm or dismiss one match on an AML check in ``refer``, then settle the check if that
    was the last open match.

    A decided match can be decided again while the check is in ``refer``, so an operator who got
    one wrong has the route back; once the check is clear (or rejected) it is 409. Gated on
    ``aml:adjudicate``, which exists only in the operations portal: only Graphene clears a match.
    """
    require_permission(ctx.principal, AML_ADJUDICATE)
    comment = _optional_comment(body.comment)
    screening = _screening()
    session = ctx.session
    row = await _check_row(ctx, uid, lock=True)
    if row["kind"] != "aml":
        raise _conflict("Only an AML check has matches.")
    if row["status"] in {"clear", "rejected"}:
        raise _conflict("This check is settled. Its matches can no longer be decided.")
    if row["status"] != "refer":
        raise _conflict("This check is not waiting on a decision.")
    if row["origin"] == "portal" and row["decided_at"] is not None:
        raise _conflict("This journey was allowed to continue. Its matches can no longer change.")
    match = (
        await session.execute(
            text(
                "SELECT id, provider_match_id FROM compliance.matches "
                "WHERE uid = :m AND check_id = :c"
            ),
            {"m": match_uid, "c": row["id"]},
        )
    ).first()
    if match is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown match")
    if not row["provider_check_id"]:
        raise _conflict("This check has no ComplyCube record.")
    async with provider_call():
        await screening.validate_check(
            str(row["provider_check_id"]),
            MATCH_OUTCOMES[body.decision],
            match_id=str(match.provider_match_id),
            comment=comment,
        )
    operator = await _operator(ctx)
    await session.execute(
        text(
            "UPDATE compliance.matches SET decision = :d, decided_by = :by, decided_at = now(), "
            "  comment = :comment WHERE id = :id"
        ),
        {"d": body.decision, "by": operator, "comment": comment, "id": int(match.id)},
    )
    await _settle_if_decided(ctx, screening, row, operator)
    return await _detail(
        ctx,
        uid,
        method="POST",
        route="/aml/checks/{uid}/matches/{match_uid}/decision",
    )


async def _settle_if_decided(
    ctx: AmlContext, screening: ScreeningClient, row: dict[str, Any], operator: str
) -> None:
    """Settle an AML check once none of its matches is undecided.

    * every match dismissed → the check is validated clear at ComplyCube and becomes ``clear``;
      routing then closes its tasks, passes the portal kyc row, and tells a partner firm.
    * any match confirmed, on a portal check → the check stays ``refer`` and undecided: it is HELD.
      The kyc row stays pending and the journey's AML step keeps waiting, so the firm sees the
      screen as in progress, never the match. Only an operator moves it on, with
      ``POST /aml/checks/{uid}/allow``. Its matches can still be changed until then.
    * any match confirmed, on a partner check → the check stays ``refer``, decided: the partner
      firm's client is referred.

    No check-level validate is sent: ComplyCube clears a screening check by itself once every match
    is rejected, and refuses a validate without a ``matchId`` on a screening check.
    """
    session = ctx.session
    counts = (
        await session.execute(
            text(
                "SELECT count(*) AS total, "
                "  count(*) FILTER (WHERE decision IS NULL) AS undecided, "
                "  count(*) FILTER (WHERE decision = 'confirmed') AS confirmed "
                "FROM compliance.matches WHERE check_id = :c"
            ),
            {"c": row["id"]},
        )
    ).one()
    if int(counts.undecided) > 0:
        return
    total = int(counts.total)
    confirmed = int(counts.confirmed)
    if confirmed == 0:
        summary = f"All {total} match{'es' if total != 1 else ''} dismissed."
        await session.execute(
            text(
                "UPDATE compliance.checks SET status = 'clear', decided_by = :by, "
                "  decided_at = now(), decision_comment = :summary WHERE id = :id"
            ),
            {"by": operator, "summary": summary, "id": row["id"]},
        )
        record = await _record(session, int(row["id"]))
        await routing.route_result(session, record, "refer", "clear")
        return
    summary = f"{confirmed} of {total} match{'es' if total != 1 else ''} confirmed."
    if row["origin"] == "portal":
        # Held: the review is not over until an operator allows the journey to continue. The
        # summary is kept for the console; `decided_at` stays empty, so the client still needs
        # attention and the kyc row stays pending.
        await session.execute(
            text("UPDATE compliance.checks SET decision_comment = :summary WHERE id = :id"),
            {"summary": summary, "id": row["id"]},
        )
        return
    await session.execute(
        text(
            "UPDATE compliance.checks SET decided_by = :by, decided_at = now(), "
            "  decision_comment = :summary WHERE id = :id"
        ),
        {"by": operator, "summary": summary, "id": row["id"]},
    )
    record = await _record(session, int(row["id"]))
    # The status does not change, so `route_result` has nothing to route. The review is done,
    # though: its tasks close.
    await tasks.close_screening_task(session, tasks.check_task_ref(record.uid))


class CheckDecisionRequest(BaseModel):
    """Clear or reject an identity check. ``comment`` is required (non-blank)."""

    decision: Literal["clear", "rejected"]
    comment: str


@router.post("/checks/{uid}/decision", name="decide-aml-check")
async def decide_check(
    uid: str, body: CheckDecisionRequest, ctx: AmlContext = Depends(aml_session)
) -> dict[str, object]:
    """Clear or reject an identity check.

    A ``refer`` can be cleared or rejected, and the verdict is validated at ComplyCube. A check
    ComplyCube could not run (``failed``) can only be rejected — there is no result to clear, and
    nothing at the provider to validate — which tells the firm or client to send new images.
    An AML check is never decided as a whole: it settles from its matches (409 here).
    """
    require_permission(ctx.principal, AML_ADJUDICATE)
    comment = _required_comment(body.comment)
    screening = _screening()
    session = ctx.session
    row = await _check_row(ctx, uid, lock=True)
    if row["kind"] != "identity":
        raise _conflict("An AML check settles from its matches.")
    old = str(row["status"])
    if row["decided_at"] is not None or old not in {"refer", TECHNICAL_FAILURE}:
        raise _conflict("This check is not waiting on a decision.")
    if old == TECHNICAL_FAILURE and body.decision != "rejected":
        raise _conflict("ComplyCube could not run this check, so it can only be rejected.")
    if old == "refer" and body.decision == "clear" and row["origin"] == "portal":
        # The one clear path for a portal identity check.
        try:
            await decisions.clear_portal_identity(
                session,
                check_pk=int(row["id"]),
                decided_by=await _operator(ctx),
                comment=comment,
            )
        except decisions.IdentityDecisionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        return await _detail(ctx, uid, method="POST", route="/aml/checks/{uid}/decision")
    if old == "refer":
        if not row["provider_check_id"]:
            raise _conflict("This check has no ComplyCube record.")
        async with provider_call():
            await screening.validate_check(
                str(row["provider_check_id"]), CHECK_OUTCOMES[body.decision], comment=comment
            )
    await session.execute(
        text(
            "UPDATE compliance.checks SET status = :st, decided_by = :by, decided_at = now(), "
            "  decision_comment = :comment WHERE id = :id"
        ),
        {"st": body.decision, "by": await _operator(ctx), "comment": comment, "id": row["id"]},
    )
    record = await _record(session, int(row["id"]))
    await routing.route_result(session, record, old, body.decision)
    return await _detail(ctx, uid, method="POST", route="/aml/checks/{uid}/decision")


PhotoChoice = Literal["document", "selfie"]

#: Each photo an operator can ask for, as the console's history names it.
PHOTO_LABELS: dict[str, str] = {"document": "document", "selfie": "selfie"}


class PhotoRequest(BaseModel):
    """Ask the client for new photos of a portal identity try held for review.

    ``photos`` names what to take again (the document is both its sides). ``reason`` is what the
    client is told (``identity.PHOTO_REQUEST_REASONS``; ``none`` tells them nothing specific).
    ``comment`` is required (non-blank) and stays internal: it is the record of why.
    """

    photos: list[PhotoChoice] = Field(min_length=1, max_length=2)
    reason: identity.PhotoReason
    comment: str


@router.post("/checks/{uid}/request-photos", name="request-aml-photos")
async def request_photos(
    uid: str, body: PhotoRequest, ctx: AmlContext = Depends(aml_session)
) -> dict[str, object]:
    """Ask the client for new photos of a portal identity check held for review.

    For an undecided portal identity ``refer``, on any try. The check is
    marked decided under the operator, with the comment, so its client no longer needs attention;
    its status stays ``refer`` and the kyc row stays pending. The client gets a new try at the next
    attempt that reopens only the chosen photos and keeps the rest (``routing.ask_for_photos``),
    and is told the chosen reason. Nothing is sent to ComplyCube: this is not a decision on the
    client. 409 when the check is not such a refer, or no try of theirs waits on it.
    """
    require_permission(ctx.principal, AML_ADJUDICATE)
    comment = _required_comment(body.comment)
    session = ctx.session
    row = await _check_row(ctx, uid, lock=True)
    if (
        row["kind"] != "identity"
        or row["origin"] != "portal"
        or row["status"] != "refer"
        or row["decided_at"] is not None
    ):
        raise _conflict(
            "Only a portal identity check waiting on a decision can ask for new photos."
        )
    reopen = tuple(
        dict.fromkeys(key for photo in body.photos for key in capture.PHOTO_ITEMS[photo])
    )
    record = await _record(session, int(row["id"]))
    asked = await routing.ask_for_photos(
        session, record, reopen=reopen, last_reason=identity.PHOTO_REQUEST_REASONS[body.reason]
    )
    if not asked:
        raise _conflict("The client's identity step is no longer waiting on this check.")
    await session.execute(
        text(
            "UPDATE compliance.checks SET decided_by = :by, decided_at = now(), "
            "  decision_comment = :comment, updated_at = now() WHERE id = :id"
        ),
        {"by": await _operator(ctx), "comment": comment, "id": row["id"]},
    )
    return await _detail(ctx, uid, method="POST", route="/aml/checks/{uid}/request-photos")


class AllowJourneyRequest(BaseModel):
    """Let a held journey continue. ``comment`` is optional: why, and any measures taken."""

    comment: str | None = None


@router.post("/checks/{uid}/allow", name="allow-aml-journey")
async def allow_journey(
    uid: str, body: AllowJourneyRequest, ctx: AmlContext = Depends(aml_session)
) -> dict[str, object]:
    """Let a portal client's journey continue past a confirmed AML match.

    For a portal AML check HELD on a confirmed match (``refer``, every match decided, at least one
    confirmed, not yet decided as a whole; see ``_settle_if_decided``). The check is decided under
    the operator, with the comment when one is given, and stays ``refer``: the confirmed match
    stands on record. Its tasks close, the kyc row passes and the journey's AML step completes,
    so the firm sees the screen as passed. Nothing goes to ComplyCube: the matches were already
    validated there. The act is the ``aml.journey_allowed`` event. 409 when the check is not held.
    """
    require_permission(ctx.principal, AML_ADJUDICATE)
    comment = _optional_comment(body.comment)
    session = ctx.session
    row = await _check_row(ctx, uid, lock=True)
    if (
        row["kind"] != "aml"
        or row["origin"] != "portal"
        or row["status"] != "refer"
        or row["decided_at"] is not None
    ):
        raise _conflict("Only a portal AML screen held on a confirmed match can be allowed.")
    counts = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE decision IS NULL) AS undecided, "
                "  count(*) FILTER (WHERE decision = 'confirmed') AS confirmed "
                "FROM compliance.matches WHERE check_id = :c"
            ),
            {"c": row["id"]},
        )
    ).one()
    if int(counts.undecided) > 0 or int(counts.confirmed) == 0:
        raise _conflict("Only a portal AML screen held on a confirmed match can be allowed.")
    summary = row["decision_comment"]
    await session.execute(
        text(
            "UPDATE compliance.checks SET decided_by = :by, decided_at = now(), "
            "  decision_comment = :comment, updated_at = now() WHERE id = :id"
        ),
        {
            "by": await _operator(ctx),
            "comment": " ".join(
                part
                for part in (
                    summary,
                    f"Allowed to continue: {comment}" if comment else "Allowed to continue.",
                )
                if part
            ),
            "id": row["id"],
        },
    )
    record = await _record(session, int(row["id"]))
    await tasks.close_screening_task(session, tasks.check_task_ref(record.uid))
    await routing.settle_portal_check(session, record, to_kyc_status("clear"))
    return await _detail(ctx, uid, method="POST", route="/aml/checks/{uid}/allow")


class MonitoringRequest(BaseModel):
    enabled: bool


@router.put("/checks/{uid}/monitoring", name="set-aml-monitoring")
async def set_monitoring(
    uid: str, body: MonitoringRequest, ctx: AmlContext = Depends(aml_session)
) -> dict[str, object]:
    """Turn ComplyCube's ongoing monitoring on or off for an AML check."""
    require_permission(ctx.principal, AML_WRITE)
    screening = _screening()
    row = await _check_row(ctx, uid, lock=True)
    if row["kind"] != "aml":
        raise _conflict("Only an AML check is monitored.")
    if not row["provider_check_id"]:
        raise _conflict("This check has no ComplyCube record.")
    async with provider_call():
        await screening.update_monitoring(str(row["provider_check_id"]), enabled=body.enabled)
    await ctx.session.execute(
        text("UPDATE compliance.checks SET monitoring = :e, enable_monitoring = :e WHERE id = :id"),
        {"e": body.enabled, "id": row["id"]},
    )
    return await _detail(ctx, uid, method="PUT", route="/aml/checks/{uid}/monitoring")


@router.post("/checks/{uid}/rerun", name="rerun-aml-check")
async def rerun_check(uid: str, ctx: AmlContext = Depends(aml_session)) -> dict[str, str]:
    """Run an AML screen ComplyCube could not run again, for the same ComplyCube client.

    For a portal or a partner client alike. The new check keeps the old one's firm, origin,
    client, kyc row and monitoring, and its attempt number (a technical failure is not one of the
    client's tries). The old check is marked decided ("Run again as …") and its tasks close. A
    portal client is first brought up to date at ComplyCube, as every portal screen is
    (``clients.bind_portal_client``), so a name corrected since is the name screened. A partner
    client is not brought up to date yet, but is still checked to be this client's: ComplyCube's
    ``externalId`` must be the contact uid, and no other deployment's ``graphene_env``. Either
    refusal is a 409 with nothing screened.
    """
    require_permission(ctx.principal, AML_WRITE)
    screening = _screening()
    session = ctx.session
    row = await _check_row(ctx, uid, lock=True)
    if row["kind"] != "aml" or row["status"] != TECHNICAL_FAILURE or row["decided_at"] is not None:
        raise _conflict("Only an AML screen ComplyCube could not run can be run again.")
    if not row["provider_client_id"]:
        raise _conflict("This check has no ComplyCube client.")
    if row["origin"] == "portal" and row["contact_id"] is not None:
        contact = (
            (
                await session.execute(
                    text(
                        f"SELECT {clients.CONTACT_COLUMNS} FROM crm.contacts "  # noqa: S608 - a module constant
                        "WHERE id = :c AND tenant_id = :t"
                    ),
                    {"c": row["contact_id"], "t": row["tenant_id"]},
                )
            )
            .mappings()
            .one_or_none()
        )
        if contact is not None:
            try:
                async with provider_call():
                    bound = await clients.bind_portal_client(session, screening, dict(contact))
            except clients.ClientConflict as exc:
                raise _conflict(str(exc)) from exc
            if bound != int(row["client_id"]):
                raise _conflict(
                    "This check's ComplyCube client is not its client's. Ask engineering."
                )
    elif row["origin"] == "partner":
        async with provider_call():
            held = await screening.get_client(str(row["provider_client_id"]))
        held = held if isinstance(held, dict) else {}
        env, stamped = environment(), environment_of(held)
        if held.get("externalId") != row["contact_uid"] or (
            env and stamped is not None and stamped != env
        ):
            raise _conflict(
                "That ComplyCube client is not this client's, so it was not screened. "
                "Ask engineering to check it."
            )
    async with provider_call():
        created = await screening.create_screening(
            str(row["provider_client_id"]), monitoring=screen.DEFAULT_MONITORING
        )
    try:
        record = await routing.register_check(
            session,
            origin=str(row["origin"]),
            tenant_id=int(row["tenant_id"]),
            client_id=int(row["client_id"]),
            kind="aml",
            provider_check_id=str(created["id"]),
            kyc_check_id=row["kyc_check_id"],
            attempt=next_attempt(int(row["attempt"]), TECHNICAL_FAILURE),
            monitoring=monitoring_of(created, screen.DEFAULT_MONITORING),
        )
    except LookupError as exc:
        raise _conflict(str(exc)) from exc
    await session.execute(
        text(
            "UPDATE compliance.checks SET decided_by = :by, decided_at = now(), "
            "  decision_comment = :comment WHERE id = :id"
        ),
        {"by": await _operator(ctx), "comment": f"Run again as {record.uid}", "id": row["id"]},
    )
    await tasks.close_screening_task(session, tasks.check_task_ref(uid))
    return {"check_uid": record.uid}


# ── Internal notes ────────────────────────────────────────────────────────────────────────────

#: The longest note, after trimming. The same bound as ``compliance.client_notes``' CHECK (0228).
NOTE_MAX_CHARS = 5000


class ClientNoteRequest(BaseModel):
    """An internal note on a client: free text, 1 to ``NOTE_MAX_CHARS`` characters once trimmed."""

    body: str


@router.post("/clients/{key}/notes", name="add-aml-client-note")
async def add_client_note(
    key: str, body: ClientNoteRequest, ctx: AmlContext = Depends(aml_session)
) -> dict[str, object]:
    """Add an internal note on a client: why something was allowed, a call made, enhanced due
    diligence done. Returns the note in the shape ``GET /aml/clients/{key}`` lists it.

    ``key`` resolves as on the client page (``_resolve_client``); 404 for an unknown client. The
    note is kept on the client's contact when it has one, else on its compliance client, under
    the operator. Graphene only: ``compliance.client_notes`` has no grant to a firm, and the act is
    the ``aml.client_note_added`` event, which a firm's audit view leaves out. Its subject is
    never captured, so the note's text stays off the audit row. Notes are never edited or
    deleted. 422 for a blank note or one over ``NOTE_MAX_CHARS``.
    """
    require_permission(ctx.principal, AML_WRITE)
    note = body.body.strip()
    if not note:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Write a note."
        )
    if len(note) > NOTE_MAX_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"A note can be at most {NOTE_MAX_CHARS} characters.",
        )
    found = await _resolve_client(ctx, key)
    row = (
        (
            await ctx.session.execute(
                text(
                    "INSERT INTO compliance.client_notes "
                    "  (tenant_id, contact_id, client_id, body, author_user_id, author_name) "
                    "VALUES (:t, :contact, :client, :body, CAST(:user AS uuid), :author) "
                    "RETURNING uid, body, author_name, created_at"
                ),
                {
                    "t": found.tenant_id,
                    "contact": found.contact_id,
                    "client": None if found.contact_id is not None else found.client_id,
                    "body": note,
                    "user": ctx.user_id,
                    "author": await _operator(ctx),
                },
            )
        )
        .mappings()
        .one()
    )
    return _note_item(row)


#: The longest internal note an upload of identity photos takes.
UPLOAD_COMMENT_MAX_CHARS = NOTE_MAX_CHARS


@router.post("/clients/{key}/identity-photos", name="upload-aml-identity-photos")
async def upload_identity_photos(
    key: str,
    comment: Annotated[str, Form()],
    document_type: Annotated[
        Literal["passport", "driving_licence", "national_id"] | None, Form()
    ] = None,
    issuing_country: Annotated[str | None, Form()] = None,
    idv_document_front: Annotated[UploadFile | None, File()] = None,
    idv_document_back: Annotated[UploadFile | None, File()] = None,
    idv_selfie: Annotated[UploadFile | None, File()] = None,
    ctx: AmlContext = Depends(aml_session),
) -> dict[str, object]:
    """Upload a portal client's identity photos for them, and start an identity check on them
    (``app.complycube.uploads``): the client sent a new document by email, their document or name
    changed, or they sent better photos of a try held for review.

    Multipart: ``comment`` (required, internal: why), ``document_type`` and the document's sides
    (``idv_document_front``, and ``idv_document_back`` unless a passport) when the document is
    new, ``idv_selfie`` when the selfie is, and ``issuing_country`` to name another issuer than
    the client's nationality. A photo left out is the one ComplyCube holds for the client. The
    photos go to ComplyCube now and are never kept here.

    When a journey step waits on the client's identity the check answers it, and the client is
    asked for nothing; a try held for review is closed under the operator. Otherwise the check
    settles the client's identity kyc row (a new one when none is waiting), so a clear verifies
    them again from today. 409 when no upload can be made now (a partner client, a check
    running, nothing held to keep); 422 when a photo cannot be used, ComplyCube's refusal named.
    """
    require_permission(ctx.principal, AML_ADJUDICATE)
    note = _required_comment(comment)
    country = (issuing_country or "").strip().upper() or None
    if country is not None and not re.fullmatch(r"[A-Z]{2}", country):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The issuing country is a two-letter country code.",
        )
    if len(note) > UPLOAD_COMMENT_MAX_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"A comment is at most {UPLOAD_COMMENT_MAX_CHARS} characters.",
        )
    files = {
        capture.FRONT: idv_document_front,
        capture.BACK: idv_document_back,
        capture.SELFIE: idv_selfie,
    }
    try:
        photos = await uploads.read_photos(files)
        found = await _resolve_client(ctx, key)
        screening = _screening()
        async with provider_call():
            started = await uploads.start(
                ctx.session,
                screening,
                tenant_id=found.tenant_id,
                contact_id=found.contact_id,
                client_pk=found.client_id,
                upload=uploads.Upload(
                    photos=photos,
                    document_type=document_type,
                    issuing_country=country,
                    uploaded_by=await _operator(ctx),
                    comment=note,
                ),
            )
    except uploads.UploadRefused as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except uploads.UploadBlocked as exc:
        raise _conflict(str(exc)) from exc
    return {
        "check_uid": started.check_uid,
        "photos": list(started.parts),
        "document_type": started.document_type,
    }


@router.get(
    "/clients/{key}/report",
    name="get-aml-client-report",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def get_client_report(key: str, ctx: AmlContext = Depends(aml_session)) -> Response:
    """Graphene's client report (``complycube.reports``): the client's details and addresses,
    their risk, and every identity check and AML screen with its outcome. Made as it is asked for,
    never stored; the download is recorded (``aml.report_downloaded``). 404 when the client has
    never been checked; 503 when the PDF cannot be made."""
    require_permission(ctx.principal, AML_READ)
    found = await _resolve_client(ctx, key)
    if found.client_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This client has not been checked yet.")
    reader = await complycube_reports.reader_name(ctx.session, ctx.user_id)
    try:
        data = await complycube_reports.client_report(
            ctx.session, found.client_id, audience="operations", generated_by=reader
        )
    except complycube_reports.ReportUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The report could not be made. Try again."
        ) from exc
    await actions.record_report_download(
        ctx.session,
        event_type=actions.AML_REPORT_EVENT,
        principal=ctx.principal,
        tenant_id=ctx.own_tenant_id,
        target_tenant_id=found.tenant_id,
        actor_id=ctx.user_id,
        route="/aml/clients/{key}/report",
        path_params={"key": found.key},
        report="client",
    )
    return complycube_reports.response(data, f"client-report-{found.key}.pdf")


@router.get(
    "/checks/{uid}/report",
    name="get-aml-check-report",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def get_check_report(uid: str, ctx: AmlContext = Depends(aml_session)) -> Response:
    """Graphene's report of one check (``complycube.reports``): an identity check with its
    analysis, or an AML screen with the lists it searched and each match; with the decision and
    its comment once the check is decided. Made as it is asked for, never stored; the download is
    recorded (``aml.report_downloaded``). 404 for a check of another kind; 503 when the PDF cannot
    be made."""
    require_permission(ctx.principal, AML_READ)
    row = await _check_row(ctx, uid)
    if row["kind"] not in complycube_reports.REPORTED_KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no report of this check.")
    reader = await complycube_reports.reader_name(ctx.session, ctx.user_id)
    analysis = (
        identity_analysis(row["result"]) if row["kind"] == "identity" and row["result"] else None
    )
    try:
        data = await complycube_reports.check_report(
            ctx.session,
            int(row["id"]),
            audience="operations",
            generated_by=reader,
            analysis=analysis,
        )
    except complycube_reports.ReportUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The report could not be made. Try again."
        ) from exc
    await actions.record_report_download(
        ctx.session,
        event_type=actions.AML_REPORT_EVENT,
        principal=ctx.principal,
        tenant_id=ctx.own_tenant_id,
        target_tenant_id=int(row["tenant_id"]),
        actor_id=ctx.user_id,
        route="/aml/checks/{uid}/report",
        path_params={"uid": uid},
        report=str(row["kind"]),
    )
    return complycube_reports.response(
        data, complycube_reports.check_filename(str(row["kind"]), uid)
    )
