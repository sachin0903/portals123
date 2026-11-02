"""Row-level-security isolation proof for 0005_documents_storage.

Seeds two tenants each with a file + document (via a superuser, bypassing RLS), then connects as
the `authenticated` request role with a staff principal's claims and asserts the staff boundary
holds: a firm sees only its own documents/files, and empty claims see nothing (fail-closed).
Client-visibility scopes through the CRM contact (auth.contact_scope() over the contact_ids claim):
a client sees a document/request when its contact_id is in their scope.

Order-independent: uses unique ids per run and tenant-scoped assertions, and never truncates (so
it can't wipe the migration-seeded model tenant other tests rely on).
"""

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.routers.documents import _SELECT
from tests.conftest import principal_conn


@pytest.fixture
def seed(super_engine: Engine) -> dict[str, object]:
    """Two firms, a staff user in Firm A, and one file+document per firm — all with unique ids."""
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
                        "uid": f"ten_{letter}_{s}",
                        "alias": f"firm-{letter}-{s}",
                        "name": f"Firm {letter.upper()} {s}",
                    },
                ).scalar_one()
            )

        ta, tb = mk_tenant("a"), mk_tenant("b")
        staff_a = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, email, workos_user_id, enabled) "
                    "VALUES (:uid, :email, :wos, true) RETURNING id"
                ),
                {
                    "uid": f"usr_staff_a_{s}",
                    "email": f"staff_a_{s}@firm-a.test",
                    "wos": f"wos_a_{s}",
                },
            ).scalar_one()
        )
        c.execute(
            text("INSERT INTO auth.organisation_memberships (user_id, tenant_id) VALUES (:u,:t)"),
            {"u": staff_a, "t": ta},
        )

        def mk_doc(tenant: int, letter: str) -> str:
            doc_uid = f"doc_{letter}_{s}"
            fid = c.execute(
                text(
                    "INSERT INTO documents.files "
                    "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                    "VALUES (:t,'portals-documents',:k,'application/pdf',10,'sha') RETURNING id"
                ),
                {"t": tenant, "k": f"uploads/ten_{letter}_{s}/{doc_uid}/original.pdf"},
            ).scalar_one()
            c.execute(
                text(
                    "INSERT INTO documents.documents "
                    "  (uid, tenant_id, document_type, source, status, "
                    "   original_file_id, pdf_file_id) "
                    "VALUES (:uid,:t,'other','uploaded','generated',:f,:f)"
                ),
                {"uid": doc_uid, "t": tenant, "f": fid},
            )
            return doc_uid

        doc_a, doc_b = mk_doc(ta, "a"), mk_doc(tb, "b")

    return {
        "tenant_a": ta,
        "tenant_b": tb,
        "staff_a": staff_a,
        "wos_a": f"wos_a_{s}",
        "org_a": f"org_a_{s}",
        "doc_a": doc_a,
        "doc_b": doc_b,
        "key_a": f"uploads/ten_a_{s}/{doc_a}/original.pdf",
    }


def _staff_a_claims(seed: dict[str, object]) -> dict[str, object]:
    return {
        "contact_ids": [],
        "account_ids": [],
        "sub": seed["wos_a"],
        "role": "adviser",
        "portal": "platform",
        "permissions": ["documents:write"],
        "org_id": seed["org_a"],
        "user_uuid": seed["staff_a"],
        "tenant_id": seed["tenant_a"],
        "tenant_ids": [seed["tenant_a"]],
    }


def test_staff_sees_only_own_tenant_documents(app_engine: Engine, seed: dict[str, object]) -> None:
    """Firm A staff see Firm A's document; Firm B's is invisible (tenant lineage boundary)."""
    with principal_conn(app_engine, _staff_a_claims(seed)) as conn:
        uids = set(conn.execute(text("SELECT uid FROM documents.documents")).scalars())
    assert seed["doc_a"] in uids
    assert seed["doc_b"] not in uids


def test_files_are_tenant_isolated(app_engine: Engine, seed: dict[str, object]) -> None:
    with principal_conn(app_engine, _staff_a_claims(seed)) as conn:
        keys = set(conn.execute(text("SELECT storage_key FROM documents.files")).scalars())
    assert seed["key_a"] in keys
    assert all("ten_b_" not in k for k in keys)  # never Firm B's key


