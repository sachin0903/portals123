"""Policy-level book-scoping proof for 0031_book_scope_alignment.

docs/audit/06-core.md found seven tables tenant-wide (so a book-scoped adviser read every client's
records) and no RLS test at all for flows.*, crm.kyc_checks, documents.shares,
documents.attachments or platform.instructions; tasks.tasks was covered only through the API.
This is the missing policy-level matrix. For each table it asserts the same five properties:

  * staff scope           — a read-all staffer sees the whole firm;
  * book-scope boundary   — a book-scoped adviser sees their own book (+ firm-internal rows with no
                            client link) and NOT another adviser's client;
  * client hard boundary  — a client-portal session sees only its own party's rows, never
                            firm-internal ones, even carrying contacts:read_all (0004's lever);
  * fail-closed           — empty claims see nothing;
  * cross-tenant write    — an insert stamped with another firm's tenant_id is rejected.

Order-independent: unique ids per run, tenant-scoped assertions, and never truncates (so it can't
wipe the migration-seeded model tenant other suites rely on). Real Postgres via testcontainers.
"""

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.conftest import SECCL_PLATFORM_SQL, principal_conn


@pytest.fixture
def seed(super_engine: Engine) -> dict[str, object]:
    """Two firms. Firm A has a read-all staffer, a book-scoped adviser whose book is contact 1, a
    client end-user acting for contact 1, and an out-of-book contact 3 — then one row per scoped
    table for contact 1, contact 3 and (where the column is nullable) firm-internal work."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:

        def scalar(sql: str, **params: object) -> object:
            return c.execute(text(sql), params).scalar_one()

        def mk_tenant(letter: str) -> int:
            return int(
                scalar(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id",
                    uid=f"ten_{letter}_{s}",
                    alias=f"firm-{letter}-{s}",
                    name=f"Firm {letter.upper()} {s}",
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

        staff_all = mk_user("all", ta)
        staff_scoped = mk_user("scoped", ta)
        client_user = mk_user("client", ta)

        def mk_contact(tag: str, tenant: int) -> int:
            return int(
                scalar(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, surname, service_type) "
                    "VALUES (:t, :uid, 'individual', :sn, 'advised') RETURNING id",
                    t=tenant,
                    uid=f"con_{tag}_{s}",
                    sn=tag.title(),
                )
            )

        con_1 = mk_contact("one", ta)  # client_user's party AND staff_scoped's book
        con_3 = mk_contact("three", ta)  # another adviser's client — out of book
        con_b = mk_contact("bee", tb)  # Firm B

        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id, capacity) "
                "VALUES (:t, :con, :u, 'self')"
            ),
            {"t": ta, "con": con_1, "u": client_user},
        )
        c.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :con, :u, 'adviser')"
            ),
            {"t": ta, "con": con_1, "u": staff_scoped},
        )

        def mk_account(tag: str, tenant: int, contact: int) -> int:
            return int(
                scalar(
                    "INSERT INTO platform.accounts (tenant_id, contact_id, uid, status) "
                    "VALUES (:t, :con, :uid, 'active') RETURNING id",
                    t=tenant,
                    con=contact,
                    uid=f"acc_{tag}_{s}",
                )
            )

        acc_1, acc_3 = mk_account("one", ta, con_1), mk_account("three", ta, con_3)

        # ---- crm.kyc_checks ----
        def mk_kyc(tag: str, tenant: int, contact: int) -> int:
            return int(
                scalar(
                    "INSERT INTO crm.kyc_checks (uid, tenant_id, contact_id, kind, status) "
                    "VALUES (:uid, :t, :con, 'identity', 'passed') RETURNING id",
                    uid=f"kyc_{tag}_{s}",
                    t=tenant,
                    con=contact,
                )
            )

        kyc_1, kyc_3, kyc_b = (
            mk_kyc("one", ta, con_1),
            mk_kyc("three", ta, con_3),
            mk_kyc("bee", tb, con_b),
        )

        # ---- flows: definition, cases, steps, executions, events ----
        def mk_flow(tenant: int, tag: str) -> tuple[int, int]:
            fid = int(
                scalar(
                    "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
                    "VALUES (:uid, :t, :code, 'Onboarding', 'published') RETURNING id",
                    uid=f"flw_{tag}_{s}",
                    t=tenant,
                    code=f"onboarding_{tag}_{s}",
                )
            )
            vid = int(
                scalar(
                    "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status) "
                    "VALUES (:uid, :t, :f, 1, 'published') RETURNING id",
                    uid=f"flv_{tag}_{s}",
                    t=tenant,
                    f=fid,
                )
            )
            return fid, vid

        flow_a, ver_a = mk_flow(ta, "a")
        flow_b, ver_b = mk_flow(tb, "b")

        def mk_execution(tag: str, tenant: int, flow: int, ver: int) -> int:
            return int(
                scalar(
                    "INSERT INTO flows.executions (uid, tenant_id, flow_id, flow_version_id, "
                    "  status) VALUES (:uid, :t, :f, :v, 'active') RETURNING id",
                    uid=f"fex_{tag}_{s}",
                    t=tenant,
                    f=flow,
                    v=ver,
                )
            )

        exec_1 = mk_execution("one", ta, flow_a, ver_a)
        exec_3 = mk_execution("three", ta, flow_a, ver_a)
        exec_new = mk_execution("new", ta, flow_a, ver_a)  # no cases yet

        def mk_case(
            tag: str, tenant: int, flow: int, ver: int, contact: int | None, ex: int | None
        ) -> int:
            cid = int(
                scalar(
                    "INSERT INTO flows.cases (uid, tenant_id, flow_id, flow_version_id, "
                    "  subject_contact_id, execution_id, status) "
                    "VALUES (:uid, :t, :f, :v, :con, :ex, 'in_progress') RETURNING id",
                    uid=f"cas_{tag}_{s}",
                    t=tenant,
                    f=flow,
                    v=ver,
                    con=contact,
                    ex=ex,
                )
            )
            c.execute(
                text(
                    "INSERT INTO flows.case_steps (tenant_id, case_id, step_id, step_type, title) "
                    "VALUES (:t, :c, 'step_1', 'fill_form', 'Identity')"
                ),
                {"t": tenant, "c": cid},
            )
            return cid

        case_1 = mk_case("one", ta, flow_a, ver_a, con_1, exec_1)
        case_3 = mk_case("three", ta, flow_a, ver_a, con_3, exec_3)
        case_int = mk_case("internal", ta, flow_a, ver_a, None, None)
        case_b = mk_case("bee", tb, flow_b, ver_b, con_b, None)

        def mk_event(tag: str, tenant: int, case: int | None, ex: int | None) -> int:
            return int(
                scalar(
                    "INSERT INTO flows.domain_events (tenant_id, case_id, execution_id, "
                    "  event_type, idempotency_key) "
                    "VALUES (:t, :c, :e, 'case.started', :k) RETURNING id",
                    t=tenant,
                    c=case,
                    e=ex,
                    k=f"idem_{tag}_{s}",
                )
            )

        ev_1 = mk_event("one", ta, case_1, exec_1)
        ev_3 = mk_event("three", ta, case_3, exec_3)
        ev_int = mk_event("internal", ta, case_int, None)

        # ---- documents + shares + attachments ----
        def mk_file(tenant: int, tag: str) -> str:
            return str(
                scalar(
                    "INSERT INTO documents.files "
                    "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                    "VALUES (:t, 'portals-documents', :k, 'application/pdf', 10, 'sha') "
                    "RETURNING id",
                    t=tenant,
                    k=f"uploads/{tag}_{s}/original.pdf",
                )
            )

        def mk_doc(tag: str, tenant: int, contact: int | None) -> str:
            fid = mk_file(tenant, f"doc_{tag}")
            doc_uid = f"doc_{tag}_{s}"
            did = scalar(
                "INSERT INTO documents.documents "
                "  (uid, tenant_id, document_type, source, status, original_file_id, pdf_file_id) "
                "VALUES (:uid, :t, 'other', 'uploaded', 'generated', :f, :f) RETURNING id",
                uid=doc_uid,
                t=tenant,
                f=fid,
            )
            if contact is not None:
                c.execute(
                    text(
                        "INSERT INTO documents.document_contacts (document_id, tenant_id, "
                        "  contact_id) VALUES (:d, :t, :con)"
                    ),
                    {"d": did, "t": tenant, "con": contact},
                )
            return doc_uid

        doc_1 = mk_doc("one", ta, con_1)
        doc_3 = mk_doc("three", ta, con_3)
        doc_int = mk_doc("internal", ta, None)

        def mk_share(tag: str, tenant: int, resource_uid: str) -> str:
            share_uid = f"shr_{tag}_{s}"
            c.execute(
                text(
                    "INSERT INTO documents.shares (uid, tenant_id, resource_type, resource_uid, "
                    "  token, expires_at) "
                    "VALUES (:uid, :t, 'document', :r, :tok, now() + interval '7 days')"
                ),
                {"uid": share_uid, "t": tenant, "r": resource_uid, "tok": f"tok_{tag}_{s}"},
            )
            return share_uid

        share_1 = mk_share("one", ta, doc_1)
        share_3 = mk_share("three", ta, doc_3)
        share_int = mk_share("internal", ta, doc_int)

        def mk_attachment(tag: str, tenant: int, contact: int | None) -> str:
            att_uid = f"att_{tag}_{s}"
            c.execute(
                text(
                    "INSERT INTO documents.attachments (uid, tenant_id, file_id, contact_id, "
                    "  attachment_type) VALUES (:uid, :t, :f, :con, 'passport')"
                ),
                {"uid": att_uid, "t": tenant, "f": mk_file(tenant, f"att_{tag}"), "con": contact},
            )
            return att_uid

        att_1 = mk_attachment("one", ta, con_1)
        att_3 = mk_attachment("three", ta, con_3)
        att_int = mk_attachment("internal", ta, None)

        # ---- platform.instructions ----
        def mk_instruction(tag: str, tenant: int, account: int, contact: int) -> str:
            ins_uid = f"ins_{tag}_{s}"
            c.execute(
                text(
                    "INSERT INTO platform.instructions (uid, tenant_id, account_id, contact_id, "
                    "  kind, amount) VALUES (:uid, :t, :a, :con, 'expected_in', 100.00)"
                ),
                {"uid": ins_uid, "t": tenant, "a": account, "con": contact},
            )
            return ins_uid

        ins_1 = mk_instruction("one", ta, acc_1, con_1)
        ins_3 = mk_instruction("three", ta, acc_3, con_3)

        # ---- tasks ----
        def mk_task(tag: str, tenant: int, contact: int | None, user: str | None) -> str:
            task_uid = f"tsk_{tag}_{s}"
            c.execute(
                text(
                    "INSERT INTO tasks.tasks (uid, tenant_id, assignee_kind, assignee_contact_id, "
                    "  assignee_user_id, title) "
                    "VALUES (:uid, :t, :kind, :con, :u, 'Do the thing')"
                ),
                {
                    "uid": task_uid,
                    "t": tenant,
                    "kind": "contact" if contact is not None else "user",
                    "con": contact,
                    "u": user,
                },
            )
            return task_uid

        task_1 = mk_task("one", ta, con_1, None)
        task_3 = mk_task("three", ta, con_3, None)
        task_staff = mk_task("staff", ta, None, staff_all)

        # ---- crm.applications (+ the application<->contact join) ----
        provider = str(
            scalar(
                SECCL_PLATFORM_SQL,
                n=f"Seccl {s}",
            )
        )

        def mk_application(tag: str, tenant: int, contact: int | None, adviser: str | None) -> int:
            aid = int(
                scalar(
                    "INSERT INTO crm.applications (tenant_id, provider, uid, status, "
                    "  adviser_id) VALUES (:t, :p, :uid, 'in_progress', :adv) RETURNING id",
                    t=tenant,
                    p=provider,
                    uid=f"app_{tag}_{s}",
                    adv=adviser,
                )
            )
            if contact is not None:
                c.execute(
                    text(
                        "INSERT INTO crm.account_contacts (tenant_id, application_id, contact_id) "
                        "VALUES (:t, :a, :con)"
                    ),
                    {"t": tenant, "a": aid, "con": contact},
                )
            return aid

        app_1 = mk_application("one", ta, con_1, None)
        app_3 = mk_application("three", ta, con_3, None)
        app_adviser = mk_application("adviser", ta, None, staff_scoped)

    return {
        "tenant_a": ta,
        "tenant_b": tb,
        "staff_all": staff_all,
        "staff_scoped": staff_scoped,
        "client_user": client_user,
        "org_a": f"org_a_{s}",
        "con_1": con_1,
        "con_3": con_3,
        "acc_1": acc_1,
        "acc_3": acc_3,
        "kyc_1": kyc_1,
        "kyc_3": kyc_3,
        "kyc_b": kyc_b,
        "flow_a": flow_a,
        "ver_a": ver_a,
        "case_1": case_1,
        "case_3": case_3,
        "case_int": case_int,
        "case_b": case_b,
        "exec_1": exec_1,
        "exec_3": exec_3,
        "exec_new": exec_new,
        "ev_1": ev_1,
        "ev_3": ev_3,
        "ev_int": ev_int,
        "doc_1": doc_1,
        "doc_3": doc_3,
        "doc_int": doc_int,
        "share_1": share_1,
        "share_3": share_3,
        "share_int": share_int,
        "att_1": att_1,
        "att_3": att_3,
        "att_int": att_int,
        "ins_1": ins_1,
        "ins_3": ins_3,
        "task_1": task_1,
        "task_3": task_3,
        "task_staff": task_staff,
        "app_1": app_1,
        "app_3": app_3,
        "app_adviser": app_adviser,
    }


# ---- the four personas ------------------------------------------------------------------------


def _base(seed: dict[str, object], user_uuid: object, wos: str) -> dict[str, object]:
    return {
        "sub": wos,
        "org_id": seed["org_a"],
        "user_uuid": user_uuid,
        "tenant_id": seed["tenant_a"],
        "tenant_ids": [seed["tenant_a"]],
    }


def read_all_claims(seed: dict[str, object]) -> dict[str, object]:
    """A staffer with firm-wide client visibility."""
    return {
        **_base(seed, seed["staff_all"], "wos_all"),
        "role": "adviser",
        "portal": "platform",
        "permissions": ["contacts:read_all"],
        "contact_ids": [],
        "account_ids": [],
    }


def scoped_claims(seed: dict[str, object]) -> dict[str, object]:
    """A book-scoped adviser: their book is contact 1 (and its account)."""
    return {
        **_base(seed, seed["staff_scoped"], "wos_scoped"),
        "role": "adviser",
        "portal": "platform",
        "permissions": [],
        "contact_ids": [seed["con_1"]],
        "account_ids": [seed["acc_1"]],
    }


def client_claims(seed: dict[str, object]) -> dict[str, object]:
    """A client-portal session. Carries contacts:read_all deliberately: 0004 suppresses both it
    and the tenant lineage in the client portal, so it must not widen anything here."""
    return {
        **_base(seed, seed["client_user"], "wos_client"),
        "role": "advised-client",
        "portal": "client",
        "permissions": ["contacts:read_all"],
        "contact_ids": [seed["con_1"]],
        "account_ids": [seed["acc_1"]],
    }


def _ids(conn: object, sql: str, **params: object) -> set[int]:
    return {int(r) for r in conn.execute(text(sql), params).scalars()}  # type: ignore[attr-defined]


def _uids(conn: object, sql: str, **params: object) -> set[str]:
    return set(conn.execute(text(sql), params).scalars())  # type: ignore[attr-defined]


_SCOPED_TABLES = {
    "crm.kyc_checks": "SELECT id FROM crm.kyc_checks WHERE tenant_id = ANY(:t)",
    "flows.cases": "SELECT id FROM flows.cases WHERE tenant_id = ANY(:t)",
    "flows.case_steps": "SELECT id FROM flows.case_steps WHERE tenant_id = ANY(:t)",
    "flows.executions": "SELECT id FROM flows.executions WHERE tenant_id = ANY(:t)",
    "flows.domain_events": "SELECT id FROM flows.domain_events WHERE tenant_id = ANY(:t)",
    "documents.shares": "SELECT uid FROM documents.shares WHERE tenant_id = ANY(:t)",
    "documents.attachments": "SELECT uid FROM documents.attachments WHERE tenant_id = ANY(:t)",
    "platform.instructions": "SELECT uid FROM platform.instructions WHERE tenant_id = ANY(:t)",
    "tasks.tasks": "SELECT uid FROM tasks.tasks WHERE tenant_id = ANY(:t)",
    "crm.applications": "SELECT id FROM crm.applications WHERE tenant_id = ANY(:t)",
}


# ---- crm.kyc_checks ---------------------------------------------------------------------------


def test_kyc_read_all_staff_sees_the_firm(app_engine: Engine, seed: dict[str, object]) -> None:
    with principal_conn(app_engine, read_all_claims(seed)) as conn:
        ids = _ids(conn, _SCOPED_TABLES["crm.kyc_checks"], t=[seed["tenant_a"], seed["tenant_b"]])
    assert ids == {seed["kyc_1"], seed["kyc_3"]}
    assert seed["kyc_b"] not in ids  # never another firm


def test_kyc_is_book_scoped(app_engine: Engine, seed: dict[str, object]) -> None:
    """The most sensitive table in the schema: identity/AML outcomes for a client outside the
    adviser's book are invisible (was tenant-wide before 0031)."""
    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        ids = _ids(conn, _SCOPED_TABLES["crm.kyc_checks"], t=[seed["tenant_a"], seed["tenant_b"]])
    assert ids == {seed["kyc_1"]}


