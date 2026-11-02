"""Firm self-service surface (the platform portal).

The controls the admin (Graphene ops) has, trickled down to firm staff for their OWN firm:
manage the hard-separated staff and client Profile directories, assign role sets, and edit the
firm's role definitions. Every endpoint is scoped to the caller's own tenant — resolved from their
verified identity by ``firm_session`` (NOT from any client-supplied id) — and gated by a
permission on their token (``auth.require_permission``). Operations is Graphene-internal and is
never exposed here: only the client + platform portals.
"""

from dataclasses import asdict
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from workos._errors import WorkOSError

from app import audit_actions as actions
from app import (
    audit_stream,
    branding_uploads,
    directory,
    listing,
    provisioning,
    rbac,
    storage,
)
from app import branding as branding_mod
from app import contacts as contacts_mod
from app import instructions as instructions_mod
from app import permissions as permissions_registry
from app import platforms as platforms_mod
from app import providers as providers_mod
from app.actions.attempt import OperationError
from app.actions.money import (
    lodge_instruction,
    money_action_flags,
    requires_reason,
    switched_off,
)
from app.actions.runs import ActionError, Held
from app.actions.runs import pending_response as action_pending
from app.addressing import book_read_contact_id, book_write_contact_id, firm_contact_id
from app.auth import require_any_permission, require_permission
from app.branding import Branding
from app.comms import stream as comms_stream
from app.db import (
    FirmContext,
    firm_book_session,
    firm_book_write_session,
    firm_session,
    target_firm_session,
)
from app.email import transport as email_mod
from app.event_schema import registry as event_schema
from app.flows import automation as flow_automation
from app.flows import triggers as flow_triggers
from app.lake import staff as lake_staff
from app.notifications import rules_service as notif_rules
from app.page_views import visible_page_views
from app.permissions import (
    ACTIVITY_READ,
    BRANDING_READ,
    BRANDING_WRITE,
    CONTACTS_CREATE,
    CONTACTS_READ_ALL,
    CONTACTS_WRITE,
    DATA_SOURCES_READ,
    DOCUMENTS_READ,
    INSTRUCTIONS_READ,
    INSTRUCTIONS_WRITE,
    MEMBERS_READ,
    MEMBERS_WRITE,
    NOTIFICATIONS_MANAGE,
    NOTIFICATIONS_READ,
    PAGES_WRITE,
    PROFILES_READ,
    PROFILES_WRITE,
    ROLES_READ,
    ROLES_WRITE,
    SCREENING_READ,
    TEAMS_READ,
    TEAMS_WRITE,
    satisfies,
)
from app.reviews.hold import idempotency_key, maybe_hold, pending_response, raise_review
from app.reviews.service import ReviewError
from app.session_refresh import refresh_member_sessions

_ASSET_KINDS = {"logo", "logo-dark", "icon", "favicon", "card-art"}

router = APIRouter(prefix="/firm", tags=["firm"])

# A FIRM'S PEOPLE ARE AT `/firm/members`, and the rename to `/users` was REVERTED.
#
# This branch renamed them, and main had independently built the same partition (`?kind=`) on the
# path it already had, plus multi-role memberships and a `/profiles` page. Reconciling the two on
# main's shape put the routes back — so the twelve decorators below are `/members…`, the operation
# ids are `firm-*-member`, and `audit.actions`' event keys with them.
#
# The rename's argument was not wrong (a login is not a sub-resource of the firm — it is the thing
# a firm's staff, its clients and a custodian's synced employees are all rows of), and it is
# recorded here rather than deleted because it is worth making again deliberately. What it is NOT
# is the state of the code: four platform loaders were left calling `/users` after the revert and
# all four 404'd silently, emptying the adviser, login, assignee and provider-staff pickers.

RoleSlug = Annotated[str, Field(pattern="^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")]
# Firms only ever manage the two web-exposed portals; operations is Graphene-internal.
FirmPortal = Annotated[str, Field(pattern="^(client|platform)$")]


class InviteMember(BaseModel):
    email: str
    # role_slug stays as a compatibility input for older clients. New surfaces send role_slugs,
    # which is the complete replacement set and must all belong to one portal.
    role_slug: RoleSlug | None = None
    role_slugs: list[RoleSlug] = Field(default_factory=list, max_length=20)
    first_name: str | None = None
    last_name: str | None = None


class GrantPortal(BaseModel):
    role_slug: RoleSlug | None = None
    role_slugs: list[RoleSlug] = Field(default_factory=list, max_length=20)

    def roles(self) -> list[str]:
        roles = list(dict.fromkeys(self.role_slugs or ([self.role_slug] if self.role_slug else [])))
        if not roles:
            raise ValueError("at least one role is required")
        return roles


class ProviderRef(BaseModel):
    """A custodian's own id for a person. BLANK CLEARS — see `provisioning.set_provider_ref`."""

    external_id: str = ""


class UpdateUser(BaseModel):
    """What a firm says about one of its people. EVERY FIELD OPTIONAL, and absent means untouched.

    `type` is a LIST because `client` and `staff` compound — a firm's adviser is commonly also a
    client of the firm. `is_adviser` is separate because advising is a capability WITHIN staff:
    as a third `type` member it would make `{client, adviser}` expressible, and there is no such
    person (`users_adviser_is_staff` now forbids it in the database too).

    The members are validated in `provisioning.update_member_profile` against its own vocabulary
    so the refusal NAMES them; a bare `list[str]` here also keeps the generated client from
    needing a regenerate every time the vocabulary grows.
    """

    first_name: str | None = None
    last_name: str | None = None
    type: list[str] | None = None
    is_adviser: bool | None = None
    # The staff profile (0118). `avatar` is deliberately absent — it mirrors WorkOS's picture and
    # the sync writes it, so a firm's edit would be reverted on the next reconcile; `photo_url` is
    # the one a firm controls and every reader falls back to `avatar` when it is unset.
    job_title: str | None = None
    qualifications: list[str] | None = None
    fca_reference: str | None = None
    # The custodian organisation this adviser opens new clients under. Blank clears that
    # provider; a field the form did not send is left alone. Seccl is an enabled node.
    # SEI is an employee this firm already knows, not the office team.
    seccl_node: str | None = None
    sei_dsr: str | None = None
    bio: str | None = None
    photo_url: str | None = None
    direct_phone: str | None = None


class CreateRole(BaseModel):
    name: str
    slug: RoleSlug
    portal: FirmPortal
    description: str | None = None
    permissions: list[str] = []


class SetPermissions(BaseModel):
    permissions: list[str]


class SetBranding(BaseModel):
    name: str | None = None
    branding: Branding


ContactType = Annotated[str, Field(pattern="^(individual|organisation)$")]
# `ServiceType`/`SubType` are `app.contacts`' now, beside the column allowlist they belong to and
# beside `UpdateContact`, which is derived from. Re-exported here because `CreateContact` below is
# still this router's.
ServiceType = contacts_mod.ServiceType
SubType = contacts_mod.SubType
UpdateContact = contacts_mod.UpdateContact
Capacity = Annotated[str, Field(pattern="^(self|authorised|power_of_attorney|delegate)$")]


class CreateContact(BaseModel):
    type: ContactType = "individual"
    service_type: ServiceType | None = None
    sub_type: SubType | None = None
    title: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    surname: str | None = None
    name: str | None = None
    email: str | None = None
    dob: date | None = None
    marital_status: contacts_mod.MaritalStatus | None = None
    retirement_age: int | None = Field(default=None, ge=50, le=75)
    # The mobile the drawer asks for. The other number is a detail-page field.
    mobile: str | None = None

    @field_validator("mobile")
    @classmethod
    def _validate_mobile(cls, value: str | None) -> str | None:
        return contacts_mod.validate_contact_phone(value)


class AssignAdviser(BaseModel):
    adviser_id: str
    #: One ``adviser`` per client; naming a new one moves the current adviser to ``support``.
    role: contacts_mod.ClientTeamRole = "adviser"


