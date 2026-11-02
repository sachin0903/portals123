"""Cross-tenant admin surface (Graphene Internal Operations staff).

Two-layer authorisation: the `admin_session` dependency is the coarse gate (the caller must be a
Graphene operations principal — system, or resolved to the operations tenant — and it runs as
service_role + audits every write); then each endpoint enforces a granular **operations
permission** via `requires(...)` / `_require_tenant_scope(...)` — the `tenants` / `operations` /
`model` / `audit` read+write permissions from `app.permissions` (write implies read; `system`
bypasses). The member/role endpoints are shared across firms, the operations tenant and the model
tenant, so their gate is chosen by the target tenant. Network-locked at the edge in deployment.
"""

import json
import re
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Path,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from workos._errors import WorkOSError

from app import (
    addressing,
    audit_actions,
    audit_stream,
    branding_uploads,
    crypto,
    currencies,
    directory,
    environments,
    listing,
    platform_entities,
    provisioning,
    rbac,
    storage,
    storage_usage,
    tenant_data,
    tenant_registry,
)
from app import branding as branding_mod
from app import platform_actions as platform_actions_mod
from app import platforms as platforms_mod
from app import providers as providers_mod
from app import tenant_storage as tenant_storage_mod
from app.addressing import admin_environment_id, admin_tenant_id
from app.auth import Principal, get_current_principal, require_any_permission, require_permission
from app.branding import Branding
from app.config import settings
from app.db import admin_session
from app.email import catalog as email_catalog
from app.email import defaults as email_defaults
from app.email import sender_domains
from app.email import templates_service as email_templates_service
from app.email import transport as email_transport
from app.flows import ai_usage
from app.notifications import config as notif_config
from app.notifications import registry
from app.notifications import rules_service as notif_rules
from app.permissions import (
    AUDIT_READ,
    BOOK_READ,
    MODEL_READ,
    MODEL_WRITE,
    OPERATIONS_READ,
    OPERATIONS_WRITE,
    PAGES_WRITE,
    PLATFORMS_QUERY,
    PLATFORMS_READ,
    PLATFORMS_WRITE,
    TENANT_PORTAL_READ,
    TENANTS_READ,
    TENANTS_WRITE,
    Permission,
)
from app.routers.email_templates import (
    CreateEmailTemplate,
    EmailCodeOut,
    EmailDefaultOut,
    EmailImageLibraryOut,
    EmailTemplateOut,
    EmailVersionDetailOut,
    EmailVersionOut,
    PreviewEmail,
    PreviewEmailOut,
    PublishEmailResultOut,
    SaveEmailVersion,
    SendTestEmail,
    SendTestEmailOut,
    UpdateEmailTemplate,
    UploadedEmailImageOut,
    _ImageUploadRoute,
    build_preview,
    list_images,
    send_test,
    store_image,
)
from app.routers.email_templates import _raise_http as _raise_email_http
from app.session_refresh import refresh_member_sessions

router = APIRouter(prefix="/admin", tags=["admin"])


def requires(permission: Permission | str) -> Callable[..., Principal]:
    """A dependency that authorises the caller against an operations permission (``system``
    bypasses). Use on admin endpoints whose required permission is fixed. Endpoints acting on a
    specific tenant use ``_require_tenant_scope`` instead (the permission depends on the target)."""

    def _dep(principal: Principal = Depends(get_current_principal)) -> Principal:
        require_permission(principal, permission)
        return principal

    return _dep


# Module-level dependency singletons (a bare name in Depends(...) — not a call in the default,
# so ruff's B008 stays happy; mirrors how admin_session is referenced).
_tenants_read = requires(TENANTS_READ)
_tenants_write = requires(TENANTS_WRITE)
_operations_read = requires(OPERATIONS_READ)
_model_read = requires(MODEL_READ)
_audit_read = requires(AUDIT_READ)
_platforms_read = requires(PLATFORMS_READ)
_platforms_write = requires(PLATFORMS_WRITE)
_platforms_query = requires(PLATFORMS_QUERY)
_book_read = requires(BOOK_READ)


