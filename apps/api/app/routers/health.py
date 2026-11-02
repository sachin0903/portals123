"""Liveness, readiness and metrics.

Three deliberately different questions:

* ``GET /health`` — **liveness**. Is this process serving? Static and dependency-free on purpose:
  an orchestrator kills what fails liveness, and killing the API because Postgres blipped turns a
  database incident into an outage.
* ``GET /health/ready`` — **readiness**. Are the dependencies this process needs actually
  reachable (database, document storage, WorkOS config, transactional email)? 503 when a REQUIRED
  dependency is down, so ``depends_on: service_healthy`` and a load balancer gate on something
  real. Optional-but-off dependencies (no Resend key, no documents bucket) report ``disabled``
  and degrade the overall status without failing it — an honest "this feature is off here"
  instead of a silent no-op. It also carries the ``abuse`` probe: readiness is the only operator
  surface that needs no token, so a shared rate-limit budget being blown through is reported here
  as ``degraded`` (see ``_probe_abuse``).
* ``GET /metrics`` — Prometheus text exposition: queue depth + oldest-queued age for the render
  queue and the flow outbox (a wedged worker shows up here first), rate-limit rejections and
  advisory-budget exceedances, error counts. Hand-rolled so no metrics dependency is needed.

**Only ``GET /health`` is in the OpenAPI schema.** ``/health/ready`` and ``/metrics`` are
operator/orchestrator surfaces consumed by compose, the load balancer and Prometheus — never by a
portal — so they carry ``include_in_schema=False``. Two reasons, and the second is the one that
bites: they are not part of the typed client's contract, and every schema'd route/model here lands
in the generated ``packages/api-client``, whose drift gate (``.github/workflows/pr.yml``
``client-drift``) then fails CI until someone regenerates it. ``tests/test_health.py`` pins this.

Both are UNAUTHENTICATED, so neither hands out anything an attacker can use: probe failures report
the exception class (the full text, which carries DB hosts/roles, goes to the log) outside dev, and
``/metrics`` requires ``METRICS_TOKEN`` outside dev. Their one piece of real work — the queue
aggregates — is TTL-cached so a flood cannot amplify it into database load.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Header, HTTPException, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import text

from app import platforms, providers, storage
from app.config import settings
from app.db import engine, system_transaction
from app.email import transport as email
from app.logging import error_counter
from app.ratelimit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# A probe must never be the slow thing: an unreachable dependency has to answer fast enough that
# the healthcheck's own timeout doesn't fire first.
PROBE_TIMEOUT_SECONDS = 3.0
# A queue whose oldest row is older than this reads as "the worker isn't draining".
QUEUE_STALL_SECONDS = 300

# The queue tables, and the statuses worth counting. Deliberately NOT every status: terminal rows
# ('done', 'delivered') are retained forever, so `GROUP BY status` over the whole table is an
# ever-growing sequential scan, and a monotonically rising count of finished rows is a useless
# gauge anyway. Counting only the live statuses keeps the queued slice on the tables' existing
# partial indexes (`render_jobs_claim_idx`, `outbox_due_idx`).
_QUEUES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("render_jobs", "documents.render_jobs", ("queued", "processing", "failed")),
    ("outbox", "flows.outbox", ("queued", "dead_letter")),
)
# Both readers of the queue aggregates are unauthenticated (/health/ready, /metrics), so the DB
# work is TTL-cached: an orchestrator probe every 30s and a scrape every 15s must not multiply,
# and a flood must not turn a health endpoint into a load generator.
QUEUE_STATS_TTL_SECONDS = 15.0
# A probe must never wait on a slow aggregate; timing out reports "degraded", which is honest.
QUEUE_STATS_TIMEOUT_MS = 2000
# The whole readiness answer is cached briefly for the same reason: /health/ready is
# unauthenticated and each call opens a database connection and does a bucket HEAD, so an
# anonymous loop over it would otherwise hammer our own dependencies. Well under the 30s probe
# interval, so an orchestrator still sees a state change promptly.
READINESS_TTL_SECONDS = 5.0

ProbeStatus = Literal["ok", "degraded", "down", "disabled"]


class Health(BaseModel):
    status: str


class Probe(BaseModel):
    name: str
    status: ProbeStatus
    detail: str | None = None


class Readiness(BaseModel):
    status: Literal["ok", "degraded", "down"]
    stage: str
    checks: list[Probe]


@router.get("/health")
async def check() -> Health:
    """LIVENESS — the process is up and serving. Deliberately probes nothing (see the module
    docstring); use ``/health/ready`` to gate traffic."""
    return Health(status="ok")


def _probe_detail(exc: BaseException) -> str:
    """As much of a failure as is safe to hand an UNAUTHENTICATED caller.

    A driver's exception text is an infrastructure disclosure: psycopg's ``OperationalError``
    carries the database host, port and role name, and a botocore error carries the endpoint and
    bucket. ``/health/ready`` needs no credential, so outside dev the response gets the exception
    CLASS and the full text goes to the log — where the operator diagnosing it already is.
    """
    logger.warning(
        "readiness probe failed",
        extra={
            "event": "readiness_probe_failed",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        },
    )
    if settings.app_stage == "dev":
        return f"{type(exc).__name__}: {exc}"
    return f"{type(exc).__name__} — full detail in the API log (event=readiness_probe_failed)"


async def _probe_database() -> Probe:
    """The one hard requirement: no database, no request can be served."""
    try:
        async with engine.connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=PROBE_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 — timeout, auth, network: all the same signal
        return Probe(name="database", status="down", detail=_probe_detail(exc))
    return Probe(name="database", status="ok")


async def _probe_documents_storage() -> Probe:
    """The private documents bucket. Blank config is a real deployment choice (document storage
    off), so that is ``disabled``, not ``down``; a configured-but-unreachable bucket is down."""
    if not storage.documents_configured():
        return Probe(
            name="documents_storage",
            status="disabled",
            detail="S3_DOCUMENTS_BUCKET unset — uploads and document rendering are off",
        )

    def _head() -> None:
        storage._client().head_bucket(Bucket=settings.s3_documents_bucket)  # noqa: SLF001

    try:
        await asyncio.wait_for(asyncio.to_thread(_head), timeout=PROBE_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 — any storage failure is the same signal
        return Probe(name="documents_storage", status="down", detail=_probe_detail(exc))
    return Probe(name="documents_storage", status="ok")


def _probe_workos() -> Probe:
    """Config presence only — token verification 500s without a client id. The JWKS fetch itself
    isn't probed here: it is per-tenant and cached, and we won't call WorkOS every 30 seconds."""
    if not settings.workos_client_id:
        return Probe(
            name="workos", status="down", detail="WORKOS_CLIENT_ID unset — token verification fails"
        )
    return Probe(name="workos", status="ok")


def _probe_email() -> Probe:
    """Resend. Two ways every send returns False — no credential, or dev/staging with an empty
    ``EMAIL_ALLOWLIST`` — and both mean invites, password resets and task notices are
    silently off. Say it out loud rather than letting an operator discover it from a user, and name
    WHICH one: "email: ok" on a staging box that suppresses everything would be the worse lie."""
    reason = email.sending_disabled()
    if reason is not None:
        return Probe(
            name="email",
            status="disabled",
            detail=(
                f"{reason} — invites, password-reset and task-notification emails are NOT sent "
                "(the render worker also skips its notifier scan)"
            ),
        )
    return Probe(name="email", status="ok")


async def _probe_pipelines() -> Probe:
    """The provider pipeline daemons — the head of every custodian's data path.

    CONFIGURATION FIRST, then reachability. An unset `INTERLACE_{PROVIDER}_URL` is not a neutral
    state: `platforms.service_url` returns "", the platform page reports `unconfigured`, and every
    sync / refresh / rebuild for that custodian answers 503 — while its pages still render. Both
    SEI and Morgan Lloyd were in exactly that state, with no container defined for them at all, so
    two of the three custodians could not run in a deployment and nothing said so.

    `degraded`, never `down`: the portals read the BOOK, which is Postgres, and a firm's data does
    not disappear because a daemon is restarting. What stops is the refresh — which matters, and
    is the difference between "this firm has no data" and "nothing has run since Tuesday".
    """
    configured = {p: platforms.service_url(p) for p in providers.PROVIDERS}
    missing = sorted(p for p, url in configured.items() if not url)
    reachable = await asyncio.gather(
        *(_pipeline_up(url) for url in configured.values() if url), return_exceptions=False
    )
    named = [p for p, url in configured.items() if url]
    unreachable = sorted(p for p, up in zip(named, reachable, strict=True) if not up)

    detail: list[str] = []
    if missing:
        detail.append(f"unconfigured: {', '.join(missing)} (INTERLACE_*_URL unset)")
    if unreachable:
        detail.append(f"unreachable: {', '.join(unreachable)}")
    if not detail:
        return Probe(name="pipelines", status="ok")
    return Probe(
        name="pipelines",
        status="degraded",
        detail="; ".join(detail) + " — provider syncs and per-firm refreshes cannot run",
    )


async def _pipeline_up(url: str) -> bool:
    """One daemon's `/health`. Short timeout: a probe must not wait on a slow dependency."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            return (await client.get(f"{url.rstrip('/')}/health")).status_code == 200
    except Exception:  # noqa: BLE001 — an unreachable daemon is the answer, not an error
        return False