class AddNote(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class LinkUser(BaseModel):
    user_id: str
    capacity: Capacity = "self"


class InvitePortal(BaseModel):
    role_slug: RoleSlug | None = None
    role_slugs: list[RoleSlug] = Field(default_factory=list, max_length=20)


# ---- team (members + per-portal access) ----


# The directory READS are target-aware: the workspace's task, journey and client-people surfaces
# all pick a person or a role from the firm's own list, so refusing them leaves an operator looking
# at an assignee dropdown with nothing in it. The member/role WRITES below stay firm-only on
# purpose — provisioning a person and editing a role are firm administration, and Graphene does
# both through `/admin/tenants/{id}/…` under `tenants:write`, not through the firm's own door.
@router.get("/members", name="list-members")
async def list_members(
    kind: Literal["staff", "profile", "unclassified"] | None = None,
    q: str | None = None,
    role: Annotated[list[str] | None, Query()] = None,
    status: Annotated[list[str] | None, Query()] = None,
    since: str | None = None,
    until: str | None = None,
    active_since: str | None = None,
    active_until: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    ctx: FirmContext = Depends(target_firm_session),
) -> list[dict[str, object]] | dict[str, object]:
    """The firm's identities, optionally partitioned by their hard staff/profile kind.

    `q` searches name and email; `role`/`status` narrow to the people holding any of the given
    roles/statuses; `since`/`until` narrow by membership start and `active_since`/`active_until`
    by last sign-in. All are applied in SQL, because `limit` windows the result: filtering a
    window in the browser searches the page you happen to be on and reports it as the firm's
    answer.

    `limit` also chooses the response shape, the convention `/crm/accounts` sets: absent ⇒ the bare
    list every existing caller reads, given ⇒ `{items, total}`.
    """
    require_any_permission(ctx.principal, MEMBERS_READ, TEAMS_READ, PROFILES_READ)
    return await provisioning.members_with_portals(
        ctx.session,
        ctx.tenant_id,
        access_kind=kind,
        q=q,
        role_slug=role,
        filters=provisioning.MemberFilters(
            status=status,
            created_since=audit_stream.parse_instant(since, "since"),
            created_until=audit_stream.parse_instant(until, "until"),
            active_since=audit_stream.parse_instant(active_since, "active_since"),
            active_until=audit_stream.parse_instant(active_until, "active_until"),
        ),
        limit=limit,
        offset=offset,
    )


@router.get("/members/by-uid/{user_uid}", name="get-member-profile")
async def get_member_profile(
    user_uid: str,
    background: BackgroundTasks,
    ctx: FirmContext = Depends(target_firm_session),
) -> dict[str, object]:
    """A complete, tenant-scoped auth profile for Team and Profiles detail pages.

    Sessions in the response are the rows already stored. WorkOS is asked for that
    person's live sessions only after this response is sent.
    """
    require_permission(ctx.principal, MEMBERS_READ)
    profile = await directory.member_profile(ctx.session, ctx.tenant_id, user_uid)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    background.add_task(refresh_member_sessions, ctx.tenant_id, str(profile["user_id"]))
    try:
        user = profile.get("user")
        current = (
            ctx.principal.session_id
            if isinstance(user, dict) and user.get("workos_user_id") == ctx.principal.user_id
            else None
        )
        profile["security"] = await provisioning.member_security_posture(
            ctx.session,
            tenant_id=ctx.tenant_id,
            user_id=str(profile["user_id"]),
            current_session_id=current,
        )
    except (LookupError, WorkOSError):
        # The authorisation mirror remains useful during a WorkOS outage. Do not turn the whole
        # profile into a 5xx merely because live factor/session posture cannot be refreshed.
        profile["security"] = {"available": False}
    # The heatmap is counts. The path list names records. A members:read login does not
    # thereby learn a client, account, document, or compliance page they could not open.
    held = set(ctx.principal.permissions)
    views = profile.get("recent_page_views")
    if isinstance(views, list):
        profile["recent_page_views"] = visible_page_views(
            [row for row in views if isinstance(row, dict)],
            see_all_clients=satisfies(held, CONTACTS_READ_ALL.slug),
            see_documents=satisfies(held, DOCUMENTS_READ.slug),
            see_screening=satisfies(held, SCREENING_READ.slug),
        )
    return profile


@router.post("/members", name="invite-member")
async def invite_member(
    body: InviteMember, ctx: FirmContext = Depends(firm_session)
) -> dict[str, object]:
    """Invite a staff identity or create a client-linked profile.

    A client Profile is never a bare login: without a ``crm.contact_users`` bridge its portal
    scope is empty and the person signs in to no client record. The client-role branch therefore
    uses ``provision_client_login`` to create the minimal contact and bridge in the same act.
    """
    require_any_permission(ctx.principal, MEMBERS_WRITE, TEAMS_WRITE, PROFILES_WRITE)
    roles = list(dict.fromkeys(body.role_slugs or ([body.role_slug] if body.role_slug else [])))
    if not roles:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="at least one role is required",
        )
    try:
        portal = await provisioning.validate_member_role_set(
            ctx.session, tenant_id=ctx.tenant_id, role_slugs=roles
        )
        if portal == "client":
            require_permission(ctx.principal, CONTACTS_CREATE)
            result = await provisioning.provision_client_login(
                ctx.session,
                tenant_id=ctx.tenant_id,
                role_slug=roles[0],
                email=body.email,
                first_name=body.first_name,
                last_name=body.last_name,
            )
        else:
            result = await provisioning.grant_portal_access(
                ctx.session,
                tenant_id=ctx.tenant_id,
                role_slug=roles[0],
                portal=portal,
                email=body.email,
                first_name=body.first_name,
                last_name=body.last_name,
            )
        if len(roles) > 1:
            result = await provisioning.set_member_roles(
                ctx.session,
                tenant_id=ctx.tenant_id,
                user_id=str(result["user_id"]),
                portal=str(result["portal"]),
                role_slugs=roles,
            ) | {
                key: value
                for key, value in result.items()
                if key
                in {
                    "email",
                    "status",
                    "invitation_sent",
                    "email_delivered",
                    "sessions_revoked",
                    "invitation_url",
                }
            }
        return result
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.put("/members/{user_id}/portals/{portal}", name="grant-portal")
async def grant_portal(
    user_id: str,
    portal: FirmPortal,
    body: GrantPortal,
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Grant an existing person access to a portal with a role (reactivates if revoked)."""
    require_any_permission(ctx.principal, MEMBERS_WRITE, TEAMS_WRITE, PROFILES_WRITE)
    try:
        roles = body.roles()
        await provisioning.validate_member_role_set(
            ctx.session, tenant_id=ctx.tenant_id, role_slugs=roles, portal=portal
        )
        result = await provisioning.grant_portal_access(
            ctx.session,
            tenant_id=ctx.tenant_id,
            user_id=user_id,
            portal=portal,
            role_slug=roles[0],
        )
        if len(roles) > 1:
            result = await provisioning.set_member_roles(
                ctx.session,
                tenant_id=ctx.tenant_id,
                user_id=user_id,
                portal=portal,
                role_slugs=roles,
            )
        return result
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.delete("/members/{user_id}/portals/{portal}", name="revoke-portal")
async def revoke_portal(
    user_id: str,
    portal: FirmPortal,
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Revoke a person's access to one portal (their other access survives)."""
    require_any_permission(ctx.principal, MEMBERS_WRITE, TEAMS_WRITE, PROFILES_WRITE)
    try:
        return await provisioning.revoke_portal_access(
            ctx.session, tenant_id=ctx.tenant_id, user_id=user_id, portal=portal
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.patch("/members/{user_id}/portals/{portal}", name="set-portal-role")
async def set_portal_role(
    user_id: str,
    portal: FirmPortal,
    body: GrantPortal,
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Replace a person's complete role set within one portal."""
    require_any_permission(ctx.principal, MEMBERS_WRITE, TEAMS_WRITE, PROFILES_WRITE)
    try:
        return await provisioning.set_member_roles(
            ctx.session,
            tenant_id=ctx.tenant_id,
            user_id=user_id,
            portal=portal,
            role_slugs=body.roles(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        # set_member_role's role<->portal axis refusal — the caller named a role that belongs to a
        # different portal, which is a request problem, not a missing resource.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.put("/members/{user_id}", name="update-member")
async def update_user(
    user_id: str,
    body: UpdateUser,
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """What a firm says about one of its people — the team member page's save.

    Three groups in one call because they are one editing session on one person: the NAME, the
    NATURE (`type`, `is_adviser`) and the STAFF PROFILE (0118 — job title, qualifications, FCA
    reference, bio, photo, direct line).

    **Only the keys the body carries are written**, which is the rule the catalogue's model form
    learned the hard way: a form that posts its whole shape clears every control it does not
    render, and that is how saving a model's name came to wipe the VAT flag the platform had set.

    Deliberately NOT an access endpoint despite sitting among them. `type` says what someone IS;
    `auth.organisation_memberships` says what they may DO, and this writes nothing of the latter.
    `enabled` is absent for the same reason: deactivating has to reach WorkOS or the person keeps
    their session, so `/deactivate` and `/reactivate` own it.
    """
    require_permission(ctx.principal, MEMBERS_WRITE)
    try:
        return await provisioning.update_member_profile(
            ctx.session,
            tenant_id=ctx.tenant_id,
            user_id=user_id,
            # `exclude_unset`, not `exclude_none`: a field the caller SENT as null is a request to
            # CLEAR it (an adviser who no longer holds a qualification), where a field it did not
            # send must be left alone. The two are different intentions and `exclude_none` cannot
            # tell them apart.
            fields=body.model_dump(exclude_unset=True),
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.get("/provider-orgs", name="firm-provider-orgs")
async def firm_provider_orgs(ctx: FirmContext = Depends(firm_session)) -> dict[str, object]:
    """Seccl nodes and SEI employees an adviser can be assigned, with the name when we have one.

    The adviser card's dropdowns. Seccl is the nodes the firm enabled. SEI is the employees
    the book already grants for this firm's SEI firm, by SEI's id and name.
    """
    from app.adviser_orgs import provider_org_options

    require_permission(ctx.principal, MEMBERS_READ)
    return await provider_org_options(ctx.session, tenant_id=ctx.tenant_id)


@router.get("/members/custodians", name="list-custodians")
async def list_custodians(ctx: FirmContext = Depends(firm_session)) -> list[dict[str, object]]:
    """The CUSTODIANS this firm is enabled on — which mapping controls its members page renders.

    A firm's enablements filtered to the custodian role, because a scheme administrator's people
    are not what `provider_refs` is about: Morgan Lloyd names one adviser contact for a whole firm,
    where a custodian knows each of a firm's staff by their own id. Derived from
    `PROVIDER_META['role']` rather than by naming providers, so a second custodian is not silently
    left out — the rule the `provider_positions` block already follows.
    """
    require_permission(ctx.principal, MEMBERS_READ)
    enabled = await providers_mod.list_for_tenant(ctx.session, ctx.tenant_id)
    custodians = providers_mod.custodians()
    return [
        {"provider": row["provider"], "name": row["name"]}
        for row in enabled
        if row["enabled"] and row["provider"] in custodians
    ]


@router.get("/members/provider-staff/{provider}", name="provider-staff")
async def provider_staff(
    provider: str,
    q: str = "",
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """The custodian's own employees for this firm — the picker behind the mapping control.

    Read from the firm's OWN SLICE (`tenant_{uid}.users`, computed by that provider's chain), not
    from a live vendor call, which reverses what 0118 planned and for good reasons — see
    `app/lake/staff.py`. The short version: the pipeline already fetches this nightly, the lake
    copy is the CONFORMED one (scoped to the firm, filtered to employees rather than the 865
    investors and 20 service logins in the same report, name split), and SEI allowlists its API by
    source IP so a live call works from the pipeline's host and not necessarily from this one.

    `unavailable` rather than an empty list when the chain has not run. A picker showing nothing
    because the warehouse is unreadable looks exactly like a firm with no staff at that custodian,
    and those need different actions from whoever is looking at it.
    """
    require_permission(ctx.principal, MEMBERS_READ)
    known = provider.strip().lower()
    if known not in providers_mod.custodians():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Staff cannot be selected from the '{provider}' custodian.",
        )
    # `FirmContext` carries the tenant's serial, not its uid, and the lake's schema is named by
    # the uid — the same resolve `instructions.py` and `tenant_storage.py` each do.
    tenant_uid = (
        await ctx.session.execute(
            text("SELECT uid FROM auth.tenants WHERE id = :t"), {"t": ctx.tenant_id}
        )
    ).scalar_one()
    try:
        found = await lake_staff.firm_staff(known, str(tenant_uid), search=q)
    except lake_staff.HoldingsUnavailable as exc:
        return {"provider": known, "staff": [], "unavailable": str(exc)}
    return {"provider": known, "staff": found, "unavailable": None}


@router.put("/members/{user_id}/provider-ref/{provider}", name="set-provider-ref")
async def set_user_provider_ref(
    user_id: str,
    provider: str,
    body: ProviderRef,
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Record which id a custodian knows this person by, or clear it by sending a blank.

    The FIRM's door onto `provisioning.set_provider_ref` — the same implementation the operations
    route uses, because what a mapping is must not differ by who set it. A firm knows its own
    people better than Graphene ops does, which is why this exists at all; the operations route
    stays for a firm that has not been onboarded onto the platform portal yet.
    """
    require_permission(ctx.principal, MEMBERS_WRITE)
    try:
        return await provisioning.set_provider_ref(
            ctx.session,
            tenant_id=ctx.tenant_id,
            user_id=user_id,
            provider=provider,
            external_id=body.external_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.post("/members/{user_id}/deactivate", name="deactivate-member")
async def deactivate_member(
    user_id: str, ctx: FirmContext = Depends(firm_session)
) -> dict[str, object]:
    """Suspend a person entirely in the firm (all portals)."""
    require_any_permission(ctx.principal, MEMBERS_WRITE, TEAMS_WRITE, PROFILES_WRITE)
    try:
        return await provisioning.deactivate_member(
            ctx.session, tenant_id=ctx.tenant_id, user_id=user_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.post("/members/{user_id}/reactivate", name="reactivate-member")
async def reactivate_member(
    user_id: str, ctx: FirmContext = Depends(firm_session)
) -> dict[str, object]:
    """Un-suspend a person (all portals)."""
    require_any_permission(ctx.principal, MEMBERS_WRITE, TEAMS_WRITE, PROFILES_WRITE)
    try:
        return await provisioning.reactivate_member(
            ctx.session, tenant_id=ctx.tenant_id, user_id=user_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.post("/members/{user_id}/resend-invite", name="resend-invite")
async def resend_invite(
    user_id: str, ctx: FirmContext = Depends(firm_session)
) -> dict[str, object]:
    """Resend a pending onboarding email + return a fresh set-password URL."""
    require_any_permission(ctx.principal, MEMBERS_WRITE, TEAMS_WRITE, PROFILES_WRITE)
    try:
        return await provisioning.resend_member_invite(ctx.session, user_id=user_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/members/{user_id}/invitation", name="revoke-invitation")
async def revoke_invitation(
    user_id: str, ctx: FirmContext = Depends(firm_session)
) -> dict[str, object]:
    """Revoke the pending WorkOS invitation while leaving the portal grant ready to resend."""
    require_any_permission(ctx.principal, MEMBERS_WRITE, TEAMS_WRITE, PROFILES_WRITE)
    try:
        return await provisioning.revoke_member_invitation(ctx.session, user_id=user_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.post("/members/{user_id}/password-reset", name="send-member-password-reset")
async def send_member_password_reset(
    user_id: str,
    portal: FirmPortal | None = None,
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Email a fresh password-reset link and end the member's live sessions."""
    require_permission(ctx.principal, MEMBERS_WRITE)
    try:
        return await provisioning.send_member_password_reset(
            ctx.session, tenant_id=ctx.tenant_id, user_id=user_id, portal=portal
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.post("/members/{user_id}/sessions/{session_id}", name="revoke-member-session")
async def revoke_member_session(
    user_id: str, session_id: str, ctx: FirmContext = Depends(firm_session)
) -> dict[str, object]:
    """End one of this member's live sessions. The id must be on their own list."""
    require_permission(ctx.principal, MEMBERS_WRITE)
    try:
        return await provisioning.revoke_member_session(
            ctx.session, tenant_id=ctx.tenant_id, user_id=user_id, session_id=session_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.post("/members/{user_id}/reset-mfa", name="reset-member-mfa")
async def reset_member_mfa(
    user_id: str, ctx: FirmContext = Depends(firm_session)
) -> dict[str, object]:
    """Clear MFA factors after identity verification and end the member's live sessions."""
    require_permission(ctx.principal, MEMBERS_WRITE)
    try:
        return await provisioning.reset_member_mfa(
            ctx.session, tenant_id=ctx.tenant_id, user_id=user_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


# ---- roles (the firm's own role definitions) ----


@router.get("/permissions", name="list-permissions")
async def list_permissions(ctx: FirmContext = Depends(firm_session)) -> list[dict[str, object]]:
    """The permission catalogue a firm may actually grant, to build the role editor.

    Graphene's Internal Operations permissions — `system` above all — are filtered out to match
    what ``rbac.assert_grantable`` enforces on the write. Otherwise the role editor rendered a
    "System administration" tickbox that either escalated the firm to cross-tenant superuser
    (before the guard) or 403'd (after it)."""
    require_permission(ctx.principal, ROLES_READ)
    # Serve the canonical registry, not its startup-synchronised DB projection. A deploy window or
    # failed sync must not make an arbitrary subset of switches disappear from the editor.
    return [asdict(p) for p in permissions_registry.ALL if p.portal == "platform"]


@router.get("/roles", name="list-roles")
async def list_roles(ctx: FirmContext = Depends(target_firm_session)) -> list[dict[str, object]]:
    """The firm's roles with their permissions. Serves both the role editor (roles:read) and the
    member editor's role picker (members:read/teams:read/profiles:read)."""
    require_any_permission(ctx.principal, ROLES_READ, MEMBERS_READ, TEAMS_READ, PROFILES_READ)
    return await rbac.list_org_roles(ctx.session, ctx.tenant_id)


@router.post("/roles", name="create-role")
async def create_role(
    body: CreateRole, ctx: FirmContext = Depends(firm_session)
) -> dict[str, object]:
    """Create one of the firm's own roles (client or platform)."""
    require_permission(ctx.principal, ROLES_WRITE)
    try:
        return await rbac.create_org_role(
            ctx.session,
            tenant_id=ctx.tenant_id,
            name=body.name,
            slug=body.slug,
            portal=body.portal,
            description=body.description,
            permission_slugs=body.permissions,
        )
    except rbac.PermissionNotGrantable as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.put("/roles/{slug}/permissions", name="set-role-permissions")
async def set_role_permissions(
    slug: str, body: SetPermissions, ctx: FirmContext = Depends(firm_session)
) -> dict[str, object]:
    """Set the exact permission set on one of the firm's roles.

    `roles:write` lets a firm administrator edit their own roles — it must not let them *widen*
    the firm: ``rbac.assert_grantable`` refuses Graphene-internal permissions (above all
    `system`, which bypasses every gate in Python and under RLS) and any slug outside the
    catalogue. A firm can therefore only ever shuffle permissions it already lives inside."""
    require_permission(ctx.principal, ROLES_WRITE)
    try:
        return await rbac.set_org_role_permissions(
            ctx.session, tenant_id=ctx.tenant_id, role_slug=slug, permission_slugs=body.permissions
        )
    except rbac.PermissionNotGrantable as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


# ---- branding (firm self-service white-label) ----


@router.get("/branding", name="get-branding")
async def get_branding(
    portal: branding_mod.Portal = "client", ctx: FirmContext = Depends(firm_session)
) -> dict[str, object]:
    """The firm's own branding + name, for one portal (client|platform)."""
    require_permission(ctx.principal, BRANDING_READ)
    return await branding_mod.get_branding(ctx.session, ctx.tenant_id, portal)


@router.put("/branding", name="set-branding")
async def set_branding(
    body: SetBranding,
    portal: branding_mod.Portal = "client",
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Edit the firm's own branding for one portal (validated tokens) + optional name.

    Portal COMPOSITION is gated separately: a change to ``Branding.pages`` (the client-portal
    page set) additionally requires ``pages:write`` — colours/logos/copy stay ``branding:write``.
    The check is a canonical diff against the stored pages (``branding.pages_changed``), so a
    save that merely echoes the stored pages back — the studio always submits the whole branding
    object — never trips it for a pages-less editor."""
    require_permission(ctx.principal, BRANDING_WRITE)
    current = await branding_mod.stored_branding(ctx.session, ctx.tenant_id, portal)
    if branding_mod.pages_changed(current, body.branding):
        require_permission(ctx.principal, PAGES_WRITE)
    return await branding_mod.set_branding(
        ctx.session, ctx.tenant_id, portal=portal, name=body.name, branding=body.branding
    )


@router.post("/branding/asset", name="upload-branding-asset")
async def upload_branding_asset(
    kind: str,
    file: UploadFile = File(...),
    ctx: FirmContext = Depends(firm_session),
    email_copy: Annotated[UploadFile | None, File()] = None,
) -> dict[str, str]:
    """Upload a branding asset and return its opaque same-origin source.

    ``email_copy`` is optional: the PNG the branding studio draws of an SVG logo or icon, stored
    beside it for email (``app/branding_uploads.py``)."""
    require_permission(ctx.principal, BRANDING_WRITE)
    if kind not in _ASSET_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"kind must be one of {_ASSET_KINDS}"
        )
    data = await file.read()
    try:
        # A logo/icon/favicon is stored display-sized (long side <= MAX_SHELL_SIDE): sharp at the
        # sidebar and login lockup on a retina screen, and not the multi-megapixel bitmap that was
        # the page's Largest Contentful Paint.
        src = await branding_uploads.store(
            ctx.session,
            ctx.tenant_id,
            kind,
            data,
            file.content_type or "application/octet-stream",
            email_copy=await branding_uploads.read_email_copy(email_copy),
        )
    except storage.StorageUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Image uploads are unavailable. Enter a publicly hosted image URL instead.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return {"src": src}


# ---- contacts (clients) -- the write path. Reads are RLS-scoped via GET /crm/contacts. ----


@router.post("/contacts", name="create-contact")
async def create_contact(
    body: CreateContact, ctx: FirmContext = Depends(firm_session)
) -> dict[str, object]:
    """Create a client (contact) in the firm.

    ``created_by_workos`` is the caller's own verified WorkOS id, and it is what puts the creating
    adviser on the new client's book in the same transaction. Without it a book-scoped adviser
    (no ``contacts:read_all``) created a client and it was immediately invisible to them —
    ``crm.contacts`` RLS admits a row only via that permission or ``auth.contact_scope()``, so the
    form succeeded, the toast fired, and the client vanished.

    Gated ``contacts:create``, not ``contacts:write``: minting a person onto the book is a
    different act from editing one who is already there, so a firm can grant either without the
    other. ``write`` does not imply ``create``."""
    require_permission(ctx.principal, CONTACTS_CREATE)
    return await contacts_mod.create_contact(
        ctx.session,
        tenant_id=ctx.tenant_id,
        created_by_workos=ctx.principal.user_id,
        **body.model_dump(),
    )


@router.patch("/contacts/{contact_uid}", name="update-contact", response_model=None)
async def update_contact(
    contact_uid: str,
    contact_id: Annotated[int, Depends(book_write_contact_id)],
    body: UpdateContact,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, object] | JSONResponse:
    """Edit a client's details (only the fields supplied are changed).

    ``exclude_unset=True`` is load-bearing: a field the caller did not send is left alone, and a
    field sent as ``null`` is CLEARED. Dumping the whole model instead made every unset field an
    explicit ``None``, so the service layer had to ignore nulls wholesale — which is why clearing a
    phone, email, title or address was a silent no-op (see ``contacts.update_contact``).

    Four-eye enabled: when the firm turns approval on for ``client.details`` the edit is held for
    a second person (``maybe_hold`` starts a baton review run) rather than written at once.
    ``exclude`` keeps ``justification`` (the review reason) out of the fields the service writes.

    Book-enabled (proxy middle-office): an operations principal holding ``book:write`` may name a
    target firm with ``?tenant=<uid>`` (``db.firm_book_write_session``); without the param this is
    the firm's own self-service write, unchanged."""
    require_permission(ctx.principal, CONTACTS_WRITE)
    fields = body.model_dump(mode="json", exclude_unset=True, exclude={"justification"})
    if fields:
        payload = {"contact_id": contact_id, "fields": fields}
        try:
            hold = await maybe_hold(
                ctx.session,
                tenant_id=ctx.tenant_id,
                workos_user_id=ctx.principal.user_id,
                action_key="client.details_changed",
                subject_type="contact",
                subject_id=contact_uid,
                contact_id=contact_id,
                payload=payload,
                justification=body.justification,
                idempotency_key=idempotency_key(
                    "client.details_changed", "contact", contact_uid, payload
                ),
            )
        except ReviewError as exc:
            raise_review(exc)
        if hold.held:
            return pending_response(hold)
    try:
        return await contacts_mod.update_contact(
            ctx.session,
            tenant_id=ctx.tenant_id,
            contact_id=contact_id,
            fields=fields,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/contacts/{contact_uid}", name="archive-contact")
async def archive_contact(
    contact_id: Annotated[int, Depends(firm_contact_id)],
    ctx: FirmContext = Depends(target_firm_session),
) -> dict[str, object]:
    """Close a client (soft delete): they leave the live book and AUM, their history stays,
    and the record itself stays loadable so the header can say Closed.

    Refused with 409 while any account still has a balance, or while the client has pending
    money-movement instructions — nothing settles them, so closing would strand a live claim
    behind a row the live lists no longer show."""
    require_permission(ctx.principal, CONTACTS_WRITE)
    try:
        return await contacts_mod.archive_contact(
            ctx.session, tenant_id=ctx.tenant_id, contact_id=contact_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/contacts/{contact_uid}/restore", name="restore-contact")
async def restore_contact(
    contact_id: Annotated[int, Depends(firm_contact_id)],
    ctx: FirmContext = Depends(target_firm_session),
) -> dict[str, object]:
    """Reopen a closed client."""
    require_permission(ctx.principal, CONTACTS_WRITE)
    try:
        return await contacts_mod.restore_contact(
            ctx.session, tenant_id=ctx.tenant_id, contact_id=contact_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/contacts/{contact_uid}/advisers", name="assign-adviser")
async def assign_adviser(
    contact_id: Annotated[int, Depends(firm_contact_id)],
    body: AssignAdviser,
    ctx: FirmContext = Depends(target_firm_session),
) -> dict[str, object]:
    """Put a member of staff on a client's team, or change their role there. One adviser per
    client: naming a new adviser keeps the old one on the team as support."""
    require_permission(ctx.principal, CONTACTS_WRITE)
    try:
        return await contacts_mod.assign_adviser(
            ctx.session,
            tenant_id=ctx.tenant_id,
            contact_id=contact_id,
            adviser_id=body.adviser_id,
            role=body.role,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/contacts/{contact_uid}/advisers/{adviser_id}", name="unassign-adviser")
async def unassign_adviser(
    contact_id: Annotated[int, Depends(firm_contact_id)],
    adviser_id: str,
    ctx: FirmContext = Depends(target_firm_session),
) -> dict[str, object]:
    """Remove a staff member from a client's book."""
    require_permission(ctx.principal, CONTACTS_WRITE)
    try:
        return await contacts_mod.unassign_adviser(
            ctx.session, tenant_id=ctx.tenant_id, contact_id=contact_id, adviser_id=adviser_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/contacts/{contact_uid}/users", name="link-contact-user")
async def link_contact_user(
    contact_id: Annotated[int, Depends(firm_contact_id)],
    body: LinkUser,
    ctx: FirmContext = Depends(target_firm_session),
) -> dict[str, object]:
    """Link a login to a client (the party they may act for), with a capacity."""
    require_permission(ctx.principal, CONTACTS_WRITE)
    try:
        return await contacts_mod.link_user(
            ctx.session,
            tenant_id=ctx.tenant_id,
            contact_id=contact_id,
            user_id=body.user_id,
            capacity=body.capacity,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/contacts/{contact_uid}/portal-invite", name="invite-contact-to-portal")
async def invite_contact_to_portal(
    contact_id: Annotated[int, Depends(firm_contact_id)],
    body: InvitePortal,
    ctx: FirmContext = Depends(target_firm_session),
) -> dict[str, object]:
    """Give a client portal access in one step — create-or-reuse their login, link it to THIS
    contact, and send the set-password invite (``provisioning.provision_client_login``).

    The building blocks already existed separately (``invite_member`` for the login,
    ``link_contact_user`` for the bridge) with nothing chaining them, which is how a login can
    end up with no ``crm.contact_users`` row: the person can sign in yet ``auth.contact_scope()``
    resolves empty, so they see and fulfil nothing. Gated on BOTH permissions because the action
    is the union of the two primitives — granting the login and attributing it to a specific
    client are each independently privileged."""
    require_permission(ctx.principal, CONTACTS_WRITE)
    require_any_permission(ctx.principal, MEMBERS_WRITE, TEAMS_WRITE, PROFILES_WRITE)
    contact = (
        (
            await ctx.session.execute(
                text(
                    "SELECT email, first_name, surname, type FROM crm.contacts "
                    "WHERE id = :c AND tenant_id = :t AND deleted_at IS NULL"
                ),
                {"c": contact_id, "t": ctx.tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found.")
    if not contact["email"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This client has no email on file — add one before inviting them.",
        )
    try:
        roles = list(
            dict.fromkeys(body.role_slugs or ([body.role_slug] if body.role_slug else ["client"]))
        )
        await provisioning.validate_member_role_set(
            ctx.session, tenant_id=ctx.tenant_id, role_slugs=roles, portal="client"
        )
        result = await provisioning.provision_client_login(
            ctx.session,
            tenant_id=ctx.tenant_id,
            role_slug=roles[0],
            email=contact["email"],
            first_name=contact["first_name"],
            last_name=contact["surname"],
            contact_id=contact_id,
            contact_type=contact["type"] or "individual",
        )
        if len(roles) > 1:
            role_result = await provisioning.set_member_roles(
                ctx.session,
                tenant_id=ctx.tenant_id,
                user_id=str(result["user_id"]),
                portal="client",
                role_slugs=roles,
            )
            result["role_slug"] = role_result["role_slug"]
            result["role_slugs"] = role_result["role_slugs"]
        return result
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.post("/contacts/{contact_uid}/notes", name="add-contact-note")
async def add_contact_note(
    contact_id: Annotated[int, Depends(firm_contact_id)],
    body: AddNote,
    ctx: FirmContext = Depends(target_firm_session),
) -> dict[str, object]:
    """Add a staff note to a client."""
    require_permission(ctx.principal, CONTACTS_WRITE)
    try:
        return await contacts_mod.add_note(
            ctx.session,
            tenant_id=ctx.tenant_id,
            contact_id=contact_id,
            author_workos_id=ctx.principal.user_id,
            body=body.body,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/contacts/{contact_uid}/notes/{note_uid}", name="delete-contact-note")
async def delete_contact_note(
    note_uid: str,
    contact_id: Annotated[int, Depends(book_write_contact_id)],
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, object]:
    """Delete a note you wrote.

    `contacts:write`, the same grant that adds one — and the service layer restricts it to the
    CALLER'S OWN notes in the WHERE clause. That is the whole authorisation story: an adviser
    withdrawing their own remark about a conversation is not the destruction of a record, where
    deleting a colleague's note would be, and no permission in the registry draws that line.
    A note somebody else wrote answers 404, not 403 — whether it exists is not something a caller
    who may not touch it should learn.
    """
    require_permission(ctx.principal, CONTACTS_WRITE)
    try:
        await contacts_mod.delete_note(
            ctx.session,
            tenant_id=ctx.tenant_id,
            contact_id=contact_id,
            note_uid=note_uid,
            author_workos_id=ctx.principal.user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"deleted": note_uid}


@router.delete("/contacts/{contact_uid}/users/{user_id}", name="unlink-contact-user")
async def unlink_contact_user(
    contact_id: Annotated[int, Depends(firm_contact_id)],
    user_id: str,
    ctx: FirmContext = Depends(target_firm_session),
) -> dict[str, object]:
    """Unlink a login from a client."""
    require_permission(ctx.principal, CONTACTS_WRITE)
    try:
        return await contacts_mod.unlink_user(
            ctx.session, tenant_id=ctx.tenant_id, contact_id=contact_id, user_id=user_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


# ── Money-movement instructions (the "five actions" — demo-grade) ────────────────────────────
InstructionKind = Annotated[
    str, Field(pattern="^(expected_in|withdrawal|adhoc_fee|model_topup|model_withdrawal)$")
]


class CreateInstruction(BaseModel):
    kind: InstructionKind
    account_id: int
    amount: float = Field(gt=0)
    model_id: int | None = None
    #: Which of the firm's fee packages an ``adhoc_fee`` is charged under — the KIND of charge,
    #: never its price. Refused on any other kind. Optional: the account drawer records a fee
    #: amount without a type.
    fee_package_id: int | None = None
    due_date: str | None = None  # YYYY-MM-DD (expected_in)
    note: str | None = None
    justification: str | None = None
    #: The rail this withdrawal pays over. Omitted means the method saved on the bank account.
    #: BACS or Faster Payment for a UK destination; SWIFT is the only rail for an IBAN.
    payment_method: Literal["bacs", "faster_payment", "swift"] | None = None
    #: When false the firm pays the rail fee and it is not taken from the client's cash.
    #: Omitted means the fee is passed on, which is what a withdrawal did before the choice existed.
    pass_rail_fee: bool | None = None
    #: Pension payments only. Personal, employer, or third party — the category that
    #: selects the custodian's treatment code. A GIA payment has none.
    category: (
        Literal["personal_contribution", "employer_contribution", "third_party_contribution"] | None
    ) = None
    #: Adviser charge on a payment in or a withdrawal. Omitted or zero means no fee. On a payment it
    #: must be strictly less than ``amount``; on a withdrawal it is an additional debit.
    adviser_fee: float | None = None
    #: The firm's fee package the adviser fee is charged under — SEI needs a fee TYPE for it, so the
    #: drawer requires one on a SEI account; omitted for Seccl (its fee leg is amount-only).
    adviser_fee_package_id: int | None = None
    #: Sell down holdings to fund this withdrawal (SEI raise-cash / Seccl autoDisinvest). Refused on
    #: any kind but a withdrawal.
    auto_disinvest: bool = False
    #: Invest a pay-in into the account's model once the cash lands (the inverse of auto_disinvest).
    #: Default on; false leaves it as cash. Meaningful on a payment in only.
    auto_invest: bool = True


@router.get("/money-actions", name="money-actions")
async def money_actions(ctx: FirmContext = Depends(firm_book_session)) -> dict[str, list[str]]:
    """Money actions this firm has switched off, and those whose form must ask why.

    The account page hides Payment, Withdraw and Ad hoc fee from ``disabled``, and asks for a
    reason for each key in ``require_justification``. Gated on the instruction permissions
    because that is who sees those controls, not ``actions:read``.
    """
    require_any_permission(ctx.principal, INSTRUCTIONS_READ, INSTRUCTIONS_WRITE)
    disabled, reasons = await money_action_flags(ctx.session, tenant_id=ctx.tenant_id)
    return {"disabled": disabled, "require_justification": reasons}


@router.get("/contacts/{contact_uid}/instructions", name="list-instructions")
async def list_instructions(
    contact_id: Annotated[int, Depends(book_read_contact_id)],
    account_id: int | None = None,
    ctx: FirmContext = Depends(firm_book_session),
) -> list[dict[str, object]]:
    """A client's money-movement instructions (expected-in, withdrawals, model moves).

    Gated ``instructions:read`` — a READ gate at last (this used to demand ``contacts:write``,
    so an oversight role could not see the pending queue without also being able to add to it).
    Book-enabled as a READ (``?tenant=<uid>`` + ``book:read``): the admin proxy forms need the
    pending list before raising or cancelling anything; the gate is satisfied for ops by the
    firm-context overlay (``db._BOOK_FIRM_VISIBILITY``) and stays the firm-staff requirement on
    the no-param path."""
    require_permission(ctx.principal, INSTRUCTIONS_READ)
    return await instructions_mod.list_for_contact(
        ctx.session, tenant_id=ctx.tenant_id, contact_id=contact_id, account_id=account_id
    )


@router.post(
    "/contacts/{contact_uid}/instructions",
    name="create-instruction",
    response_model=None,
)
async def create_instruction(
    contact_uid: str,
    contact_id: Annotated[int, Depends(book_write_contact_id)],
    body: CreateInstruction,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, object] | JSONResponse:
    """Record an adviser action against one of the client's accounts. A pending outbound
    transaction is written alongside and synced best-effort to the account's custodian
    (`docs/transaction-status.md`). Cash withdrawals are checked against the account's available
    balance.

    Book-enabled (proxy middle-office, ``?tenant=<uid>`` + ``book:write``): the row lands in the
    TARGET tenant and every funding/ownership gate runs against it; ``audit.record()`` names the
    acting operator (the row's ``created_by`` resolves only within the target tenant, so an
    ops-proxied instruction carries NULL there — the audit trail is the attribution).

    Gated ``instructions:write`` (split out of ``contacts:write`` — moving money is not editing
    a client record)."""
    require_permission(ctx.principal, INSTRUCTIONS_WRITE)
    if await switched_off(ctx.session, tenant_id=ctx.tenant_id, kind=body.kind):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This action is switched off."
        )
    # The send is the hold: approvers on the money action (`transaction.payment` /
    # `withdrawal` / `fee`) hold the run that sends this instruction, so the booking itself is
    # not reviewed a second time.
    justification = (body.justification or "").strip() or None
    if (
        await requires_reason(ctx.session, tenant_id=ctx.tenant_id, kind=body.kind)
        and not justification
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A reason is required."
        )
    try:
        created = await instructions_mod.create_instruction(
            ctx.session,
            tenant_id=ctx.tenant_id,
            created_by_workos=ctx.principal.user_id,
            kind=body.kind,
            account_id=body.account_id,
            amount=body.amount,
            model_id=body.model_id,
            fee_package_id=body.fee_package_id,
            due_date=body.due_date,
            note=body.note,
            payment_method=body.payment_method,
            pass_rail_fee=body.pass_rail_fee,
            category=body.category,
            adviser_fee=body.adviser_fee,
            adviser_fee_package_id=body.adviser_fee_package_id,
            auto_disinvest=body.auto_disinvest,
            auto_invest=body.auto_invest,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    try:
        sent = await lodge_instruction(
            ctx.session,
            tenant_id=ctx.tenant_id,
            kind=str(created["kind"]),
            instruction_uid=str(created["uid"]),
            account_id=body.account_id,
            contact_id=contact_id,
            justification=justification,
        )
    except ActionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except OperationError as exc:
        # THE BOOKING STANDS; THE SEND DID NOT. A custodian that refused or could not be
        # reached (an SEI ad-hoc fee against a slow or unconfigured gateway was the case
        # found) used to escape here as a 500 — which rolled the instruction AND its failed
        # run back, so after a two-minute wait the adviser was told "Could not raise the
        # instruction" and had nothing on the record to retry. The transfers route already
        # takes this posture (`contributions.py::_lodge_transfer`): keep the row, keep the
        # run that says why, and tell the caller in words. The run's own page is the retry.
        return {**created, "provider_error": str(exc)}
    if isinstance(sent, Held):
        return action_pending(sent)
    return created


class CancelInstruction(BaseModel):
    # Accepted so an older client that still posts a reason keeps working; nothing reads it now
    # that a cancel is immediate.
    justification: str | None = None


@router.post("/instructions/{uid}/cancel", name="cancel-instruction", response_model=None)
async def cancel_instruction(
    uid: str,
    body: CancelInstruction | None = None,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, object] | JSONResponse:
    """Cancel a pending instruction — the caller's own firm, or (``?tenant=<uid>`` +
    ``book:write``) the named target firm's; the tenant-scoped UPDATE can never cross firms.
    Gated ``instructions:write`` like its create sibling."""
    require_permission(ctx.principal, INSTRUCTIONS_WRITE)
    try:
        ok = await instructions_mod.cancel_instruction(
            ctx.session, tenant_id=ctx.tenant_id, uid=uid
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No pending instruction with that id."
        )
    return {"ok": True}


# ---- the firm's own action trail (the audit waterfall) ------------------------------------------

#: Event-type families withheld from the firm's view of the stream — THE SAFETY RAIL. Do not
#: weaken this without reading docs/aml.md first.
#:
#: AML/identity screening review activity must never reach a firm through the audit stream. The
#: withholding posture (docs/aml.md; `screening:read`'s registry comment) is that a firm sees
#: screening OUTCOMES — via `GET /crm/contacts/{id}/screening`, which already withholds undecided
#: hits and the vendor dossier — and never Graphene's review ACTIVITY: a "Ran a screen on this
#: firm's client" or "Adjudicated a hit" event in the firm's own audit queue is exactly the
#: side-channel that posture (and the POCA 2002 s.333A tipping-off control behind it) exists to
#: close. Every /aml route shares the `aml` router tag, so its operation ids — and therefore its
#: event types (`action.` + operation id) — share the `action.aml-` prefix (run-aml-screen,
#: decide-aml-hit, set-aml-monitoring, link-aml-profile, run-aml-sync, connect/disconnect-aml-
#: operator); a PREFIX exclusion covers current and future aml routes alike, and it is applied
#: server-side after every caller filter, so no filter combination can un-hide the family
#: (pinned in tests/test_firm_audit_actions.py).
#:
#: Everything else stays visible ON PURPOSE — surveyed, decided, documented:
#: * Proxied book/support actions (`?tenant=` on crm/documents/storage/firm routes) are the very
#:   transparency this endpoint exists for — the firm watching Graphene's hand in its records.
#: * Admin tenant-management actions ABOUT the firm would be fine to show, but they never enter
#:   the firm's slice anyway: they name the firm in a path param, not `?tenant=`, so the event
#:   carries the operations tenant on both sides.
#: * The `break_glass` metadata flag stays on the event: the firm seeing that Graphene used
#:   emergency access on its data is a feature of the trail, not a leak.
FIRM_EXCLUDED_EVENT_PREFIXES: tuple[str, ...] = ("aml.", "action.aml-")


#: What an unresolvable `?contact=` narrows to. A serial no row can hold, so the predicate is kept
#: and matches nothing -- see `_contact_filter_id`.
_NO_SUCH_CONTACT = -1


async def _contact_filter_id(session: AsyncSession, uid: str | None) -> int | None:
    """`?contact=<uid>` -> the serial the ledgers recorded. None ONLY when no filter was asked for.

    A filter value in a URL names the client the way every other URL does. `audit_stream` and
    `comms.stream` keep their integer contracts because the STREAMS hold serials -- the path params
    a route received, and `comms.messages.recipient_contact_id` -- which are history and not
    rewritten.

    AN UNKNOWN UID NARROWS TO NOTHING, and the distinction from None is the whole point. Both
    streams read `None` as "no contact predicate at all", so returning it for a uid that resolved to
    nothing does not narrow -- it WIDENS, to the firm. That is not theoretical: the client record's
    Events tab sent the serial here, matched no uid, and answered page one with every client's
    events. So an unresolvable value becomes a serial no row can hold; the predicate stays, and the
    answer is empty. Still no 404 -- a filter that cannot be honoured must not take the page down.
    """
    if not uid:
        return None
    found = (
        await session.execute(text("SELECT id FROM crm.contacts WHERE uid = :uid"), {"uid": uid})
    ).scalar_one_or_none()
    return int(found) if found is not None else _NO_SUCH_CONTACT


@router.get("/audit/actions", name="list-audit-actions")
async def list_firm_audit_actions(
    ctx: FirmContext = Depends(target_firm_session),
    limit: int = listing.DEFAULT_LIMIT,
    before: int | None = None,
    #: The live poll's keyset end: rows NEWER than this id, newest first. Filters AND the deny list
    #: compose as usual — so this can never count an `action.aml-*` event the firm view withholds
    #: (`FIRM_EXCLUDED_EVENT_PREFIXES`), which a naive "how many since?" count would leak as a
    #: number even while refusing to show the rows.
    after: int | None = None,
    event: int | None = None,
    actor: str | None = None,
    #: Event-type filter, comma-separated PREFIXES (`event_type=action.firm-,action.tasks-`), ORed:
    #: a row matches if it starts with any of them, so an exact type is its own prefix. Contrast
    #: `tags`, which ANDs — two event types are alternatives, two entity kinds are both required.
    event_type: str | None = None,
    #: Free-text search across what a reader recognises a row by: the actor, the CLIENT's name,
    #: the document's name, the event type and the route (ORed; see
    #: `audit_stream._search_clause`). ANDed with every other filter, so it narrows, never replaces.
    q: str | None = None,
    method: str | None = None,
    proxied: bool | None = None,
    since: str | None = None,
    until: str | None = None,
    #: The client's UID -- a filter value in a URL, so never the serial. Resolved below.
    contact: str | None = None,
    document: str | None = None,
    document_family: bool = False,
    #: Entity-KIND filter, comma-separated (`tags=document,contact`), ANDed: a row must carry
    #: every kind named. Same wire format as the admin surface and as the toolbar's own builder
    #: (`@graphene/ui`'s `actionLogQuery`), so one spelling serves both trails.
    tags: str | None = None,
    facets: bool = False,
) -> dict[str, object]:
    """The firm's own slice of the action-event stream — the audit log waterfalled to the
    platform portal.

    ``contact`` narrows to one client's events (the client-record "Events" tab) and ``document`` to
    one document's — both ANDed AFTER the firm scope and the AML deny list, so they can only narrow,
    never surface a foreign or withheld row. ``document_family`` narrows to the document + shares
    families (the document-activity glances — the library's recent-activity card, a client's
    document rail; share links are a retired feature, but their historic rows still belong to a
    document's history); it composes with ``contact`` for a client's document events.

    Scope, hard-coded from the caller's session (there is deliberately NO ``?tenant=`` here —
    contrast `GET /admin/audit/actions`, whose `tenant` is an operator filter): events where the
    firm is the ACTING tenant (its own staff's actions, in any portal) OR the proxied TARGET
    (Graphene operations acting on the firm via ``?tenant=`` — transparency over the proxy
    middle-office). ``audit.actions`` is `service_role`-only, which `firm_session` provides while
    resolving the tenant from the caller's verified identity; the gate is the platform-portal
    `activity:read` permission, grantable to firm roles.

    Proxied rows are attributed to the NAMED operator (the actor join), not an anonymous
    "Graphene" — accountability is the point of the trail, and the operator acted on this firm's
    records. The UI labels them "Graphene support · {operator}".

    The AML event family is excluded server-side — see `FIRM_EXCLUDED_EVENT_PREFIXES` above; the
    stream must not become a side-channel around the screening withholding posture. An operations
    principal calling this endpoint gets the OPERATIONS tenant's own slice (their firm is
    Graphene), never a cross-tenant view — ops read the stream at the admin endpoint.

    Items carry the same read-time `entities` labels as the admin view (the resolver is shared):
    every id a row references belongs to this firm's slice by construction, so resolving labels
    with the session's service_role stays within what the firm may see.

    Filters and the `before` cursor mirror the admin endpoint (`app/audit_stream.py` is the one
    query); the envelope is `{items, has_more, facets}` — no `tenants` lookup, because there is no
    firm dropdown to feed. `event` (the master-detail deep-link id) is ANDed with BOTH the firm
    scope and the AML deny list, so a firm can never fetch a withheld screening event by guessing
    its id — a foreign or denied id returns nothing, not a leaked row.

    `?facets=true` returns the toolbar's event-type/actor VOCABULARY for this firm's slice —
    computed over the recent stream and ignoring every filter, so choosing an actor no longer
    shrinks the event-type dropdown. OPT-IN: the surface asks once on page entry and every later
    filter fetch leaves it off, so the extra scan is not a cost the common request pays; not
    requested ⇒ `facets: null`. It runs through the SAME AML deny list as the rows, because an
    OPTION is a disclosure too — a firm offered "Screened a client" as a filterable type would
    learn from the dropdown alone that screening happened."""
    require_permission(ctx.principal, ACTIVITY_READ)
    # ONE scope predicate, hoisted and handed to both the page and the facets below: the facet
    # lists must describe exactly the rows this firm may read, and two copies is how they would
    # come to disagree.
    # THE FIRM'S ROWS, AND THEN ONLY THE ONES THIS READER MAY SEE. The tenant half is the firm
    # boundary; `CONTACT_SCOPE_SQL` is the client one, and it is NOT optional - `activity:read` and
    # `contacts:read_all` are different permissions and the adviser role holds the first without the
    # second, so without this an adviser reads every client's events including their bank details.
    scope_sql = (
        f"(a.tenant_id = :firm OR a.target_tenant_id = :firm) AND {audit_stream.CONTACT_SCOPE_SQL}"
    )
    scope_params = {"firm": ctx.tenant_id}
    page = await audit_stream.fetch_actions(
        ctx.session,
        scope_sql=scope_sql,
        scope_params=scope_params,
        exclude_event_prefixes=FIRM_EXCLUDED_EVENT_PREFIXES,
        limit=limit,
        before=before,
        after=after,
        event_id=event,
        actor=actor,
        event_type=event_type,
        query=q,
        method=method,
        proxied=proxied,
        since=since,
        until=until,
        contact=await _contact_filter_id(ctx.session, contact),
        contact_uid=contact,
        document=document,
        document_family=document_family,
        tags=[t for t in (tags or "").split(",") if t.strip()],
    )
    # The read-time entity labeller lives beside the admin endpoint today; importing it here
    # (rather than copying it) keeps one field→kind map and one resolver per kind. Imported at
    # the call site, not module level: it is a private helper of that module reached across a
    # router boundary — if a third reader ever appears, move it into app/audit_stream.py.
    from app.routers.admin import _resolve_entities

    await _resolve_entities(ctx.session, page["items"])
    # The toolbar's event-type and actor vocabularies, over this firm's scope and independent of
    # every filter above — computed only when the caller ASKS (`?facets=true`), which is once per
    # page entry: a filter is applied in place now, so every later fetch re-reads page one with no
    # cursor and would recompute this for nothing.
    #
    # THE SAFETY RAIL, IN FACET FORM: the AML deny list is passed here too, and it must stay. A
    # dropdown entry is a disclosure — a firm offered "Screened a client" as a filterable type
    # would learn from the OPTION alone that screening happened, which is exactly what
    # `FIRM_EXCLUDED_EVENT_PREFIXES` (POCA 2002 s.333A, docs/aml.md) exists to prevent. Pinned by
    # tests/test_firm_audit_actions.py::test_facets_withhold_the_aml_family_the_rows_withhold.
    facet_lists = (
        await audit_stream.action_facets(
            ctx.session,
            scope_sql=scope_sql,
            scope_params=scope_params,
            exclude_event_prefixes=FIRM_EXCLUDED_EVENT_PREFIXES,
            # The client record's SCOPE, not a filter — so it narrows the vocabulary too. Without it
            # the record's dropdowns offered the whole FIRM's types and actors, and choosing one
            # no events on this client emptied the list, which reads as a broken filter. Every other
            # filter is still deliberately ignored here (see `action_facets`): a reader can clear
            # those and expects the option to still be offered.
            contact=await _contact_filter_id(ctx.session, contact),
            contact_uid=contact,
        )
        if facets
        else None
    )
    return {"items": page["items"], "has_more": page["has_more"], "facets": facet_lists}


# ---- outbound email (the Activity feed) --------------------------------------------------------


@router.get("/comms/messages", name="list-comms-messages")
async def list_firm_comms_messages(
    ctx: FirmContext = Depends(target_firm_session),
    limit: int = listing.DEFAULT_LIMIT,
    before: int | None = None,
    after: int | None = None,
    #: Comma-separated, ORed. `sent,delivered` is "either", the way a set of alternatives should be.
    status: str | None = None,
    #: Comma-separated PREFIXES, ORed — same wire format as the action stream's, so one toolbar
    #: builder serves both surfaces.
    event_type: str | None = None,
    template_code: str | None = None,
    category: str | None = None,
    #: The client this feed is narrowed to, by UID — the same spelling (and the same resolve) as the
    #: action stream's `?contact=`, because the client record shows the two side by side and one
    #: vocabulary across both tabs is the point. `comms.stream` keeps its integer contract: the
    #: LEDGER stores `recipient_contact_id`, a serial, and that is not rewritten.
    contact: str | None = None,
    q: str | None = None,
    since: str | None = None,
    until: str | None = None,
    #: The direction pages by OFFSET: the keyset cursor belongs to the default order alone.
    sort: str | None = None,
    dir: str | None = None,  # noqa: A002 - the wire name the portal sends
    offset: int = 0,
    facets: bool = False,
) -> dict[str, object]:
    """Every email this firm has sent, newest first — the Activity feed (``docs/comms.md``).

    Answers the question the action stream cannot: not "we recorded that a document was sent", but
    "the email went to this address, and the provider says it was delivered at 14:02". Until the
    ledger existed there was no answer at all — ``send_email`` returned a boolean and wrote nothing.

    Scope is hard-coded from the caller's session and there is deliberately NO ``?tenant=``.
    ``firm_session`` runs as ``service_role``, so RLS is bypassed and this predicate is the ONLY
    thing keeping one firm out of another's ledger — hence it is applied in
    ``comms.stream``, ahead of every filter, rather than being left to a policy that is not in
    force on this path.

    The gate is ``activity:read``, the same permission as the action trail: both answer "what has
    this firm been doing", and a firm administrator who may read one has no reason to be refused
    the other. The AML family is excluded server-side for the reason
    ``FIRM_EXCLUDED_EVENT_PREFIXES`` records — nothing in the ledger carries an AML event type
    today, and the exclusion is applied anyway so that stays true by construction rather than by
    luck.

    ``?facets=true`` returns the toolbar's vocabulary over the recent ledger, ignoring every
    filter, so choosing a status does not shrink the template dropdown. Opt-in: the page asks once
    on entry and every later filter fetch leaves it off.
    """
    require_permission(ctx.principal, ACTIVITY_READ)
    page = await comms_stream.fetch_messages(
        ctx.session,
        tenant_id=ctx.tenant_id,
        limit=limit,
        before=before,
        after=after,
        status=status,
        event_type=event_type,
        template_code=template_code,
        category=category,
        # Resolved at the door, by the same helper the action stream uses -- so an unresolvable
        # client narrows this feed to nothing rather than widening it to the firm.
        contact=await _contact_filter_id(ctx.session, contact),
        query=q,
        since=since,
        until=until,
        sort=sort,
        direction=dir,
        offset=offset,
        exclude_event_prefixes=FIRM_EXCLUDED_EVENT_PREFIXES,
    )
    facet_lists = (
        await comms_stream.message_facets(
            ctx.session,
            tenant_id=ctx.tenant_id,
            exclude_event_prefixes=FIRM_EXCLUDED_EVENT_PREFIXES,
        )
        if facets
        else None
    )
    await email_mod.refresh_allowlist()
    return {
        "items": page["items"],
        "has_more": page["has_more"],
        "facets": facet_lists,
        # Whether this DEPLOYMENT can send at all, and why not. Carried on the feed's own response
        # rather than read from `/health/ready`: that probe is infrastructure (out of the OpenAPI
        # schema, 503s when an unrelated dependency is down, and no portal calls it), and the feed
        # needs this as product context. Without it a dev box's wall of "Suppressed" reads as
        # "every email is failing" rather than as the send guard doing its job.
        "sending_disabled": email_mod.sending_disabled(),
    }


@router.get("/comms/messages/{uid}", name="get-comms-message")
async def get_firm_comms_message(
    uid: str,
    ctx: FirmContext = Depends(target_firm_session),
) -> dict[str, object]:
    """One message: the envelope, the delivery timeline, the body as it was sent, and the wire
    request.

    The body is what makes this worth opening — "did the client get it" is usually followed by
    "what did it say". It is rendered in a SANDBOXED iframe on the client side and never injected
    into the portal's own document: it is firm-authored template output containing client data, and
    the portal must not hand it the adviser's session.

    The request is the JSON posted to the provider (attachment bytes stripped). It is what the Raw
    tab shows — not a second dump of the preview HTML.

    A uid outside this firm's ledger 404s rather than 403ing — the two are the same answer here,
    and distinguishing them would confirm that a message exists.
    """
    require_permission(ctx.principal, ACTIVITY_READ)
    message = await comms_stream.fetch_message(
        ctx.session,
        tenant_id=ctx.tenant_id,
        uid=uid,
        exclude_event_prefixes=FIRM_EXCLUDED_EVENT_PREFIXES,
    )
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return message


# ---- the event registry (what each event does, and what it notifies) ---------------------------


@router.get("/event-registry", name="get-event-registry")
async def get_firm_event_registry(
    ctx: FirmContext = Depends(target_firm_session),
) -> dict[str, object]:
    """EVERY event this platform records, and what this firm has attached to each.

    All of them, deliberately — an earlier version listed only the ~17 a notification can currently
    be authored against, which is a shorter and less useful answer to "what happens in my firm".
    The cost of completeness is that most rows cannot be assigned anything yet, so each one says
    plainly whether it can and, when it cannot, why.

    **What makes an event assignable is a SUBJECT.** `catalogue._offered_audiences` derives the
    audiences (client / adviser / actor) from a subject kind alone, off generic resolvers — which is
    why the twelve "offered" triggers are five-tuples with everything else computed. So the only
    question per event is whether it captured something a person can be resolved from. An event that
    records no contact, account, task, application or case has nobody to notify: not because the
    machinery is missing, but because the row does not say who it concerned. Those are reported
    `assignable: false` with that reason rather than offered and then failing.

    Four states, and the counts are worth reading before extending anything:

    * `assignable` — a catalogue trigger covers it; rules can be authored now.
    * `ready` — a subject source exists but no trigger uses it. A five-tuple in
      `catalogue._OFFERED` is all each of these needs.
    * `declarable` — the event RECORDS a subject, but `automation._SUBJECTS` declares no source for
      it, so nothing can resolve a recipient. A declaration, not new capture — the cheapest real
      work on this surface.
    * `actor_only` — the event names no client, account or task. The ACTOR is still resolvable (it
      is on every action row), so an actor or role audience could reach these; a client or adviser
      cannot.

    The two counts join on the raw `event_type`, not on a friendly key: `flows.triggers` and the
    notification catalogue use different names for the same event, and the event is the only thing
    they agree on.
    """
    require_permission(ctx.principal, NOTIFICATIONS_READ)

    catalogue = notif_rules.catalogue_json()
    rules = await notif_rules.list_rules(ctx.session, ctx.tenant_id)

    by_trigger: dict[str, list[dict[str, object]]] = {}
    for rule in rules:
        by_trigger.setdefault(str(rule["trigger_key"]), []).append(rule)

    # event_type -> EVERY catalogue entry covering it, in catalogue order.
    #
    # A LIST, not a single value, and `catalogue._BY_EVENT` uses `setdefault(...).append(...)` for
    # exactly this reason: the relationship runs both ways. One trigger names several events, AND
    # one event is named by several triggers — `message.sent` and `message.attachment_sent` are
    # each claimed by both `message.received_by_adviser` and `message.received_by_client`. A
    # last-wins dict dropped the first of those from the registry altogether: it appeared on no
    # row, so a firm could not see, edit, disable or delete its own rule for it, and it was absent
    # from the flat rule list the editor is handed as well.
    triggers_for_event: dict[str, list[dict[str, Any]]] = {}
    for entry in catalogue:
        for event in entry.get("event_types", []):
            triggers_for_event.setdefault(str(event), []).append(entry)

    # TWO different questions, and conflating them is what the first version of this got wrong.
    #
    #  * `resolvable` — can a CLIENT or ADVISER be resolved from this event? That is decided by
    #    `automation._SUBJECTS` (read via `events_with_subject_source`), which every generic
    #    audience resolver funnels through. An event absent from it reaches nobody but the actor,
    #    however much its route appears to name something.
    #  * `captures` — does the event RECORD an entity in its metadata (`event_schema.REGISTRY`)?
    #    A capture with no subject source declared is unreachable, so the difference between these
    #    two sets is its own state: closing it is a declaration, not new capture.
    resolvable = flow_automation.events_with_subject_source()
    captures: dict[str, set[str]] = {}
    for declared in event_schema.REGISTRY:
        declared_kinds = {f.entity_kind for f in declared.fields if f.entity_kind}
        if declared_kinds:
            captures[declared.event_type] = {k for k in declared_kinds if k}

    automation_rows = (
        await ctx.session.execute(
            text(
                "SELECT trigger_key, count(*) AS n FROM flows.automations "
                "WHERE tenant_id = :t AND enabled GROUP BY trigger_key"
            ),
            {"t": ctx.tenant_id},
        )
    ).all()
    automations_by_event: dict[str, int] = {}
    for trigger_key, count in automation_rows:
        spec = flow_triggers.resolve(str(trigger_key))
        # An unknown stored key is skipped rather than guessed at — `resolve` returns None for it.
        if spec is not None and spec.event:
            automations_by_event[spec.event] = automations_by_event.get(spec.event, 0) + int(count)

    email_rows = (
        await ctx.session.execute(
            text(
                "SELECT event_type, count(*) AS n FROM comms.messages "
                "WHERE tenant_id = :t AND event_type IS NOT NULL "
                "  AND created_at >= now() - interval '30 days' "
                "GROUP BY event_type"
            ),
            {"t": ctx.tenant_id},
        )
    ).all()
    emails_by_event = {str(event): int(count) for event, count in email_rows}

    declared_events = sorted(
        set(actions.EVENT_TYPES.values()) | set(actions.SYNTHETIC_EVENT_TYPES.values())
    )
    items: list[dict[str, Any]] = []
    counts = {"assignable": 0, "ready": 0, "declarable": 0, "actor_only": 0}
    for event in declared_events:
        event_triggers = triggers_for_event.get(event, [])
        kinds = sorted(captures.get(event, set()))
        if event_triggers:
            state, reason = "assignable", None
        elif event in resolvable:
            state, reason = (
                "ready",
                "A client can be resolved from this event; it has no notification defined yet.",
            )
        elif set(kinds) & flow_triggers.SUBJECT_KINDS:
            state, reason = (
                "declarable",
                "This event records a subject but no source is declared for it, so nothing can "
                "resolve a recipient from it yet. Needs a `_SUBJECTS` entry, not new capture.",
            )
        else:
            state, reason = (
                "actor_only",
                "This event names no client, account or task. Only the person who performed it, or "
                "a role, could be told — a client or adviser cannot be resolved from it.",
            )
        counts[state] += 1
        items.append(
            {
                "event_type": event,
                "resource": actions.event_resource(event),
                "state": state,
                "unavailable_reason": reason,
                "subject_kinds": kinds,
                # The actor is on EVERY action row (`_resolve_actor` reads `actor_id` and needs no
                # subject), so this is true throughout — it is what makes `actor_only` a state
                # rather than a dead end.
                "actor_resolvable": True,
                # EVERY trigger that fires on this event, so the editor needs no second call.
                # `trigger` stays as the first, for a reader that wants one label; `triggers` is
                # the truth, and the UI builds its attached list from all of them.
                "trigger": event_triggers[0] if event_triggers else None,
                "triggers": event_triggers,
                # DEDUPED by uid: two triggers claiming this event both contribute their rules, and
                # a trigger claiming two events would otherwise have its rules counted twice once
                # the caller flattens this list.
                "rules": list(
                    {
                        str(rule["uid"]): rule
                        for entry in event_triggers
                        for rule in by_trigger.get(str(entry["key"]), [])
                    }.values()
                ),
                "automation_count": automations_by_event.get(event, 0),
                "email_count_30d": emails_by_event.get(event, 0),
            }
        )

    return {"items": items, "summary": {"total": len(items), **counts}}


# ---- notification rules (firm self-service — the builder in firm posture) ----------------------
# A firm reads its rules + the catalogue, and edits the rules Graphene marked `firm_editable` (plus
# any it authored itself). It cannot touch the ops guardrails (`mandatory`, `firm_editable`) — those
# are stripped from a firm PATCH — and may only delete rules it authored.


class FirmRuleCreate(BaseModel):
    trigger_key: str
    audience: str
    role_slug: str | None = None
    label: str = ""
    title: str
    body: str | None = None
    link: str | None = None
    priority: str = "normal"
    default_in_app: bool = True
    default_email: bool = False
    mandatory: bool = False
    enabled: bool = True
    # The email template this rule renders (an email.templates code); NULL → the general wrapper.
    email_code: str | None = None


class FirmRuleUpdate(BaseModel):
    label: str | None = None
    title: str | None = None
    body: str | None = None
    link: str | None = None
    priority: str | None = None
    default_in_app: bool | None = None
    default_email: bool | None = None
    mandatory: bool | None = None
    enabled: bool | None = None
    email_code: str | None = None


async def _firm_rule(ctx: FirmContext, uid: str, *, for_delete: bool) -> dict[str, object]:
    """Fetch a rule and assert the firm may touch it, or raise. Edit needs firm-authored OR
    `firm_editable`; delete needs firm-authored (a firm never deletes an ops-authored rule)."""
    rule = await notif_rules.get_rule(ctx.session, uid)
    if rule is None or rule["tenant_id"] != ctx.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    firm_authored = rule["authored_by"] == "firm"
    allowed = firm_authored if for_delete else (firm_authored or rule["firm_editable"])
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This rule is managed by Graphene."
        )
    return rule


@router.get("/notification-catalogue", name="firm-notification-catalogue")
async def firm_notification_catalogue(
    ctx: FirmContext = Depends(target_firm_session),
) -> list[dict[str, object]]:
    require_permission(ctx.principal, NOTIFICATIONS_READ)
    return notif_rules.catalogue_json()


@router.get("/notification-email-templates", name="firm-notification-email-templates")
async def firm_notification_email_templates(
    ctx: FirmContext = Depends(target_firm_session),
) -> list[dict[str, str]]:
    """The email templates a rule may render — the builder's "Email template" dropdown."""
    require_permission(ctx.principal, NOTIFICATIONS_READ)
    return notif_rules.email_templates_json()


@router.get("/notification-rules", name="firm-list-notification-rules")
async def firm_list_notification_rules(
    ctx: FirmContext = Depends(target_firm_session),
) -> list[dict[str, object]]:
    require_permission(ctx.principal, NOTIFICATIONS_READ)
    return await notif_rules.list_rules(ctx.session, ctx.tenant_id)


@router.post("/notification-rules", name="firm-create-notification-rule")
async def firm_create_notification_rule(
    body: FirmRuleCreate, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, object]:
    require_permission(ctx.principal, NOTIFICATIONS_MANAGE)
    try:
        # A firm's own rule is always firm-editable; that ops guardrail is not the firm's to set.
        return await notif_rules.create_rule(
            ctx.session, ctx.tenant_id, authored_by="firm", firm_editable=True, **body.model_dump()
        )
    except notif_rules.RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/notification-rules/{uid}", name="firm-update-notification-rule")
async def firm_update_notification_rule(
    uid: str, body: FirmRuleUpdate, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, object]:
    require_permission(ctx.principal, NOTIFICATIONS_MANAGE)
    await _firm_rule(ctx, uid, for_delete=False)
    try:
        # scope='firm' — the firm's own edit, attributed to the firm on the version history.
        updated = await notif_rules.update_rule(
            ctx.session, uid, scope="firm", **body.model_dump(exclude_none=True)
        )
    except notif_rules.RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assert updated is not None  # noqa: S101 — _firm_rule already proved it exists
    return updated


@router.delete("/notification-rules/{uid}", name="firm-delete-notification-rule")
async def firm_delete_notification_rule(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, bool]:
    require_permission(ctx.principal, NOTIFICATIONS_MANAGE)
    await _firm_rule(ctx, uid, for_delete=True)
    await notif_rules.delete_rule(ctx.session, uid, scope="firm")
    return {"ok": True}


@router.get("/notification-rules/{uid}/versions", name="firm-notification-rule-versions")
async def firm_notification_rule_versions(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> list[dict[str, object]]:
    """The firm's own view of a rule's change history — who changed it (the firm or Graphene) and
    what it held. Scoped to the caller's tenant so it can't read another firm's rule by id."""
    require_permission(ctx.principal, NOTIFICATIONS_READ)
    rule = await notif_rules.get_rule(ctx.session, uid)
    if rule is None or rule["tenant_id"] != ctx.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return await notif_rules.list_rule_versions(ctx.session, uid)


@router.get("/data-sources", name="list-firm-data-sources")
async def list_firm_data_sources(
    ctx: FirmContext = Depends(target_firm_session),
) -> list[dict[str, object]]:
    """Which custodians feed this firm's book, and whether they are feeding it.

    THE FIRM HAD NO VIEW OF THIS AT ALL. Whether a firm's book is current — which custodians it is
    connected to, when data last landed, what was refused — was answerable only from Graphene's
    own operations console, on pages the firm cannot reach. Its own advisers were reading a book
    with no way to tell "this client has no transactions" from "nothing has landed since Tuesday".

    SCOPED SERVER-SIDE, with no tenant parameter, exactly as `/firm/audit/actions` is: the tenant
    comes from the caller's verified identity, so there is no argument for a caller to get wrong.

    WHAT IT DELIBERATELY WITHHOLDS. Not the slice keys — those are the custodian's own identifiers
    for the firm's records and belong to the enablement, which is Graphene's to set. Not the SQL
    console, the model graph, the run history or any other firm's anything. What is left is the
    firm's own answer to "is our data current, and did any of it not make it".
    """
    require_permission(ctx.principal, DATA_SOURCES_READ)
    enablements = await providers_mod.list_for_tenant(ctx.session, ctx.tenant_id)

    out: list[dict[str, object]] = []
    for row in enablements:
        if not row.get("enabled"):
            # A disabled connection is not a data source. It is also not this surface's business
            # to explain why it is off — that is a conversation with Graphene, not a status line.
            continue
        provider = str(row["provider"])
        meta = providers_mod.meta(provider)
        # The last RUN for this firm, and what it did. `merge_runs` is already scoped by tenant.
        ledger = await platforms_mod.merge_runs(
            ctx.session, provider, tenant_id=ctx.tenant_id, limit=1
        )
        runs = ledger["runs"]
        last = runs[0] if runs else None
        out.append(
            {
                "provider": provider,
                "name": meta["name"],
                "role": meta["role"],
                "description": meta["description"],
                "transports": meta["transports"],
                # Stamped by the merge. `last_fanout_at` is the wire name until the contract phase
                # of 0079 drops the column it was read from.
                "last_merged_at": row.get("last_fanout_at"),
                "last_run": (
                    {
                        "run_at": last["run_at"],
                        "created": last["created"],
                        "updated": last["updated"],
                        "rejected": last["rejected"],
                        # How many ENTITIES failed, not which — the firm needs to know its book is
                        # incomplete and to ask; the provider's column names are not its problem.
                        "failed": last["failed"],
                    }
                    if last
                    else None
                ),
            }
        )
    return out
