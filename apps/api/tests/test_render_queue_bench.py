"""Load/benchmark tests for the render queue — "if 500 were generated at once, would it cope?"

Bulk-enqueues a batch of queued documents + render jobs and drains them with several concurrent
worker loops (the real ``worker.handle_once`` claim/render/persist path against a real Postgres),
with the PDF renderer and object storage mocked so the queue mechanics — SKIP LOCKED claiming,
exactly-once completion, retry/backoff and dead-lettering — are what's measured, not WeasyPrint CPU.

Batch size is tunable: ``RENDER_BENCH_DOCS`` (default 500). The throughput floor asserted is
deliberately conservative (it exists to catch pathological regressions, not to be a race); the
measured rate is printed for eyeballing.

The 500-document case is ``pytest.mark.bench`` and is not part of ``api:test`` (or pull-request
CI). The smaller retry/dead-letter case stays in the default run: that one is a correctness
check, and sixty rows is enough to show claims interleaving. Run the load test with
``uv run pytest -m bench``.
"""

import asyncio
import json
import os
import re
import time
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import storage, worker

BENCH_DOCS = int(os.environ.get("RENDER_BENCH_DOCS", "500"))
WORKERS = 4
# Regression floor, not a target — mocked renders through testcontainers Postgres go far faster.
MIN_DOCS_PER_SECOND = 5.0

RENDER_SCHEMA = {
    "html": "<div class='page'><h1>{{ document_name }}</h1></div>",
    "css": ".page{padding:0}",
    "pageSize": {"width": "210mm", "height": "297mm"},
    "gjs_project": {},
}


@pytest.fixture
def bench_seed(super_engine: Engine) -> dict[str, object]:
    """A tenant + staff user + published template version to hang the batch off."""
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
                    "name": "BenchFirm",
                },
            ).scalar_one()
        )
        staff_user = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, email, workos_user_id, first_name, last_name, "
                    "  enabled) VALUES (:uid, :email, :wos, 'Bea', 'Bench', true) RETURNING id"
                ),
                {"uid": f"busr_{s}", "email": f"bench_{s}@b.test", "wos": f"bwos_{s}"},
            ).scalar_one()
        )
        tid = c.execute(
            text(
                "INSERT INTO documents.templates "
                "  (uid, tenant_id, code, name, document_type, created_by) "
                "VALUES (:uid, :t, :code, 'Bench', 'illustration', CAST(:by AS uuid)) RETURNING id"
            ),
            {"uid": f"btpl_{s}", "t": tenant, "code": f"bench_{s}", "by": staff_user},
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
                "uid": f"btplv_{s}",
                "t": tenant,
                "tid": tid,
                "schema": json.dumps(RENDER_SCHEMA),
                "by": staff_user,
            },
        ).scalar_one()
        c.commit()
    return {"tenant_id": tenant, "version_id": vid, "run": s}


@pytest_asyncio.fixture
async def worker_maker(db: dict[str, str]):
    """The worker's DB access (migrator = a service_role member), shared by the drain loops."""
    engine = create_async_engine(db["migrator"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _bulk_enqueue(super_engine: Engine, seed: dict[str, object], n: int) -> None:
    """Insert n queued documents + their render jobs in two statements (the enqueue endpoint's
    rows, minus the per-request HTTP overhead — this benchmarks the queue, not FastAPI)."""
    with super_engine.connect() as c:
        c.execute(text("DELETE FROM documents.render_jobs"))  # isolate from other tests' leftovers
        c.execute(
            text(
                "WITH docs AS ("
                "  INSERT INTO documents.documents "
                "    (uid, tenant_id, template_version_id, document_type, source, name, status) "
                "  SELECT 'bench_' || :run || '_' || g, :t, :vid, 'illustration', 'generated', "
                "         'Bench ' || :run || ' ' || g, 'queued' "
                "  FROM generate_series(1, :n) g RETURNING id"
                ") "
                "INSERT INTO documents.render_jobs (tenant_id, document_id) "
                "SELECT :t, id FROM docs"
            ),
            {"run": seed["run"], "t": seed["tenant_id"], "vid": seed["version_id"], "n": n},
        )
        c.commit()


async def _open_jobs(maker: async_sessionmaker[AsyncSession]) -> int:
    async with maker() as session:
        return (
            await session.execute(
                text(
                    "SELECT count(*) FROM documents.render_jobs "
                    "WHERE status IN ('queued', 'processing')"
                )
            )
        ).scalar_one()


async def _drain(maker: async_sessionmaker[AsyncSession], deadline_s: float) -> None:
    """One worker loop: process until the whole queue is settled (done/failed) or the deadline.

    ``handle_once`` returning False isn't proof the queue is finished — another worker may be
    mid-render, or a zero-backoff retry may be about to requeue — so idle loops re-check the open
    count before exiting (mirrors the real poll loop, minus the 2s sleep)."""
    start = time.monotonic()
    while time.monotonic() - start < deadline_s:
        if await worker.handle_once(maker):
            continue
        if await _open_jobs(maker) == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("queue did not drain within the deadline")


def _bench_counts(super_engine: Engine, run: str) -> dict[str, int]:
    with super_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT d.status::text AS doc_status, rj.status AS job_status, rj.attempts "
                "FROM documents.render_jobs rj "
                "JOIN documents.documents d ON d.id = rj.document_id "
                "WHERE d.uid LIKE 'bench_' || :run || '_%'"
            ),
            {"run": run},
        ).all()
    return {
        "total": len(rows),
        "generated": sum(1 for r in rows if r.doc_status == "generated"),
        "doc_failed": sum(1 for r in rows if r.doc_status == "failed"),
        "job_done": sum(1 for r in rows if r.job_status == "done"),
        "job_failed": sum(1 for r in rows if r.job_status == "failed"),
        "max_attempts_hit": sum(1 for r in rows if r.attempts >= 3),
    }


