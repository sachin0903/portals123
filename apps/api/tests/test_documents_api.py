"""Behaviour tests for the documents surface the functional audit found broken.

Pure units (error mapping, the document-type vocabulary, the streaming size cap, the branded email
templates) run everywhere. The rest exercise the real endpoints against a real ``authenticated``
session on Postgres (testcontainers), because every fix here is a SQL/RLS behaviour: the archived
template gate, generate idempotency, the retry re-queue, archive→unarchive, and the send that
persists its covering note and notifies the client.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import BytesIO
from typing import Any

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks, HTTPException, Response, UploadFile
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import document_types as doc_types
from app import storage
from app.auth import Principal, get_current_principal
from app.db import book_session, target_tenant_session
from app.email import render as email_render
from app.email import transport as email_mod
from app.main import app
from app.routers import documents as docs

PDF = b"%PDF-1.4 tiny\n%%EOF\n"
RENDER_SCHEMA = {"html": "<div class='page'><h1>{{ document_name }}</h1></div>", "css": ""}


# ── pure units ────────────────────────────────────────────────────────────────


def test_classify_render_error_maps_kinds_and_never_leaks_the_message() -> None:
    """The worker's diagnostic can quote a dataset's SQL or a client's data, so the API must expose
    a mapped kind + a fixed sentence — never the raw text. The exception CLASS name is safe."""
    template = docs.classify_render_error("TemplateSyntaxError: unexpected '}' at line 4")
    assert template is not None
    assert template.kind == "template"
    assert template.code == "TemplateSyntaxError"

    raw = (
        "ProgrammingError: (psycopg.errors.UndefinedColumn) column c.nino does not exist\n"
        "[SQL: SELECT c.nino, c.dob FROM crm.contacts c WHERE c.id = 42]"
    )
    data = docs.classify_render_error(raw)
    assert data is not None
    assert data.kind == "data"
    # The single most important assertion in this module: no fragment of the raw error escapes.
    assert "SELECT" not in data.summary
    assert "nino" not in data.summary
    assert "nino" not in (data.code or "")

    assert docs.classify_render_error("TimeoutError: pdf timed out").kind == "timeout"  # type: ignore[union-attr]
    assert docs.classify_render_error("StorageUnavailable: no bucket").kind == "storage"  # type: ignore[union-attr]
    assert (
        docs.classify_render_error("RuntimeError: template version has no rendered content").kind
        == "template"
    )  # type: ignore[union-attr]  # noqa: E501

    # An unrecognised failure is still surfaced, as 'unknown' — and a bare message (no class
    # prefix) yields no `code`, so nothing user-supplied is echoed.
    unknown = docs.classify_render_error("everything went sideways for client 4111 1111")
    assert unknown is not None
    assert unknown.kind == "unknown"
    assert unknown.code is None

    assert docs.classify_render_error(None) is None
    assert docs.classify_render_error("   ") is None


def test_a_missing_request_item_is_404_not_500() -> None:
    """UAT-20/27/28: a stale SIPP declaration must not 500 the client portal."""
    import inspect

    src = inspect.getsource(docs.fulfill_request_item)
    assert 'detail="Request item not found"' in src
    assert "HTTP_404_NOT_FOUND" in src
    none_branch = src.split("if item is None:", 1)[1][:400]
    assert "HTTP_500" not in none_branch


def test_document_type_tags_are_open_ended_and_normalised() -> None:
    """Types are no longer a closed vocabulary (the old `require_document_type` 422): any tag is
    normalised to a stable slug and registered on use. `slugify_tag` folds case and collapses runs
    of non-alphanumerics, so 'Report'/'report' dedupe to one tag and free text becomes a joinable
    slug; `labelize` prettifies a bare slug for display."""
    assert doc_types.slugify_tag("Suitability Report") == "suitability_report"
    assert doc_types.slugify_tag("Report") == doc_types.slugify_tag("report") == "report"
    assert doc_types.slugify_tag("proof of address!!") == "proof_of_address"
    assert doc_types.slugify_tag("   ") == ""  # blank ⇒ the caller rejects it
    assert doc_types.labelize("suitability_report") == "Suitability Report"


class _ChunkedUpload:
    """An upload that declares no size (a chunked POST) — ``file.size`` is None, which is exactly
    the case the old pre-check missed. Counts the bytes actually read."""

    def __init__(self, total: int) -> None:
        self.remaining = total
        self.read_bytes = 0
        self.size = None

    async def read(self, size: int = -1) -> bytes:
        n = min(size if size > 0 else self.remaining, self.remaining)
        self.remaining -= n
        self.read_bytes += n
        return b"x" * n


@pytest.mark.asyncio
async def test_read_capped_aborts_before_buffering_the_whole_body() -> None:
    """An over-cap body with no Content-Length must be refused while streaming, having buffered at
    most the cap plus one chunk — not the whole multi-GB payload."""
    limit = 4 * storage.READ_CHUNK_BYTES
    big = _ChunkedUpload(limit * 4)
    with pytest.raises(storage.PayloadTooLarge):
        await storage.read_capped(big, limit)
    assert big.read_bytes <= limit + storage.READ_CHUNK_BYTES

    small = _ChunkedUpload(1024)
    assert len(await storage.read_capped(small, limit)) == 1024


def test_document_sent_email_carries_the_note_and_escapes_it() -> None:
    """The covering note reaches the client (it used to be discarded) and is HTML-escaped, so an
    adviser can't inject markup into the mail we send on their behalf.

    Rendered through ``email_render.render_default`` — the shipped ``document_sent`` default merged
    for a send — since the ``document_sent_email`` builder this used to call is gone; the copy lives
    once, in ``app/email/defaults.py``."""
    subject, html = email_render.render_default(
        "document_sent",
        {
            "brand": email_mod.brand_tokens(email_mod.Brand(name="Ridley & Co", accent="#123456")),
            "document_name": "Annual Review 2026",
            "sent_by": "Ada Adviser",
            "message": "Please read section 3.\n<script>alert(1)</script>",
            "portal_url": "https://portal.example",
            "first_name": "Bo",
        },
        asset_origin=None,
    )
    # The subject is plain text on the wire, so the ampersand is NOT entity-escaped there; the body
    # is HTML, so it is.
    assert "Ridley & Co" in subject
    assert "Ridley &amp; Co" in html
    assert "Annual Review 2026" in html
    assert "Please read section 3." in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "https://portal.example" in html


@pytest.mark.asyncio
async def test_resolve_sender_wears_the_firms_name_without_breaking_delivery(monkeypatch) -> None:
    """White-label as far as deliverability allows: the firm's own support address is used as the
    From only when it is on the verified sending domain; otherwise the verified address stays and
    the firm's address becomes Reply-To."""
    email_mod.clear_caches()
    monkeypatch.setattr(email_mod.settings, "email_from", "Graphene <no-reply@graphene.test>")

    async def _row_off_domain(session, tenant_id):  # noqa: ANN001, ARG001
        return "Ridley & Co", {"client": {"support": {"email": "hello@ridley.test"}}}

    monkeypatch.setattr(email_mod, "_tenant_row", _row_off_domain)
    sender = await email_mod.resolve_sender(None, 7)  # type: ignore[arg-type]
    assert sender.from_addr == '"Ridley & Co" <no-reply@graphene.test>'
    assert sender.reply_to == "hello@ridley.test"
    # The sync shim now answers from the warmed cache instead of the Graphene default.
    assert email_mod.sender_for(7) == sender.from_addr

    email_mod.clear_caches()

    async def _row_on_domain(session, tenant_id):  # noqa: ANN001, ARG001
        return "Ridley & Co", {"client": {"support": {"email": "advice@graphene.test"}}}

    monkeypatch.setattr(email_mod, "_tenant_row", _row_on_domain)
    on_domain = await email_mod.resolve_sender(None, 8)  # type: ignore[arg-type]
    assert on_domain.from_addr == '"Ridley & Co" <advice@graphene.test>'
    assert on_domain.reply_to is None
    email_mod.clear_caches()


