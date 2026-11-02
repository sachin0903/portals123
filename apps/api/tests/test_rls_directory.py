"""Isolation proof for 0030_auth_directory_scope.

0001_auth_core left three `auth` tables reading with USING (true). The worst was auth.tenants: any
authenticated principal — including a client end-user of one firm — could enumerate every firm on
the instance (name, alias, uid, website, workos_project_id and the whole white-label `theme`
jsonb), which punched straight through the otherwise-hard client-portal boundary.
auth.permissions and auth.role_permissions leaked the catalogue and the grant graph.

0107/0108 tightened the mechanism further: auth.tenant_id() is claims-only (the org-based psql
fallback died with the org columns) and auth.tenant_lineage() is a whitelist on the `portal`
claim — a session without pre-resolved claims fails CLOSED rather than resolving through
auth.tenants. The surviving SECURITY DEFINER accessor (auth.tenant_lineage_of) still guards its
own argument.

Order-independent: unique ids per run, tenant-scoped assertions, never truncates.
"""

import uuid

import pytest
from sqlalchemy import Engine, text

from tests.conftest import principal_conn


@pytest.fixture
def seed(super_engine: Engine) -> dict[str, object]:
    """Two unrelated firms, a staffer + a client end-user in Firm A, and one role with one grant
    in each firm (so the grant graph has something to leak)."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:

        def scalar(sql: str, **params: object) -> object:
            return c.execute(text(sql), params).scalar_one()

        def mk_tenant(letter: str) -> int:
            return int(
                scalar(
                    "INSERT INTO auth.tenants (uid, alias, name, website, workos_project_id, "
                    "  theme) "
                    "VALUES (:uid, :alias, :name, :site, :proj, :theme) RETURNING id",
                    uid=f"ten_{letter}_{s}",
                    alias=f"firm-{letter}-{s}",
                    name=f"Firm {letter.upper()} {s}",
                    site=f"https://firm-{letter}-{s}.test",
                    proj=f"project_{letter}_{s}",
                    theme='{"version": 1}',
                )
            )

        ta, tb = mk_tenant("a"), mk_tenant("b")

        def mk_user(tag: str, tenant: int) -> str:
            uid = str(
                scalar(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :email, :wos, true) RETURNING id",
                    uid=f"usr_{tag}_{s}",
                    t=tenant,
                    email=f"{tag}_{s}@firm.test",
                    wos=f"wos_{tag}_{s}",
                )
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id) VALUES (:u,:t)"
                ),
                {"u": uid, "t": tenant},
            )
            return uid

        staff_a = mk_user("dirstaff", ta)
        client_a = mk_user("dirclient", ta)

        def mk_role(tag: str, tenant: int, perm: str) -> int:
            rid = int(
                scalar(
                    "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
                    "VALUES (:t, :slug, :name, 'organisation', 'platform') RETURNING id",
                    t=tenant,
                    slug=f"role_{tag}_{s}",
                    name=f"Role {tag}",
                )
            )
            c.execute(
                text(
                    "INSERT INTO auth.role_permissions (role_id, permission_id) "
                    "SELECT :r, id FROM auth.permissions WHERE slug = :p"
                ),
                {"r": rid, "p": perm},
            )
            return rid

        role_a = mk_role("a", ta, "documents:read")
        role_b = mk_role("b", tb, "tenants:write")

    return {
        "tenant_a": ta,
        "tenant_b": tb,
        "alias_a": f"firm-a-{s}",
        "alias_b": f"firm-b-{s}",
        "staff_a": staff_a,
        "client_a": client_a,
        "org_a": f"org_a_{s}",
        "org_b": f"org_b_{s}",
        "role_a": role_a,
        "role_b": role_b,
    }


def _staff_claims(seed: dict[str, object]) -> dict[str, object]:
    return {
        "sub": "wos_dirstaff",
        "role": "adviser",
        "portal": "platform",
        "permissions": ["documents:read"],
        "org_id": seed["org_a"],
        "user_uuid": seed["staff_a"],
        "tenant_id": seed["tenant_a"],
        "tenant_ids": [seed["tenant_a"]],
        "contact_ids": [],
        "account_ids": [],
    }


def _client_claims(seed: dict[str, object]) -> dict[str, object]:
    return {
        "sub": "wos_dirclient",
        "role": "advised-client",
        "portal": "client",
        "permissions": [],
        "org_id": seed["org_a"],
        "user_uuid": seed["client_a"],
        "tenant_id": seed["tenant_a"],
        "tenant_ids": [seed["tenant_a"]],
        "contact_ids": [],
        "account_ids": [],
    }


def test_staff_see_only_their_own_firm(app_engine: Engine, seed: dict[str, object]) -> None:
    with principal_conn(app_engine, _staff_claims(seed)) as conn:
        aliases = set(
            conn.execute(
                text("SELECT alias FROM auth.tenants WHERE id = ANY(:t)"),
                {"t": [seed["tenant_a"], seed["tenant_b"]]},
            ).scalars()
        )
    assert aliases == {seed["alias_a"]}


def test_client_portal_cannot_enumerate_the_firm_directory(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The headline finding: a client end-user of one firm could list every firm on the instance,
    branding included. They now see exactly their own firm (which their portal already renders)."""
    with principal_conn(app_engine, _client_claims(seed)) as conn:
        rows = conn.execute(
            text("SELECT alias FROM auth.tenants WHERE id = ANY(:t)"),
            {"t": [seed["tenant_a"], seed["tenant_b"]]},
        ).scalars()
        aliases = set(rows)
        lineage = conn.execute(text("SELECT auth.tenant_lineage()")).scalar_one()
    assert aliases == {seed["alias_a"]}  # own firm only — never Firm B
    assert lineage == []  # ...and it is NOT reached through the staff lineage


