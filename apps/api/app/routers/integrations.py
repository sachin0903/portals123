"""SEI custodian/software platform integrations — the Graphene-ops-only approval dashboard.

Ported from the legacy `platform` repo's admin-only "Integrations" screen: which SEI
custodian/software platforms a firm may use, per-platform feature toggles, and job-run history.

`firm_id` names which real portals tenant (`auth.tenants`) this integration belongs to
(0246_integrations_tenant) -- it used to be SEI's own firm CODE (`integrations.firms`), the DSR
grants under it, and the SEI<->Intelliflo Office advisor-identity mapping scoped by it, all
ported from the legacy screen along with it. 0246 retired all three along with the "Provider"
field (never populated): once `firm_id` means a portals tenant rather than an SEI firm, none of
SEI's own firm/DSR/advisor reference data resolves against it any more, and the Provider dropdown
had no displayed data depending on it either. See 0246_integrations_tenant.sql for the full
reasoning.

This is Graphene-ops-only data, not tenant data in the RLS sense: `integrations.*` is
ENABLE+FORCE ROW LEVEL SECURITY with ZERO policies (see 0231_integrations_schema.sql), so access is
enforced here, in the router, via `require_permission` -- never by a WHERE-clause. Every handler
opens `admin_session` (service_role, cross-tenant, coarsely gated to Graphene operations staff)
and then calls `require_permission` as the first line of its body.

Deliberately NOT ported: `GET /integrations/providers` -- confirmed dead/broken in the legacy
repo (queried a nonexistent table, had no auth guard, and no component called it).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from intelliflo import client as intelliflo_client
from intelliflo.client import IntellifloApiError, IntellifloAuthError
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import listing, providers
from app.auth import Principal, get_current_principal, require_permission
from app.db import admin_session
from app.permissions import INTEGRATIONS_READ, INTEGRATIONS_WRITE
from app.routers.crm import (
    IoClientOut,
    IoClientUnlinkOut,
    IoPlanOut,
    IoUnlinkOut,
    _io_client_id_for_contact,
    _io_client_out,
    _io_client_search_tiers,
    _io_plan_out,
    _unlink_account_io,
    _unlink_contact_io,
    _upsert_account_io_link,
    _upsert_contact_io_link,
)

router = APIRouter(prefix="/integrations", tags=["integrations"])

PlatformStatus = Literal["pending", "approved", "rejected"]


# ---------------------------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------------------------


class TenantOut(BaseModel):
    """A real portals tenant (`auth.tenants`), as the edit drawer's Firm dropdown now offers it --
    replaces `FirmOut`'s SEI firm codes (0210)."""

    id: int
    name: str


class FeatureOut(BaseModel):
    code: str
    description: str | None = None


class PlatformIntegrationOut(BaseModel):
    uid: str
    #: The portals tenant (`auth.tenants.id`) this integration belongs to (0210). `None` until an
    #: operator picks one -- not every integration has been assigned a firm yet.
    firm_id: int | None
    firm_description: str | None
    platform: str | None  # aliased from platforms.platform_name -- the legacy list bug this fixes
    tenant_id: str
    tenant_name: str
    status: str
    enabled: bool
    installed: bool
    feature_ids: list[str]
    #: aliased from platforms.justification. On the base (list) type, not detail-only: the edit
    #: sheet opens from either the list's row-click or the detail page's header, and needs this to
    #: seed its Reason field either way -- a rejected integration's own stored reason, not a blank
    #: the operator must retype before any OTHER field (firm, features, enabled) can be saved.
    rejection_reason: str | None
    created_date: datetime
    updated_date: datetime


class PlatformIntegrationDetailOut(PlatformIntegrationOut):
    application_data: dict[str, Any] | None = None
    approved_by: str | None  # aliased from platforms.actioned_by
    updated_by: str | None


class IntegrationDetailResponse(BaseModel):
    integration: PlatformIntegrationDetailOut
    firms: list[TenantOut]
    features: list[FeatureOut]
    #: This integration's single most recent run, independent of the Job Runs tab's own page/
    #: search -- see `_last_job_run`. `None` when it has never run.
    last_run: JobRunOut | None = None


class UpdatePlatformIntegration(BaseModel):
    """A full replacement (PUT): every field but `reason` must be sent. There are no defaults,
    because a default here is a write -- an omitted `status` would un-approve the integration, an
    omitted `firm_id` un-assign it and an omitted `features` clear them. `firm_id: null` and
    `features: []` are how a caller says "none"."""

    firm_id: int | None
    status: PlatformStatus
    enabled: bool
    features: list[str]  # feature codes
    reason: str | None = None


class JobRunOut(BaseModel):
    uid: str
    run_date: datetime
    feature: str
    status: str  # "success" | "failed"
    error_message: str | None
    record_count: int | None
    newly_added: int | None
    failed: int | None
    data_date: date | None


class JobRunsPage(BaseModel):
    items: list[JobRunOut]
    total: int
    page: int
    limit: int


# ---------------------------------------------------------------------------------------------
# Shared query helpers
# ---------------------------------------------------------------------------------------------

# Joins platforms+tenants+platform_features in one statement (this codebase builds such joins
# inline per-endpoint rather than a SQL view, e.g. lookthrough's admin_list). `platform_name AS
# platform` is the fix: the legacy list endpoint forgot this alias while its own detail endpoint
# got it right, so a naive port would silently drop the column here.
_INTEGRATION_SELECT_TEMPLATE = """
    SELECT p.uid, p.firm_id, t.name AS firm_description, p.platform_name AS platform,
           p.tenant_id, p.tenant_name, p.status::text AS status, p.enabled,
           p.installed, p.application_data, p.actioned_by, p.justification, p.updated_by,
           p.created_at, p.updated_at,
           COALESCE(
             array_agg(DISTINCT pf.feature_code) FILTER (WHERE pf.feature_code IS NOT NULL),
             '{{}}'
           ) AS feature_ids
      FROM integrations.platforms p
      LEFT JOIN auth.tenants t ON t.id = p.firm_id
      LEFT JOIN integrations.platform_features pf ON pf.platform_id = p.id
     {where}
     GROUP BY p.id, t.name
"""