async def _read_queue_stats() -> dict[str, Any]:
    """Depth by live status + age of the oldest queued row, for the render queue and the outbox.

    One short service_role transaction, capped by ``statement_timeout`` and restricted to the
    statuses in ``_QUEUES`` (see that constant for why "every status" is the wrong query).
    Returns ``{}`` on any failure (an older schema, a DB blip, the timeout): a metric must never
    be the thing that takes an endpoint down.
    """
    stats: dict[str, Any] = {}
    try:
        async with system_transaction() as session:
            timeout = f"SET LOCAL statement_timeout = '{QUEUE_STATS_TIMEOUT_MS}ms'"
            await session.execute(text(timeout))
            for name, table, statuses in _QUEUES:
                rows = (
                    (
                        await session.execute(
                            text(
                                f"SELECT status, count(*) AS n FROM {table} "  # noqa: S608
                                "WHERE status = ANY(:statuses) GROUP BY status"
                            ),
                            {"statuses": list(statuses)},
                        )
                    )
                    .mappings()
                    .all()
                )
                sql = f"SELECT min(created_at) FROM {table} WHERE status='queued'"  # noqa: S608
                oldest = (await session.execute(text(sql))).scalar_one()
                age = (datetime.now(UTC) - oldest).total_seconds() if oldest else 0.0
                # Zero-filled so a series never disappears from the exposition when a queue is
                # empty — a vanished series and a healthy zero look identical to an alert rule.
                depth = dict.fromkeys(statuses, 0)
                depth.update({str(r["status"]): int(r["n"]) for r in rows})
                stats[name] = {"depth": depth, "oldest_queued_seconds": age}
    except Exception as exc:  # noqa: BLE001 — reported, never raised
        logger.warning(
            "queue stats unavailable", extra={"event": "queue_stats_failed", "error": str(exc)}
        )
        return {}
    return stats


