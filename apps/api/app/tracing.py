"""One trace per request, exported only when a collector is configured.

``OTEL_EXPORTER_OTLP_ENDPOINT`` empty means local dev and tests stay quiet: ``init_tracing``
does not install a provider. When it is set, spans go to that endpoint over OTLP/HTTP. The
sampler named in ``OTEL_TRACES_SAMPLER`` decides the baseline, and a processor keeps errors
and slow server spans on top of it — a sampler cannot, because it runs before status and
duration exist.

Nothing that leaves the process carries raw SQL, bind values, row contents, or a raw URL
path. Statement text is fingerprinted onto the server span and then dropped. Each database
span is named with that fingerprint, so the waterfall is not eighteen spans called
``SELECT portals``.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Awaitable, Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


# Stable HTTP attribute names (``http.request.method``, ``http.response.status_code``) are
# what the collector's Prometheus metrics group by. The instrumentor caches this choice the
# first time it runs, which is at import, so it has to be set before that import.
def _prefer_stable_http_semconv() -> None:
    current = os.environ.get("OTEL_SEMCONV_STABILITY_OPT_IN", "")
    parts = [part.strip() for part in current.split(",") if part.strip()]
    if "http" not in parts and "http/dup" not in parts:
        parts.append("http")
    os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = ",".join(parts)


_prefer_stable_http_semconv()

from opentelemetry import context as otel_context  # noqa: E402
from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.resources import Resource  # noqa: E402
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.id_generator import IdGenerator, RandomIdGenerator  # noqa: E402
from opentelemetry.sdk.trace.sampling import ALWAYS_ON  # noqa: E402
from opentelemetry.trace import SpanKind, StatusCode  # noqa: E402

from app.config import settings  # noqa: E402

# A client that never finishes must not pin an unbounded buffer. Either limit drops it.
_MAX_SPANS = 1000
_MAX_AGE_NS = 10_000_000_000
_MAX_BUFFERS = 4096
_FINGERPRINT_MAX = 300
_SLOWEST = 3

_TRACE_ID_LIMIT = (1 << 64) - 1

_STATEMENT_KEYS = ("db.statement", "db.query.text")
_DROP_KEYS = frozenset(
    {
        "db.statement",
        "db.query.text",
        "db.statement.parameters",
        "db.query.parameters",
        # The route template stays on ``http.route``. These are the raw path.
        "http.url",
        "http.target",
        "url.full",
        "url.path",
        "url.query",
        "url.original",
    }
)

_WS = re.compile(r"\s+")
_STRING = re.compile(r"'(?:''|[^'])*'")
_PLACEHOLDER = re.compile(r"\$\d+|%\([\w]+\)s|%s|(?<!:):[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"(?<!\$)\b\d+(?:\.\d+)?\b")
_IN_LIST = re.compile(r"\bIN\s*\(\s*\?(?:\s*,\s*\?)*\s*\)", re.IGNORECASE)

_forced_trace_id: ContextVar[int | None] = ContextVar("otel_forced_trace_id", default=None)
_forced_span_id: ContextVar[int | None] = ContextVar("otel_forced_span_id", default=None)

_provider: TracerProvider | None = None


class _PageIdGenerator(IdGenerator):
    """Random ids, unless this task is emitting the page-view span with a known id."""

    def __init__(self) -> None:
        self._random = RandomIdGenerator()

    def generate_span_id(self) -> int:
        forced = _forced_span_id.get()
        if forced:
            return forced
        return self._random.generate_span_id()

    def generate_trace_id(self) -> int:
        forced = _forced_trace_id.get()
        if forced:
            return forced
        return self._random.generate_trace_id()

    def is_trace_id_random(self) -> bool:
        return True


def fingerprint(statement: str) -> str:
    """A stable shape of a statement: no literals, no bind values, collapsed ``IN`` lists."""
    text = _WS.sub(" ", statement).strip()
    text = _STRING.sub("?", text)
    text = _PLACEHOLDER.sub("?", text)
    text = _NUMBER.sub("?", text)
    text = _IN_LIST.sub("IN (?)", text)
    if len(text) > _FINGERPRINT_MAX:
        return text[:_FINGERPRINT_MAX]
    return text


def sampler_rate(name: str, arg: str) -> float:
    """The fraction ``OTEL_TRACES_SAMPLER`` keeps, before the error and slow overrides."""
    key = (name or "").strip().lower() or "parentbased_always_on"
    if key in {"always_on", "parentbased_always_on"}:
        return 1.0
    if key in {"always_off", "parentbased_always_off"}:
        return 0.0
    if key in {"traceidratio", "parentbased_traceidratio"}:
        try:
            rate = float((arg or "").strip() or "1")
        except ValueError:
            return 1.0
        return min(1.0, max(0.0, rate))
    return 1.0


def ratio_selected(trace_id: int, rate: float) -> bool:
    """Same comparison as the SDK's ``TraceIdRatioBased``: the low 64 bits of the trace id."""
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    bound = round(rate * (_TRACE_ID_LIMIT + 1))
    return (trace_id & _TRACE_ID_LIMIT) < bound


