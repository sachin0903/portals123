"""Row-level-security proof for 0012_templates' datasets tables (datasets + dataset_versions).

Seeds two firms, each with a dataset + a version (via a superuser, bypassing RLS), then connects as
the `authenticated` request role and asserts staff read only their own tenant's rows, empty claims
see nothing, a cross-tenant write is rejected, and (tenant, code) is unique.
"""

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.conftest import principal_conn


@pytest.fixture
def ds_seed(super_engine: Engine) -> dict[str, object]:
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
                        "uid": f"dten_{letter}_{s}",
                        "alias": f"dfirm-{letter}-{s}",
                        "name": f"DFirm {letter.upper()} {s}",
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
                        "uid": f"dusr_{tag}_{s}",
                        "email": f"{tag}_{s}@d.test",
                        "wos": f"dwos_{tag}_{s}",
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

        def mk_dataset(tag: str, tenant: int, by: str) -> str:
            ds_uid = f"dds_{tag}_{s}"
            did = c.execute(
                text(
                    "INSERT INTO documents.datasets "
                    "  (uid, tenant_id, code, name, format, scope, created_by) "
                    "VALUES (:uid, :t, :code, :name, 'inline', 'global', CAST(:by AS uuid)) "
                    "RETURNING id"
                ),
                {"uid": ds_uid, "t": tenant, "code": f"ds_{tag}_{s}", "name": f"D {tag}", "by": by},
            ).scalar_one()
            c.execute(
                text(
                    "INSERT INTO documents.dataset_versions "
                    "  (uid, tenant_id, dataset_id, version_number, format, inline_data, "
                    "   created_by) "
                    "VALUES (:uid, :t, :did, 1, 'inline', '{}'::jsonb, CAST(:by AS uuid))"
                ),
                {"uid": f"ddsv_{tag}_{s}", "t": tenant, "did": did, "by": by},
            )
            return ds_uid

        ta, tb = mk_tenant("a"), mk_tenant("b")
        staff_a = mk_user("staff_a", ta)
        staff_b = mk_user("staff_b", tb)
        ds_a = mk_dataset("a", ta, staff_a)
        ds_b = mk_dataset("b", tb, staff_b)

    return {
        "tenant_a": ta,
        "tenant_b": tb,
        "staff_a": staff_a,
        "wos_staff_a": f"dwos_staff_a_{s}",
        "org_a": f"dorg_a_{s}",
        "ds_a": ds_a,
        "ds_b": ds_b,
        "code_a": f"ds_a_{s}",
        "s": s,
    }


def _staff_a_claims(seed: dict[str, object]) -> dict[str, object]:
    return {
        "contact_ids": [],
        "account_ids": [],
        "sub": seed["wos_staff_a"],
        "role": "adviser",
        "portal": "platform",
        "permissions": ["datasets:read"],
        "org_id": seed["org_a"],
        "user_uuid": seed["staff_a"],
        "tenant_id": seed["tenant_a"],
        "tenant_ids": [seed["tenant_a"]],
    }


def test_staff_sees_only_own_datasets(app_engine: Engine, ds_seed: dict[str, object]) -> None:
    with principal_conn(app_engine, _staff_a_claims(ds_seed)) as conn:
        uids = set(conn.execute(text("SELECT uid FROM documents.datasets")).scalars())
        vtenants = set(
            conn.execute(
                text("SELECT DISTINCT tenant_id FROM documents.dataset_versions")
            ).scalars()
        )
    assert ds_seed["ds_a"] in uids
    assert ds_seed["ds_b"] not in uids
    assert vtenants == {ds_seed["tenant_a"]}


def test_empty_claims_see_no_datasets(app_engine: Engine, ds_seed: dict[str, object]) -> None:
    with principal_conn(app_engine, {}) as conn:
        count = conn.execute(text("SELECT count(*) FROM documents.datasets")).scalar_one()
    assert count == 0


def test_staff_cannot_write_dataset_into_other_tenant(
    app_engine: Engine, ds_seed: dict[str, object]
) -> None:
    with principal_conn(app_engine, _staff_a_claims(ds_seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(
                "INSERT INTO documents.datasets (uid, tenant_id, code, name, format, scope, "
                "  created_by) VALUES (:uid, :t, :code, 'Evil', 'inline', 'global', "
                "  CAST(:by AS uuid))"
            ),
            {
                "uid": f"dds_evil_{uuid.uuid4().hex[:6]}",
                "t": ds_seed["tenant_b"],
                "code": f"evil_{ds_seed['s']}",
                "by": ds_seed["staff_a"],
            },
        )


def test_dataset_code_unique_per_tenant(app_engine: Engine, ds_seed: dict[str, object]) -> None:
    with principal_conn(app_engine, _staff_a_claims(ds_seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(
                "INSERT INTO documents.datasets (uid, tenant_id, code, name, format, scope, "
                "  created_by) VALUES (:uid, :t, :code, 'Dupe', 'inline', 'global', "
                "  CAST(:by AS uuid))"
            ),
            {
                "uid": f"dds_dupe_{uuid.uuid4().hex[:6]}",
                "t": ds_seed["tenant_a"],
                "code": ds_seed["code_a"],
                "by": ds_seed["staff_a"],
            },
        )
