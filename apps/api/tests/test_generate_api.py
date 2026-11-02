"""Behaviour tests for async generation: POST /documents/generate (enqueue) + the DocumentWorker.

The endpoint runs against a real ``authenticated`` session; the worker against the migrator role
(a service_role member). The PDF renderer and object storage are mocked (no WeasyPrint native stack
/ no S3 in tests), so the SQL, the Jinja context assembly, the claim/ack/retry and the
documents/files/audit writes are all exercised.
"""

import contextlib
import json
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import storage, worker
from app.auth import Principal
from app.routers import documents as docs_router

# A published version's saved render blob (the shape the editor persists post-T4a).
RENDER_HTML = "<div class='page'><h1>{{ document_name }}</h1><p>Hi {{ client.name }}</p></div>"
RENDER_SCHEMA = {
    "html": RENDER_HTML,
    "css": ".page{padding:0}",
    "pageSize": {"width": "210mm", "height": "297mm"},
    "gjs_project": {},
}


class _FakeRenderer:
    """Capturing stub — records the last rendered HTML so a test can assert on the substituted
    context, without asserting inside the (shared) renderer."""

    last_html: str | None = None

    async def to_pdf(self, html, css="", branding_css="", page_size=None):
        _FakeRenderer.last_html = html
        return b"%PDF-1.4 fake", "deadbeef"


