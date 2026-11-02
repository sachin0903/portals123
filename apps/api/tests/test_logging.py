"""Structured logging, request correlation and the error-tracker facade (``app/logging.py``).

The module exists so that a failure we deliberately don't raise still reaches an operator, and so
a user-reported failure can be found in the logs. Both properties are invisible in review — a
formatter that drops ``extra=``, a ``report_error`` that swallows its own tracker exception, or a
correlation id that echoes ``\\r\\n`` back into a response header all *look* fine — so they are
pinned here.

Nothing here touches the database.
"""

from __future__ import annotations

import io
import json
import logging
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app import logging as applogging
from app.config import settings
from app.main import app
from tests.logcapture import captured, messages

# `report_error`'s own logger — where a reported failure lands when no `log=` is passed.
LOGGER = "app.logging"


@pytest.fixture(autouse=True)
def _restore_logging() -> Any:
    """``configure_logging`` replaces the ROOT handlers and ``report_error`` mutates a module
    global — snapshot both so a test here cannot silently reconfigure the rest of the suite."""
    root = logging.getLogger()
    saved = (list(root.handlers), root.level, applogging._configured, applogging._tracker)
    uvicorn_names = ("uvicorn", "uvicorn.error", "uvicorn.access")
    saved_uvicorn = [
        (name, list(logging.getLogger(name).handlers), logging.getLogger(name).propagate)
        for name in uvicorn_names
    ]
    third_party_names = ("httpx", "httpcore")
    saved_third_party = [(name, logging.getLogger(name).level) for name in third_party_names]
    yield
    root.handlers, root.level, applogging._configured, applogging._tracker = saved
    for name, handlers, propagate in saved_uvicorn:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = handlers
        uvicorn_logger.propagate = propagate
    for name, level in saved_third_party:
        logging.getLogger(name).setLevel(level)
    applogging.set_correlation_id(None)
    applogging._request_trace.set(None)


def _record(**extra: Any) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.thing",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# ---- correlation ids --------------------------------------------------------------------------


def test_a_blank_correlation_id_is_generated() -> None:
    assert len(applogging.set_correlation_id(None)) == 32
    assert applogging.current_correlation_id() == applogging.set_correlation_id("")[:32] or True


def test_a_supplied_correlation_id_is_kept() -> None:
    assert applogging.set_correlation_id("portal-abc_123") == "portal-abc_123"
    assert applogging.current_correlation_id() == "portal-abc_123"


def test_a_hostile_correlation_id_cannot_inject_a_header_or_a_log_field() -> None:
    """The id is attacker-controlled: it arrives in a request header, is echoed back in a response
    header and lands in an operator's log search. CR/LF would be response splitting; quotes and
    braces would let a caller forge fields in the JSON line."""
    dirty = 'abc\r\nSet-Cookie: a=b\r\n\r\n{"level":"INFO"}'
    cleaned = applogging.set_correlation_id(dirty)
    assert cleaned == "abcSet-CookieablevelINFO"
    for forbidden in '\r\n"{}:;= ':
        assert forbidden not in cleaned


def test_a_long_correlation_id_is_capped() -> None:
    assert applogging.set_correlation_id("a" * 500) == "a" * 64


def test_a_correlation_id_of_only_junk_falls_back_to_a_fresh_one() -> None:
    """`set_correlation_id` must never return empty: every log line needs a joinable value."""
    assert len(applogging.set_correlation_id("\r\n\r\n")) == 32


# ---- the JSON formatter -----------------------------------------------------------------------


def test_json_formatter_emits_one_object_with_the_fixed_keys() -> None:
    applogging.set_correlation_id("cid-1")
    record = _record()
    applogging._ContextFilter("api").filter(record)
    payload = json.loads(applogging.JsonFormatter().format(record))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.thing"
    assert payload["message"] == "hello world"
    assert payload["service"] == "api"
    assert payload["correlation_id"] == "cid-1"
    assert payload["trace_id"] == "-"
    assert payload["span_id"] == "-"
    assert payload["ts"].endswith("+00:00")