def test_empty_claims_see_no_documents(app_engine: Engine, seed: dict[str, object]) -> None:
    """Absent claims => empty lineage => fail closed: this principal sees no documents/files."""
    with principal_conn(app_engine, {}) as conn:
        assert conn.execute(text("SELECT count(*) FROM documents.documents")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM documents.files")).scalar_one() == 0


def test_unread_flips_once_the_viewer_opens_the_document(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """`unread`/`last_viewed_at` (DocumentOut) are per-viewer: true/null until *this* principal has
    a `document.viewed` audit row for the document (see `document_content`/`document_url` in
    documents.py, which log one on every open). Reuses the production `_SELECT` projection so this
    proves the actual query, not a re-implementation of it."""
    with principal_conn(app_engine, _staff_a_claims(seed)) as conn:
        row = (
            conn.execute(
                text(_SELECT + "WHERE d.uid = :uid"),
                {"uid": seed["doc_a"], "client_contact_id": None},
            )
            .mappings()
            .one()
        )
        assert row["unread"] is True
        assert row["last_viewed_at"] is None

        # Mirrors app._audit(session, uid, "document.viewed") — what opening the document records.
        conn.execute(
            text(
                "INSERT INTO documents.audit_log (tenant_id, document_id, actor_id, action) "
                "SELECT auth.tenant_id(), d.id, auth.uid(), 'document.viewed' "
                "FROM documents.documents d WHERE d.uid = :uid"
            ),
            {"uid": seed["doc_a"]},
        )

        row = (
            conn.execute(
                text(_SELECT + "WHERE d.uid = :uid"),
                {"uid": seed["doc_a"], "client_contact_id": None},
            )
            .mappings()
            .one()
        )
        assert row["unread"] is False
        assert row["last_viewed_at"] is not None


def test_book_scoped_staff_only_sees_their_assigned_clients_documents(
    app_engine: Engine, super_engine: Engine
) -> None:
    """0021: documents_read now mirrors crm.contacts — a staffer without contacts:read_all sees
    only documents bound to a contact in their own book (auth.contact_scope()), not every document
    in the tenant. Full-book staff (contacts:read_all) still see everything."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {
                    "uid": f"bten_{s}",
                    "alias": f"bfirm-{s}",
                    "name": f"BFirm {s}",
                },
            ).scalar_one()
        )
        staff = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, email, workos_user_id, enabled) "
                    "VALUES (:uid, :email, :wos, true) RETURNING id"
                ),
                {"uid": f"busr_{s}", "email": f"staff_{s}@bfirm.test", "wos": f"bwos_{s}"},
            ).scalar_one()
        )
        c.execute(
            text("INSERT INTO auth.organisation_memberships (user_id, tenant_id) VALUES (:u,:t)"),
            {"u": staff, "t": tenant},
        )

        def mk_contact(tag: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                        "VALUES (:t, :uid, 'individual', :name, :email) RETURNING id"
                    ),
                    {
                        "t": tenant,
                        "uid": f"bcnt_{tag}_{s}",
                        "name": f"Client {tag}",
                        "email": f"{tag}_{s}@bfirm.test",
                    },
                ).scalar_one()
            )

        own_contact = mk_contact("own")
        other_contact = mk_contact("other")

        def mk_doc(tag: str, contact: int) -> str:
            doc_uid = f"bdoc_{tag}_{s}"
            fid = c.execute(
                text(
                    "INSERT INTO documents.files "
                    "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                    "VALUES (:t,'portals-documents',:k,'application/pdf',10,'sha') RETURNING id"
                ),
                {"t": tenant, "k": f"uploads/bten_{s}/{doc_uid}/original.pdf"},
            ).scalar_one()
            doc_id = c.execute(
                text(
                    "INSERT INTO documents.documents "
                    "  (uid, tenant_id, document_type, source, status, "
                    "   original_file_id, pdf_file_id) "
                    "VALUES (:uid,:t,'other','uploaded','generated',:f,:f) RETURNING id"
                ),
                {"uid": doc_uid, "t": tenant, "f": fid},
            ).scalar_one()
            c.execute(
                text(
                    "INSERT INTO documents.document_contacts "
                    "  (document_id, tenant_id, contact_id, role) "
                    "VALUES (:d, :t, :cid, 'recipient')"
                ),
                {"d": doc_id, "t": tenant, "cid": contact},
            )
            return doc_uid

        doc_own = mk_doc("own", own_contact)
        doc_other = mk_doc("other", other_contact)
        c.commit()

    def claims(*, read_all: bool) -> dict[str, object]:
        perms = ["documents:write"] + (["contacts:read_all"] if read_all else [])
        return {
            "contact_ids": [own_contact],
            "account_ids": [],
            "sub": f"bwos_{s}",
            "role": "adviser",
            "portal": "platform",
            "permissions": perms,
            "org_id": f"borg_{s}",
            "user_uuid": staff,
            "tenant_id": tenant,
            "tenant_ids": [tenant],
        }

    with principal_conn(app_engine, claims(read_all=False)) as conn:
        uids = set(conn.execute(text("SELECT uid FROM documents.documents")).scalars())
    assert doc_own in uids
    assert doc_other not in uids  # book-scoped: not their client

    with principal_conn(app_engine, claims(read_all=True)) as conn:
        uids = set(conn.execute(text("SELECT uid FROM documents.documents")).scalars())
    assert doc_own in uids
    assert doc_other in uids  # contacts:read_all: whole tenant


def test_activity_trail_and_requests_are_book_scoped(
    app_engine: Engine, super_engine: Engine
) -> None:
    """0022: audit_log + requests mirror documents_read's book scoping. A book-scoped adviser sees
    activity/requests only for their own book (no more leaking other advisers' clients' names via
    the overview feed); a client reads back their OWN audit events (so Unread/Last-interacted work,
    previously stuck because clients matched no audit_read branch); contacts:read_all sees the
    whole tenant."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {
                    "uid": f"aten_{s}",
                    "alias": f"afirm-{s}",
                    "name": f"AFirm {s}",
                },
            ).scalar_one()
        )

        def mk_user(tag: str, portal: str) -> str:
            uid = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, email, workos_user_id, enabled) "
                        "VALUES (:uid, :email, :wos, true) RETURNING id"
                    ),
                    {
                        "uid": f"ausr_{tag}_{s}",
                        "email": f"{tag}_{s}@afirm.test",
                        "wos": f"awos_{tag}_{s}",
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

        staff = mk_user("staff", "platform")
        client_user = mk_user("client", "client")

        def mk_contact(tag: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                        "VALUES (:t, :uid, 'individual', :name, :email) RETURNING id"
                    ),
                    {
                        "t": tenant,
                        "uid": f"acnt_{tag}_{s}",
                        "name": f"Client {tag}",
                        "email": f"{tag}_{s}@afirm.test",
                    },
                ).scalar_one()
            )

        own_contact = mk_contact("own")
        other_contact = mk_contact("other")

        def mk_doc(tag: str, contact: int) -> int:
            doc_uid = f"adoc_{tag}_{s}"
            fid = c.execute(
                text(
                    "INSERT INTO documents.files "
                    "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                    "VALUES (:t,'portals-documents',:k,'application/pdf',10,'sha') RETURNING id"
                ),
                {"t": tenant, "k": f"uploads/aten_{s}/{doc_uid}/original.pdf"},
            ).scalar_one()
            doc_id = c.execute(
                text(
                    "INSERT INTO documents.documents "
                    "  (uid, tenant_id, document_type, source, status, "
                    "   original_file_id, pdf_file_id) "
                    "VALUES (:uid,:t,'other','uploaded','generated',:f,:f) RETURNING id"
                ),
                {"uid": doc_uid, "t": tenant, "f": fid},
            ).scalar_one()
            c.execute(
                text(
                    "INSERT INTO documents.document_contacts "
                    "  (document_id, tenant_id, contact_id, role) "
                    "VALUES (:d, :t, :cid, 'recipient')"
                ),
                {"d": doc_id, "t": tenant, "cid": contact},
            )
            return doc_id

        doc_own = mk_doc("own", own_contact)
        doc_other = mk_doc("other", other_contact)

        def audit(doc_id: int, action: str, actor: str) -> None:
            c.execute(
                text(
                    "INSERT INTO documents.audit_log (tenant_id, document_id, actor_id, action) "
                    "VALUES (:t, :d, CAST(:a AS uuid), :act)"
                ),
                {"t": tenant, "d": doc_id, "a": actor, "act": action},
            )

        audit(doc_own, "document.sent", staff)
        audit(doc_own, "document.viewed", client_user)  # the client's own view of their own doc
        audit(doc_other, "document.sent", staff)

        def mk_req(tag: str, contact: int) -> int:
            rid = c.execute(
                text(
                    "INSERT INTO documents.requests (uid, tenant_id, contact_id, requested_by) "
                    "VALUES (:uid, :t, :cid, CAST(:by AS uuid)) RETURNING id"
                ),
                {"uid": f"areq_{tag}_{s}", "t": tenant, "cid": contact, "by": staff},
            ).scalar_one()
            c.execute(
                text(
                    "INSERT INTO documents.request_items "
                    "  (uid, request_id, tenant_id, action, title) "
                    "VALUES (:uid, :rid, :t, 'upload', 'Passport')"
                ),
                {"uid": f"aitem_{tag}_{s}", "rid": rid, "t": tenant},
            )
            return rid

        req_own = mk_req("own", own_contact)
        req_other = mk_req("other", other_contact)
        c.commit()

    def staff_claims(*, read_all: bool) -> dict[str, object]:
        perms = ["documents:write"] + (["contacts:read_all"] if read_all else [])
        return {
            "contact_ids": [own_contact],
            "account_ids": [],
            "sub": f"awos_staff_{s}",
            "role": "adviser",
            "portal": "platform",
            "permissions": perms,
            "org_id": f"aorg_{s}",
            "user_uuid": staff,
            "tenant_id": tenant,
            "tenant_ids": [tenant],
        }

    client_claims = {
        "contact_ids": [own_contact],
        "account_ids": [],
        "sub": f"awos_client_{s}",
        "role": "client",
        "portal": "client",
        "permissions": [],
        "org_id": f"aorg_{s}",
        "user_uuid": client_user,
        "tenant_id": tenant,
        "tenant_ids": [tenant],
    }

    # Book-scoped adviser: activity + requests only for their own book.
    with principal_conn(app_engine, staff_claims(read_all=False)) as conn:
        audit_docs = set(
            conn.execute(text("SELECT document_id FROM documents.audit_log")).scalars()
        )
        req_ids = set(conn.execute(text("SELECT id FROM documents.requests")).scalars())
    assert doc_own in audit_docs
    assert doc_other not in audit_docs  # another adviser's client — no longer leaked
    assert req_own in req_ids
    assert req_other not in req_ids

    # Full-book adviser (contacts:read_all): the whole tenant's activity + requests.
    with principal_conn(app_engine, staff_claims(read_all=True)) as conn:
        audit_docs = set(
            conn.execute(text("SELECT document_id FROM documents.audit_log")).scalars()
        )
        req_ids = set(conn.execute(text("SELECT id FROM documents.requests")).scalars())
    assert {doc_own, doc_other} <= audit_docs
    assert {req_own, req_other} <= req_ids

    # Client: reads back their OWN view event (un-sticks Unread) + own request; nothing else.
    with principal_conn(app_engine, client_claims) as conn:
        own_actions = set(
            conn.execute(
                text("SELECT action FROM documents.audit_log WHERE document_id = :d"),
                {"d": doc_own},
            ).scalars()
        )
        other_visible = conn.execute(
            text("SELECT count(*) FROM documents.audit_log WHERE document_id = :d"),
            {"d": doc_other},
        ).scalar_one()
        req_ids = set(conn.execute(text("SELECT id FROM documents.requests")).scalars())
    assert "document.viewed" in own_actions  # client can now read their own view → Unread works
    assert other_visible == 0  # another client's document activity stays hidden
    assert req_ids == {req_own}