@pytest.fixture
def staff(super_engine: Engine) -> dict[str, object]:
    """A tenant, a staff user, a client user, and a published template version with render HTML."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {
                    "uid": f"gten_{s}",
                    "alias": f"gfirm-{s}",
                    "name": f"GFirm {s}",
                },
            ).scalar_one()
        )
        staff_user = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, email, workos_user_id, first_name, last_name, "
                    "  enabled) VALUES (:uid, :email, :wos, 'Ada', 'Staff', true) RETURNING id"
                ),
                {"uid": f"gusr_{s}", "email": f"staff_{s}@g.test", "wos": f"gwos_{s}"},
            ).scalar_one()
        )
        client_user = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, email, workos_user_id, first_name, last_name, "
                    "  enabled) VALUES (:uid, :email, :wos, 'Ada', 'Client', true) RETURNING id"
                ),
                {"uid": f"gcli_{s}", "email": f"client_{s}@g.test", "wos": f"gwoscli_{s}"},
            ).scalar_one()
        )
        for u, portal in ((staff_user, "platform"), (client_user, "client")):
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (:u, :t, :p)"
                ),
                {"u": u, "t": tenant, "p": portal},
            )
        # The client is the CRM contact (the true client model); the client login maps to it via
        # crm.contact_users so a client session's contact_scope reaches this contact's documents.
        contact_id = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :uid, 'individual', :name, :email) RETURNING id"
                ),
                {
                    "t": tenant,
                    "uid": f"gcnt_{s}",
                    "name": "Ada Client",
                    "email": f"client_{s}@g.test",
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id) "
                "VALUES (:t, :cid, CAST(:u AS uuid))"
            ),
            {"t": tenant, "cid": contact_id, "u": client_user},
        )
        # A second contact in the same tenant, to test sending one document to multiple clients.
        second_contact_id = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :uid, 'individual', :name, :email) RETURNING id"
                ),
                {
                    "t": tenant,
                    "uid": f"gcnt3_{s}",
                    "name": "Bo Client",
                    "email": f"bo_{s}@g.test",
                },
            ).scalar_one()
        )
        # Template + a PUBLISHED version carrying the render blob; point the template at it.
        tpl_uid = f"gtpl_{s}"
        tid = c.execute(
            text(
                "INSERT INTO documents.templates "
                "  (uid, tenant_id, code, name, document_type, created_by) "
                "VALUES (:uid, :t, :code, 'Illus', 'illustration', CAST(:by AS uuid)) RETURNING id"
            ),
            {"uid": tpl_uid, "t": tenant, "code": f"illus_{s}", "by": staff_user},
        ).scalar_one()
        vid = c.execute(
            text(
                "INSERT INTO documents.template_versions "
                "  (uid, tenant_id, template_id, version_number, status, renderer, schema, "
                "   created_by, published_at) "
                "VALUES (:uid, :t, :tid, 1, 'published', 'weasy', CAST(:schema AS jsonb), "
                "  CAST(:by AS uuid), now()) RETURNING id"
            ),
            {
                "uid": f"gtplv_{s}",
                "t": tenant,
                "tid": tid,
                "schema": json.dumps(RENDER_SCHEMA),
                "by": staff_user,
            },
        ).scalar_one()
        c.execute(
            text("UPDATE documents.templates SET current_published_version_id = :v WHERE id = :t"),
            {"v": vid, "t": tid},
        )
        # A draft-only template (no published version) to test the 404 path.
        draft_uid = f"gtpldraft_{s}"
        c.execute(
            text(
                "INSERT INTO documents.templates "
                "  (uid, tenant_id, code, name, document_type, created_by) "
                "VALUES (:uid, :t, :code, 'Draft', 'review', CAST(:by AS uuid))"
            ),
            {"uid": draft_uid, "t": tenant, "code": f"draft_{s}", "by": staff_user},
        )
        # A contact in ANOTHER tenant — invisible to this staffer's RLS, so binding to it must 422.
        other_tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {
                    "uid": f"gten2_{s}",
                    "alias": f"gfirm2-{s}",
                    "name": f"GFirm2 {s}",
                },
            ).scalar_one()
        )
        foreign_contact_id = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :uid, 'individual', :name, :email) RETURNING id"
                ),
                {
                    "t": other_tenant,
                    "uid": f"gcnt2_{s}",
                    "name": "Other Firm Client",
                    "email": f"other_{s}@g.test",
                },
            ).scalar_one()
        )
        c.commit()

    return {
        "tenant_id": str(tenant),
        "user_uuid": staff_user,
        "client_uuid": client_user,
        "client_contact_id": contact_id,
        "second_contact_id": second_contact_id,
        "foreign_contact_id": foreign_contact_id,
        "org": f"gorg_{s}",
        "wos": f"gwos_{s}",
        "tpl_uid": tpl_uid,
        "draft_uid": draft_uid,
    }


def _principal(perms: list[str]) -> Principal:
    return Principal(user_id="x", organisation_id="x", role="adviser", permissions=perms)


@pytest_asyncio.fixture
async def session_factory(db: dict[str, str], staff: dict[str, str]):
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    claims = {
        "sub": staff["wos"],
        "role": "adviser",
        "portal": "platform",
        # contacts:read_all lets the staff session SEE the CRM contact under RLS, so generate can
        # validate + bind it (crm.contacts is invisible otherwise → generate would 422).
        "permissions": ["documents:write", "templates:read", "contacts:read_all"],
        "org_id": staff["org"],
        "user_uuid": staff["user_uuid"],
        "tenant_id": int(staff["tenant_id"]),
        "tenant_ids": [int(staff["tenant_id"])],
        "contact_ids": [],
        "account_ids": [],
    }

    @asynccontextmanager
    async def _open() -> AsyncIterator:
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE authenticated"))
            await session.execute(
                text("SELECT set_config('request.jwt.claims', :c, true)"),
                {"c": json.dumps(claims)},
            )
            yield session

    yield _open
    await engine.dispose()


@pytest_asyncio.fixture
async def worker_maker(db: dict[str, str]):
    """A session maker over the migrator role (a service_role member) — the worker's DB access."""
    engine = create_async_engine(db["migrator"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _enqueue(session_factory, staff, **overrides) -> Any:
    principal = _principal(["documents:write"])
    body = docs_router.GenerateDocument(
        template_uid=staff["tpl_uid"],
        answers=overrides.get("answers", {"amount": "1000"}),
        client_contact_id=overrides.get("client_contact_id", staff["client_contact_id"]),
        name=overrides.get("name", "Ada's Illustration"),
    )
    async with session_factory() as session:
        return await docs_router.generate_document(body=body, principal=principal, session=session)


@pytest.mark.asyncio
async def test_generate_enqueues(session_factory, staff) -> None:
    """The endpoint records a queued document (with inputs + the intended client) and a render job —
    it does not render synchronously.

    Generating FOR a client records the intent (``intended_contact_id``) and NOTHING ELSE: no
    document_contacts link yet. That link is the client's whole read permission — the client branch
    of ``documents_read`` matches on contact_id with no status predicate — so filing it here would
    show them a document that has not rendered, or a failed one for ever. ``test_worker_renders``
    below asserts an illustration still is not filed on them when the render succeeds: sharing
    an illustration is the firm's choice (``illustrations_auto_share``), not the renderer's."""
    doc = await _enqueue(session_factory, staff)
    assert doc.status == "queued"
    assert doc.source == "generated"
    assert doc.name == "Ada's Illustration"
    assert doc.client == "Ada Client"  # from intended_contact_id, not a document_contacts row

    principal = _principal(["documents:write"])
    async with session_factory() as session:
        job = (
            await session.execute(
                text(
                    "SELECT rj.status FROM documents.render_jobs rj "
                    "JOIN documents.documents d ON d.id = rj.document_id WHERE d.uid = :uid"
                ),
                {"uid": doc.uid},
            )
        ).scalar_one()
        inputs = (
            await session.execute(
                text("SELECT inputs FROM documents.documents WHERE uid = :uid"), {"uid": doc.uid}
            )
        ).scalar_one()
        intended = (
            await session.execute(
                text("SELECT intended_contact_id FROM documents.documents WHERE uid = :uid"),
                {"uid": doc.uid},
            )
        ).scalar_one()
        bound = (
            (
                await session.execute(
                    text(
                        "SELECT dc.contact_id, dc.role FROM documents.document_contacts dc "
                        "JOIN documents.documents d ON d.id = dc.document_id WHERE d.uid = :uid"
                    ),
                    {"uid": doc.uid},
                )
            )
            .mappings()
            .all()
        )
        listed = await docs_router.list_documents(principal=principal, session=session)
    assert job == "queued"
    assert inputs == {"amount": "1000"}
    assert intended == staff["client_contact_id"]
    # Not filed under the client YET: the render has not happened, so there is nothing for them to
    # read. `intended_contact_id` above is what carries the filing across to the worker.
    assert bound == []
    assert doc.uid in {d.uid for d in listed}  # visible to staff while queued


@pytest.mark.asyncio
async def test_worker_renders(session_factory, worker_maker, staff, super_engine, monkeypatch):
    """The worker claims the job, renders (client resolved from the binding), stores the PDF, marks
    the document generated + writes the audit event + acks the job."""
    # Isolate: the worker drains the whole queue, so clear any jobs other tests left behind.
    with super_engine.connect() as c:
        c.execute(text("DELETE FROM documents.render_jobs"))
    _FakeRenderer.last_html = None
    monkeypatch.setattr(worker, "get_renderer", lambda name: _FakeRenderer())

    async def _fake_put(key, data, content_type, **_kw):
        assert content_type == "application/pdf"
        assert data == b"%PDF-1.4 fake"

    monkeypatch.setattr(storage, "put_document", _fake_put)

    doc = await _enqueue(session_factory, staff)
    assert await worker.handle_once(worker_maker) is True
    assert "Hi Ada Client" in (_FakeRenderer.last_html or "")  # client resolved from the binding

    principal = _principal(["documents:write"])
    async with session_factory() as session:
        got = await docs_router.get_document(uid=doc.uid, principal=principal, session=session)
        row = (
            (
                await session.execute(
                    text(
                        "SELECT pdf_file_id, renderer_version, generated_at "
                        "FROM documents.documents WHERE uid = :uid"
                    ),
                    {"uid": doc.uid},
                )
            )
            .mappings()
            .one()
        )
        jstatus = (
            await session.execute(
                text(
                    "SELECT rj.status FROM documents.render_jobs rj "
                    "JOIN documents.documents d ON d.id = rj.document_id WHERE d.uid = :uid"
                ),
                {"uid": doc.uid},
            )
        ).scalar_one()
        events = (
            (
                await session.execute(
                    text(
                        "SELECT action FROM documents.audit_log a "
                        "JOIN documents.documents d ON d.id = a.document_id WHERE d.uid = :uid"
                    ),
                    {"uid": doc.uid},
                )
            )
            .scalars()
            .all()
        )
        bound = (
            (
                await session.execute(
                    text(
                        "SELECT dc.contact_id, dc.role FROM documents.document_contacts dc "
                        "JOIN documents.documents d ON d.id = dc.document_id WHERE d.uid = :uid"
                    ),
                    {"uid": doc.uid},
                )
            )
            .mappings()
            .all()
        )
    assert got.status == "generated"
    # An illustration is not filed under the client on render unless the firm has switched
    # illustrations_auto_share on. intended_contact_id still says who it is for; the subject
    # link, which is the client's read permission, waits for a person to share it.
    assert [(r["contact_id"], r["role"]) for r in bound] == []
    assert row["pdf_file_id"] is not None
    assert row["renderer_version"] == "weasy"
    assert row["generated_at"] is not None
    assert jstatus == "done"
    assert "document.generated" in events


@pytest.mark.asyncio
async def test_worker_retries_on_render_error(
    session_factory, worker_maker, staff, super_engine, monkeypatch
):
    """A render failure requeues the job with backoff (attempts < max) and leaves the doc queued."""
    with super_engine.connect() as c:
        c.execute(text("DELETE FROM documents.render_jobs"))

    class _Boom:
        async def to_pdf(self, *a, **k):
            raise RuntimeError("render exploded")

    monkeypatch.setattr(worker, "get_renderer", lambda name: _Boom())

    doc = await _enqueue(session_factory, staff, answers={}, client_contact_id=None)
    await worker.handle_once(worker_maker)

    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT rj.status, rj.attempts, rj.last_error, "
                        "       d.status::text AS doc_status "
                        "FROM documents.render_jobs rj "
                        "JOIN documents.documents d ON d.id = rj.document_id "
                        "WHERE d.uid = :uid"
                    ),
                    {"uid": doc.uid},
                )
            )
            .mappings()
            .one()
        )
    assert row["status"] == "queued"  # requeued, not dead-lettered (1 < 3 attempts)
    assert row["attempts"] == 1
    assert "render exploded" in row["last_error"]
    assert row["doc_status"] == "queued"