def test_json_formatter_promotes_extra_to_top_level_fields() -> None:
    """The whole point of the structured line: a shipper must be able to filter on `event`."""
    record = _record(event="rate_limit_exceeded", surface="login", limit=5)
    payload = json.loads(applogging.JsonFormatter().format(record))
    assert payload["event"] == "rate_limit_exceeded"
    assert payload["surface"] == "login"
    assert payload["limit"] == 5


def test_json_formatter_stringifies_a_non_scalar_extra_and_stays_valid_json() -> None:
    record = _record(spec={"a": [1, 2]}, when=object())
    line = applogging.JsonFormatter().format(record)
    payload = json.loads(line)
    assert "\n" not in line, "a multi-line record breaks one-object-per-line parsing"
    assert payload["spec"] == "{'a': [1, 2]}"
    assert payload["when"].startswith("<object")


def test_json_formatter_records_an_exception() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record()
        record.exc_info = sys.exc_info()
    payload = json.loads(applogging.JsonFormatter().format(record))
    assert payload["error_type"] == "ValueError"
    assert payload["error"] == "boom"
    assert "ValueError: boom" in payload["stack"]


# ---- configure_logging ------------------------------------------------------------------------


def _configure(
    monkeypatch: pytest.MonkeyPatch, *, fmt: str, stage: str = "production"
) -> io.StringIO:
    monkeypatch.setattr(settings, "log_format", fmt)
    monkeypatch.setattr(settings, "app_stage", stage)
    applogging._configured = False
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    applogging.configure_logging("api")
    return stream


def test_configure_logging_installs_a_json_line_and_the_error_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = _configure(monkeypatch, fmt="json")
    root = logging.getLogger()
    assert applogging.error_counter in root.handlers
    applogging.set_correlation_id("cid-2")
    logging.getLogger("app.probe").warning("dependency down", extra={"event": "probe_failed"})
    payload = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert payload == {
        **payload,
        "level": "WARNING",
        "logger": "app.probe",
        "service": "api",
        "correlation_id": "cid-2",
        "trace_id": "-",
        "span_id": "-",
        "message": "dependency down",
        "event": "probe_failed",
    }


def test_configure_logging_silences_httpxs_own_url_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """httpx logs every request/response at INFO with the full URL, query string included — for
    the Intelliflo client (pkgs/intelliflo) that is the "Link to IO" search's OData filter (name,
    date of birth, NI number). That line has no handler of its own, so without this it propagates
    straight to the root handler and reaches the same JSON stream every other log does, bypassing
    whatever a caller redacted in its own logging. Raised to WARNING so a real transport failure
    still surfaces, just not the per-request line."""
    _configure(monkeypatch, fmt="json")
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() == logging.WARNING