def test_audit_append_cannot_forge_foreign_tenant(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """0014: the audit-append policy scopes INSERTs to the caller's own tenant (auth.tenant_id()).
    0005 shipped `WITH CHECK (true)`, which let any authenticated principal stamp an audit row with
    an ARBITRARY tenant_id -- forging another firm's compliance/consent trail. A row for the
    caller's own tenant is allowed (what app._audit does); a row for Firm B's tenant is rejected."""
    with principal_conn(app_engine, _staff_a_claims(seed)) as conn:
        # Own tenant: allowed.
        conn.execute(
            text(
                "INSERT INTO documents.audit_log (tenant_id, action) VALUES (:t, 'document.viewed')"
            ),
            {"t": seed["tenant_a"]},
        )
        # Foreign tenant: rejected by the WITH CHECK (new-row RLS violation).
        with pytest.raises(DBAPIError):
            conn.execute(
                text("INSERT INTO documents.audit_log (tenant_id, action) VALUES (:t, 'forged')"),
                {"t": seed["tenant_b"]},
            )


@pytest.fixture
def req_seed(super_engine: Engine) -> dict[str, object]:
    """Two firms; a staff + client in Firm A, a client in Firm B; one request (+ item) per firm."""
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
                        "uid": f"rten_{letter}_{s}",
                        "alias": f"rfirm-{letter}-{s}",
                        "name": f"RFirm {letter.upper()} {s}",
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
                        "uid": f"rusr_{tag}_{s}",
                        "email": f"{tag}_{s}@rfirm.test",
                        "wos": f"rwos_{tag}_{s}",
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
            # The client is the CRM contact (the true client model); a request binds to it.
            return int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                        "VALUES (:t, :uid, 'individual', :name, :email) RETURNING id"
                    ),
                    {
                        "t": tenant,
                        "uid": f"rcnt_{tag}_{s}",
                        "name": f"Client {tag}",
                        "email": f"{tag}_{s}@rfirm.test",
                    },
                ).scalar_one()
            )

        def mk_req(tag: str, tenant: int, contact: int, by: str) -> tuple[str, str]:
            req_uid, item_uid = f"req_{tag}_{s}", f"rqi_{tag}_{s}"
            rid = c.execute(
                text(
                    "INSERT INTO documents.requests (uid, tenant_id, contact_id, requested_by) "
                    "VALUES (:uid, :t, :cid, CAST(:by AS uuid)) RETURNING id"
                ),
                {"uid": req_uid, "t": tenant, "cid": contact, "by": by},
            ).scalar_one()
            c.execute(
                text(
                    "INSERT INTO documents.request_items "
                    "  (uid, request_id, tenant_id, action, title) "
                    "VALUES (:uid, :rid, :t, 'upload', 'Passport')"
                ),
                {"uid": item_uid, "rid": rid, "t": tenant},
            )
            return req_uid, item_uid

        ta, tb = mk_tenant("a"), mk_tenant("b")
        staff_a = mk_user("staff_a", ta, "platform")
        client_a = mk_user("client_a", ta, "client")
        client_b = mk_user("client_b", tb, "client")
        contact_a = mk_contact("a", ta)
        contact_b = mk_contact("b", tb)
        req_a, item_a = mk_req("a", ta, contact_a, staff_a)
        req_b, item_b = mk_req("b", tb, contact_b, client_b)

    return {
        "tenant_a": ta,
        "staff_a": staff_a,
        "wos_staff_a": f"rwos_staff_a_{s}",
        "org_a": f"rorg_a_{s}",
        "client_a": client_a,
        "wos_client_a": f"rwos_client_a_{s}",
        "contact_a": contact_a,
        "contact_b": contact_b,
        "req_a": req_a,
        "req_b": req_b,
        "item_a": item_a,
        "item_b": item_b,
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


def _staff_a_req_claims(seed: dict[str, object]) -> dict[str, object]:
    # A full-book adviser (contacts:read_all) — since 0022, requests are book-scoped, so only a
    # read_all adviser sees the whole firm's requests; the book-scoped case is covered separately.
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


def test_staff_sees_own_tenant_requests(app_engine: Engine, req_seed: dict[str, object]) -> None:
    """A full-book Firm A adviser sees Firm A's request (tenant lineage), never Firm B's."""
    with principal_conn(app_engine, _staff_a_req_claims(req_seed)) as conn:
        uids = set(conn.execute(text("SELECT uid FROM documents.requests")).scalars())
    assert req_seed["req_a"] in uids
    assert req_seed["req_b"] not in uids


@pytest.fixture
def share_seed(super_engine: Engine) -> dict[str, object]:
    """A firm with a client and two documents — one delivered to the client, one not."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {
                    "uid": f"sten_{s}",
                    "alias": f"sfirm-{s}",
                    "name": f"SFirm {s}",
                },
            ).scalar_one()
        )
        client = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                ),
                {"uid": f"susr_{s}", "t": tenant, "email": f"c_{s}@sfirm.test", "wos": f"swos_{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'client')"
            ),
            {"u": client, "t": tenant},
        )
        # The client is the CRM contact a document is delivered to.
        contact = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :uid, 'individual', :name, :email) RETURNING id"
                ),
                {"t": tenant, "uid": f"scnt_{s}", "name": "Client", "email": f"c_{s}@sfirm.test"},
            ).scalar_one()
        )

        def mk_doc(tag: str) -> str:
            fid = c.execute(
                text(
                    "INSERT INTO documents.files "
                    "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                    "VALUES (:t,'portals-documents',:k,'application/pdf',10,'sha') RETURNING id"
                ),
                {"t": tenant, "k": f"uploads/sten_{s}/{tag}/original.pdf"},
            ).scalar_one()
            return str(
                c.execute(
                    text(
                        "INSERT INTO documents.documents "
                        "  (uid, tenant_id, document_type, source, status, "
                        "   original_file_id, pdf_file_id) "
                        "VALUES (:uid,:t,'statement','generated','generated',:f,:f) RETURNING id"
                    ),
                    {"uid": f"sdoc_{tag}_{s}", "t": tenant, "f": fid},
                ).scalar_one()
            )

        sent_id = mk_doc("sent")
        mk_doc("private")  # created but never delivered to the client
        # Deliver only `sent` to the client (what /send and assign-task do).
        c.execute(
            text(
                "INSERT INTO documents.document_contacts "
                "  (document_id, tenant_id, contact_id, role, added_by) "
                "VALUES (CAST(:d AS uuid), :t, :cid, 'recipient', CAST(:u AS uuid))"
            ),
            {"d": sent_id, "t": tenant, "cid": contact, "u": client},
        )

    return {
        "tenant": tenant,
        "client": client,
        "contact": contact,
        "wos_client": f"swos_{s}",
        "org": f"sorg_{s}",
        "sent_uid": f"sdoc_sent_{s}",
        "private_uid": f"sdoc_private_{s}",
    }


def _share_client_claims(seed: dict[str, object]) -> dict[str, object]:
    return {
        "contact_ids": [seed["contact"]],
        "account_ids": [],
        "sub": seed["wos_client"],
        "role": "client",
        "portal": "client",
        "permissions": [],
        "org_id": seed["org"],
        "user_uuid": seed["client"],
        "tenant_id": seed["tenant"],
        "tenant_ids": [seed["tenant"]],
    }


def test_client_sees_only_documents_delivered_to_them(
    app_engine: Engine, share_seed: dict[str, object]
) -> None:
    """A delivered document (linked via document_contacts by /send or assign-task) shows in the
    client's view; a document never delivered to them stays invisible (empty lineage)."""
    with principal_conn(app_engine, _share_client_claims(share_seed)) as conn:
        uids = set(conn.execute(text("SELECT uid FROM documents.documents")).scalars())
    assert share_seed["sent_uid"] in uids
    assert share_seed["private_uid"] not in uids


@pytest.fixture
def file_seed(super_engine: Engine) -> dict[str, object]:
    """One client, one document delivered to them whose original, PDF and HTML renditions are three
    DIFFERENT files, one private document's file, and one file no document points at."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {"uid": f"vten_{s}", "alias": f"vfirm-{s}", "name": f"VFirm {s}"},
            ).scalar_one()
        )
        client = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                ),
                {"uid": f"vusr_{s}", "t": tenant, "email": f"c_{s}@vfirm.test", "wos": f"vwos_{s}"},
            ).scalar_one()
        )
        contact = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :uid, 'individual', 'Client', :email) RETURNING id"
                ),
                {"t": tenant, "uid": f"vcnt_{s}", "email": f"c_{s}@vfirm.test"},
            ).scalar_one()
        )

        def mk_file(tag: str) -> str:
            return str(
                c.execute(
                    text(
                        "INSERT INTO documents.files "
                        "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                        "VALUES (:t,'portals-documents',:k,'application/pdf',10,'sha') RETURNING id"
                    ),
                    {"t": tenant, "k": f"uploads/vten_{s}/{tag}"},
                ).scalar_one()
            )

        original, pdf, html = mk_file("original"), mk_file("pdf"), mk_file("html")
        private, orphan = mk_file("private"), mk_file("orphan")
        sent_id = c.execute(
            text(
                "INSERT INTO documents.documents (uid, tenant_id, document_type, source, status, "
                "  original_file_id, pdf_file_id, html_file_id) "
                "VALUES (:uid,:t,'statement','generated','generated',:o,:p,:h) RETURNING id"
            ),
            {"uid": f"vdoc_sent_{s}", "t": tenant, "o": original, "p": pdf, "h": html},
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO documents.documents (uid, tenant_id, document_type, source, status, "
                "  original_file_id, pdf_file_id) "
                "VALUES (:uid,:t,'statement','generated','generated',:f,:f)"
            ),
            {"uid": f"vdoc_private_{s}", "t": tenant, "f": private},
        )
        c.execute(
            text(
                "INSERT INTO documents.document_contacts "
                "  (document_id, tenant_id, contact_id, role, added_by) "
                "VALUES (:d, :t, :cid, 'recipient', CAST(:u AS uuid))"
            ),
            {"d": sent_id, "t": tenant, "cid": contact, "u": client},
        )

    return {
        "tenant": tenant,
        "client": client,
        "contact": contact,
        "wos_client": f"vwos_{s}",
        "org": f"vorg_{s}",
        "delivered": {original, pdf, html},
        "private": private,
        "orphan": orphan,
    }


def test_client_reads_each_file_of_a_delivered_document_and_no_other(
    app_engine: Engine, file_seed: dict[str, object]
) -> None:
    """The document arm of `documents_files_read`, one assertion per file column. A client portal
    has an EMPTY tenant lineage, so the lineage arm cannot pass and every file here is visible only
    through a document the client can read. 0229 split that arm's single EXISTS over
    `IN (original, pdf, html)` into one EXISTS per column; a column dropped in the split would hide
    that rendition, and a wrongly widened arm would show the private or orphan file."""
    with principal_conn(app_engine, _share_client_claims(file_seed)) as conn:
        assert conn.execute(text("SELECT auth.tenant_lineage()")).scalar_one() == []
        ids = {str(i) for i in conn.execute(text("SELECT id FROM documents.files")).scalars()}
    assert ids == file_seed["delivered"]
    assert file_seed["private"] not in ids
    assert file_seed["orphan"] not in ids


# ---- filed under a client: visible to them, and still not "sent" -------------------------------
# The goal the 'subject' link exists for: a client sees EVERY document filed against them, with no
# staffer having to send it. The client branch of documents_read (0021) matches on contact_id with
# no role predicate, which is why the link alone is enough and no policy was touched. These are the
# RLS half of the proof; the reporting half (a subject link must not read as delivery) is pinned in
# test_documents_api.py.


@pytest.fixture
def filed_seed(super_engine: Engine) -> dict[str, object]:
    """One firm, one client login, and three documents: one FILED UNDER that client and never sent,
    one filed under a DIFFERENT client of the same firm, one filed under nobody."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {
                    "uid": f"ften_{s}",
                    "alias": f"ffirm-{s}",
                    "name": f"FFirm {s}",
                },
            ).scalar_one()
        )
        client = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                ),
                {"uid": f"fusr_{s}", "t": tenant, "email": f"c_{s}@ffirm.test", "wos": f"fwos_{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'client')"
            ),
            {"u": client, "t": tenant},
        )

        def mk_contact(tag: str) -> int:
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

        mine, theirs = mk_contact("mine"), mk_contact("theirs")

        def mk_doc(tag: str, contact: int | None) -> str:
            doc_uid = f"fdoc_{tag}_{s}"
            fid = c.execute(
                text(
                    "INSERT INTO documents.files "
                    "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                    "VALUES (:t,'portals-documents',:k,'application/pdf',10,'sha') RETURNING id"
                ),
                {"t": tenant, "k": f"uploads/ften_{s}/{doc_uid}/original.pdf"},
            ).scalar_one()
            doc_id = c.execute(
                text(
                    "INSERT INTO documents.documents "
                    "  (uid, tenant_id, document_type, source, status, "
                    "   original_file_id, pdf_file_id, intended_contact_id) "
                    "VALUES (:uid,:t,'statement','uploaded','generated',:f,:f,:cid) RETURNING id"
                ),
                {"uid": doc_uid, "t": tenant, "f": fid, "cid": contact},
            ).scalar_one()
            if contact is not None:
                # Exactly what the filing paths now write — role='subject', and NO recipient row:
                # this document has never been sent to anybody.
                c.execute(
                    text(
                        "INSERT INTO documents.document_contacts "
                        "  (document_id, tenant_id, contact_id, role) "
                        "VALUES (:d, :t, :cid, 'subject')"
                    ),
                    {"d": doc_id, "t": tenant, "cid": contact},
                )
            return doc_uid

        filed_mine = mk_doc("mine", mine)
        filed_theirs = mk_doc("theirs", theirs)
        unfiled = mk_doc("unfiled", None)
        c.commit()

    return {
        "tenant": tenant,
        "client": client,
        "contact": mine,
        "wos_client": f"fwos_{s}",
        "org": f"forg_{s}",
        "filed_mine": filed_mine,
        "filed_theirs": filed_theirs,
        "unfiled": unfiled,
    }


