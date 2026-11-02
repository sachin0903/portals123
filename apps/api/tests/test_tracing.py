"""Statement fingerprints, and which traces are exported.

The ratio is decided when the span starts. Status and duration are only known when it ends,
so the export decision lives in the processor, not in a sampler.
"""

from __future__ import annotations

from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    SpanKind,
    TraceFlags,
    set_span_in_context,
)

from app.tracing import (
    _ExportProcessor,
    fingerprint,
    ratio_selected,
    should_export,
)


def test_fingerprint_strips_literals() -> None:
    assert (
        fingerprint("SELECT * FROM t WHERE id = 42 AND name = 'alice'")
        == "SELECT * FROM t WHERE id = ? AND name = ?"
    )
    assert (
        fingerprint("SELECT  *\nFROM t WHERE email = 'ada@x.com' AND n = 1.5")
        == "SELECT * FROM t WHERE email = ? AND n = ?"
    )


def test_fingerprint_collapses_in_lists() -> None:
    assert (
        fingerprint("SELECT id FROM t WHERE id IN (1, 2, 3)") == "SELECT id FROM t WHERE id IN (?)"
    )
    assert (
        fingerprint("SELECT id FROM t WHERE id IN ($1, $2, $3)")
        == "SELECT id FROM t WHERE id IN (?)"
    )
    assert fingerprint("SELECT id FROM t WHERE id IN (?,?)") == "SELECT id FROM t WHERE id IN (?)"


def test_fingerprint_caps_length() -> None:
    assert len(fingerprint("SELECT " + ("a" * 400))) == 300


def test_ratio_trace_is_exported() -> None:
    assert should_export(
        ratio_selected=True,
        parent_sampled=False,
        http_status=200,
        duration_ms=10,
        keep_errors=True,
        slow_ms=1000,
    )


def test_500_is_exported_when_the_ratio_says_no() -> None:
    assert should_export(
        ratio_selected=False,
        parent_sampled=False,
        http_status=500,
        duration_ms=10,
        keep_errors=True,
        slow_ms=1000,
    )


def test_fast_200_is_dropped_when_the_ratio_says_no() -> None:
    assert not should_export(
        ratio_selected=False,
        parent_sampled=False,
        http_status=200,
        duration_ms=10,
        keep_errors=True,
        slow_ms=1000,
    )


def test_parent_sampled_trace_is_always_exported() -> None:
    assert should_export(
        ratio_selected=False,
        parent_sampled=True,
        http_status=200,
        duration_ms=10,
        keep_errors=False,
        slow_ms=1000,
    )


def test_keep_errors_off_leaves_only_the_ratio() -> None:
    assert not should_export(
        ratio_selected=False,
        parent_sampled=False,
        http_status=500,
        duration_ms=5000,
        keep_errors=False,
        slow_ms=1000,
    )


def test_ratio_matches_the_trace_id() -> None:
    assert ratio_selected(1, 1.0)
    assert not ratio_selected(1, 0.0)
    assert ratio_selected(1, 0.0) is False
    # Same id, same decision, every time.
    first = ratio_selected(0x1234_5678_9ABC_DEF0, 0.1)
    assert ratio_selected(0x1234_5678_9ABC_DEF0, 0.1) is first


class _Sink:
    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def on_start(self, span: ReadableSpan, parent_context: object = None) -> None:
        del span, parent_context

    def on_end(self, span: ReadableSpan) -> None:
        self.spans.append(span)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        del timeout_millis
        return True


def _collect(rate: float, keep_errors: bool = True):
    sink = _Sink()
    provider = TracerProvider(sampler=ALWAYS_ON)
    provider.add_span_processor(
        _ExportProcessor(sink, rate=rate, keep_errors=keep_errors, slow_ms=1000)
    )
    return provider, provider.get_tracer("test"), sink


