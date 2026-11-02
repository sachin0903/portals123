"""Row-level-security isolation proof for 0001_auth_core.

Seeds two tenants (Firm A, Firm B) with users + memberships + API keys via a superuser
(bypassing RLS), then connects as the `app`/`authenticated` request role with each
principal's pre-resolved claims and asserts the boundary holds:

  * the `auth.resolve_claims` minter resolves WorkOS ids -> internal claims;
  * staff see their firm's users, never another tenant's;
  * a `client` principal (empty lineage) sees only their own user row;
  * `auth.api_keys` is tenant-isolated and invisible to clients;
  * empty/absent claims see nothing (fail-closed);
  * `audit.log` is append-only to `authenticated`.
"""

import json

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import principal_conn


@pytest.fixture
def seed(super_engine: Engine) -> dict[str, object]:
    """Two firms, three users, memberships and one API key each. Returns their ids."""
    with super_engine.connect() as c:
        # The container DB is session-scoped, so start each test from a clean slate.
        # CASCADE clears memberships/api_keys/audit.log; the NULL-tenant role templates
        # seeded by the migration are untouched (CASCADE only follows real tenant FKs).
        c.exec_driver_sql("TRUNCATE auth.users, auth.tenants RESTART IDENTITY CASCADE")
        tenant_a = c.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES ('ten_a','firm-a','Firm A') RETURNING id"
            )
        ).scalar_one()
        tenant_b = c.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES ('ten_b','firm-b','Firm B') RETURNING id"
            )
        ).scalar_one()
        # App-only portal (0107): sessions are classified by the application client id, so each
        # firm's environment declares its portal apps.
        for ten, prefix in ((tenant_a, "a"), (tenant_b, "b")):
            c.execute(
                text(
                    "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                    "  client_portal_client_id, platform_client_id) "
                    "VALUES (:t, 'dev', :env, :capp, :papp)"
                ),
                {
                    "t": ten,
                    "env": f"env_{prefix}",
                    "capp": f"app_client_{prefix}",
                    "papp": f"app_platform_{prefix}",
                },
            )

        def mk_user(uid: str, email: str, wos: str, ten: int) -> str:
            return str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                        "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                    ),
                    {"uid": uid, "t": ten, "email": email, "wos": wos},
                ).scalar_one()
            )

        staff_a = mk_user("usr_staff_a", "staff_a@firm-a.test", "wos_staff_a", tenant_a)
        client_a = mk_user("usr_client_a", "client_a@firm-a.test", "wos_client_a", tenant_a)
        user_b = mk_user("usr_staff_b", "staff_b@firm-b.test", "wos_staff_b", tenant_b)

        # Membership rows ARE portal access now (0107): staff hold platform, the client client.
        for uid_, ten, portal in [
            (staff_a, tenant_a, "platform"),
            (client_a, tenant_a, "client"),
            (user_b, tenant_b, "platform"),
        ]:
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships "
                    "  (user_id, tenant_id, portal, status) VALUES (:u, :t, :p, 'active')"
                ),
                {"u": uid_, "t": ten, "p": portal},
            )

        c.execute(
            text(
                "INSERT INTO auth.api_keys (uid, tenant_id, name, key_prefix, key_hash) "
                "VALUES ('key_a', :t, 'A key', 'gpk_a', 'hash_a')"
            ),
            {"t": tenant_a},
        )
        c.execute(
            text(
                "INSERT INTO auth.api_keys (uid, tenant_id, name, key_prefix, key_hash) "
                "VALUES ('key_b', :t, 'B key', 'gpk_b', 'hash_b')"
            ),
            {"t": tenant_b},
        )

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "staff_a": staff_a,
        "client_a": client_a,
        "user_b": user_b,
    }


def _claims(seed: dict[str, object], *, who: str) -> dict[str, object]:
    base = {"contact_ids": [], "account_ids": []}
    if who == "staff_a":
        return {
            **base,
            "sub": "wos_staff_a",
            "role": "adviser",
            "portal": "platform",
            "permissions": ["contacts:read_all"],
            "org_id": "org_a",
            "user_uuid": seed["staff_a"],
            "tenant_id": seed["tenant_a"],
            "tenant_ids": [seed["tenant_a"]],
        }
    if who == "client_a":
        return {
            **base,
            "sub": "wos_client_a",
            "role": "client",
            "portal": "client",
            "permissions": [],
            "org_id": "org_a",
            "user_uuid": seed["client_a"],
            "tenant_id": seed["tenant_a"],
            "tenant_ids": [seed["tenant_a"]],  # ignored: tenant_lineage() is empty for client
        }
    raise ValueError(who)


def test_resolve_claims_mints_internal_claims(app_engine: Engine, seed: dict[str, object]) -> None:
    """The SECURITY DEFINER minter resolves a WorkOS user+org into the internal claims."""
    with principal_conn(app_engine, {}) as conn:
        resolved = conn.execute(
            text("SELECT auth.resolve_claims('wos_staff_a', 'org_a')")
        ).scalar_one()
    if isinstance(resolved, str):  # driver may hand back text
        resolved = json.loads(resolved)
    assert resolved["user_uuid"] == seed["staff_a"]
    assert resolved["tenant_id"] == seed["tenant_a"]
    assert resolved["tenant_ids"] == [seed["tenant_a"]]
    assert resolved["contact_ids"] == [] and resolved["account_ids"] == []