# ── DB-backed fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def firm(super_engine: Engine) -> dict[str, Any]:
    """A tenant with a staff user, two clients (one with no email address), a live template with a
    published version, and an ARCHIVED template with one too."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {
                    "uid": f"dten_{s}",
                    "alias": f"dfirm-{s}",
                    "name": f"DFirm {s}",
                },
            ).scalar_one()
        )
        staff = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, email, workos_user_id, first_name, last_name, "
                    "  enabled) VALUES (:uid, :email, :wos, 'Ada', 'Adviser', true) RETURNING id"
                ),
                {"uid": f"dusr_{s}", "email": f"staff_{s}@d.test", "wos": f"dwos_{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'platform')"
            ),
            {"u": staff, "t": tenant},
        )
        # A real individual carries first_name AND surname: `crm.contacts.name` is nullable and
        # is really the organisation column. This used to set first_name alone, which no contact
        # does -- and that shape hid the composed-name projection, since `_SELECT` now reads
        # COALESCE(first_name || ' ' || surname, name) and a first-name-only row reports "Bo".
        contacts = []
        contact_uids = []
        for label, email in (("Bo Client", f"bo_{s}@d.test"), ("No Email", None)):
            contact_uid = f"dcnt_{s}_{label[:2]}"
            contact_uids.append(contact_uid)
            contacts.append(
                int(
                    c.execute(
                        text(
                            "INSERT INTO crm.contacts (tenant_id, uid, type, name, first_name, "
                            "  surname, email) "
                            "VALUES (:t, :uid, 'individual', :n, :fn, :sn, :e) RETURNING id"
                        ),
                        {
                            "t": tenant,
                            "uid": contact_uid,
                            "n": label,
                            "fn": label.split(" ")[0],
                            "sn": label.split(" ")[1],
                            "e": email,
                        },
                    ).scalar_one()
                )
            )

        def _template(code: str, archived: bool) -> str:
            tpl_uid = f"dtpl_{code}_{s}"
            tid = c.execute(
                text(
                    "INSERT INTO documents.templates "
                    "  (uid, tenant_id, code, name, document_type, created_by, archived_at) "
                    "VALUES (:uid, :t, :code, 'Review', 'review', CAST(:by AS uuid), "
                    "  CASE WHEN :arch THEN now() ELSE NULL END) RETURNING id"
                ),
                {"uid": tpl_uid, "t": tenant, "code": f"{code}_{s}", "by": staff, "arch": archived},
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
                    "uid": f"dtplv_{code}_{s}",
                    "t": tenant,
                    "tid": tid,
                    "schema": json.dumps(RENDER_SCHEMA),
                    "by": staff,
                },
            ).scalar_one()
            c.execute(
                text(
                    "UPDATE documents.templates SET current_published_version_id = :v WHERE id = :t"
                ),
                {"v": vid, "t": tid},
            )
            return tpl_uid

        live = _template("live", archived=False)
        archived = _template("arch", archived=True)
        c.commit()
    return {
        "tenant_id": tenant,
        "user_uuid": staff,
        "org": f"dorg_{s}",
        "wos": f"dwos_{s}",
        "contact_id": contacts[0],
        "contact_uid": contact_uids[0],
        "no_email_contact_id": contacts[1],
        "tpl_uid": live,
        "archived_tpl_uid": archived,
    }


def _principal() -> Principal:
    return Principal(
        user_id="x",
        organisation_id="x",
        role="adviser",
        # documents:send rides along: the 0042 backfill grants it to every documents:write
        # holder, and this principal exercises the send/request (release) surface too.
        permissions=["documents:write", "documents:send", "contacts:read_all"],
    )


@pytest_asyncio.fixture
async def session_factory(db: dict[str, str], firm: dict[str, Any]):
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    claims = {
        "sub": firm["wos"],
        "role": "adviser",
        "portal": "platform",
        "permissions": ["documents:write", "documents:send", "contacts:read_all"],
        "org_id": firm["org"],
        "user_uuid": firm["user_uuid"],
        "tenant_id": firm["tenant_id"],
        "tenant_ids": [firm["tenant_id"]],
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
async def wire(session_factory, monkeypatch) -> AsyncIterator[AsyncClient]:
    """The real ASGI app with this firm's authenticated session injected.

    Everything else here calls the handler function directly, which is fine for SQL behaviour but
    bypasses the two things that changed ON THE WIRE — multipart form binding and query-alias
    resolution. A regression in either would leave every direct-call test green."""

    async def _session() -> AsyncIterator[Any]:
        async with session_factory() as session:
            yield session

    stored: list[str] = []

    async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        stored.append(key)

    monkeypatch.setattr(storage, "put_document", _put)
    app.dependency_overrides[target_tenant_session] = _session
    # The staff-side GETs (list here) now run on book_session — the tenant_session twin that
    # additionally accepts the operations-only ?tenant= (whose semantics test_book_session.py
    # owns). Same injected firm session for both, so this suite keeps testing the wire contract.
    app.dependency_overrides[book_session] = _session
    app.dependency_overrides[get_current_principal] = _principal
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(target_tenant_session, None)
        app.dependency_overrides.pop(book_session, None)
        app.dependency_overrides.pop(get_current_principal, None)


class _OverCapPart:
    """A multipart file part that declares NO size (a chunked POST with no Content-Length) and then
    yields more bytes than the cap allows — the shape a ``file.size``-only pre-check cannot see. Not
    an ``UploadFile``: the bytes are generated per read, so the test needn't allocate them up front.
    Distinct from ``_ChunkedUpload`` above, which counts reads for the pure ``read_capped`` unit."""

    def __init__(self, total: int, filename: str = "huge.pdf") -> None:
        self.filename = filename
        self.content_type = "application/pdf"
        self.size: int | None = None
        self._left = total

    async def read(self, size: int = -1, /) -> bytes:
        if self._left <= 0:
            return b""
        take = self._left if size < 0 else min(size, self._left)
        self._left -= take
        return (b"%PDF-1.4" + b"x" * (take - 8)) if take > 8 else b"x" * take


async def _generate(session_factory, firm, **overrides) -> Any:
    body = docs.GenerateDocument(
        template_uid=overrides.pop("template_uid", firm["tpl_uid"]),
        answers={},
        allow_incomplete=True,
        **overrides,
    )
    async with session_factory() as session:
        return await docs.generate_document(body=body, principal=_principal(), session=session)


# ── DB-backed behaviour ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_refuses_an_archived_template(session_factory, firm) -> None:
    """An archived template has been withdrawn — its published version must stop producing
    client-facing documents (the lookup had no archived_at filter)."""
    with pytest.raises(HTTPException) as ei:
        await _generate(session_factory, firm, template_uid=firm["archived_tpl_uid"])
    assert ei.value.status_code == 404
    assert "archived" in str(ei.value.detail).lower()


@pytest.mark.asyncio
async def test_generate_is_idempotent_per_key(session_factory, firm) -> None:
    """A double-submitted generate used to queue two documents and two renders. With an
    idempotency key the second call returns the first document and enqueues nothing."""
    first = await _generate(session_factory, firm, idempotency_key="idem-1")
    second = await _generate(session_factory, firm, idempotency_key="idem-1")
    assert second.uid == first.uid
    async with session_factory() as session:
        jobs = (
            await session.execute(
                text(
                    "SELECT count(*) FROM documents.render_jobs j "
                    "JOIN documents.documents d ON d.id = j.document_id WHERE d.uid = :uid"
                ),
                {"uid": first.uid},
            )
        ).scalar_one()
        docs_with_key = (
            await session.execute(
                text(
                    "SELECT count(*) FROM documents.documents "
                    "WHERE metadata ->> 'idempotency_key' = 'idem-1'"
                )
            )
        ).scalar_one()
    assert jobs == 1
    assert docs_with_key == 1

    # A different key is a different document (the guard must not swallow real second generates).
    other = await _generate(session_factory, firm, idempotency_key="idem-2")
    assert other.uid != first.uid


@pytest.mark.asyncio
async def test_failed_document_exposes_a_mapped_error(session_factory, firm, super_engine) -> None:
    """DocumentOut carried no error field at all, so every failure read as one generic sentence.
    The worker's diagnostic is now surfaced as a mapped kind — with none of its raw text."""
    doc = await _generate(session_factory, firm)
    with super_engine.connect() as c:
        c.execute(
            text("UPDATE documents.documents SET status = 'failed', error = :e WHERE uid = :uid"),
            {
                "e": "TemplateSyntaxError: unexpected end of template [SQL: SELECT secret FROM t]",
                "uid": doc.uid,
            },
        )
        c.commit()
    async with session_factory() as session:
        got = await docs.get_document(uid=doc.uid, principal=_principal(), session=session)
        failed = await docs.list_documents(
            document_status="failed", principal=_principal(), session=session
        )
    assert got.status == "failed"
    assert got.error  # the sentence the UI shows
    assert got.error_kind == "template"
    assert got.error_code == "TemplateSyntaxError"
    assert "SELECT" not in got.error  # the raw diagnostic never crosses the boundary
    assert "secret" not in got.error
    assert doc.uid in {d.uid for d in failed}


@pytest.mark.asyncio
async def test_retry_requeues_a_failed_document_idempotently(
    session_factory, firm, super_engine
) -> None:
    """A dead-lettered document was terminal — no endpoint, no UI. Retry re-queues it once: a
    second call while the job is live must not enqueue a second render."""
    doc = await _generate(session_factory, firm)
    with super_engine.connect() as c:
        c.execute(
            text(
                "UPDATE documents.documents SET status = 'failed', error = 'RuntimeError: boom' "
                "WHERE uid = :uid"
            ),
            {"uid": doc.uid},
        )
        c.execute(
            text(
                "UPDATE documents.render_jobs SET status = 'failed', attempts = 3, "
                "  last_error = 'RuntimeError: boom' "
                "WHERE document_id = (SELECT id FROM documents.documents WHERE uid = :uid)"
            ),
            {"uid": doc.uid},
        )
        c.commit()

    async with session_factory() as session:
        retried = await docs.retry_document(uid=doc.uid, principal=_principal(), session=session)
    assert retried.status == "queued"
    assert retried.error is None

    async with session_factory() as session:
        again = await docs.retry_document(uid=doc.uid, principal=_principal(), session=session)
        live = (
            await session.execute(
                text(
                    "SELECT count(*) FROM documents.render_jobs j "
                    "JOIN documents.documents d ON d.id = j.document_id "
                    "WHERE d.uid = :uid AND j.status = 'queued'"
                ),
                {"uid": doc.uid},
            )
        ).scalar_one()
        events = (
            (
                await session.execute(
                    text(
                        "SELECT a.detail FROM documents.audit_log a "
                        "JOIN documents.documents d ON d.id = a.document_id "
                        "WHERE d.uid = :uid AND a.action = 'document.queued'"
                    ),
                    {"uid": doc.uid},
                )
            )
            .scalars()
            .all()
        )
    assert again.status == "queued"
    assert live == 1  # not two
    assert any((e or {}).get("retry") for e in events)


