"""RBAC management -- tenant roles + their permissions.

Every role is tenant-scoped (no global templates). Role OBJECTS write through to that tenant's
WorkOS project (its portal orgs) — `role`/`roles` ride the access token harmlessly, a few bytes
each. A role's PERMISSIONS are DB-only and deliberately never pushed to WorkOS: WorkOS's AuthKit
embeds a role's assigned permissions straight into every access token it mints, and this
registry has grown large enough that doing so pushed some roles' sealed session cookies past the
browser's ~4096-byte ceiling (docs/auth.md). `app/auth.py::get_current_principal` reads
permissions fresh from `auth.role_permissions` on every request instead
(`auth.member_permissions`) — this module is the sole place they are ever written. The model
tenant has no WorkOS project, so its role edits are mirror-only regardless — its roles are the
preset new tenants clone, and they land in WorkOS per tenant when that tenant's environment is
set up.
"""

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import environments, permissions


class PermissionNotGrantable(HTTPException, ValueError):
    """A role edit tried to grant a permission the target tenant may not hold.

    Both an ``HTTPException(403)`` and a ``ValueError``, on purpose. It is raised in the service
    layer, and a router that forgets to translate it would otherwise turn a correct refusal into a
    500 — which is what happened to ``/admin/tenants/{id}/roles`` while ``/firm/roles`` returned
    403 for the identical input. Carrying its own status means every caller, present and future,
    answers 403 with the explanation; the ``ValueError`` base keeps the older
    ``except ValueError`` call sites working. ``__str__`` stays the bare message so the routers
    that DO translate it (``detail=str(exc)``) don't produce "403: 403: ...".
    """

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

    def __str__(self) -> str:
        return str(self.detail)


# Permissions that belong to Graphene's own Internal Operations surface — including `system`,
# the cross-tenant superuser that `auth.is_system()` (Python) and `auth.is_system()` (SQL) bypass
# every gate on. Derived from the registry's `portal` field so a new operations permission is
# covered the moment it is declared.
OPERATIONS_ONLY: frozenset[str] = permissions.OPERATIONS_SLUGS


async def _is_operations_tenant(session: AsyncSession, tenant_id: int) -> bool:
    """Is this Graphene's own operations tenant (the home of the core WorkOS project)? Fails
    closed: if the core environment isn't registered, nothing is the operations tenant."""
    try:
        return await environments.operations_tenant_id(session) == tenant_id
    except LookupError:
        return False


async def _ungrantable(
    session: AsyncSession, tenant_id: int, permission_slugs: list[str]
) -> tuple[list[str], list[str]]:
    """Split the slugs this tenant may NOT hold into (unknown, operations-reserved).

    Exact-match against the catalogue on purpose: ``slug = ANY(:s)`` is case- and
    whitespace-sensitive, so ``SYSTEM``/``System``/``" system"`` are *unknown* rather than
    privileged-but-allowed — a variation cannot slip past the reserved-permission check by dodging
    the string comparison, because dodging it lands in the unknown bucket, which is also refused.
    """
    if not permission_slugs:
        return [], []
    known = set(
        (
            await session.execute(
                text("SELECT slug FROM auth.permissions WHERE slug = ANY(:s)"),
                {"s": list(permission_slugs)},
            )
        )
        .scalars()
        .all()
    )
    unknown = sorted({s for s in permission_slugs if s not in known})
    privileged = sorted(OPERATIONS_ONLY.intersection(permission_slugs))
    if privileged and await _is_operations_tenant(session, tenant_id):
        privileged = []
    return unknown, privileged


