"""Isolation proof for 0035_link_tenant_integrity — the two remaining ways a firm's data could
cross the tenant boundary through a link rather than through a policy.

PART A — auth.resolve_claims containment. 0030 established that a BYPASSRLS SECURITY DEFINER
function taking a caller-supplied argument is itself a cross-tenant oracle and must guard its own
argument. auth.resolve_claims is the biggest one of those in the schema (it is the claim minter: it
returns a principal's whole resolved scope) and it was left open, so a client-portal session of one
firm could read back another firm's adviser's entire book. The guard covers BOTH arguments — a
session with published claims may only resolve its OWN sub against its OWN org — so the
`resolve_claims(NULL, '<their org>')` spelling stays refused even though 0107/0108 deleted the
org->tenant fallback it once fed (the tenant now comes only from auth.users.tenant_id).

PART B — link tenancy. `contact_id = ANY (auth.contact_scope())` appears as the client branch of a
dozen read policies with no tenant conjunct, and the matching WITH CHECKs leave contact_id
unconstrained on the read-all branch, so a read-all staffer at Firm A could file a Firm A row
stamped with Firm B's contact and Firm B's client would then read it. Composite (tenant_id, link)
foreign keys make that row unrepresentable — for `authenticated` AND for service_role, which no
WITH CHECK binds at all.

Order-independent: unique ids per run, tenant-scoped assertions, never truncates.
"""

import json
import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.conftest import principal_conn

# SQLSTATEs the guards raise. Pinned so the contract is greppable from the app side.
INSUFFICIENT_PRIVILEGE = "42501"
FOREIGN_KEY_VIOLATION = "23503"


def _sqlstate(exc: DBAPIError) -> str:
    return str(getattr(exc.orig, "sqlstate", ""))