@pytest.mark.asyncio
async def test_retry_rejects_a_live_or_uploaded_document(session_factory, firm) -> None:
    """Retry is for a failed render only — a generated (finished) document 409s, and an uploaded
    document has nothing to re-render (422)."""
    doc = await _generate(session_factory, firm)
    async with session_factory() as session:
        await session.execute(
            text("UPDATE documents.documents SET status = 'generated' WHERE uid = :uid"),
            {"uid": doc.uid},
        )
        with pytest.raises(HTTPException) as ei:
            await docs.retry_document(uid=doc.uid, principal=_principal(), session=session)
        assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_retry_refuses_an_uploaded_document(session_factory, firm, monkeypatch) -> None:
    """An uploaded document has no template version, so there is nothing to re-render — the guard at
    ``source != 'generated' or template_version_id is None`` must 422 rather than enqueue a job the
    worker can only fail. (The 409 sibling covers a finished render; this branch was untested.)"""

    async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        return None

    monkeypatch.setattr(storage, "put_document", _put)
    async with session_factory() as session:
        uploaded = await docs.upload_document(
            file=[UploadFile(filename="scan.pdf", file=BytesIO(PDF), size=len(PDF))],
            document_type="identity",
            name=None,
            subject_contact_id=None,
            principal=_principal(),
            session=session,
        )
        uid = uploaded.documents[0].uid
        # Put it in the only status retry would otherwise accept, to prove the source guard is what
        # refuses it and not the status check.
        await session.execute(
            text("UPDATE documents.documents SET status = 'failed' WHERE uid = :uid"), {"uid": uid}
        )
        with pytest.raises(HTTPException) as ei:
            await docs.retry_document(uid=uid, principal=_principal(), session=session)
    assert ei.value.status_code == 422
    assert "generated" in str(ei.value.detail)


@pytest.mark.asyncio
async def test_two_parallel_retries_queue_exactly_one_render(
    session_factory, firm, super_engine
) -> None:
    """``render_jobs`` has no unique constraint on ``document_id``, so retry's check-then-INSERT is
    only safe if the two statements are serialised. Two tabs clicking Retry at the same moment used
    to enqueue two jobs — the worker then renders twice and both writes race on ``pdf_file_id``."""
    doc = await _generate(session_factory, firm)
    with super_engine.connect() as c:
        c.execute(
            text("UPDATE documents.documents SET status = 'failed' WHERE uid = :uid"),
            {"uid": doc.uid},
        )
        c.execute(
            text(
                "UPDATE documents.render_jobs SET status = 'failed', attempts = 3 "
                "WHERE document_id = (SELECT id FROM documents.documents WHERE uid = :uid)"
            ),
            {"uid": doc.uid},
        )
        c.commit()

    async def _retry() -> Any:
        async with session_factory() as session:
            return await docs.retry_document(uid=doc.uid, principal=_principal(), session=session)

    # Separate sessions ⇒ separate connections ⇒ a genuine concurrent pair, not a retry loop.
    both = await asyncio.gather(_retry(), _retry())
    assert {d.status for d in both} == {"queued"}
    with super_engine.connect() as c:
        queued = c.execute(
            text(
                "SELECT count(*) FROM documents.render_jobs j "
                "JOIN documents.documents d ON d.id = j.document_id "
                "WHERE d.uid = :uid AND j.status = 'queued'"
            ),
            {"uid": doc.uid},
        ).scalar_one()
        retries = c.execute(
            text(
                "SELECT count(*) FROM documents.audit_log a "
                "JOIN documents.documents d ON d.id = a.document_id "
                "WHERE d.uid = :uid AND a.action = 'document.queued' "
                "  AND a.detail ->> 'retry' = 'true'"
            ),
            {"uid": doc.uid},
        ).scalar_one()
    assert queued == 1  # not two
    assert retries == 1


@pytest.mark.asyncio
async def test_unarchive_restores_a_legacy_archived_document_as_generated(
    session_factory, firm, super_engine
) -> None:
    """A document archived BEFORE ``metadata.archived_from`` was recorded has nothing to restore
    from. It must come back as ``generated`` (the live state) rather than fail the cast or stay
    stuck — the COALESCE default, which no test covered."""
    doc = await _generate(session_factory, firm)
    async with session_factory() as session:
        await docs.archive_document(uid=doc.uid, principal=_principal(), session=session)
    with super_engine.connect() as c:  # rewind to how a pre-fix archive left the row
        c.execute(
            text(
                "UPDATE documents.documents SET metadata = metadata - 'archived_from' "
                "WHERE uid = :uid"
            ),
            {"uid": doc.uid},
        )
        c.commit()
    async with session_factory() as session:
        restored = await docs.unarchive_document(
            uid=doc.uid, principal=_principal(), session=session
        )
    assert restored.status == "generated"


@pytest.mark.asyncio
async def test_allow_incomplete_overrides_the_completeness_gate(
    session_factory, firm, super_engine
) -> None:
    """The gate refuses a template whose tokens have no data source, and its 422 names
    ``allow_incomplete`` as the override. That override has to actually work, or the message sends
    the adviser after a flag that does nothing."""
    s = uuid.uuid4().hex[:8]
    tpl_uid = f"dtpl_unwired_{s}"
    with super_engine.connect() as c:
        tid = c.execute(
            text(
                "INSERT INTO documents.templates "
                "  (uid, tenant_id, code, name, document_type, created_by) "
                "VALUES (:uid, :t, :code, 'Unwired', 'review', CAST(:by AS uuid)) RETURNING id"
            ),
            {
                "uid": tpl_uid,
                "t": firm["tenant_id"],
                "code": f"unwired_{s}",
                "by": firm["user_uuid"],
            },
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
                "uid": f"dtplv_unwired_{s}",
                "t": firm["tenant_id"],
                "tid": tid,
                "schema": json.dumps({"html": "<p>{{ mystery_total }}</p>", "css": ""}),
                "by": firm["user_uuid"],
            },
        ).scalar_one()
        c.execute(
            text("UPDATE documents.templates SET current_published_version_id = :v WHERE id = :t"),
            {"v": vid, "t": tid},
        )
        c.commit()

    async with session_factory() as session:
        with pytest.raises(HTTPException) as ei:
            await docs.generate_document(
                body=docs.GenerateDocument(template_uid=tpl_uid, answers={}),
                principal=_principal(),
                session=session,
            )
    assert ei.value.status_code == 422
    assert "mystery_total" in str(ei.value.detail)
    assert "allow_incomplete" in str(ei.value.detail)

    async with session_factory() as session:
        draft = await docs.generate_document(
            body=docs.GenerateDocument(template_uid=tpl_uid, answers={}, allow_incomplete=True),
            principal=_principal(),
            session=session,
        )
    assert draft.status == "queued"


@pytest.mark.asyncio
async def test_archive_then_unarchive_restores_the_prior_status(session_factory, firm) -> None:
    """Archiving was one-way: no unarchive, no archived view. It now round-trips, and the archived
    slice is reachable — so the confirmation copy may honestly call it recoverable."""
    doc = await _generate(session_factory, firm)
    async with session_factory() as session:
        await session.execute(
            text("UPDATE documents.documents SET status = 'failed' WHERE uid = :uid"),
            {"uid": doc.uid},
        )
        archived = await docs.archive_document(uid=doc.uid, principal=_principal(), session=session)
        assert archived.status == "archived"
        active = await docs.list_documents(principal=_principal(), session=session)
        assert doc.uid not in {d.uid for d in active}
        in_archive = await docs.list_documents(
            document_status="archived", principal=_principal(), session=session
        )
        assert doc.uid in {d.uid for d in in_archive}
        # Archiving twice is not an error (the bulk bar retries).
        again = await docs.archive_document(uid=doc.uid, principal=_principal(), session=session)
        assert again.status == "archived"

        restored = await docs.unarchive_document(
            uid=doc.uid, principal=_principal(), session=session
        )
        assert restored.status == "failed"  # exactly what it was, not a fake 'generated'
        actions = (
            (
                await session.execute(
                    text(
                        "SELECT a.action FROM documents.audit_log a "
                        "JOIN documents.documents d ON d.id = a.document_id WHERE d.uid = :uid"
                    ),
                    {"uid": doc.uid},
                )
            )
            .scalars()
            .all()
        )
    assert "document.archived" in actions
    assert "document.unarchived" in actions


@pytest.mark.asyncio
async def test_send_persists_the_note_and_reports_who_can_be_emailed(session_factory, firm) -> None:
    """Send grants portal visibility and persists the covering note on each recipient's own
    `document.sent` event — which is where the notification system reads it to render the
    `document_sent` email. There is no inline send here; `notified` reports how many recipients CAN
    be emailed (have an address), and a recipient with none is named back."""
    doc = await _generate(session_factory, firm)
    async with session_factory() as session:
        result = await docs.send_document(
            uid=doc.uid,
            body=docs.SendDocument(
                contact_ids=[firm["contact_id"], firm["no_email_contact_id"]],
                message="Please read section 3 before we meet.",
            ),
            principal=_principal(),
            session=session,
        )
        details = (
            (
                await session.execute(
                    text(
                        "SELECT a.detail FROM documents.audit_log a "
                        "JOIN documents.documents d ON d.id = a.document_id "
                        "WHERE d.uid = :uid AND a.action = 'document.sent'"
                    ),
                    {"uid": doc.uid},
                )
            )
            .scalars()
            .all()
        )
    assert result.sent == 2
    assert result.notified == 1  # only the recipient who has an email address
    assert result.not_notified == ["No Email"]
    # The note the notification email will carry is persisted on each recipient's send event.
    assert len(details) == 2
    assert all(d["message"] == "Please read section 3 before we meet." for d in details)