async def assert_grantable(
    session: AsyncSession, *, tenant_id: int, permission_slugs: list[str]
) -> None:
    """Guard the ONLY path by which a permission is attached to a role. Two fail-closed rules:

    * **Unknown slugs are refused.** A slug outside the synced registry has no `auth.permissions`
      row for `_mirror_org_role_permissions`'s join to match, so letting one through would just
      silently grant nothing — refusing names the mistake instead.
    * **`operations`-portal permissions are refused unless the tenant IS the operations tenant.**
      Without this, `PUT /firm/roles/{slug}/permissions` (gated only on `roles:write`, i.e. any
      firm administrator) could grant a firm role the `system` permission — and since `system`
      bypasses `require_permission` in Python and `auth.is_system()` under RLS, that firm would
      read and write every tenant in the database. A tenant must never be able to widen its own
      blast radius; escalation stays a Graphene-side act on the operations tenant.
    """
    unknown, privileged = await _ungrantable(session, tenant_id, permission_slugs)
    if unknown:
        raise PermissionNotGrantable(
            f"Unknown permission(s): {', '.join(unknown)}. Grant only permissions from the "
            "catalogue (GET /firm/permissions)."
        )
    if privileged:
        raise PermissionNotGrantable(
            f"Permission(s) {', '.join(privileged)} are reserved for Graphene Internal "
            "Operations and cannot be granted to a firm's roles."
        )


async def _is_model_tenant(session: AsyncSession, tenant_id: int) -> bool:
    """The model tenant has no WorkOS project, so its role edits are mirror-only."""
    return bool(
        (
            await session.execute(
                text("SELECT is_model FROM auth.tenants WHERE id = :t"), {"t": tenant_id}
            )
        ).scalar_one_or_none()
    )


async def list_permissions(session: AsyncSession) -> list[dict[str, object]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT slug, name, resource, action, portal, description "
                    "FROM auth.permissions ORDER BY portal, resource, slug"
                )
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


# ---- per-tenant roles (every role is tenant-scoped; the model tenant's are the preset) ----