def should_export(
    *,
    ratio_selected: bool,
    parent_sampled: bool,
    http_status: int | None,
    duration_ms: float,
    keep_errors: bool,
    slow_ms: float,
    span_error: bool = False,
) -> bool:
    """Whether a finished trace is worth sending.

    A sampled parent is always kept (parent-based). Otherwise the ratio applies. Status and
    duration are consulted only when ``keep_errors`` is on, because they do not exist when a
    sampler would have run. ``span_error`` covers a worker job that failed without an HTTP
    status.
    """
    if parent_sampled or ratio_selected:
        return True
    if not keep_errors:
        return False
    if http_status is not None and http_status >= 500:
        return True
    if span_error:
        return True
    return duration_ms >= slow_ms


def format_slowest(items: list[tuple[float, str]]) -> str:
    return "\n".join(f"{int(round(ms))}ms {text}" for ms, text in items[:_SLOWEST])


@dataclass
class _Buffer:
    spans: list[ReadableSpan] = field(default_factory=list)
    span_ids: list[int] = field(default_factory=list)
    started_ns: int = 0
    ratio: bool = False
    parent_sampled: bool = False
    query_count: int = 0
    time_ms: float = 0.0
    slowest: list[tuple[float, str]] = field(default_factory=list)

    def note(self, duration_ms: float, shape: str) -> None:
        self.query_count += 1
        self.time_ms += duration_ms
        self.slowest.append((duration_ms, shape))
        self.slowest.sort(key=lambda item: item[0], reverse=True)
        del self.slowest[_SLOWEST:]