def _filed_client_claims(seed: dict[str, object]) -> dict[str, object]:
    """A client-portal principal: their own contact in scope and EMPTY tenant lineage, so nothing
    here can pass by the staff branch of the policy — the client branch is doing the work."""
    return {
        "contact_ids": [seed["contact"]],
        "account_ids": [],
        "sub": seed["wos_client"],
        "role": "client",
        "permissions": [],
        "org_id": seed["org"],
        "user_uuid": seed["client"],
        "tenant_id": seed["tenant"],
        "tenant_ids": [seed["tenant"]],
        "portal": "client",
    }


def test_client_reads_a_document_filed_under_them_that_was_never_sent(
    app_engine: Engine, filed_seed: dict[str, object]
) -> None:
    """The feature. A document filed under the client (a 'subject' link, no send, no recipient row)
    is readable BY THE CLIENT: the policy's client branch matches on contact_id alone. Another
    client's filed document, and one filed under nobody, stay invisible — the auto-link widens
    visibility to exactly the person each document was recorded as being for, and no further."""
    with principal_conn(app_engine, _filed_client_claims(filed_seed)) as conn:
        uids = set(conn.execute(text("SELECT uid FROM documents.documents")).scalars())
    assert filed_seed["filed_mine"] in uids
    assert filed_seed["filed_theirs"] not in uids
    assert filed_seed["unfiled"] not in uids