@pytest.mark.asyncio
async def test_raising_a_request_persists_it_for_notification(session_factory, firm) -> None:
    """Creating a request persists the request + its items — the content the `document.request`
    notification email renders (item list + note + due date). Delivery itself is the notification
    system's job, not an inline send."""
    async with session_factory() as session:
        req = await docs.create_request(
            body=docs.CreateRequest(
                contact_id=firm["contact_id"],
                message="Two things when you have a moment.",
                due_date="2026-09-01",
                items=[
                    docs.RequestItemIn(action="upload", title="Passport scan"),
                    docs.RequestItemIn(action="upload", title="Proof of address"),
                ],
            ),
            principal=_principal(),
            session=session,
        )
        titles = (
            (
                await session.execute(
                    text(
                        "SELECT i.title FROM documents.request_items i "
                        "JOIN documents.requests r ON r.id = i.request_id "
                        "WHERE r.uid = :u ORDER BY i.id"
                    ),
                    {"u": req.uid},
                )
            )
            .scalars()
            .all()
        )
    assert req.status == "pending"
    assert titles == ["Passport scan", "Proof of address"]


@pytest.mark.asyncio
async def test_raising_a_request_mirrors_each_item_as_a_task(session_factory, firm) -> None:
    """docs/tasks.md: Tasks and Requests are separate primitives, but a request-raised task
    still needs to show up under Tasks — one per item, assigned to the CLIENT (the action is
    theirs to do), linked back via `source_kind='document'` + the item's own uid as
    `source_ref` — never `subject_type='document'`, which means an actual documents.documents
    row (app/tasks/service.py's _SUBJECT_CHECKS), not a request."""
    async with session_factory() as session:
        await docs.create_request(
            body=docs.CreateRequest(
                contact_id=firm["contact_id"],
                due_date="2026-09-01",
                priority="urgent",
                items=[
                    docs.RequestItemIn(action="upload", title="Passport scan"),
                    docs.RequestItemIn(action="upload", title="Proof of address"),
                ],
            ),
            principal=_principal(),
            session=session,
        )
        tasks = (
            (
                await session.execute(
                    text(
                        "SELECT title, category, source_kind, source_ref, assignee_kind, "
                        "  assignee_contact_id, priority, due_date, status "
                        "FROM tasks.tasks WHERE tenant_id = :t AND assignee_contact_id = :c "
                        "ORDER BY id"
                    ),
                    {"t": firm["tenant_id"], "c": firm["contact_id"]},
                )
            )
            .mappings()
            .all()
        )
    assert [t["title"] for t in tasks] == ["Passport scan", "Proof of address"]
    assert all(t["category"] == "upload" for t in tasks)
    assert all(t["source_kind"] == "document" for t in tasks)
    assert all(t["assignee_kind"] == "contact" for t in tasks)
    assert all(t["priority"] == "high" for t in tasks)  # tasks has no 'urgent' — folds into 'high'
    assert all(str(t["due_date"]) == "2026-09-01" for t in tasks)
    assert all(t["status"] == "open" for t in tasks)
    assert len({t["source_ref"] for t in tasks}) == 2  # distinct per item, never collapsed


@pytest.mark.asyncio
async def test_upload_stores_every_dropped_file_and_names_the_rejects(
    session_factory, firm, monkeypatch
) -> None:
    """A multi-file drop posted only files[0] and reported success. Every part is now stored, and a
    file we can't accept is named back instead of vanishing."""
    stored: list[str] = []

    async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        stored.append(key)

    monkeypatch.setattr(storage, "put_document", _put)
    files = [
        UploadFile(filename="one.pdf", file=BytesIO(PDF), size=len(PDF)),
        UploadFile(filename="two.pdf", file=BytesIO(PDF), size=len(PDF)),
        UploadFile(filename="notes.txt", file=BytesIO(b"just text"), size=9),
        UploadFile(filename="malware.exe", file=BytesIO(b"MZ\x90\x00binary"), size=10),
    ]
    async with session_factory() as session:
        result = await docs.upload_document(
            file=files,
            document_type="statement",
            name=None,
            subject_contact_id=None,
            principal=_principal(),
            session=session,
        )
    assert [d.name for d in result.documents] == ["one.pdf", "two.pdf", "notes.txt"]
    assert {d.document_type for d in result.documents} == {"statement"}
    assert len(stored) == 3
    assert [r.filename for r in result.rejected] == ["malware.exe"]
    assert result.rejected[0].reason


@pytest.mark.asyncio
async def test_an_undeclared_over_cap_upload_is_refused_at_the_call_site(
    session_factory, firm, monkeypatch
) -> None:
    """The streaming cap has to bite where the endpoint reads, not only in the helper's own unit
    test. A part that declares no size and then exceeds the cap is named back (the batch reports it)
    and nothing is stored — a good file in the same drop still lands."""
    stored: list[str] = []

    async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        stored.append(key)

    monkeypatch.setattr(storage, "put_document", _put)
    over_cap = _OverCapPart(storage.MAX_DOCUMENT_BYTES + 1)
    async with session_factory() as session:
        result = await docs.upload_document(
            file=[UploadFile(filename="ok.pdf", file=BytesIO(PDF), size=len(PDF)), over_cap],  # type: ignore[list-item]
            document_type="statement",
            name=None,
            subject_contact_id=None,
            principal=_principal(),
            session=session,
        )
    assert [d.name for d in result.documents] == ["ok.pdf"]
    assert len(stored) == 1
    assert [r.filename for r in result.rejected] == ["huge.pdf"]
    assert "MiB" in result.rejected[0].reason

    # Nothing landed at all ⇒ the whole request is refused, naming the reason.
    async with session_factory() as session:
        with pytest.raises(HTTPException) as ei:
            await docs.upload_document(
                file=[_OverCapPart(storage.MAX_DOCUMENT_BYTES + 1)],  # type: ignore[list-item]
                document_type="statement",
                name=None,
                subject_contact_id=None,
                principal=_principal(),
                session=session,
            )
    assert ei.value.status_code == 422
    assert "MiB" in str(ei.value.detail)


@pytest.mark.asyncio
async def test_upload_refuses_more_files_than_the_ceiling_allows(session_factory, firm) -> None:
    """The request-body ceiling is sized for ``MAX_UPLOAD_FILES`` documents, so the part count has
    to be bounded too — otherwise one POST costs whatever the client feels like sending."""
    async with session_factory() as session:
        with pytest.raises(HTTPException) as ei:
            await docs.upload_document(
                file=[
                    UploadFile(filename=f"f{i}.pdf", file=BytesIO(PDF), size=len(PDF))
                    for i in range(docs.MAX_UPLOAD_FILES + 1)
                ],
                document_type="statement",
                name=None,
                subject_contact_id=None,
                principal=_principal(),
                session=session,
            )
    assert ei.value.status_code == 422
    assert str(docs.MAX_UPLOAD_FILES) in str(ei.value.detail)


@pytest.mark.asyncio
async def test_the_changed_wire_contracts_bind_over_http(wire, firm) -> None:
    """Both contracts the audit changed, exercised through FastAPI rather than around it: repeated
    ``file`` parts must each become a document (the bug was posting files[0] only), and the archived
    slice must bind through ``?status=`` (the alias — the handler's own parameter is named
    ``document_status``, and an unknown query param would be silently ignored)."""
    part = ("file", ("a.pdf", PDF, "application/pdf"))
    single = await wire.post("/documents/upload", files=[part], data={"document_type": "statement"})
    assert single.status_code == 201
    assert len(single.json()["documents"]) == 1  # one part ⇒ a one-element list, not a 422

    both = await wire.post(
        "/documents/upload",
        files=[
            ("file", ("one.pdf", PDF, "application/pdf")),
            ("file", ("two.pdf", PDF, "application/pdf")),
        ],
        data={"document_type": "statement"},
    )
    assert both.status_code == 201
    assert [d["name"] for d in both.json()["documents"]] == ["one.pdf", "two.pdf"]

    archived_uid = both.json()["documents"][0]["uid"]
    assert (await wire.post(f"/documents/{archived_uid}/archive")).status_code == 200

    active = await wire.get("/documents")
    assert active.status_code == 200
    assert archived_uid not in {d["uid"] for d in active.json()}

    archived = await wire.get("/documents", params={"status": "archived"})
    assert archived.status_code == 200
    assert archived_uid in {d["uid"] for d in archived.json()}

    # An invented slice is refused, so a mistyped view can't silently render the working set.
    assert (await wire.get("/documents", params={"status": "bogus"})).status_code == 422