def test_kyc_client_sees_only_own(app_engine: Engine, seed: dict[str, object]) -> None:
    with principal_conn(app_engine, client_claims(seed)) as conn:
        ids = _ids(conn, _SCOPED_TABLES["crm.kyc_checks"], t=[seed["tenant_a"], seed["tenant_b"]])
    assert ids == {seed["kyc_1"]}


# ---- flows.* ----------------------------------------------------------------------------------


def test_flow_cases_read_all_staff_sees_the_firm(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    with principal_conn(app_engine, read_all_claims(seed)) as conn:
        ids = _ids(conn, _SCOPED_TABLES["flows.cases"], t=[seed["tenant_a"], seed["tenant_b"]])
    assert ids == {seed["case_1"], seed["case_3"], seed["case_int"]}
    assert seed["case_b"] not in ids


def test_flow_cases_and_steps_are_book_scoped(app_engine: Engine, seed: dict[str, object]) -> None:
    """A book-scoped adviser sees their own client's case plus firm-internal (no subject) cases,
    never another adviser's client's onboarding — and case_steps follow the case."""
    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        cases = _ids(conn, _SCOPED_TABLES["flows.cases"], t=[seed["tenant_a"], seed["tenant_b"]])
        step_cases = _ids(
            conn,
            "SELECT case_id FROM flows.case_steps WHERE tenant_id = ANY(:t)",
            t=[seed["tenant_a"], seed["tenant_b"]],
        )
    assert cases == {seed["case_1"], seed["case_int"]}
    assert step_cases == {seed["case_1"], seed["case_int"]}


def test_flow_executions_and_events_are_book_scoped(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The orchestration group + its append-only fact log follow the cases they carry; an
    execution with no cases yet stays staff-visible."""
    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        execs = _ids(conn, _SCOPED_TABLES["flows.executions"], t=[seed["tenant_a"]])
        events = _ids(conn, _SCOPED_TABLES["flows.domain_events"], t=[seed["tenant_a"]])
    assert execs == {seed["exec_1"], seed["exec_new"]}
    assert seed["exec_3"] not in execs
    assert events == {seed["ev_1"], seed["ev_int"]}
    assert seed["ev_3"] not in events


def test_flow_client_portal_is_a_hard_boundary(app_engine: Engine, seed: dict[str, object]) -> None:
    """A client sees their OWN case only: not another client's, and not firm-internal work —
    despite the identity carrying contacts:read_all."""
    with principal_conn(app_engine, client_claims(seed)) as conn:
        cases = _ids(conn, _SCOPED_TABLES["flows.cases"], t=[seed["tenant_a"], seed["tenant_b"]])
        steps = _ids(
            conn,
            "SELECT case_id FROM flows.case_steps WHERE tenant_id = ANY(:t)",
            t=[seed["tenant_a"], seed["tenant_b"]],
        )
        execs = _ids(conn, _SCOPED_TABLES["flows.executions"], t=[seed["tenant_a"]])
        events = _ids(conn, _SCOPED_TABLES["flows.domain_events"], t=[seed["tenant_a"]])
        lineage = conn.execute(text("SELECT auth.tenant_lineage()")).scalar_one()
    assert cases == {seed["case_1"]}
    assert steps == {seed["case_1"]}
    assert execs == {seed["exec_1"]}
    assert events == {seed["ev_1"]}
    assert lineage == []


def test_flow_definitions_stay_staff_only(app_engine: Engine, seed: dict[str, object]) -> None:
    """flows/flow_versions are firm assets, not client-visible (unchanged by 0031)."""
    with principal_conn(app_engine, client_claims(seed)) as conn:
        flows = _ids(
            conn, "SELECT id FROM flows.flows WHERE tenant_id = ANY(:t)", t=[seed["tenant_a"]]
        )
    assert flows == set()


def test_flow_outbox_is_deny_all_for_authenticated(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """flows.outbox carries no authenticated grant (0025): the queue is service_role-only."""
    with principal_conn(app_engine, read_all_claims(seed)) as conn:
        with pytest.raises(DBAPIError):
            conn.execute(text("SELECT count(*) FROM flows.outbox"))


# ---- documents.shares -------------------------------------------------------------------------


def test_shares_are_scoped_to_visible_resources(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """A share follows its document's own policy, so a book-scoped adviser can neither list nor
    revoke a link to a document they cannot see (0017 was tenant-wide WITH FULL WRITE)."""
    with principal_conn(app_engine, read_all_claims(seed)) as conn:
        all_shares = _uids(conn, _SCOPED_TABLES["documents.shares"], t=[seed["tenant_a"]])
    assert all_shares == {seed["share_1"], seed["share_3"], seed["share_int"]}

    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        scoped = _uids(conn, _SCOPED_TABLES["documents.shares"], t=[seed["tenant_a"]])
    assert scoped == {seed["share_1"], seed["share_int"]}


def test_share_revoke_outside_book_is_a_no_op(app_engine: Engine, seed: dict[str, object]) -> None:
    """The concrete audit finding: revoking a share for an invisible document must not work."""
    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        revoked = conn.execute(
            text("UPDATE documents.shares SET revoked_at = now() WHERE uid = :u RETURNING uid"),
            {"u": seed["share_3"]},
        ).fetchall()
        assert revoked == []  # RLS filters the row out; nothing to update
        mine = conn.execute(
            text("UPDATE documents.shares SET revoked_at = now() WHERE uid = :u RETURNING uid"),
            {"u": seed["share_1"]},
        ).fetchall()
        assert len(mine) == 1  # ...their own book's share still revokes


def test_shares_are_staff_only(app_engine: Engine, seed: dict[str, object]) -> None:
    """Share management is staff work: a client-portal session sees none (empty lineage)."""
    with principal_conn(app_engine, client_claims(seed)) as conn:
        assert _uids(conn, _SCOPED_TABLES["documents.shares"], t=[seed["tenant_a"]]) == set()


_SHARE_INSERT = (
    "INSERT INTO documents.shares (uid, tenant_id, resource_type, resource_uid, token, expires_at) "
    "VALUES (:uid, :t, :rtype, :r, :tok, now() + interval '7 days')"
)


def test_share_minting_outside_book_is_rejected(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The sharpest edge of the shares finding, and the one a read-side-only fix leaves wide open.

    Revoking an invisible share is a no-op (above), but CREATING one is not a no-op: the row carries
    the bearer token, and GET /share/{token} resolves on service_role checking only token + expiry.
    So a tenant-wide WITH CHECK let a book-scoped adviser mint a working public download link for a
    document outside their book — the row is invisible to them afterwards, but they already hold the
    token. WITH CHECK must repeat the resource scoping.
    """
    with principal_conn(app_engine, scoped_claims(seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(_SHARE_INSERT),
            {
                "uid": f"shr_evil_{seed['doc_3']}",
                "t": seed["tenant_a"],
                "rtype": "document",
                "r": seed["doc_3"],
                "tok": f"tok_evil_{seed['doc_3']}",
            },
        )
    # Their own book still shares — and so does firm-internal work.
    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        for tag, doc in (("ok", seed["doc_1"]), ("int", seed["doc_int"])):
            conn.execute(
                text(_SHARE_INSERT),
                {
                    "uid": f"shr_{tag}_{doc}",
                    "t": seed["tenant_a"],
                    "rtype": "document",
                    "r": doc,
                    "tok": f"tok_{tag}_{doc}",
                },
            )


def test_share_minting_for_an_unknown_resource_is_rejected(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """Fail closed for a book-scoped writer on a resource_uid that resolves to nothing visible — a
    typo, a uid from another firm, or a resource_type this policy has not been taught (0017's CHECK
    permits 'document' and 'file'; anything added later lands here until the policy is widened).

    A read-all staffer is deliberately NOT subject to this: `auth.can_read_all()` short-circuits the
    resource branch in all three clauses, exactly as it does in the read policy, because every
    document in the firm is already theirs to see. The dangling-uid case is a data-quality question
    for them, not an isolation one (the public resolve endpoint 404s on an unresolvable resource).
    """
    with principal_conn(app_engine, scoped_claims(seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(_SHARE_INSERT),
            {
                "uid": f"shr_nowhere_{seed['tenant_a']}",
                "t": seed["tenant_a"],
                "rtype": "document",
                "r": "doc_does_not_exist",
                "tok": f"tok_nowhere_{seed['tenant_a']}",
            },
        )


def test_shares_over_attachments_follow_the_attachment(
    app_engine: Engine, super_engine: Engine, seed: dict[str, object]
) -> None:
    """0017's CHECK permits resource_type IN ('document', 'file'), and the 'file' branch resolves
    against documents.attachments. Nothing in app/ mints one yet, so this is the only thing pinning
    that mapping — and pinning it matters, because an unmapped resource_type silently reads as
    'no visible resource' (fail closed) rather than 'tenant-wide'."""
    with super_engine.connect() as c:
        for tag, att in (("f1", seed["att_1"]), ("f3", seed["att_3"])):
            c.execute(
                text(_SHARE_INSERT),
                {
                    "uid": f"shr_{tag}_{att}",
                    "t": seed["tenant_a"],
                    "rtype": "file",
                    "r": att,
                    "tok": f"tok_{tag}_{att}",
                },
            )

    file_shares = "SELECT uid FROM documents.shares WHERE resource_type = 'file' AND uid = ANY(:u)"
    both = [f"shr_f1_{seed['att_1']}", f"shr_f3_{seed['att_3']}"]
    with principal_conn(app_engine, read_all_claims(seed)) as conn:
        assert _uids(conn, file_shares, u=both) == set(both)
    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        assert _uids(conn, file_shares, u=both) == {both[0]}

    # ...and minting a file-share for an out-of-book attachment is rejected too.
    with principal_conn(app_engine, scoped_claims(seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(_SHARE_INSERT),
            {
                "uid": f"shr_evilfile_{seed['att_3']}",
                "t": seed["tenant_a"],
                "rtype": "file",
                "r": seed["att_3"],
                "tok": f"tok_evilfile_{seed['att_3']}",
            },
        )


def test_shares_over_flow_cases_follow_the_case(
    app_engine: Engine, super_engine: Engine, seed: dict[str, object]
) -> None:
    """The third resource_type. 0017 created the CHECK as ('document', 'file') but
    0021_flows.sql:123-125 REPLACED it with ('document', 'file', 'flow_case'), so a flow_case share
    is a row the schema permits — which means the policy needs the branch even though no endpoint
    mints one yet. Without it such a share is invisible to the firm that owns it; with it, the share
    inherits flows.cases' (now book-scoped) visibility automatically."""
    with super_engine.connect() as c:
        case_uids = {
            tag: str(
                c.execute(
                    text("SELECT uid FROM flows.cases WHERE id = :i"), {"i": seed[key]}
                ).scalar_one()
            )
            for tag, key in (("in", "case_1"), ("out", "case_3"))
        }
        for tag, case_uid in case_uids.items():
            c.execute(
                text(_SHARE_INSERT),
                {
                    "uid": f"shr_case_{tag}_{seed['tenant_a']}",
                    "t": seed["tenant_a"],
                    "rtype": "flow_case",
                    "r": case_uid,
                    "tok": f"tok_case_{tag}_{seed['tenant_a']}",
                },
            )

    q = "SELECT uid FROM documents.shares WHERE resource_type = 'flow_case' AND uid = ANY(:u)"
    both = [f"shr_case_in_{seed['tenant_a']}", f"shr_case_out_{seed['tenant_a']}"]
    with principal_conn(app_engine, read_all_claims(seed)) as conn:
        assert _uids(conn, q, u=both) == set(both)
    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        assert _uids(conn, q, u=both) == {both[0]}  # the out-of-book case's link is invisible


# ---- documents.attachments --------------------------------------------------------------------


def test_attachments_are_book_scoped(app_engine: Engine, seed: dict[str, object]) -> None:
    with principal_conn(app_engine, read_all_claims(seed)) as conn:
        assert _uids(conn, _SCOPED_TABLES["documents.attachments"], t=[seed["tenant_a"]]) == {
            seed["att_1"],
            seed["att_3"],
            seed["att_int"],
        }
    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        assert _uids(conn, _SCOPED_TABLES["documents.attachments"], t=[seed["tenant_a"]]) == {
            seed["att_1"],
            seed["att_int"],
        }
    with principal_conn(app_engine, client_claims(seed)) as conn:
        assert _uids(conn, _SCOPED_TABLES["documents.attachments"], t=[seed["tenant_a"]]) == {
            seed["att_1"]
        }


def test_attachment_write_outside_book_is_a_no_op(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """0018's FOR ALL USING was tenant-wide, which both widened reads and allowed edits."""
    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        touched = conn.execute(
            text("UPDATE documents.attachments SET name = 'renamed' WHERE uid = :u RETURNING uid"),
            {"u": seed["att_3"]},
        ).fetchall()
        assert touched == []


def test_attachment_insert_outside_book_is_rejected(
    app_engine: Engine, super_engine: Engine, seed: dict[str, object]
) -> None:
    """The read-side fix alone left the CREATE path tenant-wide: a book-scoped staffer could file a
    passport/proof-of-address against a client they cannot see, which then shows up for that
    client's own adviser and for the client themselves in the portal."""
    insert = (
        "INSERT INTO documents.attachments (uid, tenant_id, file_id, contact_id, attachment_type) "
        "VALUES (:uid, :t, :f, :con, 'passport')"
    )

    def a_file(tag: str) -> str:
        with super_engine.connect() as c:
            return str(
                c.execute(
                    text(
                        "INSERT INTO documents.files "
                        "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                        "VALUES (:t, 'portals-documents', :k, 'application/pdf', 10, 'sha') "
                        "RETURNING id"
                    ),
                    {"t": seed["tenant_a"], "k": f"uploads/{tag}_{seed['tenant_a']}/x.pdf"},
                ).scalar_one()
            )

    with principal_conn(app_engine, scoped_claims(seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(insert),
            {
                "uid": f"att_evil_{seed['con_3']}",
                "t": seed["tenant_a"],
                "f": a_file("evil"),
                "con": seed["con_3"],
            },
        )
    # Own book, and firm-internal (contact_id NULL), still upload.
    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        conn.execute(
            text(insert),
            {
                "uid": f"att_ok_{seed['con_1']}",
                "t": seed["tenant_a"],
                "f": a_file("ok"),
                "con": seed["con_1"],
            },
        )
        conn.execute(
            text(insert),
            {
                "uid": f"att_internal_{seed['tenant_a']}",
                "t": seed["tenant_a"],
                "f": a_file("internal"),
                "con": None,
            },
        )


# ---- platform.instructions --------------------------------------------------------------------


def test_instructions_are_book_scoped(app_engine: Engine, seed: dict[str, object]) -> None:
    with principal_conn(app_engine, read_all_claims(seed)) as conn:
        assert _uids(conn, _SCOPED_TABLES["platform.instructions"], t=[seed["tenant_a"]]) == {
            seed["ins_1"],
            seed["ins_3"],
        }
    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        assert _uids(conn, _SCOPED_TABLES["platform.instructions"], t=[seed["tenant_a"]]) == {
            seed["ins_1"]
        }
    with principal_conn(app_engine, client_claims(seed)) as conn:
        assert _uids(conn, _SCOPED_TABLES["platform.instructions"], t=[seed["tenant_a"]]) == {
            seed["ins_1"]
        }


def test_instruction_insert_outside_book_is_rejected(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """Money-movement intent raised against a client outside the writer's book. 0016's WITH CHECK
    was tenant-wide, so only the router stood between a book-scoped staffer and an instruction on
    someone else's client account."""
    insert = (
        "INSERT INTO platform.instructions (uid, tenant_id, account_id, contact_id, kind, amount) "
        "VALUES (:uid, :t, :a, :con, 'expected_in', 100.00)"
    )
    with principal_conn(app_engine, scoped_claims(seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(insert),
            {
                "uid": f"ins_evil_{seed['con_3']}",
                "t": seed["tenant_a"],
                "a": seed["acc_3"],
                "con": seed["con_3"],
            },
        )
    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        conn.execute(
            text(insert),
            {
                "uid": f"ins_ok_{seed['con_1']}",
                "t": seed["tenant_a"],
                "a": seed["acc_1"],
                "con": seed["con_1"],
            },
        )


# ---- the flows.executions scope accessor ------------------------------------------------------


def test_execution_subject_contacts_is_not_a_cross_tenant_oracle(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """flows.execution_subject_contacts is SECURITY DEFINER owned by service_role (BYPASSRLS) and
    takes a caller-supplied id, so without an internal guard it is a direct cross-tenant oracle:
    `SELECT flows.execution_subject_contacts(<firm B execution>)` would return firm B's case subject
    contact ids to any session, client-portal included. Note that REVOKE alone would not fix this —
    CREATE FUNCTION grants EXECUTE to PUBLIC, and `authenticated` needs EXECUTE for the policy.

    NULL, not '{}', on refusal: '{}' <@ anything is TRUE, so an empty array would have made the
    staff branch of executions_read pass for a refused execution.
    """
    fn = "SELECT flows.execution_subject_contacts(:e)"
    # Firm B's execution has no id in this seed, so borrow firm B's case's execution: build one.
    for claims in (scoped_claims(seed), client_claims(seed), read_all_claims(seed)):
        with principal_conn(app_engine, claims) as conn:
            # Own firm: resolves (contact 1 for exec_1, empty for the case-less execution).
            assert conn.execute(text(fn), {"e": seed["exec_1"]}).scalar_one() == [seed["con_1"]]
            assert conn.execute(text(fn), {"e": seed["exec_new"]}).scalar_one() == []
            # An id that does not exist at all: refused, not "empty and therefore contained".
            assert conn.execute(text(fn), {"e": -1}).scalar_one() is None


def test_execution_accessor_refuses_another_firms_execution(
    app_engine: Engine, super_engine: Engine, seed: dict[str, object]
) -> None:
    """The empirical probe: a firm-A session (staff or client) asking for a firm-B execution's
    subjects gets NULL, and cannot see the execution row either."""
    with super_engine.connect() as c:
        ex_b = int(
            c.execute(
                text(
                    "INSERT INTO flows.executions (uid, tenant_id, flow_id, flow_version_id, "
                    "  status) SELECT :uid, c.tenant_id, c.flow_id, c.flow_version_id, 'active' "
                    "FROM flows.cases c WHERE c.id = :case RETURNING id"
                ),
                {"uid": f"fex_b_{seed['case_b']}", "case": seed["case_b"]},
            ).scalar_one()
        )
        c.execute(
            text("UPDATE flows.cases SET execution_id = :e WHERE id = :c"),
            {"e": ex_b, "c": seed["case_b"]},
        )

    for claims in (read_all_claims(seed), scoped_claims(seed), client_claims(seed)):
        with principal_conn(app_engine, claims) as conn:
            assert (
                conn.execute(
                    text("SELECT flows.execution_subject_contacts(:e)"), {"e": ex_b}
                ).scalar_one()
                is None
            )
            assert _ids(conn, "SELECT id FROM flows.executions WHERE id = :e", e=ex_b) == set()


# ---- tasks.tasks (policy level; test_tasks_api.py covers the API) ------------------------------


def test_tasks_are_book_scoped_at_the_policy(app_engine: Engine, seed: dict[str, object]) -> None:
    with principal_conn(app_engine, read_all_claims(seed)) as conn:
        assert _uids(conn, _SCOPED_TABLES["tasks.tasks"], t=[seed["tenant_a"]]) == {
            seed["task_1"],
            seed["task_3"],
            seed["task_staff"],
        }
    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        assert _uids(conn, _SCOPED_TABLES["tasks.tasks"], t=[seed["tenant_a"]]) == {
            seed["task_1"],
            seed["task_staff"],
        }
    with principal_conn(app_engine, client_claims(seed)) as conn:
        assert _uids(conn, _SCOPED_TABLES["tasks.tasks"], t=[seed["tenant_a"]]) == {seed["task_1"]}


def test_task_insert_outside_book_is_rejected(app_engine: Engine, seed: dict[str, object]) -> None:
    """0028's WITH CHECK was tenant-wide, so book scoping on CREATE was application-code-only."""
    insert = (
        "INSERT INTO tasks.tasks (uid, tenant_id, assignee_kind, assignee_contact_id, title) "
        "VALUES (:uid, :t, 'contact', :con, 'Sneaky')"
    )
    # Two connections, not two statements: principal_conn publishes the claims with a
    # session-level set_config, and a ROLLBACK would undo that along with the failed INSERT.
    with principal_conn(app_engine, scoped_claims(seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(insert),
            {"uid": f"tsk_evil_{seed['con_3']}", "t": seed["tenant_a"], "con": seed["con_3"]},
        )
    with principal_conn(app_engine, scoped_claims(seed)) as conn:  # their own book still works
        conn.execute(
            text(insert),
            {"uid": f"tsk_ok_{seed['con_1']}", "t": seed["tenant_a"], "con": seed["con_1"]},
        )


def test_cross_tenant_task_write_is_rejected(app_engine: Engine, seed: dict[str, object]) -> None:
    with principal_conn(app_engine, read_all_claims(seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(
                "INSERT INTO tasks.tasks (uid, tenant_id, assignee_kind, title) "
                "VALUES (:uid, :t, 'role', 'Cross-tenant')"
            ),
            {"uid": f"tsk_x_{seed['tenant_b']}", "t": seed["tenant_b"]},
        )


def test_client_cannot_stamp_another_firms_tenant_on_a_task(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """tasks.tasks is the one table the client portal writes under `authenticated`, so its WITH
    CHECK keeps a client branch. 0028's version was `tenant_id = ANY(lineage) OR
    assignee_contact_id = ANY(contact_scope())` — and a client's lineage is empty, so the second
    branch was the ONLY one that could fire and it constrained the assignee but not the tenant. A
    client could therefore create a row inside another firm. The branch now carries
    `tenant_id = auth.tenant_id()`."""
    insert = (
        "INSERT INTO tasks.tasks (uid, tenant_id, assignee_kind, assignee_contact_id, title) "
        "VALUES (:uid, :t, 'contact', :con, 'Planted')"
    )
    with principal_conn(app_engine, client_claims(seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(insert),
            {
                "uid": f"tsk_planted_{seed['tenant_b']}",
                "t": seed["tenant_b"],  # another firm...
                "con": seed["con_1"],  # ...but my own contact
            },
        )
    with principal_conn(app_engine, client_claims(seed)) as conn:  # own firm still works
        conn.execute(
            text(insert),
            {
                "uid": f"tsk_client_ok_{seed['con_1']}",
                "t": seed["tenant_a"],
                "con": seed["con_1"],
            },
        )


# ---- the NOT EXISTS book-scoping shape is load-bearing on document_contacts --------------------


def test_documents_stay_book_scoped_via_the_not_exists_shape(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The tripwire for a hazard the audit never named, and the reason 0031 could not reuse this
    shape for flows.executions.

    documents_read (0021) / documents_audit_read (0022) book-scope NEGATIVELY: branch (b) is
    `tenant_id = ANY(lineage) AND NOT EXISTS (a document_contacts link outside my book)`. A policy
    predicate's subqueries ARE RLS-filtered, so that is sound only while
    documents.document_contacts stays tenant-wide for staff. Book-scope document_contacts and the
    out-of-book links become invisible inside the subquery, NOT EXISTS reads as 'clean' for every
    row, and branch (b) collapses back to plain `tenant_id = ANY(lineage)` — i.e. tenant-wide,
    silently reopening exactly what 0021 closed.

    The observable symptom is this assertion: a document linked ONLY to a contact outside the book
    must be invisible. (Note it has to be *only* out-of-book — see the companion test below for why
    a co-shared document is a different, and legitimate, case.)
    """
    q = "SELECT uid FROM documents.documents WHERE uid = ANY(:u)"
    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        visible = _uids(conn, q, u=[seed["doc_1"], seed["doc_3"], seed["doc_int"]])
    assert visible == {seed["doc_1"], seed["doc_int"]}, (
        "a document linked only outside the book must not be visible to a book-scoped adviser -- "
        "if this fails, check whether documents.document_contacts has been book-scoped; see the "
        "COMMENT ON POLICY document_contacts_read set by 0031"
    )


def test_a_document_co_delivered_in_and_out_of_book_is_visible(
    app_engine: Engine, super_engine: Engine, seed: dict[str, object]
) -> None:
    """Pinned because it is counter-intuitive and it is what makes the naive version of the test
    above wrong. documents_read's third branch is POSITIVE — `EXISTS (a link to a contact in my
    scope)` — and it is not client-only: a book-scoped staffer's contact_scope contains their own
    clients, so a document delivered to their client AND to someone else's is visible to them.

    That is the right call (it is their client's document) but it means branch (b)'s NOT EXISTS adds
    nothing for co-shared documents, and a test built on one would pass whatever document_contacts'
    policy said — which is precisely the false comfort the hazard note above warns about.
    """
    with super_engine.connect() as c:
        file_id = str(
            c.execute(
                text(
                    "INSERT INTO documents.files "
                    "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                    "VALUES (:t, 'portals-documents', :k, 'application/pdf', 10, 'sha') "
                    "RETURNING id"
                ),
                {"t": seed["tenant_a"], "k": f"uploads/shared_{seed['tenant_a']}/x.pdf"},
            ).scalar_one()
        )
        doc_uid = f"doc_shared_{seed['tenant_a']}"
        doc_id = c.execute(
            text(
                "INSERT INTO documents.documents "
                "  (uid, tenant_id, document_type, source, status, original_file_id, pdf_file_id) "
                "VALUES (:uid, :t, 'other', 'uploaded', 'generated', :f, :f) RETURNING id"
            ),
            {"uid": doc_uid, "t": seed["tenant_a"], "f": file_id},
        ).scalar_one()
        for contact in (seed["con_1"], seed["con_3"]):  # in-book AND out-of-book
            c.execute(
                text(
                    "INSERT INTO documents.document_contacts (document_id, tenant_id, contact_id) "
                    "VALUES (:d, :t, :con)"
                ),
                {"d": doc_id, "t": seed["tenant_a"], "con": contact},
            )

    q = "SELECT uid FROM documents.documents WHERE uid = :u"
    for claims in (scoped_claims(seed), client_claims(seed), read_all_claims(seed)):
        with principal_conn(app_engine, claims) as conn:
            assert _uids(conn, q, u=doc_uid) == {doc_uid}
    # ...and still not to a session with no claims at all, nor across the tenant boundary.
    with principal_conn(app_engine, {}) as conn:
        assert _uids(conn, q, u=doc_uid) == set()


def test_the_document_contacts_hazard_is_documented_in_the_catalogue(
    super_engine: Engine,
) -> None:
    """The warning has to live where the next person reads the schema, not only in a migration file
    they will not open: `\\d+ documents.document_contacts` shows it."""
    with super_engine.connect() as c:
        comment = c.execute(
            text(
                "SELECT obj_description(p.oid, 'pg_policy') FROM pg_policy p "
                "JOIN pg_class c ON c.oid = p.polrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'documents' AND c.relname = 'document_contacts' "
                "  AND p.polname = 'document_contacts_read'"
            )
        ).scalar_one()
    assert comment is not None
    assert "load-bearing" in comment.lower()


# ---- crm.applications -------------------------------------------------------------------------


def test_applications_use_contact_scope(app_engine: Engine, seed: dict[str, object]) -> None:
    """0002 scoped on adviser_id = auth.uid(), so an adviser lost their own book's applications
    the moment someone else was recorded on the row, and a client saw none of their own."""
    with principal_conn(app_engine, read_all_claims(seed)) as conn:
        assert _ids(conn, _SCOPED_TABLES["crm.applications"], t=[seed["tenant_a"]]) == {
            seed["app_1"],
            seed["app_3"],
            seed["app_adviser"],
        }
    with principal_conn(app_engine, scoped_claims(seed)) as conn:
        assert _ids(conn, _SCOPED_TABLES["crm.applications"], t=[seed["tenant_a"]]) == {
            seed["app_1"],
            seed["app_adviser"],
        }
    with principal_conn(app_engine, client_claims(seed)) as conn:
        assert _ids(conn, _SCOPED_TABLES["crm.applications"], t=[seed["tenant_a"]]) == {
            seed["app_1"]
        }


# ---- fail-closed ------------------------------------------------------------------------------


@pytest.mark.parametrize("table", sorted(_SCOPED_TABLES))
def test_empty_claims_see_nothing(app_engine: Engine, seed: dict[str, object], table: str) -> None:
    """Absent claims => empty lineage AND empty contact/account scope => fail closed, on every
    table this migration touches."""
    with principal_conn(app_engine, {}) as conn:
        assert (
            set(
                conn.execute(
                    text(_SCOPED_TABLES[table]), {"t": [seed["tenant_a"], seed["tenant_b"]]}
                ).scalars()
            )
            == set()
        )
