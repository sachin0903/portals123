"""Row-level-security proof for 0010_form_responses (+ the request due-date write scope).

Seeds two firms, each with a client, a request + complete_form item, and a stored response (via a
superuser, bypassing RLS), then connects as the `authenticated` request role and asserts:
  * a client reads only their own response (via the denormalised contact_id branch, matched against
    auth.contact_scope()),
  * staff read only their own tenant's responses,
  * empty claims see nothing (fail-closed),
  * a client cannot INSERT a response directly (writes are staff-only — real submissions go through
    the elevated service_role endpoint), and
  * staff can UPDATE the due date of their own tenant's request but not another firm's.

Order-independent: unique ids per run, tenant-scoped assertions, never truncates.
"""

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.conftest import principal_conn


@pytest.fixture
def fr_seed(super_engine: Engine) -> dict[str, object]:
    """Two firms; a staff + client in Firm A, a client in Firm B; one complete_form request + stored
    form_response per firm — all with unique ids."""
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
                        "uid": f"ften_{letter}_{s}",
                        "alias": f"ffirm-{letter}-{s}",
                        "name": f"FFirm {letter.upper()} {s}",
                    },
                ).scalar_one()
            )

        def mk_user(tag: str, tenant: int, portal: str) -> str:
            uid = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, email, workos_user_id, enabled) "
                        "VALUES (:uid, :email, :wos, true) RETURNING id"
                    ),
                    {
                        "uid": f"fusr_{tag}_{s}",
                        "email": f"{tag}_{s}@ffirm.test",
                        "wos": f"fwos_{tag}_{s}",
                    },
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (:u, :t, :p)"
                ),
                {"u": uid, "t": tenant, "p": portal},
            )
            return uid

        def mk_contact(tag: str, tenant: int) -> int:
            # The client is the CRM contact (the true client model).
            return int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                        "VALUES (:t, :uid, 'individual', :name, :email) RETURNING id"
                    ),
                    {
                        "t": tenant,
                        "uid": f"fcnt_{tag}_{s}",
                        "name": f"Client {tag}",
                        "email": f"{tag}_{s}@ffirm.test",
                    },
                ).scalar_one()
            )

        def mk_req_item(tag: str, tenant: int, contact: int, by: str) -> int:
            rid = c.execute(
                text(
                    "INSERT INTO documents.requests (uid, tenant_id, contact_id, requested_by) "
                    "VALUES (:uid, :t, :cid, CAST(:by AS uuid)) RETURNING id"
                ),
                {"uid": f"freq_{tag}_{s}", "t": tenant, "cid": contact, "by": by},
            ).scalar_one()
            return int(
                c.execute(
                    text(
                        "INSERT INTO documents.request_items "
                        "  (uid, request_id, tenant_id, action, title, form_uid) "
                        "VALUES (:uid, :rid, :t, 'complete_form', 'Fact find', 'frm-fact-find') "
                        "RETURNING id"
                    ),
                    {"uid": f"fitem_{tag}_{s}", "rid": rid, "t": tenant},
                ).scalar_one()
            )

        def mk_response(tag: str, tenant: int, item_id: int, contact: int, by: str) -> str:
            c.execute(
                text(
                    "INSERT INTO documents.form_responses "
                    "  (uid, tenant_id, request_item_id, form_uid, contact_id, answers, "
                    "   submitted_by) "
                    "VALUES (:uid, :t, :iid, 'frm-fact-find', :cid, "
                    "  CAST(:a AS jsonb), CAST(:by AS uuid))"
                ),
                {
                    "uid": f"frsp_{tag}_{s}",
                    "t": tenant,
                    "iid": item_id,
                    "cid": contact,
                    "by": by,
                    "a": '{"name": "Jane"}',
                },
            )
            return f"frsp_{tag}_{s}"

        ta, tb = mk_tenant("a"), mk_tenant("b")
        staff_a = mk_user("staff_a", ta, "platform")
        client_a = mk_user("client_a", ta, "client")
        client_b = mk_user("client_b", tb, "client")
        contact_a = mk_contact("a", ta)
        contact_b = mk_contact("b", tb)
        item_a = mk_req_item("a", ta, contact_a, staff_a)
        item_b = mk_req_item("b", tb, contact_b, client_b)
        fr_a = mk_response("a", ta, item_a, contact_a, client_a)
        fr_b = mk_response("b", tb, item_b, contact_b, client_b)

    return {
        "tenant_a": ta,
        "staff_a": staff_a,
        "wos_staff_a": f"fwos_staff_a_{s}",
        "org_a": f"forg_a_{s}",
        "client_a": client_a,
        "wos_client_a": f"fwos_client_a_{s}",
        "contact_a": contact_a,
        "item_a": item_a,
        "fr_a": fr_a,
        "fr_b": fr_b,
        "req_a": f"freq_a_{s}",
        "req_b": f"freq_b_{s}",
    }


