"""The sync side's persist half.

The queue and the drain are tested without Postgres by substituting the write: what matters here
is the plumbing (batching, the sentinel, a failure that does not take the run down, the install
that makes a bare client record), not the SQL, which `apps/api/tests/test_api_log.py` exercises
against a real database as `book_writer`.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from api_log import ApiCall, clear_default_recorder, default_recorder

from book_sink.api_log import SyncApiLog, _as_json, _params, install_api_log
from book_sink.config import SinkConfig


class _Collecting(SyncApiLog):
    """A SyncApiLog whose write goes to a list instead of Postgres."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("postgresql://unused", **kwargs)
        self.written: list[ApiCall] = []
        self.batches: list[int] = []
        self.done = threading.Event()

    def _write(self, batch: list[ApiCall]) -> None:
        self.written.extend(batch)
        self.batches.append(len(batch))
        self.done.set()


@pytest.fixture(autouse=True)
def _no_leaked_default() -> Any:
    """The default recorder is a process global; a leak from one test silently changes another."""
    yield
    clear_default_recorder()


class TestDrain:
    def test_a_call_is_written(self) -> None:
        sink = _Collecting()
        sink.start()
        try:
            sink(ApiCall(provider="seccl", operation="get_clients"))
            assert sink.done.wait(timeout=2)
        finally:
            sink.stop()
        assert [c.operation for c in sink.written] == ["get_clients"]

    def test_stop_flushes_what_is_queued(self) -> None:
        """A run that ends must not discard the tail of its own audit trail."""
        sink = _Collecting()
        sink.start()
        for i in range(20):
            sink(ApiCall(provider="seccl", operation=f"op{i}"))
        sink.stop()
        assert len(sink.written) == 20

    def test_calls_are_batched(self) -> None:
        """One INSERT per row would make the drain the slowest thing in a sync run."""
        sink = _Collecting()
        for i in range(50):
            sink(ApiCall(provider="sei", operation=f"op{i}"))
        sink.start()  # started AFTER queueing, so the drain finds them all waiting
        sink.stop()
        assert len(sink.written) == 50
        assert max(sink.batches) > 1, f"expected batching, got {sink.batches}"

    def test_a_failing_write_does_not_kill_the_drain(self) -> None:
        """One unwritable row must not silence the audit trail for the rest of the run."""
        calls: list[ApiCall] = []

        class _Flaky(SyncApiLog):
            def __init__(self) -> None:
                super().__init__("postgresql://unused")
                self.first = True

            def _write(self, batch: list[ApiCall]) -> None:
                if self.first:
                    self.first = False
                    raise RuntimeError("deadlock")
                calls.extend(batch)

        sink = _Flaky()
        sink.start()
        sink(ApiCall(provider="sei", operation="doomed"))
        # Give the drain time to fail the first batch before queueing the second, so the two are
        # not merged into one.
        import time

        time.sleep(0.05)
        sink(ApiCall(provider="sei", operation="survives"))
        sink.stop()
        assert [c.operation for c in calls] == ["survives"]

    def test_a_full_queue_drops_rather_than_blocking(self) -> None:
        """A sync must never stall on its own audit trail."""
        sink = _Collecting()  # not started: nothing drains it
        sink._queue.maxsize = 2
        for i in range(5):
            sink(ApiCall(provider="sei", operation=f"op{i}"))
        assert sink._dropped == 3


class TestParams:
    def test_the_source_is_stamped(self) -> None:
        params = _params(ApiCall(provider="seccl"), "sync", None)
        assert params["source"] == "sync"

    def test_a_scheduled_pull_has_no_user(self) -> None:
        """Null is the honest answer, not a placeholder: nobody asked for this call."""
        params = _params(ApiCall(provider="seccl"), "sync", None)
        assert params["user_id"] is None

    def test_every_insert_column_is_supplied(self) -> None:
        """A column named in the SQL and missing from the params dict is a psycopg KeyError at
        run time, inside a swallowed handler — so nothing would report it."""
        import re

        from book_sink.api_log import _INSERT

        named = set(re.findall(r"%\((\w+)\)s", _INSERT))
        assert named == set(_params(ApiCall(provider="x"), "sync", None))

    def test_bodies_are_serialised_for_jsonb(self) -> None:
        from decimal import Decimal

        assert _as_json({"amount": Decimal("12.34")}) == '{"amount": "12.34"}'
        assert _as_json(None) is None
        assert _as_json("plain") == '{"value": "plain"}'


class TestInstall:
    def test_no_dsn_means_no_recorder(self) -> None:
        """`interlace plan` on a laptop has no book, exactly as `tenants.enabled` tolerates."""
        assert install_api_log(SinkConfig(book_dsn="", datalake_dsn="")) is None
        assert default_recorder() is None

    def test_installing_makes_it_the_process_default(self) -> None:
        """The one call that records a whole sync run: every client falls back to this."""
        sink = install_api_log(SinkConfig(book_dsn="postgresql://unused", datalake_dsn=""))
        assert sink is not None
        try:
            assert default_recorder() is sink
        finally:
            sink.stop()

    def test_the_installed_recorder_satisfies_the_protocol_positionally(self) -> None:
        """`Recorder.__call__` takes its argument positionally, so a bound method or a queue's
        `put` can serve. `SyncApiLog.__call__` must not require a keyword."""
        sink = _Collecting()
        sink(ApiCall(provider="sei"))  # positional; a TypeError here is the failure
        assert sink._queue.qsize() == 1