def test_content_disposition_encodes_non_latin1_names() -> None:
    """A document name with characters outside latin-1 (em dash, curly quotes, accents) must not
    crash the response encoder — the header must be latin-1-safe (ASCII filename + UTF-8
    filename*). Regression: names like 'Suitability Report — Q1 2026' were 500→502 on view."""
    cd = docs_router._content_disposition("inline", "Suitability Report — Q1 2026")
    cd.encode("latin-1")  # must not raise — this is what the ASGI server does to header values
    assert "filename*=UTF-8''" in cd
    assert "%E2%80%94" in cd  # the em dash, percent-encoded
    assert 'filename="Suitability Report  Q1 2026"' in cd  # ASCII fallback drops the em dash

    # A purely ASCII name is unchanged in the fallback and still carries a filename*.
    ascii_cd = docs_router._content_disposition("attachment", "Fact Find.pdf")
    ascii_cd.encode("latin-1")
    assert 'filename="Fact Find.pdf"' in ascii_cd

    # A name with nothing ASCII-encodable still yields a non-empty fallback.
    fallback = docs_router._content_disposition("inline", "—")
    fallback.encode("latin-1")
    assert 'filename="document"' in fallback


@pytest.mark.asyncio
async def test_generate_requires_published_version(session_factory, staff) -> None:
    principal = _principal(["documents:write"])
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await docs_router.generate_document(
                body=docs_router.GenerateDocument(template_uid=staff["draft_uid"]),
                principal=principal,
                session=session,
            )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_generate_requires_permission(session_factory, staff) -> None:
    principal = _principal([])  # no documents:write
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await docs_router.generate_document(
                body=docs_router.GenerateDocument(template_uid=staff["tpl_uid"]),
                principal=principal,
                session=session,
            )
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_generate_rejects_non_client_recipient(session_factory, staff) -> None:
    """`client_contact_id` must be a crm.contacts row the caller may see.

    Another tenant's contact (invisible under this staffer's RLS) and a bogus contact id are both
    refused with 422. RLS on document_contacts only checks the tenant, so without this a staffer
    could bind a document to an arbitrary contact (incl. another tenant's client). Mirrors
    send_document."""
    principal = _principal(["documents:write"])
    for bad in (staff["foreign_contact_id"], 99999999):
        body = docs_router.GenerateDocument(
            template_uid=staff["tpl_uid"], answers={}, client_contact_id=bad, name="x"
        )
        with pytest.raises(HTTPException) as ei:
            async with session_factory() as session:
                await docs_router.generate_document(body=body, principal=principal, session=session)
        assert ei.value.status_code == 422