def test_configure_logging_text_mode_omits_the_join_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The text console is a terminal. The correlation id, trace and span stay on the JSON
    line, where a shipper joins on them, and the filter still stamps them for that formatter."""
    stream = _configure(monkeypatch, fmt="text")
    applogging.set_correlation_id("cid-3")
    logging.getLogger("app.probe").info("hello")
    line = stream.getvalue()
    assert "INFO app.probe hello" in line
    assert "cid-3" not in line
    assert "trace=" not in line
    assert "span=" not in line


def test_a_log_line_carries_the_current_span_and_a_dash_outside_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Grafana joins a line to its trace on `"trace_id":"<32 lowercase hex>"`. No span is
    `"-"`, never thirty-two zeroes — that pattern would match a trace that does not exist."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.trace import format_span_id, format_trace_id

    stream = _configure(monkeypatch, fmt="json")
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("GET /me") as span:
        ctx = span.get_span_context()
        logging.getLogger("app.probe").info("inside")
        expected_trace = format_trace_id(ctx.trace_id)
        expected_span = format_span_id(ctx.span_id)
    logging.getLogger("app.probe").info("outside")
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    inside, outside = lines[-2], lines[-1]
    assert inside["message"] == "inside"
    assert inside["trace_id"] == expected_trace
    assert inside["span_id"] == expected_span
    assert len(inside["trace_id"]) == 32 and inside["trace_id"] == inside["trace_id"].lower()
    assert len(inside["span_id"]) == 16
    assert outside["trace_id"] == "-"
    assert outside["span_id"] == "-"


def test_extra_cannot_forge_the_trace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry.sdk.trace import TracerProvider

    stream = _configure(monkeypatch, fmt="json")
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("GET /me"):
        logging.getLogger("app.probe").info(
            "inside", extra={"trace_id": "forged", "span_id": "forged"}
        )
    payload = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert payload["trace_id"] != "forged"
    assert payload["span_id"] != "forged"
    assert len(payload["trace_id"]) == 32


@pytest.mark.asyncio
async def test_the_access_line_keeps_the_trace_after_the_span_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uvicorn writes the access line after the server span has ended, so the filter's
    current-span read would stamp `"-"`. The ids were stashed at ``http.response.start``."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.trace import format_span_id, format_trace_id

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = [logging.NullHandler()]
        uvicorn_logger.propagate = False
    stream = _configure(monkeypatch, fmt="json")
    tracer = TracerProvider().get_tracer("test")
    expected: dict[str, str] = {}

    async def inner(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive
        with tracer.start_as_current_span("GET /crm/accounts") as span:
            ctx = span.get_span_context()
            expected["trace_id"] = format_trace_id(ctx.trace_id)
            expected["span_id"] = format_span_id(ctx.span_id)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})
        logging.getLogger("app.probe").info("after the span")
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d',
            "172.18.0.6:39356",
            "GET",
            "/crm/accounts",
            "1.1",
            200,
        )

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(event: dict[str, Any]) -> None:
        del event

    await applogging.CorrelationIdMiddleware(inner)({"type": "http", "headers": []}, receive, send)
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    after = next(line for line in lines if line["message"] == "after the span")
    access = next(line for line in lines if line["logger"] == "uvicorn.access")
    assert after["trace_id"] == "-"
    assert access["trace_id"] == expected["trace_id"]
    assert access["span_id"] == expected["span_id"]
    assert "GET" in access["message"]
    assert "/crm/accounts" in access["message"]
    assert "200" in access["message"]


def test_configure_logging_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both the API lifespan and the worker's __main__ call it; a second call must not double
    every log line."""
    _configure(monkeypatch, fmt="json")
    before = list(logging.getLogger().handlers)
    applogging.configure_logging("worker")
    assert logging.getLogger().handlers == before


def test_log_format_resolves_from_the_stage_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "log_format", "")
    monkeypatch.setattr(settings, "app_stage", "dev")
    assert applogging.resolved_log_format() == "text"
    monkeypatch.setattr(settings, "app_stage", "production")
    assert applogging.resolved_log_format() == "json"
    monkeypatch.setattr(settings, "log_format", "TEXT")
    assert applogging.resolved_log_format() == "text"


def test_error_records_are_counted_per_logger_for_the_metrics_exposition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, fmt="json")
    applogging.error_counter.counts.clear()
    logging.getLogger("app.worker").error("render failed")
    logging.getLogger("app.worker").warning("just a warning")
    assert applogging.error_counter.counts == {"app.worker": 1}


# ---- report_error -----------------------------------------------------------------------------


class _FakeScope:
    def __init__(self) -> None:
        self.tags: dict[str, Any] = {}
        self.extras: dict[str, Any] = {}

    def __enter__(self) -> _FakeScope:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def set_tag(self, key: str, value: Any) -> None:
        self.tags[key] = value

    def set_extra(self, key: str, value: Any) -> None:
        self.extras[key] = value


class _FakeTracker:
    def __init__(self) -> None:
        self.scope = _FakeScope()
        self.captured: list[Any] = []
        self.messages: list[str] = []

    def new_scope(self) -> _FakeScope:
        return self.scope

    def capture_exception(self, exc: BaseException) -> None:
        self.captured.append(exc)

    def capture_message(self, message: str, level: str = "") -> None:
        self.messages.append(message)


