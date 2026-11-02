"""Structured logging, request correlation and the error-tracker facade.

Three concerns, one small module, because they are the same concern in practice: when something
fails, an operator must be able to (a) find the log line, (b) tie it to the request the user
complained about, and (c) be told without waiting for the complaint.

* **Structured logs.** ``configure_logging`` installs a single stdout handler emitting one JSON
  object per line (``log_format=text`` for a human console; blank resolves by ``APP_STAGE``).
  Anything passed as ``extra=`` lands as top-level fields, so log shippers can filter on them.
* **Correlation.** ``correlation_id`` is a ContextVar stamped on every record. The API's
  ``CorrelationIdMiddleware`` seeds it from the caller's ``x-correlation-id`` (so the portal's id
  survives into API logs) and echoes it back; the worker seeds its own per job/outbox row.
  The JSON line includes it. The text console does not.
* **Trace.** Every record also carries ``trace_id`` (32 lowercase hex) and ``span_id`` (16
  lowercase hex) from the active span, or ``"-"`` when there is none. The JSON line includes
  both so a shipper can join a line to its trace. The text console omits them, and the
  correlation id, because a local terminal is not where that join happens and nothing parses
  that format. The access line is written after that span has ended, so the middleware
  stashes the two ids at ``http.response.start`` and the access logger reads the stash.
* **Error tracking.** ``report_error`` is the ONE call site pattern for a swallowed exception: it
  logs at ERROR with structured context *and* forwards to Sentry when ``SENTRY_DSN`` is set and
  the optional ``sentry-sdk`` package is installed. Off by default, on by env, never silent.

``ERROR``+ records are also counted per logger for the ``/metrics`` exposition, so "the error rate
went up" is visible without an external tracker at all.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from opentelemetry.trace import format_span_id, format_trace_id, get_current_span

from app.config import settings

logger = logging.getLogger(__name__)

CORRELATION_HEADER = "x-correlation-id"

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# The server span's ids, captured at http.response.start. Uvicorn writes the access line after
# that span has ended, so get_current_span() is invalid by then and would stamp "-".
_request_trace: ContextVar[tuple[str, str] | None] = ContextVar("request_trace", default=None)

# Fields the stdlib puts on every LogRecord — anything else is caller-supplied `extra=` and is
# promoted to a top-level JSON field.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
        "correlation_id",
        "trace_id",
        "span_id",
        "service",
    }
)


def new_correlation_id() -> str:
    """A fresh id. Short (hex32) — it travels in a header and in every log line."""
    return uuid.uuid4().hex


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def set_correlation_id(value: str | None) -> str:
    """Bind a correlation id for this context (generating one when absent). Returns the id.

    Caller-supplied ids are length-capped and stripped of anything that isn't safe in a header
    or a log field — it's attacker-controlled input that ends up in an operator's log search.
    """
    cleaned = "".join(c for c in (value or "") if c.isalnum() or c in "-_")[:64]
    resolved = cleaned or new_correlation_id()
    _correlation_id.set(resolved)
    return resolved


def _active_trace() -> tuple[str, str]:
    """The current span as ``(trace_id, span_id)``, or ``("-", "-")`` when it is not valid.

    ``format_trace_id(0)`` is thirty-two zeroes. Grafana's log-to-trace link matches
    ``[0-9a-f]{32}`` and would join those to a trace that does not exist, so an invalid
    context is ``"-"`` rather than the formatted zero.
    """
    ctx = get_current_span().get_span_context()
    if not ctx.is_valid:
        return "-", "-"
    return format_trace_id(ctx.trace_id), format_span_id(ctx.span_id)


class _ContextFilter(logging.Filter):
    """Stamp the correlation id, the trace and the service on every record (both formatters
    read them, and a filter is the only hook that runs for records emitted anywhere)."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = current_correlation_id() or "-"
        record.service = self.service
        # The access line is emitted after the server span ends. Its ids were stashed while
        # the span was still current; reading get_current_span() here would stamp "-".
        if record.name == "uvicorn.access":
            stashed = _request_trace.get()
            record.trace_id, record.span_id = stashed if stashed is not None else ("-", "-")
        else:
            record.trace_id, record.span_id = _active_trace()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: fixed keys first, then whatever the call site passed as
    ``extra=``. Exceptions carry ``error``/``error_type``/``stack``."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": getattr(record, "service", ""),
            "correlation_id": getattr(record, "correlation_id", "-"),
            "trace_id": getattr(record, "trace_id", "-"),
            "span_id": getattr(record, "span_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            payload["error_type"] = type(exc).__name__
            payload["error"] = str(exc)
            payload["stack"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = (
                value if isinstance(value, str | int | float | bool | type(None)) else repr(value)
            )
        return json.dumps(payload, default=str)


class ErrorCounter(logging.Handler):
    """Counts ERROR+ records per logger, for the /metrics exposition. Cheap: an in-process
    Counter, reset on restart — enough to alert on a rate, not an audit trail."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.counts: Counter[str] = Counter()

    def emit(self, record: logging.LogRecord) -> None:
        self.counts[record.name] += 1


error_counter = ErrorCounter()

_configured = False


def resolved_log_format() -> str:
    """'json' unless explicitly asked for text (or we're on a dev box, where a console wins)."""
    explicit = (settings.log_format or "").strip().lower()
    if explicit in {"json", "text"}:
        return explicit
    return "text" if settings.app_stage == "dev" else "json"


def configure_logging(service: str) -> None:
    """Install the single stdout handler for this process. Idempotent — safe from both the API
    lifespan and the worker's ``__main__``."""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    if resolved_log_format() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(_ContextFilter(service))
    root = logging.getLogger()
    root.handlers = [handler, error_counter]
    root.setLevel(getattr(logging, (settings.log_level or "INFO").upper(), logging.INFO))
    # Uvicorn installs its own handlers (propagate off) after import and before the lifespan,
    # so this has to run here — at configuration time — not at import. Otherwise the access
    # line stays `INFO: 172.18.0.6 ... "GET /path" 200` and never reaches JsonFormatter.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # httpx logs every outbound request/response verbatim at INFO ("HTTP Request: GET <url> ...
    # <status>"), full query string included — for the Intelliflo client (pkgs/intelliflo) that
    # is the "Link to IO" search's OData filter: name, date of birth, NI number, `quote`-encoded
    # but trivially reversible (a space is `%20`). That line has no handler of its own, so it
    # propagates straight to the root handler above and lands in the same JSON stream every other
    # log reaches — a caller-redacted log line in client.py does not stop httpx's OWN line from
    # saying the same thing. WARNING keeps real connection/transport failures visible without the
    # per-request access line; httpcore (httpx's transport) logs at the same levels for the same
    # reason.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    _configured = True


# ---- error tracking ------------------------------------------------------------------------

_tracker: Any | None = None


def init_error_tracking(service: str) -> bool:
    """Wire the error tracker if configured. Returns True when it is actually on.

    ``sentry-sdk`` is an OPTIONAL dependency: with a DSN set but the package absent we log an
    error (loudly — the operator believes they have alerting) and carry on with log-only
    reporting."""
    global _tracker
    if not settings.sentry_dsn:
        return False
    try:
        # Optional dependency: not in apps/api/pyproject.toml, so mypy can't see it either.
        import sentry_sdk  # type: ignore[import-not-found]
    except ImportError:
        logger.error(
            "SENTRY_DSN is set but the sentry-sdk package is not installed — error tracking is "
            "OFF (errors still reach the structured log). Add sentry-sdk to apps/api "
            "dependencies to enable it.",
            extra={"event": "error_tracking_unavailable"},
        )
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_stage,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # Never ship request bodies/headers or user identifiers to a third party by default:
        # this is a regulated multi-tenant product, and the correlation id is enough to join.
        send_default_pii=False,
    )
    sentry_sdk.set_tag("service", service)
    _tracker = sentry_sdk
    logger.info("error tracking enabled", extra={"event": "error_tracking_enabled"})
    return True


def init_tracing(service_name: str) -> None:
    """Export traces when a collector endpoint is configured. Otherwise a no-op.

    Lives next to error tracking because they are the same operational concern, and the
    implementation is in ``app/tracing.py`` so this module stays the logging facade.
    ``service_name`` is written to ``OTEL_SERVICE_NAME``, which wins over anything in
    ``OTEL_RESOURCE_ATTRIBUTES``.
    """
    from app.tracing import init_tracing as _init

    _init(service_name)


def shutdown_tracing() -> None:
    """Flush spans and stop the exporter. Safe when tracing was never started."""
    from app.tracing import shutdown_tracing as _shutdown

    _shutdown()


def error_tracking_enabled() -> bool:
    return _tracker is not None


def report_error(
    message: str,
    /,
    *,
    exc: BaseException | None = None,
    log: logging.Logger | None = None,
    **context: Any,
) -> None:
    """Report a failure we are deliberately not raising: structured ERROR log + error tracker.

    Replaces the bare ``except Exception: logger.exception(...)`` idiom at every swallow site, so
    an outage reaches an operator instead of only container stdout.

    ``message`` is POSITIONAL-ONLY: ``**context`` is caller-chosen and a context key called
    ``message`` (or ``name``, ``module``, …) would otherwise be a ``TypeError``/``KeyError``,
    turning a reported failure into a second, unreported one. Reserved keys are dropped from the
    log record instead — they still reach the error tracker's scope."""
    target = log or logger
    extra = {k: v for k, v in context.items() if k not in _RESERVED}
    target.error(message, exc_info=exc, extra=extra)
    if _tracker is None:
        return
    try:
        # sentry-sdk 2.x renamed push_scope -> new_scope; support both.
        scope_cm = getattr(_tracker, "new_scope", None) or _tracker.push_scope
        with scope_cm() as scope:
            scope.set_tag("correlation_id", current_correlation_id() or "-")
            for key, value in context.items():
                scope.set_extra(key, value)
            if exc is not None:
                _tracker.capture_exception(exc)
            else:
                _tracker.capture_message(message, level="error")
    except Exception:  # noqa: BLE001 — the tracker must never break the caller's error path
        target.warning("error tracker capture failed", extra={"event": "error_tracking_failed"})


# ---- ASGI middleware -----------------------------------------------------------------------

Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class CorrelationIdMiddleware:
    """Bind a correlation id for the request and echo it back.

    Pure ASGI (not BaseHTTPMiddleware) so it costs nothing and cannot swallow streaming
    responses. The portal sends ``x-correlation-id``; anything else gets a fresh id, so every
    request has one either way and a user-reported failure can be traced portal → API → worker.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = ""
        for name, value in scope.get("headers") or []:
            if name == CORRELATION_HEADER.encode():
                incoming = value.decode("latin-1", "ignore")
                break
        cid = set_correlation_id(incoming)
        # A previous request on this task must not lend its trace to this one's access line.
        _request_trace.set(None)

        async def send_with_header(event: dict[str, Any]) -> None:
            if event["type"] == "http.response.start":
                headers = list(event.get("headers") or [])
                headers.append((CORRELATION_HEADER.encode(), cid.encode()))
                # Still the live server span. Absent when tracing is off (the current span
                # is then invalid). The page trace the browser joins is the one the portal
                # minted, carried in page data, not this header. The access log is written
                # after this span ends, so the same two ids are stashed for that line.
                current = get_current_span().get_span_context()
                if current.is_valid:
                    trace_id = format_trace_id(current.trace_id)
                    span_id = format_span_id(current.span_id)
                    _request_trace.set((trace_id, span_id))
                    token = f"00-{trace_id}-{span_id}-{int(current.trace_flags):02x}"
                    headers.append((b"traceparent", token.encode()))
                event = {**event, "headers": headers}
            await send(event)

        await self.app(scope, receive, send_with_header)
