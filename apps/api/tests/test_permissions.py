"""The canonical permission registry (app/permissions.py) — the single source of truth.

Covers the pure `satisfies` implication rule, the idempotent DB sync against the testcontainer,
and the two places where "who is a superuser" has to hold up rather than merely look right:

* the claims the API publishes must not make SQL's ``auth.is_system()`` true for a tenant-chosen
  role name. Migration ``0034`` removed the ``role IN ('system','admin')`` branch from
  ``auth.is_system()``, the generated UI gate stopped trusting ``role``, and ``0107`` retired the
  last SQL reader of the claim (``auth.has_role()``/the ``role='client'`` belt) — the role is
  published verbatim and display-only — so these run the real functions in Postgres, which is the
  layer that decides what a session can actually read;
* a tenant must not be able to GRANT itself ``system`` through its own role editor — including via
  a sibling endpoint, a spelling variation, or the WorkOS reconcile.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import environments, permissions, rbac
from app.auth import Principal, build_jwt_claims, get_current_principal
from app.db import FirmContext, admin_session, firm_session, target_firm_session
from app.main import app
from app.permissions import satisfies
from tests.conftest import principal_conn


def test_satisfies_write_implies_read_and_system_bypass() -> None:
    assert satisfies({"members:write"}, "members:read")  # write implies read
    assert satisfies({"members:write"}, "members:write")  # exact
    assert not satisfies({"members:read"}, "members:write")  # read does NOT imply write
    assert not satisfies(set(), "members:read")
    assert satisfies({"system"}, "anything:at:all")  # system satisfies everything
    # extras are siblings of write, never implied by it — minting a client is not editing one
    assert not satisfies({"contacts:write"}, "contacts:create")
    assert not satisfies({"contacts:create"}, "contacts:write")
    assert not satisfies({"contacts:create"}, "contacts:read")
    # dumping a listing is not viewing one
    assert not satisfies({"contacts:read"}, "listings:export")
    assert not satisfies({"contacts:write"}, "listings:export")
    # minting a person in Morgan Lloyd's CRM is not editing a client record
    assert not satisfies({"contacts:write"}, "contacts:sync_morgan_lloyd")
    assert not satisfies({"contacts:sync_morgan_lloyd"}, "contacts:write")
    assert not satisfies({"contacts:sync_morgan_lloyd"}, "contacts:read")


def test_for_portal_keeps_system_off_a_firm_session() -> None:
    """A Graphene role can carry ``system`` (the operations tenant may grant it) and sit on
    both memberships. The platform portal must still honour the firm toggles — otherwise
    turning off contacts:create leaves New client lit because ``can()`` treats system as yes
    to everything."""
    held = ["contacts:write", "contacts:create", "system", "tenants:write"]
    assert permissions.for_portal(held, "operations") == held
    assert permissions.for_portal(held, "platform") == ["contacts:write", "contacts:create"]
    assert "system" not in permissions.for_portal(held, "client")
    assert "system" not in permissions.for_portal(["system", "contacts:write"], None)
    # stripping create on a firm session is what hides New client, even if system stays on the role
    assert not permissions.satisfies(
        set(permissions.for_portal(["contacts:write", "system"], "platform")),
        "contacts:create",
    )


def test_session_permissions_are_scoped_to_the_portal() -> None:
    """GET /me (and every require_permission) must run the grant set through for_portal —
    otherwise a Graphene admin's platform session still carries system and every toggle is
    theatre."""
    import inspect

    from app import auth

    src = inspect.getsource(auth._permissions_for)
    assert "for_portal" in src
    assert rbac.OPERATIONS_ONLY is permissions.OPERATIONS_SLUGS


def test_registry_is_consistent() -> None:
    slugs = [p.slug for p in permissions.ALL]
    assert len(slugs) == len(set(slugs)), "duplicate permission slugs"
    # a couple of anchors from the migrated taxonomy
    assert "members:write" in slugs
    assert "tenants:read" in slugs
    assert "tenant_portal:read" in slugs
    assert "tenant_portal:write" in slugs
    assert "system" in slugs
    # the retired :manage taxonomy is gone
    assert not any(s.endswith(":manage") for s in slugs)
    # minting a client is its own extra, platform-portal so grantable to firm roles
    create = next(p for p in permissions.ALL if p.slug == "contacts:create")
    assert create.portal == "platform"
    assert create.action == "create"
    assert "contacts:create" not in rbac.OPERATIONS_ONLY
    export = next(p for p in permissions.ALL if p.slug == "listings:export")
    assert export.portal == "platform"
    assert export.action == "export"
    assert "listings:export" not in rbac.OPERATIONS_ONLY
    sync_ml = next(p for p in permissions.ALL if p.slug == "contacts:sync_morgan_lloyd")
    assert sync_ml.portal == "platform"
    assert sync_ml.action == "sync_morgan_lloyd"
    assert sync_ml.name == "Sync to Morgan Lloyd"
    assert "contacts:sync_morgan_lloyd" not in rbac.OPERATIONS_ONLY


@pytest.mark.asyncio
async def test_sync_permissions_is_idempotent(service_session: AsyncSession) -> None:
    """Syncing twice upserts the whole registry without duplicating or erroring; the catalogue
    then contains every registry slug."""
    n1 = await permissions.sync_permissions(service_session)
    n2 = await permissions.sync_permissions(service_session)
    assert n1 == n2 == len(permissions.ALL)

    rows = (
        (await service_session.execute(text("SELECT slug FROM auth.permissions"))).scalars().all()
    )
    catalogue = set(rows)
    for p in permissions.ALL:
        assert p.slug in catalogue


# ---- the claims contract, executed in Postgres ---------------------------------------------
#
# `auth.is_system()` was `role IN ('system','admin') OR 'system' = ANY(...)` in 0001_auth_core.sql,
# and `auth.has_permission()` / `auth.can_read_all()` short-circuit on it — so the claim payload,
# not the Python gate, is what decides whether a firm calling one of its roles "admin" reads the
# whole database. 0034 dropped the role branch; 0107 then made the role claim display-only end to
# end (`build_jwt_claims` publishes it verbatim — no SQL branch grants OR denies on it, the
# `portal` claim is the signal). These assert the OUTCOME against the real functions, so they hold
# whichever of the two layers changes (and they failed against the original of both).


def _claims(role: str | None, perms: list[str], portal: str = "platform") -> dict[str, object]:
    """Exactly what db.py::tenant_session publishes for this principal: the JWT-derived half plus
    the `portal` claim resolve_claims mints (0107: without it, tenant_lineage()/can_read_all()
    whitelist-deny — a staff fixture must carry it)."""
    return {
        **build_jwt_claims(Principal(user_id="user_x", role=role, permissions=perms)),
        "portal": portal,
    }


@pytest.mark.parametrize("role", ["admin", "system", "Admin"])
def test_role_named_admin_is_not_system_under_rls(app_engine: Engine, role: str) -> None:
    """The residual the audit's fix left open. A tenant-chosen role name must not satisfy
    auth.is_system() / auth.has_permission() / auth.can_read_all() in Postgres."""
    with principal_conn(app_engine, _claims(role, [])) as conn:
        assert conn.execute(text("SELECT auth.is_system()")).scalar_one() is False
        assert (
            conn.execute(text("SELECT auth.has_permission('members:write')")).scalar_one() is False
        )
        assert conn.execute(text("SELECT auth.can_read_all()")).scalar_one() is False


def test_the_system_permission_is_what_makes_sql_agree(app_engine: Engine) -> None:
    """The other half: a real system principal (the `system` PERMISSION) IS system in Postgres.
    Python and SQL agree on one mechanism, so the admin surface can no longer work while reading
    nothing."""
    with principal_conn(app_engine, _claims("adviser", ["system"])) as conn:
        assert conn.execute(text("SELECT auth.is_system()")).scalar_one() is True
        assert conn.execute(text("SELECT auth.can_read_all()")).scalar_one() is True


def test_the_portal_claim_is_what_gates_staff_lineage(app_engine: Engine) -> None:
    """0107: `auth.tenant_lineage()` is a WHITELIST on the `portal` claim (platform/operations).
    A client-portal session gets '{}' whatever its role is named, a staff session gets its
    tenant_ids — and a session WITHOUT a portal claim at all fails closed (the old role='client'
    deny belt and the claims-less fallback are both gone)."""
    with principal_conn(
        app_engine, {**_claims("adviser", [], portal="client"), "tenant_ids": [1, 2]}
    ) as conn:
        assert conn.execute(text("SELECT auth.tenant_lineage()")).scalar_one() == []
    with principal_conn(app_engine, {**_claims("adviser", []), "tenant_ids": [1, 2]}) as conn:
        assert conn.execute(text("SELECT auth.tenant_lineage()")).scalar_one() == [1, 2]
    # No portal claim ⇒ nothing tenant-scoped, even with a staff-looking role name.
    no_portal = build_jwt_claims(Principal(user_id="user_x", role="adviser", permissions=[]))
    with principal_conn(app_engine, {**no_portal, "tenant_ids": [1, 2]}) as conn:
        assert conn.execute(text("SELECT auth.tenant_lineage()")).scalar_one() == []


# ---- what a tenant may grant itself ---------------------------------------------------------
#
# `PUT /firm/roles/{slug}/permissions` is gated on `roles:write` — an ordinary firm-administrator
# capability. Since `system` bypasses every check in Python AND under RLS, letting a firm attach it
# to its own role would make `roles:write` equivalent to cross-tenant superuser.


async def _firm_tenant(session: AsyncSession, alias: str) -> int:
    """Get-or-create, so a parametrised test may ask for the same firm on every run."""
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:u, :a, 'Escalation Test Firm') "
                    "ON CONFLICT (uid) DO UPDATE SET name = auth.tenants.name RETURNING id"
                ),
                {"u": f"ten_{alias}", "a": alias},
            )
        ).scalar_one()
    )


def test_every_operations_permission_is_denied_to_tenants() -> None:
    """The denylist is derived from the registry, so a new Graphene-internal permission is covered
    the moment it is declared — nothing to remember to add."""
    assert "system" in rbac.OPERATIONS_ONLY
    for p in permissions.ALL:
        assert (p.slug in rbac.OPERATIONS_ONLY) == (p.portal == "operations")


def test_tenant_portal_permissions_are_operations_only_and_write_implies_read() -> None:
    by_slug = {p.slug: p for p in permissions.ALL}
    assert by_slug["tenant_portal:read"].portal == "operations"
    assert by_slug["tenant_portal:write"].portal == "operations"
    assert {"tenant_portal:read", "tenant_portal:write"} <= rbac.OPERATIONS_ONLY
    assert satisfies({"tenant_portal:write"}, "tenant_portal:read")
    assert not satisfies({"tenant_portal:read"}, "tenant_portal:write")


@pytest.mark.asyncio
async def test_firm_cannot_grant_itself_system(service_session: AsyncSession) -> None:
    """The escalation, at the service layer: refused before any WorkOS write, and the mirror is
    left untouched."""
    tid = await _firm_tenant(service_session, "firm-escalate")
    await service_session.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
            "VALUES (:t, 'firm-admin', 'Firm Admin', 'organisation', 'platform')"
        ),
        {"t": tid},
    )

    with pytest.raises(rbac.PermissionNotGrantable):
        await rbac.set_org_role_permissions(
            service_session,
            tenant_id=tid,
            role_slug="firm-admin",
            permission_slugs=["members:read", "system"],
        )
    for slug in ("tenants:write", "operations:write", "model:write", "audit:read"):
        with pytest.raises(rbac.PermissionNotGrantable):
            await rbac.set_org_role_permissions(
                service_session, tenant_id=tid, role_slug="firm-admin", permission_slugs=[slug]
            )
    # nothing was mirrored — the refusal happens before the write
    granted = (
        (
            await service_session.execute(
                text(
                    "SELECT p.slug FROM auth.role_permissions rp "
                    "JOIN auth.permissions p ON p.id = rp.permission_id "
                    "JOIN auth.roles r ON r.id = rp.role_id "
                    "WHERE r.tenant_id = :t"
                ),
                {"t": tid},
            )
        )
        .scalars()
        .all()
    )
    assert granted == []


@pytest.mark.asyncio
async def test_firm_cannot_invent_a_permission_slug(service_session: AsyncSession) -> None:
    """An unknown slug is refused too: `_mirror_org_role_permissions`'s join needs a matching
    `auth.permissions` row, so letting an invented slug through would silently grant nothing —
    refusing it up front names the mistake instead."""
    tid = await _firm_tenant(service_session, "firm-invent")
    with pytest.raises(rbac.PermissionNotGrantable):
        await rbac.set_org_role_permissions(
            service_session, tenant_id=tid, role_slug="whatever", permission_slugs=["god:mode"]
        )
    with pytest.raises(rbac.PermissionNotGrantable):
        await rbac.create_org_role(
            service_session,
            tenant_id=tid,
            name="Sneaky",
            slug="sneaky",
            portal="platform",
            permission_slugs=["god:mode"],
        )


@pytest.mark.asyncio
async def test_client_portal_roles_cannot_carry_permissions(
    service_session: AsyncSession,
) -> None:
    """The portal axis: every registered permission is a staff or operations capability (the
    client portal's surface deliberately needs none), and require_permission has no portal
    check — so a staff slug on a client-portal role would let a CLIENT'S token pass staff
    gates (e.g. contacts:write through firm_session) against the whole tenant. Both the
    create path (portal known from the request) and the set-permissions path (portal from
    the mirrored role) must refuse; an empty set on a client role stays fine."""
    tid = await _firm_tenant(service_session, "firm-client-axis")
    with pytest.raises(rbac.PermissionNotGrantable):
        await rbac.create_org_role(
            service_session,
            tenant_id=tid,
            name="Client plus",
            slug="client-plus",
            portal="client",
            permission_slugs=["contacts:write"],
        )
    # A permission-less client role is the legitimate shape — seed one straight into the mirror
    # (create_org_role would need a WorkOS project; the axis check under test runs before any
    # WorkOS resolution on the update path, so the stub isn't needed) …
    await service_session.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
            "VALUES (:t, 'client-axis', 'Client', 'client')"
        ),
        {"t": tid},
    )
    # … and the update path cannot widen it afterwards (checked before the WorkOS write).
    with pytest.raises(rbac.PermissionNotGrantable):
        await rbac.set_org_role_permissions(
            service_session,
            tenant_id=tid,
            role_slug="client-axis",
            permission_slugs=["contacts:write"],
        )


@pytest.mark.asyncio
async def test_firm_can_still_grant_its_own_permissions(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must not break the feature: ordinary platform/client permissions still write
    through to the mirror — and, deliberately, ONLY the mirror. `set_org_role_permissions` never
    resolves a WorkOS client at all any more (permissions are DB-only; see its docstring for why
    embedding them in access tokens was the problem), so `firm_org_and_client` is stubbed to
    raise if it's ever called — proving that, not just observing that nothing happened to reach
    from an unstubbed one.

    Deliberately its OWN tenant, not the model tenant. Using the model tenant (the shortcut that
    avoided needing a project) left a stray role behind in the shared testcontainer, so
    `test_schema_invariants.py::test_model_tenant_has_a_usable_role_preset` failed or passed
    depending on module order."""

    async def _firm_org_and_client(_session: AsyncSession, *, tenant_id: int) -> tuple[str, object]:
        raise AssertionError(
            "set_org_role_permissions must not resolve a WorkOS client — permissions are DB-only"
        )

    monkeypatch.setattr(environments, "firm_org_and_client", _firm_org_and_client)

    tid = await _firm_tenant(service_session, "firm-happy-path")
    await service_session.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
            "VALUES (:t, 'perm-guard-role', 'Guard', 'organisation', 'platform') "
            "ON CONFLICT (tenant_id, slug) DO NOTHING"
        ),
        {"t": tid},
    )
    await rbac.set_org_role_permissions(
        service_session,
        tenant_id=tid,
        role_slug="perm-guard-role",
        permission_slugs=["members:read", "contacts:read_all"],
    )
    roles = await rbac.list_org_roles(service_session, tid)
    role = next(r for r in roles if r["slug"] == "perm-guard-role")
    assert sorted(role["permissions"]) == ["contacts:read_all", "members:read"]  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_graphenes_own_operations_tenant_may_still_hold_system(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must not be a blanket denial: this is how an operations role gets `system` today
    (admin `/operations/roles` → `PUT /admin/tenants/{ops}/roles/{slug}/permissions`, resolving the
    tenant from `GET /admin/operations` — the same `environments.operations_tenant_id` this checks).
    """
    from app.config import settings

    monkeypatch.setattr(settings, "workos_client_id", "client_core_test")
    tid = await _firm_tenant(service_session, "graphene-ops")
    await service_session.execute(
        text(
            "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id) "
            "VALUES (:t, 'production', 'client_core_test')"
        ),
        {"t": tid},
    )
    await service_session.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
            "VALUES (:t, 'ops-admin', 'Ops Admin', 'organisation', 'operations')"
        ),
        {"t": tid},
    )
    # No exception: the operations tenant is where `system` legitimately lives.
    await rbac.assert_grantable(
        service_session, tenant_id=tid, permission_slugs=["system", "tenants:write"]
    )
    # ...and it is still refused for a different tenant in the same database.
    other = await _firm_tenant(service_session, "not-graphene-ops")
    with pytest.raises(rbac.PermissionNotGrantable):
        await rbac.assert_grantable(service_session, tenant_id=other, permission_slugs=["system"])


@pytest.mark.asyncio
async def test_the_firm_catalogue_does_not_offer_operations_permissions(
    service_session: AsyncSession,
) -> None:
    """The role editor is built from `GET /firm/permissions`; it must not render a tickbox the write
    path refuses (it used to list "System administration" to every firm administrator)."""
    from app.db import FirmContext
    from app.routers import firm

    ctx = FirmContext(
        session=service_session,
        tenant_id=await _firm_tenant(service_session, "firm-catalogue"),
        principal=Principal(user_id="user_firm_admin", permissions=["roles:read"]),
    )
    slugs = {str(p["slug"]) for p in await firm.list_permissions(ctx=ctx)}
    platform_slugs = {p.slug for p in permissions.ALL if p.portal == "platform"}
    assert "system" not in slugs
    assert not (slugs & rbac.OPERATIONS_ONLY)
    # The catalogue is the role editor's complete switch list. A spot-check let newly-added
    # permissions disappear from that view while older switches still rendered, which looked
    # random to the administrator trying to configure a role.
    assert slugs == platform_slugs


@pytest.mark.asyncio
async def test_the_firm_endpoint_itself_refuses_the_escalation(
    service_session: AsyncSession,
) -> None:
    """Through the handler that is actually routed (PUT /firm/roles/{slug}/permissions), not just
    the service function — a 403, not a 500."""
    from fastapi import HTTPException

    from app.db import FirmContext
    from app.routers import firm

    tid = await _firm_tenant(service_session, "firm-endpoint")
    ctx = FirmContext(
        session=service_session,
        tenant_id=tid,
        principal=Principal(user_id="user_firm_admin", permissions=["roles:write"]),
    )
    with pytest.raises(HTTPException) as exc:
        await firm.set_role_permissions(
            slug="firm-admin", body=firm.SetPermissions(permissions=["system"]), ctx=ctx
        )
    assert exc.value.status_code == 403
    assert "system" in str(exc.value.detail)


# ---- the same refusal ON THE WIRE ------------------------------------------------------------
#
# The tests above call the handler functions directly with a hand-built context, which proves the
# guard but not the routing or the dependency wiring. These go through the real ASGI app. They are
# also where the admin-surface regression shows up: `PermissionNotGrantable` is a ValueError, so a
# router that did not translate it returned 500 (an unhandled exception) for a refusal that
# /firm answered with 403 — a correct decision reported as a server fault.


@pytest_asyncio.fixture
async def wire(service_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """The real ASGI app with both privileged sessions bound to the test transaction and an
    operations principal that holds every permission a caller could need to REACH these
    endpoints — so nothing is refused by the coarse gate and the only thing under test is
    `assert_grantable`."""
    principal = Principal(
        user_id="user_wire_ops",
        organisation_id="org_wire",
        permissions=["tenants:write", "roles:write", "members:write"],
    )

    async def _session() -> AsyncIterator[AsyncSession]:
        yield service_session

    async def _firm_ctx() -> AsyncIterator[FirmContext]:
        yield FirmContext(
            session=service_session,
            tenant_id=await _firm_tenant(service_session, "firm-on-the-wire"),
            principal=principal,
        )

    app.dependency_overrides[admin_session] = _session
    app.dependency_overrides[firm_session] = _firm_ctx
    # `target_firm_session` is `firm_session` plus an optional operations target, and the firm's
    # directory reads take it so the tenant workspace can list a firm's own people and roles. It is
    # overridden alongside its twin because an override is keyed on the CALLABLE: without this,
    # `GET /firm/roles` runs the real dependency, answers 401, and the assertion that the refused
    # role was not created reads a `{"detail": …}` body as a list of roles.
    app.dependency_overrides[target_firm_session] = _firm_ctx
    app.dependency_overrides[get_current_principal] = lambda: principal
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        for dep in (admin_session, firm_session, target_firm_session, get_current_principal):
            app.dependency_overrides.pop(dep, None)


# Every spelling of the reserved permission an attacker would reach for after the plain one is
# refused. The catalogue lookup is exact, so a variation that dodges the reserved-name comparison
# lands in the "unknown slug" bucket — which is refused too. Both branches must answer 403.
_SYSTEM_VARIATIONS = ["system", "SYSTEM", "System", " system", "system ", '"system"', "system;"]


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", _SYSTEM_VARIATIONS)
async def test_the_firm_endpoint_refuses_every_spelling_on_the_wire(
    wire: AsyncClient, service_session: AsyncSession, slug: str
) -> None:
    res = await wire.put("/firm/roles/firm-admin/permissions", json={"permissions": [slug]})
    assert res.status_code == 403, res.text
    granted = (
        (
            await service_session.execute(
                text(
                    "SELECT count(*) FROM auth.role_permissions rp "
                    "JOIN auth.roles r ON r.id = rp.role_id "
                    "JOIN auth.tenants t ON t.id = r.tenant_id "
                    "WHERE t.alias = 'firm-on-the-wire'"
                )
            )
        )
        .scalars()
        .one()
    )
    assert granted == 0


@pytest.mark.asyncio
async def test_the_sibling_firm_endpoint_is_not_a_way_round_it(wire: AsyncClient) -> None:
    """A route-specific guard is worth little if a sibling endpoint with the same permission does
    the same thing. `POST /firm/roles` also attaches permissions (at creation), on the same
    `roles:write`, so it has to refuse the same input on the wire."""
    res = await wire.post(
        "/firm/roles",
        json={
            "name": "Totally Normal",
            "slug": "totally-normal",
            "portal": "platform",
            "permissions": ["members:read", "system"],
        },
    )
    assert res.status_code == 403, res.text
    # ...and the role itself must not have been created as a side effect of the attempt.
    roles = await wire.get("/firm/roles")
    assert all(r["slug"] != "totally-normal" for r in roles.json())


@pytest.mark.asyncio
async def test_the_admin_endpoint_answers_403_not_500(
    wire: AsyncClient, service_session: AsyncSession
) -> None:
    """The regression: /admin/tenants/{tenant_uid}/roles/{slug}/permissions catches LookupError and
    WorkOSError but not PermissionNotGrantable, so the refusal used to surface as a 500. It now
    carries its own 403 (rbac.PermissionNotGrantable is an HTTPException), which every present and
    future caller inherits."""
    tid = await _firm_tenant(service_session, "admin-wire-firm")
    # The routes take the firm's uid; the serial is what the seed below keys on. `_firm_tenant`
    # mints `ten_{alias}`, so the two are known without a second read.
    tuid = "ten_admin-wire-firm"
    await service_session.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
            "VALUES (:t, 'firm-admin', 'Firm Admin', 'organisation', 'platform')"
        ),
        {"t": tid},
    )
    res = await wire.put(
        f"/admin/tenants/{tuid}/roles/firm-admin/permissions", json={"permissions": ["system"]}
    )
    assert res.status_code == 403, res.text
    assert "system" in res.json()["detail"]
    # ...and the message is the bare explanation, not HTTPException's "403: ..." stringification.
    assert not res.json()["detail"].startswith("403")

    res = await wire.post(
        f"/admin/tenants/{tuid}/roles",
        json={"name": "Sneaky", "slug": "sneaky", "portal": "platform", "permissions": ["system"]},
    )
    assert res.status_code == 403, res.text


# ---- the reconcile path ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_workos_reconcile_never_touches_permissions(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sync_org_roles_from_workos` used to mirror whatever WorkOS reported straight into
    `auth.role_permissions` (filtered through `grantable_only`) — the one hole in the
    "assert_grantable is the only path" claim, since a reserved slug set directly on a WorkOS
    role would smuggle through. That whole pull direction is gone now, for a bigger reason than
    smuggling: this app never pushes permissions to WorkOS any more (see
    `rbac.set_org_role_permissions`), so WorkOS's `role.permissions` is not a trustworthy signal
    of anything — the reconcile only ever syncs role EXISTENCE/metadata. Proven two ways: an
    EXISTING role's local permissions survive untouched regardless of what WorkOS reports for it
    (including a reserved slug), and a role WorkOS knows about but the mirror doesn't yet lands
    with none, not whatever WorkOS happened to report."""

    class _Role:
        type = "OrganizationRole"

        def __init__(self, slug: str, perms: list[str]) -> None:
            self.id = f"role_{slug}"
            self.slug = f"org-{slug}"
            self.name = slug
            self.description = None
            self.permissions = perms

    class _Roles:
        def __init__(self, data: list[_Role]) -> None:
            self.data = data

    tid = await _firm_tenant(service_session, "firm-reconcile")
    existing_role_id = (
        await service_session.execute(
            text(
                "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
                "VALUES (:t, 'existing', 'Existing', 'organisation', 'platform') RETURNING id"
            ),
            {"t": tid},
        )
    ).scalar_one()
    await service_session.execute(
        text(
            "INSERT INTO auth.role_permissions (role_id, permission_id) "
            "SELECT :r, id FROM auth.permissions WHERE slug = 'contacts:read'"
        ),
        {"r": existing_role_id},
    )

    class _Authorization:
        @staticmethod
        async def list_organization_roles(_org: str) -> _Roles:
            return _Roles(
                [
                    # Reports something entirely different from the DB's own record — proving
                    # the reconcile doesn't touch permissions at all, not merely that it filters.
                    _Role("existing", ["members:read", "system", "tenants:write"]),
                    _Role("brand-new", ["members:read", "system"]),
                ]
            )

    class _Client:
        authorization = _Authorization()

    async def _firm_org_and_client(_session: AsyncSession, *, tenant_id: int) -> tuple[str, object]:
        return "org_reconcile", _Client()

    monkeypatch.setattr(environments, "firm_org_and_client", _firm_org_and_client)

    assert await rbac.sync_org_roles_from_workos(service_session, tid) == 2
    roles = await rbac.list_org_roles(service_session, tid)
    existing = next(r for r in roles if r["slug"] == "existing")
    brand_new = next(r for r in roles if r["slug"] == "brand-new")
    # Untouched by whatever WorkOS reported for it.
    assert existing["permissions"] == ["contacts:read"]
    # A role newly discovered from WorkOS starts with NONE — not WorkOS's report.
    assert brand_new["permissions"] == []