async def list_org_roles(session: AsyncSession, tenant_id: int) -> list[dict[str, object]]:
    """A tenant's organisation roles with their granted permission slugs."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT r.slug, r.name, r.description, r.portal, "
                    "  COALESCE("
                    "    array_agg(p.slug ORDER BY p.slug) FILTER (WHERE p.slug IS NOT NULL), '{}'"
                    "  ) AS permissions "
                    "FROM auth.roles r "
                    "LEFT JOIN auth.role_permissions rp ON rp.role_id = r.id "
                    "LEFT JOIN auth.permissions p ON p.id = rp.permission_id "
                    "WHERE r.tenant_id = :t "
                    "GROUP BY r.id, r.slug, r.name, r.description, r.portal "
                    "ORDER BY r.portal, r.slug"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def _mirror_org_role_permissions(
    session: AsyncSession, tenant_id: int, role_slug: str, permission_slugs: list[str]
) -> None:
    """Write a role's exact permission set into the mirror.

    The guard runs HERE, not only in the public entry points, because this is the single statement
    that attaches a permission to a role — so the choke-point claim is true of the code rather than
    of a convention. ``sync_org_roles_from_workos`` never reaches this function at all any more
    (it reconciles role existence from WorkOS, never permissions — see its own docstring for why).
    """
    await assert_grantable(session, tenant_id=tenant_id, permission_slugs=permission_slugs)
    # The PORTAL AXIS, enforced at the same choke point: a client-portal role may carry NO
    # permissions at all. Every registered slug is platform- or operations-portal (the client
    # portal's surface deliberately needs none — see permissions.py), and require_permission has
    # no portal check, so a staff slug attached to a client role would let a CLIENT'S token pass
    # staff gates (e.g. contacts:write through firm_session) against the whole tenant. Refuse
    # here — the one statement that attaches permissions — not only in the public entry points.
    if permission_slugs:
        role_portal = (
            await session.execute(
                text("SELECT portal FROM auth.roles WHERE tenant_id = :t AND slug = :slug"),
                {"t": tenant_id, "slug": role_slug},
            )
        ).scalar_one_or_none()
        if role_portal == "client":
            raise PermissionNotGrantable(
                "Client-portal roles cannot carry permissions: every permission is a staff or "
                "operations capability, and a client login must never hold one."
            )
    await session.execute(
        text(
            "DELETE FROM auth.role_permissions WHERE role_id = "
            "  (SELECT id FROM auth.roles WHERE tenant_id = :t AND slug = :slug)"
        ),
        {"t": tenant_id, "slug": role_slug},
    )
    for perm_slug in permission_slugs:
        await session.execute(
            text(
                "INSERT INTO auth.role_permissions (role_id, permission_id) "
                "SELECT rl.id, pm.id FROM auth.roles rl, auth.permissions pm "
                "WHERE rl.tenant_id = :t AND rl.slug = :rslug AND pm.slug = :pslug "
                "ON CONFLICT DO NOTHING"
            ),
            {"t": tenant_id, "rslug": role_slug, "pslug": perm_slug},
        )


async def create_org_role(
    session: AsyncSession,
    *,
    tenant_id: int,
    name: str,
    slug: str,
    portal: str,
    description: str | None = None,
    permission_slugs: list[str] | None = None,
) -> dict[str, object]:
    """Create a tenant role + mirror it. Project-per-tenant: for a normal tenant the role is
    created in WorkOS on the portal org matching `portal` (client → Client Portal, staff →
    Platform) via that environment's client; the mirror keeps the clean slug and WorkOS gets the
    required `org-` prefix. For the **model tenant** (no WorkOS project) the write is mirror-only
    — model roles land in WorkOS per tenant when that tenant's environment is set up."""
    perms = permission_slugs or []
    # Before any WorkOS write: a tenant may not grant itself Graphene-internal permissions.
    await assert_grantable(session, tenant_id=tenant_id, permission_slugs=perms)
    # And the portal axis (also re-checked at the mirror choke point, where the role's stored
    # portal is authoritative): client-portal roles carry no permissions, full stop.
    if perms and portal == "client":
        raise PermissionNotGrantable(
            "Client-portal roles cannot carry permissions: every permission is a staff or "
            "operations capability, and a client login must never hold one."
        )
    # One-firm-org: every role lives on the tenant's single firm org (the role's PORTAL is
    # mirror-only metadata; portals are applications). Shared with `grant_portal_access`, which
    # needs the same role to exist before WorkOS will accept a membership naming it.
    #
    # Deliberately NOT pushing `perms` to WorkOS (no `set_organization_role_permissions` call):
    # WorkOS's AuthKit embeds a role's assigned permissions straight into every access token it
    # mints for that role, and this registry has grown large enough that doing so pushes some
    # roles' sealed session cookies past the browser's ~4096-byte ceiling (docs/auth.md). The role
    # OBJECT still gets created in WorkOS (role/roles ride the token harmlessly, a few bytes each)
    # — only its permission ASSIGNMENT stays DB-only, read fresh by `auth.member_permissions` on
    # every request instead.
    workos_role_id = await environments.ensure_workos_org_role(
        session, tenant_id=tenant_id, slug=slug, name=name, description=description
    )

    await session.execute(
        text(
            "INSERT INTO auth.roles "
            "  (workos_role_id, tenant_id, slug, name, type, portal, description) "
            "VALUES (:wid, :t, :slug, :name, 'organisation', :portal, :desc) "
            "ON CONFLICT (tenant_id, slug) DO UPDATE SET workos_role_id = EXCLUDED.workos_role_id, "
            "  name = EXCLUDED.name, type = 'organisation', portal = EXCLUDED.portal, "
            "  description = EXCLUDED.description"
        ),
        {
            "wid": workos_role_id,
            "t": tenant_id,
            "slug": slug,
            "name": name,
            "portal": portal,
            "desc": description,
        },
    )
    await _mirror_org_role_permissions(session, tenant_id, slug, perms)
    return {
        "tenant_id": tenant_id,
        "slug": slug,
        "name": name,
        "portal": portal,
        "permissions": perms,
    }