def test_the_filed_document_the_client_reads_has_no_recipient_link(
    app_engine: Engine, super_engine: Engine, filed_seed: dict[str, object]
) -> None:
    """Guards the test above against passing for the wrong reason. If a 'recipient' row ever crept
    in, the read would prove nothing about the subject role — and every send-derived signal
    (`sent_to_client`, `recipients`) would have flipped with it."""
    with super_engine.connect() as c:
        roles = set(
            c.execute(
                text(
                    "SELECT dc.role FROM documents.document_contacts dc "
                    "JOIN documents.documents d ON d.id = dc.document_id WHERE d.uid = :u"
                ),
                {"u": filed_seed["filed_mine"]},
            ).scalars()
        )
    assert roles == {"subject"}


def test_client_still_cannot_write_a_document_filed_under_them(
    app_engine: Engine, filed_seed: dict[str, object]
) -> None:
    """Visibility is not authorship. The client branch appears in documents_write's USING clause
    too (0021 gave both policies the same expression), so a client can SELECT their filed document
    — but WITH CHECK is tenant-lineage-only and a client's lineage is empty, so they cannot insert
    one. Filing must not turn a reader into a writer."""
    with (
        principal_conn(app_engine, _filed_client_claims(filed_seed)) as conn,
        pytest.raises(DBAPIError),
    ):
        conn.execute(
            text(
                "INSERT INTO documents.documents (uid, tenant_id, document_type, source, status) "
                "VALUES ('fdoc_forged', :t, 'statement', 'uploaded', 'generated')"
            ),
            {"t": filed_seed["tenant"]},
        )