@pytest.mark.asyncio
async def test_upload_registers_an_unknown_type_rather_than_refusing_it(
    session_factory, firm, monkeypatch
) -> None:
    """Types are open-ended: an unrecognised `document_type` is normalised to a slug, registered as
    a reusable suggestion in the tenant's registry, and the upload succeeds under it — NOT the old
    closed-vocabulary 422. The reserved sentinels (blank, 'auto', 'other') are still refused, at the
    point the tag would be registered."""

    async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        return None  # storage isn't the subject here (no live S3 in unit tests)

    monkeypatch.setattr(storage, "put_document", _put)
    async with session_factory() as session:
        result = await docs.upload_document(
            file=[UploadFile(filename="a.pdf", file=BytesIO(PDF), size=len(PDF))],
            document_type="Bespoke Brochure",
            name=None,
            subject_contact_id=None,
            principal=_principal(),
            session=session,
        )
    assert [d.document_type for d in result.documents] == ["bespoke_brochure"]
    # The new tag is now a reusable suggestion in the tenant's registry.
    async with session_factory() as session:
        slugs = (
            (
                await session.execute(
                    text("SELECT slug FROM documents.document_types WHERE kind = 'document'")
                )
            )
            .scalars()
            .all()
        )
    assert "bespoke_brochure" in slugs
    # The reserved sentinels remain refused where the tag is registered.
    for bad in ("", "   ", "auto", "other"):
        async with session_factory() as session:
            with pytest.raises(HTTPException) as ei:
                await docs.upload_document(
                    file=[UploadFile(filename="a.pdf", file=BytesIO(PDF), size=len(PDF))],
                    document_type=bad,
                    name=None,
                    subject_contact_id=None,
                    principal=_principal(),
                    session=session,
                )
            assert ei.value.status_code == 422


@pytest.mark.asyncio
async def test_assign_files_a_document_under_a_client(session_factory, firm) -> None:
    """PATCH /documents/{uid} with intended_contact_id files an existing document under a client
    (the "Add from existing documents" path): the made-for name reads back on the document, and a
    distinct `document.assigned` event lands in the audit trail."""
    doc = await _generate(session_factory, firm)
    async with session_factory() as session:
        updated = await docs.update_document(
            doc.uid,
            docs.UpdateDocument(intended_contact_id=firm["contact_id"]),
            _principal(),
            session,
        )
    assert updated.client == "Bo Client"
    async with session_factory() as session:
        actions = (
            (
                await session.execute(
                    text(
                        "SELECT al.action FROM documents.audit_log al "
                        "JOIN documents.documents d ON d.id = al.document_id WHERE d.uid = :u"
                    ),
                    {"u": doc.uid},
                )
            )
            .scalars()
            .all()
        )
    assert "document.assigned" in actions


@pytest.mark.asyncio
async def test_assign_refuses_a_client_the_caller_cannot_see(session_factory, firm) -> None:
    """The made-for target must be a contact the caller can see (RLS on crm.contacts) — assigning to
    an unknown/foreign contact is a 404, never a silent cross-tenant filing."""
    doc = await _generate(session_factory, firm)
    async with session_factory() as session:
        with pytest.raises(HTTPException) as ei:
            await docs.update_document(
                doc.uid,
                docs.UpdateDocument(intended_contact_id=10_000_000),
                _principal(),
                session,
            )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_upload_refuses_a_subject_client_the_caller_cannot_see(
    session_factory, firm, monkeypatch
) -> None:
    """Filing an upload under a client (``subject_contact_id``) is gated on the SAME visibility
    check as generate/request: an unknown or cross-tenant contact is refused (422) BEFORE anything
    is stored — a staffer can't file under a client they can't see, and a bad id can't reach the
    0035 composite FK as a 500."""
    stored: list[str] = []

    async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        stored.append(key)

    monkeypatch.setattr(storage, "put_document", _put)
    async with session_factory() as session:
        with pytest.raises(HTTPException) as ei:
            await docs.upload_document(
                file=[UploadFile(filename="a.pdf", file=BytesIO(PDF), size=len(PDF))],
                document_type="statement",
                name=None,
                subject_contact_id=10_000_000,
                principal=_principal(),
                session=session,
            )
    assert ei.value.status_code == 422
    assert stored == []  # refused before any object was written to the bucket


@pytest.mark.asyncio
async def test_upload_files_under_a_visible_client(session_factory, firm, monkeypatch) -> None:
    """The happy path still works: uploading with a subject the caller CAN see files the document
    under that client (the visibility gate doesn't block a legitimate made-for)."""

    async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        return None

    monkeypatch.setattr(storage, "put_document", _put)
    async with session_factory() as session:
        result = await docs.upload_document(
            file=[UploadFile(filename="a.pdf", file=BytesIO(PDF), size=len(PDF))],
            document_type="statement",
            name=None,
            subject_contact_id=firm["contact_id"],
            principal=_principal(),
            session=session,
        )
    assert result.documents[0].client == "Bo Client"


async def test_the_response_carries_the_filed_client_s_ID_not_only_their_name(
    session_factory, firm, monkeypatch
) -> None:
    """`client_contact_id` must reach the wire, on the upload AND on the later reads.

    THE DEFECT THIS EXISTS FOR, and why nothing caught it. `_SELECT` aliased
    `d.intended_contact_id AS client_contact_id`, and `DocumentOut.from_row` passes it to the model
    as a kwarg — but the model never DECLARED the field. Pydantic v2 defaults to `extra='ignore'`,
    so the value was discarded during construction: no error, no warning, nothing in
    `__pydantic_extra__`. FastAPI's `response_model` then emits declared fields only, so the id
    never left the API, and the generated TS client never gained the field either.

    Two shipped features depended on it — the record's link to the client, and "Raise a request"
    opening on that client — and both gate on `clientContactId != null`, so both were dead in every
    environment. The UI tests passed throughout because their fixtures set the field by hand: they
    proved the UI works GIVEN the id and never touched the code that failed to produce it.

    So this asserts the ENDPOINT's own output, which is the only place the bug was visible. The name
    is checked alongside deliberately: `client` was always populated, which is exactly what made the
    missing id easy to miss.
    """

    async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        return None

    monkeypatch.setattr(storage, "put_document", _put)
    contact_id = firm["contact_id"]
    async with session_factory() as session:
        result = await docs.upload_document(
            file=[UploadFile(filename="a.pdf", file=BytesIO(PDF), size=len(PDF))],
            document_type="statement",
            name=None,
            subject_contact_id=contact_id,
            principal=_principal(),
            session=session,
        )
    uploaded = result.documents[0]
    assert uploaded.client == "Bo Client"
    assert uploaded.client_contact_id == contact_id
    # …and their UID, which went missing in exactly the same way and for exactly the same reason:
    # `_SELECT` aliased `ic.uid AS client_contact_uid` and the model did not declare it, so the
    # record's "Filed for" row — which gates on `clientContactUid != null` — never became a link.
    assert uploaded.client_contact_uid == firm["contact_uid"]

    # …and on the single-document read the record page actually calls.
    async with session_factory() as session:
        fetched = await docs.get_document(uid=uploaded.uid, principal=_principal(), session=session)
    assert fetched.client_contact_id == contact_id
    assert fetched.client_contact_uid == firm["contact_uid"]

    # …and on the listing.
    async with session_factory() as session:
        listed = await docs.list_documents(principal=_principal(), session=session)
    row = next(d for d in listed if d.uid == uploaded.uid)
    assert row.client_contact_id == contact_id
    assert row.client_contact_uid == firm["contact_uid"]

    # A document filed under NOBODY reports null rather than 0 or a stray id.
    async with session_factory() as session:
        unfiled = await docs.upload_document(
            file=[UploadFile(filename="b.pdf", file=BytesIO(PDF), size=len(PDF))],
            document_type="statement",
            name=None,
            subject_contact_id=None,
            principal=_principal(),
            session=session,
        )
    assert unfiled.documents[0].client_contact_id is None
    assert unfiled.documents[0].client_contact_uid is None


async def test_the_filed_client_s_name_is_composed_not_read_from_the_name_column(
    session_factory, firm, super_engine, monkeypatch
) -> None:
    """`client` must be the client's DISPLAY name, composed from first_name/surname.

    THE DEFECT THIS EXISTS FOR. `_SELECT` read `ic.name AS client`, but `crm.contacts.name` is
    NULLABLE and is really the organisation column — an individual's name lives in
    first_name/surname. So for an ordinary client the Client column read "—" on every correctly
    filed document, which is indistinguishable from "this document is filed under nobody".

    Worse, it was not only cosmetic: `client-documents.svelte` builds its "Add from existing
    documents" picker from the documents with no `client` — i.e. the ones filed under NOBODY — so
    every properly filed document was still offered as unassigned and could be silently re-homed
    to a different client.

    The tenant fixture's own contacts carry both halves, so this uses a name-less contact to pin
    the composition itself rather than a row where `name` happens to agree with it.
    """

    async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        return None

    monkeypatch.setattr(storage, "put_document", _put)
    # Inserted elevated: the request session runs as `authenticated` under RLS, which has no INSERT
    # on crm.contacts — the same reason the `firm` fixture builds its contacts on super_engine.
    with super_engine.connect() as c:
        contact_id = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                    "VALUES (:t, :uid, 'individual', 'Margaret', 'Okonjo') RETURNING id"
                ),
                {"t": firm["tenant_id"], "uid": f"dcnt_named_{firm['org']}"},
            ).scalar_one()
        )
        c.commit()

    async with session_factory() as session:
        result = await docs.upload_document(
            file=[UploadFile(filename="c.pdf", file=BytesIO(PDF), size=len(PDF))],
            document_type="statement",
            name=None,
            subject_contact_id=contact_id,
            principal=_principal(),
            session=session,
        )
    assert result.documents[0].client == "Margaret Okonjo"

    # An ORGANISATION has no first_name/surname and its name IS `name` — the COALESCE must still
    # fall through to it rather than reporting a blank.
    with super_engine.connect() as c:
        org_id = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name) "
                    "VALUES (:t, :uid, 'organisation', 'Okonjo Holdings Ltd') RETURNING id"
                ),
                {"t": firm["tenant_id"], "uid": f"dcnt_org_{firm['org']}"},
            ).scalar_one()
        )
        c.commit()

    async with session_factory() as session:
        org_result = await docs.upload_document(
            file=[UploadFile(filename="d.pdf", file=BytesIO(PDF), size=len(PDF))],
            document_type="statement",
            name=None,
            subject_contact_id=org_id,
            principal=_principal(),
            session=session,
        )
    assert org_result.documents[0].client == "Okonjo Holdings Ltd"


