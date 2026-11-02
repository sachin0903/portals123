"""Liveness, readiness, the metrics exposition and the render worker's heartbeat.

The point of every test here is the same: an operator must learn about a broken dependency from
a probe, not from a user. So each probe's *honest* answer is pinned — a static "ok" while the
database is unreachable is the bug these tests exist to prevent.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app import ratelimit, worker
from app.config import settings
from app.logging import error_counter
from app.main import app
from app.routers import health

API_ROOT = Path(__file__).resolve().parent.parent  # apps/api

# Captured before any test patches them, so the "restore the real thing" tests get the real one.
REAL_STORAGE_PROBE = health._probe_documents_storage
REAL_QUEUE_STATS = health._queue_stats
REAL_PIPELINES_PROBE = health._probe_pipelines


@pytest.fixture(autouse=True)
def _clean_queue_stats_cache() -> Any:
    """The aggregates are cached process-wide; no test may inherit another's snapshot.

    The rate limiter is reset for the same reason: the `abuse` probe reads its recent-exceedance
    state, which is process-global, so without this a readiness assertion here would depend on
    whether tests/test_ratelimit.py happened to run first.
    """
    health.reset_queue_stats_cache()
    ratelimit.limiter.reset()
    yield
    health.reset_queue_stats_cache()
    ratelimit.limiter.reset()


QUEUES_IDLE: dict[str, Any] = {
    "render_jobs": {"depth": {"done": 2}, "oldest_queued_seconds": 0.0},
    "outbox": {"depth": {"delivered": 5}, "oldest_queued_seconds": 0.0},
}


@pytest.fixture
def healthy_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every dependency reachable and configured — the baseline each test perturbs.

    ``app_stage`` is pinned here because the email probe now reports the non-production send guard
    too (``email.sending_disabled``): a developer whose ``.env.local`` says ``APP_STAGE=dev`` would
    otherwise see a *degraded* baseline locally and *ok* in CI, for reasons having nothing to do
    with the test. The stage-guard cases pin the stage themselves."""
    monkeypatch.setattr(health, "_probe_database", _ok("database"))
    monkeypatch.setattr(health, "_probe_documents_storage", _ok("documents_storage"))
    monkeypatch.setattr(health, "_queue_stats", _stats(QUEUES_IDLE))
    # The provider daemons are out of process and unconfigured in a test environment, which is a
    # legitimately degraded state — see the pipeline cases below, which pin it deliberately.
    monkeypatch.setattr(health, "_probe_pipelines", _ok("pipelines"))
    monkeypatch.setattr(settings, "workos_client_id", "client_test")
    monkeypatch.setattr(settings, "resend_api_key", "re_test")
    monkeypatch.setattr(settings, "app_stage", "production")


def _ok(name: str) -> Any:
    async def probe() -> health.Probe:
        return health.Probe(name=name, status="ok")

    return probe


def _down(name: str, detail: str) -> Any:
    async def probe() -> health.Probe:
        return health.Probe(name=name, status="down", detail=detail)

    return probe


def _stats(value: dict[str, Any]) -> Any:
    async def stats() -> dict[str, Any]:
        return value

    return stats


async def _get(path: str, **kwargs: Any) -> Any:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, **kwargs)


# ---- liveness ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_liveness_is_static_and_probes_nothing() -> None:
    """Deliberately dependency-free: an orchestrator kills what fails liveness, and a DB blip
    must not become an API outage. Readiness is where the probing lives."""
    resp = await _get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_only_liveness_is_in_the_openapi_schema() -> None:
    """The ops endpoints must stay OUT of the schema, and this is the test that says why.

    Everything schema'd here lands in the generated `packages/api-client`, whose drift gate
    (`.github/workflows/pr.yml` → `client-drift`) fails CI until someone regenerates it. A route
    only compose, the load balancer and Prometheus ever call has no business in a typed client for
    the portals, so /health/ready and /metrics carry include_in_schema=False.
    """
    schema = app.openapi()
    assert "/health" in schema["paths"]
    assert "/health/ready" not in schema["paths"]
    assert "/metrics" not in schema["paths"]
    components = schema.get("components", {}).get("schemas", {})
    assert "Readiness" not in components
    assert "Probe" not in components