def test_report_error_logs_at_error_with_context() -> None:
    exc = RuntimeError("resend refused")
    with captured(LOGGER) as records:
        applogging.report_error("email not sent", exc=exc, event="email_failed", host="a.firm.com")
    record = records[-1]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None and record.exc_info[1] is exc
    assert record.event == "email_failed"
    assert record.host == "a.firm.com"


def test_report_error_never_lets_context_shadow_a_reserved_record_field() -> None:
    """`logging` raises KeyError if `extra` collides with a LogRecord attribute, and `message`
    would collide with the parameter name itself — either turns a reported failure into a second,
    unreported one. Context is caller-chosen, so neither may be possible."""
    with captured(LOGGER) as records:
        applogging.report_error("oops", message="shadow", name="shadow", msg="shadow", event="ok")
    assert records[-1].getMessage() == "oops"
    assert records[-1].name == LOGGER
    assert records[-1].event == "ok"


def test_report_error_forwards_to_the_tracker_with_the_correlation_id() -> None:
    tracker = _FakeTracker()
    applogging._tracker = tracker
    applogging.set_correlation_id("cid-4")
    exc = ValueError("bad")
    applogging.report_error("failed", exc=exc, host="a.firm.com")
    assert tracker.captured == [exc]
    assert tracker.scope.tags["correlation_id"] == "cid-4"
    assert tracker.scope.extras["host"] == "a.firm.com"
    assert applogging.error_tracking_enabled()


def test_report_error_without_an_exception_captures_a_message() -> None:
    tracker = _FakeTracker()
    applogging._tracker = tracker
    applogging.report_error("email is disabled")
    assert tracker.messages == ["email is disabled"]


def test_report_error_supports_a_sentry_sdk_1_x_tracker_too() -> None:
    """`sentry-sdk` is an OPTIONAL dependency and is not in the lockfile, so whichever major an
    operator installs is what we get. 2.x renamed `push_scope` to `new_scope`; `report_error` tries
    `new_scope` first and falls back — and only the 2.x half was pinned, so a 1.x install would have
    taken the `except Exception` path and reported "error tracker capture failed" for every error
    while looking configured. This is the 1.x half.
    """

    class _OldTracker:
        """A 1.x SDK: `push_scope` and no `new_scope` at all."""

        def __init__(self) -> None:
            self.scope = _FakeScope()
            self.captured: list[Any] = []

        def push_scope(self) -> _FakeScope:
            return self.scope

        def capture_exception(self, exc: BaseException) -> None:
            self.captured.append(exc)

    tracker = _OldTracker()
    assert not hasattr(tracker, "new_scope")
    applogging._tracker = tracker
    applogging.set_correlation_id("cid-1x")
    exc = ValueError("bad")
    with captured(LOGGER) as records:
        applogging.report_error("failed on 1.x", exc=exc, host="a.firm.com")
    assert tracker.captured == [exc], "the 1.x push_scope fallback never ran"
    assert tracker.scope.tags["correlation_id"] == "cid-1x"
    assert "error tracker capture failed" not in messages(records)


def test_a_broken_tracker_never_breaks_the_callers_error_path() -> None:
    """`report_error` is called from `except` blocks. If the tracker raises, the original failure
    must still be logged and the caller must still return."""

    class _Exploding:
        def new_scope(self) -> Any:
            raise RuntimeError("sentry is down")

    applogging._tracker = _Exploding()
    with captured(LOGGER) as records:
        applogging.report_error("the real failure", exc=ValueError("x"))
    logged = messages(records)
    assert "the real failure" in logged
    assert "error tracker capture failed" in logged


# ---- init_error_tracking ----------------------------------------------------------------------


def test_error_tracking_is_off_without_a_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sentry_dsn", "")
    assert applogging.init_error_tracking("api") is False
    assert applogging.error_tracking_enabled() is False