# ── auto-link on filing: the client sees what is filed under them ─────────────
# A client should see EVERY document filed against them, with no staffer having to "send" it.
# Filing binds a `role='subject'` document_contacts link, which the client branch of documents_read
# (0021) honours because it matches on contact_id alone. The role is what keeps this separable from
# a send, so these tests pin BOTH halves: the link exists, and it reports nothing as sent.


async def _as_rendered(session_factory, doc_uid: str, contact_id: int | None = None) -> None:
    """Stand in for ``worker._persist``: mark a queued document ``generated`` and, when it is filed
    under a client, file that client's 'subject' link.

    Tests needing a document the client can ALREADY see come through here now, because ``generate``
    deliberately leaves a queued one unlinked: the client branch of ``documents_read`` has no status
    predicate, so a link filed at enqueue time showed the client a document that had not rendered
    (and, on failure, never would). ``test_worker_render_callback`` owns the proof that the worker
    itself does this; here it is only a precondition.
    """
    async with session_factory() as session:
        await session.execute(
            text("UPDATE documents.documents SET status = 'generated' WHERE uid = :u"),
            {"u": doc_uid},
        )
        if contact_id is not None:
            await session.execute(
                text(
                    "INSERT INTO documents.document_contacts "
                    "  (document_id, tenant_id, contact_id, role) "
                    "SELECT d.id, d.tenant_id, :cid, 'subject' FROM documents.documents d "
                    "WHERE d.uid = :u ON CONFLICT DO NOTHING"
                ),
                {"u": doc_uid, "cid": contact_id},
            )
        await session.commit()


async def _links(session_factory, doc_uid: str) -> list[tuple[int, str]]:
    """Every (contact_id, role) link on a document — the table the whole feature turns on."""
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT dc.contact_id, dc.role FROM documents.document_contacts dc "
                        "JOIN documents.documents d ON d.id = dc.document_id WHERE d.uid = :u"
                    ),
                    {"u": doc_uid},
                )
            )
            .mappings()
            .all()
        )
    return [(int(r["contact_id"]), str(r["role"])) for r in rows]


@pytest.mark.asyncio
async def test_generate_files_a_subject_link_so_the_client_can_see_it(
    session_factory, firm
) -> None:
    """Generating a document FOR a client makes it theirs to see without anyone sending it — but
    only ONCE IT HAS RENDERED. At enqueue the document is `queued` and carries no link at all: the
    client branch of `documents_read` matches on contact_id with no status predicate, so a link here
    would show them a document the renderer had not touched, or a failed one for ever.
    `intended_contact_id` carries the intent across, and `worker._persist` files the link on
    success (proved end-to-end in test_worker_render_callback)."""
    doc = await _generate(session_factory, firm, client_contact_id=firm["contact_id"])
    assert await _links(session_factory, doc.uid) == []
    async with session_factory() as session:
        intended = await session.execute(
            text("SELECT intended_contact_id FROM documents.documents WHERE uid = :u"),
            {"u": doc.uid},
        )
    assert intended.scalar_one() == firm["contact_id"], "the intent must survive to the worker"
    # …and once rendered, the client can see it — the feature, at the right moment.
    await _as_rendered(session_factory, doc.uid, firm["contact_id"])
    assert await _links(session_factory, doc.uid) == [(firm["contact_id"], "subject")]


@pytest.mark.asyncio
async def test_upload_files_a_subject_link_so_the_client_can_see_it(
    session_factory, firm, monkeypatch
) -> None:
    """Same for an upload filed under a client (``subject_contact_id``), which is how a staffer
    files a scanned or externally-produced document against a client record."""

    async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        return None

    monkeypatch.setattr(storage, "put_document", _put)
    async with session_factory() as session:
        result = await docs.upload_document(
            file=[UploadFile(filename="a.pdf", file=BytesIO(PDF), size=len(PDF))],
            document_type="statement",
            name=None,
            subject_contact_id=firm["contact_id"],
            principal=_principal(),
            session=session,
        )
    uid = result.documents[0].uid
    assert await _links(session_factory, uid) == [(firm["contact_id"], "subject")]


@pytest.mark.asyncio
async def test_assign_files_a_subject_link_and_a_refile_keeps_the_first(
    session_factory, firm
) -> None:
    """PATCH's re-file case. Filing under a client links them; RE-filing under a different client
    links the new one and deliberately LEAVES the first link standing — the first client has
    already been able to see the document, so silently retracting it would be a different (and
    unlogged) decision. Re-filing under the SAME client stays a no-op (ON CONFLICT DO NOTHING)."""
    doc = await _generate(session_factory, firm)
    assert await _links(session_factory, doc.uid) == []  # not filed under anyone yet
    # Rendered first: assigning a still-QUEUED document deliberately files no link (the worker does
    # it when the render succeeds), so this test would otherwise be asserting about that rule
    # instead of the re-file one it is about.
    await _as_rendered(session_factory, doc.uid)

    async def _assign(contact_id: int) -> None:
        async with session_factory() as session:
            await docs.update_document(
                doc.uid,
                docs.UpdateDocument(intended_contact_id=contact_id),
                _principal(),
                session,
            )

    await _assign(firm["contact_id"])
    assert await _links(session_factory, doc.uid) == [(firm["contact_id"], "subject")]

    await _assign(firm["contact_id"])  # idempotent: no duplicate-key error, no second row
    assert await _links(session_factory, doc.uid) == [(firm["contact_id"], "subject")]

    await _assign(firm["no_email_contact_id"])
    assert sorted(await _links(session_factory, doc.uid)) == sorted(
        [(firm["contact_id"], "subject"), (firm["no_email_contact_id"], "subject")]
    )


@pytest.mark.asyncio
async def test_a_filed_document_does_not_report_as_sent(session_factory, firm) -> None:
    """THE POINT OF THE SEPARATE ROLE. A subject link must not be mistaken for delivery: filing
    leaves ``sent_to_client`` false, ``sent_at`` null and ``recipients`` empty — the signals
    ``_SELECT`` computes by filtering ``role = 'recipient'``. Auto-linking as a recipient would have
    reported every filed document as already sent, which is the one thing this must not do."""
    doc = await _generate(session_factory, firm, client_contact_id=firm["contact_id"])
    async with session_factory() as session:
        listed = await docs.list_documents(
            contact_id=firm["contact_id"], principal=_principal(), session=session
        )
    filed = next(d for d in listed if d.uid == doc.uid)
    assert filed.client == "Bo Client"  # it IS filed under them (and so visible to them)
    assert filed.sent_to_client is False  # but it has NOT been sent
    assert filed.sent_at is None
    assert filed.recipients is None


@pytest.mark.asyncio
async def test_sending_a_filed_document_still_records_a_real_send(session_factory, firm) -> None:
    """The two roles coexist on one primary key (document_id, contact_id, role), so filing never
    consumes the send: sending a document already filed under that client adds the 'recipient' row
    and the sent/recipients signals flip. Filing then sending must read as a send."""
    doc = await _generate(session_factory, firm, client_contact_id=firm["contact_id"])
    await _as_rendered(session_factory, doc.uid, firm["contact_id"])
    async with session_factory() as session:
        await docs.send_document(
            uid=doc.uid,
            body=docs.SendDocument(contact_ids=[firm["contact_id"]]),
            principal=_principal(),
            session=session,
        )
    assert sorted(await _links(session_factory, doc.uid)) == sorted(
        [(firm["contact_id"], "recipient"), (firm["contact_id"], "subject")]
    )
    async with session_factory() as session:
        listed = await docs.list_documents(
            contact_id=firm["contact_id"], principal=_principal(), session=session
        )
    sent = next(d for d in listed if d.uid == doc.uid)
    assert sent.sent_to_client is True
    assert sent.sent_at is not None
    assert sent.recipients == "Bo Client"


# The five `notify` tests stood here. Removed with the feature: `POST /documents/{uid}/notify` told
# a client that a document filed under them was there, and a document REQUEST already asks a client
# to act on one AND records whether they did. The behaviours these pinned — the filed-client check,
# the send permission, the deleted-document refusal, the unreachable report — went with the route.
# Their two helpers went too — `_rendered` (advanced a document to `generated`) and `_notified`
# (read `document.notified` rows). Both were left behind, called by nothing, and the second queried
# an audit action no code path writes any more.


# ── the download gate (permission audit L1) ──────────────────────────────────