def _client_a_claims(seed: dict[str, object]) -> dict[str, object]:
    # A client's tenant lineage resolves empty (the 0001 safety lever); they reach rows only via
    # contact_id = ANY(auth.contact_scope()), fed by the contact_ids claim. tenant_ids is passed but
    # ignored by tenant_lineage() for role=client.
    return {
        "contact_ids": [seed["contact_a"]],
        "account_ids": [],
        "sub": seed["wos_client_a"],
        "role": "client",
        "portal": "client",
        "permissions": [],
        "org_id": seed["org_a"],
        "user_uuid": seed["client_a"],
        "tenant_id": seed["tenant_a"],
        "tenant_ids": [seed["tenant_a"]],
    }


def _staff_a_claims(seed: dict[str, object]) -> dict[str, object]:
    # Full-book adviser (contacts:read_all): since 0022 requests are book-scoped, so a book-scoped
    # session with empty contact_ids can't see/update a tenant request whose contact isn't in its
    # book. These tests assert own-tenant (not book) access, so model a full-book adviser.
    return {
        "contact_ids": [],
        "account_ids": [],
        "sub": seed["wos_staff_a"],
        "role": "adviser",
        "portal": "platform",
        "permissions": ["documents:write", "contacts:read_all"],
        "org_id": seed["org_a"],
        "user_uuid": seed["staff_a"],
        "tenant_id": seed["tenant_a"],
        "tenant_ids": [seed["tenant_a"]],
    }


def test_client_sees_only_own_form_response(app_engine: Engine, fr_seed: dict[str, object]) -> None:
    with principal_conn(app_engine, _client_a_claims(fr_seed)) as conn:
        uids = set(conn.execute(text("SELECT uid FROM documents.form_responses")).scalars())
    assert uids == {fr_seed["fr_a"]}
    assert fr_seed["fr_b"] not in uids


def test_staff_sees_tenant_form_response(app_engine: Engine, fr_seed: dict[str, object]) -> None:
    with principal_conn(app_engine, _staff_a_claims(fr_seed)) as conn:
        uids = set(conn.execute(text("SELECT uid FROM documents.form_responses")).scalars())
    assert fr_seed["fr_a"] in uids
    assert fr_seed["fr_b"] not in uids


def test_empty_claims_see_no_form_responses(app_engine: Engine, fr_seed: dict[str, object]) -> None:
    with principal_conn(app_engine, {}) as conn:
        count = conn.execute(text("SELECT count(*) FROM documents.form_responses")).scalar_one()
    assert count == 0


def test_client_cannot_insert_form_response_directly(
    app_engine: Engine, fr_seed: dict[str, object]
) -> None:
    # Persistence goes through the elevated (service_role) endpoint; a direct client write is denied
    # (the client's tenant lineage is empty, so the staff-only WITH CHECK rejects the row).
    with principal_conn(app_engine, _client_a_claims(fr_seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(
                "INSERT INTO documents.form_responses "
                "  (uid, tenant_id, request_item_id, form_uid, contact_id, answers) "
                "VALUES (:uid, :t, :iid, 'frm-fact-find', :cid, '{}'::jsonb)"
            ),
            {
                "uid": f"frsp_evil_{uuid.uuid4().hex[:6]}",
                "t": fr_seed["tenant_a"],
                "iid": fr_seed["item_a"],
                "cid": fr_seed["contact_a"],
            },
        )


def test_staff_can_update_own_request_due_date(
    app_engine: Engine, fr_seed: dict[str, object]
) -> None:
    with principal_conn(app_engine, _staff_a_claims(fr_seed)) as conn:
        row = conn.execute(
            text(
                "UPDATE documents.requests SET due_date = DATE '2026-12-31' "
                "WHERE uid = :u RETURNING id"
            ),
            {"u": fr_seed["req_a"]},
        ).first()
    assert row is not None  # RLS requests_write allows the own-tenant update


def test_staff_cannot_update_other_tenant_due_date(
    app_engine: Engine, fr_seed: dict[str, object]
) -> None:
    with principal_conn(app_engine, _staff_a_claims(fr_seed)) as conn:
        row = conn.execute(
            text(
                "UPDATE documents.requests SET due_date = DATE '2026-12-31' "
                "WHERE uid = :u RETURNING id"
            ),
            {"u": fr_seed["req_b"]},
        ).first()
    assert row is None  # RLS filters the cross-tenant row to zero