@pytest.mark.asyncio
async def test_send_to_multiple_clients_lists_them_all(session_factory, staff) -> None:
    """A document sent to more than one client lists every recipient. Since the made-for/
    delivered-to split (`intended_contact_id` vs recipient links), `client` names WHO THE
    DOCUMENT WAS MADE FOR — generation filed it for Ada, and sending to more people must not
    rewrite that — while `recipients` aggregates everyone it was actually sent to."""
    doc = await _enqueue(session_factory, staff)
    # send is the client-facing release act — documents:send since the 0042 split.
    principal = _principal(["documents:send"])
    async with session_factory() as session:
        result = await docs_router.send_document(
            uid=doc.uid,
            body=docs_router.SendDocument(
                contact_ids=[staff["client_contact_id"], staff["second_contact_id"]]
            ),
            principal=principal,
            session=session,
        )
        assert result.sent == 2
        sent = await docs_router.get_document(uid=doc.uid, principal=principal, session=session)
        assert sent.client == "Ada Client"
        assert sent.recipients is not None
        assert "Ada Client" in sent.recipients
        assert "Bo Client" in sent.recipients


@pytest.mark.asyncio
async def test_list_documents_client_viewed(session_factory, staff) -> None:
    """The list endpoint's `client_viewed`/`client_viewed_at` ask whether the client passed as
    `contact_id` has opened the document via any of their logins (crm.contact_users) — distinct
    from `unread`/`last_viewed_at`, which reflect the CALLING (staff) principal's own view state,
    not the client's. Null/false with no contact_id (the tenant-wide overview)."""
    doc = await _enqueue(session_factory, staff)
    # documents:send for the send below; the listing read itself is RLS-scoped.
    principal = _principal(["documents:send"])
    async with session_factory() as session:
        await docs_router.send_document(
            uid=doc.uid,
            body=docs_router.SendDocument(
                contact_ids=[staff["client_contact_id"], staff["second_contact_id"]]
            ),
            principal=principal,
            session=session,
        )
        # Ada Client's own login views it; Bo Client's never does.
        await session.execute(
            text(
                "INSERT INTO documents.audit_log (tenant_id, document_id, actor_id, action) "
                "SELECT :t, d.id, CAST(:u AS uuid), 'document.viewed' "
                "FROM documents.documents d WHERE d.uid = :uid"
            ),
            {"t": int(staff["tenant_id"]), "u": staff["client_uuid"], "uid": doc.uid},
        )

        ada_docs = await docs_router.list_documents(
            contact_id=staff["client_contact_id"], principal=principal, session=session
        )
        ada_doc = next(d for d in ada_docs if d.uid == doc.uid)
        assert ada_doc.client_viewed is True
        assert ada_doc.client_viewed_at is not None

        bo_docs = await docs_router.list_documents(
            contact_id=staff["second_contact_id"], principal=principal, session=session
        )
        bo_doc = next(d for d in bo_docs if d.uid == doc.uid)
        assert bo_doc.client_viewed is False
        assert bo_doc.client_viewed_at is None

        all_docs = await docs_router.list_documents(
            contact_id=None, principal=principal, session=session
        )
        overview_doc = next(d for d in all_docs if d.uid == doc.uid)
        assert overview_doc.client_viewed is False


