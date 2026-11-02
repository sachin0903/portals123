"""Row-level-security proof for 0012_templates (templates + template_versions).

Seeds two firms, each with a template + a version (via a superuser, bypassing RLS), then connects as
the `authenticated` request role and asserts staff read only their own tenant's rows, empty claims
see nothing, a cross-tenant write is rejected, and (tenant, code) is unique.
"""

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.conftest import principal_conn


@pytest.fixture
def tpl_seed(super_engine: Engine) -> dict[str, object]:
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:

        def mk_tenant(letter: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:uid, :alias, :name) RETURNING id"
                    ),
                    {
                        "uid": f"tten_{letter}_{s}",
                        "alias": f"tfirm-{letter}-{s}",
                        "name": f"TFirm {letter.upper()} {s}",
                    },
                ).scalar_one()
            )

        def mk_user(tag: str, tenant: int) -> str:
            uid = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, email, workos_user_id, enabled) "
                        "VALUES (:uid, :email, :wos, true) RETURNING id"
                    ),
                    {
                        "uid": f"tusr_{tag}_{s}",
                        "email": f"{tag}_{s}@t.test",
                        "wos": f"twos_{tag}_{s}",
                    },
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (:u, :t, 'platform')"
                ),
                {"u": uid, "t": tenant},
            )
            return uid

        def mk_template(tag: str, tenant: int, by: str) -> str:
            tpl_uid = f"ttpl_{tag}_{s}"
            tid = c.execute(
                text(
                    "INSERT INTO documents.templates "
                    "  (uid, tenant_id, code, name, document_type, created_by) "
                    "VALUES (:uid, :t, :code, :name, 'illustration', CAST(:by AS uuid)) "
                    "RETURNING id"
                ),
                {
                    "uid": tpl_uid,
                    "t": tenant,
                    "code": f"tpl_{tag}_{s}",
                    "name": f"T {tag}",
                    "by": by,
                },
            ).scalar_one()
            c.execute(
                text(
                    "INSERT INTO documents.template_versions "
                    "  (uid, tenant_id, template_id, version_number, created_by) "
                    "VALUES (:uid, :t, :tid, 1, CAST(:by AS uuid))"
                ),
                {"uid": f"ttplv_{tag}_{s}", "t": tenant, "tid": tid, "by": by},
            )
            return tpl_uid

        ta, tb = mk_tenant("a"), mk_tenant("b")
        staff_a = mk_user("staff_a", ta)
        staff_b = mk_user("staff_b", tb)
        tpl_a = mk_template("a", ta, staff_a)
        tpl_b = mk_template("b", tb, staff_b)

    return {
        "tenant_a": ta,
        "tenant_b": tb,
        "staff_a": staff_a,
        "wos_staff_a": f"twos_staff_a_{s}",
        "org_a": f"torg_a_{s}",
        "tpl_a": tpl_a,
        "tpl_b": tpl_b,
        "code_a": f"tpl_a_{s}",
        "s": s,
    }


def _staff_a_claims(seed: dict[str, object]) -> dict[str, object]:
    return {
        "contact_ids": [],
        "account_ids": [],
        "sub": seed["wos_staff_a"],
        "role": "adviser",
        "portal": "platform",
        "permissions": ["templates:write"],
        "org_id": seed["org_a"],
        "user_uuid": seed["staff_a"],
        "tenant_id": seed["tenant_a"],
        "tenant_ids": [seed["tenant_a"]],
    }


def test_staff_sees_only_own_templates(app_engine: Engine, tpl_seed: dict[str, object]) -> None:
    with principal_conn(app_engine, _staff_a_claims(tpl_seed)) as conn:
        uids = set(conn.execute(text("SELECT uid FROM documents.templates")).scalars())
        vtenants = set(
            conn.execute(
                text("SELECT DISTINCT tenant_id FROM documents.template_versions")
            ).scalars()
        )
    assert tpl_seed["tpl_a"] in uids
    assert tpl_seed["tpl_b"] not in uids
    assert vtenants == {tpl_seed["tenant_a"]}


def test_empty_claims_see_no_templates(app_engine: Engine, tpl_seed: dict[str, object]) -> None:
    with principal_conn(app_engine, {}) as conn:
        count = conn.execute(text("SELECT count(*) FROM documents.templates")).scalar_one()
    assert count == 0


def test_staff_cannot_write_template_into_other_tenant(
    app_engine: Engine, tpl_seed: dict[str, object]
) -> None:
    with principal_conn(app_engine, _staff_a_claims(tpl_seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(
                "INSERT INTO documents.templates (uid, tenant_id, code, name, document_type, "
                "  created_by) VALUES (:uid, :t, :code, 'Evil', 'review', CAST(:by AS uuid))"
            ),
            {
                "uid": f"ttpl_evil_{uuid.uuid4().hex[:6]}",
                "t": tpl_seed["tenant_b"],
                "code": f"evil_{tpl_seed['s']}",
                "by": tpl_seed["staff_a"],
            },
        )


def test_template_code_unique_per_tenant(app_engine: Engine, tpl_seed: dict[str, object]) -> None:
    with principal_conn(app_engine, _staff_a_claims(tpl_seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(
                "INSERT INTO documents.templates (uid, tenant_id, code, name, document_type, "
                "  created_by) VALUES (:uid, :t, :code, 'Dupe', 'review', CAST(:by AS uuid))"
            ),
            {
                "uid": f"ttpl_dupe_{uuid.uuid4().hex[:6]}",
                "t": tpl_seed["tenant_a"],
                "code": tpl_seed["code_a"],
                "by": tpl_seed["staff_a"],
            },
        )