@pytest.mark.bench
@pytest.mark.asyncio
async def test_queue_drains_bulk_batch_concurrently(
    bench_seed, worker_maker, super_engine, monkeypatch
) -> None:
    """A batch of BENCH_DOCS queued documents drains to completion under concurrent workers:
    every document generated, every job done, every job rendered EXACTLY once (SKIP LOCKED gives no
    duplicate work), and throughput above the (deliberately low) regression floor."""
    renders = 0

    class _Counting:
        async def to_pdf(self, html, css="", branding_css="", page_size=None):
            nonlocal renders
            renders += 1
            await asyncio.sleep(0.001)  # a whiff of latency so claims genuinely interleave
            return b"%PDF-1.4 bench", "cafebabe"

    async def _fake_put(key, data, content_type, **_kw):
        return None

    monkeypatch.setattr(worker, "get_renderer", lambda name: _Counting())
    monkeypatch.setattr(storage, "put_document", _fake_put)

    _bulk_enqueue(super_engine, bench_seed, BENCH_DOCS)

    started = time.monotonic()
    await asyncio.gather(*[_drain(worker_maker, deadline_s=180) for _ in range(WORKERS)])
    elapsed = time.monotonic() - started

    counts = _bench_counts(super_engine, str(bench_seed["run"]))
    rate = BENCH_DOCS / elapsed if elapsed > 0 else float("inf")
    print(
        f"\n[bench] {BENCH_DOCS} docs, {WORKERS} workers: {elapsed:.2f}s "
        f"({rate:.0f} docs/s), {renders} renders"
    )
    assert counts["total"] == BENCH_DOCS
    assert counts["generated"] == BENCH_DOCS
    assert counts["job_done"] == BENCH_DOCS
    assert counts["doc_failed"] == 0 and counts["job_failed"] == 0
    assert renders == BENCH_DOCS  # exactly once — no duplicate claims under concurrency
    assert rate >= MIN_DOCS_PER_SECOND, f"throughput regressed: {rate:.1f} docs/s"


@pytest.mark.asyncio
async def test_queue_retries_and_dead_letters_under_load(
    bench_seed, worker_maker, super_engine, monkeypatch
) -> None:
    """A batch with failures mixed in still settles correctly under concurrent workers: flaky
    renders (fail once, then succeed) retry to completion, permanently-broken ones dead-letter
    after max_attempts, and neither disturbs the healthy majority."""
    n, flaky, dead = 60, 6, 4
    run = str(bench_seed["run"])
    # Deterministic per-document failure injection, keyed off the rendered document name.
    flaky_names = {f"Bench {run} {i}" for i in range(1, flaky + 1)}
    dead_names = {f"Bench {run} {i}" for i in range(flaky + 1, flaky + dead + 1)}
    failed_once: set[str] = set()
    renders = 0

    class _Faulty:
        async def to_pdf(self, html, css="", branding_css="", page_size=None):
            nonlocal renders
            renders += 1
            m = re.search(r"<h1>(.*?)</h1>", html)
            name = m.group(1) if m else ""
            if name in dead_names:
                raise RuntimeError("permanently broken template")
            if name in flaky_names and name not in failed_once:
                failed_once.add(name)
                raise RuntimeError("transient render blip")
            return b"%PDF-1.4 bench", "cafebabe"

    async def _fake_put(key, data, content_type, **_kw):
        return None

    monkeypatch.setattr(worker, "get_renderer", lambda name: _Faulty())
    monkeypatch.setattr(storage, "put_document", _fake_put)
    # Zero backoff so retried jobs are claimable immediately (30s waits would stall the test).
    monkeypatch.setattr(worker, "RETRY_BACKOFF_SECONDS", 0)

    _bulk_enqueue(super_engine, bench_seed, n)
    await asyncio.gather(*[_drain(worker_maker, deadline_s=120) for _ in range(WORKERS)])

    counts = _bench_counts(super_engine, run)
    assert counts["total"] == n
    assert counts["generated"] == n - dead
    assert counts["job_done"] == n - dead
    assert counts["doc_failed"] == dead and counts["job_failed"] == dead
    assert counts["max_attempts_hit"] == dead  # dead-lettered only after exhausting attempts
    # healthy once-only + flaky twice + dead 3 attempts each
    assert renders == (n - flaky - dead) + flaky * 2 + dead * 3