def _integration_select(where: str = "") -> str:
    """The shared list/detail query, with an optional fixed WHERE clause spliced in before the
    GROUP BY. `where` is always a literal string built by callers in this module (never request
    data); the values it references are still bound parameters."""
    return _INTEGRATION_SELECT_TEMPLATE.format(where=where)  # noqa: S608 - `where` is a fixed clause built in this module, never request data


def _list_out(row: dict[str, Any]) -> PlatformIntegrationOut:
    return PlatformIntegrationOut(
        uid=row["uid"],
        firm_id=row["firm_id"],
        firm_description=row["firm_description"],
        platform=row["platform"],
        tenant_id=row["tenant_id"],
        tenant_name=row["tenant_name"],
        status=row["status"],
        enabled=row["enabled"],
        installed=row["installed"],
        feature_ids=list(row["feature_ids"] or []),
        rejection_reason=row["justification"],
        created_date=row["created_at"],
        updated_date=row["updated_at"],
    )


def _detail_out(row: dict[str, Any]) -> PlatformIntegrationDetailOut:
    base = _list_out(row)
    return PlatformIntegrationDetailOut(
        **base.model_dump(),
        application_data=row["application_data"],
        approved_by=str(row["actioned_by"]) if row["actioned_by"] else None,
        updated_by=str(row["updated_by"]) if row["updated_by"] else None,
    )


async def _list_tenants(session: AsyncSession) -> list[TenantOut]:
    """Every real portals tenant -- the edit drawer's Firm dropdown (0210; replaces the SEI firm
    codes `_list_firms` used to offer). `is_model` excludes the template/system tenant, same
    filter `admin.list_tenants` applies for the same reason."""
    result = await session.execute(
        text("SELECT id, name FROM auth.tenants WHERE NOT is_model ORDER BY name")
    )
    return [TenantOut(**r) for r in result.mappings().all()]


async def _list_features(session: AsyncSession) -> list[FeatureOut]:
    result = await session.execute(
        text("SELECT code, description FROM integrations.features ORDER BY code")
    )
    return [FeatureOut(**r) for r in result.mappings().all()]


# ---------------------------------------------------------------------------------------------
# Reference-data + static-path endpoints. Registered BEFORE the /{uid} routes below so FastAPI
# never mistakes e.g. "advisors-map" for a uid.
# ---------------------------------------------------------------------------------------------


