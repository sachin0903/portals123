"""auth.member_permissions (0093) -- a role set's permission slugs, from the DB tables that are
the actual source of truth, not trusted from a JWT claim. Takes `role_ids` directly (not a WorkOS
identity) so app/auth.py::resolve_claims_cached's result can be reused rather than re-resolved --
see the migration's own header comment for the full reasoning. See app/auth.py::_permissions_for,
its only caller, for how role_ids gets here from a verified identity.
"""

import json
import uuid

import pytest
from sqlalchemy import Engine, text


@pytest.fixture
def seed(super_engine: Engine) -> dict[str, object]:
    """One tenant, two roles with different permissions -- proves the lookup is scoped to
    exactly the role_ids given, not every role the tenant happens to have. Everything is
    uniquely suffixed rather than truncated: this session's Postgres is shared across the whole
    test run (auth.permissions is seeded once, session-scoped), so wiping it here would break
    every test that runs after this one — the mistake test_rls.py's own seed fixture already
    avoids by leaving that table alone. Reuses three REAL permission slugs from the seeded
    registry rather than inserting synthetic ones, sidestepping any risk of colliding with
    auth.permissions.slug's uniqueness."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = c.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES (:u, :a, 'Firm MP') RETURNING id"
            ),
            {"u": f"ten_mp_{s}", "a": f"firm-mp-{s}"},
        ).scalar_one()

        adviser_role = c.execute(
            text(
                "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                "VALUES (:t, :slug, 'Adviser', 'platform') RETURNING id"
            ),
            {"t": tenant, "slug": f"adviser_mp_{s}"},
        ).scalar_one()
        other_role = c.execute(
            text(
                "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                "VALUES (:t, :slug, 'Compliance', 'platform') RETURNING id"
            ),
            {"t": tenant, "slug": f"compliance_mp_{s}"},
        ).scalar_one()

        contacts_read, messages_write, audit_read = (
            c.execute(
                text("SELECT id FROM auth.permissions WHERE slug = :s"), {"s": slug}
            ).scalar_one()
            for slug in ("contacts:read", "messages:write", "audit:read")
        )

        for role_id, perm_id in (
            (adviser_role, contacts_read),
            (adviser_role, messages_write),
            (other_role, audit_read),
        ):
            c.execute(
                text("INSERT INTO auth.role_permissions (role_id, permission_id) VALUES (:r, :p)"),
                {"r": role_id, "p": perm_id},
            )

    return {
        "tenant": tenant,
        "adviser_role": adviser_role,
        "other_role": other_role,
        "messages_write": messages_write,
    }


def _member_permissions(app_engine: Engine, role_ids: list[int] | None) -> list[str]:
    with app_engine.connect() as conn:
        result = conn.execute(
            text("SELECT auth.member_permissions(:r)"), {"r": role_ids}
        ).scalar_one()
    if isinstance(result, str):  # driver may hand back text
        result = json.loads(result)
    return list(result)


def test_permissions_come_from_exactly_the_given_role(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The exact two slugs that role holds -- not the other role's."""
    assert sorted(_member_permissions(app_engine, [seed["adviser_role"]])) == [
        "contacts:read",
        "messages:write",
    ]


def test_multiple_role_ids_union_and_dedupe(app_engine: Engine, seed: dict[str, object]) -> None:
    """role_ids is an array (a membership can in principle hold more than one role, per
    auth.resolve_claims's own role_ids shape) -- passing both roles returns the union, with no
    duplicate even though nothing here happens to overlap."""
    result = _member_permissions(app_engine, [seed["adviser_role"], seed["other_role"]])
    assert sorted(result) == ["audit:read", "contacts:read", "messages:write"]
    assert len(result) == len(set(result))


def test_a_role_change_is_visible_immediately(
    app_engine: Engine, super_engine: Engine, seed: dict[str, object]
) -> None:
    """No caching sits in front of THIS function (the caching that this migration exists to
    enable lives one level up, in resolve_claims_cached, and only ever caches
    auth.resolve_claims's result -- never this join) -- revoking a permission is visible on the
    very next call."""
    with super_engine.connect() as c:
        c.execute(
            text("DELETE FROM auth.role_permissions WHERE role_id = :r AND permission_id = :p"),
            {"r": seed["adviser_role"], "p": seed["messages_write"]},
        )
    assert _member_permissions(app_engine, [seed["adviser_role"]]) == ["contacts:read"]


def test_null_or_empty_role_ids_return_empty(app_engine: Engine, seed: dict[str, object]) -> None:
    """A membership with no role assigned yet (auth.resolve_claims's role_ids comes back empty)
    must fail closed here too, not error."""
    assert _member_permissions(app_engine, None) == []
    assert _member_permissions(app_engine, []) == []