@asynccontextmanager
async def _download_session(
    db: dict[str, str], firm: dict[str, Any], *, portal: str, permissions: list[str]
) -> AsyncIterator[Any]:
    """An authenticated RLS session with an explicit portal claim — the axis the download gate
    branches on. `portal='client'` is what `auth.portal()` reads back; staff sessions carry the
    platform portal exactly as resolve_claims mints them."""
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    claims = {
        "sub": firm["wos"],
        "role": "authenticated" if portal == "client" else "adviser",
        "permissions": permissions,
        "user_uuid": firm["user_uuid"],
        "tenant_id": firm["tenant_id"],
        "tenant_ids": [firm["tenant_id"]],
        "portal": portal,
        "contact_ids": [firm["contact_id"]] if portal == "client" else [],
        "account_ids": [],
        "role_ids": [],
    }
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"),
            {"c": json.dumps(claims)},
        )
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_staff_download_requires_documents_read(db, firm) -> None:
    """/files is gated on documents:read, so the per-document `/url` + `/content` routes must be
    too — otherwise a zero-permission staff login walks in through the side door. The refusal
    fires BEFORE the document lookup: holding no permission means not even learning whether the
    uid exists."""
    bare = Principal(user_id=firm["wos"], organisation_id="x", role="adviser", permissions=[])
    async with _download_session(db, firm, portal="platform", permissions=[]) as session:
        for call in (docs.document_url, docs.document_content):
            with pytest.raises(HTTPException) as exc:
                await call(
                    "doc_whatever",
                    background_tasks=BackgroundTasks(),
                    principal=bare,
                    session=session,
                )
            assert exc.value.status_code == 403
            assert "documents:read" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_staff_with_documents_read_pass_the_download_gate(db, firm) -> None:
    """The same routes open for documents:read staff — an unknown uid now 404s, which proves the
    gate passed and the lookup ran."""
    reader = Principal(
        user_id=firm["wos"], organisation_id="x", role="adviser", permissions=["documents:read"]
    )
    async with _download_session(
        db, firm, portal="platform", permissions=["documents:read"]
    ) as session:
        for call in (docs.document_url, docs.document_content):
            with pytest.raises(HTTPException) as exc:
                await call(
                    "doc_nonexistent",
                    background_tasks=BackgroundTasks(),
                    principal=reader,
                    session=session,
                )
            assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_clients_keep_rls_only_document_downloads(db, firm) -> None:
    """A client principal carries NO permissions at all, and must still reach their own documents
    — RLS is their gate (they see only what document_contacts links to them). The gate must not
    403 them: an unknown uid 404s, exactly as it did before L1."""
    client = Principal(
        user_id=firm["wos"], organisation_id="x", role="authenticated", permissions=[]
    )
    async with _download_session(db, firm, portal="client", permissions=[]) as session:
        for call in (docs.document_url, docs.document_content):
            with pytest.raises(HTTPException) as exc:
                await call(
                    "doc_nonexistent",
                    background_tasks=BackgroundTasks(),
                    principal=client,
                    session=session,
                )
            assert exc.value.status_code == 404


def _insert_doc(engine: Engine, tenant_id: int, document_type: str, metadata: dict) -> str:
    uid = f"ddoc_{uuid.uuid4().hex[:10]}"
    with engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO documents.documents "
                "  (uid, tenant_id, document_type, source, name, status, metadata) "
                "VALUES (:u, :t, :dt, 'generated', 'Declaration', 'generated', "
                "  CAST(:m AS jsonb))"
            ),
            {"u": uid, "t": tenant_id, "dt": document_type, "m": json.dumps(metadata)},
        )
        c.commit()
    return uid


def _view_count(engine: Engine, uid: str) -> int:
    with engine.connect() as c:
        return c.execute(
            text(
                "SELECT count(*) FROM documents.audit_log a "
                "JOIN documents.documents d ON d.id = a.document_id "
                "WHERE d.uid = :uid AND a.action = 'document.viewed'"
            ),
            {"uid": uid},
        ).scalar_one()


async def _content(db, firm, uid: str, tasks: BackgroundTasks, **kw: Any) -> Response:
    perms = ["documents:read", "contacts:read_all"]
    reader = Principal(user_id=firm["wos"], organisation_id="x", role="adviser", permissions=perms)
    async with _download_session(db, firm, portal="platform", permissions=perms) as session:
        return await docs.document_content(
            uid, background_tasks=tasks, principal=reader, session=session, **kw
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("preview,cache", [(True, "private, max-age=300"), (False, None)])
async def test_a_terms_declaration_serves_sanitised_html_preview_skips_the_view(
    db, firm, super_engine, preview: bool, cache: str | None
) -> None:
    """No file: /content serves the terms HTML inline, sanitised and CSP-sandboxed, preview or not.
    A preview records no view and caches briefly; a normal fetch is the viewer path and does."""
    uid = _insert_doc(
        super_engine,
        firm["tenant_id"],
        "terms",
        {"content": "<p>I agree</p><script>alert(1)</script>", "audience": "client"},
    )
    tasks = BackgroundTasks()
    res = await _content(db, firm, uid, tasks, preview=preview)
    assert res.status_code == 200
    assert res.media_type == "text/html; charset=utf-8"
    assert res.headers["content-disposition"].startswith("inline")
    assert res.headers["content-security-policy"] == "sandbox"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers.get("cache-control") == cache
    body = bytes(res.body).decode()
    assert "I agree" in body
    assert "<script" not in body
    if preview:
        assert tasks.tasks == []
    else:
        assert [t.func for t in tasks.tasks] == [docs.record_document_action]
        assert tasks.tasks[0].kwargs["event_type"] == docs.DOCUMENT_VIEW_EVENT
    assert _view_count(super_engine, uid) == (0 if preview else 1)


@pytest.mark.asyncio
async def test_a_preview_of_an_undrawable_file_skips_the_bytes(
    db, firm, super_engine, monkeypatch
) -> None:
    """A DOCX cannot be drawn in a card, so a preview answers 204 without reading the store."""
    uid = _insert_doc(super_engine, firm["tenant_id"], "review", {})
    with super_engine.connect() as c:
        c.execute(
            text(
                "WITH f AS (INSERT INTO documents.files "
                "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                "  VALUES (:t, 'b', :k, :ct, 1, 'x') RETURNING id) "
                "UPDATE documents.documents SET original_file_id = (SELECT id FROM f) "
                "WHERE uid = :u"
            ),
            {
                "t": firm["tenant_id"],
                "k": f"k/{uid}",
                "ct": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "u": uid,
            },
        )
        c.commit()

    async def _never(*_a: object, **_k: object) -> None:
        raise AssertionError("a preview must not fetch an undrawable file")

    monkeypatch.setattr(storage, "get_document", _never)
    tasks = BackgroundTasks()
    res = await _content(db, firm, uid, tasks, preview=True)
    assert res.status_code == 204
    assert tasks.tasks == []
    assert _view_count(super_engine, uid) == 0


@pytest.mark.asyncio
async def test_a_document_with_neither_file_nor_content_still_404s(db, firm, super_engine) -> None:
    uid = _insert_doc(super_engine, firm["tenant_id"], "review", {})
    with pytest.raises(HTTPException) as exc:
        await _content(db, firm, uid, BackgroundTasks())
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------------------------
# `GET /documents/requests` — two response shapes, and why the counts are the point.
#
# THE DEFECT. The requests page fetched every request the firm had ever raised, split them into
# three tabs in the browser and labelled each tab with its own array's length. That grows without
# bound: the whole request history ships to draw a page of twelve rows.
#
# Paging it is not enough on its own. The tabs read "Outstanding (12)" and there is an "overdue"
# headline above them — all derived from the loaded rows. Send the page a window and every one of
# those numbers silently becomes a statement about the WINDOW: "Outstanding (100)" on a queue of
# 300, and an overdue figure that shrinks as you page. A specific wrong number stated confidently
# is worse than a slow page, so the counts are computed server-side across the whole queue.
#
# `limit` chooses the shape — absent ⇒ the bare list every existing caller reads (the client
# portal, the client record's message rail), which keeps this a widening rather than a break.
# ---------------------------------------------------------------------------------------------


async def _raise(session, contact_id: int, title: str, due: str | None = None):
    return await docs.create_request(
        body=docs.CreateRequest(
            contact_id=contact_id,
            message=title,
            due_date=due,
            items=[docs.RequestItemIn(action="upload", title=title)],
        ),
        principal=_principal(),
        session=session,
    )


@pytest.mark.asyncio
async def test_without_limit_the_shape_is_the_bare_list(session_factory, firm) -> None:
    # The contract every existing caller reads. If this ever returns an envelope, the client portal
    # and the record's message rail both render nothing — with no error to notice.
    async with session_factory() as session:
        await _raise(session, firm["contact_id"], "Passport")
        out = await docs.list_requests(session=session)
    assert isinstance(out, list)
    assert [r.items[0].title for r in out] == ["Passport"]


@pytest.mark.asyncio
async def test_with_limit_the_shape_is_an_envelope(session_factory, firm) -> None:
    async with session_factory() as session:
        for n in range(3):
            await _raise(session, firm["contact_id"], f"Doc {n}")
        out = await docs.list_requests(limit=2, session=session)
    assert isinstance(out, dict)
    assert len(out["items"]) == 2
    assert out["total"] == 3


@pytest.mark.asyncio
async def test_the_counts_describe_the_QUEUE_not_the_window(session_factory, firm) -> None:
    # The assertion the whole change exists for. Five outstanding, a window of two: the tab must
    # still say five, because that is what the label claims.
    async with session_factory() as session:
        for n in range(5):
            await _raise(session, firm["contact_id"], f"Doc {n}")
        out = await docs.list_requests(limit=2, session=session)
    assert len(out["items"]) == 2
    assert out["counts"]["pending"] == 5
    assert out["counts"]["fulfilled"] == 0
    assert out["counts"]["cancelled"] == 0


