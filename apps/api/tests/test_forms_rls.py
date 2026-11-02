"""Row-level-security proof for 0011_forms (documents.forms + documents.form_fields).

Seeds two firms, each with a form + a field (via a superuser, bypassing RLS), then connects as the
`authenticated` request role and asserts:
  * staff read only their own tenant's forms and fields,
  * empty claims see nothing (fail-closed),
  * a staffer cannot write a form/field into another tenant (WITH CHECK rejects it),
  * (tenant, code) is unique — a duplicate code in the same firm is rejected.

Order-independent: unique ids per run, tenant-scoped assertions, never truncates.
"""

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.conftest import principal_conn


@pytest.fixture
def form_seed(super_engine: Engine) -> dict[str, object]:
    """Two firms; a staff user + one form (with one field) in each — all with unique ids."""
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
                        "uid": f"mten_{letter}_{s}",
                        "alias": f"mfirm-{letter}-{s}",
                        "name": f"MFirm {letter.upper()} {s}",
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
                        "uid": f"musr_{tag}_{s}",
                        "email": f"{tag}_{s}@mfirm.test",
                        "wos": f"mwos_{tag}_{s}",
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

        def mk_form(tag: str, tenant: int, by: str) -> str:
            form_uid = f"mfrm_{tag}_{s}"
            fid = c.execute(
                text(
                    "INSERT INTO documents.forms (uid, tenant_id, code, name, created_by) "
                    "VALUES (:uid, :t, :code, :name, CAST(:by AS uuid)) RETURNING id"
                ),
                {
                    "uid": form_uid,
                    "t": tenant,
                    "code": f"form_{tag}_{s}",
                    "name": f"Form {tag}",
                    "by": by,
                },
            ).scalar_one()
            c.execute(
                text(
                    "INSERT INTO documents.form_fields "
                    "  (uid, tenant_id, form_id, field_key, label, sort_order) "
                    "VALUES (:uid, :t, :fid, 'name', 'Full name', 1)"
                ),
                {"uid": f"mfld_{tag}_{s}", "t": tenant, "fid": fid},
            )
            return form_uid

        ta, tb = mk_tenant("a"), mk_tenant("b")
        staff_a = mk_user("staff_a", ta)
        staff_b = mk_user("staff_b", tb)
        form_a = mk_form("a", ta, staff_a)
        form_b = mk_form("b", tb, staff_b)

    return {
        "tenant_a": ta,
        "tenant_b": tb,
        "staff_a": staff_a,
        "wos_staff_a": f"mwos_staff_a_{s}",
        "org_a": f"morg_a_{s}",
        "form_a": form_a,
        "form_b": form_b,
        "code_a": f"form_a_{s}",
        "s": s,
    }


def _staff_a_claims(seed: dict[str, object]) -> dict[str, object]:
    return {
        "contact_ids": [],
        "account_ids": [],
        "sub": seed["wos_staff_a"],
        "role": "adviser",
        "portal": "platform",
        "permissions": ["forms:write"],
        "org_id": seed["org_a"],
        "user_uuid": seed["staff_a"],
        "tenant_id": seed["tenant_a"],
        "tenant_ids": [seed["tenant_a"]],
    }


def test_staff_sees_only_own_tenant_forms(app_engine: Engine, form_seed: dict[str, object]) -> None:
    with principal_conn(app_engine, _staff_a_claims(form_seed)) as conn:
        uids = set(conn.execute(text("SELECT uid FROM documents.forms")).scalars())
    assert form_seed["form_a"] in uids
    assert form_seed["form_b"] not in uids


def test_staff_sees_only_own_tenant_fields(
    app_engine: Engine, form_seed: dict[str, object]
) -> None:
    with principal_conn(app_engine, _staff_a_claims(form_seed)) as conn:
        tenants = set(
            conn.execute(text("SELECT DISTINCT tenant_id FROM documents.form_fields")).scalars()
        )
    assert tenants == {form_seed["tenant_a"]}


def test_empty_claims_see_no_forms(app_engine: Engine, form_seed: dict[str, object]) -> None:
    with principal_conn(app_engine, {}) as conn:
        count = conn.execute(text("SELECT count(*) FROM documents.forms")).scalar_one()
    assert count == 0


def test_staff_cannot_write_form_into_other_tenant(
    app_engine: Engine, form_seed: dict[str, object]
) -> None:
    # The WITH CHECK on forms_write rejects a row whose tenant_id isn't in the caller's lineage.
    with principal_conn(app_engine, _staff_a_claims(form_seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(
                "INSERT INTO documents.forms (uid, tenant_id, code, name) "
                "VALUES (:uid, :t, :code, 'Evil')"
            ),
            {
                "uid": f"mfrm_evil_{uuid.uuid4().hex[:6]}",
                "t": form_seed["tenant_b"],
                "code": f"evil_{form_seed['s']}",
            },
        )


def test_form_code_unique_per_tenant(app_engine: Engine, form_seed: dict[str, object]) -> None:
    # A second form with the same code in the same firm is rejected (forms_tenant_code_key).
    with principal_conn(app_engine, _staff_a_claims(form_seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(
                "INSERT INTO documents.forms (uid, tenant_id, code, name) "
                "VALUES (:uid, :t, :code, 'Dupe')"
            ),
            {
                "uid": f"mfrm_dupe_{uuid.uuid4().hex[:6]}",
                "t": form_seed["tenant_a"],
                "code": form_seed["code_a"],
            },
        )
