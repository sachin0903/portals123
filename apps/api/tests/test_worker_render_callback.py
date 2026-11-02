"""End-to-end proof that the render worker closes the flow engine's loop (needs Docker).

A ``generate_document`` step enqueues a render job and reports WAITING. The only thing that can
move it on is ``app.worker`` calling ``orchestrator.on_document_rendered``. That call was MISSING —
``on_document_rendered`` existed, was unit-tested by calling it directly, and had no caller — so in
the running system a journey containing a generated document waited forever. These tests drive the
real path (step → render_jobs row → ``worker.handle_once`` → callback → step advances), which is the
only shape that can catch that class of inertness.

The renderer + object store are stubbed (WeasyPrint's native stack isn't available here, and the
worker deliberately holds no DB connection while rendering); everything else — the claim, the
document write, the audit row and the flow callback — is real SQL against a real Postgres.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import worker
from app.flows import orchestrator
from app.flows.planning import AccountSpec, ApplicationGraph, Participant
from app.render.resolve import DatasetProblem
from tests.conftest import SECCL_PLATFORM_SQL


async def _scalar(sess: AsyncSession, sql: str, **params: Any) -> Any:
    return (await sess.execute(text(sql), params)).scalar_one_or_none()


class _Stubs:
    """A renderer + storage that never touch the network, and record what they were asked for."""

    def __init__(self, *, fail: str | None = None) -> None:
        self.fail = fail
        self.uploaded: list[str] = []
        self.html: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = self

        class _Renderer:
            async def to_pdf(self, html: str, css: str, _hf: str, _page: Any) -> tuple[bytes, str]:
                stub.html.append(html)
                if stub.fail:
                    raise RuntimeError(stub.fail)
                return b"%PDF-1.7 stub", "0" * 64

        async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
            stub.uploaded.append(key)

        monkeypatch.setattr(worker, "get_renderer", lambda _name: _Renderer())
        monkeypatch.setattr(worker.storage, "put_document", _put)


async def _seed_generate_document_step(sess: AsyncSession, *, html: str = "<p>Hi</p>") -> Any:
    """A tenant + application + one owner + a published template, running a one-step flow whose
    single step is a ``generate_document``. Returns the fixture bag."""
    s = uuid.uuid4().hex[:8]
    tenant = int(
        await _scalar(
            sess,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
            u=f"wk_{s}",
            a=f"wk-{s}",
            n=f"WK {s}",
        )
    )
    platform = str(
        await _scalar(
            sess,
            SECCL_PLATFORM_SQL,
            n=f"Seccl {s}",
        )
    )
    app_id = int(
        await _scalar(
            sess,
            "INSERT INTO crm.applications (tenant_id, provider, uid, status) "
            "VALUES (:t, :p, :u, 'draft') RETURNING id",
            t=tenant,
            p=platform,
            u=f"app_{s}",
        )
    )
    contact = int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, email, name) "
            "VALUES (:t, :u, 'individual', :e, :n) RETURNING id",
            t=tenant,
            u=f"c_{s}",
            e=f"c_{s}@t.test",
            n="Ada Client",
        )
    )
    account = int(
        await _scalar(
            sess,
            "INSERT INTO platform.accounts (tenant_id, contact_id, application_id, status, uid) "
            "VALUES (:t, :c, :a, 'draft', :u) RETURNING id",
            t=tenant,
            c=contact,
            a=app_id,
            u=f"acc_{s}",
        )
    )
    await sess.execute(
        text(
            "INSERT INTO crm.account_contacts "
            "  (tenant_id, application_id, account_id, contact_id, role) "
            "VALUES (:t, :a, :acc, :c, CAST('owner' AS crm.account_role))"
        ),
        {"t": tenant, "a": app_id, "acc": account, "c": contact},
    )
    author = await _scalar(
        sess,
        "INSERT INTO auth.users (uid, tenant_id, email) VALUES (:u, :t, :e) RETURNING id",
        u=f"usr_{s}",
        t=tenant,
        e=f"author_{s}@t.test",
    )
    template_uid = f"tpl_{s}"
    tpl = int(
        await _scalar(
            sess,
            "INSERT INTO documents.templates "
            "  (uid, tenant_id, code, name, document_type, created_by) "
            "VALUES (:u, :t, :c, 'Suitability pack', 'suitability', :by) RETURNING id",
            u=template_uid,
            t=tenant,
            c=f"tc_{s}",
            by=author,
        )
    )
    vid = int(
        await _scalar(
            sess,
            "INSERT INTO documents.template_versions "
            "  (uid, tenant_id, template_id, version_number, status, schema, created_by) "
            "VALUES (:u, :t, :tpl, 1, 'published', CAST(:sch AS jsonb), :by) RETURNING id",
            u=f"tv_{s}",
            t=tenant,
            tpl=tpl,
            sch=json.dumps({"html": html, "css": "", "components": []}),
            by=author,
        )
    )
    await sess.execute(
        text("UPDATE documents.templates SET current_published_version_id = :v WHERE id = :i"),
        {"v": vid, "i": tpl},
    )
    definition = {
        "steps": [
            {
                "id": "pack",
                "type": "generate_document",
                "title": "Suitability pack",
                "scope": "application",
                "assignee": "staff",
                "config": {"template_uid": template_uid},
            }
        ],
        "milestones": [],
    }
    flow_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
            "VALUES (:u, :t, :code, 'Onboarding', 'published') RETURNING id",
            u=f"flw_{s}",
            t=tenant,
            code=f"onb_{s}",
        )
    )
    version_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flow_versions "
            "  (uid, tenant_id, flow_id, version, status, definition, published_at) "
            "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id",
            u=f"flv_{s}",
            t=tenant,
            f=flow_id,
            d=json.dumps(definition),
        )
    )
    created = await orchestrator.start_execution(
        sess,
        tenant_id=tenant,
        flow_id=flow_id,
        flow_version_id=version_id,
        definition=definition,
        graph=ApplicationGraph(
            participants=(Participant(contact, "individual", frozenset({"owner"})),),
            accounts=(AccountSpec(account, (contact,)),),
        ),
        application_id=app_id,
        data={},
    )
    step = (
        (
            await sess.execute(
                text(
                    "SELECT cs.id, cs.case_id, cs.status, cs.data FROM flows.case_steps cs "
                    "JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE c.execution_id = :e AND cs.step_key = 'pack'"
                ),
                {"e": int(created["id"])},
            )
        )
        .mappings()
        .one()
    )
    # There is no per-tenant render queue: ``_claim`` takes the oldest ready job in the WHOLE table.
    # Any earlier test that committed a render job it never drained (the flow lifecycle suite does
    # exactly that) would therefore be the job ``handle_once`` picks up — and this test would then
    # assert about a document it never seeded and fail. Clear the queue down to this test's own job;
    # every other row belongs to a test that has already finished asserting.
    await sess.execute(
        text("DELETE FROM documents.render_jobs WHERE document_id <> CAST(:d AS uuid)"),
        {"d": step["data"]["document_id"]},
    )
    return {
        "tenant": tenant,
        "template_uid": template_uid,
        "execution_id": int(created["id"]),
        "step": dict(step),
        "document_id": step["data"]["document_id"],
        "contact": contact,
    }


async def _step_status(sess: AsyncSession, execution_id: int) -> str:
    return str(
        await _scalar(
            sess,
            "SELECT cs.status FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
            "WHERE c.execution_id = :e AND cs.step_key = 'pack'",
            e=execution_id,
        )
    )


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A sessionmaker the worker can use — and that this test COMMITS through.

    Deliberately not the shared ``service_session`` fixture: that holds one open transaction, so a
    render job seeded inside it is invisible to the worker's own connection and ``_claim`` finds
    nothing. Committing is the point — the worker is a separate process in production.
    """
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@asynccontextmanager
async def _svc(maker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """One committed service_role transaction — mirrors how the worker itself works."""
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        yield session


# ==================================================================================================
# the whole path
# ==================================================================================================
@pytest.mark.asyncio
async def test_worker_render_advances_the_waiting_flow_step(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """step → render_jobs row → worker.handle_once → callback → step fulfilled.

    Fails if ``on_document_rendered`` has no caller in the worker, which is exactly how the
    'generate_document waits forever' dead end survived a fix that only tested the callback.
    """
    async with _svc(maker) as sess:
        fx = await _seed_generate_document_step(sess, html="<p>{{ client_name }}</p>")
        assert await _step_status(sess, fx["execution_id"]) == "waiting"
    stubs = _Stubs()
    stubs.install(monkeypatch)

    assert await worker.handle_once(maker) is True

    async with _svc(maker) as sess:
        assert await _step_status(sess, fx["execution_id"]) == "fulfilled", (
            "the render finished but nothing told the engine — the step still waits forever"
        )
        assert (
            await _scalar(
                sess,
                "SELECT status FROM documents.documents WHERE id = CAST(:d AS uuid)",
                d=fx["document_id"],
            )
            == "generated"
        )
        assert (
            await _scalar(
                sess,
                "SELECT status FROM documents.render_jobs WHERE document_id = CAST(:d AS uuid)",
                d=fx["document_id"],
            )
            == "done"
        )
    assert stubs.uploaded, "the PDF must be stored"
    # The bound client context reached the template, not just an empty variable bag.
    assert "Ada Client" in stubs.html[0]


@pytest.mark.asyncio
async def test_a_dead_lettered_render_fails_the_step_rather_than_pinning_it(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _svc(maker) as sess:
        fx = await _seed_generate_document_step(sess)
        # Last attempt: the next failure dead-letters instead of requeuing.
        await sess.execute(
            text(
                "UPDATE documents.render_jobs SET attempts = max_attempts - 1 "
                "WHERE document_id = CAST(:d AS uuid)"
            ),
            {"d": fx["document_id"]},
        )
    _Stubs(fail="chromium exploded").install(monkeypatch)

    assert await worker.handle_once(maker) is True

    async with _svc(maker) as sess:
        assert await _step_status(sess, fx["execution_id"]) == "failed", (
            "a permanently failed render must fail its step, not leave the journey waiting"
        )
        doc = (
            (
                await sess.execute(
                    text(
                        "SELECT status, error FROM documents.documents WHERE id = CAST(:d AS uuid)"
                    ),
                    {"d": fx["document_id"]},
                )
            )
            .mappings()
            .one()
        )
        assert doc["status"] == "failed"
        assert "chromium exploded" in str(doc["error"])
        last = await _scalar(
            sess,
            "SELECT cs.data ->> 'last_event' FROM flows.case_steps cs "
            "JOIN flows.cases c ON c.id = cs.case_id "
            "WHERE c.execution_id = :e AND cs.step_key = 'pack'",
            e=fx["execution_id"],
        )
        assert "render_failed" in str(last)


@pytest.mark.asyncio
async def test_a_transient_render_failure_requeues_and_leaves_the_step_waiting(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The callback must fire on a DEAD LETTER only — a retryable failure keeps the step waiting."""
    async with _svc(maker) as sess:
        fx = await _seed_generate_document_step(sess)
    _Stubs(fail="transient blip").install(monkeypatch)

    assert await worker.handle_once(maker) is True

    async with _svc(maker) as sess:
        assert await _step_status(sess, fx["execution_id"]) == "waiting"
        assert (
            await _scalar(
                sess,
                "SELECT status FROM documents.render_jobs WHERE document_id = CAST(:d AS uuid)",
                d=fx["document_id"],
            )
            == "queued"
        )


# ==================================================================================================
# datasets are never silently blank
# ==================================================================================================
@pytest.mark.asyncio
async def test_a_dataset_that_cannot_be_resolved_fails_the_render(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A template bound to a dataset that doesn't exist used to render a client PDF with silently
    empty tables (the worker called the rows-only ``resolve_datasets``, which logs and shrugs)."""
    async with _svc(maker) as sess:
        fx = await _seed_generate_document_step(
            sess, html="{% for r in holdings %}{{ r.x }}{% endfor %}"
        )
        await sess.execute(
            text("UPDATE documents.templates SET linked_datasets = :d WHERE uid = :u"),
            {"d": ["holdings"], "u": fx["template_uid"]},
        )
        await sess.execute(
            text(
                "UPDATE documents.render_jobs SET attempts = max_attempts - 1 "
                "WHERE document_id = CAST(:d AS uuid)"
            ),
            {"d": fx["document_id"]},
        )
    _Stubs().install(monkeypatch)

    assert await worker.handle_once(maker) is True

    async with _svc(maker) as sess:
        doc = (
            (
                await sess.execute(
                    text(
                        "SELECT status, error FROM documents.documents WHERE id = CAST(:d AS uuid)"
                    ),
                    {"d": fx["document_id"]},
                )
            )
            .mappings()
            .one()
        )
        assert doc["status"] == "failed", "an unresolved dataset must not ship as a blank table"
        assert "holdings" in str(doc["error"])
        assert await _step_status(sess, fx["execution_id"]) == "failed"


@pytest.mark.asyncio
async def test_unsourced_tokens_are_recorded_as_empty_fields(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A merge token with no source renders blank AND is recorded on the document — its note and its
    audit detail — so a silent empty field becomes a visible advisory, not spotted only by eye."""
    async with _svc(maker) as sess:
        fx = await _seed_generate_document_step(
            sess, html="<p>{{ client_name }} owes {{ mystery_fee }}</p>"
        )
    _Stubs().install(monkeypatch)

    assert await worker.handle_once(maker) is True

    async with _svc(maker) as sess:
        note = await _scalar(
            sess,
            "SELECT error FROM documents.documents WHERE id = CAST(:d AS uuid)",
            d=fx["document_id"],
        )
        detail = await _scalar(
            sess,
            "SELECT detail ->> 'empty_fields' FROM documents.audit_log "
            "WHERE document_id = CAST(:d AS uuid) AND action = 'document.generated'",
            d=fx["document_id"],
        )
    assert note is not None and "mystery_fee" in note
    assert detail is not None and "mystery_fee" in detail


def test_advisory_dataset_problems_are_recorded_not_fatal() -> None:
    # empty/truncated are advisory: the rows are genuine, there are just none (or not all) of them.
    note = worker._warning_note(
        [
            DatasetProblem(
                code="holdings", reason="empty", detail="Dataset 'holdings' had no rows."
            ),
            DatasetProblem(code="fees", reason="missing", detail="No dataset 'fees'."),
        ]
    )
    assert note == "Dataset 'holdings' had no rows."
    assert worker._warning_note([]) is None


async def _client_links(sess: AsyncSession, document_id: str) -> list[tuple[str, int]]:
    """Every `document_contacts` row on a document — what the client branch of `documents_read`
    reads, and the whole of a client's permission to see it."""
    rows = (
        await sess.execute(
            text(
                "SELECT role, contact_id FROM documents.document_contacts "
                "WHERE document_id = CAST(:d AS uuid) ORDER BY role"
            ),
            {"d": document_id},
        )
    ).all()
    return [(str(r[0]), int(r[1])) for r in rows]


@pytest.mark.asyncio
async def test_the_client_is_linked_only_once_the_render_has_succeeded(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A queued document is NOT yet the client's to see; a rendered one is.

    The link is the entire permission: the client branch of `documents_read` (0021_documents_scope)
    matches on `contact_id` alone with NO status predicate, and `list_documents`' default
    `status=active` is only `status <> 'archived'`. So a link filed at ENQUEUE time — which both the
    flow step and `documents/service.py::generate` used to do — put a document the renderer had not
    touched into the client's portal, with no adviser having reviewed or sent anything. This asserts
    the ordering, not just the end state, because the end state is identical either way.
    """
    async with _svc(maker) as sess:
        fx = await _seed_generate_document_step(sess)
        assert await _client_links(sess, fx["document_id"]) == [], (
            "a queued document must not be visible to the client yet"
        )
    _Stubs().install(monkeypatch)

    assert await worker.handle_once(maker) is True

    async with _svc(maker) as sess:
        assert await _client_links(sess, fx["document_id"]) == [("subject", int(fx["contact"]))]


@pytest.mark.asyncio
async def test_a_rendered_illustration_is_held_back_until_the_firm_opts_in(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """FB-20261006-04. An illustration rendered straight into the client's portal, reviewed by
    nobody. It is the one type the worker does not file under its client on render — unless the
    firm has switched `illustrations_auto_share` on (0248), in which case it is filed as before.
    The document keeps `intended_contact_id` either way, so staff can share it from the record."""
    async with _svc(maker) as sess:
        fx = await _seed_generate_document_step(sess)
        await sess.execute(
            text(
                "UPDATE documents.documents SET document_type = 'illustration' "
                "WHERE id = CAST(:d AS uuid)"
            ),
            {"d": fx["document_id"]},
        )
    _Stubs().install(monkeypatch)

    assert await worker.handle_once(maker) is True

    async with _svc(maker) as sess:
        assert (
            await _scalar(
                sess,
                "SELECT status FROM documents.documents WHERE id = CAST(:d AS uuid)",
                d=fx["document_id"],
            )
            == "generated"
        )
        assert await _client_links(sess, fx["document_id"]) == []
        assert await _scalar(
            sess,
            "SELECT intended_contact_id FROM documents.documents WHERE id = CAST(:d AS uuid)",
            d=fx["document_id"],
        ) == int(fx["contact"])

        # The firm that wants every illustration in the portal at once says so.
        opted = await _seed_generate_document_step(sess)
        await sess.execute(
            text(
                "UPDATE documents.documents SET document_type = 'illustration' "
                "WHERE id = CAST(:d AS uuid)"
            ),
            {"d": opted["document_id"]},
        )
        await sess.execute(
            text("UPDATE auth.tenants SET illustrations_auto_share = true WHERE id = :t"),
            {"t": opted["tenant"]},
        )

    assert await worker.handle_once(maker) is True

    async with _svc(maker) as sess:
        assert await _client_links(sess, opted["document_id"]) == [
            ("subject", int(opted["contact"]))
        ]


@pytest.mark.asyncio
async def test_a_permanently_failed_render_never_becomes_the_client_s_to_see(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half of the bug that was not a race. A failed document keeps its status for ever, so a
    link filed at enqueue time left it in the client's portal permanently — a document that failed
    to render, described to the client as theirs. Nothing retracts a link, so the only fix is never
    to have filed it."""
    async with _svc(maker) as sess:
        fx = await _seed_generate_document_step(sess)
        await sess.execute(
            text(
                "UPDATE documents.render_jobs SET attempts = max_attempts - 1 "
                "WHERE document_id = CAST(:d AS uuid)"
            ),
            {"d": fx["document_id"]},
        )
    _Stubs(fail="chromium exploded").install(monkeypatch)

    assert await worker.handle_once(maker) is True

    async with _svc(maker) as sess:
        assert (
            await _scalar(
                sess,
                "SELECT status FROM documents.documents WHERE id = CAST(:d AS uuid)",
                d=fx["document_id"],
            )
            == "failed"
        )
        assert await _client_links(sess, fx["document_id"]) == []