class _TtlCache[T]:
    """One value, recomputed at most once per ``ttl`` seconds, shared by concurrent callers.

    The point is that the *callers* are unauthenticated: without this, the database load and the
    outbound calls an unauthenticated endpoint makes are chosen by whoever is calling it, and a
    trivial loop over /health/ready becomes a load generator against our own dependencies. It
    caches FAILURES too — a dependency that is down must not be hammered by the probe reporting it.
    """

    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._lock = asyncio.Lock()
        self._entry: tuple[float, T] | None = None

    def _fresh(self) -> tuple[float, T] | None:
        entry = self._entry
        if entry is not None and time.monotonic() - entry[0] < self._ttl:
            return entry
        return None

    async def get(self, produce: Callable[[], Awaitable[T]]) -> T:
        entry = self._fresh()
        if entry is not None:
            return entry[1]
        async with self._lock:
            # Re-check: a concurrent caller may have refreshed it while we waited for the lock.
            entry = self._fresh()
            if entry is not None:
                return entry[1]
            value = await produce()
            self._entry = (time.monotonic(), value)
            return value

    def clear(self) -> None:
        self._entry = None


_stats_cache: _TtlCache[dict[str, Any]] = _TtlCache(QUEUE_STATS_TTL_SECONDS)
_readiness_cache: _TtlCache[list[Probe]] = _TtlCache(READINESS_TTL_SECONDS)