class _ExportProcessor(SpanProcessor):
    """Buffer a trace until its root span ends, then export or discard it.

    The SDK sampler is always-on so the spans exist to be judged. This processor is the
    decision. It also strips statement text off database spans, names each one with the
    fingerprint, and writes the query summary onto the span that closes the buffer.
    """

    def __init__(
        self,
        next_processor: Any,
        *,
        rate: float,
        keep_errors: bool,
        slow_ms: float,
    ) -> None:
        self._next = next_processor
        self._rate = rate
        self._keep_errors = keep_errors
        self._slow_ms = slow_ms
        self._buffers: dict[int, _Buffer] = {}
        self._owner: dict[int, int] = {}
        self._lock = threading.Lock()

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        del parent_context
        parent = span.parent
        parent_id = parent.span_id if parent is not None and parent.span_id else 0
        span_id = span.context.span_id
        with self._lock:
            owner = self._owner.get(parent_id) if parent_id else None
            if owner is None or owner not in self._buffers:
                parent_sampled = bool(parent is not None and parent.trace_flags.sampled)
                buf = _Buffer(
                    started_ns=time.monotonic_ns(),
                    ratio=ratio_selected(span.context.trace_id, self._rate),
                    parent_sampled=parent_sampled,
                )
                buf.span_ids.append(span_id)
                self._buffers[span_id] = buf
                self._owner[span_id] = span_id
                self._evict_locked()
                return
            self._owner[span_id] = owner
            self._buffers[owner].span_ids.append(span_id)

    def _on_ending(self, span: Any) -> None:
        ending = getattr(self._next, "_on_ending", None)
        if ending is not None:
            ending(span)

    def on_end(self, span: ReadableSpan) -> None:
        # Read the statement before it is stripped. The exporter must not see it.
        shape = _statement(span)
        self._redact(span)
        span_id = span.context.span_id if span.context is not None else 0
        with self._lock:
            owner = self._owner.get(span_id)
            self._evict_others_locked(owner)
            buf = self._buffers.get(owner) if owner is not None else None
            if owner is None or buf is None:
                return
            # A client that never finishes: drop the buffer rather than grow it.
            # The span that closes the buffer is still judged, so a slow request
            # that does finish is kept.
            if span_id != owner and (
                len(buf.spans) >= _MAX_SPANS or time.monotonic_ns() - buf.started_ns > _MAX_AGE_NS
            ):
                self._drop_locked(owner)
                return
            if shape is not None:
                named = fingerprint(shape)
                span._name = named  # noqa: SLF001 — name is the statement fingerprint
                buf.note(_duration_ms(span), named)
            buf.spans.append(span)
            if span_id != owner:
                return
            self._finish_locked(span, buf)
            self._drop_locked(span_id)

    def shutdown(self) -> None:
        with self._lock:
            pending = list(self._buffers.items())
            self._buffers.clear()
            self._owner.clear()
        for _owner, buf in pending:
            for item in buf.spans:
                self._next.on_end(item)
        self._next.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        flushed = self._next.force_flush(timeout_millis)
        return bool(flushed)

    def _finish_locked(self, span: ReadableSpan, buf: _Buffer) -> None:
        page = span.name == "browser.view"
        if not page:
            _write(span, "db.query_count", buf.query_count)
            _write(span, "db.time_ms", buf.time_ms)
            if buf.slowest:
                _write(span, "db.slowest", format_slowest(buf.slowest))
        _promote_http(span)
        # The page span's wall time is the settle time, which is often over the slow
        # threshold. Judging it on that would export every page view. It follows the
        # ratio (and a sampled parent) so it joins the same traces as the API spans.
        if not should_export(
            ratio_selected=buf.ratio,
            parent_sampled=buf.parent_sampled,
            http_status=None if page else _http_status(span),
            duration_ms=0.0 if page else _duration_ms(span),
            keep_errors=self._keep_errors,
            slow_ms=self._slow_ms,
            span_error=False if page else span.status.status_code == StatusCode.ERROR,
        ):
            return
        for item in buf.spans:
            self._next.on_end(item)

    def _drop_locked(self, owner: int) -> None:
        buf = self._buffers.pop(owner, None)
        if buf is None:
            return
        for span_id in buf.span_ids:
            self._owner.pop(span_id, None)

    def _evict_locked(self) -> None:
        self._evict_others_locked(None)

    def _evict_others_locked(self, keep: int | None) -> None:
        now = time.monotonic_ns()
        stale = [
            owner
            for owner, buf in self._buffers.items()
            if owner != keep and (now - buf.started_ns > _MAX_AGE_NS or len(buf.spans) > _MAX_SPANS)
        ]
        for owner in stale:
            self._drop_locked(owner)
        while len(self._buffers) > _MAX_BUFFERS:
            oldest = min(self._buffers, key=lambda key: self._buffers[key].started_ns)
            if oldest == keep:
                break
            self._drop_locked(oldest)

    def _redact(self, span: ReadableSpan) -> None:
        attrs = _open(span)
        if attrs is None:
            return
        try:
            for key in _DROP_KEYS:
                attrs.pop(key, None)
        finally:
            _close(span)