@pytest.mark.asyncio
async def test_generate_gate_blocks_unsourced_variables(
    session_factory, super_engine, staff
) -> None:
    """Completeness gate: a template whose tokens have no data source (they'd render blank) is
    refused (422, naming them) — unless allow_incomplete is passed, which lets a deliberate draft
    through. `client.name` resolves (core context); `mystery_fee` does not."""
    s = uuid.uuid4().hex[:8]
    tenant = int(staff["tenant_id"])
    gap_uid = f"ggap_{s}"
    schema = {
        **RENDER_SCHEMA,
        "html": "<div class='page'><p>{{ mystery_fee }} for {{ client.name }}</p></div>",
    }
    with super_engine.connect() as c:
        tid = c.execute(
            text(
                "INSERT INTO documents.templates "
                "  (uid, tenant_id, code, name, document_type, created_by) "
                "VALUES (:uid, :t, :code, 'Gap', 'illustration', CAST(:by AS uuid)) RETURNING id"
            ),
            {"uid": gap_uid, "t": tenant, "code": f"gap_{s}", "by": staff["user_uuid"]},
        ).scalar_one()
        vid = c.execute(
            text(
                "INSERT INTO documents.template_versions "
                "  (uid, tenant_id, template_id, version_number, status, renderer, schema, "
                "   created_by, published_at) "
                "VALUES (:uid, :t, :tid, 1, 'published', 'weasy', CAST(:schema AS jsonb), "
                "  CAST(:by AS uuid), now()) RETURNING id"
            ),
            {
                "uid": f"ggapv_{s}",
                "t": tenant,
                "tid": tid,
                "schema": json.dumps(schema),
                "by": staff["user_uuid"],
            },
        ).scalar_one()
        c.execute(
            text("UPDATE documents.templates SET current_published_version_id = :v WHERE id = :t"),
            {"v": vid, "t": tid},
        )
        c.commit()
    principal = _principal(["documents:write"])
    # Default: blocked, and the offending token is named in the error.
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await docs_router.generate_document(
                body=docs_router.GenerateDocument(template_uid=gap_uid),
                principal=principal,
                session=session,
            )
    assert ei.value.status_code == 422
    assert "mystery_fee" in str(ei.value.detail)
    # Explicit override: generates anyway (a deliberate incomplete draft).
    async with session_factory() as session:
        doc = await docs_router.generate_document(
            body=docs_router.GenerateDocument(template_uid=gap_uid, allow_incomplete=True),
            principal=principal,
            session=session,
        )
    assert doc.status == "queued"