def reset_queue_stats_cache() -> None:
    """Drop both caches (tests; and a break-glass for an operator who wants them recomputed)."""
    _stats_cache.clear()
    _readiness_cache.clear()


async def _queue_stats() -> dict[str, Any]:
    """``_read_queue_stats`` behind a ``QUEUE_STATS_TTL_SECONDS`` cache."""
    return await _stats_cache.get(_read_queue_stats)


def _queue_probe(stats: dict[str, Any]) -> Probe:
    """A backlog whose oldest queued row is older than ``QUEUE_STALL_SECONDS`` means the worker is
    not draining — degraded (the API itself is fine), which is what an alert should fire on."""
    if not stats:
        return Probe(name="queues", status="degraded", detail="queue depth could not be read")
    stalled = [
        f"{name}: oldest queued {int(data['oldest_queued_seconds'])}s"
        for name, data in stats.items()
        if data["oldest_queued_seconds"] > QUEUE_STALL_SECONDS
    ]
    dead = [
        f"{name}: {data['depth'].get('dead_letter', 0) + data['depth'].get('failed', 0)} failed"
        for name, data in stats.items()
        if data["depth"].get("dead_letter") or data["depth"].get("failed")
    ]
    if stalled:
        return Probe(
            name="queues",
            status="degraded",
            detail="worker not draining — " + "; ".join(stalled + dead),
        )
    if dead:
        return Probe(
            name="queues", status="degraded", detail="dead-lettered rows — " + "; ".join(dead)
        )
    return Probe(name="queues", status="ok")


def _probe_abuse() -> Probe:
    """Has a SHARED (advisory) rate-limit budget been exceeded in the last hour?

    Shared budgets do not refuse — refusing on a key held in common by every user of a portal is
    a self-inflicted outage (``app/ratelimit.py``) — so nothing else in the deployment notices when
    one is being blown through. ``/metrics`` carries the counter, but that needs ``METRICS_TOKEN``
    set *and* something scraping it; readiness needs neither, and the deploy smoke test already
    echoes this body. Degraded, never down: this is an abuse signal, not a broken dependency, and
    the API healthcheck deliberately gates on ``/health`` so nothing restarts on it.

    Aggregate only — no host, no address, no count of who. It goes quiet on its own once the abuse
    stops (``recent_budget_exceeded``), so it cannot pin the deployment to "degraded" forever.
    """
    recent = limiter.recent_budget_exceeded()
    if not recent:
        return Probe(name="abuse", status="ok")
    surfaces = ", ".join(f"{s}: {n}" for s, n in sorted(recent.items()))
    return Probe(
        name="abuse",
        status="degraded",
        detail=(
            f"a shared rate-limit budget was exceeded in the last hour ({surfaces}) — requests "
            "are still being served; see event=rate_limit_budget_exceeded in the API log and "
            "graphene_rate_limit_budget_exceeded_total"
        ),
    )


async def _run_probes() -> list[Probe]:
    """Every dependency, probed concurrently (the slowest one sets the latency, not their sum)."""
    database, storage_probe, stats, pipelines = await asyncio.gather(
        _probe_database(), _probe_documents_storage(), _queue_stats(), _probe_pipelines()
    )
    # The saved allowlist, so "email: disabled" names the list the admin page set, not only the
    # env default. Outside dev and staging this does not touch the database.
    await email.refresh_allowlist()
    return [
        database,
        storage_probe,
        _probe_workos(),
        _probe_email(),
        _queue_probe(stats),
        _probe_abuse(),
        pipelines,
    ]


@router.get("/health/ready", name="ready", include_in_schema=False)
async def ready(response: Response) -> Readiness:
    """READINESS — probe every dependency; 503 if a required one (database, WorkOS) is down.

    Out of the OpenAPI schema on purpose: compose, the load balancer and the deploy smoke test call
    it, no portal does, and a schema'd route here drags ``Readiness``/``Probe`` into the generated
    client and fails the drift gate (module docstring).
    """
    checks = await _readiness_cache.get(_run_probes)
    required_down = any(c.status == "down" for c in checks if c.name in {"database", "workos"})
    overall: Literal["ok", "degraded", "down"] = "ok"
    if required_down:
        overall = "down"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif any(c.status in {"down", "degraded", "disabled"} for c in checks):
        overall = "degraded"
    return Readiness(status=overall, stage=settings.app_stage, checks=checks)