def test_client_sees_only_their_own_request(
    app_engine: Engine, req_seed: dict[str, object]
) -> None:
    """The target client reads their own request (contact_id in contact_scope) — with empty lineage
    — and never another client's."""
    with principal_conn(app_engine, _client_a_claims(req_seed)) as conn:
        uids = set(conn.execute(text("SELECT uid FROM documents.requests")).scalars())
    assert uids == {req_seed["req_a"]}  # exactly their own; not req_b, not via any lineage


def test_client_sees_only_their_own_request_items(
    app_engine: Engine, req_seed: dict[str, object]
) -> None:
    """A client reaches request items through their own request's contact_id (one-way subquery into
    documents.requests, matched against contact_scope); another firm's items stay invisible."""
    with principal_conn(app_engine, _client_a_claims(req_seed)) as conn:
        uids = set(conn.execute(text("SELECT uid FROM documents.request_items")).scalars())
    assert uids == {req_seed["item_a"]}
    assert req_seed["item_b"] not in uids


def test_client_cannot_create_request(app_engine: Engine, req_seed: dict[str, object]) -> None:
    """Writes are staff-only: a client can't self-issue a request (empty lineage fails WITH CHECK).
    They fulfil via the elevated service_role endpoint instead."""
    with principal_conn(app_engine, _client_a_claims(req_seed)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(
                "INSERT INTO documents.requests (uid, tenant_id, contact_id, requested_by) "
                "VALUES (:uid, :t, :cid, CAST(:u AS uuid))"
            ),
            {
                "uid": f"req_evil_{uuid.uuid4().hex[:6]}",
                "t": req_seed["tenant_a"],
                "cid": req_seed["contact_a"],
                "u": req_seed["client_a"],
            },
        )