# -- batch generation ----------------------------------------------------------------------------
# One template, many clients. Each client gets an INDEPENDENT document; the interesting behaviour is
# all at the edges — partial success, replay, and which failures belong to a client rather than to
# the request.


@pytest.fixture
def extra_clients(super_engine: Engine, staff: dict[str, object]) -> list[int]:
    """Two more clients in the staffer's own firm, so a batch has something to fan out over."""
    s = uuid.uuid4().hex[:8]
    ids: list[int] = []
    with super_engine.connect() as c:
        for n in ("Bea Client", "Cy Client"):
            ids.append(
                int(
                    c.execute(
                        text(
                            "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                            "VALUES (:t, :uid, 'individual', :name, :email) RETURNING id"
                        ),
                        {
                            "t": int(staff["tenant_id"]),
                            "uid": uuid.uuid4().hex[:12],
                            "name": n,
                            "email": f"{n.split()[0].lower()}_{s}@g.test",
                        },
                    ).scalar_one()
                )
            )
        c.commit()
    return ids


async def _batch(session_factory, staff, contact_ids, **overrides) -> Any:
    body = docs_router.GenerateBatch(
        template_uid=overrides.get("template_uid", staff["tpl_uid"]),
        contact_ids=contact_ids,
        answers=overrides.get("answers", {"amount": "1000"}),
        name=overrides.get("name"),
        idempotency_key=overrides.get("idempotency_key"),
    )
    async with session_factory() as session:
        return await docs_router.generate_documents_batch(
            body=body,
            principal=_principal(overrides.get("perms", ["documents:write"])),
            session=session,
        )