async def set_org_role_permissions(
    session: AsyncSession,
    *,
    tenant_id: int,
    role_slug: str,
    permission_slugs: list[str],
) -> dict[str, object]:
    """Set the exact permission set on a tenant's role — mirror-only, deliberately never pushed
    to WorkOS (see `create_org_role`'s comment: WorkOS embeds a role's permissions straight into
    every access token it mints, which is what pushed some sealed session cookies past the
    browser's cookie ceiling). This app is the sole source of truth for a role's permissions,
    for every tenant alike, model tenant included — there is no WorkOS write left here to make
    conditional on it.

    This is the endpoint behind `PUT /firm/roles/{slug}/permissions`, so ``assert_grantable``
    runs first — a firm administrator must not be able to hand its own role the `system`
    permission."""
    await assert_grantable(session, tenant_id=tenant_id, permission_slugs=permission_slugs)
    # Portal axis: client-portal roles carry nothing (the mirror choke point re-checks too).
    if permission_slugs:
        role_portal = (
            await session.execute(
                text("SELECT portal FROM auth.roles WHERE tenant_id = :t AND slug = :s"),
                {"t": tenant_id, "s": role_slug},
            )
        ).scalar_one_or_none()
        if role_portal == "client":
            raise PermissionNotGrantable(
                "Client-portal roles cannot carry permissions: every permission is a staff or "
                "operations capability, and a client login must never hold one."
            )
    await _mirror_org_role_permissions(session, tenant_id, role_slug, permission_slugs)
    return {"tenant_id": tenant_id, "role_slug": role_slug, "permissions": permission_slugs}


async def sync_org_roles_from_workos(session: AsyncSession, tenant_id: int) -> int:
    """Pull a tenant's organisation (custom) roles straight from WorkOS into the mirror. For
    normal tenants, WorkOS is the source of truth for roles that already exist there (e.g.
    created directly against a shared/deployed project, or left behind by a request that created
    the WorkOS role but never committed the mirror row) — this reconciles `auth.roles` (role
    existence/name/description only) to match reality instead of erroring on every future write.
    A no-op for the model tenant (no WorkOS project). Returns the number of roles synced.

    WorkOS is the source of truth for which roles EXIST, never for what permissions a role
    carries: this app never pushes permission assignments to WorkOS (see
    `set_org_role_permissions`), so `role.permissions` here would just be whatever WorkOS's own
    dashboard was used to set directly — and mirroring it in would let a tenant grant itself
    anything by editing WorkOS's UI rather than this app's role editor, bypassing
    `assert_grantable` entirely. `auth.role_permissions` is therefore left untouched by this
    reconcile; only `auth.roles`' metadata is synced."""
    if await _is_model_tenant(session, tenant_id):
        return 0

    synced = 0
    # One-firm-org: all roles live on the single firm org. WorkOS does not know a role's PORTAL —
    # that is mirror-only metadata — so an already-mirrored role keeps its stored portal, and a
    # role first seen from WorkOS defaults to 'platform' (staff; never silently client).
    try:
        worg, workos = await environments.firm_org_and_client(session, tenant_id=tenant_id)
    except LookupError:
        return 0  # no firm org yet — nothing to sync
    for role in (await workos.authorization.list_organization_roles(worg)).data:
        if role.type != "OrganizationRole":
            continue  # environment-level default roles aren't tenant-managed
        slug = role.slug.removeprefix("org-")
        await session.execute(
            text(
                "INSERT INTO auth.roles "
                "  (workos_role_id, tenant_id, slug, name, type, portal, description) "
                "VALUES (:wid, :t, :slug, :name, 'organisation', 'platform', :desc) "
                "ON CONFLICT (tenant_id, slug) DO UPDATE SET "
                "  workos_role_id = EXCLUDED.workos_role_id, "
                "  name = EXCLUDED.name, type = 'organisation', "
                "  description = EXCLUDED.description"
            ),
            {
                "wid": role.id,
                "t": tenant_id,
                "slug": slug,
                "name": role.name,
                "desc": role.description,
            },
        )
        synced += 1
    return synced