def test_staff_sees_own_firm_users_only(app_engine: Engine, seed: dict[str, object]) -> None:
    with principal_conn(app_engine, _claims(seed, who="staff_a")) as conn:
        ids = {str(r) for r in conn.execute(text("SELECT id FROM auth.users")).scalars()}
    assert seed["staff_a"] in ids
    assert seed["client_a"] in ids
    assert seed["user_b"] not in ids  # cross-tenant user is invisible


def test_client_sees_only_self(app_engine: Engine, seed: dict[str, object]) -> None:
    with principal_conn(app_engine, _claims(seed, who="client_a")) as conn:
        ids = {str(r) for r in conn.execute(text("SELECT id FROM auth.users")).scalars()}
    assert ids == {seed["client_a"]}


def test_api_keys_tenant_isolated(app_engine: Engine, seed: dict[str, object]) -> None:
    with principal_conn(app_engine, _claims(seed, who="staff_a")) as conn:
        uids = set(conn.execute(text("SELECT uid FROM auth.api_keys")).scalars())
    assert uids == {"key_a"}  # never key_b

    with principal_conn(app_engine, _claims(seed, who="client_a")) as conn:
        uids = set(conn.execute(text("SELECT uid FROM auth.api_keys")).scalars())
    assert uids == set()  # client (empty lineage) sees no keys


def test_empty_claims_see_nothing(app_engine: Engine, seed: dict[str, object]) -> None:
    """Absent claims => fail closed: no users, no keys."""
    with principal_conn(app_engine, {}) as conn:
        assert conn.execute(text("SELECT count(*) FROM auth.users")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM auth.api_keys")).scalar_one() == 0


def test_email_unique_per_tenant_not_globally(super_engine: Engine) -> None:
    """Project-per-tenant headline (docs/identity-and-access.md §0): the same email is two
    separate logins in two different tenants, but must be unique WITHIN a tenant."""
    with super_engine.connect() as c:
        c.exec_driver_sql("TRUNCATE auth.users, auth.tenants RESTART IDENTITY CASCADE")
        mk_tenant = (
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:uid, :alias, :name) RETURNING id"
        )
        ta = c.execute(
            text(mk_tenant), {"uid": "ten_x", "alias": "firm-x", "name": "Firm X"}
        ).scalar_one()
        tb = c.execute(
            text(mk_tenant), {"uid": "ten_y", "alias": "firm-y", "name": "Firm Y"}
        ).scalar_one()

        def mk(uid: str, tenant: int, email: str) -> None:
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, enabled) "
                    "VALUES (:uid, :t, :e, true)"
                ),
                {"uid": uid, "t": tenant, "e": email},
            )

        # Same email in two different tenants -> both allowed (two distinct logins).
        mk("usr_x", ta, "jo@shared.test")
        mk("usr_y", tb, "jo@shared.test")

        # Same email in the SAME tenant -> rejected by UNIQUE (tenant_id, email).
        with pytest.raises(IntegrityError):
            mk("usr_x2", ta, "jo@shared.test")


def test_app_classifies_portal_and_gates_staff_on_membership(
    super_engine: Engine, app_engine: Engine
) -> None:
    """App-only portal (0107): the token's `client_id` names the application, and ANY portal is
    held only with an ACTIVE membership for it — a client who signs into the platform application
    resolves to NO portal at all (the request layer refuses), never a downgrade, never a default
    (WorkOS does not restrict which application an environment account can sign into)."""
    with super_engine.connect() as c:
        c.exec_driver_sql("TRUNCATE auth.users, auth.tenants RESTART IDENTITY CASCADE")
        tid = c.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES ('ten_a','firm-a','Firm A') RETURNING id"
            )
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                "  client_portal_client_id, platform_client_id) "
                "VALUES (:t, 'dev', 'env_client', 'app_client', 'app_platform')"
            ),
            {"t": tid},
        )

        def mk_user(uid: str, wos: str) -> str:
            return str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                        "VALUES (:uid, :t, :e, :wos, true) RETURNING id"
                    ),
                    {"uid": uid, "t": tid, "e": f"{uid}@firm-a.test", "wos": wos},
                ).scalar_one()
            )

        staff = mk_user("usr_staff", "wos_staff")
        client = mk_user("usr_client", "wos_client")
        # staff holds a PLATFORM membership; client holds only a CLIENT membership.
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, status) "
                "VALUES (:u, :t, 'platform', 'active')"
            ),
            {"u": staff, "t": tid},
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, status) "
                "VALUES (:u, :t, 'client', 'active')"
            ),
            {"u": client, "t": tid},
        )

    def portal(wos: str, client_id: str) -> str | None:
        # Empty claims => the mint branch (what db.py does before publishing claims).
        with principal_conn(app_engine, {}) as conn:
            resolved = conn.execute(
                text("SELECT auth.resolve_claims(:u, 'org_a', :cid)"),
                {"u": wos, "cid": client_id},
            ).scalar_one()
        if isinstance(resolved, str):
            resolved = json.loads(resolved)
        return resolved["portal"]

    # Staff through the platform app -> platform (holds the membership).
    assert portal("wos_staff", "app_platform") == "platform"
    # Client through the client app -> client.
    assert portal("wos_client", "app_client") == "client"
    # THE GATE: client into the PLATFORM app -> NO portal (no platform membership) -> refused.
    assert portal("wos_client", "app_platform") is None
    # An unknown app id resolves NO portal — there is no org fallback and no staff default (0107).
    assert portal("wos_staff", "unknown_app") is None
    # And staff into the CLIENT app is refused the same way (no client membership).
    assert portal("wos_staff", "app_client") is None