def test_recent_activity_is_tenant_scoped(
    app_engine: Engine, super_engine: Engine, seed: dict[str, object]
) -> None:
    """The tenant-wide activity feed reads the audit log under RLS: Firm A staff see Firm A's
    events, never Firm B's."""
    with super_engine.connect() as c:
        for uid, tenant in ((seed["doc_a"], seed["tenant_a"]), (seed["doc_b"], seed["tenant_b"])):
            c.execute(
                text(
                    "INSERT INTO documents.audit_log (tenant_id, document_id, action) "
                    "SELECT :t, d.id, 'document.viewed' FROM documents.documents d WHERE d.uid = :u"
                ),
                {"t": tenant, "u": uid},
            )
    with principal_conn(app_engine, _staff_a_claims(seed)) as conn:
        docs = set(
            conn.execute(
                text(
                    "SELECT d.uid FROM documents.audit_log a "
                    "JOIN documents.documents d ON d.id = a.document_id"
                )
            ).scalars()
        )
    assert seed["doc_a"] in docs
    assert seed["doc_b"] not in docs


def test_documents_audit_log_is_append_only(super_engine: Engine) -> None:
    """Product audit trail is append-only for the request role (SELECT/INSERT, no UPDATE/DELETE)."""
    with super_engine.connect() as c:
        privs = {
            p: c.execute(
                text("SELECT has_table_privilege('authenticated', 'documents.audit_log', :p)"),
                {"p": p},
            ).scalar_one()
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE")
        }
    assert privs["SELECT"] and privs["INSERT"]
    assert not privs["UPDATE"] and not privs["DELETE"]