# ---- readiness --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readiness_is_ok_when_everything_is_reachable(healthy_deps: None) -> None:
    resp = await _get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert {c["name"] for c in body["checks"]} == {
        "database",
        "documents_storage",
        "workos",
        "email",
        "queues",
        "abuse",
        "pipelines",
    }


@pytest.mark.asyncio
async def test_readiness_fails_when_the_database_is_unreachable(
    healthy_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression this whole endpoint exists for: /health used to answer "ok" with a dead
    database, so compose's depends_on: service_healthy gated the portals on nothing."""
    monkeypatch.setattr(health, "_probe_database", _down("database", "OperationalError: refused"))
    resp = await _get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "down"
    database = next(c for c in body["checks"] if c["name"] == "database")
    assert database["status"] == "down"
    assert "refused" in database["detail"]


@pytest.mark.asyncio
async def test_readiness_fails_without_a_workos_client_id(
    healthy_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "workos_client_id", "")
    resp = await _get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "down"


@pytest.mark.asyncio
async def test_unconfigured_email_degrades_and_says_so(
    healthy_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank RESEND_API_KEY silently turns off invites, password resets and task notices. It
    must not read as healthy — but it also must not fail readiness, since it is a legitimate
    local/dev state."""
    monkeypatch.setattr(settings, "resend_api_key", "")
    resp = await _get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    email = next(c for c in body["checks"] if c["name"] == "email")
    assert email["status"] == "disabled"
    assert "RESEND_API_KEY" in email["detail"]


@pytest.mark.asyncio
async def test_a_suppressing_stage_guard_degrades_and_names_the_reason(
    healthy_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second way to send nothing: a non-production stage with an empty EMAIL_ALLOWLIST (see
    tests/test_email_send_guard.py). A staging box holding a real Resend key and mailing nobody must
    not report `email: ok` — that is the harder failure to diagnose of the two, because everything
    looks configured. Not `down`: suppression is the state we WANT there."""
    monkeypatch.setattr(settings, "email_nonprod_guard", True)
    monkeypatch.setattr(settings, "app_stage", "staging")
    monkeypatch.setattr(settings, "email_allowlist", [])
    resp = await _get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    email = next(c for c in body["checks"] if c["name"] == "email")
    assert email["status"] == "disabled"
    assert "EMAIL_ALLOWLIST" in email["detail"]


@pytest.mark.asyncio
async def test_an_allowlisted_stage_reports_email_ok(
    healthy_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured allowlist is not "disabled" — those recipients really do get mail, so readiness
    must not sit at degraded for every dev/staging deployment that has set the guard up properly."""
    monkeypatch.setattr(settings, "email_nonprod_guard", True)
    monkeypatch.setattr(settings, "app_stage", "staging")
    monkeypatch.setattr(settings, "email_allowlist", ["@grapheneplatforms.com"])
    body = (await _get("/health/ready")).json()
    assert body["status"] == "ok"
    assert next(c for c in body["checks"] if c["name"] == "email")["status"] == "ok"


@pytest.mark.asyncio
async def test_unconfigured_document_storage_degrades(
    healthy_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "s3_documents_bucket", "")
    monkeypatch.setattr(health, "_probe_documents_storage", REAL_STORAGE_PROBE)
    resp = await _get("/health/ready")
    assert resp.status_code == 200
    storage_check = next(c for c in resp.json()["checks"] if c["name"] == "documents_storage")
    assert storage_check["status"] == "disabled"
    assert "S3_DOCUMENTS_BUCKET" in storage_check["detail"]


@pytest.mark.asyncio
async def test_a_stalled_queue_reports_the_worker_is_not_draining(
    healthy_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API can't see the worker's container, so a backlog is how a wedged worker surfaces."""
    monkeypatch.setattr(
        health,
        "_queue_stats",
        _stats(
            {
                "render_jobs": {"depth": {"queued": 4}, "oldest_queued_seconds": 3600.0},
                "outbox": {"depth": {"dead_letter": 2}, "oldest_queued_seconds": 0.0},
            }
        ),
    )
    resp = await _get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    queues = next(c for c in body["checks"] if c["name"] == "queues")
    assert queues["status"] == "degraded"
    assert "not draining" in queues["detail"]
    assert "2 failed" in queues["detail"]


@pytest.mark.asyncio
async def test_a_probe_failure_does_not_leak_infrastructure_to_an_anonymous_caller(
    healthy_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/health/ready needs no credential, and a psycopg OperationalError string carries the
    database host, port and role name. Outside dev the caller gets the exception CLASS; the full
    text goes to the log, where the operator diagnosing it already is."""
    monkeypatch.setattr(settings, "app_stage", "production")

    async def _boom() -> health.Probe:
        exc = OSError(
            'connection to server at "db.internal.example" (10.1.2.3), port 5432 failed: '
            'FATAL: password authentication failed for user "app"'
        )
        return health.Probe(name="database", status="down", detail=health._probe_detail(exc))

    monkeypatch.setattr(health, "_probe_database", _boom)
    resp = await _get("/health/ready")
    assert resp.status_code == 503
    detail = next(c for c in resp.json()["checks"] if c["name"] == "database")["detail"]
    assert detail.startswith("OSError")
    for secret in ("db.internal.example", "10.1.2.3", "5432", 'user "app"'):
        assert secret not in detail


@pytest.mark.asyncio
async def test_a_probe_failure_is_verbose_in_dev(
    healthy_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "app_stage", "dev")
    assert "refused at db.local" in health._probe_detail(OSError("refused at db.local"))


# ---- the queue aggregates ---------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def scalar_one(self) -> Any:
        return self._scalar


class _FakeSession:
    """Records the SQL the queue probe issues, so the *shape* of the query is testable without a
    database (the cost of that shape is the finding: two forever-growing tables)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.executed: list[tuple[str, Any]] = []
        self.fail = fail

    async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
        sql = str(statement)
        self.executed.append((sql, params))
        if self.fail:
            raise RuntimeError("relation does not exist")
        if "count(*)" in sql:
            return _FakeResult(rows=[{"status": "queued", "n": 2}])
        return _FakeResult(scalar=None)


def _patch_session(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
    @contextlib.asynccontextmanager
    async def _txn() -> AsyncIterator[_FakeSession]:
        yield session

    monkeypatch.setattr(health, "system_transaction", _txn)


@pytest.mark.asyncio
async def test_the_queue_probe_counts_only_live_statuses_and_caps_its_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal rows ('done', 'delivered') are retained forever, so `GROUP BY status` over the
    whole table is an ever-growing sequential scan on a 30-second probe — and a rising count of
    finished rows is a useless gauge. Only the live statuses are counted, under a statement
    timeout, and the empty ones are zero-filled so an alert rule's series never vanishes."""
    session = _FakeSession()
    _patch_session(monkeypatch, session)
    stats = await health._read_queue_stats()

    sql = " ".join(s for s, _ in session.executed)
    assert "statement_timeout" in session.executed[0][0]
    assert "'done'" not in sql and "'delivered'" not in sql
    params = [p for _, p in session.executed if p]
    assert {"statuses": ["queued", "processing", "failed"]} in params
    assert {"statuses": ["queued", "dead_letter"]} in params
    assert stats["render_jobs"]["depth"] == {"queued": 2, "processing": 0, "failed": 0}
    assert stats["outbox"]["depth"] == {"queued": 2, "dead_letter": 0}
    assert stats["render_jobs"]["oldest_queued_seconds"] == 0.0


def _migration_sql() -> str:
    files = sorted((API_ROOT / "migrations" / "sql").glob("*.sql"))
    assert files, "no migration SQL found — this test would silently pass"
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


@pytest.mark.parametrize(("name", "table", "statuses"), health._QUEUES)
def test_the_queue_probe_queries_columns_and_statuses_that_actually_exist(
    name: str, table: str, statuses: tuple[str, ...]
) -> None:
    """``_read_queue_stats`` builds its SQL from ``_QUEUES``: interpolated table names, a literal
    ``status='queued'``, ``created_at``, and a status vocabulary it filters on. All of it is a
    STRING, so a renamed column or a retired status is not a type error and not a test failure —
    the probe just returns ``{}`` for ever and every queue gauge silently disappears, which is
    exactly how a wedged worker becomes invisible again.

    So assert the contract against the migrations themselves: table present, both columns present,
    every status in ``_QUEUES`` still a legal value of the CHECK, and nothing later renaming them.
    This is the check that fires in CI on the migration that breaks it, with no database.
    """
    sql = _migration_sql()
    schema, relation = table.split(".")
    create = re.search(
        rf"CREATE TABLE (?:IF NOT EXISTS\s+)?{schema}\.{relation}\s*\((.*?)\n\);",
        sql,
        re.DOTALL | re.IGNORECASE,
    )
    assert create, f"{table} is queried by the {name} queue probe but never created"
    body = create.group(1)
    assert re.search(r"\bstatus\b", body), f"{table} has no `status` column"
    assert re.search(r"\bcreated_at\b", body), (
        f"{table} has no `created_at` column — oldest_queued_seconds would always be 0"
    )
    check = re.search(r"status\s+IN\s*\(([^)]*)\)", body, re.IGNORECASE)
    assert check, f"{table}.status has no CHECK constraint to compare the probe against"
    allowed = set(re.findall(r"'([^']+)'", check.group(1)))
    missing = set(statuses) - allowed
    assert not missing, (
        f"health._QUEUES counts {sorted(missing)} on {table}, but the migration's CHECK only "
        f"allows {sorted(allowed)} — those series are permanently zero"
    )
    # 'queued' is hard-coded in the oldest-row query, separately from the parametrised list.
    assert "queued" in allowed, f"{table} has no 'queued' status; the oldest-row query is dead SQL"
    for rename in (
        rf"ALTER TABLE\s+{schema}\.{relation}\s+RENAME\s+COLUMN\s+status",
        rf"ALTER TABLE\s+{schema}\.{relation}\s+RENAME\s+COLUMN\s+created_at",
        rf"ALTER TABLE\s+{schema}\.{relation}\s+RENAME\s+TO",
        rf"ALTER TABLE\s+{schema}\.{relation}\s+DROP\s+COLUMN\s+(?:status|created_at)",
    ):
        assert not re.search(rename, sql, re.IGNORECASE), (
            f"a later migration changes {table} in a way the queue probe's SQL does not follow"
        )


@pytest.mark.asyncio
async def test_the_queue_probe_binds_its_status_list_as_one_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`status = ANY(:statuses)` with a Python list is the only bit of the query the driver has to
    adapt (psycopg turns the list into a Postgres array). Pin the shape: one bound parameter
    carrying a list, not N interpolated literals — an f-string of statuses would be injection-shaped
    and would also break the moment a status contains a quote."""
    session = _FakeSession()
    _patch_session(monkeypatch, session)
    await health._read_queue_stats()
    aggregates = [(s, p) for s, p in session.executed if "count(*)" in s]
    assert len(aggregates) == len(health._QUEUES)
    for statement, params in aggregates:
        assert "status = ANY(:statuses)" in statement
        assert isinstance(params, dict) and isinstance(params["statuses"], list)
        assert all(isinstance(s, str) for s in params["statuses"])


@pytest.mark.asyncio
async def test_the_queue_probe_is_cached_so_an_anonymous_flood_cannot_amplify_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both readers are unauthenticated. Without a TTL the database cost of /health/ready is set
    by whoever is calling it."""
    session = _FakeSession()
    _patch_session(monkeypatch, session)
    first = await health._queue_stats()
    for _ in range(20):
        assert await health._queue_stats() == first
    one_pass = len(session.executed)
    assert one_pass <= 6, f"one pass should be timeout + 2 queries per table, got {one_pass}"
    health.reset_queue_stats_cache()
    assert await health._queue_stats() == first
    assert len(session.executed) == one_pass * 2


@pytest.mark.asyncio
async def test_a_failing_queue_probe_is_swallowed_cached_and_degrades_readiness(
    healthy_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A metric must never take an endpoint down — and a database that is down must not be
    hammered by the probe reporting it."""
    session = _FakeSession(fail=True)
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(health, "_queue_stats", REAL_QUEUE_STATS)  # the real, cached one
    resp = await _get("/health/ready")
    assert resp.status_code == 200
    queues = next(c for c in resp.json()["checks"] if c["name"] == "queues")
    assert queues["status"] == "degraded"
    attempts = len(session.executed)
    assert attempts > 0, "the probe never reached the database"
    # Not via the endpoint (the readiness answer has its own cache): straight at the stats cache.
    assert await health._queue_stats() == {}
    assert len(session.executed) == attempts, "the failure was not cached"


@pytest.mark.asyncio
async def test_the_readiness_answer_is_cached_briefly(
    healthy_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/health/ready is unauthenticated and each fresh call opens a database connection and does a
    bucket HEAD. Without a short TTL, an anonymous loop over it hammers our own dependencies."""
    calls: list[int] = []

    async def _counting() -> health.Probe:
        calls.append(1)
        return health.Probe(name="database", status="ok")

    monkeypatch.setattr(health, "_probe_database", _counting)
    for _ in range(10):
        assert (await _get("/health/ready")).json()["status"] == "ok"
    assert len(calls) == 1
    health.reset_queue_stats_cache()
    assert (await _get("/health/ready")).json()["status"] == "ok"
    assert len(calls) == 2, "the cache never expires — a real state change would be invisible"


# ---- metrics ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_expose_queue_depth_and_oldest_queued_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "metrics_token", "")
    monkeypatch.setattr(settings, "app_stage", "dev")
    monkeypatch.setattr(
        health,
        "_queue_stats",
        _stats(
            {
                "render_jobs": {
                    "depth": {"queued": 3, "failed": 1},
                    "oldest_queued_seconds": 42.5,
                },
                "outbox": {"depth": {"queued": 7}, "oldest_queued_seconds": 9.0},
            }
        ),
    )
    error_counter.counts.clear()
    resp = await _get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert 'graphene_render_jobs_depth{status="queued"} 3.0' in body
    assert 'graphene_render_jobs_depth{status="failed"} 1.0' in body
    assert "graphene_render_jobs_oldest_queued_seconds 42.5" in body
    assert 'graphene_outbox_depth{status="queued"} 7.0' in body
    assert "graphene_outbox_oldest_queued_seconds 9.0" in body
    assert 'graphene_rate_limit_rejected_total{surface="login"}' in body
    assert 'graphene_rate_limit_budget_exceeded_total{surface="forgot-password"}' in body
    assert "graphene_email_configured" in body


@pytest.mark.asyncio
async def test_metrics_count_requests_served_over_an_advisory_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared (advisory) budget deliberately refuses nobody, so it produces NO rejection — this
    counter is the only machine-readable witness that our forgot-password spend is being blown
    through. If it stayed at zero the control would be invisible."""
    monkeypatch.setattr(settings, "metrics_token", "")
    monkeypatch.setattr(settings, "app_stage", "dev")
    monkeypatch.setattr(health, "_queue_stats", _stats(QUEUES_IDLE))
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 1)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 1)
    for who in ("a@example.com", "b@example.com", "c@example.com"):
        keyed = ratelimit.forgot_password_limits(email=who, host="app.firm.com", client_ip=None)
        assert (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, keyed)).allowed
    body = (await _get("/metrics")).text
    assert 'graphene_rate_limit_budget_exceeded_total{surface="forgot-password"} 2.0' in body
    assert 'graphene_rate_limit_rejected_total{surface="forgot-password"} 0.0' in body


@pytest.mark.asyncio
async def test_a_blown_shared_budget_degrades_readiness_and_then_clears(
    healthy_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/metrics needs METRICS_TOKEN set AND something scraping it; readiness needs neither, and the
    deploy smoke test already echoes this body. So the one signal that a shared budget is being
    blown through (nothing is refused, by design) has to reach readiness too — degraded, never
    down, and self-clearing so the deployment does not sit at 'degraded' for ever."""
    assert (await _get("/health/ready")).json()["status"] == "ok"
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 1)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 1)
    clock = _Clock()
    monkeypatch.setattr(ratelimit.limiter, "_clock", clock)
    for who in ("a@example.com", "b@example.com"):
        keyed = ratelimit.forgot_password_limits(email=who, host="app.firm.com", client_ip=None)
        await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, keyed)

    health.reset_queue_stats_cache()  # the readiness answer is TTL-cached
    resp = await _get("/health/ready")
    # 200, not 503: an abuse signal is not a broken dependency. And LIVENESS — the one thing the
    # api container's healthcheck reads — must be untouched, or an abuse burst restarts the API.
    assert resp.status_code == 200
    assert (await _get("/health")).json() == {"status": "ok"}
    body = resp.json()
    abuse = next(c for c in body["checks"] if c["name"] == "abuse")
    assert abuse["status"] == "degraded"
    assert body["status"] == "degraded"
    assert "shared rate-limit budget was exceeded" in abuse["detail"]
    # UNAUTHENTICATED endpoint: aggregate only. No host, no address.
    assert "app.firm.com" not in abuse["detail"] and "example.com" not in abuse["detail"]

    clock.now += ratelimit.HOUR + 1
    health.reset_queue_stats_cache()
    assert (await _get("/health/ready")).json()["status"] == "ok"


class _Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_metrics_require_the_token_when_one_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "metrics_token", "m3trics")
    monkeypatch.setattr(health, "_queue_stats", _stats(QUEUES_IDLE))
    assert (await _get("/metrics")).status_code == 404
    assert (await _get("/metrics", headers={"x-metrics-token": "wrong"})).status_code == 404
    ok = await _get("/metrics", headers={"x-metrics-token": "m3trics"})
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_metrics_fail_closed_outside_dev_when_no_token_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shipped default is a blank METRICS_TOKEN, and the endpoint is unauthenticated: on a
    real deployment that is a world-readable exposition wherever the API is reachable. "The api
    service has no public domain" is a deployment convention, not a control — so refuse."""
    monkeypatch.setattr(settings, "metrics_enabled", True)
    monkeypatch.setattr(settings, "metrics_token", "")
    monkeypatch.setattr(health, "_queue_stats", _stats(QUEUES_IDLE))
    for stage in ("production", "staging"):
        monkeypatch.setattr(settings, "app_stage", stage)
        assert (await _get("/metrics")).status_code == 404, f"open /metrics on {stage}"
    # Dev stays open so a local `curl :8000/metrics` needs no setup.
    monkeypatch.setattr(settings, "app_stage", "dev")
    assert (await _get("/metrics")).status_code == 200


@pytest.mark.asyncio
async def test_metrics_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "metrics_enabled", False)
    monkeypatch.setattr(settings, "app_stage", "dev")
    monkeypatch.setattr(settings, "metrics_token", "m3trics")
    assert (await _get("/metrics", headers={"x-metrics-token": "m3trics"})).status_code == 404


# ---- the render worker's heartbeat ------------------------------------------------------------


def test_heartbeat_is_healthy_once_every_loop_has_beaten(tmp_path: Any) -> None:
    beat = worker.Heartbeat(tmp_path / "hb.json")
    for loop in worker.LOOP_STALL_SECONDS:
        beat.touch(loop)
    healthy, reason = worker.healthcheck(beat.path)
    assert healthy, reason
    assert (
        json.loads(beat.path.read_text(encoding="utf-8"))["loops"].keys()
        == worker.LOOP_STALL_SECONDS.keys()
    )


def test_heartbeat_is_unhealthy_before_a_loop_has_run(tmp_path: Any) -> None:
    beat = worker.Heartbeat(tmp_path / "hb.json")
    beat.touch(worker.RENDER_LOOP)
    healthy, reason = worker.healthcheck(beat.path)
    assert not healthy
    assert "outbox: never ran" in reason


def test_a_wedged_loop_is_unhealthy(tmp_path: Any) -> None:
    """The finding: three loops in one process with no healthcheck, so a wedged outbox looks
    like a happily running container."""
    beat = worker.Heartbeat(tmp_path / "hb.json")
    for loop in worker.LOOP_STALL_SECONDS:
        beat.touch(loop)
    later = max(worker.LOOP_STALL_SECONDS.values()) + 10
    healthy, reason = worker.healthcheck(beat.path, now=beat.stamps[worker.OUTBOX_LOOP] + later)
    assert not healthy
    assert worker.OUTBOX_LOOP in reason


def test_missing_heartbeat_file_is_unhealthy(tmp_path: Any) -> None:
    """A crash before the first iteration (bad env, import error) must fail the healthcheck."""
    healthy, reason = worker.healthcheck(tmp_path / "absent.json")
    assert not healthy
    assert "no readable heartbeat" in reason


def test_healthcheck_cli_exit_code(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "hb.json"
    monkeypatch.setattr(settings, "worker_heartbeat_path", str(path))
    assert worker.main(["--healthcheck"]) == 1
    beat = worker.Heartbeat()
    for loop in worker.LOOP_STALL_SECONDS:
        beat.touch(loop)
    assert beat.path == path
    assert worker.main(["--healthcheck"]) == 0


@pytest.mark.asyncio
async def test_workos_reconcile_is_a_worker_loop_with_its_own_heartbeat(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    async def run_forever(maker: object, *, heartbeat: Any = None) -> None:
        captured["maker"] = maker
        heartbeat()

    monkeypatch.setattr(worker.poller, "run_forever", run_forever)
    maker = object()
    beat = worker.Heartbeat(tmp_path / "workos-heartbeat.json")

    await worker._workos_sync_loop(maker, beat)  # type: ignore[arg-type]

    assert captured["maker"] is maker
    assert worker.WORKOS_SYNC_LOOP in beat.stamps
    assert worker.WORKOS_SYNC_LOOP in worker.LOOP_STALL_SECONDS


@pytest.mark.asyncio
async def test_disabled_workos_reconcile_returns_without_touching_a_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "workos_sync_poll_seconds", 0)
    await worker.poller.run_forever(None)  # type: ignore[arg-type]


# --- the provider pipelines ---------------------------------------------------------------------
# The head of every custodian's data path, and previously absent from readiness entirely: health.py
# contained no `provider`, `lake` or `interlace` token at all. Two of the three custodians shipped
# with no `INTERLACE_*_URL` and no container, so their syncs could never run — and the only way to
# find out was to open the admin platform page and read a badge.


def _pipelines(monkeypatch: pytest.MonkeyPatch, *, up: bool, **urls: str) -> None:
    """The real probe, over a scripted configuration and reachability."""
    monkeypatch.setattr(health, "_probe_pipelines", REAL_PIPELINES_PROBE)
    for provider in ("seccl", "morgan-lloyd", "sei"):
        # The SETTINGS attribute is the provider code with the hyphen substituted — a hyphen is
        # not a legal Python identifier, which is the same reason `provider_schema` substitutes
        # for the warehouse identifier. `service_url` derives it the same way (0131), and this
        # helper's KWARGS are under the same rule, so a caller writes `morgan_lloyd=...`.
        key = provider.replace("-", "_")
        monkeypatch.setattr(settings, f"interlace_{key}_url", urls.get(key, ""))

    async def _reach(_url: str) -> bool:
        return up

    monkeypatch.setattr(health, "_pipeline_up", _reach)


@pytest.mark.asyncio
async def test_an_unconfigured_daemon_degrades_readiness_and_names_the_provider(
    healthy_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unset is NOT a neutral state: `service_url` returns "", the platform page says
    `unconfigured`, and every sync / refresh / rebuild for that custodian answers 503 while its
    pages still render."""
    _pipelines(monkeypatch, up=True)

    body = (await _get("/health/ready")).json()
    probe = next(c for c in body["checks"] if c["name"] == "pipelines")
    assert probe["status"] == "degraded"
    assert "unconfigured" in probe["detail"]
    for provider in ("seccl", "sei", "morgan-lloyd"):
        assert provider in probe["detail"]
    # Degraded, never down: the portals read the BOOK, and a firm's data does not vanish because a
    # daemon is unreachable. What stops is the refresh.
    assert body["status"] == "degraded"


@pytest.mark.asyncio
async def test_an_unreachable_daemon_is_named_separately_from_an_unconfigured_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two different problems with two different fixes: one is a missing env var, the other is a
    container that is down. Reporting them as one state sends an operator to the wrong place."""
    _pipelines(monkeypatch, up=False, seccl="http://seccl-pipeline:8100")

    probe = await health._probe_pipelines()
    assert probe.status == "degraded"
    assert "unreachable: seccl" in (probe.detail or "")
    assert "unconfigured" in (probe.detail or "")


@pytest.mark.asyncio
async def test_all_three_configured_and_up_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _pipelines(
        monkeypatch,
        up=True,
        seccl="http://seccl-pipeline:8100",
        morgan_lloyd="http://morgan-lloyd-pipeline:8102",
        sei="http://sei-pipeline:8103",
    )
    probe = await health._probe_pipelines()
    assert probe.status == "ok"
    assert probe.detail is None