@pytest.fixture
def seed(super_engine: Engine) -> dict[str, object]:
    """Two unrelated firms. Firm A has an adviser with a real book (a contact + an account) — the
    thing the oracle handed out. Firm B has a client end-user linked to its own contact, which is
    the hardest boundary in the product and the session the probe ran from."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:

        def scalar(sql: str, **params: object) -> object:
            return c.execute(text(sql), params).scalar_one()

        def mk_tenant(letter: str) -> int:
            return int(
                scalar(
                    "INSERT INTO auth.tenants (uid, alias, name, workos_project_id) "
                    "VALUES (:uid, :alias, :name, :proj) RETURNING id",
                    uid=f"ten_lt_{letter}_{s}",
                    alias=f"lt-{letter}-{s}",
                    name=f"LT Firm {letter.upper()} {s}",
                    proj=f"proj_lt_{letter}_{s}",
                )
            )

        ta, tb = mk_tenant("a"), mk_tenant("b")

        # App-only portal (0107): the scope-asserting resolve calls below pass an application
        # client id, matched against these rows (+ the membership each mk_user inserts).
        for ten, letter in ((ta, "a"), (tb, "b")):
            c.execute(
                text(
                    "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                    "  client_portal_client_id, platform_client_id) "
                    "VALUES (:t, 'dev', :env, :capp, :papp)"
                ),
                {
                    "t": ten,
                    "env": f"env_lt_{letter}_{s}",
                    "capp": f"app_client_lt_{letter}_{s}",
                    "papp": f"app_platform_lt_{letter}_{s}",
                },
            )

        def mk_user(tag: str, tenant: int) -> tuple[str, str]:
            wos = f"wos_lt_{tag}_{s}"
            uid = str(
                scalar(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :email, :wos, true) RETURNING id",
                    uid=f"usr_lt_{tag}_{s}",
                    t=tenant,
                    email=f"lt_{tag}_{s}@firm.test",
                    wos=wos,
                )
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (:u, :t, :p)"
                ),
                {"u": uid, "t": tenant, "p": "client" if tag.endswith("client") else "platform"},
            )
            return uid, wos

        adviser_a, adviser_a_wos = mk_user("adviser", ta)
        staff_a, _ = mk_user("staff", ta)
        client_b, client_b_wos = mk_user("bclient", tb)

        def mk_contact(tag: str, tenant: int) -> int:
            return int(
                scalar(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name) "
                    "VALUES (:t, :uid, 'individual', :name) RETURNING id",
                    t=tenant,
                    uid=f"con_lt_{tag}_{s}",
                    name=f"Client {tag}",
                )
            )

        contact_a = mk_contact("a", ta)
        contact_b = mk_contact("b", tb)

        # Firm A's adviser owns contact_a (their book); Firm B's client IS contact_b.
        c.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :c, :u, 'adviser')"
            ),
            {"t": ta, "c": contact_a, "u": adviser_a},
        )
        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id) VALUES (:t, :c, :u)"
            ),
            {"t": tb, "c": contact_b, "u": client_b},
        )
        account_a = int(
            scalar(
                "INSERT INTO platform.accounts (tenant_id, contact_id, uid) "
                "VALUES (:t, :c, :uid) RETURNING id",
                t=ta,
                c=contact_a,
                uid=f"acc_lt_a_{s}",
            )
        )
        file_a = str(
            scalar(
                "INSERT INTO documents.files (tenant_id, bucket, storage_key, content_type, "
                "  size_bytes, sha256) VALUES (:t, 'b', :k, 'application/pdf', 1, 'x') "
                "RETURNING id",
                t=ta,
                k=f"uploads/{ta}/lt_{s}",
            )
        )

    return {
        "tenant_a": ta,
        "tenant_b": tb,
        "org_a": f"org_lt_a_{s}",
        "org_b": f"org_lt_b_{s}",
        "app_platform_a": f"app_platform_lt_a_{s}",
        "app_client_b": f"app_client_lt_b_{s}",
        "adviser_a": adviser_a,
        "adviser_a_wos": adviser_a_wos,
        "staff_a": staff_a,
        "client_b": client_b,
        "client_b_wos": client_b_wos,
        "contact_a": contact_a,
        "contact_b": contact_b,
        "account_a": account_a,
        "file_a": file_a,
        "suffix": s,
    }


def _client_b_claims(seed: dict[str, object]) -> dict[str, object]:
    """The probe's session: Firm B's client end-user. Empty lineage (the client-portal safety
    lever), one contact of its own, no permissions."""
    return {
        "sub": seed["client_b_wos"],
        "role": "advised-client",
        "portal": "client",
        "permissions": [],
        "org_id": seed["org_b"],
        "user_uuid": seed["client_b"],
        "tenant_id": seed["tenant_b"],
        "tenant_ids": [],
        "contact_ids": [seed["contact_b"]],
        "account_ids": [],
    }


def _read_all_a_claims(seed: dict[str, object]) -> dict[str, object]:
    """A Firm A staffer with the firm-wide client lever — the actor in the reported WITH CHECK
    finding (the narrower one: it takes read-all, not just any staffer)."""
    return {
        "sub": f"wos_lt_staff_{seed['suffix']}",
        "role": "compliance",
        "portal": "platform",
        "permissions": ["contacts:read_all", "documents:write"],
        "org_id": seed["org_a"],
        "user_uuid": seed["staff_a"],
        "tenant_id": seed["tenant_a"],
        "tenant_ids": [seed["tenant_a"]],
        "contact_ids": [],
        "account_ids": [],
    }


# ==============================================================================================
# PART A — auth.resolve_claims containment
# ==============================================================================================

RESOLVE = "SELECT auth.resolve_claims(:u, :o)"
# The request-path spelling (0107): arg 3 is the token's application client id, which is what
# classifies the `portal` claim — and therefore whether any contact/account scope is minted.
RESOLVE_APP = "SELECT auth.resolve_claims(:u, :o, :c)"


def test_resolve_claims_does_not_hand_another_firms_book_to_a_client_session(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The reported leak, verbatim: from a Firm B CLIENT-portal session, resolve Firm A's adviser +
    Firm A's org and read back {tenant_id, tenant_ids, contact_ids, account_ids} — that adviser's
    whole book. It must now be refused, not merely emptied: returning a stripped answer would still
    confirm the ids exist."""
    with principal_conn(app_engine, _client_b_claims(seed)) as conn:
        with pytest.raises(DBAPIError) as excinfo:
            conn.execute(text(RESOLVE), {"u": seed["adviser_a_wos"], "o": seed["org_a"]})
        assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE


