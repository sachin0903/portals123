"""Tenant WorkOS environment registration + secret handling (project-per-tenant, §0).

An operator creates the WorkOS project/environment in the dashboard, then registers it here
(via the admin UI): the non-secret routing config lands in ``auth.tenant_environments`` and
the api key + cookie password are Fernet-encrypted into ``auth.environment_secrets``. The
shared portal later fetches the decrypted config for its request host from the internal
endpoint (``env_config_by_host``). All writes run as ``service_role`` (admin_session).
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from workos import AsyncWorkOSClient
from workos._errors import ConflictError, UnprocessableEntityError

from app import branding, crypto, shell_images, storage
from app.config import settings

# vite :5173/:5175/:5174 ↔ compose :3000/:3001/:3002. The host column is UNIQUE.
_LOCAL_PORT_PAIRS = ((5173, 3000), (5175, 3001), (5174, 3002))


def local_host_alias(host: str) -> str:
    """Loopback compose/vite port counterpart, else ``host``."""
    name, sep, port_s = host.rpartition(":")
    if not sep or name not in ("localhost", "127.0.0.1"):
        return host
    try:
        port = int(port_s)
    except ValueError:
        return host
    for vite, compose in _LOCAL_PORT_PAIRS:
        if port == vite:
            return f"{name}:{compose}"
        if port == compose:
            return f"{name}:{vite}"
    return host


def core_workos_client() -> AsyncWorkOSClient:
    """A WorkOS client for the platform's own (core) project — from settings, not a tenant's
    stored key. Used for internal-operations staff/roles (Graphene ops), which live in the
    core project, not any firm's."""
    return AsyncWorkOSClient(api_key=settings.workos_api_key, client_id=settings.workos_client_id)


async def operations_tenant_id(session: AsyncSession) -> int:
    """The tenant that owns the core WorkOS project (Graphene Platforms) — the home for
    internal-operations staff/roles. Resolved by matching the configured core client id, so it
    isn't hard-coded to a row id. Raises LookupError if the core environment isn't registered."""
    tid = (
        await session.execute(
            text(
                "SELECT tenant_id FROM auth.tenant_environments "
                "WHERE workos_client_id = :core ORDER BY tenant_id LIMIT 1"
            ),
            {"core": settings.workos_client_id},
        )
    ).scalar_one_or_none()
    if tid is None:
        raise LookupError("the core WorkOS environment (Graphene Platforms) is not registered")
    return int(tid)


def workos_org_slug(slug: str) -> str:
    """WorkOS requires organisation-role slugs to be prefixed with `org-`. We keep the clean
    slug in our mirror and only prefix it for the WorkOS calls (idempotent)."""
    return slug if slug.startswith("org-") else f"org-{slug}"


