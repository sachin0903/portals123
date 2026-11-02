"""Core-catalogue pool: one provider, one thread, reuse the attach, discard on error."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import providers
from app.lake import core as lake_core
from app.lake import holdings as lake_holdings
from app.lake.pool import CorePool


class _Conn:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _opener(opened: list[str]):
    def opener(provider: str) -> _Conn:
        opened.append(provider)
        return _Conn(provider)

    return opener


def test_two_checkouts_are_different_connections() -> None:
    opened: list[str] = []
    pool = CorePool(_opener(opened), max_total=3)
    first = pool.acquire("seccl")
    second = pool.acquire("seccl")
    assert first is not second
    assert first.conn is not second.conn
    assert first.in_use and second.in_use
    assert opened == ["seccl", "seccl"]
    pool.release(first)
    pool.release(second)


def test_a_released_connection_is_reused() -> None:
    opened: list[str] = []
    pool = CorePool(_opener(opened), max_total=3)
    first = pool.acquire("seccl")
    pool.release(first)
    second = pool.acquire("seccl")
    assert second is first
    assert opened == ["seccl"]
    assert first.conn.closed is False
    pool.release(second)


def test_a_connection_is_not_shared_across_providers() -> None:
    opened: list[str] = []
    pool = CorePool(_opener(opened), max_total=3)
    seccl = pool.acquire("seccl")
    pool.release(seccl)
    sei = pool.acquire("sei")
    assert sei is not seccl
    assert sei.conn.name == "sei"
    assert opened == ["seccl", "sei"]
    pool.release(sei)


def test_a_failed_body_discards_the_connection() -> None:
    opened: list[str] = []
    pool = CorePool(_opener(opened), max_total=2)
    pooled = pool.acquire("seccl")
    pool.release(pooled, discard=True)
    assert pooled.conn.closed is True
    assert pool.stats()["total"] == 0
    again = pool.acquire("seccl")
    assert again is not pooled
    assert opened == ["seccl", "seccl"]
    pool.release(again)


def test_the_cap_waits_until_a_connection_is_released() -> None:
    opened: list[str] = []
    pool = CorePool(_opener(opened), max_total=2)
    first = pool.acquire("seccl")
    second = pool.acquire("seccl")
    started = threading.Event()
    finished = threading.Event()

    def third() -> None:
        started.set()
        pooled = pool.acquire("seccl")
        finished.set()
        pool.release(pooled)

    worker = threading.Thread(target=third)
    worker.start()
    assert started.wait(1)
    worker.join(0.05)
    assert not finished.is_set()
    pool.release(first)
    assert finished.wait(1)
    worker.join(1)
    pool.release(second)
    assert opened == ["seccl", "seccl"]


def test_an_opener_failure_frees_the_slot() -> None:
    calls = {"n": 0}

    def flaky(provider: str) -> _Conn:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("attach failed")
        return _Conn(provider)

    pool = CorePool(flaky, max_total=1)
    with pytest.raises(RuntimeError):
        pool.acquire("seccl")
    assert pool.stats() == {"total": 0, "in_use": 0}
    pooled = pool.acquire("seccl")
    assert pooled.conn.name == "seccl"
    pool.release(pooled)


def test_read_slice_discards_a_connection_whose_query_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    class _Boom:
        def execute(self, sql: str, params: list[Any] | None = None) -> Any:
            del sql, params
            raise RuntimeError("Catalog Error: positions does not exist")

        def close(self) -> None:
            return None

    class _Ok:
        def execute(self, sql: str, params: list[Any] | None = None) -> Any:
            del sql, params

            class _Cursor:
                description = [("account_external_id",)]

                def fetchall(self) -> list[tuple[str]]:
                    return [("ACC1",)]

            return _Cursor()

        def close(self) -> None:
            return None

    def _connect(provider: str) -> Any:
        del provider
        calls["n"] += 1
        return _Boom() if calls["n"] == 1 else _Ok()

    monkeypatch.setattr(lake_core, "connect", _connect)
    with pytest.raises(RuntimeError):
        lake_holdings.read_slice(
            "seccl", "ten_abc123", "SELECT 1", [], statement="positions", relation="positions"
        )
    rows = lake_holdings.read_slice(
        "seccl", "ten_abc123", "SELECT 1", [], statement="positions", relation="positions"
    )
    assert rows == [{"account_external_id": "ACC1"}]
    assert calls["n"] == 2


def test_read_slice_spans_acquire_separately_from_the_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(lake_holdings, "_tracer", lambda: provider.get_tracer("portals-api"))

    class _Cursor:
        description = [("n",)]

        def fetchall(self) -> list[tuple[int]]:
            return [(1,)]

    class _Quiet:
        def execute(self, sql: str, params: list[Any] | None = None) -> _Cursor:
            del sql, params
            return _Cursor()

        def close(self) -> None:
            return None

    monkeypatch.setattr(lake_core, "connect", lambda _provider: _Quiet())
    lake_holdings.read_slice(
        "seccl",
        "ten_secretuid",
        "SELECT 1 FROM {relation}",
        [],
        statement="positions_summary",
        relation="positions",
    )
    spans = list(exporter.get_finished_spans())
    assert [span.name for span in spans] == ["lake.core.acquire", "lake.core.query"]
    assert spans[0].end_time is not None and spans[1].start_time is not None
    assert spans[0].end_time <= spans[1].start_time
    for span in spans:
        attrs = dict(span.attributes or {})
        assert attrs["lake.provider"] == "seccl"
        assert attrs["lake.statement"] == "positions_summary"
        blob = " ".join(str(value) for value in attrs.values())
        assert "secretuid" not in blob
        assert "SELECT" not in blob


async def test_holdings_summary_span_parents_the_warehouse_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DuckDB wait is its own span on the request, and the acquire/query nest under it.

    Started inside the worker thread alone, those two spans were not children of the request,
    so the holdings summary showed up as an empty gap.
    """
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(lake_holdings, "_tracer", lambda: provider.get_tracer("portals-api"))

    class _Cursor:
        description = [("n",)]

        def fetchall(self) -> list[tuple[int]]:
            return [(1,)]

    class _Quiet:
        def execute(self, sql: str, params: list[Any] | None = None) -> _Cursor:
            del sql, params
            return _Cursor()

        def close(self) -> None:
            return None

    monkeypatch.setattr(lake_core, "connect", lambda _provider: _Quiet())
    await lake_holdings.firm_holdings_summary("seccl", "ten_secretuid", ["ACC"])
    spans = {span.name: span for span in exporter.get_finished_spans()}
    summary = spans["lake.holdings.summary"]
    assert spans["lake.core.query"].parent is not None
    assert spans["lake.core.query"].parent.span_id == summary.context.span_id
    assert spans["lake.core.acquire"].parent is not None
    assert spans["lake.core.acquire"].parent.span_id == summary.context.span_id
    attrs = dict(summary.attributes or {})
    assert attrs["lake.provider"] == "seccl"
    assert attrs["lake.statement"] == "positions_summary"
    assert attrs["lake.accounts"] == 1
    blob = " ".join(str(value) for value in attrs.values())
    assert "secretuid" not in blob
    assert "SELECT" not in blob