def _metric(name: str, kind: str, help_text: str, samples: list[tuple[str, float]]) -> list[str]:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
    lines += [f"{name}{labels} {value}" for labels, value in samples]
    return lines


@router.get("/metrics", include_in_schema=False, response_class=PlainTextResponse)
async def metrics(x_metrics_token: str = Header(default="")) -> PlainTextResponse:
    """Prometheus text exposition. Aggregate counts only — no tenant or user data.

    Guarded by ``x-metrics-token``, checked in constant time; a miss is a 404, not a 401, so the
    path is never advertised. ``METRICS_ENABLED=false`` removes it entirely.

    Outside dev an UNSET ``METRICS_TOKEN`` fails CLOSED. The endpoint is unauthenticated, so a
    blank token means "world-readable wherever the API is reachable", and "the api service has no
    public domain" is a deployment convention, not a control. Dev keeps it open so a local
    ``curl :8000/metrics`` needs no setup.
    """
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not settings.metrics_enabled:
        raise not_found
    if settings.metrics_token:
        if not hmac.compare_digest(x_metrics_token, settings.metrics_token):
            raise not_found
    elif settings.app_stage != "dev":
        logger.warning(
            "GET /metrics refused: METRICS_TOKEN is unset, so the exposition would be "
            "unauthenticated on a %s deployment. Set METRICS_TOKEN (and send it as "
            "x-metrics-token), or set METRICS_ENABLED=false to stop asking.",
            settings.app_stage,
            extra={"event": "metrics_untokenised"},
        )
        raise not_found

    stats = await _queue_stats()
    await email.refresh_allowlist()
    lines: list[str] = []
    for queue, data in stats.items():
        lines += _metric(
            f"graphene_{queue}_depth",
            "gauge",
            f"Rows in {queue} by status.",
            [(f'{{status="{s}"}}', float(n)) for s, n in sorted(data["depth"].items())],
        )
        lines += _metric(
            f"graphene_{queue}_oldest_queued_seconds",
            "gauge",
            f"Age of the oldest queued row in {queue}.",
            [("", data["oldest_queued_seconds"])],
        )
    lines += _metric(
        "graphene_rate_limit_rejected_total",
        "counter",
        "Requests rejected by the rate limiter since start, by surface.",
        [(f'{{surface="{s}"}}', float(n)) for s, n in sorted(limiter.rejections().items())],
    )
    lines += _metric(
        "graphene_rate_limit_budget_exceeded_total",
        "counter",
        # The series to alert on: a shared-key budget (e.g. forgot-password per portal host) is
        # ADVISORY — it is exceeded rather than enforced, precisely so that abuse never refuses
        # innocent third parties. Nothing else notices it.
        "Requests served OVER a shared (advisory) rate-limit budget since start, by surface.",
        [(f'{{surface="{s}"}}', float(n)) for s, n in sorted(limiter.budget_exceeded().items())],
    )
    lines += _metric(
        "graphene_errors_total",
        "counter",
        "ERROR-or-worse log records since start, by logger.",
        [(f'{{logger="{name}"}}', float(n)) for name, n in sorted(error_counter.counts.items())],
    )
    lines += _metric(
        "graphene_email_configured",
        "gauge",
        "1 when transactional email (Resend) is configured, 0 when every send is a no-op.",
        [("", 1.0 if email.configured() else 0.0)],
    )
    lines += _metric(
        "graphene_email_suppressed",
        "gauge",
        # Configured and suppressed are different states and both matter: this is 1 on a staging
        # box that holds a real Resend key and mails nobody, which is the state we WANT there and
        # the one that otherwise looks identical to healthy.
        "1 when dev or staging is suppressing every send (see EMAIL_ALLOWLIST).",
        [("", 1.0 if email.stage_guard_suppresses_all() else 0.0)],
    )
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