@pytest.mark.asyncio
async def test_batch_queues_one_document_per_client(session_factory, staff, extra_clients) -> None:
    """A document EACH, not one shared between them: every client's is filed, sent and audited on
    its own, so they cannot be one row."""
    targets = [int(staff["client_contact_id"]), *extra_clients]
    out = await _batch(session_factory, staff, targets)
    assert [d.status for d in out.documents] == ["queued"] * 3
    assert out.rejected == []
    # Each is bound to its own client, in the order asked for.
    assert [d.client_contact_id for d in out.documents] == targets
    # Distinct documents, not the same one three times.
    assert len({d.uid for d in out.documents}) == 3


@pytest.mark.asyncio
async def test_batch_reports_a_client_it_may_not_target_and_still_queues_the_rest(
    session_factory, staff, extra_clients
) -> None:
    """Partial success is the contract. An operator who picked three clients wants the two that
    work plus a reason for the one that does not — losing the batch to it would be worse."""
    out = await _batch(
        session_factory,
        staff,
        [extra_clients[0], int(staff["foreign_contact_id"]), extra_clients[1]],
    )
    assert [d.client_contact_id for d in out.documents] == extra_clients
    assert [r.contact_id for r in out.rejected] == [int(staff["foreign_contact_id"])]
    assert "access" in out.rejected[0].reason.lower()


@pytest.mark.asyncio
async def test_batch_refuses_the_request_when_nothing_landed(session_factory, staff) -> None:
    """An empty `documents` must never read as success, so no survivors is a refusal of the whole
    request — naming every reason."""
    with pytest.raises(HTTPException) as err:
        await _batch(session_factory, staff, [int(staff["foreign_contact_id"])])
    assert err.value.status_code == 422
    assert str(err.value.detail).startswith("client ")


@pytest.mark.asyncio
async def test_batch_is_replayable_under_one_key(session_factory, staff, extra_clients) -> None:
    """The rule a 250-client action lives or dies by: a retried batch returns what the first attempt
    queued instead of queueing a second render for every client."""
    targets = [int(staff["client_contact_id"]), *extra_clients]
    first = await _batch(session_factory, staff, targets, idempotency_key="annual-review-2026")
    again = await _batch(session_factory, staff, targets, idempotency_key="annual-review-2026")
    assert [d.uid for d in again.documents] == [d.uid for d in first.documents]


@pytest.mark.asyncio
async def test_a_client_named_twice_gets_one_document(
    session_factory, staff, extra_clients
) -> None:
    """A batch is a SET of clients — a duplicated id is a slip in the caller's list, not a request
    for two copies."""
    out = await _batch(session_factory, staff, [extra_clients[0], extra_clients[0]])
    assert len(out.documents) == 1