# --- startup warm-up ------------------------------------------------------------------------
#
# The first core read in a fresh process pays INSTALL (an extension download per container)
# plus the attach, measured at several seconds. `warm` moves that to startup. It must never
# fail startup, and what it opens must be what the first request then reuses.


def test_warm_without_a_catalogue_configured_does_not_raise() -> None:
    # `_storage_unconfigured` (conftest) has blanked DATALAKE_CATALOG_DSN, so `connect` raises
    # CoreUnavailable — the state every deployment without a warehouse boots in.
    assert lake_core.warm(["seccl", "sei"]) == {"seccl": False, "sei": False}
    assert lake_core._get_pool().stats() == {"total": 0, "in_use": 0}


def test_warm_leaves_one_idle_connection_per_provider_for_the_first_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(lake_core, "connect", _opener(opened))

    assert lake_core.warm(["sei", "seccl"]) == {"sei": True, "seccl": True}
    assert opened == ["sei", "seccl"]
    assert lake_core._get_pool().stats() == {"total": 2, "in_use": 0}

    # The request path's first checkout reuses the warmed connection rather than opening.
    conn = lake_core.checkout("sei")
    assert conn.name == "sei"
    assert opened == ["sei", "seccl"]
    lake_core.checkin(conn, discard=False)


def test_warm_skips_a_provider_that_cannot_open_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    inner = _opener(opened)

    def flaky(provider: str) -> _Conn:
        if provider == "seccl":
            raise RuntimeError("schema seccl does not exist")
        return inner(provider)

    monkeypatch.setattr(lake_core, "connect", flaky)
    assert lake_core.warm(["seccl", "sei"]) == {"seccl": False, "sei": True}
    assert lake_core._get_pool().stats() == {"total": 1, "in_use": 0}


def test_warm_order_puts_custodians_first_within_the_pool_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings

    order = lake_core.warm_order()
    assert set(order) <= set(providers.PROVIDERS)
    custodians = set(providers.custodians())
    seen_administrator = False
    for provider in order:
        if provider in custodians:
            assert not seen_administrator, "a custodian was ordered after a scheme administrator"
        else:
            seen_administrator = True
    # The cap bounds it, but never below the floor the pool itself refuses to go under.
    monkeypatch.setattr(settings, "lake_core_pool_max", 1)
    assert len(lake_core.warm_order()) == 3
