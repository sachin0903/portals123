"""Daemon start: ``--no-apply``, then one full ``POST /runs`` — the logic, not a live serve."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from book_sink.startup_run import (
    bind_from_argv,
    debounce_from_env,
    env_enabled,
    loopback_base,
    run_catch_up,
    serve_argv,
    skip_reason,
    wait_healthy,
)


class _FakeHttp:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, bytes | None]] = []

    def request(
        self, method: str, url: str, *, body: bytes | None = None, timeout: float
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, body))
        if not self.responses:
            raise AssertionError(f"unexpected {method} {url}")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        if not isinstance(item, tuple) or len(item) != 2:
            raise AssertionError(f"bad fake response: {item!r}")
        status, payload = item
        if isinstance(payload, bytes):
            return int(status), payload
        return int(status), json.dumps(payload).encode()


def _now() -> datetime:
    return datetime(2026, 9, 25, 14, 0, tzinfo=UTC)


class TestEnvEnabled:
    def test_unset_and_blank_are_on(self) -> None:
        assert env_enabled(None) is True
        assert env_enabled("") is True
        assert env_enabled("  ") is True

    def test_explicit_off(self) -> None:
        for value in ("0", "false", "NO", "off"):
            assert env_enabled(value) is False

    def test_explicit_on(self) -> None:
        for value in ("1", "true", "YES", "on"):
            assert env_enabled(value) is True


class TestDebounceFromEnv:
    def test_blank_is_thirty_minutes(self) -> None:
        assert debounce_from_env(None) == timedelta(minutes=30)
        assert debounce_from_env("") == timedelta(minutes=30)

    def test_junk_falls_back(self) -> None:
        assert debounce_from_env("nope") == timedelta(minutes=30)

    def test_zero_is_allowed(self) -> None:
        assert debounce_from_env("0") == timedelta(0)


class TestBindFromArgv:
    def test_defaults(self) -> None:
        assert bind_from_argv([]) == ("127.0.0.1", 8000)

    def test_space_and_equals_forms(self) -> None:
        assert bind_from_argv(["--host", "0.0.0.0", "--port", "8103"]) == ("0.0.0.0", 8103)
        assert bind_from_argv(["--host=::", "--port=8100"]) == ("::", 8100)


class TestLoopbackBase:
    def test_wildcard_becomes_localhost(self) -> None:
        assert loopback_base("0.0.0.0", 8103) == "http://127.0.0.1:8103"
        assert loopback_base("::", 8100) == "http://127.0.0.1:8100"

    def test_explicit_host_is_kept(self) -> None:
        assert loopback_base("127.0.0.1", 8103) == "http://127.0.0.1:8103"


class TestSkipReason:
    def test_empty_enqueues(self) -> None:
        assert skip_reason([], now=_now(), debounce=timedelta(minutes=30)) is None

    def test_in_flight_wins(self) -> None:
        runs: list[dict[str, Any]] = [
            {"state": "running", "enqueued_at": "2026-09-25T13:00:00+00:00"}
        ]
        assert skip_reason(runs, now=_now(), debounce=timedelta(minutes=30)) == "in-flight"

    def test_recent_success_is_debounced(self) -> None:
        runs: list[dict[str, Any]] = [
            {"state": "succeeded", "enqueued_at": "2026-09-25T13:45:00+00:00"}
        ]
        assert skip_reason(runs, now=_now(), debounce=timedelta(minutes=30)) == "debounced"

    def test_last_nights_success_enqueues(self) -> None:
        """The empty-warehouse-after-reset case: sqlite still has last night's success."""
        runs: list[dict[str, Any]] = [
            {"state": "succeeded", "enqueued_at": "2026-09-24T05:00:00+00:00"}
        ]
        assert skip_reason(runs, now=_now(), debounce=timedelta(minutes=30)) is None

    def test_recent_failure_enqueues(self) -> None:
        runs: list[dict[str, Any]] = [
            {"state": "failed", "enqueued_at": "2026-09-25T13:50:00+00:00"}
        ]
        assert skip_reason(runs, now=_now(), debounce=timedelta(minutes=30)) is None

    def test_naive_stamp_is_treated_as_utc(self) -> None:
        runs: list[dict[str, Any]] = [{"state": "succeeded", "enqueued_at": "2026-09-25T13:50:00"}]
        assert skip_reason(runs, now=_now(), debounce=timedelta(minutes=30)) == "debounced"