def test_the_org_only_spelling_is_refused_too(app_engine: Engine, seed: dict[str, object]) -> None:
    """The variation that beats a user-id-only guard. 0108 deleted the org->tenant fallback this
    spelling once fed (a NULL user id now resolves nothing at all), but the containment guard
    still refuses it OUTRIGHT rather than answering an empty shape — an empty answer would still
    confirm which calls are well-formed. Same for a non-existent user id."""
    for user_arg in (None, "wos_no_such_user"):
        # A fresh connection per attempt: the raise aborts the transaction, and a second statement
        # in an aborted transaction reports 25P02, which would mask the SQLSTATE under test.
        with principal_conn(app_engine, _client_b_claims(seed)) as conn:
            with pytest.raises(DBAPIError) as excinfo:
                conn.execute(text(RESOLVE), {"u": user_arg, "o": seed["org_a"]})
            assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE


def test_own_identity_with_a_foreign_org_is_refused(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The third spelling: your own user id, someone else's org. The org no longer classifies
    anything (0107: the portal comes from the application client id), but the containment guard
    still requires a published session to name its OWN org — a mismatched org is a probe."""
    with principal_conn(app_engine, _client_b_claims(seed)) as conn:
        with pytest.raises(DBAPIError) as excinfo:
            conn.execute(text(RESOLVE), {"u": seed["client_b_wos"], "o": seed["org_a"]})
        assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE


def test_a_session_may_still_resolve_itself(app_engine: Engine, seed: dict[str, object]) -> None:
    """The guard must be transparent to the legitimate question. Own sub + own org resolves (the
    client application's id classifies the portal, so the own-contact scope is minted), and a
    NULL org (the shape app/provisioning tests use) is allowed against one's own identity."""
    with principal_conn(app_engine, _client_b_claims(seed)) as conn:
        resolved = conn.execute(
            text(RESOLVE_APP),
            {"u": seed["client_b_wos"], "o": seed["org_b"], "c": seed["app_client_b"]},
        ).scalar_one()
        assert resolved["tenant_id"] == seed["tenant_b"]
        assert resolved["portal"] == "client"
        assert resolved["contact_ids"] == [seed["contact_b"]]
        assert (
            conn.execute(text(RESOLVE), {"u": seed["client_b_wos"], "o": None}).scalar_one()[
                "tenant_id"
            ]
            == seed["tenant_b"]
        )


def test_the_mint_path_still_works_with_no_claims(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """THE REQUEST PATH. db.py::tenant_session does `SET LOCAL ROLE authenticated` and then calls
    resolve_claims BEFORE publishing request.jwt.claims, so the minting call always arrives with
    auth.jwt() = '{}'. Break this and every authenticated request 500s — which is why it is pinned
    here rather than left to the API suites."""
    with principal_conn(app_engine, {}) as conn:
        resolved = conn.execute(
            text(RESOLVE_APP),
            {"u": seed["adviser_a_wos"], "o": seed["org_a"], "c": seed["app_platform_a"]},
        ).scalar_one()
        assert resolved["tenant_id"] == seed["tenant_a"]
        assert resolved["portal"] == "platform"
        assert resolved["contact_ids"] == [seed["contact_a"]]
        assert resolved["account_ids"] == [seed["account_a"]]


def test_graphene_ops_keeps_the_cross_tenant_view(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The guard keys off auth.is_system() exactly like 0030's accessors and the auth.tenants
    policy — and after 0034 that means the `system` PERMISSION, which a firm cannot grant itself."""
    claims = {**_read_all_a_claims(seed), "permissions": ["system"]}
    with principal_conn(app_engine, claims) as conn:
        resolved = conn.execute(
            text(RESOLVE), {"u": seed["client_b_wos"], "o": seed["org_b"]}
        ).scalar_one()
    assert resolved["tenant_id"] == seed["tenant_b"]


def test_a_tenant_role_named_admin_does_not_unlock_the_oracle(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The obvious way to try to get round an is_system()-shaped escape hatch, post-0034: a firm
    naming its own WorkOS role `admin`/`system`. The role claim confers nothing."""
    for name in ("admin", "system", "tenant:admin"):
        claims = {**_client_b_claims(seed), "role": name}
        with principal_conn(app_engine, claims) as conn:
            with pytest.raises(DBAPIError) as excinfo:
                conn.execute(text(RESOLVE), {"u": seed["adviser_a_wos"], "o": seed["org_a"]})
            assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE


def test_the_minter_will_not_import_another_firms_contact_into_a_claim(
    app_engine: Engine, super_engine: Engine, seed: dict[str, object]
) -> None:
    """Defence in depth on the other side of the same function: resolve_claims resolved contact
    scope by user_id alone, so one mis-tenanted crm.contact_users row would put another firm's
    contact into a login's contact_ids — the root of every contact_scope() policy branch. The scope
    lookups are now tenant-scoped, so even a link row that should not exist cannot widen the claim.

    Part B makes the row unrepresentable, so the hazard is staged the only way left: a link stamped
    with the WRONG OWN tenant (Firm B's contact + Firm B's tenant_id, attached to Firm A's adviser),
    which the composite FK permits because it constrains the link, not the login."""
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :c, :u, 'adviser')"
            ),
            {"t": seed["tenant_b"], "c": seed["contact_b"], "u": seed["adviser_a"]},
        )
    with principal_conn(app_engine, {}) as conn:
        resolved = conn.execute(
            text(RESOLVE_APP),
            {"u": seed["adviser_a_wos"], "o": seed["org_a"], "c": seed["app_platform_a"]},
        ).scalar_one()
    assert resolved["contact_ids"] == [seed["contact_a"]]  # never contact_b
    assert seed["contact_b"] not in resolved["contact_ids"]


# ==============================================================================================
# PART B — a row's client/account link cannot leave the row's firm
# ==============================================================================================


def test_read_all_staff_cannot_stamp_another_firms_contact_on_an_attachment(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The reported finding, verbatim: a Firm A read-all staffer files a Firm A attachment carrying
    Firm B's contact_id. It was ACCEPTED (attachments_write's WITH CHECK says nothing about
    contact_id on the can_read_all() branch) and Firm B's own client could then read the row through
    their contact scope. The composite FK refuses the INSERT, and the same session's legitimate
    same-firm insert still works."""
    with principal_conn(app_engine, _read_all_a_claims(seed)) as conn:
        with pytest.raises(DBAPIError) as excinfo:
            conn.execute(
                text(
                    "INSERT INTO documents.attachments (uid, tenant_id, file_id, contact_id) "
                    "VALUES (:uid, :t, CAST(:f AS uuid), :c)"
                ),
                {
                    "uid": f"att_lt_bad_{seed['suffix']}",
                    "t": seed["tenant_a"],
                    "f": seed["file_a"],
                    "c": seed["contact_b"],
                },
            )
        assert _sqlstate(excinfo.value) == FOREIGN_KEY_VIOLATION

    with principal_conn(app_engine, _read_all_a_claims(seed)) as conn:
        conn.execute(
            text(
                "INSERT INTO documents.attachments (uid, tenant_id, file_id, contact_id) "
                "VALUES (:uid, :t, CAST(:f AS uuid), :c)"
            ),
            {
                "uid": f"att_lt_ok_{seed['suffix']}",
                "t": seed["tenant_a"],
                "f": seed["file_a"],
                "c": seed["contact_a"],
            },
        )
        conn.commit()


def test_the_exploit_is_demonstrated_not_assumed(
    super_engine: Engine, seed: dict[str, object]
) -> None:
    """A CONTROL for the test above, in the repo's house style: with the constraint dropped, the
    poisoned row really is readable by the other firm's client — so the constraint is what closes
    it, not an accident of the fixture.

    All in one rolled-back transaction: drop the composite FK, insert Firm A's attachment carrying
    Firm B's contact_id, then become `authenticated` with Firm B's client claims on the same
    connection (superuser bypasses RLS; `authenticated` does not, and FORCE ROW LEVEL SECURITY is
    on) and read it back through attachments_read's client branch."""
    txn_engine = super_engine.execution_options(isolation_level="READ COMMITTED")
    uid = f"att_lt_control_{seed['suffix']}"
    with txn_engine.connect() as c:
        c.execute(
            text(
                "ALTER TABLE documents.attachments DROP CONSTRAINT attachments_contact_tenant_fkey"
            )
        )
        c.execute(
            text(
                "INSERT INTO documents.attachments (uid, tenant_id, file_id, contact_id) "
                "VALUES (:uid, :t, CAST(:f AS uuid), :c)"
            ),
            {"uid": uid, "t": seed["tenant_a"], "f": seed["file_a"], "c": seed["contact_b"]},
        )
        c.execute(text("SET LOCAL ROLE authenticated"))
        c.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"),
            {"c": json.dumps(_client_b_claims(seed))},
        )
        leaked = c.execute(
            text("SELECT count(*) FROM documents.attachments WHERE uid = :uid"), {"uid": uid}
        ).scalar_one()
        c.rollback()
    assert leaked == 1, "the control failed: the exploit no longer reproduces, so it proves nothing"