@router.get("/features", name="list-integration-features")
async def list_integration_features(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> list[FeatureOut]:
    require_permission(principal, INTEGRATIONS_READ)
    return await _list_features(session)


@router.get("/tenants", name="list-integration-tenants")
async def list_integration_tenants(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> list[TenantOut]:
    require_permission(principal, INTEGRATIONS_READ)
    return await _list_tenants(session)


# ---------------------------------------------------------------------------------------------
# The "Clients and Accounts" tab -- linking a portals client/account to its Intelliflo Office
# client/plan from the admin side, the same write `link_contact_to_io`/`link_account_to_io`
# (crm.py) perform from the platform portal, reused here via their shared `_upsert_*` helpers.
#
# Deliberately NOT scoped to one tenant, even though `integrations.platforms.firm_id` is now a
# real FK to `auth.tenants` (0210): not every integration has had a firm picked, and scoping this
# tab to it is a separate, unasked-for change -- the Clients picker still searches across every
# tenant's contacts, and the write resolves `tenant_id` off whichever contact is actually picked
# (and verifies the picked account belongs to that same contact), so an operator never has to say
# which tenant up front.
# ---------------------------------------------------------------------------------------------

#: The Clients table's own page size -- fixed server-side (the frontend still passes it, so the
#: contract is explicit rather than an implicit shared constant on both sides).
_CLIENTS_TABLE_MAX_PAGE_SIZE = 100


class AccountSearchResult(BaseModel):
    account_uid: str
    display_name: str


def _account_display_name(name: str | None, account_number: str | None, uid: str) -> str:
    """ "name (number)" for the Accounts dropdown -- the number is the account's account number
    (`_account_number_sql`), falling back to the account's own `uid` when it has none."""
    number = account_number or uid
    return f"{name} ({number})" if name else number


def _account_number_sql(alias: str) -> str:
    """SQL for one account's own account number: the `external_id` of its primary link at a
    custodian (SEI, Seccl or Morgan Lloyd), the same value `link_account_to_io` pushes into
    Intelliflo as the policy number. Binds `:custodians` (`providers.custodians()`).

    It is read from `platform.links` and from nowhere else. No column on `platform.accounts` is
    consulted for it: the book-sink merge records the number on that link and on nothing of the
    account row, so a second source for it would be a second source of truth."""
    return (
        "(SELECT sl.external_id FROM platform.links sl "  # noqa: S608 - `alias` is the caller's literal
        f"WHERE sl.tenant_id = {alias}.tenant_id AND sl.entity_type = 'account' "
        f"AND sl.entity_id = {alias}.id AND sl.link_type = 'primary' "
        "AND sl.provider::text = ANY(:custodians) ORDER BY sl.provider LIMIT 1)"
    )


class LinkClientAccountBody(BaseModel):
    contact_uid: str
    client_id_io: str
    #: Accepted, but no longer read: `_upsert_contact_io_link` re-verifies `client_id_io` against
    #: this contact's own Intelliflo search and stores what THAT returns, never a caller's claim
    #: (`crm.LinkIoClientBody` dropped its own `raw` for the same reason). Kept on the request
    #: body rather than removed so an unchanged frontend keeps sending a valid payload.
    client_raw: dict[str, Any]
    account_uid: str
    plan_id_io: str
    #: The exact `IoPlanOut.raw` picked from the IO Accounts dropdown -- same posture as
    #: `crm.LinkIoPlanBody.raw`.
    plan_raw: dict[str, Any]


class LinkClientAccountOut(BaseModel):
    client: IoClientOut
    plan: IoPlanOut


class LinkedPairOut(BaseModel):
    """The account/plan half of an already-complete mapping -- a `ClientsTableRowOut`'s own
    contact is already known from the row it sits on, so only what's downstream of it travels
    here."""

    client: IoClientOut
    account_uid: str
    account_display_name: str
    plan: IoPlanOut


class ClientLinkBody(BaseModel):
    contact_uid: str
    client_id_io: str


class AccountLinkBody(BaseModel):
    #: The integration the link is made for: its provider decides which plans can be linked.
    integration_uid: str
    account_uid: str
    plan_id_io: str


class AccountsTableRowOut(BaseModel):
    """One ACCOUNT of a client that is already mapped to an Intelliflo client: the Accounts tab has
    a row per account, so every linked account is visible (the Clients tab has a row per client)."""

    account_uid: str
    account_display_name: str
    #: The custodian's account number (`_account_number_sql`), or None for an account without one.
    account_number: str | None
    contact_uid: str
    contact_display_name: str
    #: The Intelliflo client this account's client is mapped to; the plans dropdown is its plans.
    client_id_io: str
    #: The account's current plan link, or None when it is not linked yet.
    plan: IoPlanOut | None


class AccountsTablePageOut(BaseModel):
    items: list[AccountsTableRowOut]
    total: int
    #: Accounts of this firm whose CLIENT is not mapped yet, so they cannot be linked here. Shown as
    #: a pointer to the Clients tab rather than listed.
    accounts_awaiting_client: int


class ClientsTableRowOut(BaseModel):
    contact_uid: str
    contact_display_name: str
    contact_tenant_name: str | None
    #: The Intelliflo client this client is mapped to, or None. Independent of accounts.
    io_client: IoClientOut | None = None
    #: How many of this client's accounts carry an Intelliflo link. While it is above zero the
    #: client's Intelliflo client cannot be changed (`_upsert_contact_io_link`): they are unlinked
    #: first, one at a time.
    linked_account_count: int = 0
    #: `None` when this client has no complete Intelliflo mapping yet. A client with more than one
    #: linked account (each account its own `platform.links` row) surfaces only the most recently
    #: updated one -- this table has one Account dropdown per client row, same as the per-client
    #: Account picker it replaced, so it can only show/restore one at a time.
    existing_link: LinkedPairOut | None


class ClientsTablePageOut(BaseModel):
    items: list[ClientsTableRowOut]
    total: int


async def _integration_firm_id(session: AsyncSession, integration_uid: str) -> int | None:
    row = (
        await session.execute(
            text("SELECT firm_id FROM integrations.platforms WHERE uid = :uid"),
            {"uid": integration_uid},
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Integration not found.")
    firm_id: int | None = row[0]
    return firm_id


async def _integration_provider_id(session: AsyncSession, integration_uid: str) -> str | None:
    """The Intelliflo product provider this integration pushes to (`platforms.provider_id`).

    Valuation batches and transactions are submitted under that one provider and Intelliflo only
    matches a plan that sits under it, so a plan of any other provider can be linked here but can
    never receive data. None when the integration has no provider recorded."""
    row = (
        await session.execute(
            text("SELECT provider_id FROM integrations.platforms WHERE uid = :uid"),
            {"uid": integration_uid},
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Integration not found.")
    provider_id = str(row[0] or "").strip()
    return provider_id or None


def _plan_provider(plan: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """A plan's `(productProvider.id, productProvider.name)` as Intelliflo reports them."""
    provider = plan.get("productProvider")
    if not isinstance(provider, Mapping):
        return None, None
    provider_id = provider.get("id")
    name = provider.get("name")
    return (
        str(provider_id) if provider_id is not None else None,
        str(name) if name is not None else None,
    )


#: A search term longer than this is a paste accident, not a name or an account number. Capped so
#: the ILIKE patterns below stay cheap.
_CLIENTS_SEARCH_MAX_LENGTH = 100

#: Narrows `crm.contacts c` to the ones the term finds: the client's own name, OR any account of
#: theirs by name or number (the same set the Account dropdown lists, closed ones included).
#: "Number" is what the tab can show for one: the `external_id` of any primary link on the account
#: (the custodian's account number, or the Intelliflo policy number once mapped), or the account
#: `uid` the dropdown falls back to (see `_account_display_name`). A client matched through an
#: account still lists ALL of its accounts -- the row's Account dropdown is the client's, not the
#: match's.
_CLIENTS_SEARCH_CLAUSE = (
    " AND (c.name ILIKE :search ESCAPE '\\' "
    "   OR c.first_name ILIKE :search ESCAPE '\\' "
    "   OR c.surname ILIKE :search ESCAPE '\\' "
    "   OR concat_ws(' ', c.first_name, c.surname) ILIKE :search ESCAPE '\\' "
    "   OR EXISTS ("
    "     SELECT 1 FROM platform.accounts sa "
    "     WHERE sa.contact_id = c.id AND sa.tenant_id = c.tenant_id "
    "       AND (sa.name ILIKE :search ESCAPE '\\' "
    "         OR sa.uid ILIKE :search ESCAPE '\\' "
    "         OR EXISTS ("
    "           SELECT 1 FROM platform.links sl "
    "           WHERE sl.entity_type = 'account' AND sl.entity_id = sa.id "
    "             AND sl.tenant_id = sa.tenant_id AND sl.link_type = 'primary' "
    "             AND sl.external_id ILIKE :search ESCAPE '\\')))) "
)


@router.get("/clients-table", name="clients-table")
async def clients_table(
    integration_uid: str,
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> ClientsTablePageOut:
    """Every client belonging to this integration's firm, cross-tenant session but filtered down to
    one tenant (see this module's own docstring on `firm_id`) -- the Clients and Accounts tab's
    Client column is this listing: an operator pages through the book, or narrows it with
    `search` (client name, account name or account number -- see `_CLIENTS_SEARCH_CLAUSE`). The
    search is SERVER-side because the listing is paginated: filtering the one page the browser
    holds would say "no client called Halloway" when it means "not on this page". `total` honours
    it, so the pager counts the matches, not the book. An integration with no firm assigned yet
    shows no clients at all, same as "no results", since there is no tenant to scope the listing
    to. Each row also carries whichever complete Intelliflo mapping this client already has
    (`LinkedPairOut`), so a page load restores it without a second round trip, the same way the
    tab used to restore from a dedicated endpoint."""
    require_permission(principal, INTEGRATIONS_READ)
    page = max(1, page)
    page_size = max(1, min(page_size, _CLIENTS_TABLE_MAX_PAGE_SIZE))

    firm_id = await _integration_firm_id(session, integration_uid)
    if firm_id is None:
        return ClientsTablePageOut(items=[], total=0)

    term = (search or "").strip()[:_CLIENTS_SEARCH_MAX_LENGTH]
    scope = "c.deleted_at IS NULL AND c.tenant_id = :firm_id"
    params: dict[str, Any] = {"firm_id": firm_id}
    if term:
        scope += _CLIENTS_SEARCH_CLAUSE
        params["search"] = f"%{listing.escape_like(term)}%"

    total = (
        await session.execute(
            text(f"SELECT count(*) FROM crm.contacts c WHERE {scope}"),  # noqa: S608
            params,
        )
    ).scalar_one()

    rows = (
        (
            await session.execute(
                text(
                    "WITH page_contacts AS ("  # noqa: S608 - `scope` is a fixed clause; the term binds
                    "  SELECT c.id, c.tenant_id, c.uid, c.name, c.first_name, c.surname, "
                    "         t.name AS tenant_name "
                    "  FROM crm.contacts c JOIN auth.tenants t ON t.id = c.tenant_id "
                    f"  WHERE {scope} "
                    "  ORDER BY c.surname NULLS LAST, c.name NULLS LAST, c.id "
                    "  LIMIT :limit OFFSET :offset"
                    "), link AS ("
                    "  SELECT DISTINCT ON (a.contact_id) "
                    "         a.contact_id, cl.external_id AS client_id_io, "
                    "         cl.data AS client_data, "
                    "         a.uid AS account_uid, a.name AS account_name, "
                    f"         {_account_number_sql('a')} AS account_number, al.data AS plan_data "
                    "  FROM platform.links al "
                    "  JOIN platform.accounts a "
                    "    ON a.id = al.entity_id AND a.tenant_id = al.tenant_id "
                    "  JOIN platform.links cl ON cl.tenant_id = al.tenant_id "
                    "    AND cl.entity_type = 'contact' AND cl.entity_id = a.contact_id "
                    "    AND cl.provider = 'intelliflo' AND cl.link_type = 'primary' "
                    "  WHERE al.entity_type = 'account' AND al.provider = 'intelliflo' "
                    "    AND al.link_type = 'primary' "
                    "    AND a.contact_id IN (SELECT id FROM page_contacts) "
                    "  ORDER BY a.contact_id, al.updated_at DESC"
                    ") "
                    "SELECT pc.*, l.client_id_io, l.client_data, l.account_uid, l.account_name, "
                    "       l.account_number, l.plan_data, "
                    "       (SELECT x.external_id FROM platform.links x "
                    "         WHERE x.tenant_id = pc.tenant_id AND x.entity_type = 'contact' "
                    "           AND x.entity_id = pc.id AND x.provider = 'intelliflo' "
                    "           AND x.link_type = 'primary') AS io_client_id, "
                    "       (SELECT x.data FROM platform.links x "
                    "         WHERE x.tenant_id = pc.tenant_id AND x.entity_type = 'contact' "
                    "           AND x.entity_id = pc.id AND x.provider = 'intelliflo' "
                    "           AND x.link_type = 'primary') AS io_client_data, "
                    "       (SELECT count(*) FROM platform.links y JOIN platform.accounts a2 "
                    "          ON a2.id = y.entity_id AND a2.tenant_id = y.tenant_id "
                    "         WHERE y.tenant_id = pc.tenant_id AND y.entity_type = 'account' "
                    "           AND y.provider = 'intelliflo' AND y.link_type = 'primary' "
                    "           AND a2.contact_id = pc.id) AS linked_account_count "
                    "FROM page_contacts pc LEFT JOIN link l ON l.contact_id = pc.id "
                    "ORDER BY pc.surname NULLS LAST, pc.name NULLS LAST, pc.id"
                ),
                {
                    **params,
                    "custodians": list(providers.custodians()),
                    "limit": page_size,
                    "offset": (page - 1) * page_size,
                },
            )
        )
        .mappings()
        .all()
    )

    def row_out(r: Mapping[str, Any]) -> ClientsTableRowOut:
        existing_link = (
            None
            if r["client_id_io"] is None
            else LinkedPairOut(
                client=_io_client_out({**(r["client_data"] or {}), "id": r["client_id_io"]}),
                account_uid=r["account_uid"],
                account_display_name=_account_display_name(
                    r["account_name"], r["account_number"], r["account_uid"]
                ),
                plan=_io_plan_out(r["plan_data"] or {}),
            )
        )
        return ClientsTableRowOut(
            io_client=(
                None
                if r["io_client_id"] is None
                else _io_client_out({**(r["io_client_data"] or {}), "id": r["io_client_id"]})
            ),
            linked_account_count=int(r["linked_account_count"] or 0),
            contact_uid=r["uid"],
            contact_display_name=(
                r["name"] or " ".join(p for p in (r["first_name"], r["surname"]) if p) or r["uid"]
            ),
            contact_tenant_name=r["tenant_name"],
            existing_link=existing_link,
        )

    # `dict()` per row: `RowMapping` does not satisfy `Mapping[str, Any]` for mypy, and widening
    # `row_out`'s signature to `Any` to save a copy would trade a real type for a micro-gain.
    return ClientsTablePageOut(items=[row_out(dict(r)) for r in rows], total=total)


async def _contact_row_by_uid(session: AsyncSession, contact_uid: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, tenant_id, type, name, first_name, surname, national_identifier, "
                    "       national_identifier_type "
                    "FROM crm.contacts WHERE uid = :uid AND deleted_at IS NULL"
                ),
                {"uid": contact_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


@router.get("/clients/{contact_uid}/io-clients", name="io-clients-for-contact")
async def io_clients_for_contact(
    contact_uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> list[IoClientOut]:
    """Search Intelliflo for this picked client's IO counterpart, narrowest filter first: NI
    number (when this contact has one on file), then first+last name together -- stopping at the
    first tier that finds anything. No single-name tier: in the shared Intelliflo tenant it would
    return other firms' clients (see `_io_client_search_tiers`). A vendor auth/API failure becomes
    a 502, never a raw 500."""
    require_permission(principal, INTEGRATIONS_READ)
    contact = await _contact_row_by_uid(session, contact_uid)
    if contact is None:
        raise HTTPException(status_code=404, detail="Client not found")

    clients: list[dict[str, Any]] = []
    try:
        for tier in _io_client_search_tiers(contact):
            clients = await intelliflo_client.get_clients(**tier)
            if clients:
                break
    except (IntellifloAuthError, IntellifloApiError) as exc:
        raise HTTPException(status_code=502, detail=f"Intelliflo request failed: {exc}") from exc
    return [_io_client_out(c) for c in clients]


@router.get("/clients/{contact_uid}/accounts", name="accounts-for-contact")
async def accounts_for_contact(
    contact_uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> list[AccountSearchResult]:
    """This client's own platform accounts, any custodian -- the Accounts dropdown, populated once
    a Client is picked. Cross-tenant session, but `contact_id` already pins one tenant's rows, and
    `clients_table` has already scoped which clients are reachable here to the integration's own
    firm."""
    require_permission(principal, INTEGRATIONS_READ)
    contact = await _contact_row_by_uid(session, contact_uid)
    if contact is None:
        raise HTTPException(status_code=404, detail="Client not found")
    rows = (
        (
            await session.execute(
                text(
                    f"SELECT a.uid, a.name, {_account_number_sql('a')} AS account_number "  # noqa: S608
                    "FROM platform.accounts a WHERE a.contact_id = :cid ORDER BY a.id"
                ),
                {"cid": contact["id"], "custodians": list(providers.custodians())},
            )
        )
        .mappings()
        .all()
    )
    return [
        AccountSearchResult(
            account_uid=r["uid"],
            display_name=_account_display_name(r["name"], r["account_number"], r["uid"]),
        )
        for r in rows
    ]


@router.get("/io-clients/{client_id_io}/accounts", name="io-accounts-for-io-client")
async def io_accounts_for_client(
    client_id_io: str,
    integration_uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> list[IoPlanOut]:
    """This IO client's plans UNDER THIS INTEGRATION'S PROVIDER -- the IO Accounts dropdown,
    populated once an IO Client is picked. A client's plans under any other provider are left out:
    the integration pushes valuations and transactions under one provider, and a plan under another
    can never receive them. Only plans with NO `policyNumber` (null, empty or blank) are offered:
    pushing a fresh policy number into exactly such a plan is this tab's whole point, and a plan
    that already has one belongs to something else. The link endpoints enforce the same rule."""
    require_permission(principal, INTEGRATIONS_READ)
    provider_id = await _integration_provider_id(session, integration_uid)
    try:
        plans = await intelliflo_client.get_client_plans(client_id_io)
    except (IntellifloAuthError, IntellifloApiError) as exc:
        raise HTTPException(status_code=502, detail=f"Intelliflo request failed: {exc}") from exc
    if provider_id is not None:
        plans = [p for p in plans if _plan_provider(p)[0] == provider_id]
    # Only plans Intelliflo holds no policy number for: the link pushes ours into exactly such one.
    plans = [p for p in plans if not str(p.get("policyNumber") or "").strip()]
    return [_io_plan_out(p) for p in plans]


@router.post("/clients-accounts-link", name="link-client-and-account-to-io")
async def link_client_and_account_to_io(
    body: LinkClientAccountBody,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> LinkClientAccountOut:
    """The tab's one Link action: resolves the picked client/account by uid, then upserts BOTH the
    contact-level and account-level Intelliflo links in one step, via the exact same
    `_upsert_contact_io_link`/`_upsert_account_io_link` helpers `crm.py`'s own endpoints use --
    so the two paths (platform portal, admin ops) can never drift, including the account link's
    push of the custody account number into Intelliflo's plan `policyNumber`.

    `tenant_id` is read off the picked CONTACT, never supplied directly; the picked account is
    verified to belong to that same contact first, so an operator cannot wire together two
    different clients' records by mistake.
    """
    require_permission(principal, INTEGRATIONS_WRITE)
    contact = await _contact_row_by_uid(session, body.contact_uid)
    if contact is None:
        raise HTTPException(status_code=404, detail="Client not found")
    account_id = (
        await session.execute(
            text("SELECT id FROM platform.accounts WHERE uid = :uid AND contact_id = :cid"),
            {"uid": body.account_uid, "cid": contact["id"]},
        )
    ).scalar_one_or_none()
    if account_id is None:
        raise HTTPException(status_code=400, detail="That account does not belong to this client")

    tenant_id = contact["tenant_id"]
    # `body.client_raw` is accepted but no longer trusted: `_upsert_contact_io_link` re-verifies
    # `client_id_io` against this contact's own Intelliflo search and stores what THAT returns,
    # the same write-time check `crm.link_contact_to_io` relies on.
    client_out = await _upsert_contact_io_link(session, tenant_id, contact["id"], body.client_id_io)
    plan_out = await _upsert_account_io_link(
        session, tenant_id, account_id, body.plan_id_io, body.plan_raw, only_unassigned_plans=True
    )
    return LinkClientAccountOut(client=client_out, plan=plan_out)


class UnlinkAccountBody(BaseModel):
    #: Remove only the local mapping and leave Intelliflo alone. For a plan Intelliflo will not let
    #: us blank, so an operator is never stuck with an account they cannot unlink.
    skip_intelliflo: bool = False


@router.post("/accounts/{account_uid}/io-unlink", name="unlink-account-from-io")
async def unlink_account_from_io(
    account_uid: str,
    body: UnlinkAccountBody | None = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> IoUnlinkOut:
    """Unlink ONE account from its Intelliflo plan: blank the plan's policy number in Intelliflo
    (when this link put our number there), then remove the account's Intelliflo link.

    One account at a time, by design. Changing a client's Intelliflo client is refused while any of
    its accounts is still mapped (`_upsert_contact_io_link`), and this is how they are released.
    `tenant_id` is read off the account, never supplied."""
    require_permission(principal, INTEGRATIONS_WRITE)
    account = (
        (
            await session.execute(
                text("SELECT id, tenant_id FROM platform.accounts WHERE uid = :uid"),
                {"uid": account_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return await _unlink_account_io(
        session,
        account["tenant_id"],
        account["id"],
        skip_intelliflo=bool(body and body.skip_intelliflo),
    )


@router.post("/clients/{contact_uid}/io-unlink", name="unlink-client-from-io")
async def unlink_client_from_io(
    contact_uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> IoClientUnlinkOut:
    """Unlink a client from its Intelliflo client: remove the client's link row, and nothing else.

    409 while any of the client's accounts is still linked to an Intelliflo plan: those are
    unlinked first, one at a time, on the Accounts tab (`unlink_account_from_io`). The inverse of
    `link_client_to_io`. `tenant_id` is read off the contact, never supplied."""
    require_permission(principal, INTEGRATIONS_WRITE)
    contact = await _contact_row_by_uid(session, contact_uid)
    if contact is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return await _unlink_contact_io(session, contact["tenant_id"], contact["id"])


#: The Accounts tab's search: the client's name, the account's name, or its number (the
#: `external_id` of any primary link on it, or the account `uid` the dropdown falls back to). Same
#: terms as the Clients tab, applied to an account row instead of a client row.
_ACCOUNTS_SEARCH_CLAUSE = (
    " AND (c.name ILIKE :search ESCAPE '\\' "
    "   OR c.first_name ILIKE :search ESCAPE '\\' "
    "   OR c.surname ILIKE :search ESCAPE '\\' "
    "   OR concat_ws(' ', c.first_name, c.surname) ILIKE :search ESCAPE '\\' "
    "   OR a.name ILIKE :search ESCAPE '\\' "
    "   OR a.uid ILIKE :search ESCAPE '\\' "
    "   OR EXISTS ("
    "     SELECT 1 FROM platform.links sl "
    "     WHERE sl.entity_type = 'account' AND sl.entity_id = a.id "
    "       AND sl.tenant_id = a.tenant_id AND sl.link_type = 'primary' "
    "       AND sl.external_id ILIKE :search ESCAPE '\\')) "
)

_CLIENT_LINK_JOIN = (
    "JOIN platform.links cl ON cl.tenant_id = a.tenant_id AND cl.entity_type = 'contact' "
    "  AND cl.entity_id = c.id AND cl.provider = 'intelliflo' AND cl.link_type = 'primary' "
)


@router.get("/accounts-table", name="accounts-table")
async def accounts_table(
    integration_uid: str,
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    unlinked_only: bool = False,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> AccountsTablePageOut:
    """The Accounts tab: one row per account of this integration's firm whose CLIENT is already
    mapped to an Intelliflo client, with the account's plan link when it has one.

    Only mapped clients' accounts are listed: an Intelliflo plan only exists under an Intelliflo
    client, the plan is verified against THAT client when linked, and an account cannot be synced
    without its client mapped. Accounts of unmapped clients are counted (`accounts_awaiting_client`)
    so the tab can point at the Clients tab. `search` and `unlinked_only` narrow the listing, and
    `total` honours them."""
    require_permission(principal, INTEGRATIONS_READ)
    page = max(1, page)
    page_size = max(1, min(page_size, _CLIENTS_TABLE_MAX_PAGE_SIZE))
    firm_id = await _integration_firm_id(session, integration_uid)
    if firm_id is None:
        return AccountsTablePageOut(items=[], total=0, accounts_awaiting_client=0)

    term = (search or "").strip()[:_CLIENTS_SEARCH_MAX_LENGTH]
    scope = "a.tenant_id = :firm_id AND c.deleted_at IS NULL"
    params: dict[str, Any] = {"firm_id": firm_id, "custodians": list(providers.custodians())}
    if term:
        scope += _ACCOUNTS_SEARCH_CLAUSE
        params["search"] = f"%{listing.escape_like(term)}%"
    link_join = (
        "LEFT JOIN platform.links al ON al.tenant_id = a.tenant_id AND al.entity_type = 'account' "
        "  AND al.entity_id = a.id AND al.provider = 'intelliflo' AND al.link_type = 'primary' "
    )
    if unlinked_only:
        scope += " AND al.id IS NULL"
    from_sql = (
        "FROM platform.accounts a "
        "JOIN crm.contacts c ON c.id = a.contact_id AND c.tenant_id = a.tenant_id "
        f"{_CLIENT_LINK_JOIN}{link_join}"
    )

    total = (
        await session.execute(
            text(f"SELECT count(*) {from_sql} WHERE {scope}"),  # noqa: S608
            {k: v for k, v in params.items() if k != "custodians"},
        )
    ).scalar_one()
    rows = (
        (
            await session.execute(
                text(
                    f"SELECT a.uid AS account_uid, a.name AS account_name, "  # noqa: S608
                    f"       {_account_number_sql('a')} AS account_number, "
                    "       c.uid AS contact_uid, c.name AS contact_name, c.first_name, c.surname, "
                    "       cl.external_id AS client_id_io, al.data AS plan_data "
                    f"{from_sql} WHERE {scope} "
                    "ORDER BY c.surname NULLS LAST, c.name NULLS LAST, a.name NULLS LAST, a.id "
                    "LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": page_size, "offset": (page - 1) * page_size},
            )
        )
        .mappings()
        .all()
    )
    awaiting = (
        await session.execute(
            text(
                "SELECT count(*) FROM platform.accounts a "  # noqa: S608
                "JOIN crm.contacts c ON c.id = a.contact_id AND c.tenant_id = a.tenant_id "
                "WHERE a.tenant_id = :firm_id AND c.deleted_at IS NULL AND NOT EXISTS ("
                "  SELECT 1 FROM platform.links cl WHERE cl.tenant_id = a.tenant_id "
                "    AND cl.entity_type = 'contact' AND cl.entity_id = c.id "
                "    AND cl.provider = 'intelliflo' AND cl.link_type = 'primary')"
            ),
            {"firm_id": firm_id},
        )
    ).scalar_one()

    items = [
        AccountsTableRowOut(
            account_uid=r["account_uid"],
            account_display_name=_account_display_name(
                r["account_name"], r["account_number"], r["account_uid"]
            ),
            account_number=r["account_number"],
            contact_uid=r["contact_uid"],
            contact_display_name=(
                r["contact_name"]
                or " ".join(p for p in (r["first_name"], r["surname"]) if p)
                or r["contact_uid"]
            ),
            client_id_io=r["client_id_io"],
            plan=None if r["plan_data"] is None else _io_plan_out(r["plan_data"]),
        )
        for r in rows
    ]
    return AccountsTablePageOut(items=items, total=total, accounts_awaiting_client=awaiting)


@router.post("/clients-link", name="link-client-to-io")
async def link_client_to_io(
    body: ClientLinkBody,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> IoClientOut:
    """The Clients tab's Link: map this client to an Intelliflo client, and nothing else. Refused
    (409) while any of the client's accounts is still mapped. The tenant is read off the contact."""
    require_permission(principal, INTEGRATIONS_WRITE)
    contact = await _contact_row_by_uid(session, body.contact_uid)
    if contact is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return await _upsert_contact_io_link(
        session, contact["tenant_id"], contact["id"], body.client_id_io
    )


@router.post("/accounts-link", name="link-account-plan")
async def link_account_plan(
    body: AccountLinkBody,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> IoPlanOut:
    """The Accounts tab's Link: map this account to a plan of its client's Intelliflo client.

    The client must already be mapped (409 otherwise), and the plan is taken from THAT client's own
    plans, never from a caller's claim, then linked through the same helper the platform portal uses
    (so the custodian number is pushed into the plan, and a plan already on another account is
    refused). `tenant_id` is read off the account."""
    require_permission(principal, INTEGRATIONS_WRITE)
    provider_id = await _integration_provider_id(session, body.integration_uid)
    account = (
        (
            await session.execute(
                text("SELECT id, tenant_id, contact_id FROM platform.accounts WHERE uid = :uid"),
                {"uid": body.account_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    client_id_io = await _io_client_id_for_contact(session, account["contact_id"])
    if client_id_io is None:
        raise HTTPException(
            status_code=409,
            detail="Link this account's client to Intelliflo first, on the Clients tab.",
        )
    try:
        plans = await intelliflo_client.get_client_plans(client_id_io)
    except (IntellifloAuthError, IntellifloApiError) as exc:
        raise HTTPException(status_code=502, detail=f"Intelliflo request failed: {exc}") from exc
    verified = next((p for p in plans if str(p.get("id")) == body.plan_id_io), None)
    if verified is None:
        raise HTTPException(
            status_code=400, detail="That plan does not belong to this client's Intelliflo record."
        )
    plan_provider_id, plan_provider_name = _plan_provider(verified)
    if provider_id is not None and plan_provider_id != provider_id:
        raise HTTPException(
            status_code=409,
            detail=(
                f"That plan is under the Intelliflo provider "
                f"{plan_provider_name or plan_provider_id}, but this integration pushes to "
                f"provider {provider_id}. Valuations and transactions only reach a plan under "
                "that provider; pick one of its plans."
            ),
        )
    return await _upsert_account_io_link(
        session,
        account["tenant_id"],
        account["id"],
        body.plan_id_io,
        verified,
        verified_plan=verified,
        only_unassigned_plans=True,
    )


# ---------------------------------------------------------------------------------------------
# The integrations list/detail/update/runs surface.
# ---------------------------------------------------------------------------------------------


@router.get("", name="list-integrations")
async def list_integrations(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> list[PlatformIntegrationOut]:
    require_permission(principal, INTEGRATIONS_READ)
    result = await session.execute(
        text(_integration_select() + " ORDER BY p.tenant_name, p.platform_name")
    )
    rows = [dict(r) for r in result.mappings().all()]
    return [_list_out(row) for row in rows]


@router.get("/{uid}", name="get-integration")
async def get_integration(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> IntegrationDetailResponse:
    require_permission(principal, INTEGRATIONS_READ)
    result = await session.execute(
        text(_integration_select("WHERE p.uid = :uid")),
        {"uid": uid},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Integration not found.")
    row_dict = dict(row)
    platform_id = (
        await session.execute(
            text("SELECT id FROM integrations.platforms WHERE uid = :uid"), {"uid": uid}
        )
    ).scalar_one()
    firms = await _list_tenants(session)
    features = await _list_features(session)
    last_run = await _last_job_run(session, platform_id)
    return IntegrationDetailResponse(
        integration=_detail_out(row_dict),
        firms=firms,
        features=features,
        last_run=last_run,
    )


@router.put("/{uid}", name="update-integration")
async def update_integration(
    uid: str,
    body: UpdatePlatformIntegration,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> PlatformIntegrationDetailOut:
    """Approve/reject/configure one integration.

    Fixes vs. the legacy version: `actioned_by` is resolved from the authenticated principal
    server-side (never trusted from the body); a reject with a blank reason 422s here too (the DB
    CHECK is the belt, this is the braces); feature membership is fully replaced
    (delete-then-insert) and the response's `feature_ids` is read back from the database, never
    echoed from the request.
    """
    require_permission(principal, INTEGRATIONS_WRITE)
    if body.status == "rejected" and not (body.reason or "").strip():
        raise HTTPException(
            status_code=422, detail="A rejection reason is required when rejecting an integration."
        )
    platform_id = (
        await session.execute(
            text("SELECT id FROM integrations.platforms WHERE uid = :uid"), {"uid": uid}
        )
    ).scalar_one_or_none()
    if platform_id is None:
        raise HTTPException(status_code=404, detail="Integration not found.")

    # A firm or feature that does not exist is the caller's mistake, so say so (422) rather than
    # let the foreign key fail the write as a 500.
    if body.firm_id is not None:
        firm_exists = (
            await session.execute(
                text("SELECT 1 FROM auth.tenants WHERE id = :id AND NOT is_model"),
                {"id": body.firm_id},
            )
        ).scalar_one_or_none()
        if firm_exists is None:
            raise HTTPException(status_code=422, detail=f"Unknown firm: {body.firm_id}.")
    if body.features:
        known = set(
            (
                await session.execute(
                    text(
                        "SELECT code FROM integrations.features "
                        "WHERE code = ANY(CAST(:codes AS text[]))"
                    ),
                    {"codes": body.features},
                )
            ).scalars()
        )
        unknown = sorted(set(body.features) - known)
        if unknown:
            raise HTTPException(
                status_code=422, detail=f"Unknown feature(s): {', '.join(unknown)}."
            )

    actor = (await session.execute(text("SELECT auth.uid()"))).scalar_one_or_none()

    await session.execute(
        text(
            """UPDATE integrations.platforms
                  SET firm_id       = :firm_id,
                      status        = CAST(:status AS integrations.platform_status),
                      enabled       = :enabled,
                      justification = :reason,
                      actioned_by   = :actor,
                      updated_by    = :actor,
                      updated_at    = now()
                WHERE id = :id"""
        ),
        {
            "firm_id": body.firm_id,
            "status": body.status,
            "enabled": body.enabled,
            "reason": body.reason,
            "actor": actor,
            "id": platform_id,
        },
    )

    # Full replace of feature membership -- delete then bulk-insert, not a diff.
    await session.execute(
        text("DELETE FROM integrations.platform_features WHERE platform_id = :id"),
        {"id": platform_id},
    )
    if body.features:
        await session.execute(
            text(
                "INSERT INTO integrations.platform_features (platform_id, feature_code) "
                "SELECT :id, code FROM unnest(CAST(:codes AS text[])) AS code"
            ),
            {"id": platform_id, "codes": body.features},
        )

    result = await session.execute(
        text(_integration_select("WHERE p.uid = :uid")),
        {"uid": uid},
    )
    row = dict(result.mappings().one())
    return _detail_out(row)


# Shared between `list_integration_runs` and `_last_job_run` -- per-feature stats live in
# `job_runs.data`, extracted here via the jsonb `->`/`->>` operators against the row's own `feature`
# column (never by string-formatting the feature name into the query). Two shapes exist: the legacy
# writer keyed the blob by feature name (sometimes in lowercase), and the engine now stores the
# feature's own counts at the top level, so the row's `data` itself is the fallback.
_JOB_RUN_STATS = "COALESCE(jr.data -> jr.feature, jr.data -> lower(jr.feature), jr.data)"
_JOB_RUN_SELECT_COLUMNS = f"""
    jr.uid, jr.run_date, jr.feature, jr.status, jr.error_message, jr.data_date,
    {_JOB_RUN_STATS} ->> 'record_count' AS record_count,
    {_JOB_RUN_STATS} ->> 'newly_added' AS newly_added,
    {_JOB_RUN_STATS} ->> 'failed' AS failed
"""


def _job_run_out(r: Mapping[str, Any]) -> JobRunOut:
    return JobRunOut(
        uid=r["uid"],
        run_date=r["run_date"],
        feature=r["feature"],
        status="success" if r["status"] else "failed",
        error_message=r["error_message"],
        record_count=int(r["record_count"]) if r["record_count"] is not None else None,
        newly_added=int(r["newly_added"]) if r["newly_added"] is not None else None,
        failed=int(r["failed"]) if r["failed"] is not None else None,
        data_date=r["data_date"],
    )


async def _last_job_run(session: AsyncSession, platform_id: int) -> JobRunOut | None:
    """This integration's single most recent run, regardless of the Job Runs tab's own page or
    search filter -- that tab's listing is scoped by whatever an operator is currently paging
    through or searching for, which is not what the Summary card's "Last run" means."""
    row = (
        (
            await session.execute(
                text(
                    f"""SELECT {_JOB_RUN_SELECT_COLUMNS}
                          FROM integrations.job_runs jr
                         WHERE jr.platform_id = :platform_id
                         ORDER BY jr.run_date DESC, jr.id DESC
                         LIMIT 1"""  # noqa: S608 - `_JOB_RUN_SELECT_COLUMNS` is a fixed module constant, never request data
                ),
                {"platform_id": platform_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return _job_run_out(dict(row)) if row is not None else None


@router.get("/{uid}/runs", name="list-integration-runs")
async def list_integration_runs(
    uid: str,
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> JobRunsPage:
    """Job-run history for one integration. Per-feature stats live in `job_runs.data`, a jsonb blob
    keyed by feature name -- extracted here via the jsonb `->`/`->>` operators against the row's own
    `feature` column (never by string-formatting the feature name into the query), falling back to
    a lowercase key since the legacy writer was inconsistent about casing."""
    require_permission(principal, INTEGRATIONS_READ)
    platform_id = (
        await session.execute(
            text("SELECT id FROM integrations.platforms WHERE uid = :uid"), {"uid": uid}
        )
    ).scalar_one_or_none()
    if platform_id is None:
        raise HTTPException(status_code=404, detail="Integration not found.")

    page = max(page, 1)
    limit = max(limit, 1)
    offset = (page - 1) * limit
    like = f"%{listing.escape_like(search)}%" if search else None
    where = "jr.platform_id = :platform_id"
    params: dict[str, Any] = {"platform_id": platform_id, "limit": limit, "offset": offset}
    if like is not None:
        where += (
            " AND (jr.feature ILIKE :search ESCAPE '\\' "
            "OR jr.error_message ILIKE :search ESCAPE '\\')"
        )
        params["search"] = like

    total = (
        await session.execute(
            text(f"SELECT count(*) FROM integrations.job_runs jr WHERE {where}"),  # noqa: S608
            params,
        )
    ).scalar_one()
    result = await session.execute(
        text(
            f"""SELECT {_JOB_RUN_SELECT_COLUMNS}
                  FROM integrations.job_runs jr
                 WHERE {where}
                 ORDER BY jr.run_date DESC, jr.id DESC
                 LIMIT :limit OFFSET :offset"""  # noqa: S608 - `where` is a fixed clause; values are bound params
        ),
        params,
    )
    items = [_job_run_out(dict(r)) for r in result.mappings().all()]
    return JobRunsPage(items=items, total=int(total), page=page, limit=limit)