def _statement(span: ReadableSpan) -> str | None:
    attrs = span.attributes or {}
    for key in _STATEMENT_KEYS:
        value = attrs.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _duration_ms(span: ReadableSpan) -> float:
    if span.start_time is None or span.end_time is None:
        return 0.0
    return max(0.0, (span.end_time - span.start_time) / 1_000_000)


def _http_status(span: ReadableSpan) -> int | None:
    attrs = span.attributes or {}
    for key in ("http.response.status_code", "http.status_code"):
        value = attrs.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _promote_http(span: ReadableSpan) -> None:
    """Copy the legacy HTTP names onto the stable ones the collector groups by."""
    if span.kind != SpanKind.SERVER:
        return
    attrs = _open(span)
    if attrs is None:
        return
    try:
        method = attrs.get("http.request.method") or attrs.get("http.method")
        status = attrs.get("http.response.status_code")
        if status is None:
            status = attrs.get("http.status_code")
        route = attrs.get("http.route")
        if isinstance(method, str) and "http.request.method" not in attrs:
            attrs["http.request.method"] = method
        if isinstance(status, int) and not isinstance(status, bool):
            if "http.response.status_code" not in attrs:
                attrs["http.response.status_code"] = status
        if isinstance(method, str) and isinstance(route, str):
            span._name = f"{method} {route}"  # noqa: SLF001 — name is the route template
        elif isinstance(method, str):
            span._name = method  # noqa: SLF001
    finally:
        _close(span)


def _open(span: ReadableSpan) -> MutableMapping[str, Any] | None:
    attrs = getattr(span, "_attributes", None)
    if not isinstance(attrs, MutableMapping):
        return None
    if hasattr(attrs, "_immutable"):
        attrs._immutable = False  # noqa: SLF001 — ended spans freeze attributes before on_end
    return attrs


def _close(span: ReadableSpan) -> None:
    attrs = getattr(span, "_attributes", None)
    if attrs is not None and hasattr(attrs, "_immutable"):
        attrs._immutable = True  # noqa: SLF001


def _write(span: ReadableSpan, key: str, value: str | int | float) -> None:
    attrs = _open(span)
    if attrs is None:
        return
    try:
        attrs[key] = value
    finally:
        _close(span)


def init_tracing(service_name: str) -> None:
    """Install the exporter, or do nothing when no collector endpoint is configured."""
    global _provider
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint or _provider is not None:
        return
    # Wins over ``service.name`` inside ``OTEL_RESOURCE_ATTRIBUTES``.
    os.environ["OTEL_SERVICE_NAME"] = service_name
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    rate = sampler_rate(
        os.environ.get("OTEL_TRACES_SAMPLER", ""),
        os.environ.get("OTEL_TRACES_SAMPLER_ARG", ""),
    )
    provider = TracerProvider(
        resource=Resource.create(),
        sampler=ALWAYS_ON,
        id_generator=_PageIdGenerator(),
    )
    provider.add_span_processor(
        _ExportProcessor(
            BatchSpanProcessor(OTLPSpanExporter()),
            rate=rate,
            keep_errors=settings.portals_otel_keep_errors,
            slow_ms=float(settings.portals_otel_slow_request_ms),
        )
    )
    trace.set_tracer_provider(provider)
    _provider = provider


def shutdown_tracing() -> None:
    """Flush and stop. Safe when tracing was never started."""
    global _provider
    provider = _provider
    _provider = None
    if provider is not None:
        provider.shutdown()


def instrument_engine(engine: Any) -> None:
    """Record one span per statement on this engine only.

    The instrumentor also patches ``create_engine`` for the whole process. That patch is
    removed again so a datalake or Alembic engine created afterwards is left alone. The
    listeners on ``engine`` stay.
    """
    sync = getattr(engine, "sync_engine", engine)
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=sync)
    release_global_engine_wrap()