@pytest.mark.asyncio
async def test_batch_needs_at_least_one_client(session_factory, staff) -> None:
    with pytest.raises(HTTPException) as err:
        await _batch(session_factory, staff, [])
    assert err.value.status_code == 422
    assert "at least one" in str(err.value.detail).lower()


@pytest.mark.asyncio
async def test_batch_caps_how_many_clients_it_will_take(session_factory, staff) -> None:
    """Refused rather than absorbed: the batch is one transaction, so an unbounded list holds a
    pooled connection open for as long as it takes to insert every row."""
    too_many = list(range(1, docs_router.MAX_BATCH_CLIENTS + 2))
    with pytest.raises(HTTPException) as err:
        await _batch(session_factory, staff, too_many)
    assert err.value.status_code == 422
    assert str(docs_router.MAX_BATCH_CLIENTS) in str(err.value.detail)


@pytest.mark.asyncio
async def test_a_template_condition_fails_the_whole_batch_not_every_client(
    session_factory, staff, extra_clients
) -> None:
    """An unpublished template is one fact about the request, identical for all three clients —
    reporting it three times in `rejected` would bury it in a list."""
    with pytest.raises(HTTPException) as err:
        await _batch(
            session_factory,
            staff,
            [int(staff["client_contact_id"]), *extra_clients],
            template_uid=staff["draft_uid"],
        )
    assert err.value.status_code == 404


@pytest.mark.asyncio
async def test_batch_requires_the_write_permission(session_factory, staff, extra_clients) -> None:
    with pytest.raises(HTTPException) as err:
        await _batch(session_factory, staff, extra_clients, perms=["documents:read"])
    assert err.value.status_code == 403


# ── a worker that cannot render must not claim render jobs ────────────────────
def test_the_render_probe_reports_why_it_cannot_load() -> None:
    """The probe answers a boolean AND a reason, because the reason is the whole diagnostic.

    On a machine without Pango/Cairo/GLib this returns False plus the ctypes error; in the Linux
    render container it returns True. Either way it must not raise — it is called at worker start,
    before any job is claimed, and an exception there would take down every other loop with it.
    """
    from app.render.renderers import weasyprint_available

    ok, why = weasyprint_available()
    assert isinstance(ok, bool)
    # A refusal always explains itself; an availability never needs to.
    assert (why == "") if ok else bool(why)


@pytest.mark.asyncio
async def test_a_worker_that_cannot_render_stands_down_instead_of_claiming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug: the Windows-native worker claimed jobs it could not do.

    Both workers poll `documents.render_jobs` with SKIP LOCKED, so a claim by the one without
    WeasyPrint TAKES the job from the container that has it, fails on a missing library, and burns
    one of three attempts. Three of those dead-letter a document that was never un-renderable —
    and the operator sees a document stuck at "queued" plus a log line about `libgobject`, which
    reads as a broken template.
    """
    import asyncio

    from app import worker as worker_mod

    monkeypatch.setattr(worker_mod, "weasyprint_available", lambda: (False, "no native stack"))

    claimed = False

    async def _must_not_be_called(_maker: object) -> bool:
        nonlocal claimed
        claimed = True
        return False

    monkeypatch.setattr(worker_mod, "handle_once", _must_not_be_called)
    monkeypatch.setattr(worker_mod, "POLL_INTERVAL_SECONDS", 0.01)

    beat = worker_mod.Heartbeat(Path(tempfile.mkdtemp()) / "beat.json")
    task = asyncio.create_task(worker_mod._render_loop(object(), beat))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert not claimed, "a worker with no render stack claimed a render job"
    # It still heartbeats: a deliberately idle loop must not read as a wedged container.
    assert worker_mod.RENDER_LOOP in beat.stamps