def _put_on_book(
    engine: Engine, tenant_id: int, contact_id: int, adviser_id: str, role: str = "adviser"
) -> None:
    """Seed a `crm.client_advisers` row ELEVATED. The request session runs as `authenticated`,
    which holds no INSERT on that table at all — the app assigns an adviser through
    `contacts.assign_adviser`, so a test writing it directly has to do so as the owner.

    ``role`` defaults to the adviser slot; a second row on the same contact (0245: only one
    ``adviser`` per contact) must pass another role."""
    with engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :c, :a, :r)"
            ),
            {"t": tenant_id, "c": contact_id, "a": adviser_id, "r": role},
        )
        c.commit()


@pytest.mark.asyncio
async def test_adviser_me_narrows_to_the_callers_own_book(
    session_factory, firm, super_engine
) -> None:
    """The dashboard's "Action required" queue answers "what needs ME", and the tasks beside it are
    already `assignee=me`. A firm-wide request list next to them is somebody else's work, which is
    what trains an adviser to ignore the queue — so requests narrow to the clients on the caller's
    own book (`crm.client_advisers`), the mirror of the tasks filter."""
    _put_on_book(super_engine, firm["tenant_id"], firm["contact_id"], firm["user_uuid"])
    async with session_factory() as session:
        await _raise(session, firm["contact_id"], "Mine to chase")
        await _raise(session, firm["no_email_contact_id"], "Somebody else's")

        everyones = await docs.list_requests(session=session)
        mine = await docs.list_requests(adviser="me", session=session)

    # The firm-wide queue is unchanged — the requests PAGE is meant to show the whole book.
    assert {r.items[0].title for r in everyones} == {"Mine to chase", "Somebody else's"}
    assert [r.items[0].title for r in mine] == ["Mine to chase"]


@pytest.mark.asyncio
async def test_adviser_me_counts_a_two_adviser_client_once(
    session_factory, firm, super_engine
) -> None:
    # EXISTS, not a join: a client with two staff on their record must not double their
    # requests in the queue — a duplicated row reads as two things to chase.
    tag = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        second = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, email, workos_user_id, first_name, last_name, "
                    "  enabled) VALUES (:uid, :e, :w, 'Bea', 'Second', true) RETURNING id"
                ),
                {"uid": f"dusr_{tag}", "e": f"bea_{tag}@d.test", "w": f"dwos_{tag}"},
            ).scalar_one()
        )
        c.commit()
    # Only one `adviser` row per contact (0245); the second is a support role.
    for adviser_id, role in ((firm["user_uuid"], "adviser"), (second, "support")):
        _put_on_book(super_engine, firm["tenant_id"], firm["contact_id"], adviser_id, role)
    async with session_factory() as session:
        await _raise(session, firm["contact_id"], "Only once")
        mine = await docs.list_requests(adviser="me", session=session)
    assert [r.items[0].title for r in mine] == ["Only once"]


@pytest.mark.asyncio
async def test_an_unreadable_adviser_filter_is_refused_not_ignored(session_factory) -> None:
    # A filter nobody can read silently returning EVERYTHING is how a scoped surface stops being
    # trusted: the queue would quietly widen to the firm with nothing on screen to say so.
    async with session_factory() as session:
        with pytest.raises(HTTPException) as raised:
            await docs.list_requests(adviser="someone-else", session=session)
    assert raised.value.status_code == 422


@pytest.mark.asyncio
async def test_the_counts_ignore_the_status_filter(session_factory, firm) -> None:
    # Each tab needs the OTHER tabs' sizes to label them, so narrowing to one status must not
    # zero the rest — otherwise opening "Outstanding" makes the other two tabs read "(0)".
    async with session_factory() as session:
        keep = await _raise(session, firm["contact_id"], "Stays open")
        gone = await _raise(session, firm["contact_id"], "Cancelled one")
        await session.execute(
            text("UPDATE documents.requests SET status = 'cancelled' WHERE uid = :u"),
            {"u": gone.uid},
        )
        out = await docs.list_requests(status_filter="pending", limit=50, session=session)
    assert [r.uid for r in out["items"]] == [keep.uid]
    assert out["total"] == 1, "total follows the status filter — it is what the caller pages"
    assert out["counts"] == {"pending": 1, "fulfilled": 0, "cancelled": 1}


@pytest.mark.asyncio
async def test_overdue_counts_the_whole_queue(session_factory, firm) -> None:
    # The headline number. Derived from a window it would only ever describe the window, and it
    # would shrink as somebody paged — which reads as work disappearing.
    async with session_factory() as session:
        await _raise(session, firm["contact_id"], "Late one", due="2020-01-01")
        await _raise(session, firm["contact_id"], "Also late", due="2020-01-02")
        await _raise(session, firm["contact_id"], "Not due yet", due="2099-01-01")
        await _raise(session, firm["contact_id"], "No date at all")
        out = await docs.list_requests(limit=1, session=session)
    assert len(out["items"]) == 1
    assert out["overdue"] == 2


@pytest.mark.asyncio
async def test_an_unknown_status_is_named_rather_than_returning_nothing(
    session_factory, firm
) -> None:
    # An empty list is indistinguishable from "this firm has no outstanding requests", so a typo
    # has to be a 422 that says what the valid values are.
    from fastapi import HTTPException

    async with session_factory() as session:
        with pytest.raises(HTTPException) as raised:
            await docs.list_requests(status_filter="outstanding", limit=10, session=session)
    assert raised.value.status_code == 422
    assert "pending" in str(raised.value.detail)


@pytest.mark.asyncio
async def test_an_individual_is_searchable_and_named_by_their_own_name(
    session_factory, firm, super_engine: Engine
) -> None:
    """`crm.contacts.name` is NULLABLE — an individual's name lives in `first_name`/`surname`.

    Reading the bare column names nobody: the page falls back to "Client #42" and a search for a
    real person finds nothing, which reads as "this client has no requests" rather than as a broken
    search. It is the same trap that made the documents Client column render "-" for the whole
    library, and it is why the projection and the search share one composed expression.
    """
    # Through the privileged engine: the request session is RLS-bound and holds no write on
    # `crm.contacts`, which is correct — a documents endpoint has no business editing a client.
    with super_engine.begin() as c:
        c.execute(
            text(
                "UPDATE crm.contacts SET name = NULL, first_name = 'Simple', "
                "surname = 'Accumulation' WHERE id = :c"
            ),
            {"c": firm["contact_id"]},
        )

    async with session_factory() as session:
        await _raise(session, firm["contact_id"], "Passport scan")

        listed = await docs.list_requests(session=session)
        assert listed[0].contact_name == "Simple Accumulation"

        found = await docs.list_requests(q="accumulation", limit=50, session=session)
        assert found["counts"]["pending"] == 1
        assert [r.contact_name for r in found["items"]] == ["Simple Accumulation"]


@pytest.mark.asyncio
async def test_search_narrows_the_counts_too(session_factory, firm) -> None:
    # A search is part of "which queue am I looking at", so the tab sizes have to follow it —
    # otherwise searching leaves the tabs claiming the size of the unsearched book.
    async with session_factory() as session:
        await _raise(session, firm["contact_id"], "Passport scan")
        await _raise(session, firm["contact_id"], "Bank statement")
        out = await docs.list_requests(q="passport", limit=50, session=session)
    assert out["counts"]["pending"] == 1
    assert [r.items[0].title for r in out["items"]] == ["Passport scan"]


def test_an_empty_dataset_is_not_reported_as_a_failure() -> None:
    """The worker's advisory on a document that GENERATED must not read as a crash.

    "Dataset X returned no rows" is written onto `documents.error` for a document that rendered
    perfectly — the client simply has none of that thing. With no entry in `_ERROR_KINDS` it fell
    through to `unknown`, whose summary is "Rendering failed unexpectedly — ask support to check
    the log." That is worse than silence: it sends the reader to support for an answer their own
    row already contained, about a document that is fine.
    """
    mapped = docs.classify_render_error("Dataset 'book_contributions' returned no rows.")
    assert mapped is not None
    assert mapped.kind == "empty"
    # It must not use the language of failure, and it must point at the DATA rather than the
    # template — the template is usually correct in this case.
    assert "failed" not in mapped.summary.lower()
    assert "no data" in mapped.summary.lower()


def test_a_dataset_code_that_names_nothing_says_to_re_bind() -> None:
    """Distinct from `data`: the dataset is not unreadable, it does not exist.

    The fix is in the editor (re-bind the table), so it must not be described as a read failure
    that a retry might clear.
    """
    for raw in (
        "No dataset with code 'book_fees' in this firm.",
        "Dataset 'x' uses 'book_gone', which this version of the platform no longer provides.",
    ):
        mapped = docs.classify_render_error(raw)
        assert mapped is not None, raw
        assert mapped.kind == "missing_dataset", raw

    assert "re-bind" in docs._ERROR_SUMMARY["missing_dataset"]


def test_the_specific_phrases_win_over_the_exception_families() -> None:
    """Order in `_ERROR_KINDS` is load-bearing: first match wins.

    A message can legitimately contain both a specific phrase and a family needle, and the specific
    one is the useful answer. Asserting the ordering directly, because a later insertion into the
    tuple would silently reclassify these.
    """
    kinds = [kind for kind, _ in docs._ERROR_KINDS]
    assert kinds.index("empty") < kinds.index("data")
    assert kinds.index("missing_dataset") < kinds.index("data")


def test_every_kind_has_a_summary() -> None:
    """A kind with no summary would KeyError at read time, on the error path — the worst place."""
    for kind, _ in docs._ERROR_KINDS:
        assert kind in docs._ERROR_SUMMARY, kind
    assert "unknown" in docs._ERROR_SUMMARY
