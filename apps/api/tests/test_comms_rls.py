"""Row-level security on the outbound-email ledger, against real Postgres.

Three claims, and the second is the one that needs a test rather than a code review:

1. A firm reads its own messages and no other firm's.
2. **A CLIENT reads none of it** — including messages addressed to them. ``comms.messages`` scopes
   by ``auth.tenant_lineage()`` and deliberately omits ``auth.contact_scope()``. The omission is
   invisible in the policy text unless you know to look for what is missing, and the failure it
   prevents is quiet: an adviser's contact scope is their whole book, so adding ``contact_scope``
   "for symmetry" would hand a client-portal principal the ledger of every mail sent about them,
   ``html`` bodies included. This file is what makes that a failing test rather than a review note.
3. A Graphene-side message (``tenant_id IS NULL``) is readable by operations alone — so an
   unattributable send is visible where it can be acted on, and nowhere else.

Modelled on ``test_aml_rls.py``, which pins the same shape of decision for screening, and it
deliberately asserts on ``pg_policies`` text as well as behaviour: the behavioural test passes for
a policy that happens to have no matching rows today, and the text assertion is what fails when
someone adds the clause.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from tests.conftest import principal_conn

pytestmark = pytest.mark.usefixtures("_storage_unconfigured")


@pytest.fixture
def comms_seed(super_engine: Engine) -> Iterator[dict[str, object]]:
    """Two firms with a message each, one Graphene-side message with no firm, and a real client
    login attached to Firm A's recipient contact.

    That login is the whole point: it makes the client principal's ``contact_scope()`` non-empty
    and pointed AT the very contact Firm A's message was sent to — i.e. exactly what a
    ``contact_scope`` clause in the policy WOULD match. Without it the client test would pass
    against a policy that leaks, because the client would have no scope to leak through.

    Cleans up its own two tenants (CASCADE takes users, contacts and messages with them) and its
    one unattributed row, so it neither depends on nor disturbs the rest of the suite.
    """
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:

        def mk_tenant(tag: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:uid, :alias, :name) RETURNING id"
                    ),
                    {
                        "uid": f"ten_{tag}_{s}",
                        "alias": f"firm-{tag}-{s}",
                        "name": f"Firm {tag.upper()} {s}",
                    },
                ).scalar_one()
            )

        def mk_user(tag: str, tenant: int) -> tuple[str, str]:
            workos = f"wos_{tag}_{s}"
            uid = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                        "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                    ),
                    {
                        "uid": f"usr_{tag}_{s}",
                        "t": tenant,
                        "email": f"{tag}-{s}@firm.test",
                        "wos": workos,
                    },
                ).scalar_one()
            )
            return uid, workos

        def mk_message(tag: str, tenant: int | None, contact: int | None) -> str:
            uid = f"msg{tag}{s}"[:12].ljust(12, "0")
            c.execute(
                text(
                    "INSERT INTO comms.messages (uid, tenant_id, recipient_kind, "
                    "  recipient_contact_id, to_address, to_domain, subject, status, html) "
                    "VALUES (:uid, :t, :kind, :con, :addr, 'firm.test', :subj, 'sent', :html)"
                ),
                {
                    "uid": uid,
                    "t": tenant,
                    "kind": "contact" if contact is not None else "external",
                    "con": contact,
                    "addr": f"{tag}-{s}@firm.test",
                    "subj": f"Message {tag} {s}",
                    "html": "<p>the body a client must not be able to read</p>",
                },
            )
            return uid

        tenant_a = mk_tenant("a")
        tenant_b = mk_tenant("b")
        staff_a, wos_staff_a = mk_user("staff_a", tenant_a)
        client_a, wos_client_a = mk_user("client_a", tenant_a)
        staff_b, wos_staff_b = mk_user("staff_b", tenant_b)

        contact_a = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, surname, service_type) "
                    "VALUES (:t, :uid, 'individual', 'Okafor', 'advised') RETURNING id"
                ),
                {"t": tenant_a, "uid": f"con_a_{s}"},
            ).scalar_one()
        )
        # login <-> party: the client end-user IS contact_a, so `contact_ids` in their claims is
        # non-empty and covers the recipient of Firm A's message.
        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id, capacity) "
                "VALUES (:t, :con, :u, 'self')"
            ),
            {"t": tenant_a, "con": contact_a, "u": client_a},
        )

        msg_a = mk_message("a", tenant_a, contact_a)
        msg_b = mk_message("b", tenant_b, None)
        msg_none = mk_message("n", None, None)  # a Graphene-side send: no firm
        c.commit()

        yield {
            "tenant_a": tenant_a,
            "tenant_b": tenant_b,
            "staff_a": staff_a,
            "wos_staff_a": wos_staff_a,
            "client_a": client_a,
            "wos_client_a": wos_client_a,
            "staff_b": staff_b,
            "wos_staff_b": wos_staff_b,
            "contact_a": contact_a,
            "msg_a": msg_a,
            "msg_b": msg_b,
            "msg_none": msg_none,
        }

        # Children BEFORE the tenant, and NOT one cascading delete. `crm.contacts` carries an
        # `audit.record()` trigger that writes an audit.log row keyed by tenant_id, so a cascade
        # from auth.tenants fails on a foreign key: by the time the cascade fires the child's
        # trigger, the parent tenant it wants to reference is already gone. Deleting the children
        # first keeps the tenant alive for their audit rows, and the final delete cascades those
        # away (audit.log.tenant_id is ON DELETE CASCADE). test_aml_rls.py records the same trap.
        for stmt in (
            "DELETE FROM comms.messages WHERE tenant_id = ANY(:ids)",
            "DELETE FROM crm.contact_users WHERE tenant_id = ANY(:ids)",
            "DELETE FROM crm.contacts WHERE tenant_id = ANY(:ids)",
            "DELETE FROM auth.users WHERE tenant_id = ANY(:ids)",
            "DELETE FROM auth.tenants WHERE id = ANY(:ids)",
        ):
            c.execute(text(stmt), {"ids": [tenant_a, tenant_b]})
        c.execute(text("DELETE FROM comms.messages WHERE uid = :u"), {"u": msg_none})
        c.commit()


def _staff_claims(seed: dict[str, object], firm: str = "a") -> dict[str, object]:
    return {
        "sub": seed[f"wos_staff_{firm}"],
        "role": "adviser",
        "portal": "platform",
        "permissions": ["activity:read"],
        "org_id": f"org_{firm}",
        "user_uuid": seed[f"staff_{firm}"],
        "tenant_id": seed[f"tenant_{firm}"],
        "tenant_ids": [seed[f"tenant_{firm}"]],
        "contact_ids": [],
        "account_ids": [],
    }


def _client_claims(seed: dict[str, object]) -> dict[str, object]:
    """The hostile-but-realistic client principal: the recipient's own login, with `tenant_ids`
    deliberately populated (as a real client token is) so the test proves the suppression in
    `auth.tenant_lineage()` rather than an accidentally empty claim."""
    return {
        "sub": seed["wos_client_a"],
        "role": "advised-client",  # not literally 'client' — the gate is the portal claim
        "portal": "client",
        "permissions": ["activity:read"],  # hostile on purpose; suppressed for a client portal
        "org_id": "org_a",
        "user_uuid": seed["client_a"],
        "tenant_id": seed["tenant_a"],
        "tenant_ids": [seed["tenant_a"]],
        "contact_ids": [seed["contact_a"]],  # the recipient of Firm A's message
        "account_ids": [],
    }


def _uids(conn: object) -> set[str]:
    return set(conn.execute(text("SELECT uid FROM comms.messages")).scalars())  # type: ignore[attr-defined]


def test_each_firm_reads_its_own_messages_and_no_others(
    app_engine: Engine, comms_seed: dict[str, object]
) -> None:
    """Both directions, because one of them alone cannot tell a scoped policy from a hardcoded
    one: a predicate stuck on Firm A passes A's half and fails B's."""
    with principal_conn(app_engine, _staff_claims(comms_seed)) as conn:
        a_sees = _uids(conn)
    with principal_conn(app_engine, _staff_claims(comms_seed, "b")) as conn:
        b_sees = _uids(conn)

    assert comms_seed["msg_a"] in a_sees
    assert comms_seed["msg_b"] not in a_sees
    assert comms_seed["msg_none"] not in a_sees, "an unattributed row is not any firm's"

    assert comms_seed["msg_b"] in b_sees
    assert comms_seed["msg_a"] not in b_sees


def test_a_client_cannot_read_the_ledger_of_mail_sent_to_them(
    app_engine: Engine, comms_seed: dict[str, object]
) -> None:
    """The load-bearing one. This principal's `contact_scope()` covers the exact contact Firm A's
    message was addressed to, so a policy carrying a `contact_scope` clause would return it. A
    client has their own inbox; the ledger — including the stored `html` body — is the firm's
    operational record."""
    with principal_conn(app_engine, _client_claims(comms_seed)) as conn:
        assert _uids(conn) == set()
        # And not through the child table either, which carries its own denormalised tenant_id.
        assert conn.execute(text("SELECT count(*) FROM comms.message_events")).scalar_one() == 0


def test_absent_claims_read_nothing(app_engine: Engine, comms_seed: dict[str, object]) -> None:
    """Fail closed: a mis-wired request path with no claims at all resolves no lineage, and must
    therefore see nothing rather than everything."""
    with principal_conn(app_engine, {}) as conn:
        assert _uids(conn) == set()


def test_an_unattributed_message_is_operations_only(
    app_engine: Engine, comms_seed: dict[str, object]
) -> None:
    """A Graphene-side send has no firm. It must be visible to operations — an unattributable row
    that nobody can see is the 0038 failure, where the gap the record exists to show becomes
    invisible — and to no firm."""
    ops = _staff_claims(comms_seed)
    ops["portal"] = "operations"
    ops["permissions"] = ["system"]
    with principal_conn(app_engine, ops) as conn:
        assert comms_seed["msg_none"] in _uids(conn)


def test_no_comms_policy_mentions_contact_scope(super_engine: Engine) -> None:
    """The text assertion behind the behavioural one. A `contact_scope` clause added "for symmetry"
    with 0026 would leak every client's own mail to them, and the behavioural test above could not
    catch every future shape of that mistake — this catches the clause itself.

    If you are here because this failed: the omission is deliberate. See the policy comment in
    `0116_comms_messages.sql` and the same argument for screening in docs/aml.md.
    """
    with super_engine.connect() as conn:
        policies = conn.execute(
            text(
                "SELECT policyname, COALESCE(qual, '') || ' ' || COALESCE(with_check, '') "
                "FROM pg_policies WHERE schemaname = 'comms'"
            )
        ).all()
    assert policies, "comms.* has no policies at all — RLS is not doing anything"
    offenders = [name for name, body in policies if "contact_scope" in body]
    assert not offenders, (
        f"these comms policies mention contact_scope: {offenders}. A client must not read the "
        "ledger of mail sent about them."
    )


@pytest.mark.parametrize(
    ("verb", "sql"),
    [
        (
            "INSERT",
            "INSERT INTO comms.messages (uid, tenant_id, recipient_kind, to_address, to_domain, "
            "  subject, status) VALUES ('zzz000000001', NULL, 'external', 'x@y.z', 'y.z', 's', "
            "  'sent')",
        ),
        ("UPDATE", "UPDATE comms.messages SET status = 'delivered'"),
        ("DELETE", "DELETE FROM comms.messages"),
        (
            "INSERT-events",
            "INSERT INTO comms.message_events (message_id, event_type) VALUES (1, 'x')",
        ),
    ],
)
def test_authenticated_cannot_write_the_ledger(
    app_engine: Engine, comms_seed: dict[str, object], verb: str, sql: str
) -> None:
    """SELECT and nothing else. The ledger is the record of what was sent, and the monotonic status
    rule only holds while `app/comms/ledger.py` is the sole writer.

    Parametrised so a failure names the verb that got through, rather than reporting "a write
    succeeded" and leaving you to find which.
    """
    from sqlalchemy.exc import DBAPIError

    with principal_conn(app_engine, _staff_claims(comms_seed)) as conn:
        with pytest.raises(DBAPIError, match="permission denied"):
            conn.execute(text(sql))
        conn.rollback()