@pytest.mark.parametrize(
    ("table", "columns", "values"),
    [
        # The sibling tables a policy-only fix would have left open. document_contacts is the worst
        # of them: documents_read's client branch joins through it, so one poisoned link is a
        # cross-firm DOCUMENT read, not just a stray row.
        (
            "documents.document_contacts",
            "document_id, tenant_id, contact_id",
            "CAST(:doc AS uuid), :t, :c",
        ),
        ("documents.requests", "uid, tenant_id, contact_id, requested_by", ":uid, :t, :c, :u"),
        # 0021 calls intended_contact_id "read by no RLS policy", which is true and not the whole
        # story: app/worker.py reads it to decide which client a queued render is FOR, and that
        # contact becomes the render's :subject_id. This one was found by the derived invariant test
        # in test_schema_invariants.py, not by reading the audit.
        (
            "documents.documents",
            "uid, tenant_id, document_type, intended_contact_id",
            ":uid, :t, 'other', :c",
        ),
        (
            "tasks.tasks",
            "uid, tenant_id, assignee_kind, assignee_contact_id, title",
            ":uid, :t, 'contact', :c, 'x'",
        ),
        ("crm.kyc_checks", "uid, tenant_id, contact_id, kind", ":uid, :t, :c, 'identity'"),
        (
            "platform.instructions",
            "uid, tenant_id, account_id, contact_id, kind, amount",
            ":uid, :t, :acc, :c, 'withdrawal', 1",
        ),
        ("crm.contact_users", "tenant_id, contact_id, user_id", ":t, :c, :u"),
        ("crm.client_advisers", "tenant_id, contact_id, adviser_id, role", ":t, :c, :u, 'adviser'"),
    ],
)
def test_no_role_at_all_can_write_a_cross_tenant_client_link(
    super_engine: Engine, seed: dict[str, object], table: str, columns: str, values: str
) -> None:
    """The reason this is a constraint and not a WITH CHECK edit. Every one of these tables is also
    written by service_role (firm_session / admin_session / the worker), where RLS — and therefore
    every WITH CHECK in 0016/0018/0031 — does not apply at all. Run here as the SUPERUSER, the most
    privileged writer there is: referential integrity still refuses the row.

    crm.contact_users / crm.client_advisers are in the list because they define contact scope
    itself: a mis-tenanted row there is how another firm's contact would get into a claim.
    """
    doc_id = None
    if table == "documents.document_contacts":
        with super_engine.connect() as c:
            doc_id = str(
                c.execute(
                    text(
                        "INSERT INTO documents.documents (uid, tenant_id, document_type) "
                        "VALUES (:uid, :t, 'other') RETURNING id"
                    ),
                    {"uid": f"doc_lt_{seed['suffix']}", "t": seed["tenant_a"]},
                ).scalar_one()
            )
    params = {
        "uid": f"lt_bad_{uuid.uuid4().hex[:8]}",
        "t": seed["tenant_a"],
        "c": seed["contact_b"],  # Firm B's contact on a Firm A row
        "u": seed["staff_a"],
        "acc": seed["account_a"],
        "doc": doc_id,
    }
    with super_engine.connect() as c, pytest.raises(DBAPIError) as excinfo:
        c.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({values})"), params)  # noqa: S608
    assert _sqlstate(excinfo.value) == FOREIGN_KEY_VIOLATION