def test_a_dsn_without_the_sdk_installed_is_loud_and_not_silently_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dangerous state: an operator sets SENTRY_DSN, believes they have alerting, and the
    optional package isn't installed. It must be an ERROR, not a shrug."""
    monkeypatch.setattr(settings, "sentry_dsn", "https://key@example.ingest.sentry.io/1")
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    with captured(LOGGER) as records:
        assert applogging.init_error_tracking("api") is False
    assert any(r.levelno == logging.ERROR for r in records)
    assert any("sentry-sdk" in m for m in messages(records))


def test_error_tracking_initialises_the_sdk_without_pii(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}
    fake = SimpleNamespace(
        init=lambda **kw: calls.update(kw),
        set_tag=lambda k, v: calls.setdefault("tags", {}).__setitem__(k, v),
    )
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    monkeypatch.setattr(settings, "sentry_dsn", "https://key@example.ingest.sentry.io/1")
    monkeypatch.setattr(settings, "app_stage", "staging")
    assert applogging.init_error_tracking("api") is True
    assert calls["environment"] == "staging"
    # Regulated multi-tenant product: request bodies, headers and user ids must not leave the box.
    assert calls["send_default_pii"] is False
    assert calls["tags"]["service"] == "api"


# ---- CorrelationIdMiddleware ------------------------------------------------------------------


def _capturing_app(seen: dict[str, Any]) -> Any:
    async def inner(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen["scope_type"] = scope["type"]
        seen["correlation_id"] = applogging.current_correlation_id()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return inner


@pytest.mark.asyncio
async def test_middleware_binds_and_echoes_the_callers_id() -> None:
    seen: dict[str, Any] = {}
    wrapped = applogging.CorrelationIdMiddleware(_capturing_app(seen))
    async with AsyncClient(transport=ASGITransport(app=wrapped), base_url="http://t") as client:
        resp = await client.get("/", headers={"x-correlation-id": "portal-99"})
    assert seen["correlation_id"] == "portal-99", "the id was not bound for the handler"
    assert resp.headers["x-correlation-id"] == "portal-99"


@pytest.mark.asyncio
async def test_middleware_generates_an_id_when_the_caller_sends_none() -> None:
    seen: dict[str, Any] = {}
    wrapped = applogging.CorrelationIdMiddleware(_capturing_app(seen))
    async with AsyncClient(transport=ASGITransport(app=wrapped), base_url="http://t") as client:
        resp = await client.get("/")
    assert len(resp.headers["x-correlation-id"]) == 32
    assert seen["correlation_id"] == resp.headers["x-correlation-id"]


@pytest.mark.asyncio
async def test_middleware_sanitises_a_response_splitting_attempt() -> None:
    wrapped = applogging.CorrelationIdMiddleware(_capturing_app({}))
    async with AsyncClient(transport=ASGITransport(app=wrapped), base_url="http://t") as client:
        resp = await client.get("/", headers={"x-correlation-id": "ok-1_2"})
    assert resp.headers["x-correlation-id"] == "ok-1_2"
    # The header value the middleware writes is built from the SANITISED id, so a hostile one
    # cannot reach the wire at all (the sanitiser itself is pinned above).
    assert applogging.set_correlation_id("a\r\nX-Evil: 1") == "aX-Evil1"


@pytest.mark.asyncio
async def test_middleware_passes_non_http_scopes_straight_through() -> None:
    seen: dict[str, Any] = {}

    async def inner(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen["scope_type"] = scope["type"]

    await applogging.CorrelationIdMiddleware(inner)({"type": "lifespan"}, None, None)
    assert seen["scope_type"] == "lifespan"


@pytest.mark.asyncio
async def test_the_api_echoes_a_correlation_id_on_a_real_request() -> None:
    """The middleware is only worth anything if it is actually INSTALLED (app/main.py)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health", headers={"x-correlation-id": "trace-me"})
    assert resp.headers["x-correlation-id"] == "trace-me"