def test_me_tenant_query_still_resolves(app_engine: Engine, seed: dict[str, object]) -> None:
    """GET /me/tenant is the only authenticated-role reader of auth.tenants; it must still work
    for staff AND for a client-portal session (whose lineage is empty by design)."""
    q = "SELECT alias FROM auth.tenants WHERE id = auth.tenant_id()"
    for claims in (_staff_claims(seed), _client_claims(seed)):
        with principal_conn(app_engine, claims) as conn:
            assert conn.execute(text(q)).scalar_one() == seed["alias_a"]


def test_empty_claims_see_no_tenants(app_engine: Engine, seed: dict[str, object]) -> None:
    with principal_conn(app_engine, {}) as conn:
        found = conn.execute(
            text("SELECT count(*) FROM auth.tenants WHERE id = ANY(:t)"),
            {"t": [seed["tenant_a"], seed["tenant_b"]]},
        ).scalar_one()
    assert found == 0


def test_a_session_without_resolved_claims_fails_closed(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The old psql fallback resolved tenant_id()/tenant_lineage() off auth.tenants from an org_id
    claim alone. 0107/0108 deleted it: tenant_id() is claims-only and tenant_lineage() is a
    whitelist on the `portal` claim, so a session carrying only sub + org_id resolves NOTHING —
    fail closed, never a recursion and never an org-derived tenant."""
    with principal_conn(app_engine, {"sub": "wos_dirstaff", "org_id": seed["org_a"]}) as conn:
        assert conn.execute(text("SELECT auth.tenant_id()")).scalar_one() is None
        assert conn.execute(text("SELECT auth.tenant_lineage()")).scalar_one() == []


def test_definer_accessors_are_not_cross_tenant_oracles(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The mechanism must not undo the fix it enables.

    auth.tenant_lineage_of is SECURITY DEFINER, owned by service_role (BYPASSRLS), takes a
    caller-supplied argument, and MUST be executable by `authenticated` because the SECURITY
    INVOKER helpers call it as the caller. Without an internal containment check it is a narrower
    version of the very leak the scoped policy closes, reachable from the hardest boundary in the
    product — a client-portal session. Revoking EXECUTE is not the fix either: CREATE FUNCTION
    grants EXECUTE to PUBLIC by default.

    Its sibling oracle auth.tenant_by_workos_org is DROPPED outright (0108 — the org column it
    read is gone), which is asserted here so it cannot quietly come back.

    So: your own tenant resolves; anything else fails closed ('{}').
    """
    lineage_of = "SELECT auth.tenant_lineage_of(:t)"
    for claims in (_staff_claims(seed), _client_claims(seed)):
        with principal_conn(app_engine, claims) as conn:
            # Own tenant — the only argument any in-tree caller ever passes.
            assert conn.execute(text(lineage_of), {"t": seed["tenant_a"]}).scalar_one() == [
                seed["tenant_a"]
            ]
            # Another firm's tenant id must not yield its descendant tree.
            assert conn.execute(text(lineage_of), {"t": seed["tenant_b"]}).scalar_one() == []
            assert conn.execute(text(lineage_of), {"t": None}).scalar_one() == []
            # The org->tenant oracle is gone, not merely guarded.
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_proc p "
                        "JOIN pg_namespace n ON n.oid = p.pronamespace "
                        "WHERE n.nspname = 'auth' AND p.proname = 'tenant_by_workos_org'"
                    )
                ).scalar_one()
                == 0
            )