def test_processor_strips_sql_and_summarises_the_request() -> None:
    provider, tracer, sink = _collect(1.0)
    with tracer.start_as_current_span("GET /me", kind=SpanKind.SERVER) as server:
        server.set_attribute("http.response.status_code", 200)
        server.set_attribute("http.request.method", "GET")
        server.set_attribute("http.route", "/me")
        server.set_attribute("url.full", "http://api/me?email=ada@x.com")
        with tracer.start_as_current_span("SELECT", kind=SpanKind.CLIENT) as query:
            query.set_attribute(
                "db.statement",
                "SELECT email FROM users WHERE id = 42 AND email = 'ada@x.com'",
            )
    provider.shutdown()
    exported = sink.spans
    assert exported
    blob = " ".join(str(dict(span.attributes or {})) for span in exported)
    assert "db.statement" not in blob
    assert "ada@x.com" not in blob
    assert "url.full" not in blob
    server_span = exported[-1]
    attrs = dict(server_span.attributes or {})
    assert attrs["db.query_count"] == 1
    slowest = attrs["db.slowest"]
    assert isinstance(slowest, str)
    assert "SELECT email FROM users WHERE id = ? AND email = ?" in slowest
    assert attrs["http.route"] == "/me"
    query_span = next(span for span in exported if span.kind == SpanKind.CLIENT)
    assert query_span.name == "SELECT email FROM users WHERE id = ? AND email = ?"


def test_a_request_with_no_queries_omits_the_slowest() -> None:
    provider, tracer, sink = _collect(1.0)
    with tracer.start_as_current_span("GET /health", kind=SpanKind.SERVER) as server:
        server.set_attribute("http.response.status_code", 200)
    provider.shutdown()
    assert len(sink.spans) == 1
    attrs = dict(sink.spans[0].attributes or {})
    assert attrs["db.query_count"] == 0
    assert "db.slowest" not in attrs


def test_processor_exports_a_500_the_ratio_would_drop() -> None:
    provider, tracer, sink = _collect(0.0)
    with tracer.start_as_current_span("GET /me", kind=SpanKind.SERVER) as server:
        server.set_attribute("http.response.status_code", 500)
    provider.shutdown()
    assert len(sink.spans) == 1


def test_processor_drops_a_fast_200_the_ratio_would_drop() -> None:
    provider, tracer, sink = _collect(0.0)
    with tracer.start_as_current_span("GET /me", kind=SpanKind.SERVER) as server:
        server.set_attribute("http.response.status_code", 200)
    provider.shutdown()
    assert sink.spans == []


def test_server_span_name_is_the_route_template() -> None:
    provider, tracer, sink = _collect(1.0)
    with tracer.start_as_current_span("GET /accounts/ada@x.com", kind=SpanKind.SERVER) as server:
        server.set_attribute("http.request.method", "GET")
        server.set_attribute("http.route", "/accounts/{uid}")
        server.set_attribute("http.response.status_code", 200)
    provider.shutdown()
    assert sink.spans[-1].name == "GET /accounts/{uid}"
    blob = str(dict(sink.spans[-1].attributes or {}))
    assert "ada@x.com" not in blob


def test_init_tracing_is_quiet_without_an_endpoint(monkeypatch) -> None:
    import app.tracing as tracing

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    previous = tracing._provider
    tracing._provider = None
    try:
        tracing.init_tracing("portals-api")
        assert tracing._provider is None
    finally:
        tracing._provider = previous


def test_blank_otel_settings_keep_the_defaults(monkeypatch) -> None:
    from app.config import Settings

    monkeypatch.setenv("PORTALS_OTEL_KEEP_ERRORS", "")
    monkeypatch.setenv("PORTALS_OTEL_SLOW_REQUEST_MS", "")
    loaded = Settings()
    assert loaded.portals_otel_keep_errors is True
    assert loaded.portals_otel_slow_request_ms == 1000


def test_processor_keeps_a_parent_sampled_trace() -> None:
    provider, tracer, sink = _collect(0.0, keep_errors=False)
    parent = SpanContext(
        trace_id=0xAB,
        span_id=0xCD,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    ctx = set_span_in_context(NonRecordingSpan(parent))
    with tracer.start_as_current_span("GET /me", context=ctx, kind=SpanKind.SERVER) as server:
        server.set_attribute("http.response.status_code", 200)
    provider.shutdown()
    assert len(sink.spans) == 1