class TestServeArgv:
    def test_startup_apply_is_off(self) -> None:
        assert serve_argv(["--host", "0.0.0.0", "--port", "8103", "--allow-open"]) == [
            "--host",
            "0.0.0.0",
            "--port",
            "8103",
            "--allow-open",
            "--no-apply",
        ]

    def test_an_explicit_apply_is_replaced(self) -> None:
        assert serve_argv(["--apply", "--port", "8100"]) == ["--port", "8100", "--no-apply"]

    def test_no_apply_is_not_duplicated(self) -> None:
        assert serve_argv(["--no-apply"]) == ["--no-apply"]


class TestWaitHealthy:
    def test_times_out(self) -> None:
        http = _FakeHttp([OSError("down"), OSError("down")])
        slept: list[float] = []
        ticks = iter([0.0, 0.5, 1.1])
        assert (
            wait_healthy(
                "http://127.0.0.1:9",
                http=http,
                wait=slept.append,
                timeout=1.0,
                poll=0.5,
                now=lambda: next(ticks),
            )
            is False
        )

    def test_returns_on_200(self) -> None:
        http = _FakeHttp([(200, {"status": "ok"})])
        assert (
            wait_healthy(
                "http://127.0.0.1:8103",
                http=http,
                wait=lambda _: None,
                timeout=10.0,
                poll=0.5,
                now=lambda: 0.0,
            )
            is True
        )

    def test_a_starting_apply_is_not_ready(self) -> None:
        http = _FakeHttp([(200, {"status": "starting"}), (200, {"status": "ok"})])
        ticks = iter([0.0, 0.4, 0.4])
        assert (
            wait_healthy(
                "http://127.0.0.1:8103",
                http=http,
                wait=lambda _: None,
                timeout=10.0,
                poll=0.5,
                now=lambda: next(ticks),
            )
            is True
        )
        assert http.calls[0][0] == "GET"
        assert len(http.calls) == 2


class TestRunCatchUp:
    def test_disabled_does_not_touch_http(self) -> None:
        http = _FakeHttp([])
        assert (
            run_catch_up(
                "http://127.0.0.1:8103",
                enabled=False,
                debounce=timedelta(minutes=30),
                now=_now(),
                http=http,
            )
            == "disabled"
        )
        assert http.calls == []

    def test_enqueues_when_the_queue_is_empty(self) -> None:
        http = _FakeHttp(
            [
                (200, {"status": "ok"}),
                (200, []),
                (200, {"enqueued": 1, "models": ["raw.files", "raw.accounts"]}),
            ]
        )
        assert (
            run_catch_up(
                "http://127.0.0.1:8103",
                enabled=True,
                debounce=timedelta(minutes=30),
                now=_now(),
                http=http,
                wait=lambda _: None,
                clock=lambda: 0.0,
            )
            == "enqueued"
        )
        assert http.calls[-1] == ("POST", "http://127.0.0.1:8103/runs", b"{}")

    def test_skips_when_a_run_is_in_flight(self) -> None:
        http = _FakeHttp(
            [
                (200, {"status": "ok"}),
                (200, [{"state": "queued", "enqueued_at": "2026-09-25T13:59:00+00:00"}]),
            ]
        )
        assert (
            run_catch_up(
                "http://127.0.0.1:8103",
                enabled=True,
                debounce=timedelta(minutes=30),
                now=_now(),
                http=http,
                wait=lambda _: None,
                clock=lambda: 0.0,
            )
            == "in-flight"
        )
        assert all(method != "POST" for method, _, _ in http.calls)