async def list_environments(session: AsyncSession, tenant_id: int) -> list[dict[str, Any]]:
    """A tenant's registered environments (non-secret fields + whether keys/orgs are set).
    Never returns the secrets themselves."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT te.id, te.uid, te.stage, te.workos_client_id, te.issuer, "
                    "       te.client_portal_host, te.platform_host, te.operations_host, "
                    "       te.firm_org_id, "
                    "       te.client_portal_client_id, te.platform_client_id, "
                    "       te.operations_client_id, "
                    "       (s.workos_api_key_ct IS NOT NULL AND s.cookie_password_ct IS NOT NULL) "
                    "         AS has_secrets, "
                    # Per-portal APPLICATION secret presence — the onboarding checklist's tick
                    # source: a portal signs in only once its app id AND secret are both stored.
                    "       (s.client_portal_secret_ct IS NOT NULL) AS has_client_portal_secret, "
                    "       (s.platform_secret_ct IS NOT NULL) AS has_platform_secret, "
                    "       (s.operations_secret_ct IS NOT NULL) AS has_operations_secret "
                    "FROM auth.tenant_environments te "
                    "LEFT JOIN auth.environment_secrets s ON s.tenant_environment_id = te.id "
                    "WHERE te.tenant_id = :t ORDER BY te.stage"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def env_workos_client(session: AsyncSession, tenant_environment_id: int) -> AsyncWorkOSClient:
    """Build a WorkOS client bound to a tenant environment (its own client_id + decrypted
    api key). Used for per-environment provisioning — e.g. creating its portal orgs."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT te.workos_client_id, s.workos_api_key_ct "
                    "FROM auth.tenant_environments te "
                    "LEFT JOIN auth.environment_secrets s ON s.tenant_environment_id = te.id "
                    "WHERE te.id = :id"
                ),
                {"id": tenant_environment_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or not row["workos_api_key_ct"]:
        raise LookupError("environment has no stored WorkOS api key")
    return AsyncWorkOSClient(
        api_key=crypto.decrypt(row["workos_api_key_ct"]),
        client_id=row["workos_client_id"] or "",
    )


async def client_for_env(session: AsyncSession, tenant_environment_id: int) -> AsyncWorkOSClient:
    """The WorkOS client for an environment's project. The platform's own (core) project uses
    the key from settings; every other tenant project uses its stored, decrypted key."""
    client_id = (
        await session.execute(
            text("SELECT workos_client_id FROM auth.tenant_environments WHERE id = :e"),
            {"e": tenant_environment_id},
        )
    ).scalar_one_or_none()
    if client_id and client_id == settings.workos_client_id:
        return core_workos_client()
    return await env_workos_client(session, tenant_environment_id)


async def portal_base_url(session: AsyncSession, tenant_id: int, portal: str) -> str | None:
    """The base URL of the portal a member of `portal` signs in on — used to build email
    links (set-password, verify) that point at our own portal, not AuthKit. Operations uses
    the configured ops portal URL; client/platform use the registered host. None if unknown."""
    if portal == "operations":
        return settings.operations_portal_url or None
    column = "client_portal_host" if portal == "client" else "platform_host"
    env_id = await primary_environment_id(session, tenant_id)
    host = (
        await session.execute(
            text(f"SELECT {column} FROM auth.tenant_environments WHERE id = :e"),  # noqa: S608 - column from a fixed 2-value branch, not user input
            {"e": env_id},
        )
    ).scalar_one_or_none()
    return f"https://{host}" if host else None


async def primary_environment_id(session: AsyncSession, tenant_id: int) -> int:
    """The tenant's live environment id for THIS instance — the row for this instance's stage
    (settings.app_stage), else the sole/first one. Project-per-tenant: WorkOS writes for a tenant
    (invites, memberships, org roles) target this environment's project. Raises LookupError if
    none is registered."""
    env_id = (
        await session.execute(
            text(
                "SELECT id FROM auth.tenant_environments WHERE tenant_id = :t "
                "ORDER BY (stage = :stage) DESC, id LIMIT 1"
            ),
            {"t": tenant_id, "stage": settings.app_stage},
        )
    ).scalar_one_or_none()
    if env_id is None:
        raise LookupError("tenant has no registered WorkOS environment")
    return int(env_id)


async def env_client_for_tenant(session: AsyncSession, tenant_id: int) -> AsyncWorkOSClient:
    """A WorkOS client bound to the tenant's live environment (its own project key)."""
    return await env_workos_client(session, await primary_environment_id(session, tenant_id))


async def firm_org_and_client(
    session: AsyncSession, *, tenant_id: int
) -> tuple[str, AsyncWorkOSClient]:
    """The tenant's ONE WorkOS org (its people's authentication home) + a client to act on it.

    One-firm-org: the portal is the APPLICATION, never an org, so every portal's members live in
    this single org — Graphene's own operations staff included (the ops tenant's firm org lives in
    the core project, whose client comes from settings via ``client_for_env``). Raises LookupError
    when the environment or its firm org isn't set up yet."""
    env_id = await primary_environment_id(session, tenant_id)
    org_id = (
        await session.execute(
            text("SELECT firm_org_id FROM auth.tenant_environments WHERE id = :e"),
            {"e": env_id},
        )
    ).scalar_one_or_none()
    if not org_id:
        raise LookupError("environment has no firm org yet — set the environment's keys first")
    return str(org_id), await client_for_env(session, env_id)


async def ensure_firm_org(
    session: AsyncSession, workos: AsyncWorkOSClient, *, tenant_environment_id: int
) -> str:
    """Find-or-create the SINGLE firm org for an environment (one-firm-org, 0096) and record its id.

    Idempotent against WorkOS (matches an existing org by the firm's name before creating), so a DB
    reset never mints a duplicate. Under project-per-tenant the firm's own project holds only its
    orgs, so matching by name is safe. Members join this one org (which lets them authenticate);
    which portals they may use is `auth.organisation_memberships.portal`. Returns the org id.

    Alternative the operator may prefer at cutover: repurpose the existing Platform org by setting
    `firm_org_id = platform_org_id` directly, avoiding a membership migration."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT te.firm_org_id, t.name FROM auth.tenant_environments te "
                    "JOIN auth.tenants t ON t.id = te.tenant_id WHERE te.id = :id"
                ),
                {"id": tenant_environment_id},
            )
        )
        .mappings()
        .one()
    )
    if row["firm_org_id"]:
        return str(row["firm_org_id"])
    name = str(row["name"])
    org_id: str | None = None
    page = await workos.organizations.list_organizations()
    async for o in page.auto_paging_iter():
        if o.name == name:
            org_id = o.id
            break
    if not org_id:
        org_id = (await workos.organizations.create_organization(name=name)).id
    # The firm org is a property of the tenant's PROJECT: stamp every environment row that does
    # not already carry one, so a host on any row signs into the same org.
    await session.execute(
        text(
            "UPDATE auth.tenant_environments SET firm_org_id = :oid "
            "WHERE firm_org_id IS NULL AND tenant_id = "
            "  (SELECT tenant_id FROM auth.tenant_environments WHERE id = :id)"
        ),
        {"oid": org_id, "id": tenant_environment_id},
    )
    return org_id


async def ensure_workos_org_role(
    session: AsyncSession,
    *,
    tenant_id: int,
    slug: str,
    name: str | None = None,
    description: str | None = None,
    known: dict[str, str] | None = None,
) -> str | None:
    """The WorkOS org role for one of this tenant's mirror roles — created if absent, adopted if
    present — and `auth.roles.workos_role_id` backfilled with it.

    WHY THIS IS SHARED, and why a grant calls it. WorkOS VALIDATES the `role_slug` when a member is
    added to an organisation: an unknown slug is a 422 `invalid_role`, not a warning. Our own
    authorisation never reads that role (permissions come from `auth.member_permissions` on every
    request, and which portals a person may use lives exclusively in
    `auth.organisation_memberships`) -- but the membership cannot be created without a slug WorkOS
    recognises, so the role has to exist there even though nothing reads it.

    THE BUG THIS FIXES. `create_org_role` creates the WorkOS role, so a hand-authored role was
    fine. A tenant's STARTING roles do not come from there: `provisioning.clone_model_tenant_roles`
    copies them from the model tenant with `INSERT ... SELECT`, one SQL statement that touches no
    vendor -- correctly, because tenant creation must not depend on a WorkOS round trip per role.
    The consequence was that every cloned tenant had roles with `workos_role_id IS NULL` and no
    `org-*` counterpart, so `grant_portal_access` 422'd for EVERY portal grant, which is the
    app-per-portal model's central operation. Verified live: all five of a real tenant's roles had
    a NULL `workos_role_id`, and the org's actual slugs were an unrelated hand-built set.

    Ensuring HERE rather than at clone time is deliberate: it self-heals the tenants that already
    exist, and keeps tenant creation off the WorkOS critical path.

    `known` is `{slug: role_id}` for the org's roles when the CALLER has already listed them --
    which `push_roles_to_workos` has, since it walks every role of one tenant. Without it a
    14-role push made 14 identical `GET /authorization/organizations/{org}/roles` calls, visible
    as a wall of them in the log. A single-role caller passes nothing and pays one listing.

    Returns the WorkOS role id, or None for the model tenant (which has no org).
    """
    is_model = bool(
        (
            await session.execute(
                text("SELECT is_model FROM auth.tenants WHERE id = :t"), {"t": tenant_id}
            )
        ).scalar_one_or_none()
    )
    if is_model:
        return None  # the model tenant has no WorkOS project; its roles are mirror-only
    worg, workos = await firm_org_and_client(session, tenant_id=tenant_id)
    wslug = workos_org_slug(slug)

    # Adopt first. A slug that is already there is the common case for an existing tenant, and
    # asking costs one call either way -- unless the caller already asked, hence `known`.
    if known is not None:
        found = known.get(wslug)
    else:
        roles = (await workos.authorization.list_organization_roles(worg)).data
        found = next((r.id for r in roles if r.slug == wslug), None)
    if found is not None:
        workos_role_id: str = found
    else:
        try:
            workos_role_id = (
                await workos.authorization.create_organization_role(
                    worg, name=name or slug, slug=wslug, description=description
                )
            ).id
        except (ConflictError, UnprocessableEntityError):
            # A duplicate slug is a `ConflictError` -- checked against the live API, which answers
            # `organization_role_slug_conflict`. `UnprocessableEntityError` is caught beside it
            # because `create_org_role` used to name that one ALONE for this case, on a comment
            # that said "WorkOS already has this slug"; that branch could never have fired, and
            # catching both costs nothing. Either way: re-read before giving up, because a lost
            # race is recoverable and a genuinely bad slug is not.
            roles = (await workos.authorization.list_organization_roles(worg)).data
            raced = next((r for r in roles if r.slug == wslug), None)
            if raced is None:
                raise
            workos_role_id = raced.id
            if known is not None:
                known[wslug] = workos_role_id  # a stale map must not re-raise on the next slug

    # Backfill, so the next grant needs no vendor call and `list_org_roles` reports the truth.
    await session.execute(
        text(
            "UPDATE auth.roles SET workos_role_id = :wid "
            "WHERE tenant_id = :t AND slug = :s AND workos_role_id IS DISTINCT FROM :wid"
        ),
        {"wid": workos_role_id, "t": tenant_id, "s": slug},
    )
    return workos_role_id


async def push_roles_to_workos(session: AsyncSession, tenant_id: int) -> int:
    """Create the tenant's mirror roles in its WorkOS project — each on the portal org matching
    its portal. Idempotent: a role that already exists in WorkOS is left in place. Called once
    the environment's keys are set, so a tenant's cloned/model-derived roles land in WorkOS.
    Returns the number pushed.

    Deliberately does NOT push permissions (no `set_organization_role_permissions` call): WorkOS
    embeds a role's assigned permissions straight into every access token it mints for that role,
    which is what pushed some sealed session cookies past the browser's ~4096-byte ceiling
    (docs/auth.md) — this app is the sole source of truth for a role's permissions
    (`app/rbac.py`), read fresh from the DB on every request rather than from the token.

    Uses ``client_for_env`` (not ``env_workos_client``) so the platform's own core project — whose
    key comes from settings rather than a stored ciphertext — reconciles too; otherwise the ops
    tenant's "Sync from WorkOS" raised LookupError("environment has no stored WorkOS api key").
    Every role — operations included — lives on the ONE firm org (portals are applications)."""
    env_id = await primary_environment_id(session, tenant_id)
    firm = (
        await session.execute(
            text("SELECT firm_org_id FROM auth.tenant_environments WHERE id = :e"),
            {"e": env_id},
        )
    ).scalar_one_or_none()
    if not firm:
        return 0  # no firm org yet — nothing to push roles onto

    roles = (
        (
            await session.execute(
                text(
                    "SELECT r.id, r.slug, r.name, r.description, r.portal "
                    "FROM auth.roles r WHERE r.tenant_id = :t"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )

    # One shared ensure-or-adopt (above), rather than a second create-and-swallow here. The
    # difference is the ADOPT half: this loop used to `except ConflictError: pass`, which is
    # correct as far as WorkOS goes -- the role is there -- but it left `workos_role_id` NULL in
    # the mirror forever for every role that predated the push. A tenant's `admin` role was still
    # NULL for exactly that reason while its four siblings had ids.
    # ONE listing for the whole push, shared with every `ensure_workos_org_role` below. A newly
    # created role is added to the map by the ensure, so a slug appearing twice is not re-created.
    workos = await client_for_env(session, env_id)
    known = {
        str(r.slug): str(r.id)
        for r in (await workos.authorization.list_organization_roles(str(firm))).data
    }

    pushed = 0
    for role in roles:
        created = await ensure_workos_org_role(
            session,
            tenant_id=tenant_id,
            slug=str(role["slug"]),
            name=role["name"],
            description=role["description"],
            known=known,
        )
        if created is not None:
            known[workos_org_slug(str(role["slug"]))] = created
        pushed += 1
    return pushed


async def project_claimed_by(
    session: AsyncSession, workos_client_id: str, *, except_tenant_id: int
) -> tuple[int, str] | None:
    """The OTHER tenant already registering this WorkOS project, as `(tenant_id, name)`, or None.

    **A WorkOS project belongs to exactly one tenant** (`docs/identity-and-access.md` §0), and the
    mirror enforces it whether or not anyone means it to: `auth.users.workos_user_id` is globally
    UNIQUE, so a login can be mirrored ONCE. Point two environments at one project and each one's
    event drain claims every mirrored row for its own tenant — they drag the same users back and
    forth for as long as both run, and the fight surfaces as a `users_tenant_email_key` violation
    the moment two WorkOS users share an address (which a deleted-and-recreated user is).

    So this is a question worth asking BEFORE writing a routing row, not a constraint to discover
    afterwards. It is a read, not an enforcement: the caller phrases its own refusal, because what a
    dev script should say ("give this tenant its own project") is not what an operator UI would.

    `except_tenant_id` keeps re-registration idempotent — a tenant re-claiming its own project is
    the normal case, and the bootstrap script promises to be re-runnable."""
    row = (
        await session.execute(
            text(
                "SELECT e.tenant_id, coalesce(t.name, t.alias, '?') "
                "FROM auth.tenant_environments e "
                "JOIN auth.tenants t ON t.id = e.tenant_id "
                "WHERE e.workos_client_id = :cid AND e.tenant_id <> :tid "
                "ORDER BY e.id LIMIT 1"
            ),
            {"cid": workos_client_id, "tid": except_tenant_id},
        )
    ).first()
    return (int(row[0]), str(row[1])) if row is not None else None


async def set_environment(
    session: AsyncSession,
    *,
    tenant_id: int,
    workos_client_id: str,
    issuer: str | None = None,
    client_portal_host: str | None = None,
    platform_host: str | None = None,
    operations_host: str | None = None,
) -> dict[str, Any]:
    """Upsert this tenant's non-secret WorkOS routing config for THIS instance. One config per
    tenant per instance: the stage is stamped from ``settings.app_stage`` (dev/staging/prod are
    separate deployments), so callers never pass a stage. ``operations_host`` is only meaningful
    on the Graphene tenant (the admin portal's host). Returns the row."""
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenant_environments "
                    "  (tenant_id, stage, workos_client_id, issuer, "
                    "   client_portal_host, platform_host, operations_host) "
                    "VALUES (:tenant_id, :stage, :client_id, :issuer, :cp_host, :pf_host, "
                    "        :ops_host) "
                    "ON CONFLICT (tenant_id, stage) DO UPDATE SET "
                    "  workos_client_id = EXCLUDED.workos_client_id, issuer = EXCLUDED.issuer, "
                    "  client_portal_host = EXCLUDED.client_portal_host, "
                    "  platform_host = EXCLUDED.platform_host, "
                    "  operations_host = EXCLUDED.operations_host "
                    "RETURNING id, tenant_id, stage, workos_client_id, issuer, "
                    "  client_portal_host, platform_host, operations_host"
                ),
                {
                    "tenant_id": tenant_id,
                    "stage": settings.app_stage,
                    "client_id": workos_client_id,
                    "issuer": issuer,
                    "cp_host": client_portal_host,
                    "pf_host": platform_host,
                    "ops_host": operations_host,
                },
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def set_environment_applications(
    session: AsyncSession,
    *,
    tenant_id: int,
    client_portal_client_id: str | None = None,
    platform_client_id: str | None = None,
    operations_client_id: str | None = None,
) -> dict[str, Any]:
    """Record the WorkOS APPLICATION client ids for a tenant's portals (0093 columns, migration P1).

    Portal-via-application (docs/auth-applications-migration.md, A1): each portal is its own WorkOS
    application with its own client id, created by hand in the dashboard (tracked by the admin
    onboarding checklist, P6). This stores them on the tenant's live environment. COALESCE-preserve:
    a NULL argument leaves the existing value untouched, so saving one portal's id never wipes
    another's. Kept SEPARATE from ``set_environment`` (which owns the host/issuer routing) so
    neither call can clobber the other's columns. Application credentials are a property of the
    tenant's PROJECT, so they are written to EVERY one of the tenant's environment rows — a tenant
    can carry several (one per set of hosts), and the sign-in path reads the row that matched the
    request host. Returns the updated app-id columns."""
    rows = (
        (
            await session.execute(
                text(
                    "UPDATE auth.tenant_environments SET "
                    "  client_portal_client_id = COALESCE(:cp, client_portal_client_id), "
                    "  platform_client_id      = COALESCE(:pf, platform_client_id), "
                    "  operations_client_id    = COALESCE(:ops, operations_client_id) "
                    "WHERE tenant_id = :t "
                    "RETURNING client_portal_client_id, platform_client_id, operations_client_id"
                ),
                {
                    "cp": client_portal_client_id,
                    "pf": platform_client_id,
                    "ops": operations_client_id,
                    "t": tenant_id,
                },
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        raise LookupError("tenant has no registered WorkOS environment")
    return dict(rows[0])


async def get_environment(session: AsyncSession, tenant_id: int) -> dict[str, Any] | None:
    """This tenant's single WorkOS config for THIS instance (non-secret fields + whether keys/orgs
    are set), or None if not registered yet. The instance-stage counterpart to list_environments."""
    envs = await list_environments(session, tenant_id)
    return envs[0] if envs else None


async def set_environment_secrets(
    session: AsyncSession,
    *,
    tenant_environment_id: int,
    workos_api_key: str,
    cookie_password: str,
) -> None:
    """Encrypt + store (or rotate) an environment's WorkOS api key and cookie password.
    Encryption happens here (never store or log plaintext)."""
    await session.execute(
        text(
            "INSERT INTO auth.environment_secrets "
            "  (tenant_environment_id, workos_api_key_ct, cookie_password_ct) "
            "VALUES (:eid, :api_ct, :cookie_ct) "
            "ON CONFLICT (tenant_environment_id) DO UPDATE SET "
            "  workos_api_key_ct = EXCLUDED.workos_api_key_ct, "
            "  cookie_password_ct = EXCLUDED.cookie_password_ct, updated_at = now()"
        ),
        {
            "eid": tenant_environment_id,
            "api_ct": crypto.encrypt(workos_api_key),
            "cookie_ct": crypto.encrypt(cookie_password),
        },
    )


async def set_environment_app_secrets(
    session: AsyncSession,
    *,
    tenant_id: int,
    client_portal_secret: str | None = None,
    platform_secret: str | None = None,
    operations_secret: str | None = None,
) -> None:
    """Encrypt + store (or rotate) the per-portal WorkOS APPLICATION secrets.
    COALESCE-preserve: a NULL argument leaves the stored value untouched, so one portal's secret is
    set without disturbing another's. Like the app client ids, secrets are a property of the
    tenant's PROJECT, so they are upserted for EVERY one of the tenant's environment rows — the
    sign-in path reads whichever row matched the request host."""

    def ct(v: str | None) -> str | None:
        return crypto.encrypt(v) if v else None

    env_ids = list(
        (
            await session.execute(
                text("SELECT id FROM auth.tenant_environments WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
        ).scalars()
    )
    if not env_ids:
        raise LookupError("tenant has no registered WorkOS environment")
    params = {
        "c": ct(client_portal_secret),
        "p": ct(platform_secret),
        "o": ct(operations_secret),
    }
    for env_id in env_ids:
        await session.execute(
            text(
                "INSERT INTO auth.environment_secrets "
                "  (tenant_environment_id, client_portal_secret_ct, platform_secret_ct, "
                "   operations_secret_ct) "
                "VALUES (:eid, :c, :p, :o) "
                "ON CONFLICT (tenant_environment_id) DO UPDATE SET "
                "  client_portal_secret_ct = COALESCE(:c, "
                "    auth.environment_secrets.client_portal_secret_ct), "
                "  platform_secret_ct = COALESCE(:p, auth.environment_secrets.platform_secret_ct), "
                "  operations_secret_ct = COALESCE(:o, "
                "    auth.environment_secrets.operations_secret_ct), "
                "  updated_at = now()"
            ),
            {"eid": env_id, **params},
        )


async def portal_for_host(session: AsyncSession, host: str) -> str | None:
    """Which portal a public host IS — ``client`` / ``platform`` / ``operations`` — or None for a
    host no tenant has registered.

    The ingress router's question, and only that. ``env_config_by_host`` answers it too, but
    alongside decrypting the environment's WorkOS secrets, which a per-request routing lookup
    has no business touching. One indexed read against the three host columns, the same
    matching ``env_config_by_host`` does (loopback compose ports aliased).
    """
    alias = local_host_alias(host)
    return (
        await session.execute(
            text(
                "SELECT CASE WHEN te.client_portal_host IN (:host, :alias) THEN 'client' "
                "            WHEN te.platform_host IN (:host, :alias) THEN 'platform' "
                "            WHEN te.operations_host IN (:host, :alias) THEN 'operations' END "
                "FROM auth.tenant_environments te "
                "WHERE te.client_portal_host IN (:host, :alias) "
                "   OR te.platform_host IN (:host, :alias) "
                "   OR te.operations_host IN (:host, :alias) "
                "ORDER BY te.id LIMIT 1"
            ),
            {"host": host, "alias": alias},
        )
    ).scalar_one_or_none()


async def env_config_by_host(session: AsyncSession, host: str) -> dict[str, Any] | None:
    """The full WorkOS config for a portal host, secrets decrypted — the payload the shared
    portal needs to construct its per-tenant WorkOS client. Matches either portal's host
    (both share the environment). None if the host is unknown.

    Carries ``tenant_enabled`` (``auth.tenants.enabled``) so that a portal *can* refuse the sign-in
    of a suspended firm with an honest "this firm's access is suspended" rather than the generic
    misconfigured-host state — which is why we return the config instead of 404ing the host.
    ``db._refuse_if_suspended`` remains the unconditional enforcement point.

    App-only (0107): each host's portal signs in through its OWN application — the app client id
    paired with the app secret, into the ONE firm org. A portal whose app id or secret isn't stored
    gets ``None`` for those fields, which the login surfaces as "temporarily unavailable"
    (misconfigured) rather than silently signing into some other project. There is no env-key or
    per-portal-org fallback.

    Loopback compose ports are aliased (``:alias``) so CASE still names the portal."""
    alias = local_host_alias(host)
    row = (
        (
            await session.execute(
                text(
                    "SELECT te.tenant_id, te.stage, te.workos_client_id, te.issuer, "
                    "       s.cookie_password_ct, "
                    # This host's portal APPLICATION secret — the frontend authenticates the app's
                    # client id with THIS ('invalid_client' with any other key).
                    "       CASE WHEN te.client_portal_host IN (:host, :alias) "
                    "            THEN s.client_portal_secret_ct "
                    "            WHEN te.platform_host IN (:host, :alias) "
                    "            THEN s.platform_secret_ct "
                    "            WHEN te.operations_host IN (:host, :alias) "
                    "            THEN s.operations_secret_ct END "
                    "         AS app_secret_ct, "
                    "       t.name AS tenant_name, t.theme, t.enabled AS tenant_enabled, "
                    # The org every session authenticates into: the ONE firm org.
                    "       te.firm_org_id AS organization_id, "
                    # The WorkOS APPLICATION client id for this host's portal — the portal signs in
                    # THROUGH its application, so the token's client_id IS the portal.
                    "       CASE WHEN te.client_portal_host IN (:host, :alias) "
                    "            THEN te.client_portal_client_id "
                    "            WHEN te.platform_host IN (:host, :alias) "
                    "            THEN te.platform_client_id "
                    "            WHEN te.operations_host IN (:host, :alias) "
                    "            THEN te.operations_client_id END "
                    "         AS app_client_id, "
                    # Which portal this host IS — so we return that portal's own branding.
                    "       CASE WHEN te.client_portal_host IN (:host, :alias) THEN 'client' "
                    "            WHEN te.platform_host IN (:host, :alias) THEN 'platform' "
                    "            WHEN te.operations_host IN (:host, :alias) "
                    "            THEN 'operations' END AS portal "
                    "FROM auth.tenant_environments te "
                    "JOIN auth.tenants t ON t.id = te.tenant_id "
                    "LEFT JOIN auth.environment_secrets s ON s.tenant_environment_id = te.id "
                    "WHERE te.client_portal_host IN (:host, :alias) "
                    "   OR te.platform_host IN (:host, :alias) "
                    "   OR te.operations_host IN (:host, :alias) "
                    "ORDER BY te.id LIMIT 1"
                ),
                {"host": host, "alias": alias},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    cookie_ct = row["cookie_password_ct"]
    app_secret_ct = row["app_secret_ct"]
    resolved_branding = branding.parse_lenient(
        row["theme"], "client" if row["portal"] == "operations" else (row["portal"] or "client")
    ).model_dump()
    for field in ("logo_url", "logo_dark_url", "icon_url", "favicon_url"):
        value = resolved_branding.get(field)
        if isinstance(value, str):
            # A large inline raster is withheld (None): a data URI in the staff shell is decoded
            # on the main thread and becomes Largest Contentful Paint. The editor's own read
            # (`branding.get_branding`) keeps the stored value, so nothing is lost on save.
            resolved_branding[field] = shell_images.shell_safe_src(storage.public_asset_src(value))

    return {
        "tenant_id": row["tenant_id"],
        "stage": row["stage"],
        "workos_client_id": row["workos_client_id"],
        # The portal's WorkOS Application client id + its own secret. None until configured — the
        # login then reads as misconfigured (503), never as a silent sign-in elsewhere.
        "app_client_id": row["app_client_id"],
        "issuer": row["issuer"],
        "workos_api_key": crypto.decrypt(app_secret_ct) if app_secret_ct else None,
        "cookie_password": crypto.decrypt(cookie_ct) if cookie_ct else None,
        "organization_id": row["organization_id"],
        # False => the firm is suspended: the portal must refuse sign-in (and existing sessions
        # are revoked at WorkOS by provisioning.revoke_tenant_sessions when it is switched off).
        "tenant_enabled": bool(row["tenant_enabled"]),
        # WHICH portal this host is — 'client' or 'platform'. The SQL has always derived it (the
        # branding below is keyed on it); publishing it lets a CALLER resolve the audience from the
        # host too, which is how ``routers/internal.py``'s password reset dresses a staff reset in
        # the platform brand rather than defaulting every caller to the client one. Not an oracle:
        # the caller supplies the host and the response already varies by host (different branding,
        # different org id), so this reveals nothing a caller did not already know.
        "portal": row["portal"] or "client",
        # Non-secret white-label branding for THIS portal (client vs platform branded separately),
        # applied as CSS vars + logo/title + (client) home layout.
        "app_name": row["tenant_name"],
        # Branding is per client/platform portal; the operations/admin portal wears the product
        # so parse it as 'client' (the admin app uses /internal/admin-branding regardless).
        "branding": resolved_branding,
    }