def test_app_classification_finds_ids_on_any_env_row(
    super_engine: Engine, app_engine: Engine
) -> None:
    """0097: a tenant can have more than one environment row, and the app ids may be saved to a row
    that is NOT the lowest-id one. Classification must still find them (the old single-row read took
    the lowest id, so an id on any other row was invisible and app-based classification silently
    never activated — the dev-tenant symptom)."""
    with super_engine.connect() as c:
        c.exec_driver_sql("TRUNCATE auth.users, auth.tenants RESTART IDENTITY CASCADE")
        tid = c.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES ('ten_a','firm-a','Firm A') RETURNING id"
            )
        ).scalar_one()
        # Two environment rows: the LOWEST id has no app ids; the higher id carries them.
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id) "
                "VALUES (:t, 'production', 'env_prod')"
            ),
            {"t": tid},
        )
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                "  platform_client_id) VALUES (:t, 'dev', 'env_dev', 'app_platform')"
            ),
            {"t": tid},
        )
        staff = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES ('u', :t, 'u@firm-a.test', 'wos_staff', true) RETURNING id"
                ),
                {"t": tid},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, status) "
                "VALUES (:u, :t, 'platform', 'active')"
            ),
            {"u": staff, "t": tid},
        )

    with principal_conn(app_engine, {}) as conn:
        resolved = conn.execute(
            text("SELECT auth.resolve_claims('wos_staff', 'org_a', 'app_platform')")
        ).scalar_one()
    if isinstance(resolved, str):
        resolved = json.loads(resolved)
    # The app id lives on the higher-id ('dev') row, but classification still finds it.
    assert resolved["portal"] == "platform"


def test_audit_log_is_append_only_for_authenticated(super_engine: Engine) -> None:
    """The FCA-evidence invariant: authenticated may SELECT/INSERT but never UPDATE/DELETE."""
    with super_engine.connect() as c:
        privs = {
            p: c.execute(
                text("SELECT has_table_privilege('authenticated', 'audit.log', :p)"),
                {"p": p},
            ).scalar_one()
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE")
        }
    assert privs["SELECT"] and privs["INSERT"]
    assert not privs["UPDATE"] and not privs["DELETE"]


def test_a_tenant_role_named_admin_is_not_a_superuser(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """`auth.is_system()` used to grant on `(auth.jwt() ->> 'role') IN ('system','admin')`, and that
    role name comes from the TENANT'S OWN WorkOS environment — so a firm that named a role "admin"
    got a cross-tenant superuser, because has_permission() and can_read_all() both short-circuit on
    is_system(). 0034 makes the `system` PERMISSION the only superuser signal.

    The claims here are the hostile case: tenant A's staff, role literally "admin", no permissions.
    """
    hostile = {**_claims(seed, who="staff_a"), "role": "admin", "permissions": []}
    with principal_conn(app_engine, hostile) as conn:
        assert conn.execute(text("SELECT auth.is_system()")).scalar_one() is False
        assert conn.execute(text("SELECT auth.can_read_all()")).scalar_one() is False
        write = text("SELECT auth.has_permission('tenants:write')")
        assert conn.execute(write).scalar_one() is False
        # has_role is GONE (0107): the role claim is display-only, with no SQL reader at all.
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'auth' AND p.proname = 'has_role'"
                )
            ).scalar_one()
            == 0
        )
        # And the practical consequence: still only their own firm's users.
        ids = {str(r) for r in conn.execute(text("SELECT id FROM auth.users")).scalars()}
    assert seed["user_b"] not in ids


def test_the_system_permission_still_confers_superuser(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The replacement signal works: `system` in `permissions` (registry-owned, and denylisted on
    the firm's own role editor) is what grants platform staff their bypass."""
    ops = {**_claims(seed, who="staff_a"), "role": "adviser", "permissions": ["system"]}
    with principal_conn(app_engine, ops) as conn:
        assert conn.execute(text("SELECT auth.is_system()")).scalar_one() is True
        write = text("SELECT auth.has_permission('tenants:write')")
        assert conn.execute(write).scalar_one() is True