def release_global_engine_wrap() -> None:
    """Undo the process-wide factory patch ``instrument(engine=...)`` installs."""
    import sqlalchemy
    import sqlalchemy.ext.asyncio
    from opentelemetry.instrumentation.utils import unwrap
    from sqlalchemy.engine.base import Engine

    for target, name in (
        (sqlalchemy, "create_engine"),
        (sqlalchemy.engine, "create_engine"),
        (sqlalchemy.engine.create, "create_engine"),
        (sqlalchemy.ext.asyncio, "create_async_engine"),
        (Engine, "connect"),
    ):
        # A no-op when that name was not patched. unwrap only replaces a wrapt proxy.
        unwrap(target, name)


def instrument_api(app: Any) -> None:
    """The FastAPI server span: route template, method, status, wall time."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)


@contextmanager
def job_span(name: str) -> Iterator[None]:
    """A worker job's own trace. It does not join a page view."""
    tracer = trace.get_tracer("portals.render")
    with tracer.start_as_current_span(name):
        yield


async def traced_job[T](name: str, work: Awaitable[T]) -> T:
    """Run one worker job inside ``job_span``. The awaitable must not be started yet."""
    with job_span(name):
        return await work


@dataclass(frozen=True)
class BrowserView:
    trace_id: str
    span_id: str | None
    attributes: Mapping[str, str | float]


def emit_browser_view(view: BrowserView) -> None:
    """A ``browser.view`` span in the posted trace. No-op when tracing is off."""
    if _provider is None:
        return
    trace_int = int(view.trace_id, 16)
    span_int = int(view.span_id, 16) if view.span_id else 0
    settle = view.attributes.get("rum.settle_ms")
    settle_ms = float(settle) if isinstance(settle, int | float) else 0.0
    start = time.time_ns() - int(settle_ms * 1_000_000)
    trace_token = _forced_trace_id.set(trace_int)
    span_token = _forced_span_id.set(span_int or None)
    try:
        # An empty context so this is the root of the page trace, not a child of ``/me/rum``.
        span = _provider.get_tracer("portals-api").start_span(
            "browser.view",
            context=otel_context.Context(),
            start_time=max(start, 0),
        )
    finally:
        _forced_trace_id.reset(trace_token)
        _forced_span_id.reset(span_token)
    for key, value in view.attributes.items():
        span.set_attribute(key, value)
    span.end()


_PARAM = re.compile(r"\{[^}]+\}")


def _route_pattern(template: str) -> re.Pattern[str]:
    parts: list[str] = []
    cursor = 0
    for match in _PARAM.finditer(template):
        parts.append(re.escape(template[cursor : match.start()]))
        parts.append("[^/]+")
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    return re.compile("".join(parts) + "/?")


def external_span_details(scope: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
    """Litestar span name and ``http.route``, as the route template rather than the raw path."""
    method = str(scope.get("method") or "HTTP").strip().upper() or "HTTP"
    path = str(scope.get("path") or "")
    route = _litestar_route(scope.get("app"), path)
    return f"{method} {route}", {"http.route": route}


def _litestar_route(app: Any, path: str) -> str:
    routes = getattr(app, "routes", None) or ()
    target = path.rstrip("/") or "/"
    best: tuple[int, int, str] | None = None
    for route in routes:
        template = getattr(route, "path_format", None)
        if not isinstance(template, str) or not template:
            continue
        if _route_pattern(template).fullmatch(target) is None:
            continue
        score = (template.count("{"), -len(template))
        if best is None or score < (best[0], best[1]):
            best = (score[0], score[1], template)
    return best[2] if best is not None else "unmatched"


def external_otel_plugin() -> Any | None:
    """Litestar's OpenTelemetry plugin on the same provider. ``None`` when tracing is off."""
    if _provider is None:
        return None
    from litestar.plugins.opentelemetry import OpenTelemetryConfig, OpenTelemetryPlugin

    return OpenTelemetryPlugin(
        OpenTelemetryConfig(
            tracer_provider=_provider,
            scope_span_details_extractor=external_span_details,
            exclude_spans=["receive", "send"],
        )
    )