async def _tenant_scope_permission(
    session: AsyncSession, tenant_id: int, *, write: bool
) -> Permission:
    """The operations permission required to read/manage a tenant through the admin surface. The
    members/roles endpoints are shared across firms, the operations tenant and the model tenant, so
    the gate depends on WHICH tenant is targeted: ops tenant → operations:*, model tenant →
    model:*, a firm → tenants:* — each read vs write.

    A path id naming no tenant is 404 HERE — the one place that answers it once for every
    `/admin/tenants/{tenant_uid}/...` endpoint, instead of each handler failing further down in
    whatever way its own SQL happens to break. The session is `service_role` (BYPASSRLS), so an
    absent row means absent and not merely hidden."""
    try:
        ops_tenant_id = await environments.operations_tenant_id(session)
    except LookupError:
        ops_tenant_id = None
    if tenant_id == ops_tenant_id:
        return OPERATIONS_WRITE if write else OPERATIONS_READ
    row = (
        await session.execute(
            text("SELECT is_model FROM auth.tenants WHERE id = :t"), {"t": tenant_id}
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    if bool(row[0]):
        return MODEL_WRITE if write else MODEL_READ
    return TENANTS_WRITE if write else TENANTS_READ


# Date parsing + LIKE escaping for the audit read surfaces moved to `app/audit_stream.py` when
# the action stream grew its firm-facing view (`GET /firm/audit/actions`) — one implementation
# feeds both endpoints. The alias keeps this module's call sites reading as before.
_parse_instant = audit_stream.parse_instant


async def _require_tenant_scope(
    session: AsyncSession, principal: Principal, tenant_id: int, *, write: bool
) -> None:
    require_permission(principal, await _tenant_scope_permission(session, tenant_id, write=write))


# A tenant's people are assigned one of that tenant's own roles (by slug). Validated against
# the tenant's roles at provision time, so any tenant-defined slug is allowed.
RoleSlug = Annotated[str, Field(pattern="^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")]

# A member path segment. `auth.users.id` is a uuid, and these handlers pass the segment straight
# into `WHERE id = :u` — so a non-uuid reached psycopg as a cast failure and surfaced as an
# unhandled 500 (`invalid input syntax for type uuid`) instead of a client error. Typed here so
# FastAPI answers 422 before any SQL runs. Pydantic's parser is also WIDER than Postgres's: it
# accepts the braced, dash-stripped, uppercase and `urn:uuid:` spellings and canonicalises them,
# and the last of those is the one Postgres rejects — so this normalises the alias rather than
# letting the same identifier 404 or 500 depending on how it was written.
MemberId = Annotated[UUID, Path(description="The member's auth.users id (uuid)")]


class CreateTenant(BaseModel):
    name: str
    alias: str = Field(pattern="^[a-z0-9-]+$")
    # Optional link to the tenant's WorkOS project (created manually). No WorkOS call is made.
    workos_project_id: str | None = None
    # Clone the model tenant's roles as a starting point (false => a blank tenant, no roles).
    from_model: bool = True


class UpdateTenant(BaseModel):
    name: str | None = None
    alias: str | None = Field(default=None, pattern="^[a-z0-9-]+$")
    website: str | None = None
    enabled: bool | None = None


CurrencyCode = Annotated[str, Field(pattern="^[A-Z]{3}$")]


class SetTenantCurrencies(BaseModel):
    """A firm's supported currencies and the one new monetary records default to."""

    base_currency: CurrencyCode
    allowed_currencies: list[CurrencyCode] = Field(min_length=1)


Portal = Annotated[str, Field(pattern="^(client|platform|operations)$")]


class CreateMember(BaseModel):
    email: str
    role_slug: RoleSlug
    first_name: str | None = None
    last_name: str | None = None


class GrantPortal(BaseModel):
    """Grant/assign a role in a portal. The role must belong to that portal."""

    role_slug: RoleSlug


class ChangeRole(BaseModel):
    role_slug: RoleSlug


class SetPermissions(BaseModel):
    permissions: list[str]


class CreateOrgRole(BaseModel):
    name: str
    slug: str = Field(pattern="^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")
    portal: str = Field(pattern="^(client|platform|operations)$")
    description: str | None = None
    permissions: list[str] = []


class RegisterEnvironment(BaseModel):
    """Non-secret WorkOS routing config for a tenant, for THIS instance. One config per tenant per
    instance — the stage is the instance's (settings.app_stage), so it isn't supplied here. Only the
    client id is required; issuer + portal hosts are optional (needed for real per-tenant routing,
    not for local dev, which uses the fallback environment)."""

    workos_client_id: str = Field(min_length=1)
    issuer: str | None = None
    client_portal_host: str | None = None
    platform_host: str | None = None
    # The admin/operations portal's host — Graphene tenant only.
    operations_host: str | None = None


class SetEnvironmentApplications(BaseModel):
    """The per-portal WorkOS APPLICATION credentials (portal-via-application). Each portal is its
    own WorkOS application, created by hand in the dashboard (the onboarding checklist tracks
    this): its client id AND its client secret are recorded here — a portal signs in through its
    application with the application's OWN secret. All optional: a NULL leaves the stored value
    untouched (COALESCE-preserve), so one portal's credentials are saved without disturbing
    another's. Secrets are Fernet-encrypted at rest and never echoed back."""

    client_portal_client_id: str | None = None
    platform_client_id: str | None = None
    operations_client_id: str | None = None
    client_portal_secret: str | None = None
    platform_secret: str | None = None
    operations_secret: str | None = None


class EnvironmentSecrets(BaseModel):
    """The environment's WorkOS api key + session cookie password (encrypted on write).
    The cookie password must be long enough for iron-session sealing (>= 32 chars)."""

    workos_api_key: str = Field(min_length=1)
    cookie_password: str = Field(min_length=32)


class SetBranding(BaseModel):
    """A tenant's white-label branding (structured tokens) + optional firm name."""

    name: str | None = None
    branding: Branding


# Branding asset kinds an upload may target (a fixed allowlist -> object-storage key label).
_ASSET_KINDS = {"logo", "logo-dark", "icon", "favicon", "card-art"}


@router.get("/permissions", name="list-permissions")
async def list_permissions(
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> list[dict[str, object]]:
    """The permission catalogue. Read by the three role editors, so any of their read
    permissions unlocks it (rather than the endpoint being the one with no granular gate)."""
    require_any_permission(principal, TENANTS_READ, MODEL_READ, OPERATIONS_READ)
    return await rbac.list_permissions(session)


@router.get("/model", name="get-model-tenant")
async def get_model_tenant(
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_model_read),
) -> dict[str, object]:
    """The canonical model tenant (the role preset new tenants clone). Its roles are edited via
    the usual /tenants/{id}/roles endpoints (mirror-only, since it has no WorkOS project)."""
    row = (
        (
            await session.execute(
                text(
                    # Full Tenant shape (matches get-tenant-by-uid), so the admin section layout can
                    # serve the model tenant from THIS model:read-gated endpoint — a model-only
                    # operator cannot call by-uid, which is tenants:read-gated.
                    "SELECT id, uid, alias, name, website, enabled, parent_id, workos_project_id "
                    "FROM auth.tenants WHERE is_model LIMIT 1"
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No model tenant")
    return dict(row)


@router.get("/operations", name="get-operations")
async def get_operations(
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_operations_read),
) -> dict[str, object]:
    """Internal Operations home: the tenant that owns the core project (Graphene) + its ops org
    id, ensuring the org exists. The ops UI drives staff/roles through the usual
    /tenants/{tenant_uid}/members and /roles endpoints against this tenant, filtered to the
    'operations' portal. 409 if the core environment isn't registered.

    `tenant_uid` rides along beside the id because those endpoints are addressed by uid: without
    it the ops pages would resolve the id back into a uid on every action, which is the round trip
    uid addressing removed. The id stays for callers that key on it."""
    try:
        tenant_id = await environments.operations_tenant_id(session)
        # One-firm-org: ops staff authenticate via the Graphene tenant's own firm org; the
        # operations PORTAL is the operations application + a portal='operations' membership.
        org_id, _client = await environments.firm_org_and_client(session, tenant_id=tenant_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc
    tenant_uid = (
        await session.execute(text("SELECT uid FROM auth.tenants WHERE id = :t"), {"t": tenant_id})
    ).scalar_one()
    return {"tenant_id": tenant_id, "tenant_uid": tenant_uid, "operations_org_id": org_id}


@router.get("/tenants/{tenant_uid}/roles", name="list-tenant-roles")
async def list_tenant_roles(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> list[dict[str, object]]:
    """A tenant's organisation roles with their permissions (the per-firm overrides)."""
    await _require_tenant_scope(session, principal, tenant_id, write=False)
    return await rbac.list_org_roles(session, tenant_id)


@router.post("/tenants/{tenant_uid}/roles", name="create-tenant-role")
async def create_tenant_role(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    body: CreateOrgRole,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Create a per-tenant organisation role (WorkOS + mirror)."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        return await rbac.create_org_role(
            session,
            tenant_id=tenant_id,
            name=body.name,
            slug=body.slug,
            portal=body.portal,
            description=body.description,
            permission_slugs=body.permissions,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.post("/tenants/{tenant_uid}/roles/sync", name="sync-tenant-roles")
async def sync_tenant_roles(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Reconcile the mirror against WorkOS — pulls in any organisation roles that already exist
    on the tenant's portal orgs (e.g. created directly, or a prior request that wrote to WorkOS
    but never committed the mirror row) so they show up and stop 422'ing as 'already in use'."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        synced = await rbac.sync_org_roles_from_workos(session, tenant_id)
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc
    return {"tenant_id": tenant_id, "synced": synced}


# ---- tenant WorkOS environments (project-per-tenant, docs/identity-and-access.md §0) ----


@router.get("/tenants/{tenant_uid}/environment", name="get-environment")
async def get_environment(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> dict[str, object] | None:
    """A tenant's WorkOS config for THIS instance (non-secret; `has_secrets` flags whether keys
    are set). One config per tenant per instance. None if not registered yet."""
    return await environments.get_environment(session, tenant_id)


@router.put("/tenants/{tenant_uid}/environment", name="register-environment")
async def register_environment(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    body: RegisterEnvironment,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, object]:
    """Register (or update) a tenant's WorkOS routing config for this instance (stage is the
    instance's). The operator creates the project/environment in WorkOS first, then records it."""
    exists = (
        await session.execute(text("SELECT 1 FROM auth.tenants WHERE id = :t"), {"t": tenant_id})
    ).scalar_one_or_none()
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return await environments.set_environment(
        session,
        tenant_id=tenant_id,
        workos_client_id=body.workos_client_id,
        issuer=body.issuer,
        client_portal_host=body.client_portal_host,
        platform_host=body.platform_host,
        operations_host=body.operations_host,
    )


@router.put("/tenants/{tenant_uid}/environment/applications", name="set-environment-applications")
async def set_environment_applications(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    body: SetEnvironmentApplications,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, object]:
    """Record this tenant's per-portal WorkOS APPLICATION client ids (portal-via-application). The
    operator creates each portal's application in the WorkOS dashboard (tracked by the onboarding
    checklist), then records its client id AND its client secret here — the portal signs in with
    the pair. COALESCE-preserve, so saving one portal's credentials never wipes another's. Secrets
    are Fernet-encrypted; never echoed back. 404 if the environment isn't registered yet."""
    try:
        result = await environments.set_environment_applications(
            session,
            tenant_id=tenant_id,
            client_portal_client_id=body.client_portal_client_id,
            platform_client_id=body.platform_client_id,
            operations_client_id=body.operations_client_id,
        )
        if body.client_portal_secret or body.platform_secret or body.operations_secret:
            await environments.set_environment_app_secrets(
                session,
                tenant_id=tenant_id,
                client_portal_secret=body.client_portal_secret,
                platform_secret=body.platform_secret,
                operations_secret=body.operations_secret,
            )
        return result
    except crypto.SecretsNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "SECRETS_MASTER_KEY is not set (or is not a valid Fernet key) on the API — "
                "application secrets cannot be encrypted until it is."
            ),
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Register the tenant's WorkOS environment before setting its applications.",
        ) from exc


class NotificationConfigItem(BaseModel):
    category: str
    audience: str = "client"
    enabled: bool = True
    in_app: bool | None = None
    email: bool | None = None
    mandatory: bool = False


class NotificationConfigUpdate(BaseModel):
    config: list[NotificationConfigItem]


@router.get("/tenants/{tenant_uid}/notification-config", name="get-notification-config")
async def get_notification_config(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> list[dict[str, object]]:
    """The firm's per-category notification config (the tenant layer of preference resolution) —
    stored rows merged over the registry defaults, firm-facing categories only."""
    return await notif_config.get_tenant_config(session, tenant_id)


@router.put("/tenants/{tenant_uid}/notification-config", name="set-notification-config")
async def set_notification_config(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    body: NotificationConfigUpdate,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> list[dict[str, object]]:
    """Upsert the firm's notification config (per-category enabled + channel defaults)."""
    return await notif_config.set_tenant_config(
        session, tenant_id, [i.model_dump() for i in body.config]
    )


class NotificationRuleCreate(BaseModel):
    trigger_key: str
    audience: str
    role_slug: str | None = None
    label: str = ""
    title: str
    body: str | None = None
    link: str | None = None
    priority: registry.Priority = "normal"
    default_in_app: bool = True
    default_email: bool = False
    mandatory: bool = False
    firm_editable: bool = True
    enabled: bool = True
    # The email template this rule renders (an email.templates code); NULL → the general wrapper.
    email_code: str | None = None


class NotificationRuleUpdate(BaseModel):
    label: str | None = None
    title: str | None = None
    body: str | None = None
    link: str | None = None
    priority: registry.Priority | None = None
    default_in_app: bool | None = None
    default_email: bool | None = None
    mandatory: bool | None = None
    firm_editable: bool | None = None
    enabled: bool | None = None
    email_code: str | None = None


@router.get("/notification-catalogue", name="notification-catalogue")
async def notification_catalogue(
    _p: Principal = Depends(_tenants_read),
) -> list[dict[str, object]]:
    """The builder's menu: every trigger + the audiences it offers (with default copy)."""
    return notif_rules.catalogue_json()


@router.get("/notification-email-templates", name="notification-email-templates")
async def notification_email_templates(
    _p: Principal = Depends(_tenants_read),
) -> list[dict[str, str]]:
    """The email templates a rule may render — the builder's "Email template" dropdown."""
    return notif_rules.email_templates_json()


@router.get("/tenants/{tenant_uid}/notification-rules", name="list-notification-rules")
async def list_notification_rules(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> list[dict[str, object]]:
    return await notif_rules.list_rules(session, tenant_id)


@router.post(
    "/tenants/{tenant_uid}/notification-rules/load-defaults", name="load-notification-defaults"
)
async def load_notification_defaults(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, int]:
    """Seed the tenant's notification rules from the shipped built-in defaults (real, editable
    rows). Idempotent + additive — only fills gaps. Returns how many were created."""
    created = await notif_rules.load_default_rules(session, tenant_id)
    return {"created": created}


@router.post("/tenants/{tenant_uid}/notification-rules", name="create-notification-rule")
async def create_notification_rule(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    body: NotificationRuleCreate,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, object]:
    try:
        return await notif_rules.create_rule(
            session, tenant_id, authored_by="graphene", **body.model_dump()
        )
    except notif_rules.RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/tenants/{tenant_uid}/notification-rules/{uid}", name="update-notification-rule")
async def update_notification_rule(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    uid: str,
    body: NotificationRuleUpdate,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, object]:
    try:
        # scope='operations' — a Graphene edit, recorded on the version history as such.
        updated = await notif_rules.update_rule(
            session, uid, scope="operations", **body.model_dump(exclude_none=True)
        )
    except notif_rules.RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Notification rule not found")
    return updated


@router.delete("/tenants/{tenant_uid}/notification-rules/{uid}", name="delete-notification-rule")
async def delete_notification_rule(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    uid: str,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, bool]:
    await notif_rules.delete_rule(session, uid, scope="operations")
    return {"ok": True}


@router.get(
    "/tenants/{tenant_uid}/notification-rules/{uid}/versions", name="notification-rule-versions"
)
async def notification_rule_versions(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    uid: str,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> list[dict[str, object]]:
    """A rule's change history — every create/update/delete, who made it (Graphene vs firm) + what
    it held. Scoped to the tenant so one firm's rule id can't read another's history."""
    rule = await notif_rules.get_rule(session, uid)
    if rule is None or rule["tenant_id"] != tenant_id:
        raise HTTPException(status_code=404, detail="Notification rule not found")
    return await notif_rules.list_rule_versions(session, uid)


@router.put("/environments/{environment_uid}/secrets", name="set-environment-secrets")
async def set_environment_secrets(
    environment_id: Annotated[int, Depends(admin_environment_id)],
    body: EnvironmentSecrets,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, str | None]:
    """Set/rotate an environment's WorkOS api key + cookie password (encrypted at rest), then
    ensure its portal orgs (Client Portal, Platform) exist in WorkOS. Returns the org ids;
    never echoes the plaintext keys back.

    The two likely failures are mapped to actionable errors rather than a bare 500: an unset or
    invalid `SECRETS_MASTER_KEY` (503 — the deployment, not the operator's input) and a WorkOS
    rejection of the key/org/role writes (422 with the provider's message)."""
    exists = (
        await session.execute(
            text("SELECT 1 FROM auth.tenant_environments WHERE id = :e"), {"e": environment_id}
        )
    ).scalar_one_or_none()
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Environment not found")
    try:
        await environments.set_environment_secrets(
            session,
            tenant_environment_id=environment_id,
            workos_api_key=body.workos_api_key,
            cookie_password=body.cookie_password,
        )
    except crypto.SecretsNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "SECRETS_MASTER_KEY is not set (or is not a valid Fernet key) on the API — "
                "environment keys cannot be encrypted until it is."
            ),
        ) from exc
    # Now the environment has its key, set up its portal orgs via that environment's client, and
    # push the tenant's roles (cloned from the model at creation) so members can be invited.
    tenant_id = (
        await session.execute(
            text("SELECT tenant_id FROM auth.tenant_environments WHERE id = :e"),
            {"e": environment_id},
        )
    ).scalar_one()
    try:
        workos = await environments.env_workos_client(session, environment_id)
        # One-firm-org: the tenant's people authenticate via the single firm org; portals are
        # applications. Create/record the org, then push the tenant's roles onto it.
        firm_org = await environments.ensure_firm_org(
            session, workos, tenant_environment_id=environment_id
        )
        await environments.push_roles_to_workos(session, int(tenant_id))
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc
    return {"firm_org_id": firm_org}


@router.post("/environments/refresh", name="refresh-environment-registry")
async def refresh_environment_registry(
    _session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, int]:
    """Reload the API's issuer→environment verification registry after registering an
    environment, so the shared API picks up the new environment without a restart."""
    count = await tenant_registry.refresh()
    return {"environments": count}


# ---- tenant provider enablement + fan-out (docs/storage.md §9) ----


class SetProvider(BaseModel):
    """Enable/update a platform provider for a tenant with its slice filter, e.g.
    morgan_lloyd: {"tenant_ids": ["<Microsoft tenant GUID>", ...]}."""

    config: dict[str, object] = {}
    enabled: bool = True


@router.get("/tenants/{tenant_uid}/providers", name="list-tenant-providers")
async def list_tenant_providers(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> list[dict[str, object]]:
    """The tenant's provider enablements: filter config, last merge, and whether it can run.

    `storage_missing` is carried on the READ, not only on the write that first noticed it.
    `book_sink.tenants` INNER JOINs `storage.tenants … status = 'active'` — deliberately, since a
    firm with no bucket has nowhere to put its own lake — so a firm without one is silently
    invisible to the pipeline while this page shows it enabled and green. The daemon knows; until
    now the operator only found out if they happened to be looking when the enablement was saved.
    """
    rows = await providers_mod.list_for_tenant(session, tenant_id)
    if any(row.get("enabled") for row in rows):
        store = await tenant_storage_mod.get_tenant_store(session, tenant_id)
        if store is None:
            for row in rows:
                if row.get("enabled"):
                    row["storage_missing"] = (
                        "tenant storage is not active, so this firm registers no models on the "
                        "provider's daemon and nothing will run for it — provision it from the "
                        "storage card"
                    )
    return rows


@router.put("/tenants/{tenant_uid}/providers/{provider}", name="set-tenant-provider")
async def set_tenant_provider(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    provider: str,
    body: SetProvider,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, object]:
    """Enable/update/disable a provider for the tenant, then get its data moving immediately —
    the daily provider sync keeps the core fresh, but enabling a firm shouldn't wait a day for
    its first data.

    `pipeline` reports what the provider's daemon did with this firm's chain, and it cannot fail
    the request: the enablement is already saved by the time it runs, so a failure there is
    reported rather than raised. It used to trigger `fanout.run` as well, which is gone with the
    old path (docs/provider-pipeline.md §1).
    """
    # EITHER SPELLING, like the fifteen sibling routes. This one took the raw path param while
    # its own `/refresh` sibling normalised, so the pair disagreed about what a provider is
    # called — and `unknown_slice_ids` below reads a dict keyed by the registry key, so a slug
    # skipped the id check silently before failing at `set_provider`.
    provider = _known_provider(provider)
    # The VALUES, not just the shape. `validate_config` establishes that `node_ids` is a non-empty
    # list of non-empty strings; it cannot know whether `["12"]` names a node that exists — and a
    # mistyped id saves clean, projects an empty slice, and reads as "this firm has no data with
    # this custodian". An option list that is unavailable yields no complaint (a provider may be
    # configured before its models have ever run), so this only ever refuses what it can disprove.
    if body.enabled:
        unknown = await platforms_mod.unknown_slice_ids(provider, dict(body.config), session)
        if unknown:
            named = "; ".join(f"{key}: {', '.join(values)}" for key, values in unknown.items())
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{provider} does not offer these ids — {named}",
            )
    try:
        row = await providers_mod.set_provider(
            session, tenant_id, provider, config=dict(body.config), enabled=body.enabled
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    if body.enabled:
        row["pipeline"] = await _run_tenant_pipeline(session, tenant_id, provider)
        # The tenant's own lake is written by the chain's last layer, which needs an active storage
        # row to write into. Checked here rather than left to the run, because "no bucket" and "no
        # data" are the two states this whole re-architecture exists to keep apart.
        store = await tenant_storage_mod.get_tenant_store(session, tenant_id)
        if store is None:
            row["storage_missing"] = (
                "tenant storage is not active — provision it (tenant page → storage card) "
                "before the firm's own lake can be written"
            )
    return row


async def _run_tenant_pipeline(
    session: AsyncSession, tenant_id: int, provider: str
) -> dict[str, object]:
    """Ask the provider's daemon to run this firm's chain, and say plainly what happened.

    Shared by enablement and the Refresh action so the two cannot describe the same event
    differently. The daemon recompiles when enablement changes, so a firm just attached is
    selected. An empty `models` list still means nothing ran (`restart_required`).
    """
    paused = (
        await session.execute(
            text(
                "SELECT refresh_paused_at FROM platform.tenant_platforms "
                "WHERE tenant_id=:t AND provider=:p"
            ),
            {"t": tenant_id, "p": provider},
        )
    ).scalar_one_or_none()
    if paused is not None:
        return {
            "ok": False,
            "detail": "provider refreshes are paused for this tenant by the data reset tool",
        }
    uid = (
        await session.execute(text("SELECT uid FROM auth.tenants WHERE id = :t"), {"t": tenant_id})
    ).scalar_one_or_none()
    if not uid:
        return {"ok": False, "detail": "tenant has no uid"}
    try:
        run = await platforms_mod.trigger_tenant_refresh(provider, str(uid))
    except platforms_mod.PlatformUnavailable as exc:
        return {"ok": False, "detail": str(exc)}
    selected = run.get("models") or []
    return {
        "ok": True,
        "run": run,
        "selected": len(selected),
        # Not an error, and not something the daemon reports: a newly-enabled firm has no models
        # until the daemon reloads, so this is the difference between "ran nothing" and "is broken".
        "restart_required": not selected,
    }


@router.post("/tenants/{tenant_uid}/providers/{provider}/refresh", name="refresh-tenant-provider")
async def refresh_tenant_provider(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    provider: str,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, object]:
    """Re-run just THIS firm's models on the provider's daemon: filter, stage, merge.

    `book_sink` tags every per-tenant model `tenant:{uid}`, so one selector covers the chain. The
    daemon recompiles when that set changes, so a firm attached since startup is included. `models`
    lists what the run selected; an empty list sets `restart_required`. A tag that still matches
    nothing is the daemon's 400, returned here as 503 with that detail.
    """
    uid = (
        await session.execute(text("SELECT uid FROM auth.tenants WHERE id = :t"), {"t": tenant_id})
    ).scalar_one_or_none()
    if uid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    known = _known_provider(provider)
    result = await _run_tenant_pipeline(session, tenant_id, known)
    if not result["ok"]:
        # A dedicated action gets a status code; the enablement path deliberately does not, because
        # by then the enablement has already been saved and a 503 would misreport that as a failure.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(result["detail"])
        )
    return {
        "run": result["run"],
        "selected": result["selected"],
        "restart_required": result["restart_required"],
    }


class SetModelDsrs(BaseModel):
    """The SEI DSR teams for one model, edited from the catalogue's Models editor."""

    dsr_ids: list[str] = []


@router.put(
    "/tenants/{tenant_uid}/providers/sei/models/{model_uid}/dsrs", name="set-model-sei-dsrs"
)
async def set_model_sei_dsrs(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    model_uid: str,
    body: SetModelDsrs,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, object]:
    """Set the SEI DSR teams for one model — moved off the SEI platform card onto the model itself.

    Writes `config.model_dsrs[model_uid]` on the firm's SEI row; an empty list clears it, and the
    slice config (firm, subfirms, everything else) is left untouched.
    """
    try:
        await providers_mod.set_model_dsrs(session, tenant_id, model_uid, body.dsr_ids)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    teams = [item.strip() for item in body.dsr_ids if item.strip()]
    return {"ok": True, "model_uid": model_uid, "dsr_ids": teams}


# ---------------------------------------------------------------------------------------------
# The AI gates — the per-firm switch (docs/flows.md → "The AI gates")
# ---------------------------------------------------------------------------------------------
#
# Gated on tenants:read / tenants:write rather than a new `ai:*` operations permission, and that is
# a considered reuse rather than a shortcut: enabling a firm for AI actions is tenant configuration
# in exactly the sense branding and storage provisioning already are, it lives on the same tenant
# page, and a permission whose only holder set is "whoever already administers tenants" earns its
# keep by being the same one. The control that MATTERS here is that it is operations-side at all —
# a firm cannot switch itself on, the way a firm cannot adjudicate its own AML hit.


class SetTenantAI(BaseModel):
    """Enable/disable a firm's AI actions and set its monthly token ceiling."""

    enabled: bool
    #: Omitted leaves an existing ceiling alone and gives a new row the deployment default, so
    #: switching a firm off cannot silently reset a ceiling somebody chose.
    monthly_token_cap: int | None = Field(default=None, ge=1, le=1_000_000_000)
    notes: str | None = Field(default=None, max_length=2000)


@router.get("/tenants/{tenant_uid}/ai", name="get-tenant-ai")
async def get_tenant_ai(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> dict[str, object]:
    """The firm's AI switch, ceiling and month-to-date spend.

    ``configured`` is the DEPLOYMENT half of the two switches — surfaced next to the firm half
    because "enabled" on a deployment with no API key is a setting that does nothing, and an
    operator should be able to see that without reading the deploy config.
    """
    summary = await ai_usage.summary(session, tenant_id)
    return {
        "enabled": summary.enabled,
        "monthly_token_cap": summary.monthly_token_cap,
        "tokens_this_month": summary.tokens_this_month,
        "calls_this_month": summary.calls_this_month,
        "configured": summary.configured,
    }


@router.put("/tenants/{tenant_uid}/ai", name="set-tenant-ai")
async def set_tenant_ai(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    body: SetTenantAI,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, object]:
    """Switch a firm's AI actions on or off, and set its ceiling."""
    await ai_usage.set_state(
        session,
        tenant_id=tenant_id,
        enabled=body.enabled,
        monthly_token_cap=body.monthly_token_cap,
        notes=body.notes,
    )
    return await get_tenant_ai(tenant_id, session=session, _p=_p)


class OutboundEmailAllowlist(BaseModel):
    """Who this environment may email on dev and staging."""

    entries: list[str]
    using_default: bool
    default_entries: list[str]


class SetOutboundEmailAllowlist(BaseModel):
    """Replace the saved list, or put the environment back on ``EMAIL_ALLOWLIST``.

    ``use_default`` wins: the entries are ignored and the saved row is removed. An empty
    ``entries`` list is a saved "mail nobody", not a return to the default.
    """

    entries: list[str] = Field(default_factory=list)
    use_default: bool = False


def _allowlist_view(stored: list[str] | None) -> OutboundEmailAllowlist:
    default = email_transport.default_allowlist()
    if stored is None:
        return OutboundEmailAllowlist(entries=default, using_default=True, default_entries=default)
    return OutboundEmailAllowlist(entries=stored, using_default=False, default_entries=default)


def _require_guarded_mail_stage() -> None:
    if not email_transport.stage_is_guarded():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This environment does not restrict outbound email.",
        )


@router.get("/outbound-email", name="get-outbound-email-allowlist")
async def get_outbound_email_allowlist(
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> OutboundEmailAllowlist:
    """The addresses and domains dev or staging will actually mail.

    No saved row means the deployment default (``EMAIL_ALLOWLIST``, which is
    ``@grapheneplatforms.com`` when unset). The same list applies to every firm.
    """
    _require_guarded_mail_stage()
    return _allowlist_view(await email_transport.read_stored_allowlist(session))


@router.put("/outbound-email", name="set-outbound-email-allowlist")
async def set_outbound_email_allowlist(
    body: SetOutboundEmailAllowlist,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> OutboundEmailAllowlist:
    """Save who this environment may email, or reset to the deployment default."""
    _require_guarded_mail_stage()
    if body.use_default:
        await email_transport.clear_stored_allowlist(session)
        email_transport.clear_runtime_allowlist()
        return _allowlist_view(None)
    try:
        entries = email_transport.normalise_allowlist_entries(body.entries)
    except email_transport.AllowlistTooLong as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter at most 100 addresses or domains.",
        ) from exc
    except email_transport.AllowlistEntryError as exc:
        shown = exc.entry if len(exc.entry) <= 80 else f"{exc.entry[:80]}…"
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{shown} is not an email address or a domain.",
        ) from exc
    await email_transport.write_stored_allowlist(session, entries)
    email_transport.set_runtime_allowlist(entries)
    return _allowlist_view(entries)


@router.get("/providers/{provider}/options", name="list-provider-options")
async def list_provider_options(
    provider: str,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_read),
) -> dict[str, list[dict[str, str | None]]]:
    """Live options for a provider's enablement card and the products-page enrolment
    picker, read from the core warehouse: SEI firms/subfirms/DSR teams/enrolment templates,
    Seccl nodes. Each option carries a `parent` so the UI can cascade (pick a firm, then
    only its subfirms, DSR teams and account templates remain). Empty lists mean the
    models haven't run yet — the card then falls back to manual id entry.

    `platforms:read`, not `tenants:read`. It runs registry SQL over `platform.nodes` and
    `platform.dsr_teams` ACROSS EVERY FIRM and returns their names -- which is warehouse content,
    not tenant administration, and `tenants:read` is scoped to "view firms and their members,
    roles, configuration". The enablement card that consumes it is an operations surface whose
    holder already has `platforms:read`.
    """
    return await platforms_mod.provider_options(_known_provider(provider), session)


@router.post("/platforms/{provider}/refresh", name="refresh-platform-tenants")
async def refresh_platform_tenants(
    provider: str,
    _p: Principal = Depends(_platforms_write),
) -> dict[str, object]:
    """Re-run EVERY firm's chain on this provider's daemon: filter, stage, merge, per tenant.

    The successor to the deleted `POST /admin/fanout/run` and `POST /admin/materialise/run`. Those
    were two doors because the old path had two steps that could each succeed alone — which is
    precisely how a green fan-out left a book empty. There is one step now, so there is one door.

    Distinct from `POST /admin/platforms/{provider}/sync`, which runs the whole DAG including the
    provider pull and conform. This selects only the per-tenant models (`book_sink` tags them all
    `tenant`), which is what you want after a config change rather than after new data.
    """
    known = _known_provider(provider)
    try:
        run = await platforms_mod.trigger_tenant_refresh(known)
    except platforms_mod.PlatformUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    selected = run.get("models") or []
    return {
        "run": run,
        "selected": len(selected),
        # An empty selection is not an error and the daemon will not flag it: no firm has models
        # until the daemon has loaded a project that knew about it (`Project.load` runs once, in
        # `serve`'s lifespan), and an unmatched tag selects the empty set while answering 200.
        "restart_required": not selected,
    }


# ---- platform integrations cockpit (the admin /platforms pages) ----


def _known_provider(provider: str) -> str:
    """The provider a path param names, or a 404.

    ONE SPELLING NOW (0131), so this no longer converts anything — it validates. It used to
    accept the key or its URL slug, because the admin app passes `params.slug` straight through
    and deliberately holds no map of its own; four of nineteen routes forgot to call it and the one
    provider the bridge existed for (`morgan_lloyd` vs `morgan-lloyd`) was the one that broke. The
    bridge is gone rather than mended: `PROVIDERS` is the URL form.

    It stays because the guard is still needed — an unrecognised name must be a 404 rather than an
    empty answer — a value bound into SQL for an unknown provider returns `200 []`, which reads
    as "nothing configured" rather than as a mistake.
    """
    if provider in providers_mod.PROVIDERS:
        return provider
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown provider")


@router.get("/platforms", name="list-platform-status")
async def list_platform_status(
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_read),
) -> list[dict[str, object]]:
    """Index-card data: per provider, pipeline-service health + enabled-firm count."""
    return await platforms_mod.index(session)


@router.get("/platforms/api-calls", name="list-api-calls")
async def list_api_calls(
    provider: Annotated[list[str] | None, Query()] = None,
    ok: bool | None = None,
    tenant_id: int | None = None,
    operation: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = listing.DEFAULT_LIMIT,
    offset: int = 0,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_read),
) -> dict[str, object]:
    """Outgoing calls to the custodians, newest first.

    `source` narrows to one half of the platform: 'app' for the request path, 'sync' for the
    provider daemons. An unknown value is a 422 rather than an empty page, because "no rows"
    reads as "the sync made no calls" and that is the wrong thing to believe in an incident.

    Declared BEFORE /platforms/{provider}/overview: FastAPI matches in declaration order, so a
    literal path that could also read as a `{provider}` value has to come first or it never
    matches.
    """
    try:
        return await platforms_mod.api_calls(
            session,
            provider=provider,
            ok=ok,
            tenant_id=tenant_id,
            operation=operation,
            source=source,
            q=q,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.get("/platforms/api-calls/summary", name="api-call-summary")
async def api_call_summary(
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_read),
) -> list[dict[str, object]]:
    """Per provider: 24-hour volume, failures and p95 latency."""
    return await platforms_mod.api_call_summary(session)


# Declared AFTER `/platforms/api-calls/summary`, and that ordering is load-bearing now: the
# param was an `int`, which made "summary" a 422 whichever order they sat in, and it is a
# uid string — so a dynamic route declared first would swallow the summary path. FastAPI
# matches in declaration order (the same rule the `by-uid` resolves are declared under).
@router.get("/platforms/api-calls/{call_uid}", name="get-api-call")
async def get_api_call(
    call_uid: str,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_read),
) -> dict[str, object]:
    """One call, with the request and response bodies the list projection omits."""
    call = await platforms_mod.api_call(session, call_uid)
    if call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such API call")
    return call


async def _optional_tenant_id(session: AsyncSession, uid: str | None) -> int | None:
    """Resolve a `?tenant=<uid>` filter, or None when absent.

    404 on an unknown uid rather than silently returning every firm's rows: a mistyped uid asking
    "what did this firm's merge do" must not be answered with what EVERY firm's merge did.
    """
    if not uid:
        return None
    tenant_id = (
        await session.execute(text("SELECT id FROM auth.tenants WHERE uid = :uid"), {"uid": uid})
    ).scalar_one_or_none()
    if tenant_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    return int(tenant_id)


async def _firm_slice(session: AsyncSession, provider: str, uid: str) -> tuple[int, list[str]]:
    """Resolve a firm's enablement of this provider: its tenant id and its slice keys.

    404 rather than an empty console when the firm is not enabled: "this firm holds nothing at
    this custodian" and "this firm was never connected to this custodian" are different answers,
    and only one of them is worth investigating.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT tp.tenant_id, tp.config, tp.enabled "
                    "  FROM platform.tenant_platforms tp "
                    "  JOIN auth.tenants t ON t.id = tp.tenant_id "
                    " WHERE tp.provider = :p AND t.uid = :uid"
                ),
                {"p": provider, "uid": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"this firm is not enabled on {provider}",
        )
    # `enabled` was SELECTed and then ignored, so a firm switched OFF still served its rows --
    # and the switch is how an operator disconnects a firm from a custodian. Distinguished from
    # "never connected" (404 above), because turning it back on is a different act from
    # configuring it for the first time.
    if not row["enabled"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"this firm's {provider} connection is disabled",
        )
    config = row["config"] or {}
    keys: list[str] = []
    for key in providers_mod.slice_config_keys(provider):
        value = config.get(key)
        if isinstance(value, list | tuple):
            keys.extend(str(v) for v in value)
        elif value is not None:
            keys.append(str(value))
    return int(row["tenant_id"]), keys


async def _firm_platform_config(
    session: AsyncSession, provider: str, tenant_id: int
) -> dict[str, object]:
    """This firm's enablement config, for the field defaults a drawer prefills from.

    Read separately from `_firm_slice` rather than returned by it: that function's job is the
    404/409 guards and the slice KEYS, and widening its return type would touch every caller for a
    value only this path wants.
    """
    config = (
        await session.execute(
            text(
                "SELECT config FROM platform.tenant_platforms "
                " WHERE provider = :p AND tenant_id = :t"
            ),
            {"p": provider, "t": tenant_id},
        )
    ).scalar_one_or_none()
    return dict(config) if isinstance(config, dict) else {}


async def _firm_account_id(session: AsyncSession, tenant_id: int, uid: str) -> int | None:
    """One of THIS firm's accounts, by public uid. None when it is not the firm's.

    `admin_session` is service_role, so the tenant conjunct is the only thing scoping this — the
    same discipline `app/contacts.py` applies to every statement it runs. Without it an operator
    could prefill a drawer with another firm's account number and send it to this firm's custodian.
    """
    return (
        await session.execute(
            text("SELECT id FROM platform.accounts WHERE uid = :uid AND tenant_id = :t"),
            {"uid": uid, "t": tenant_id},
        )
    ).scalar_one_or_none()


@router.get("/platforms/{provider}/firms/{uid}/entities", name="platform-firm-entities")
async def platform_firm_entities(
    provider: str,
    uid: str,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_read),
) -> dict[str, object]:
    """Every entity this platform publishes for this firm, with availability and row counts.

    Declared BEFORE `/platforms/{provider}/overview`? No -- it does not need to be: `firms` is a
    path SEGMENT here, not a whole path that could read as a provider name. The api-calls routes
    above are the ones with that problem.
    """
    known = _known_provider(provider)
    tenant_id, slice_ids = await _firm_slice(session, known, uid)
    try:
        described = await platform_entities.describe(
            known, slice_ids, session=session, tenant_id=tenant_id, uid=uid
        )
    except platforms_mod.PlatformUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return {
        "provider": known,
        "uid": uid,
        "slice_ids": slice_ids,
        "entities": described,
        # So the console's nav can hide an Actions page that would be empty, without keeping its
        # own list of which providers can be written to.
        "actions": len(platform_actions_mod.actions_for(known)),
    }


@router.get("/platforms/{provider}/firms/{uid}/entities/{entity}", name="platform-firm-entity-rows")
async def platform_firm_entity_rows(
    provider: str,
    uid: str,
    entity: str,
    q: str = "",
    sort: str | None = None,
    direction: str = "desc",
    limit: int = listing.DEFAULT_LIMIT,
    offset: int = 0,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_read),
) -> dict[str, object]:
    """One entity's rows for this firm: searched, sorted and paged.

    `sort` is validated against the entity's own column list inside `platform_entities`, so an
    unknown value falls back to the default order rather than reaching the SQL.
    """
    known = _known_provider(provider)
    defined = platform_entities.entity_for(known, entity)
    if defined is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown entity '{entity}'"
        )
    if defined.unavailable:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=defined.unavailable)
    tenant_id, slice_ids = await _firm_slice(session, known, uid)
    # A shared relation this firm has no key for. 409, not 503 and not an unnarrowed table: the
    # data is there and reachable, it is this firm's ENABLEMENT that cannot say which rows are
    # theirs -- the same conflict-with-configuration `defined.unavailable` reports above.
    if not platform_entities.is_narrowable(defined, slice_ids):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{defined.relation} holds every firm's records and this firm has no "
                f"{defined.slice_column} configured, so it cannot be narrowed to them."
            ),
        )
    try:
        return await platform_entities.rows(
            known,
            defined,
            slice_ids=slice_ids,
            q=q.strip(),
            sort=sort,
            descending=direction != "asc",
            limit=limit,
            offset=offset,
            session=session,
            tenant_id=tenant_id,
            uid=uid,
        )
    except platforms_mod.PlatformUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get("/platforms/{provider}/firms/{uid}/lookups/{field}", name="platform-firm-field-lookup")
async def platform_firm_field_lookup(
    provider: str,
    uid: str,
    field: str,
    q: str = "",
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_read),
) -> dict[str, object]:
    """Pickable records for one identifier field on an action form.

    `field` names a VENDOR FIELD (`contactId`, `schemeReference`), not a relation — the registry
    resolves it, so no caller value reaches a query as an identifier. An unmapped field is a 404
    rather than an empty list: the drawer then keeps its free-text box, where an empty list would
    say "this firm has none of these" and be wrong.
    """
    known = _known_provider(provider)
    entity_key = platform_entities.lookup_fields(known).get(field)
    if entity_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"'{field}' is not a lookup field"
        )
    defined = platform_entities.lookup_entity(known, entity_key)
    if defined is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"'{entity_key}' cannot be picked from"
        )
    _tenant_id, slice_ids = await _firm_slice(session, known, uid)
    try:
        items = await platform_entities.options(
            known, defined, slice_ids=slice_ids, q=q.strip(), uid=uid
        )
    except platforms_mod.PlatformUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return {"field": field, "entity": entity_key, "label": defined.label, "items": items}


@router.get("/platforms/{provider}/actions", name="platform-actions")
async def platform_actions(
    provider: str,
    firm: str = "",
    account: str = "",
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_read),
) -> list[dict[str, object]]:
    """The write actions this platform offers, each with the vendor's documented field contract.

    Readable with `platforms:read` because knowing WHAT can be done — and what the vendor
    requires for it — is not the same as doing it. Performing one needs `platforms:write`.

    `firm` is a tenant uid, and supplying it resolves the prefilled `defaults` from THAT firm's
    connection (`platform.providers.config`) rather than from deployment env. Optional so the
    contract itself stays firm-independent; the drawer always passes it. A firm enabled on more
    than one source is a 409 naming them, because "the vendor's values for this firm" then has no
    answer and picking one would send another firm's adviser-firm GUID with this firm's client.

    `account` is an account uid, and supplying it prefills what that ACCOUNT answers about itself —
    the account number, its principal `portfolioId` and the bank details a withdrawal is paid to.
    Two of those had no source in the estate before 0119/0120, which is why SEI's
    `create_cash_recipient` — the one drawer in the estate that can reach a sort code — was free
    text typed by hand. Optional, and an account that is not this firm's is IGNORED rather than
    refused: the contract is still worth returning, and prefilling it from another firm's account
    would be the actual harm.

    Precedence, least specific first: the firm's enablement config, then its connection, then the
    account.
    """
    known = _known_provider(provider)
    source_config: dict[str, object] = {}
    slice_config: dict[str, object] = {}
    account_values: dict[str, str] = {}
    if firm:
        tenant_id, _slice_ids = await _firm_slice(session, known, firm)
        slice_config = await _firm_platform_config(session, known, tenant_id)
        # No `AmbiguousSource` to catch since 0133: the reference values live on the firm's own
        # enablement row, so "the vendor's values for this firm" has exactly one answer or none.
        # From the config already in hand: since 0133 the vendor values live on the same
        # `tenant_platforms` row `_firm_platform_config` just returned, so re-reading it was a
        # second round trip for a strict subset of what we had.
        source_config = providers_mod.vendor_values(known, slice_config)
        if account:
            account_id = await _firm_account_id(session, tenant_id, account)
            if account_id is not None:
                account_values = await platform_actions_mod.account_defaults(
                    session, known, account_id
                )
    return platform_actions_mod.describe(
        known, source_config, slice_config=slice_config, account=account_values
    )


class ActionRequest(BaseModel):
    """A vendor payload, as the drawer assembled it. Validated against the documented contract
    before anything is sent."""

    payload: dict[str, object] = Field(default_factory=dict)
    entity_id: str | None = None
    """The vendor record a PATCH targets — required for the entity-PATCH actions, ignored by
    the rest."""


@router.post("/platforms/{provider}/firms/{uid}/actions/{key}", name="run-platform-action")
async def run_platform_action(
    provider: str,
    uid: str,
    key: str,
    body: ActionRequest,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(_platforms_write),
) -> dict[str, object]:
    """Perform one action against the custodian, on this firm's behalf.

    Recorded in `audit.api_log` as `source='app'` with the acting operator and the target firm,
    which is the whole reason the recorder takes a tenant and a user rather than reading them
    from a context variable: the tenant here is the one being ACTED ON, not the operator's own.
    """
    known = _known_provider(provider)
    defined = platform_actions_mod.action_for(known, key)
    if defined is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown action '{key}'")
    tenant_id, _slice_ids = await _firm_slice(session, known, uid)

    try:
        payload = platform_actions_mod.validate(known, defined, dict(body.payload))
    except platform_actions_mod.ActionRefused as exc:
        # 422, not 400: the request was understood and refused on its content, and the message
        # names the field. The alternative is the vendor's own 0x80040224 null-reference, which
        # says nothing about which field was wrong.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    # `audit.api_log.user_id` is a uuid FK to auth.users; the principal carries the WorkOS id
    # (`user_01H…`), which is not one. Resolved rather than coerced, and NULL where the operator
    # has no mirror row — evidence-writing must not fail because a lookup missed.
    actor = (
        await session.execute(
            text("SELECT id FROM auth.users WHERE workos_user_id = :w"),
            {"w": principal.user_id},
        )
    ).scalar_one_or_none()

    try:
        return await platform_actions_mod.run(
            known,
            defined,
            payload,
            entity_id=body.entity_id,
            tenant_id=tenant_id,
            user_id=str(actor) if actor else None,
        )
    except platform_actions_mod.ActionRefused as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except platforms_mod.PlatformUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get("/platforms/{provider}/overview", name="platform-overview")
async def platform_overview(
    provider: str,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_read),
) -> dict[str, object]:
    """What the platform overview paints: the daemon's model list, plan, runs and events,
    headline warehouse figures, and per-firm enablements. Partial when the daemon or lake
    is down (`service.status`). Per-model SQL and row counts are `/catalogue`."""
    return await platforms_mod.overview(session, _known_provider(provider))


@router.get("/platforms/{provider}/catalogue", name="platform-catalogue")
async def platform_catalogue(
    provider: str,
    _p: Principal = Depends(_platforms_read),
) -> dict[str, object]:
    """Column lineage, SQL and per-model row counts for Models and Lineage.

    Not part of the overview: that page paints before this scan finishes, and the browser
    asks for it once the platform is on screen.
    """
    return await platforms_mod.catalogue(_known_provider(provider))


@router.get("/platforms/{provider}/rejects", name="platform-rejects")
async def platform_rejects(
    provider: str,
    tenant: str | None = None,
    limit: int = 200,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_read),
) -> dict[str, object]:
    """What the last merges could not write, pooled by reason.

    The answer to "did last night's sync land?" that a row count cannot give: a firm with no rows
    and a firm whose rows were all refused look identical from every other surface. `?tenant=<uid>`
    narrows to one firm, which is how the tenant page asks the same question.
    """
    known = _known_provider(provider)
    tenant_id = await _optional_tenant_id(session, tenant)
    return await platforms_mod.merge_rejects(
        session, known, tenant_id=tenant_id, limit=max(1, min(limit, 1000))
    )


@router.get("/platforms/{provider}/ingestion", name="platform-ingestion")
async def platform_ingestion(
    provider: str,
    _session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_read),
) -> dict[str, object]:
    """How this custodian's data arrives, and whether last night's delivery did.

    Only SEI has a per-file ledger, and that is a fact about its transport rather than a gap: its
    nightly PGP-encrypted SFTP drop is the head of its own DAG. The other two are told apart by
    saying so, not by rendering an empty table that reads as "nothing arrived".
    """
    return await platforms_mod.ingestion(_known_provider(provider))


@router.get("/platforms/{provider}/ledger", name="platform-merge-runs")
async def platform_merge_runs(
    provider: str,
    tenant: str | None = None,
    limit: int = 25,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_read),
) -> dict[str, object]:
    """What the merge DID, run by run — the other half of the answer `/rejects` starts.

    Rejects say what was refused; this says whether the merge ran at all, for which firms, and how
    much it wrote. `book_sink` has always built this ledger and published it only as
    `book_{uid}.merge_ledger` — a warehouse relation holding one run, reachable only from the SQL
    console — so the durable record docs/provider-pipeline.md §5 argues for had no reader.

    `?tenant=<uid>` narrows to one firm, exactly as `/rejects` does.
    """
    known = _known_provider(provider)
    tenant_id = await _optional_tenant_id(session, tenant)
    return await platforms_mod.merge_runs(
        session, known, tenant_id=tenant_id, limit=max(1, min(limit, 200))
    )


class PlatformQuery(BaseModel):
    """One read-only SELECT for the platform Query console."""

    sql: str = Field(min_length=1)
    limit: int = Field(default=200, ge=1, le=2000)


@router.post("/platforms/{provider}/query", name="platform-query")
async def platform_query(
    provider: str,
    body: PlatformQuery,
    _session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_query),
) -> dict[str, object]:
    """Run a console query against the provider's core catalogue (lake_reader, READ_ONLY
    attach; single SELECT/WITH statements only). Engine errors come back as 400 with the
    engine's message — that's the console's error surface.

    Gated ``platforms:query`` (split out of ``platforms:read``): the cockpit's curated views show
    aggregates, but this runs arbitrary SQL over every firm's raw book at once."""
    known = _known_provider(provider)
    try:
        return await platforms_mod.query(known, body.sql, body.limit)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except platforms_mod.PlatformUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001 — DuckDB binder/catalog errors -> console text
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/platforms/{provider}/sync", name="platform-sync")
async def platform_sync(
    provider: str,
    _session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_write),
) -> dict[str, object]:
    """Enqueue a full pipeline run on the provider's daemon (its scheduler dedupes)."""
    known = _known_provider(provider)
    try:
        return await platforms_mod.trigger_sync(known)
    except platforms_mod.PlatformUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post("/platforms/{provider}/rebuild", name="platform-rebuild")
async def platform_rebuild(
    provider: str,
    _session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_platforms_write),
) -> dict[str, object]:
    """Reset a provider's pipeline and re-run every model.

    The recovery when the state db and the catalogue disagree — the daemon reading a model it
    believes it built, `Table with name … does not exist`. `POST /sync` cannot fix that: it runs
    change detection against a state db that still claims the table is current, so it skips the
    very models that are missing. This calls the daemon's `POST /reset` (confirm) and then
    enqueues a full run.

    Expensive by construction (it re-reads everything), so it is deliberately its own endpoint
    rather than a flag on sync: an operator should have to mean it.
    """
    known = _known_provider(provider)
    try:
        return await platforms_mod.rebuild(known)
    except platforms_mod.PlatformUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post("/platforms/{provider}/runs/{run_id}/cancel", name="cancel-platform-run")
async def cancel_platform_run(
    provider: str,
    run_id: str,
    _p: Principal = Depends(_platforms_write),
) -> dict[str, object]:
    """Cancel one queued or running pipeline run. A finished run is the daemon's 404.

    The id is the daemon's queue id, carried as text. An int path parameter is a
    portals row serial, and this is not one of those.
    """
    known = _known_provider(provider)
    if not run_id.isdecimal():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    try:
        return await platforms_mod.cancel_run(known, int(run_id))
    except platforms_mod.PlatformUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get(
    "/platforms/{provider}/events/stream",
    name="platform-events-stream",
    include_in_schema=False,
)
async def platform_events_stream(
    provider: str,
    after: int = 0,
    _p: Principal = Depends(_platforms_read),
) -> StreamingResponse:
    """Live daemon events for the platform pages.

    No database session: the connection stays open for as long as the page does, and holding
    one would pin a pool slot per reader. The daemon's own `/events/stream` is the source;
    this only copies the frames. `platforms:read` is the same gate as the overview.
    """
    from app.live_push import SSE_HEADERS

    known = _known_provider(provider)

    async def frames() -> AsyncIterator[bytes]:
        try:
            async for chunk in platforms_mod.iter_event_stream(known, after):
                yield chunk
        except platforms_mod.PlatformUnavailable:
            return

    return StreamingResponse(frames(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---- tenant object storage (bucket-per-tenant, docs/storage.md) ----


@router.get("/tenants/{tenant_uid}/storage", name="get-tenant-storage")
async def get_tenant_storage(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> dict[str, object] | None:
    """A tenant's object-storage registry row (non-secret). None if never provisioned —
    e.g. the tenant predates bucket-per-tenant, or the instance has no S3 configured."""
    return await tenant_storage_mod.get_storage_row(session, tenant_id)


@router.post("/tenants/{tenant_uid}/storage/provision", name="provision-tenant-storage")
async def provision_tenant_storage(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, object]:
    """Provision (or repair) the tenant's bucket. Idempotent — this is both the retry for
    a failed create-time provision and the backfill for tenants that predate it."""
    exists = (
        await session.execute(text("SELECT 1 FROM auth.tenants WHERE id = :t"), {"t": tenant_id})
    ).scalar_one_or_none()
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return await tenant_storage_mod.provision_tenant_storage(session, tenant_id)


@router.post("/storage/provision-all", name="provision-all-tenant-storage")
async def provision_all_tenant_storage(
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, object]:
    """Backfill: provision storage for every real tenant without an active registry row."""
    tenant_ids = (
        (
            await session.execute(
                text(
                    "SELECT t.id FROM auth.tenants t "
                    "LEFT JOIN storage.tenants ts ON ts.tenant_id = t.id "
                    "WHERE NOT t.is_model AND (ts.tenant_id IS NULL OR ts.status = 'provisioning') "
                    "ORDER BY t.id"
                )
            )
        )
        .scalars()
        .all()
    )
    results = [
        await tenant_storage_mod.provision_tenant_storage(session, int(tid)) for tid in tenant_ids
    ]
    return {
        "provisioned": sum(1 for r in results if r["status"] == "active"),
        "pending": sum(1 for r in results if r["status"] != "active"),
        "results": results,
    }


class StorageObjectOut(BaseModel):
    key: str
    size: int
    last_modified: str


class FolderOut(BaseModel):
    """A sub-"folder" with its recursive rollup (``storage.prefix_stats``): total size, object
    count, latest-modified, and the file extensions found within (so the browser's type filter can
    offer real types and match folders that contain them). ``truncated`` ⇒ the folder was larger
    than the scan cap, so size/count are a lower bound."""

    prefix: str
    size: int
    count: int
    last_modified: str | None = None
    exts: list[str] = []
    truncated: bool = False
    # Human label when the folder is a document/attachment bucket (leaf ``doc_…``/``att_…`` resolved
    # to its ``documents.documents``/``documents.attachments`` name); None ⇒ show the raw name.
    label: str | None = None


class ObjectPageOut(BaseModel):
    bucket: str
    prefix: str
    # False while browsing the shared documents bucket (today's interim — it holds every tenant's
    # keys); True once the tenant has its own provisioned bucket and this lists that instead.
    per_tenant: bool
    folders: list[FolderOut]
    objects: list[StorageObjectOut]
    # Distinct file extensions under this prefix (rolled up from the folders + the direct files) —
    # the browser's type-filter options.
    object_types: list[str] = []
    # Resolved names for the segments of the CURRENT prefix (raw segment → name) so the breadcrumb
    # can show e.g. the tenant name instead of its id. Only resolved segments appear.
    prefix_labels: dict[str, str] = {}
    next_token: str | None = None


def _storage_leaf(prefix: str) -> str:
    """The last path segment of a folder prefix ('documents/2/doc_x/' -> 'doc_x')."""
    return prefix.rstrip("/").rsplit("/", 1)[-1]


async def _resolve_storage_labels(session: AsyncSession, leaves: list[str]) -> dict[str, str]:
    """Map storage folder segments to human names: ``doc_…``/``att_…`` → the document/attachment
    title, a numeric ``{id}`` → the tenant's name. Cross-tenant (admin_session bypasses RLS); uids
    and ids are unique so no scoping is needed. Segments with no match are omitted — the caller
    falls back to the raw name (e.g. orphaned renders with no document row)."""
    labels: dict[str, str] = {}
    doc_uids = [lf for lf in leaves if lf.startswith(("doc_", "att_"))]
    if doc_uids:
        rows = (
            await session.execute(
                text(
                    "SELECT uid, name FROM documents.documents WHERE uid = ANY(:u) "
                    "UNION ALL SELECT uid, name FROM documents.attachments WHERE uid = ANY(:u)"
                ),
                {"u": doc_uids},
            )
        ).all()
        labels.update({r.uid: r.name for r in rows if r.name})
    tenant_ids = [int(lf) for lf in leaves if lf.isdigit()]
    if tenant_ids:
        trows = (
            await session.execute(
                text("SELECT id, name FROM auth.tenants WHERE id = ANY(:t)"),
                {"t": tenant_ids},
            )
        ).all()
        labels.update({str(r.id): r.name for r in trows if r.name})
    return labels


async def _browse_bucket(session: AsyncSession, tenant_id: int) -> str:
    """The bucket the ops object browser acts on for this tenant: its own once the registry says
    the storage is active, else the shared legacy documents bucket.

    Shared by the listing and the presign so they cannot disagree — a URL signed against the
    shared bucket for a key listed from the tenant's own would 404, and a 404 reads as a missing
    document rather than as the wrong bucket."""
    row = await tenant_storage_mod.get_storage_row(session, tenant_id)
    if row and row.get("status") == "active" and row.get("bucket"):
        return str(row["bucket"])
    shared = storage.documents_bucket()
    if not shared:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage is not configured (S3_DOCUMENTS_BUCKET).",
        )
    return shared


@router.get("/tenants/{tenant_uid}/storage/objects", name="list-tenant-storage-objects")
async def list_tenant_storage_objects(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    prefix: str = "",
    token: str | None = None,
    session: AsyncSession = Depends(admin_session),
    # `book:read`, not `tenants:read`: this browses (and object-url presigns) a firm's CLIENT
    # files, not tenant configuration. `tenants:read` means "may administer firms"; `book:read`
    # is the operations-only "may see a firm's client data" grant, so client documents stay
    # behind the same lever as every other book surface.
    _p: Principal = Depends(_book_read),
) -> ObjectPageOut:
    """Browse the object-storage bucket for a tenant. Until per-tenant buckets are provisioned this
    lists the SHARED documents bucket (every tenant's keys — an interim ops view); once the tenant
    has an active bucket in the storage registry, it lists that bucket instead, same shape. The
    datalake is never surfaced (enforced in ``storage.list_objects``)."""
    bucket = await _browse_bucket(session, tenant_id)
    per_tenant = bucket != storage.documents_bucket()
    try:
        page = await storage.list_objects(bucket, prefix, token=token, limit=200)
    except storage.BucketMissing:
        # The registry marks the tenant's own bucket active but it was never minted in the store, so
        # fall back to the shared documents bucket where the objects actually live (the interim ops
        # view). Re-provision to mint the tenant's own bucket.
        shared = storage.documents_bucket()
        if not (per_tenant and shared and shared != bucket):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Object storage bucket not found: {bucket}.",
            ) from None
        per_tenant, bucket = False, shared
        try:
            page = await storage.list_objects(bucket, prefix, token=token, limit=200)
        except storage.BucketMissing:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Object storage bucket not found: {bucket}.",
            ) from None
    # Roll each folder up recursively (size/latest-modified/types) so the browser shows real totals
    # rather than blanks, and collect the union of file types for the type filter.
    folders: list[FolderOut] = []
    exts: set[str] = set()
    for f in page.folders:
        st = await storage.prefix_stats(bucket, f)
        folders.append(
            FolderOut(
                prefix=f,
                size=st.size,
                count=st.count,
                last_modified=st.last_modified,
                exts=list(st.exts),
                truncated=st.truncated,
            )
        )
        exts.update(st.exts)
    for o in page.objects:
        e = storage.ext_of(o.key)
        if e:
            exts.add(e)

    # Friendly labels for the current folders AND the breadcrumb: doc/attachment buckets resolve to
    # their title, a numeric per-tenant bucket to the tenant name. One lookup covers both. Unmatched
    # segments keep their raw name (e.g. orphaned renders with no document row).
    segments = [s for s in prefix.split("/") if s]
    labels = await _resolve_storage_labels(
        session, [_storage_leaf(fo.prefix) for fo in folders] + segments
    )
    for fo in folders:
        fo.label = labels.get(_storage_leaf(fo.prefix))
    prefix_labels = {s: labels[s] for s in segments if s in labels}
    return ObjectPageOut(
        bucket=bucket,
        prefix=prefix,
        per_tenant=per_tenant,
        folders=folders,
        objects=[
            StorageObjectOut(key=o.key, size=o.size, last_modified=o.last_modified)
            for o in page.objects
        ],
        object_types=sorted(exts),
        prefix_labels=prefix_labels,
        next_token=page.next_token,
    )


class ObjectUrlOut(BaseModel):
    url: str


@router.get("/tenants/{tenant_uid}/storage/object-url", name="tenant-storage-object-url")
async def tenant_storage_object_url(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    key: str,
    download: bool = False,
    session: AsyncSession = Depends(admin_session),
    # Same gate as the object browser above: a presigned URL to a client document is book
    # (client-data) access, not tenant administration.
    _p: Principal = Depends(_book_read),
) -> ObjectUrlOut:
    """A short-lived presigned URL to view (``inline``) or download one object — the browser's
    per-row action. Refuses datalake/archive keys (never browsable, never fetchable).

    Presigns the SAME bucket the browser listed, resolved the same way: a URL signed against the
    shared bucket for a key the browser found in the tenant's own would 404, which reads as a
    missing document rather than as the wrong bucket."""
    if storage.is_hidden(key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not browsable.")
    bucket = await _browse_bucket(session, tenant_id)
    disposition = "attachment" if download else "inline"
    url = await storage.presigned_get_document(
        key, filename=key.rsplit("/", 1)[-1], disposition=disposition, bucket=bucket
    )
    return ObjectUrlOut(url=url)


@router.put("/tenants/{tenant_uid}/roles/{slug}/permissions", name="set-tenant-role-permissions")
async def set_tenant_role_permissions(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    slug: str,
    body: SetPermissions,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Set a tenant org role's permissions (the per-firm 'see all vs own book' lever)."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        return await rbac.set_org_role_permissions(
            session, tenant_id=tenant_id, role_slug=slug, permission_slugs=body.permissions
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.get("/users", name="list-users")
async def list_users_directory(
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
    q: str | None = None,
    enabled: bool | None = None,
    limit: int = listing.DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, object]:
    """The cross-tenant user directory: one page of logins with the firm each belongs to.

    Paged, searched and filtered **server-side** (`limit` clamped to 200) — the estate's logins
    grow with every client, so the whole table can't be shipped to the browser to be filtered
    there. That is also why `enabled` is a parameter rather than something the portal does to the
    rows it holds: narrowing one page of 25 to the disabled logins ON IT would answer "three
    disabled accounts" when the true answer is however many the estate has. Carries the firm
    name/alias, because per-tenant email uniqueness means the same address legitimately appears
    once per firm and a bare list can't tell them apart. Soft-deleted logins are excluded.
    (The query lives here rather than in `directory.list_users` — which has no paging — alongside
    the other paged admin lists.)"""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    # `escape_like` first: without it a search for "100%" matches every login in the estate, which
    # reads as a broken directory rather than as a filter. The statements name ESCAPE to match.
    pattern = f"%{listing.escape_like(q.strip())}%" if q and q.strip() else None
    params = {"q": pattern, "enabled": enabled, "limit": limit, "offset": offset}
    # The same predicate in both queries (spelled out rather than concatenated in, so the SQL
    # stays a literal): the search matches the person's email/name or their firm's name, and
    # `enabled` narrows to active or disabled logins when it is given at all.
    total = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM auth.users u "
                    "LEFT JOIN auth.tenants t ON t.id = u.tenant_id "
                    "WHERE u.deleted_at IS NULL "
                    "  AND (CAST(:q AS text) IS NULL OR u.email ILIKE :q ESCAPE '\\' "
                    "       OR u.name ILIKE :q ESCAPE '\\' OR t.name ILIKE :q ESCAPE '\\') "
                    "  AND (CAST(:enabled AS boolean) IS NULL "
                    "       OR u.enabled = CAST(:enabled AS boolean))"
                ),
                params,
            )
        ).scalar_one()
    )
    rows = (
        (
            await session.execute(
                text(
                    "SELECT u.id AS user_id, u.uid AS user_uid, u.email, u.name, u.enabled, "
                    "       u.workos_user_id, "
                    # `tenant_uid` because the admin portal's firm links are uid-addressed.
                    "       u.tenant_id, t.uid AS tenant_uid, t.name AS tenant_name, "
                    "       t.alias AS tenant_alias, "
                    "       count(DISTINCT m.tenant_id) AS memberships "
                    "FROM auth.users u "
                    "LEFT JOIN auth.tenants t ON t.id = u.tenant_id "
                    "LEFT JOIN auth.organisation_memberships m ON m.user_id = u.id "
                    "WHERE u.deleted_at IS NULL "
                    "  AND (CAST(:q AS text) IS NULL OR u.email ILIKE :q ESCAPE '\\' "
                    "       OR u.name ILIKE :q ESCAPE '\\' OR t.name ILIKE :q ESCAPE '\\') "
                    "  AND (CAST(:enabled AS boolean) IS NULL "
                    "       OR u.enabled = CAST(:enabled AS boolean)) "
                    "GROUP BY u.id, u.uid, u.email, u.name, u.enabled, u.workos_user_id, "
                    "   u.tenant_id, t.uid, t.name, t.alias "
                    "ORDER BY u.email, t.name LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return {"users": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


# Declared BEFORE `/users/{user_id}`, whose param is typed `UUID`: FastAPI matches in declaration
# order, so a later declaration would answer "by-uid" with a 422. (The ordering rule the tenant
# section already relies on for `/admin/tenants/by-uid/{uid}`.)
@router.get("/users/by-uid/{uid}", name="get-user-by-uid")
async def get_user_by_uid(
    uid: str,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> dict[str, object]:
    """The same login as ``GET /admin/users/{user_id}``, addressed by its uid.

    `auth.users.id` is a uuid, which leaks no row ordering -- but it is still the database's own
    key, and `auth.users.uid` exists precisely as the public handle. The portal addresses a login
    by the uid; the member endpoints below stay typed on the uuid this answer carries.
    """
    row = (
        (
            await session.execute(
                text("SELECT id FROM auth.users WHERE uid = :uid AND deleted_at IS NULL"),
                {"uid": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return await get_user(row["id"], session, _p)


@router.get("/users/{user_id}", name="get-user")
async def get_user(
    user_id: MemberId,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> dict[str, object]:
    """A user + their memberships across firms."""
    result = await directory.user_with_memberships(session, str(user_id))
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return result


@router.post("/tenants", name="create-tenant")
async def create_tenant(
    body: CreateTenant,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, object]:
    """Create a firm (the tenant row). Project-per-tenant: no WorkOS org is created — the
    tenant's WorkOS project is made in the dashboard and registered via the environments card."""
    try:
        return await provisioning.provision_tenant(
            session,
            name=body.name,
            alias=body.alias,
            workos_project_id=body.workos_project_id,
            from_model=body.from_model,
        )
    except ValueError as exc:  # alias already taken
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/tenants", name="list-tenants")
async def list_tenants(
    q: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> list[dict[str, object]] | dict[str, object]:
    """List all firms (cross-tenant) with onboarding status: user count and whether the
    tenant has a registered environment, its keys set, and its portal orgs created. Drives
    the dashboard's "needs setup" view and the tenants list status column.

    `q` searches name and alias IN SQL. The page used to fetch every firm and filter them in the
    browser, which searches the batch you were sent and reports it as the answer for the estate.

    `limit` chooses the response shape, the convention `/crm/accounts` sets: absent ⇒ the bare list
    every existing caller reads (the dashboard's setup view, the tenant pickers), given ⇒
    `{items, total}`.
    """
    where = "WHERE NOT t.is_model"
    params: dict[str, object] = {}
    if q and q.strip():
        # The firm search bar's match over `_search` (uid, name, alias, website).
        await session.execute(text(listing.FUZZY_THRESHOLD_SQL))
        where += " AND " + listing.search_sql("t._search")
        params.update(listing.search_params(q))
    rows = (
        (
            await session.execute(
                text(
                    # `where` is fixed text plus a bound parameter — no caller string reaches SQL.
                    "SELECT t.id, t.uid, t.alias, t.name, t.website, t.enabled, "  # noqa: S608
                    "  t.workos_project_id, "
                    "  (SELECT count(*) FROM auth.users u "
                    "     WHERE u.tenant_id = t.id AND u.deleted_at IS NULL) AS user_count, "
                    "  EXISTS (SELECT 1 FROM auth.tenant_environments te "
                    "          WHERE te.tenant_id = t.id) AS has_environment, "
                    # Both secrets, matching list_environments.has_secrets — checking only the api
                    # key made a half-configured environment read "Ready" on this list while the
                    # firm's own page said its keys were unset.
                    "  EXISTS (SELECT 1 FROM auth.tenant_environments te "
                    "          JOIN auth.environment_secrets s ON s.tenant_environment_id = te.id "
                    "          WHERE te.tenant_id = t.id AND s.workos_api_key_ct IS NOT NULL "
                    "            AND s.cookie_password_ct IS NOT NULL) "
                    "    AS has_keys, "
                    "  EXISTS (SELECT 1 FROM auth.tenant_environments te "
                    "          WHERE te.tenant_id = t.id AND te.firm_org_id IS NOT NULL) "
                    "    AS has_orgs "
                    "FROM auth.tenants t " + where + " ORDER BY t.id"  # noqa: S608
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    firms = [dict(r) for r in rows]
    if limit is None:
        return firms
    start = max(0, offset)
    return {"items": firms[start : start + max(1, min(limit, 200))], "total": len(firms)}


@router.get("/audit", name="list-audit")
async def list_audit(
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_audit_read),
    limit: int = listing.DEFAULT_LIMIT,
    action: str | None = None,
    before_id: int | None = None,
    tenant_id: Annotated[list[int] | None, Query()] = None,
    actor: Annotated[list[str] | None, Query()] = None,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, object]:
    """The append-only audit trail (most recent first), joined to the actor + firm.

    Filters are applied **server-side** so a search is over the whole trail, not one page of it:
    `action` (exact), `tenant_id` (one or more firms, ORed), `actor` (one or more substrings of the
    actor's email/name, ORed), `since`/`until` (ISO dates or timestamps, inclusive of `since`,
    exclusive of `until`), and keyset pagination via `before_id`. `limit` is clamped to 200.

    Returns `{items, tenants}`: a `tenants` lookup (every firm's id/name) rides along to feed the
    firm-filter dropdown, exactly as `list_audit_actions` does — the caller holds `audit:read`, not
    necessarily `tenants:read`, so the surface feeds its own filter.

    `since`/`until` are parsed HERE rather than cast in SQL: `CAST('yesterday' AS timestamptz)`
    raises a psycopg DataError, which reaches the caller as a 500 for what is plainly a bad
    request."""
    limit = max(1, min(limit, 200))
    since_at = _parse_instant(since, "since")
    until_at = _parse_instant(until, "until")
    # A BARE INT is ONE firm, not a sequence. Internal callers and router tests call this
    # function directly with `tenant_id=7`, and that scalar reached `CAST(:tenant_ids AS int[])`
    # as a smallint — "cannot cast type smallint to integer[]", a 500 on an ordinary call.
    tenant_ids = [tenant_id] if isinstance(tenant_id, int) else list(tenant_id or []) or None
    # `escape_like` FIRST, then the wildcards, so the ones the caller typed stay literal. Without
    # it `?actor=%` — or `_`, or an address that contains one — matched EVERY actor in the trail,
    # handing an operator who had narrowed the log to one person the whole estate's history.
    actor_patterns = [
        f"%{listing.escape_like(a)}%" for a in listing.clean_many(actor) or []
    ] or None
    rows = (
        (
            await session.execute(
                text(
                    "SELECT l.id, l.created_at, l.action, l.entity_type, l.entity_id, l.detail, "
                    "  l.tenant_id, l.user_id, u.email AS actor_email, u.name AS actor_name, "
                    "  t.name AS tenant_name "
                    "FROM audit.log l "
                    "LEFT JOIN auth.users u ON u.id = l.user_id "
                    "LEFT JOIN auth.tenants t ON t.id = l.tenant_id "
                    "WHERE (CAST(:action AS text) IS NULL OR l.action = :action) "
                    "  AND (CAST(:before_id AS bigint) IS NULL OR l.id < :before_id) "
                    "  AND (CAST(:tenant_ids AS int[]) IS NULL "
                    "       OR l.tenant_id = ANY(CAST(:tenant_ids AS int[]))) "
                    # UNNEST + EXISTS, not `ILIKE ANY(...)`, so each comparison can name
                    # its ESCAPE character: `ILIKE ANY (array)` is the subquery-operator
                    # form and takes none, leaving `escape_like`'s backslashes literal.
                    "  AND (CAST(:actor_patterns AS text[]) IS NULL "
                    "       OR EXISTS (SELECT 1 "
                    "                  FROM unnest(CAST(:actor_patterns AS text[])) AS pattern "
                    "                  WHERE u.email ILIKE pattern ESCAPE '\\' "
                    "                     OR u.name ILIKE pattern ESCAPE '\\')) "
                    "  AND (CAST(:since AS timestamptz) IS NULL OR l.created_at >= :since) "
                    "  AND (CAST(:until AS timestamptz) IS NULL OR l.created_at < :until) "
                    "ORDER BY l.id DESC LIMIT :limit"
                ),
                {
                    "action": action,
                    "before_id": before_id,
                    "tenant_ids": tenant_ids,
                    "actor_patterns": actor_patterns,
                    "since": since_at,
                    "until": until_at,
                    "limit": limit,
                },
            )
        )
        .mappings()
        .all()
    )
    # The firm-filter dropdown lookup rides only the FIRST page: a keyset continuation (before_id
    # set) never re-feeds the dropdown, and /audit/more + the CSV export discard `tenants` — so skip
    # the scan on cursor pages. Every firm's (id, name); the audit trail filters by numeric
    # tenant_id, so the lookup carries the id, unlike the action stream's uid-keyed one.
    tenants = (
        (await session.execute(text("SELECT id, name FROM auth.tenants ORDER BY lower(name)")))
        .mappings()
        .all()
        if before_id is None
        else []
    )
    return {
        "items": [dict(r) for r in rows],
        "tenants": [dict(t) for t in tenants],
    }


# ---- entity resolution for the action stream ---------------------------------------------------
# The stream stores raw ids (path params + the write-time subject allowlist,
# app/audit_actions.py::SUBJECT_FIELDS); the read surface resolves them into display labels so an
# operator reads "Margaret Cole", not "contact_id 96". Resolution is READ-time on purpose: labels
# follow renames (support work wants the client's CURRENT name), and the raw ids stay the durable
# record — a deleted entity simply resolves to a null label and the UI shows the bare id, which
# is honest. Batched: the ids on the returned page are collected per KIND and resolved with ONE
# query per kind present (never per row).

#: id-shaped field name (path param or subject key) → entity kind. Names not listed here stay
#: plain parameters — only fields whose referent is unambiguous from the name alone belong.
_ENTITY_FIELD_KINDS: dict[str, str] = {
    # BOTH SPELLINGS FOR EVERY RENAMED KIND. The stream records the path param the route actually
    # received, so a row written before its route became uid-addressed holds `<thing>_id` and one
    # written since holds `<thing>_uid`. Dropping either half loses half the history: without the
    # uid the feed shows a raw id where a name belongs, and without the id every older row does.
    #
    # `contact`, `account`, `model` and `conversation` all made that move (65 `{tenant_uid}`,
    # 20 `{contact_uid}`, 6 `{model_uid}`, 6 `{conversation_uid}`, 4 `{account_uid}` route params
    # today), and each resolver below is keyed on either spelling to match.
    "contact_uid": "contact",
    "contact_id": "contact",
    "account_uid": "account",
    "model_uid": "model",
    "conversation_uid": "conversation",
    "client_contact_id": "contact",
    "participant_contact_id": "contact",
    "intended_contact_id": "contact",
    "assignee_contact_id": "contact",
    "subject_contact_id": "contact",
    "contact_ids": "contact",  # documents-send-document: a LIST of contact ids
    "account_id": "account",
    "transaction_id": "transaction",  # contributions-classify-transaction
    "conversation_id": "conversation",  # messaging-conversation-send: names the client on it
    # Same conversation id, resolved to the ADVISER instead — the counterparty when the
    # CLIENT is the sender. Lifted off the response, since their thread route names none.
    "conversation_adviser_id": "conversation_adviser",
    "user_id": "user",
    "adviser_id": "user",
    "assignee_user_id": "user",
    # `tenant_uid` since the admin section became uid-addressed: the stream records the path
    # param the route received. `tenant_id` stays for the rows recorded before that.
    "tenant_uid": "tenant",
    "tenant_id": "tenant",
    # The admin Integrations screen's own tenant FK (integrations.platforms.firm_id,
    # 0273_integrations_tenant) -- a different field name for the same "tenant" chip.
    "firm_id": "tenant",
    "model_id": "model",
    "template_uid": "template",
    "target_document_uid": "document",
    # documents-upload-document: the created documents, lifted off the 2xx response. A LIST because
    # one drop may carry several files — `_entity_refs` fans a list out into one chip each.
    "document_uids": "document",
    # 54 live event types carried a path id that resolved to NOTHING. A param is listed only when
    # ONE table can answer it: `item_uid` is absent because it names two different records, and a
    # wrong name is worse than a bare uid because it is believed.
    "req_uid": "document_request",
    "type_uid": "document_type",
    "exec_uid": "journey",
    "app_uid": "journey",
    "case_uid": "case",
    "group_uid": "message_group",
    "credential_uid": "storage_credential",
    "environment_uid": "environment",
    "beneficiary_uid": "beneficiary",
    "product_uid": "product",
    "fee_uid": "fee_package",
    "provider_uid": "catalogue_provider",
    # A SUBJECT field, not a path param: the automation's flow (event_schema.registry).
    "flow_uid": "flow",
}

#: The generic `{uid}` path param is ambiguous on its own — the event type's RESOURCE says which
#: table it names (`actions.event_resource`).
#:
#: ONE map, TWO spellings, and they cannot collide: a current resource is singular (`document`) and
#: a
#: legacy router tag is plural or a router name (`documents`, `shares`, `firm`). `audit.actions` is
#: append-only, so a page of a firm's trail is mostly rows written before the rename — resolving
#: only
#: the current spelling would silently stop labelling all of them, and an unlabelled chip renders as
#: a
#: bare uid, which reads as "this record was deleted".
_UID_KIND_BY_RESOURCE: dict[str, str] = {
    # -- the current vocabulary
    "document": "document",
    "document_share": "document",  # POST /documents/{uid}/shares — the uid is the document's
    "template": "template",
    "form": "form",
    "flow": "flow",
    "task": "task",
    "instruction": "instruction",
    # -- legacy router tags, for rows written before the resource.verb rename
    "documents": "document",
    # RETIRED FEATURE, DELIBERATELY KEPT. The document share-links router is gone, so nothing
    # writes a share row any more — but the rows it already wrote are in `audit.actions` for good,
    # and this turns their `{uid}` into a document NAME on /events and /admin/audit. Dropping it
    # would silently degrade historic audit history to bare uids. Do not tidy away. (Both spellings:
    # `document_share` resource above for post-rename rows, this legacy router tag for older ones.)
    "shares": "document",  # the uid was the document's (POST /documents/{uid}/shares)
    "templates": "template",
    "forms": "form",
    "flows": "flow",
    "tasks": "task",
    "firm": "instruction",  # the only `{uid}` on the firm router was cancel-instruction
    # -- added by the September 2026 event-content review (see _ENTITY_FIELD_KINDS) -------------
    "automation": "automation",
    "content": "content",
    "contribution": "contribution",
    "transfer": "transfer",
    "dataset": "dataset",
    "email_template": "email_template",
    "file": "file",
    "notification": "notification",
    # NOT a platform record: `/admin/platforms/{provider}/firms/{uid}/actions/{key}` addresses the
    # FIRM, so the uid on a `platform.` event is a tenant uid and resolves as one.
    "platform": "tenant",
    # The notification REGISTRY's own rows: a rule edit rendered `uid nrule_8f0c26e8…` and named
    # nothing, where a firm reads its trail to see WHICH notice it changed.
    "notification_rule": "notification_rule",
}

#: One label query per kind: `= ANY(:ids)` over the page's collected ids. Every query returns
#: (id text, label text|null); ids are pre-validated in Python (int/uuid parses) so a junk value
#: in an old event can never error the whole page.
#: A person's display name off a joined `crm.contacts` aliased `ct` — individuals carry
#: first/surname, organisations carry `name`. Written once because eleven resolvers need it.
_CONTACT_NAME = "COALESCE(NULLIF(trim(concat_ws(' ', ct.first_name, ct.surname)), ''), ct.name)"


def _worded(column: str) -> str:
    """A stored enum member, rendered as words.

    `docs/design-language.md` bans title case across the three portals, so an instruction's
    `expected_in` becomes "Expected in" — sentence case, not `initcap`. NULL-safe: every caller
    assembles with `concat_ws`.
    """
    words = f"replace({column}, '_', ' ')"
    return f"upper(left({words}, 1)) || substr({words}, 2)"


#: An account's label. The wrapper alone is not an identity — "Individual Savings Account" is what
#: fifty of them are called — so the client is part of it, EXCEPT where the account's own name
#: already carries it ("Leo Quennell GIA"), which is what the containment test stops repeating.
_ACCOUNT_LABEL = (
    "CASE WHEN " + _CONTACT_NAME + " IS NULL OR a.name IS NULL "
    "       THEN COALESCE(a.name, " + _CONTACT_NAME + ", a.uid) "
    "     WHEN position(lower(" + _CONTACT_NAME + ") in lower(a.name)) > 0 THEN a.name "
    "     ELSE a.name || ' - ' || " + _CONTACT_NAME + " END"
)


_ENTITY_RESOLVERS: dict[str, str] = {
    # THE RULE EVERY LABEL OBEYS: name the RECORD, never its type. `describe.ts::namedSubject`
    # prints "{verb} {label}", so a label naming a type gives "Started Onboarding" on a firm
    # running fifty onboardings — true of the template, silent about what happened. So a record
    # belonging to a CLIENT is labelled with what it is AND whose it is; one belonging to the FIRM
    # is exempt, because a model or a template is a thing the firm authored once and its name IS
    # its identity.
    #
    # Every client join is LEFT and every assembly is `concat_ws` inside a `NULLIF`: `'x' || NULL`
    # is NULL, so one unreadable half must not cost the whole label — which is exactly a client
    # outside the caller's RLS scope.
    # Each of these is keyed on EITHER spelling, for `_ENTITY_FIELD_KINDS`' reason — a UID arm
    # reading `:ids` (every collected value, as text) and a SERIAL arm reading `:int_ids` (only the
    # numeric ones, filtered in Python by `_resolve_entities`).
    #
    # THE TWO ARRAYS ARE NOT A TIDINESS CHOICE. One array cast to `int[]` raises on the first uid
    # in it — `CAST('aNdY9TTi5uQT' AS int)` fails the statement, so ONE renamed route in the page
    # 500s the whole audit feed. Comparing `id::text` instead cannot raise but cannot use the
    # primary key either, so it sequentially scans the table under the caller's RLS predicate on
    # every page load. Two arrays are the only shape that is both safe and indexable.
    "contact": (
        # Individuals carry first/surname, organisations carry name — prefer the person form.
        # `uid` rides along because a row recorded before the rename stores the SERIAL the route was
        # called with (a historical fact, not to be rewritten) while a portal addresses the record
        # by its uid.
        #
        # Keyed on EITHER spelling, exactly as `tenant` is: the id half cannot be dropped (old rows
        # hold serials) and the uid half cannot be omitted (every row since the rename holds a uid).
        "SELECT uid AS id, uid, "
        "  COALESCE(NULLIF(trim(concat_ws(' ', first_name, surname)), ''), name) AS label "
        "FROM crm.contacts WHERE uid = ANY(CAST(:ids AS text[])) "
        "UNION ALL "
        "SELECT id::text AS id, uid, "
        "  COALESCE(NULLIF(trim(concat_ws(' ', first_name, surname)), ''), name) AS label "
        "FROM crm.contacts WHERE id = ANY(CAST(:int_ids AS int[]))"
    ),
    # `_ACCOUNT_LABEL` above carries the rule; the client is LEFT-joined so an account still
    # resolves when its owner is outside the caller's RLS scope.
    "account": (
        "SELECT a.uid AS id, a.uid, " + _ACCOUNT_LABEL + " AS label "
        "FROM platform.accounts a LEFT JOIN crm.contacts ct ON ct.id = a.contact_id "
        "WHERE a.uid = ANY(CAST(:ids AS text[])) "
        "UNION ALL "
        "SELECT a.id::text AS id, a.uid, " + _ACCOUNT_LABEL + " AS label "
        "FROM platform.accounts a LEFT JOIN crm.contacts ct ON ct.id = a.contact_id "
        "WHERE a.id = ANY(CAST(:int_ids AS int[]))"
    ),
    # The user's LOGIN is the label — "which login was linked" is the question the stream answers.
    "user": (
        # `:uuid_ids`, the third bound array, for the reason the serial arms take `:int_ids`: a
        # non-uuid in `:ids` raises `invalid input syntax for type uuid` and 500s the whole feed
        # just as an oversized integer does. No `*_uid` field names this kind TODAY — so this is
        # not reachable — but "not reachable" rested on a coincidence between two dicts thirty
        # lines apart, and the next `assignee_uid`-shaped field would have restored it silently.
        # The cast is unreachable by construction instead.
        "SELECT id::text AS id, uid, email AS label "
        "FROM auth.users WHERE id = ANY(CAST(:uuid_ids AS uuid[]))"
    ),
    "tenant": (
        # Keyed on EITHER spelling: a row recorded before the section was uid-addressed holds
        # the serial, one recorded since holds the uid, and both must still name the firm.
        "SELECT COALESCE(uid, id::text) AS id, uid, name AS label FROM auth.tenants "
        "WHERE uid = ANY(CAST(:ids AS text[])) "
        "UNION ALL "
        "SELECT id::text AS id, uid, name AS label FROM auth.tenants "
        "WHERE id = ANY(CAST(:int_ids AS int[]))"
    ),
    "model": (
        "SELECT uid AS id, uid, name AS label "
        "FROM platform.models WHERE uid = ANY(CAST(:ids AS text[])) "
        "UNION ALL "
        "SELECT id::text AS id, uid, name AS label "
        "FROM platform.models WHERE id = ANY(CAST(:int_ids AS int[]))"
    ),
    "template": "SELECT uid AS id, name AS label FROM documents.templates WHERE uid = ANY(:ids)",
    # A generated document's NAME is the TEMPLATE's ("Suitability report"), which identifies the
    # template and not this copy of it — whose it is does. Ordered, so the fallback to the
    # 'subject' link is stable rather than whichever row came back first.
    "document": (
        "SELECT d.uid AS id, NULLIF(concat_ws(' - ', COALESCE(d.name, d.document_type), ("  # noqa: S608 - `_CONTACT_NAME` is a module constant; every caller value binds
        "  SELECT " + _CONTACT_NAME + " FROM crm.contacts ct WHERE ct.id = COALESCE("
        "    d.intended_contact_id, (SELECT dc.contact_id FROM documents.document_contacts dc "
        "      WHERE dc.document_id = d.id ORDER BY dc.added_at, dc.contact_id LIMIT 1))"
        ")), '') AS label "
        "FROM documents.documents d WHERE d.uid = ANY(CAST(:ids AS text[]))"
    ),
    # Two titles read alike ("Review", "Chase"); WHO the task is about tells them apart. The
    # subject is a loose (type, id) pair with no FK, so this is the same CASE guard
    # `routers/tasks.py` uses — a non-numeric subject yields NULL rather than failing the cast.
    "task": (
        "SELECT t.uid AS id, NULLIF(concat_ws(' - ', t.title, " + _CONTACT_NAME + "), '') AS label "
        "FROM tasks.tasks t LEFT JOIN crm.contacts ct ON ct.id = CASE "
        "  WHEN t.subject_type = 'contact' AND t.subject_id ~ '^[0-9]+$' "
        "  THEN CAST(t.subject_id AS int) END "
        "WHERE t.uid = ANY(CAST(:ids AS text[]))"
    ),
    "flow": "SELECT uid AS id, name AS label FROM flows.flows WHERE uid = ANY(:ids)",
    "form": "SELECT uid AS id, name AS label FROM documents.forms WHERE uid = ANY(:ids)",
    # No display name exists, so the identity is WHOSE money it is and how much. `concat_ws`,
    # never `||`: an instruction with no amount used to resolve to no label at all.
    "instruction": (
        "SELECT i.uid AS id, NULLIF(concat_ws(' - ', " + _CONTACT_NAME + ", "
        "  concat_ws(' ', " + _worded("i.kind::text") + ", '£' || i.amount::text)), '') AS label "
        "FROM platform.instructions i LEFT JOIN crm.contacts ct ON ct.id = i.contact_id "
        "WHERE i.uid = ANY(CAST(:ids AS text[]))"
    ),
    # Same shape as an instruction — the currency rather than a hardcoded '£', because a book is
    # not necessarily sterling and a wrong symbol is worse than none.
    # `:int_ids` for the reason `conversation_adviser` gives — no route spells `transaction_uid`,
    # and the numeric-only array is what keeps that true of the SQL as well as of the callers.
    "transaction": (
        "SELECT t.id::text AS id, NULLIF(concat_ws(' - ', " + _CONTACT_NAME + ", "
        "  concat_ws(' ', " + _worded("t.type::text") + ", t.currency, "
        "  to_char(t.amount, 'FM999999999990.00'))), '') AS label "
        "FROM platform.transactions t LEFT JOIN crm.contacts ct ON ct.id = t.contact_id "
        "WHERE t.id = ANY(CAST(:int_ids AS bigint[]))"
    ),
    # A conversation is per-client (1:1), so name it by the client on the channel — a chat event
    # says WHO it was with, never the message text (bodies are never captured; docs/messaging.md).
    "conversation": (
        # `c.uid` since 0112_messaging_uids: the stream recorded the serial the route was
        # called with, and `/chats/{uid}` is how the thread is addressed today. Without it the
        # fallback below would hand the chip the serial as its address.
        "SELECT c.uid AS id, c.uid, "
        "  COALESCE(NULLIF(trim(concat_ws(' ', ct.first_name, ct.surname)), ''), ct.name) AS label "
        "FROM messaging.conversations c JOIN crm.contacts ct ON ct.id = c.contact_id "
        "WHERE c.uid = ANY(CAST(:ids AS text[])) "
        "UNION ALL "
        "SELECT c.id::text AS id, c.uid, "
        "  COALESCE(NULLIF(trim(concat_ws(' ', ct.first_name, ct.surname)), ''), ct.name) AS label "
        "FROM messaging.conversations c JOIN crm.contacts ct ON ct.id = c.contact_id "
        "WHERE c.id = ANY(CAST(:int_ids AS bigint[]))"
    ),
    # The OTHER end of the same channel, keyed by the same conversation id. A client's own send
    # carries no conversation on the route (their thread is derived from their scope), so the id is
    # lifted off the response — and naming the conversation's CLIENT there would name the sender,
    # printing "Craig Currie sent a message to Craig Currie". The recipient of a client's message is
    # the adviser on the channel, so this resolves that instead and the read side picks whichever of
    # the two is the counterparty.
    # No uid arm: `conversation_adviser_id` is a SUBJECT key we write ourselves, always the
    # serial, and no route param spells it. It reads `:int_ids` all the same, so that the cast
    # cannot be reached by a text value however this kind is fed in future.
    "conversation_adviser": (
        "SELECT c.id::text AS id, u.name AS label "
        "FROM messaging.conversations c JOIN auth.users u ON u.id = c.adviser_user_id "
        "WHERE c.id = ANY(CAST(:int_ids AS bigint[]))"
    ),
    # Added by the September 2026 event-content review. Every one is uid-keyed with no serial arm:
    # each route has only ever been uid-addressed. `concat_ws` rather than `||` throughout, and
    # NULLIF turns the all-NULL case back into "no label", which the UI renders honestly.
    "automation": (
        "SELECT uid AS id, name AS label FROM flows.automations "
        "WHERE uid = ANY(CAST(:ids AS text[]))"
    ),
    "content": (
        "SELECT uid AS id, title AS label FROM content.items WHERE uid = ANY(CAST(:ids AS text[]))"
    ),
    # A contribution and a transfer have no name. The client and the money are what tell two of
    # them apart, and both are already on the row being read for the label.
    "contribution": (
        "SELECT c.uid AS id, NULLIF(concat_ws(' - ', "
        "  COALESCE(NULLIF(trim(concat_ws(' ', ct.first_name, ct.surname)), ''), ct.name), "
        "  concat_ws(' ', c.currency, c.amount::text)), '') AS label "
        "FROM platform.contributions c LEFT JOIN crm.contacts ct ON ct.id = c.contact_id "
        "WHERE c.uid = ANY(CAST(:ids AS text[]))"
    ),
    "transfer": (
        "SELECT tr.uid AS id, NULLIF(concat_ws(' - ', "
        "  COALESCE(NULLIF(trim(concat_ws(' ', ct.first_name, ct.surname)), ''), ct.name), "
        "  tr.ceding_provider), '') AS label "
        "FROM platform.transfers tr LEFT JOIN crm.contacts ct ON ct.id = tr.contact_id "
        "WHERE tr.uid = ANY(CAST(:ids AS text[]))"
    ),
    "dataset": (
        "SELECT uid AS id, name AS label FROM documents.datasets "
        "WHERE uid = ANY(CAST(:ids AS text[]))"
    ),
    # A request is FOR somebody AND ASKS FOR something, and needs both: the client alone read as
    # though the client were the record, and every new client gets the same three-item ID&V
    # request. Capped at three items — a label nobody reads to the end identifies nothing.
    "document_request": (
        "SELECT r.uid AS id, NULLIF(concat_ws(' - ', " + _CONTACT_NAME + ", ("  # noqa: S608 - `_CONTACT_NAME` is a module constant; every caller value binds
        "  SELECT string_agg(i.title, ', ') FROM (SELECT ri.title FROM documents.request_items ri "
        "    WHERE ri.request_id = r.id ORDER BY ri.id LIMIT 3) i)), '') AS label "
        "FROM documents.requests r LEFT JOIN crm.contacts ct ON ct.id = r.contact_id "
        "WHERE r.uid = ANY(CAST(:ids AS text[]))"
    ),
    "document_type": (
        "SELECT uid AS id, label AS label FROM documents.document_types "
        "WHERE uid = ANY(CAST(:ids AS text[]))"
    ),
    "email_template": (
        "SELECT uid AS id, name AS label FROM email.templates WHERE uid = ANY(CAST(:ids AS text[]))"
    ),
    # The journey is named for the flow AND the people it is for: a firm runs one Onboarding
    # definition and fifty onboardings. The participants come from the CASES, not from
    # `executions.subject_type/subject_id` — those columns are NULL on every row in this estate.
    "journey": (
        "SELECT e.uid AS id, NULLIF(concat_ws(' - ', f.name, ("
        "  SELECT string_agg(DISTINCT COALESCE("
        "    NULLIF(trim(concat_ws(' ', ct.first_name, ct.surname)), ''), ct.name), ', ') "
        "  FROM flows.cases c JOIN crm.contacts ct ON ct.id = c.subject_contact_id "
        "  WHERE c.execution_id = e.id)), '') AS label "
        "FROM flows.executions e JOIN flows.flows f ON f.id = e.flow_id "
        "WHERE e.uid = ANY(CAST(:ids AS text[]))"
    ),
    # The journey's shape, one participant wide: the flow says what is being done, the contact
    # says to whom. Either alone names the definition or the client rather than the case.
    "case": (
        "SELECT c.uid AS id, NULLIF(concat_ws(' - ', f.name, " + _CONTACT_NAME + "), '') AS label "
        "FROM flows.cases c JOIN flows.flows f ON f.id = c.flow_id "
        "  LEFT JOIN crm.contacts ct ON ct.id = c.subject_contact_id "
        "WHERE c.uid = ANY(CAST(:ids AS text[]))"
    ),
    "message_group": (
        "SELECT uid AS id, name AS label FROM messaging.groups "
        "WHERE uid = ANY(CAST(:ids AS text[]))"
    ),
    # NOT filtered on `deleted_at`: the event this labels is `file.deleted`, so the only row it
    # ever reads has just been deleted. The filename, then whose it is.
    "file": (
        "SELECT f.uid AS id, NULLIF(concat_ws(' - ', f.name, " + _CONTACT_NAME + "), '') AS label "
        "FROM documents.attachments f LEFT JOIN crm.contacts ct ON ct.id = f.contact_id "
        "WHERE f.uid = ANY(CAST(:ids AS text[]))"
    ),
    # Whose keys they were. The access key id is the other candidate and is not used: a credential
    # is revoked ON somebody, and the person is what the trail is asked about.
    "storage_credential": (
        "SELECT c.uid AS id, u.name AS label "
        "FROM storage.user_credentials c JOIN auth.users u ON u.id = c.user_id "
        "WHERE c.uid = ANY(CAST(:ids AS text[]))"
    ),
    "environment": (
        "SELECT e.uid AS id, t.name AS label "
        "FROM auth.tenant_environments e JOIN auth.tenants t ON t.id = e.tenant_id "
        "WHERE e.uid = ANY(CAST(:ids AS text[]))"
    ),
    # The nominee, then the MEMBER whose account they are nominated on — a nomination is the one
    # record here where the two names are different people, so dropping either loses half the fact.
    "beneficiary": (
        "SELECT b.uid AS id, NULLIF(concat_ws(' - ', b.name, " + _CONTACT_NAME + "), '') AS label "
        "FROM crm.beneficiaries b LEFT JOIN crm.contacts ct ON ct.id = b.contact_id "
        "WHERE b.uid = ANY(CAST(:ids AS text[]))"
    ),
    "product": (
        "SELECT uid AS id, COALESCE(name, code) AS label FROM platform.products "
        "WHERE uid = ANY(CAST(:ids AS text[]))"
    ),
    "fee_package": (
        "SELECT uid AS id, name AS label FROM platform.fee_packages "
        "WHERE uid = ANY(CAST(:ids AS text[]))"
    ),
    # One field (`provider_uid`) over two registers — the route's `{kind}` segment tells them
    # apart and this cannot bind to it. The uids share one id space, so at most one side answers.
    "catalogue_provider": (
        "SELECT uid AS id, name AS label FROM platform.third_party_providers "
        "WHERE uid = ANY(CAST(:ids AS text[])) "
        "UNION ALL "
        "SELECT uid AS id, name AS label FROM platform.transfer_providers "
        "WHERE uid = ANY(CAST(:ids AS text[]))"
    ),
    # A notice's title is its RULE's words ("A task is due soon") — the same sentence on every
    # row. Who it went to tells them apart, and `recipient_kind` is 'user' or 'contact', so both
    # are joined and whichever answered wins.
    "notification": (
        "SELECT n.uid AS id, NULLIF(concat_ws(' - ', n.title, "
        "  COALESCE(u.name, " + _CONTACT_NAME + ")), '') AS label "
        "FROM notifications.notifications n "
        "  LEFT JOIN auth.users u ON u.id = n.recipient_user_id "
        "  LEFT JOIN crm.contacts ct ON ct.id = n.recipient_contact_id "
        "WHERE n.uid = ANY(CAST(:ids AS text[]))"
    ),
    # `label` is what the registry shows a rule as; `title` is the notice's subject line and
    # `trigger_key` the event it fires on — readable answers when a firm has not named the rule.
    "notification_rule": (
        "SELECT uid AS id, COALESCE(NULLIF(label, ''), NULLIF(title, ''), trigger_key) AS label "
        "FROM notifications.rules WHERE uid = ANY(CAST(:ids AS text[]))"
    ),
}

#: Kinds whose SERIAL ids need validating before the SQL cast. A kind is in here because its
#: `*_id` fields are integers, NOT because every field naming it is.
_INT_ID_KINDS = frozenset(
    {"contact", "account", "tenant", "model", "transaction", "conversation", "conversation_adviser"}
)
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


#: The int4 ceiling. The serial arms cast to `int[]` (contacts, accounts, models, tenants) or
#: `bigint[]` (transactions, conversations), and a value is only ever compared against a real
#: primary key — so bounding every one of them at the NARROWER of the two is correct: an id above
#: int4 cannot exist in an `int` column, and one that big in a `bigint` column would have to be a
#: real row this deployment has never issued. Being conservative here costs a label on a row that
#: cannot exist and buys back the "cannot raise" property the text comparison used to have.
_MAX_SERIAL = 2**31 - 1


def _fits_a_serial(value: str) -> bool:
    """Is this text safe to hand to a numeric array cast?

    `isdigit()` alone is NOT, and that is the whole reason this exists. The serial arms used to
    compare `id::text`, which cannot raise whatever the value; casting instead is what made them
    indexable, and it also made an oversized digit string fatal — `CAST('{999999999999}' AS int[])`
    raises `value out of range for type integer`, which is a statement error, which is a 500 for
    the ENTIRE audit feed rather than one unlabelled chip.

    Reachable because the stream is history: `path_params` records what a route was called with,
    `metadata.subject` records what other code wrote, and a bare 12-character nanoid can be all
    digits (the alphabet has ten of them), so `_entity_id_valid`'s `*_uid` arm legitimately admits
    a value this then has to refuse.
    """
    return value.isdigit() and int(value) <= _MAX_SERIAL


def _entity_id_valid(kind: str, value: str, field: str = "") -> bool:
    """Is this a value we will try to resolve?

    KEYED ON THE FIELD, NOT ONLY THE KIND, and that is the whole point. `_INT_ID_KINDS` says
    "a contact's id is an integer", which was true of every field naming one until the routes
    became uid-addressed. After the rename `contact_uid` still maps to kind `contact`, so the
    digits-only rule DROPPED every uid before it reached the resolver — and the entity chip that
    should carry the client's name showed a bare id instead.

    It had been failing silently for `tenant_uid` since that field was added, for exactly the same
    reason: `ten_<hex>` is not `isdigit()` either. That is why this is a rule about the field's
    SPELLING rather than another exception per kind — the next `*_uid` route needs no change here.
    """
    # `field == "uid"` is redundant TODAY and kept deliberately: every kind a bare `uid` can name
    # (`_UID_KIND_BY_RESOURCE` — document, template, form, flow, task, instruction) is already a
    # text-uid kind that falls through to the identical rule at the end of this function. It is
    # here so that adding an INT-keyed resource to that map — `"account": "account"`, the obvious
    # next one — cannot silently reintroduce the bug this function exists to fix.
    if field == "uid" or field.endswith("_uid"):
        return 0 < len(value) <= 120  # a public uid: sane length only, the cast is to text
    if kind in _INT_ID_KINDS:
        return value.isdigit()
    if kind == "user":
        return bool(_UUID_RE.match(value))
    return 0 < len(value) <= 120  # text uids: sane length only


def _entity_refs(item: dict[str, object]) -> list[tuple[str, str]]:
    """The (kind, id) pairs one event references — from its path params, its captured subject
    AND its captured before-values (`metadata.previous`, the PREVIOUS_FIELDS pre-read: the OLD
    model on a mandate change resolves to a label exactly like the new one, so the UI can say
    "Cautious → Balanced") — deduplicated, in encounter order, junk-valued ids dropped."""
    event_type = str(item.get("event_type") or "")
    resource = audit_actions.event_resource(event_type)
    sources: list[dict[str, object]] = []
    path_params = item.get("path_params")
    if isinstance(path_params, dict):
        sources.append(path_params)
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        for key in ("subject", "previous"):
            captured = metadata.get(key)
            if isinstance(captured, dict):
                sources.append(captured)

    refs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        for field, raw in source.items():
            kind = _ENTITY_FIELD_KINDS.get(field) or (
                _UID_KIND_BY_RESOURCE.get(resource) if field == "uid" else None
            )
            if kind is None:
                continue
            values = raw if isinstance(raw, list) else [raw]
            for value in values:
                if isinstance(value, bool) or not isinstance(value, str | int):
                    continue  # ids are strings/ints; bools/floats/dicts are not entity ids
                text_id = str(value)
                if not _entity_id_valid(kind, text_id, field):
                    continue
                ref = (kind, text_id)
                if ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
    return refs


async def _resolve_entities(session: AsyncSession, items: list[dict[str, object]]) -> None:
    """Annotate each event with `entities: [{kind, id, uid, label}]` — one label query per entity
    kind on the page (batched, no N+1). Unknown/deleted ids keep a null label.

    `id` is what the stream RECORDED (the path param the route actually received); `uid` is how a
    portal addresses that record today. For most kinds they are the same string, because the route
    was already uid-typed. For a contact or an account the route is serial-typed, so the resolver
    selects the uid alongside the label it was reading the row for anyway — without it the entity
    chip could only link to an address that no longer exists. An unresolved entity has no uid, and
    the UI renders it unlinked rather than guessing.
    """
    refs_by_item = [_entity_refs(item) for item in items]
    ids_by_kind: dict[str, list[str]] = {}
    for refs in refs_by_item:
        for kind, text_id in refs:
            bucket = ids_by_kind.setdefault(kind, [])
            if text_id not in bucket:
                bucket.append(text_id)

    labels: dict[tuple[str, str], str | None] = {}
    uids: dict[tuple[str, str], str] = {}
    resolved: set[tuple[str, str]] = set()
    for kind, ids in ids_by_kind.items():
        # TWO arrays, one per arm. `:ids` is every collected value as text (the uid arm); `:int_ids`
        # is only those that are actually numeric AND IN RANGE (the serial arm). Splitting here
        # rather than in SQL is what keeps the serial arm INDEXABLE: `WHERE id::text = ANY(...)`
        # cannot use the primary key, so it sequentially scanned the whole table — a firm's entire
        # book, under its RLS predicate — on every audit page load, including the now-common case
        # where every id on the page is a uid and the arm was guaranteed to match nothing.
        #
        # `_fits_a_serial`, NOT `isdigit`: the old text comparison could not raise, and swapping it
        # for a real cast handed back that property unless the values are bounded here. `isdigit()`
        # has no upper bound, so one oversized digit string reaches `CAST(... AS int[])`, raises
        # `value out of range`, and answers 500 for the whole feed — every row, every reader.
        # `audit.actions` is a HISTORICAL store: `path_params` holds whatever a route was called
        # with years ago, `metadata.subject` holds values other code wrote, and a bare 12-character
        # nanoid is drawn from an alphabet that includes the digits, so an all-digit uid is legal
        # and lands here too.
        int_ids = [i for i in ids if _fits_a_serial(i)]
        uuid_ids = [i for i in ids if _UUID_RE.match(i)]
        rows = (
            (
                await session.execute(
                    text(_ENTITY_RESOLVERS[kind]),
                    {"ids": ids, "int_ids": int_ids, "uuid_ids": uuid_ids},
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            ref = (kind, str(row["id"]))
            resolved.add(ref)
            labels[ref] = row["label"]
            # A resolver that selects no `uid` is one whose id already IS the uid.
            uids[ref] = str(row["uid"]) if row.get("uid") is not None else str(row["id"])

    for item, refs in zip(items, refs_by_item, strict=True):
        item["entities"] = [
            {
                "kind": kind,
                "id": text_id,
                "uid": uids.get((kind, text_id)) if (kind, text_id) in resolved else None,
                "label": labels.get((kind, text_id)),
            }
            for kind, text_id in refs
        ]


async def _contact_filter_id(session: AsyncSession, uid: str | None) -> int | None:
    """`?contact=<uid>` -> the serial `audit.actions` recorded, or None when absent.

    A filter value in a URL names the client the way every other URL does. `audit_stream` keeps its
    integer contract because the STREAM holds serials -- the path params a route received, which are
    history and not rewritten. An unknown uid resolves to None rather than 404ing: a filter that
    cannot be honoured must narrow to nothing, not take the page down.
    """
    if not uid:
        return None
    return (
        await session.execute(text("SELECT id FROM crm.contacts WHERE uid = :uid"), {"uid": uid})
    ).scalar_one_or_none()


@router.get("/audit/actions", name="list-audit-actions")
async def list_audit_actions(
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_audit_read),
    limit: int = listing.DEFAULT_LIMIT,
    before: int | None = None,
    #: The live poll's keyset end: rows NEWER than this id, newest first. Filters compose as usual,
    #: so the answer is exactly the events this reader would see — the count a "3 new events" pill
    #: shows must be a count of rows that will actually appear when it is clicked.
    after: int | None = None,
    event: int | None = None,
    tenant: str | None = None,
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
    #: Entity-KIND filter, comma-separated (`tags=document,contact`), ANDed: a row must carry every
    #: kind named. The wire format is fixed and shared with the toolbar's own builder
    #: (`@graphene/ui`'s `actionLogQuery`); blanks are dropped so a stray comma cannot become a kind
    #: no map knows, which `audit_stream._tags_clause` answers with an empty page on purpose.
    tags: str | None = None,
    document_family: bool = False,
    facets: bool = False,
) -> dict[str, object]:
    """The action-event stream (`audit.actions`, 0041) — newest first, joined to display names.

    The read surface promised by the 0041 migration comment: the table is `service_role`-only
    with no `authenticated` grant, so THIS endpoint is the way in — `admin_session` provides the
    service_role read and the coarse operations gate, `audit:read` is the granular permission
    (`system` bypasses), and RLS is deliberately not part of the story (the stream is
    cross-tenant evidence for Graphene operations, scoped by permission, not by policy).

    Rows come back verbatim — `path_params`/`metadata` were redacted at WRITE time
    (`app/audit_actions.py::redact`), so there is nothing left to filter here — with the actor and
    both tenant sides (acting + proxied target) joined for display in the same query (no N+1).
    A `tenants` lookup (every firm's uid/name) rides along to feed the filter dropdown: the
    caller holds `audit:read`, not necessarily `tenants:read`, so the surface feeds itself.
    `?facets=true` does the same job for the event-type and actor dropdowns — the vocabulary of
    the caller's scope, computed over the recent stream and deliberately IGNORING every filter, so
    picking one filter can no longer shrink the others' choices (`audit_stream.action_facets`). It
    is OPT-IN, and that is the design rather than a knob: a filter change is no longer a navigation,
    so the surface asks for the vocabulary ONCE on page entry and every later filter fetch — and
    every "Load more" — leaves it off and keeps what it already holds. Not requested ⇒ not computed
    (`facets: null`), so the extra scan is never a cost the common request pays. Deliberately NOT
    inferred from `before`: a filter applied in place re-reads page one with no cursor, so inferring
    would recompute the vocabulary on exactly the request whose whole point is that it must not
    change. `tenants` still rides on the cursor test — it has no such second reader.

    Each item also carries `entities`: the page's raw ids (path params + the write-time subject
    fields) batch-resolved into CURRENT display labels — one query per entity kind per page (see
    `_resolve_entities`). A deleted/unknown id resolves to a null label, and the UI shows the
    bare id — honest about what no longer exists.

    Filters (server-side, over the whole stream): `tenant` (comma-separated uids, ORed — each
    matches the acting OR the target side, so a firm's slice includes ops actions taken ON it;
    "Firm A or Firm B" is an ordinary question once several firms are in view), `actor`
    (comma-separated names, ORed — substring of the WorkOS sub, email or name each), `event_type`
    (comma-separated prefixes, ORed — `action.firm-` narrows to a family, and several name
    alternatives), `method` (comma-separated verbs, ORed), `proxied` (target_tenant_id present),
    `since`/`until` (ISO; inclusive/exclusive,
    parsed here like `list_audit` so `?since=yesterday` is a 422 not a 500). Keyset pagination
    via `before` on the monotonic id; `limit` clamped to 200; `has_more` from an over-fetch of
    one row rather than the full-page heuristic. `event` fetches a single row by id (the Events
    master-detail deep-link), ANDed with the `tenant` scope like every other filter."""
    # The query itself lives in `app/audit_stream.py`, shared with the firm-facing view
    # (`GET /firm/audit/actions`); only the scope differs — here, an OPTIONAL `?tenant=` filter
    # over both sides of the event. No deny list: this is the operations evidence surface, and
    # it sees everything (including the AML family the firm view withholds).
    #
    # NO CONTACT GATE EITHER, and that is deliberate rather than missed. `CONTACT_SCOPE_SQL` holds
    # a firm's own staff to the clients they may open; this endpoint is Graphene operations, gated
    # on `system`, and there is no client book here to be scoped to. Adding it would read every
    # operator as holding no clients and empty the surface. The firm-facing twin
    # (`routers/firm.py::list_firm_audit_actions`) is where that gate belongs and is applied.
    #
    # ONE scope predicate, hoisted into a local and handed to BOTH the page and the facets: the
    # facet lists must describe exactly the rows this caller may read, and two copies of the
    # predicate is how they would come to disagree.
    scope_params: dict[str, object] = {}
    scope_sql = audit_stream.tenant_scope_sql(tenant, scope_params)
    page = await audit_stream.fetch_actions(
        session,
        scope_sql=scope_sql,
        scope_params=scope_params,
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
        contact=await _contact_filter_id(session, contact),
        contact_uid=contact,
        document=document,
        document_family=document_family,
        tags=[t for t in (tags or "").split(",") if t.strip()],
    )
    # Only the FIRST page needs the firm-filter dropdown lookup — a keyset continuation (`before`
    # set) never re-feeds it and /events/more discards `tenants` — so skip the scan on cursor pages.
    # Every tenant, model included: this is an evidence surface, and a stream row can reference a
    # tenant the firms list would hide — completeness beats prettiness.
    tenants = (
        (await session.execute(text("SELECT uid, name FROM auth.tenants ORDER BY lower(name)")))
        .mappings()
        .all()
        if before is None
        else []
    )
    # The event-type and actor vocabularies, over the caller's scope and INDEPENDENT of every
    # filter above — so choosing an actor can no longer empty the event-type dropdown. Same scope
    # predicate as the page; no deny list here either (operations see the whole stream).
    #
    # Gated on the caller ASKING, never on `before is None` (the `tenants` test above): the surface
    # applies a filter in place now, which re-reads page one WITHOUT a cursor, so a cursor-derived
    # test would recompute this on every filter change — the one request that must not alter the
    # dropdowns is the one that would pay for them. One extra windowed scan on page entry; none
    # thereafter.
    facet_lists = (
        await audit_stream.action_facets(session, scope_sql=scope_sql, scope_params=scope_params)
        if facets
        else None
    )
    items = page["items"]
    await _resolve_entities(session, items)
    return {
        "items": items,
        "has_more": page["has_more"],
        "tenants": [dict(t) for t in tenants],
        "facets": facet_lists,
    }


@router.get("/tenants/{tenant_uid}", name="get-tenant")
async def get_tenant(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """A single firm's detail, addressed by its uid like every other route in this section.

    `tenants:read` opens the Configure section; `tenant_portal:read` opens only the Operate
    workspace. Both need this deliberately small identity projection so the shared parent layout
    can name the target before its child applies the narrower gate. No environment secrets,
    memberships or configuration are exposed here.

    `GET /tenants/by-uid/{uid}` used to sit above this with the same projection: the section is
    addressed by uid, the endpoints took an int, and something had to bridge the two. Now that the
    path itself carries the uid there is nothing to bridge, and the layout reads the record here.
    """
    require_any_permission(principal, TENANTS_READ, TENANT_PORTAL_READ)
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, alias, name, website, enabled, parent_id, "
                    "  workos_project_id "
                    "FROM auth.tenants WHERE id = :t"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return dict(row)


async def _tenant_currency_settings(session: AsyncSession, tenant_id: int) -> dict[str, object]:
    tenant = (
        (
            await session.execute(
                text("SELECT base_currency, allowed_currencies FROM auth.tenants WHERE id = :t"),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    # The reference list is the process cache. The firm's base and allow-list are the
    # only rows this request reads: those are policy, and they do change.
    reference = await currencies.catalogue()
    return {
        "base_currency": str(tenant["base_currency"]),
        "allowed_currencies": list(tenant["allowed_currencies"]),
        "currencies": [
            row.as_ref() for row in reference.ordered(base=str(tenant["base_currency"]))
        ],
    }


@router.get("/tenants/{tenant_uid}/currency-settings", name="get-tenant-currency-settings")
async def get_tenant_currency_settings(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """The firm's currency policy plus the complete ISO reference list for its editor."""
    await _require_tenant_scope(session, principal, tenant_id, write=False)
    return await _tenant_currency_settings(session, tenant_id)


@router.put("/tenants/{tenant_uid}/currency-settings", name="set-tenant-currency-settings")
async def set_tenant_currency_settings(
    body: SetTenantCurrencies,
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Replace the currency allow-list atomically; the base must be one of its members."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    allowed = list(dict.fromkeys(body.allowed_currencies))
    if len(allowed) != len(body.allowed_currencies):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Choose each allowed currency once.",
        )
    if body.base_currency not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The base currency must also be in the allowed currencies.",
        )
    reference = await currencies.catalogue()
    unknown = [code for code in allowed if code not in reference]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown currency code: {', '.join(unknown)}",
        )
    affected_products = (
        (
            await session.execute(
                text(
                    "SELECT name FROM platform.products "
                    "WHERE tenant_id = :t "
                    "  AND EXISTS (SELECT 1 FROM unnest(allowed_currencies) configured(code) "
                    "              WHERE NOT configured.code = ANY(CAST(:allowed AS text[]))) "
                    "ORDER BY name"
                ),
                {"t": tenant_id, "allowed": allowed},
            )
        )
        .scalars()
        .all()
    )
    if affected_products:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Remove the currencies from these products first: "
                + ", ".join(str(name) for name in affected_products)
            ),
        )
    await session.execute(
        text(
            "UPDATE auth.tenants "
            "SET base_currency = :base, allowed_currencies = CAST(:allowed AS text[]) "
            "WHERE id = :t"
        ),
        {"base": body.base_currency, "allowed": allowed, "t": tenant_id},
    )
    return await _tenant_currency_settings(session, tenant_id)


@router.get("/tenants/{tenant_uid}/overview", name="get-tenant-overview")
async def get_tenant_overview(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> dict[str, object]:
    """Is this firm actually set up? One call per feature area, each reporting a state.

    Every section returns one of three states and NEVER guesses between them:
      ok        -- configured and in use
      pending   -- reachable but nothing configured yet (a new firm, not a fault)
      attention -- configured and something is wrong (a failed scan, a disabled connection)

    "pending" and "attention" are kept apart on purpose. Both would render as "not green", but
    a firm nobody has finished onboarding and a firm whose storage scan is erroring are
    different jobs for different people, and collapsing them loses the one that needs acting on.

    Storage figures come from the CACHE (0045) and carry `computed_at`, because totalling a
    bucket means listing it -- see app/storage_usage.py. A tenant never scanned reports null
    figures, not zero: "no figures yet" and "an empty bucket" are different facts about a firm.
    """
    exists = (
        await session.execute(text("SELECT 1 FROM auth.tenants WHERE id = :t"), {"t": tenant_id})
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    # --- platforms -------------------------------------------------------------------
    platform_rows = (
        (
            await session.execute(
                text(
                    "SELECT provider, enabled, config, last_fanout_at "
                    "FROM platform.tenant_platforms WHERE tenant_id = :t ORDER BY provider"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    platforms = [dict(row) for row in platform_rows]
    enabled_platforms = [p for p in platforms if p["enabled"]]
    # Enabled but never fanned out is the failure that hides: the connection looks configured
    # and no data has ever moved.
    never_synced = [p["provider"] for p in enabled_platforms if p["last_fanout_at"] is None]

    # --- storage ---------------------------------------------------------------------
    store = await tenant_storage_mod.get_tenant_store(session, tenant_id)
    usage = await storage_usage.cached(session, tenant_id)

    # --- WorkOS members + roles ------------------------------------------------------
    people = (
        (
            await session.execute(
                text(
                    """
                    SELECT count(DISTINCT om.user_id) AS members,
                           count(DISTINCT om.user_id) FILTER (
                             WHERE om.portal = 'client') AS client_members,
                           count(DISTINCT om.user_id) FILTER (
                             WHERE om.portal <> 'client') AS staff_members,
                           -- `enabled`, not an email_verified flag: provisioning leaves a
                           -- member disabled until they follow the set-password link, so this
                           -- counts people who have been invited and never arrived.
                           count(DISTINCT u.id) FILTER (
                             WHERE u.enabled IS NOT TRUE) AS pending
                      FROM auth.organisation_memberships om
                      JOIN auth.users u ON u.id = om.user_id
                     WHERE om.tenant_id = :t
                    """
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one()
    )
    role_count = (
        await session.execute(
            text("SELECT count(*) FROM auth.roles WHERE tenant_id = :t"), {"t": tenant_id}
        )
    ).scalar_one()
    project_id = (
        await session.execute(
            text("SELECT workos_project_id FROM auth.tenants WHERE id = :t"), {"t": tenant_id}
        )
    ).scalar_one_or_none()

    # --- branding --------------------------------------------------------------------
    theme = (
        await session.execute(
            text("SELECT theme FROM auth.tenants WHERE id = :t"), {"t": tenant_id}
        )
    ).scalar_one_or_none()
    theme_keys = sorted(theme) if isinstance(theme, dict) else []
    # `pages` present means the firm has composed its own client portal; absent means it tracks
    # the platform defaults, which is a legitimate configured state, not an omission.
    has_pages = bool(isinstance(theme, dict) and theme.get("pages"))

    def state(ok: bool, attention: bool = False) -> str:
        if attention:
            return "attention"
        return "ok" if ok else "pending"

    return {
        "tenant_id": tenant_id,
        "sections": {
            "platforms": {
                "state": state(bool(enabled_platforms), bool(never_synced)),
                "connected": len(enabled_platforms),
                "configured": len(platforms),
                "never_synced": never_synced,
                "providers": [
                    {
                        "provider": p["provider"],
                        # The display name travels with the row, so the tile needs no lookup table
                        # of its own — it had one, and it was one of five copies across the app.
                        "name": providers_mod.meta(str(p["provider"]))["name"],
                        "enabled": p["enabled"],
                        "last_fanout_at": p["last_fanout_at"],
                        # The slice keys only, not the whole config: this is a status panel.
                        "slices": sum(
                            len(v) if isinstance(v, list) else 1
                            for v in (p["config"] or {}).values()
                        ),
                    }
                    for p in platforms
                ],
            },
            "storage": {
                "state": state(store is not None, bool(usage and usage.get("error"))),
                "bucket": store.bucket if store else None,
                "usage": usage,
            },
            "workos": {
                # A firm with no project cannot log anyone in, however many members it has.
                "state": state(bool(project_id), bool(project_id) and people["members"] == 0),
                "project_id": project_id,
                "members": people["members"],
                "staff_members": people["staff_members"],
                "client_members": people["client_members"],
                "pending": people["pending"],
                "roles": role_count,
            },
            "branding": {
                "state": state(bool(theme_keys)),
                "configured": bool(theme_keys),
                "custom_pages": has_pages,
                "tokens": len(theme_keys),
            },
        },
    }


@router.post("/tenants/{tenant_uid}/storage/usage/refresh", name="refresh-tenant-usage")
async def refresh_tenant_usage(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> dict[str, object]:
    """Rescan the tenant's bucket and update the cached totals.

    Explicitly a POST an operator triggers, not something a page load does: the scan is a full
    ListObjectsV2 walk (0045), so making it implicit would put an unbounded S3 walk behind every
    render of the overview.
    """
    return await storage_usage.refresh(session, tenant_id)


@router.patch("/tenants/{tenant_uid}", name="update-tenant")
async def update_tenant(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    body: UpdateTenant,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, object]:
    """Edit a firm's local attributes (project-per-tenant: nothing is written to WorkOS).

    Switching `enabled` off suspends the firm for real: it revokes every live WorkOS session for its
    people AND every subsequent API request from that firm is refused 403 by the request layer
    (`db._refuse_if_suspended`), so this is the incident kill switch, not a label."""
    try:
        return await provisioning.update_tenant(
            session,
            tenant_id=tenant_id,
            name=body.name,
            alias=body.alias,
            website=body.website,
            enabled=body.enabled,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:  # alias already taken
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


class TenantDataJobRequest(BaseModel):
    kind: Literal["reset", "reset_and_seed", "seed_book"]
    confirm_alias: str = Field(min_length=1, max_length=80)


@router.get("/tenants/{tenant_uid}/data-job", name="get-tenant-data-job")
async def get_tenant_data_job(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object] | None:
    await _require_tenant_scope(session, principal, tenant_id, write=False)
    if settings.app_stage == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return await tenant_data.latest(session, tenant_id)


@router.post("/tenants/{tenant_uid}/data-job", name="create-tenant-data-job")
async def create_tenant_data_job(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    body: TenantDataJobRequest,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Queue a non-production operational-data reset, a full wipe-and-demo-seed, or a
    book-only reseed (``seed_book``) that replaces the sample clients and their holdings
    without touching the rest of the firm."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    actor = await _operator_uid(session)
    try:
        return await tenant_data.enqueue(
            session,
            tenant_id=tenant_id,
            kind=body.kind,
            confirm_alias=body.confirm_alias,
            requested_by=str(actor) if actor else None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.post("/tenants/{tenant_uid}/data-job/resume-providers", name="resume-tenant-providers")
async def resume_tenant_providers(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, int]:
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    if settings.app_stage == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant data tools are not available in production",
        )
    try:
        resumed = await tenant_data.resume_refreshes(session, tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"resumed": resumed}


@router.get("/tenants/{tenant_uid}/export", name="export-tenant")
async def export_tenant(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, object]:
    """The offboarding archive for a firm — identity, roles, members and its full audit trail,
    plus row counts for its data. Take this before deleting a firm (FCA record-keeping): the audit
    rows cascade away with the tenant. Gated on `tenants:write`, because it is a bulk extraction
    of one firm's record rather than an ordinary read."""
    try:
        archive = await provisioning.export_tenant(session, tenant_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return archive


@router.delete("/tenants/{tenant_uid}", name="delete-tenant")
async def delete_tenant(
    tenant_uid: str,
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    confirm: str,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, object]:
    """Destroy a firm. Two-step by construction: `confirm` must be the firm's alias (typed by the
    operator) and the firm must already be suspended, so its logins are dead and the export step
    has happened. Deactivates its WorkOS memberships, writes a firm-less audit record that
    survives the cascade, then deletes the tenant (users/environments/data cascade)."""
    try:
        answer = await provisioning.delete_tenant(
            session, tenant_id=tenant_id, confirm_alias=confirm
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:  # wrong confirmation, or not suspended yet
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    # AFTER the delete commits, and only on success: `tenant_id_for_uid` caches uid -> serial for
    # the life of the process, so without this a deleted firm's uid kept resolving and every read
    # under it answered with rows that were gone instead of a 404.
    addressing.forget_tenant(tenant_uid)
    return answer


# ---- a firm's own sending domain (Graphene operations) ----


def _reject_sender_domain(exc: sender_domains.SenderDomainRejected) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/tenants/{tenant_uid}/sender-domain", name="get-sender-domain")
async def get_sender_domain(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> sender_domains.SenderDomainOut | None:
    """The firm's sending domain, or null when operations has not registered one.

    Mail keeps using the platform sender until this domain is verified and has an address.
    """
    return await sender_domains.read_sender_domain(session, tenant_id)


@router.post("/tenants/{tenant_uid}/sender-domain", name="register-sender-domain")
async def register_sender_domain(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    body: sender_domains.RegisterSenderDomain,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> sender_domains.SenderDomainOut:
    """Register a sending subdomain on the shared Resend account and store its DNS records."""
    try:
        return await sender_domains.register_sender_domain(session, tenant_id, body.domain)
    except sender_domains.SenderDomainRejected as exc:
        raise _reject_sender_domain(exc) from exc


@router.put("/tenants/{tenant_uid}/sender-domain/from", name="set-sender-address")
async def set_sender_address(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    body: sender_domains.SetSenderAddress,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> sender_domains.SenderDomainOut:
    """Choose the mailbox mail comes from. It has to sit on the registered domain."""
    try:
        return await sender_domains.set_from_address(session, tenant_id, body.from_address)
    except sender_domains.SenderDomainRejected as exc:
        raise _reject_sender_domain(exc) from exc


@router.post("/tenants/{tenant_uid}/sender-domain/verify", name="verify-sender-domain")
async def verify_sender_domain(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> sender_domains.SenderDomainOut:
    """Start a DNS check at Resend, then store whatever it can see now.

    Verification is asynchronous. A domain that was already verified is marked pending while
    Resend checks again, so this is the button for after the firm's DNS has changed — not a
    refresh of a domain that is already in use.
    """
    try:
        return await sender_domains.refresh_sender_domain(session, tenant_id, start=True)
    except sender_domains.SenderDomainRejected as exc:
        raise _reject_sender_domain(exc) from exc


@router.post("/tenants/{tenant_uid}/sender-domain/refresh", name="refresh-sender-domain")
async def refresh_sender_domain(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> sender_domains.SenderDomainOut:
    """Store Resend's current opinion of the domain without starting a new check."""
    try:
        return await sender_domains.refresh_sender_domain(session, tenant_id, start=False)
    except sender_domains.SenderDomainRejected as exc:
        raise _reject_sender_domain(exc) from exc


@router.delete("/tenants/{tenant_uid}/sender-domain", name="delete-sender-domain")
async def delete_sender_domain(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, bool]:
    """Remove the domain from Resend and from this firm. Mail falls back to the platform sender."""
    try:
        await sender_domains.remove_sender_domain(session, tenant_id)
    except sender_domains.SenderDomainRejected as exc:
        raise _reject_sender_domain(exc) from exc
    return {"removed": True}


@router.post("/tenants/{tenant_uid}/sender-domain/test", name="send-sender-test")
async def send_sender_test(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> sender_domains.SenderTestOut:
    """Mail the acting operator, as this firm would send to a client right now.

    No recipient parameter. The address is the operator's own ``auth.users`` row, so the button
    cannot be aimed at a client. The same hourly cap as a template test applies.
    """
    try:
        return await sender_domains.send_sender_test(session, tenant_id)
    except sender_domains.SenderDomainRejected as exc:
        raise _reject_sender_domain(exc) from exc


# ---- white-label branding (Graphene admin, per tenant) ----


@router.get("/tenants/{tenant_uid}/branding", name="get-branding")
async def get_branding(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    portal: branding_mod.Portal = "client",
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> dict[str, object]:
    """A firm's white-label branding + name, for one portal (client|platform)."""
    return await branding_mod.get_branding(session, tenant_id, portal)


@router.put("/tenants/{tenant_uid}/branding", name="set-branding")
async def set_branding(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    body: SetBranding,
    portal: branding_mod.Portal = "client",
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> dict[str, object]:
    """Set a firm's branding for one portal (structured tokens, validated) + optional name.

    A change to ``Branding.pages`` (portal composition) additionally requires ``pages:write`` —
    the same diff gate as the firm's own ``PUT /firm/branding`` (an operator who may re-brand a
    tenant is not automatically one who may recompose its client portal; ``system`` bypasses).
    A save that echoes the stored pages untouched never trips it."""
    current = await branding_mod.stored_branding(session, tenant_id, portal)
    if branding_mod.pages_changed(current, body.branding):
        require_permission(_p, PAGES_WRITE)
    return await branding_mod.set_branding(
        session, tenant_id, portal=portal, name=body.name, branding=body.branding
    )


@router.post("/tenants/{tenant_uid}/branding/asset", name="upload-branding-asset")
async def upload_branding_asset(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    kind: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
    email_copy: Annotated[UploadFile | None, File()] = None,
) -> dict[str, str]:
    """Upload a branding asset and return its opaque same-origin source.

    ``email_copy`` is optional: the PNG the branding studio draws of an SVG logo or icon, stored
    beside it for email (``app/branding_uploads.py``)."""
    return await _store_branding_asset(session, tenant_id, kind, file, email_copy)


async def _store_branding_asset(
    session: AsyncSession,
    tenant_id: int,
    kind: str,
    file: UploadFile,
    email_copy: UploadFile | None = None,
) -> dict[str, str]:
    """Shared logo/favicon upload: validate, store, return an opaque same-origin source."""
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
            session,
            tenant_id,
            kind,
            data,
            file.content_type or "application/octet-stream",
            email_copy=await branding_uploads.read_email_copy(email_copy),
        )
    except storage.StorageUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Object storage is not configured — paste a hosted https URL instead.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return {"src": src}


@router.post("/tenants/{tenant_uid}/members", name="create-tenant-member")
async def create_tenant_member(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    body: CreateMember,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Invite a person into a firm (WorkOS user + first portal membership + mirror). The portal
    is the role's portal. Add further portal access with the portal grant endpoint."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        return await provisioning.grant_portal_access(
            session,
            tenant_id=tenant_id,
            role_slug=body.role_slug,
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.put("/tenants/{tenant_uid}/members/{user_id}/portals/{portal}", name="grant-member-portal")
async def grant_member_portal(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    user_id: MemberId,
    portal: Portal,
    body: GrantPortal,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Grant an existing person a portal + role (reactivates if previously revoked)."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        return await provisioning.grant_portal_access(
            session,
            tenant_id=tenant_id,
            user_id=str(user_id),
            portal=portal,
            role_slug=body.role_slug,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.delete(
    "/tenants/{tenant_uid}/members/{user_id}/portals/{portal}", name="revoke-member-portal"
)
async def revoke_member_portal(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    user_id: MemberId,
    portal: Portal,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Revoke a person's access to one portal (the user + any other portal access survive)."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        return await provisioning.revoke_portal_access(
            session, tenant_id=tenant_id, user_id=str(user_id), portal=portal
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.patch(
    "/tenants/{tenant_uid}/members/{user_id}/portals/{portal}", name="set-member-portal-role"
)
async def set_member_portal_role(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    user_id: MemberId,
    portal: Portal,
    body: ChangeRole,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Change a person's role within one portal (WorkOS + mirror)."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        return await provisioning.set_member_role(
            session,
            tenant_id=tenant_id,
            user_id=str(user_id),
            portal=portal,
            role_slug=body.role_slug,
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


class ProviderRef(BaseModel):
    """The id one custodian knows a staff member by. Blank clears the mapping."""

    external_id: str = ""


@router.put(
    "/tenants/{tenant_uid}/members/{user_id}/provider-ref/{provider}",
    name="set-member-provider-ref",
)
async def set_member_provider_ref(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    user_id: MemberId,
    provider: str,
    body: ProviderRef,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Record which id a custodian knows this staff member by, or clear it.

    The OPERATIONS door onto `provisioning.set_provider_ref`, which is the one implementation and
    is also reachable by the firm itself at `PUT /users/{user_id}/provider-ref/{provider}`. What a
    mapping is lives there; who may set one, and how the tenant is established, is what differs
    here. (`auth.merge_provider_user` (0123) sets the same map AUTOMATICALLY wherever the
    custodian's address matches a login's — these two routes are for the people it declines to
    guess at.)

    **OPERATOR-SET, NEVER INFERRED, and that is measured rather than cautious.** Only 37 of the 233
    distinct `ACCOUNT_DSR_EMP_ID` values in SEI's ClientAccountLink appear in `UserDetail` at all —
    the rest are adviser PARTIES rather than SEI logins — so matching on a display name would be
    wrong 84% of the time, and the failure would be an account opened naming the wrong servicing
    adviser.

    Tenant-scoped explicitly: `admin_session` is service_role, so the `tenant_id` conjunct on the
    UPDATE is the only thing stopping an operator writing a mapping onto another firm's staff.
    """
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    # `_known_provider` first, so an unrecognised name is refused with THIS route's 404 shape
    # rather than the shared implementation's ValueError.
    known = _known_provider(provider)
    try:
        return await provisioning.set_provider_ref(
            session,
            tenant_id=tenant_id,
            user_id=str(user_id),
            provider=known,
            external_id=body.external_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/tenants/{tenant_uid}/sync", name="sync-tenant")
async def sync_tenant(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, int]:
    """Manually reconcile a firm's mirror from its WorkOS project (roles + memberships).
    A stopgap until webhooks + a scheduled reconcile land. Idempotent.

    Answers `members` (WorkOS org memberships seen), `imported` (people the mirror did not have)
    and `without_portal` (the firm's people holding no portal access). The last two are what make
    a successful sync legible: the reconcile deliberately never invents a portal row, so importing
    26 people and granting none of them anything is the correct outcome and used to be reported as
    a bare success."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        return await provisioning.sync_tenant(session, tenant_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc


@router.get("/tenants/{tenant_uid}/members", name="list-tenant-members")
async def list_tenant_members(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    q: str | None = None,
    role: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> list[dict[str, object]] | dict[str, object]:
    """List a firm's people, one row per person, with the portals they hold (each with its role +
    status). A person may hold Client and/or Platform access independently.

    `q`/`role`/`limit`/`offset` are the firm portal's own contract (`GET /firm/members`), forwarded
    to the same resolver — the operations view of a firm's people must not search differently from
    the firm's own view of them.
    """
    await _require_tenant_scope(session, principal, tenant_id, write=False)
    if limit is None:
        return await provisioning.members_with_portals(
            session, tenant_id, q=q, role_slug=role, offset=offset
        )
    return await provisioning.members_with_portals(
        session, tenant_id, q=q, role_slug=role, limit=limit, offset=offset
    )


@router.get("/tenants/{tenant_uid}/members/by-uid/{user_uid}", name="get-tenant-member-profile")
async def get_tenant_member_profile(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    user_uid: str,
    background: BackgroundTasks,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """One firm's complete auth profile, also used for Graphene's own operations staff.

    Sessions in the response are the rows already stored. WorkOS is asked for that
    person's live sessions only after this response is sent.
    """
    await _require_tenant_scope(session, principal, tenant_id, write=False)
    profile = await directory.member_profile(session, tenant_id, user_uid)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    background.add_task(refresh_member_sessions, tenant_id, str(profile["user_id"]))
    try:
        user = profile.get("user")
        current = (
            principal.session_id
            if isinstance(user, dict) and user.get("workos_user_id") == principal.user_id
            else None
        )
        profile["security"] = await provisioning.member_security_posture(
            session,
            tenant_id=tenant_id,
            user_id=str(profile["user_id"]),
            current_session_id=current,
        )
    except (LookupError, WorkOSError):
        profile["security"] = {"available": False}
    return profile


@router.post("/tenants/{tenant_uid}/members/{user_id}/deactivate", name="deactivate-tenant-member")
async def deactivate_tenant_member(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    user_id: MemberId,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Deactivate a member's firm membership (WorkOS + mirror)."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        return await provisioning.deactivate_member(
            session, tenant_id=tenant_id, user_id=str(user_id)
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.post("/tenants/{tenant_uid}/members/{user_id}/reactivate", name="reactivate-tenant-member")
async def reactivate_tenant_member(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    user_id: MemberId,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Reactivate a deactivated member's firm membership (WorkOS + mirror)."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        return await provisioning.reactivate_member(
            session, tenant_id=tenant_id, user_id=str(user_id)
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.post(
    "/tenants/{tenant_uid}/members/{user_id}/resend-invite", name="resend-tenant-member-invite"
)
async def resend_tenant_member_invite(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    user_id: MemberId,
    portal: Portal | None = None,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Resend a pending invitation (a fresh set-password email). Reports whether the email was
    actually delivered — with Resend unconfigured it is not, and the operator needs to know that
    rather than see a success toast. `portal` targets the link at a specific portal (a dual-portal
    person would otherwise get whichever membership sorted first). The link itself is NOT returned
    here: use the invitation-link endpoint below to reveal it deliberately."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        return await provisioning.resend_member_invite(
            session, tenant_id=tenant_id, user_id=str(user_id), portal=portal
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete(
    "/tenants/{tenant_uid}/members/{user_id}/invitation",
    name="revoke-tenant-member-invitation",
)
async def revoke_tenant_member_invitation(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    user_id: MemberId,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Revoke a pending WorkOS invitation without removing the member's portal grant."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        return await provisioning.revoke_member_invitation(
            session, tenant_id=tenant_id, user_id=str(user_id)
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.post(
    "/tenants/{tenant_uid}/members/{user_id}/password-reset",
    name="send-tenant-member-password-reset",
)
async def send_tenant_member_password_reset(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    user_id: MemberId,
    portal: Portal | None = None,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Email a password-reset link to an active member and end their live sessions."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        return await provisioning.send_member_password_reset(
            session, tenant_id=tenant_id, user_id=str(user_id), portal=portal
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.post(
    "/tenants/{tenant_uid}/members/{user_id}/invitation-link",
    name="reveal-tenant-member-invitation",
)
async def reveal_tenant_member_invitation(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    user_id: MemberId,
    portal: Portal | None = None,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Mint and RETURN a set-password link for a member — the escape hatch for when email cannot
    be delivered.

    Deliberately its own endpoint. The link is a live credential that sets someone's password, so
    it must never ride along on ordinary invite/resend responses (from where it reaches proxy logs,
    browser history and screenshots); an operator asks for it explicitly, it supersedes the
    previous link, and the reveal is recorded in the audit trail with the actor.

    ``tenant_id`` is the authorised scope AND the lookup predicate — the permission check below is
    against the path tenant, so the mint must be confined to that tenant's own logins or the path
    becomes a cross-firm credential-minting oracle (`provisioning.resend_member_invite`)."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        return await provisioning.resend_member_invite(
            session, tenant_id=tenant_id, user_id=str(user_id), portal=portal, reveal_link=True
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/tenants/{tenant_uid}/members/{user_id}/sessions/{session_id}",
    name="revoke-tenant-member-session",
)
async def revoke_tenant_member_session(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    user_id: MemberId,
    session_id: str,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """End one of this member's live sessions. The id must be on their own list."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        return await provisioning.revoke_member_session(
            session, tenant_id=tenant_id, user_id=str(user_id), session_id=session_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


@router.post("/tenants/{tenant_uid}/members/{user_id}/reset-mfa", name="reset-tenant-member-mfa")
async def reset_tenant_member_mfa(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    user_id: MemberId,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Clear a member's enrolled MFA factors so they re-enrol at their next sign-in — the recovery
    path for a lost authenticator (there are no recovery codes). Audited; also ends their live
    sessions so the next sign-in goes through enrolment."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        return await provisioning.reset_member_mfa(
            session, tenant_id=tenant_id, user_id=str(user_id)
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkOSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message
        ) from exc


# ── tenant email-template authoring (the shared editor's admin door) ───────────────────────────
# Graphene operations authors a tenant's email templates through the admin surface — above all the
# MODEL tenant, whose published preset every new firm clones on creation (there are no model-tenant
# logins, so it cannot be reached through the firm-facing /email-templates router). Persistence is
# the SAME service the firm router uses (app/email/templates_service.py) — one copy of the SQL, no
# drift — and the operations permission is chosen by the TARGET tenant via `_require_tenant_scope`
# (model tenant → model:write, a firm → tenants:write). Rows are attributed to the acting operator.
#
# Every write from this door declares `scope="operations"` (0051): the version row records that
# GRAPHENE authored it, not the firm, so a firm reading its own version history can tell an edit
# made on its behalf from one its own staff made — `created_by` resolves to a person's name, which
# cannot carry that fact. The firm router passes `scope="firm"`; the service never guesses.


async def _operator_uid(session: AsyncSession) -> str:
    """The acting operator's uuid (``auth.uid()`` under admin_session), for created/published_by."""
    uid = (await session.execute(text("SELECT auth.uid()"))).scalar_one_or_none()
    if uid is None:  # pragma: no cover — admin_session always publishes the actor's claims
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No acting operator"
        )
    return str(uid)


@router.get("/tenants/{tenant_uid}/email-templates", name="admin-list-email-templates")
async def admin_list_email_templates(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> list[EmailTemplateOut]:
    """A tenant's email templates (newest first) — the admin editor list (e.g. the model preset)."""
    await _require_tenant_scope(session, principal, tenant_id, write=False)
    rows = await email_templates_service.list_templates(session, tenant_id)
    return [EmailTemplateOut(**r) for r in rows]


@router.post(
    "/tenants/{tenant_uid}/email-templates",
    name="admin-create-email-template",
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_email_template(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    body: CreateEmailTemplate,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> EmailTemplateOut:
    """Create a template (+ an empty draft v1) for the target tenant."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    actor = await _operator_uid(session)
    try:
        created = await email_templates_service.create_template(
            session,
            tenant_id,
            actor,
            code=body.code,
            name=body.name,
            category=body.category,
            description=body.description,
            scope="operations",
            subjects=body.subjects,
        )
    except email_templates_service.EmailTemplateError as exc:
        _raise_email_http(exc)
    return EmailTemplateOut(**created)


@router.get("/tenants/{tenant_uid}/email-templates/catalog", name="admin-email-template-catalog")
async def admin_email_template_catalog(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> list[EmailCodeOut]:
    """The catalogue of send-path codes (the selectable email types). Declared before ``/{uid}``."""
    await _require_tenant_scope(session, principal, tenant_id, write=False)
    return [
        EmailCodeOut(code=c.code, label=c.label, category=c.category, description=c.description)
        for c in email_catalog.KNOWN_CODES
    ]


@router.post(
    "/tenants/{tenant_uid}/email-templates/load-defaults",
    name="admin-email-templates-load-defaults",
)
async def admin_email_templates_load_defaults(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    refresh: bool = False,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, int]:
    """Seed the target tenant with the built-in defaults for every catalogue code, as published v1s
    (idempotent — skips codes it already has). This is how an operator populates the MODEL tenant's
    preset (there is no system user to auto-seed at startup); it is attributed to the operator.

    ``refresh=true`` also brings an UNTOUCHED preset up to the current shipped default, by
    publishing a new version over it. Seeding alone can only ever create, so a preset seeded weeks
    ago stayed as it was for ever — and since every new firm CLONES the model tenant's presets, the
    model tenant's age is the age of every firm created after it. A preset with any firm-authored
    version in its history is reported as ``authored`` and never touched; see
    ``templates_service.refresh_defaults``.

    A QUERY FLAG, not a second route, so both acts keep one operation id and one audit event — the
    action stream records the query params, so which of the two an operator performed is on the
    row. The answer names every outcome (``created``/``refreshed``/``authored``/``current``)
    because "0 created" on a tenant that needed refreshing reads as "nothing to do"."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    actor = await _operator_uid(session)
    if refresh:
        return await email_templates_service.refresh_defaults(session, tenant_id, actor)
    created = await email_templates_service.load_defaults(session, tenant_id, actor)
    return {"created": created}


@router.post(
    "/tenants/{tenant_uid}/email-templates/provision", name="admin-email-templates-provision"
)
async def admin_email_templates_provision(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, int]:
    """Backfill an EXISTING tenant with the model tenant's published preset (idempotent — skips
    codes it already customised). The clone that new tenants get automatically, on demand."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    cloned = await provisioning.clone_model_email_templates(session, tenant_id)
    return {"cloned": cloned}


class _ReseedRefused(Exception):
    """Carries the publish issues out of the savepoint so the `async with` rolls it back."""

    def __init__(self, issues: list[str]) -> None:
        super().__init__("; ".join(issues))
        self.issues = issues


# TEMPORARY (Sept 2026). A stop-gap for bringing a firm seeded weeks ago up to the onboarding
# definition this build ships, until the seed has a real migration story. Delete the route, its
# EVENT_TYPES entry, the admin form action and its button together.
@router.post("/tenants/{tenant_uid}/flows/onboarding/reseed", name="reseed-onboarding-flow")
async def admin_reseed_onboarding_flow(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Re-run the built-in onboarding seed against an existing tenant.

    A firm gets the seeded journey once, at creation (`provisioning.provision_tenant`), and never
    again -- so every firm created before a seed change is frozen at the definition that shipped
    that day, with no way forward but SQL or the CLI. This is that one act on demand, the same
    shape as `admin-email-templates-provision` one door up.

    WHY IT IS SAFE FOR A FIRM WITH JOURNEYS IN FLIGHT, the only question that really matters:

    * The seed is EXPAND-ONLY. `onboarding_seed._republish` appends a new `flows.flow_versions`
      row and repoints `flows.flows.current_published_version_id`. It rewrites no version and
      deletes nothing.
    * A running journey is VERSION-PINNED. `flows.executions.flow_version_id` is NOT NULL
      ON DELETE RESTRICT, and every consumer reads its definition through
      `orchestrator._pinned_definition` rather than through the published pointer -- so a reseed
      is invisible to work already underway, and only journeys started afterwards get the new
      spine.
    * It is idempotent BY CONTENT. `_republish` deep-compares the stored definition against the
      freshly built one and writes nothing when they match, so pressing twice costs one read.
    * The CLI `--reset` (drop the flow, its forms and its terms) is deliberately NOT reachable
      here. `flows.cases` and `flows.executions` cascade from `flows.flows`, so that path would
      take a firm's entire onboarding history with it. It stays a script for a tenant with
      nothing in flight -- see `scripts/seed_onboarding.py`.

    AND THE ONE THING THAT IS NOT SAFE, which is why this is non-production only:
    `_write_form_fields` DELETEs and re-inserts the six seeded forms' fields from the hard-coded
    list, so a firm edit to one of those forms -- a relabelled field, an added one, a changed
    rule -- is lost, with no version history to restore it from (unlike an email template). Stored
    ANSWERS survive, because `documents.form_responses.answers` is keyed by `field_key` and not by
    `form_fields.id`. A client part-way through a form still sees it change under them, so the
    count of live journeys comes back in the answer for the operator to weigh rather than being
    left for them to go and look up.

    The seed publishes through its own path, which runs `validate_definition` and none of the
    DB-backed preflights the authored publish runs -- so it can install a definition the builder
    would then refuse to republish, a trap an operator cannot see and cannot fix from the UI. This
    runs the full set on the result inside a SAVEPOINT and rolls back rather than leave that
    behind. The tenant is passed explicitly rather than going through
    `flows._all_publish_issues`, which reads `auth.tenant_id()`: on this surface that is the
    OPERATOR's tenant, so it would check the wrong firm's forms and pass for the wrong reason.
    """
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    # Non-production only, read off the stage rather than a feature flag -- and `app_stage`
    # defaults to "production", so an unconfigured deployment refuses. That is the right direction
    # for a temporary tool that overwrites a firm's own form authoring.
    if settings.app_stage == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"APP_STAGE={settings.app_stage!r} is not a development stage - reseeding "
                "replaces the seeded forms' fields, so it is not offered in production."
            ),
        )

    from app.flows import definitions as flow_definitions
    from app.flows import states as flow_states
    from app.flows.onboarding_seed import FLOW_CODE, seed_onboarding
    from app.routers.flows import _automation_issues, _handler_issues, _reference_issues

    in_flight = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM flows.executions e "
                    "JOIN flows.flows f ON f.id = e.flow_id "
                    "WHERE f.tenant_id = :t AND f.code = :c "
                    "  AND NOT (e.status = ANY(:terminal))"
                ),
                {
                    "t": tenant_id,
                    "c": FLOW_CODE,
                    "terminal": [s.value for s in flow_states.TERMINAL],
                },
            )
        ).scalar_one()
    )

    try:
        async with session.begin_nested():
            seeded = await seed_onboarding(session, tenant_id=tenant_id)
            raw_definition = (
                await session.execute(
                    text(
                        "SELECT fv.definition FROM flows.flows f "
                        "JOIN flows.flow_versions fv ON fv.id = f.current_published_version_id "
                        "WHERE f.tenant_id = :t AND f.code = :c"
                    ),
                    {"t": tenant_id, "c": FLOW_CODE},
                )
            ).scalar_one()
            definition = (
                json.loads(raw_definition) if isinstance(raw_definition, str) else raw_definition
            )
            issues = (
                flow_definitions.publish_issues_detailed(definition)
                + await _reference_issues(session, definition)
                + await _handler_issues(session, tenant_id, definition)
                + await _automation_issues(session, tenant_id, definition, FLOW_CODE)
            )
            if issues:
                raise _ReseedRefused([getattr(i, "message", str(i)) for i in issues])
    except _ReseedRefused as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "The seeded definition would be refused at publish, so nothing was changed: "
                + "; ".join(exc.issues)
            ),
        ) from exc

    forms = seeded.get("forms")
    return {
        "flow_uid": seeded["flow_uid"],
        "created": seeded["created"],
        "updated": seeded["updated"],
        "in_flight": in_flight,
        "forms": len(forms) if isinstance(forms, dict) else 0,
    }


# DECLARED BEFORE `/{uid}`, and that is load-bearing: FastAPI matches in declaration order, so
# below it this path resolves as `uid="images"` and answers "Email template not found" — which
# reads to the editor as a firm with no image library rather than as a routing mistake.
# `name` does NOT repeat the `admin` tag — see the sibling POST's note.
@router.get("/tenants/{tenant_uid}/email-templates/images", name="list-email-template-images")
async def admin_list_email_template_images(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    token: str | None = None,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> EmailImageLibraryOut:
    """A tenant's own email image library, for the editor's picker on the ops side.

    The firm door scopes the bucket by the SESSION and can name no other tenant; this one names the
    tenant in the path, so it is gated by that tenant's READ scope (model preset → ``model:read``, a
    firm → ``tenants:read``) exactly as preview is. Without it an operator curating a firm's emails
    gets the link field alone, and every image they place has to be a URL found somewhere else."""
    await _require_tenant_scope(session, principal, tenant_id, write=False)
    return await list_images(session, tenant_id, token)


async def admin_upload_email_template_image(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    file: UploadFile = File(...),
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> UploadedEmailImageOut:
    """Add an image to a tenant's library on their behalf.

    The target tenant's WRITE scope, because the object lands in that firm's own public prefix — the
    same bar the ops-side branding upload clears, and the same one send-test clears for spending a
    firm's email reputation."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    return await store_image(session, tenant_id, file)


# Imperative for the asset body ceiling, as on the firm door: the decorator form cannot pass a route
# class, and the allowance must not leak onto the template-save routes.
router.add_api_route(
    "/tenants/{tenant_uid}/email-templates/images",
    admin_upload_email_template_image,
    methods=["POST"],
    # NOT "admin-upload-…": the tag is already `admin`, and `admin-admin-…` is the legacy
    # shape `test_actions_tripwire` freezes a count of and asks for no more of.
    name="upload-email-template-image",
    status_code=status.HTTP_201_CREATED,
    route_class_override=_ImageUploadRoute,
)


@router.get("/tenants/{tenant_uid}/email-templates/{uid}", name="admin-get-email-template")
async def admin_get_email_template(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    uid: str,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> EmailTemplateOut:
    """One template's metadata."""
    await _require_tenant_scope(session, principal, tenant_id, write=False)
    template = await email_templates_service.fetch(session, tenant_id, uid)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Email template not found"
        )
    return EmailTemplateOut(**template)


@router.patch("/tenants/{tenant_uid}/email-templates/{uid}", name="admin-update-email-template")
async def admin_update_email_template(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    uid: str,
    body: UpdateEmailTemplate,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> EmailTemplateOut:
    """Update a template's metadata. Partial; ``description`` is clearable with an explicit ``""``
    (the firm door's contract — see ``routers/email_templates.py::update_email_template``)."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        updated = await email_templates_service.update_template(
            session,
            tenant_id,
            uid,
            name=body.name,
            code=body.code,
            category=body.category,
            description=body.description,
            description_set="description" in body.model_fields_set,
        )
    except email_templates_service.EmailTemplateError as exc:
        _raise_email_http(exc)
    return EmailTemplateOut(**updated)


@router.delete("/tenants/{tenant_uid}/email-templates/{uid}", name="delete-email-template")
async def admin_delete_email_template(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    uid: str,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> EmailTemplateOut:
    """Delete a template and its version history on a firm's behalf. Irreversible.

    Runs as ``service_role``, so the tenant is the one named in the path rather than the session's
    own — every other write on this surface is scoped the same way, by ``_require_tenant_scope``.
    """
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    try:
        template = await email_templates_service.delete_template(session, tenant_id, uid)
    except email_templates_service.EmailTemplateError as exc:
        _raise_email_http(exc)
    return EmailTemplateOut(**template)


@router.get(
    "/tenants/{tenant_uid}/email-templates/{uid}/default", name="admin-email-template-default"
)
async def admin_email_template_default(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    uid: str,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> EmailDefaultOut:
    """The built-in default for this template's code (token HTML), for seeding a blank canvas."""
    await _require_tenant_scope(session, principal, tenant_id, write=False)
    template = await email_templates_service.fetch(session, tenant_id, uid)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Email template not found"
        )
    default = email_defaults.default_for(str(template["code"]))
    if default is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No built-in default for this template's code.",
        )
    return EmailDefaultOut(
        subject=default.subject,
        preheader=default.preheader,
        html=default.html,
        css=default.css,
        source=default.source,
    )


@router.post(
    "/tenants/{tenant_uid}/email-templates/{uid}/preview", name="admin-preview-email-template"
)
async def admin_preview_email_template(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    uid: str,
    body: PreviewEmail,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> PreviewEmailOut:
    """Render the editor's content against sample values in the tenant's brand (never sends)."""
    await _require_tenant_scope(session, principal, tenant_id, write=False)
    template = await email_templates_service.fetch(session, tenant_id, uid)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Email template not found"
        )
    return await build_preview(session, tenant_id, str(template["code"]), body)


@router.post(
    "/tenants/{tenant_uid}/email-templates/{uid}/send-test", name="admin-send-test-email-template"
)
async def admin_send_test_email_template(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    uid: str,
    body: SendTestEmail,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> SendTestEmailOut:
    """Mail the editor's current content to the ACTING OPERATOR'S OWN address — the inbox test for
    the MODEL tenant's preset, which has no firm logins and so cannot be tested through the firm
    door at all. Same rule as the firm surface: no recipient parameter, the address comes from the
    operator's own ``auth.users`` row, and any address in the body is ignored.

    Dressed in and sent AS the target tenant (``resolve_sender``/``brand_for`` on ``tenant_id``),
    matching the preview above — an operator curating a firm's template needs to see what THAT
    firm's mail looks like, not Graphene's. Requires the target tenant's WRITE scope (model tenant →
    ``model:write``, a firm → ``tenants:write``), because it spends that firm's email reputation.

    Refuses a blank subject with 422, the same gate publish applies (the shared ``send_test``
    enforces it, so neither door can mail a subjectless ``[TEST] ``)."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    template = await email_templates_service.fetch(session, tenant_id, uid)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Email template not found"
        )
    return await send_test(session, tenant_id, str(template["code"]), body)


@router.get(
    "/tenants/{tenant_uid}/email-templates/{uid}/versions",
    name="admin-list-email-template-versions",
)
async def admin_list_email_template_versions(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    uid: str,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> list[EmailVersionOut]:
    """A template's version history, newest first."""
    await _require_tenant_scope(session, principal, tenant_id, write=False)
    try:
        rows = await email_templates_service.list_versions(session, tenant_id, uid)
    except email_templates_service.EmailTemplateError as exc:
        _raise_email_http(exc)
    return [EmailVersionOut(**r) for r in rows]


@router.get(
    "/tenants/{tenant_uid}/email-templates/{uid}/versions/{vid}",
    name="admin-get-email-template-version",
)
async def admin_get_email_template_version(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    uid: str,
    vid: str,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> EmailVersionDetailOut:
    """One version, including its editor content (``schema``)."""
    await _require_tenant_scope(session, principal, tenant_id, write=False)
    try:
        row = await email_templates_service.get_version(session, tenant_id, uid, vid)
    except email_templates_service.EmailTemplateError as exc:
        _raise_email_http(exc)
    return EmailVersionDetailOut(**row)


@router.post(
    "/tenants/{tenant_uid}/email-templates/{uid}/versions", name="admin-save-email-template-version"
)
async def admin_save_email_template_version(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    uid: str,
    body: SaveEmailVersion,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> EmailVersionDetailOut:
    """Save the editor's content (autosave); rolls a fresh draft once head is published/stale."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    actor = await _operator_uid(session)
    try:
        row = await email_templates_service.save_version(
            session,
            tenant_id,
            actor,
            uid,
            subject=body.subject,
            preheader=body.preheader,
            schema=body.schema_content,
            scope="operations",
        )
    except email_templates_service.EmailTemplateError as exc:
        _raise_email_http(exc)
    return EmailVersionDetailOut(**row)


@router.post(
    "/tenants/{tenant_uid}/email-templates/{uid}/versions/{vid}/publish",
    name="admin-publish-email-template-version",
)
async def admin_publish_email_template_version(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    uid: str,
    vid: str,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> PublishEmailResultOut:
    """Publish a draft (requires subject + visible body); points the template at it.

    Answers the same soft `unsourced` warning as the firm door — an operator authoring the model
    tenant's presets is exactly who should see that a preset ships a token nothing fills."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    actor = await _operator_uid(session)
    try:
        row = await email_templates_service.publish_version(
            session, tenant_id, actor, uid, vid, scope="operations"
        )
    except email_templates_service.EmailTemplateError as exc:
        _raise_email_http(exc)
    return PublishEmailResultOut(**row)


@router.post(
    "/tenants/{tenant_uid}/email-templates/{uid}/versions/{vid}/restore",
    name="admin-restore-email-template-version",
)
async def admin_restore_email_template_version(
    tenant_id: Annotated[int, Depends(admin_tenant_id)],
    uid: str,
    vid: str,
    session: AsyncSession = Depends(admin_session),
    principal: Principal = Depends(get_current_principal),
) -> EmailVersionDetailOut:
    """Copy an earlier version into a new draft (never overwrites / auto-publishes)."""
    await _require_tenant_scope(session, principal, tenant_id, write=True)
    actor = await _operator_uid(session)
    try:
        row = await email_templates_service.restore_version(
            session, tenant_id, actor, uid, vid, scope="operations"
        )
    except email_templates_service.EmailTemplateError as exc:
        _raise_email_http(exc)
    return EmailVersionDetailOut(**row)