def test_definer_accessors_still_serve_graphene_ops(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The guard keys off auth.is_system(), matching the policies: ops keeps the directory."""
    claims = {**_staff_claims(seed), "permissions": ["system"]}
    with principal_conn(app_engine, claims) as conn:
        assert conn.execute(
            text("SELECT auth.tenant_lineage_of(:t)"), {"t": seed["tenant_b"]}
        ).scalar_one() == [seed["tenant_b"]]


def test_grant_graph_is_not_readable_across_firms(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """auth.role_permissions carries no tenant column, so USING (true) exposed every firm's
    role→permission mapping. It is now scoped through the role's tenant."""
    with principal_conn(app_engine, _staff_claims(seed)) as conn:
        roles = {
            int(r)
            for r in conn.execute(
                text("SELECT role_id FROM auth.role_permissions WHERE role_id = ANY(:r)"),
                {"r": [seed["role_a"], seed["role_b"]]},
            ).scalars()
        }
    assert roles == {seed["role_a"]}


def test_permission_catalogue_is_scoped(app_engine: Engine, seed: dict[str, object]) -> None:
    """A firm's session sees the slugs its own roles grant (plus whatever its token carries), not
    another firm's — here, Firm B's operations-only `tenants:write`."""
    with principal_conn(app_engine, _staff_claims(seed)) as conn:
        slugs = set(
            conn.execute(
                text("SELECT slug FROM auth.permissions WHERE slug = ANY(:s)"),
                {"s": ["documents:read", "tenants:write"]},
            ).scalars()
        )
    assert slugs == {"documents:read"}


def test_system_principal_keeps_the_directory(app_engine: Engine, seed: dict[str, object]) -> None:
    """Graphene ops (a role carrying `system`) still reads across firms — the admin surface runs
    on service_role, but auth.is_system() must not be made inert by the tightening."""
    claims = {**_staff_claims(seed), "permissions": ["system"]}
    with principal_conn(app_engine, claims) as conn:
        aliases = set(
            conn.execute(
                text("SELECT alias FROM auth.tenants WHERE id = ANY(:t)"),
                {"t": [seed["tenant_a"], seed["tenant_b"]]},
            ).scalars()
        )
    assert aliases == {seed["alias_a"], seed["alias_b"]}


def test_role_permission_grants_are_audited(super_engine: Engine, seed: dict[str, object]) -> None:
    """docs/audit/05-admin.md: granting a role firm-wide client visibility left no trace at all.
    The audit spine is now attached, so the INSERT the fixture made is on the trail — and a
    revoke (rbac.set_org_role_permissions delete-then-reinserts) is too."""
    with super_engine.connect() as c:
        inserted = c.execute(
            text(
                "SELECT count(*) FROM audit.log "
                "WHERE action = 'auth.role_permissions.insert' "
                "  AND (detail -> 'row' ->> 'role_id')::int = :r"
            ),
            {"r": seed["role_a"]},
        ).scalar_one()
        assert inserted == 1

        c.execute(
            text("DELETE FROM auth.role_permissions WHERE role_id = :r"), {"r": seed["role_a"]}
        )
        deleted = c.execute(
            text(
                "SELECT count(*) FROM audit.log "
                "WHERE action = 'auth.role_permissions.delete' "
                "  AND (detail -> 'row' ->> 'role_id')::int = :r"
            ),
            {"r": seed["role_a"]},
        ).scalar_one()
    assert deleted == 1