def test_an_account_link_cannot_leave_its_firm_either(
    super_engine: Engine, seed: dict[str, object]
) -> None:
    """account_scope() is the other half of client visibility — platform.instructions' read policy
    matches on it directly — so the account link needs the same containment as the contact link."""
    with super_engine.connect() as c:
        acc_b = int(
            c.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, contact_id, uid) "
                    "VALUES (:t, :c, :uid) RETURNING id"
                ),
                {
                    "t": seed["tenant_b"],
                    "c": seed["contact_b"],
                    "uid": f"acc_lt_b_{seed['suffix']}",
                },
            ).scalar_one()
        )
    with super_engine.connect() as c, pytest.raises(DBAPIError) as excinfo:
        c.execute(
            text(
                "INSERT INTO platform.instructions (uid, tenant_id, account_id, kind, amount) "
                "VALUES (:uid, :t, :a, 'withdrawal', 1)"
            ),
            {"uid": f"ins_lt_bad_{seed['suffix']}", "t": seed["tenant_a"], "a": acc_b},
        )
    assert _sqlstate(excinfo.value) == FOREIGN_KEY_VIOLATION


def test_a_contact_cannot_be_moved_to_another_firm_while_linked(
    super_engine: Engine, seed: dict[str, object]
) -> None:
    """The other direction, which a WITH CHECK could never have covered: poison the link by moving
    the CONTACT rather than by writing the row. ON UPDATE NO ACTION (the FK default) refuses it."""
    with super_engine.connect() as c, pytest.raises(DBAPIError) as excinfo:
        c.execute(
            text("UPDATE crm.contacts SET tenant_id = :tb WHERE id = :c"),
            {"tb": seed["tenant_b"], "c": seed["contact_a"]},
        )
    assert _sqlstate(excinfo.value) == FOREIGN_KEY_VIOLATION


def test_a_firm_internal_row_with_no_client_link_is_unaffected(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """MATCH SIMPLE: a NULL link is unconstrained, which is the existing semantics for every
    nullable column here (a firm-internal file, a standalone form response). Pinned so nobody
    'tightens' the constraint into breaking firm-internal work."""
    with principal_conn(app_engine, _read_all_a_claims(seed)) as conn:
        conn.execute(
            text(
                "INSERT INTO documents.attachments (uid, tenant_id, file_id, contact_id) "
                "VALUES (:uid, :t, CAST(:f AS uuid), NULL)"
            ),
            {
                "uid": f"att_lt_internal_{seed['suffix']}",
                "t": seed["tenant_a"],
                "f": seed["file_a"],
            },
        )
        conn.commit()
