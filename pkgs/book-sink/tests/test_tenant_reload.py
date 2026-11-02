"""A firm enabled after the daemon started must be in the next run's model graph.

Refresh posts `tag:tenant:{uid}`. interlace 400s when that tag matches nothing, which is
what a project compiled before the firm was enabled does. The wrapper recompiles when the
book's enabled set differs from the compiled tags, and leaves the graph alone when the
book cannot be read.
"""

from __future__ import annotations

import asyncio
from typing import Any

from book_sink import tenant_reload
from book_sink.tenant_reload import (
    enablement_drifted,
    install_tenant_reload,
    registered_tenant_uids,
)


class _Model:
    def __init__(self, tags: tuple[str, ...]) -> None:
        self.tags = tags


class _Compiled:
    def __init__(self, models: list[tuple[str, ...]]) -> None:
        self.models = {str(index): _Model(tags) for index, tags in enumerate(models)}


class _State:
    def __init__(self, compiled: _Compiled) -> None:
        self.compiled = compiled
        self.source_mtime = 10.0
        self.reload_lock = asyncio.Lock()


def test_registered_uids_ignore_the_plain_tenant_tag() -> None:
    compiled = _Compiled([("tenant:ten_abc", "tenant", "seccl"), ("raw", "seccl")])
    assert registered_tenant_uids(compiled) == {"ten_abc"}


def test_a_failed_read_is_not_drift(monkeypatch: Any) -> None:
    monkeypatch.setattr("book_sink.tenants.read_enabled", lambda _provider: None)
    compiled = _Compiled([("raw", "seccl")])
    assert enablement_drifted("seccl", compiled) is False


def test_a_newly_enabled_firm_is_drift(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "book_sink.tenants.read_enabled", lambda _provider: [type("S", (), {"uid": "ten_new"})()]
    )
    assert enablement_drifted("seccl", _Compiled([("raw", "seccl")])) is True


def test_an_unchanged_firm_is_not_drift(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "book_sink.tenants.read_enabled", lambda _provider: [type("S", (), {"uid": "ten_abc"})()]
    )
    assert enablement_drifted("seccl", _Compiled([("tenant:ten_abc", "tenant")])) is False


def _restore_reload(previous: Any) -> None:
    import sys

    import interlace.scheduler.daemon as daemon

    daemon.reload_if_stale = previous
    for module in list(sys.modules.values()):
        current = getattr(module, "reload_if_stale", None)
        if getattr(current, "_watches_tenants", False):
            module.reload_if_stale = previous  # type: ignore[attr-defined]


def test_reload_rebinds_modules_that_imported_the_original(monkeypatch: Any) -> None:
    """HTTP routes bind reload_if_stale at import, before model discovery.

    Replacing only the scheduler attribute leaves those routes on the original,
    so a firm attached after startup still 400s.
    """
    import sys
    import types

    import interlace.scheduler.daemon as daemon

    previous = daemon.reload_if_stale
    route = types.ModuleType("book_sink_test_route")
    route.reload_if_stale = previous  # type: ignore[attr-defined]
    sys.modules[route.__name__] = route

    async def original(_state: _State) -> None:
        return None

    monkeypatch.setattr(daemon, "reload_if_stale", original)
    route.reload_if_stale = original  # type: ignore[attr-defined]
    try:
        install_tenant_reload("seccl")
        assert daemon.reload_if_stale is route.reload_if_stale
        assert getattr(route.reload_if_stale, "_watches_tenants", False) is True
    finally:
        _restore_reload(previous)
        sys.modules.pop(route.__name__, None)


def test_reload_recompiles_when_enablement_changes(monkeypatch: Any) -> None:
    import interlace.scheduler.daemon as daemon

    previous = daemon.reload_if_stale
    compiled = _Compiled([("raw", "seccl")])
    state = _State(compiled)
    calls: list[float] = []

    async def original(current: _State) -> None:
        calls.append(current.source_mtime)
        if current.source_mtime < 0:
            current.compiled = _Compiled([("tenant:ten_new", "tenant", "seccl")])
            current.source_mtime = 10.0

    def drifted(_provider: str, graph: _Compiled) -> bool:
        return "ten_new" not in registered_tenant_uids(graph)

    monkeypatch.setattr(daemon, "reload_if_stale", original)
    monkeypatch.setattr(tenant_reload, "enablement_drifted", drifted)
    try:
        install_tenant_reload("seccl")
        install_tenant_reload("seccl")
        asyncio.run(daemon.reload_if_stale(state))
    finally:
        _restore_reload(previous)

    assert calls == [10.0, -1.0]
    assert registered_tenant_uids(state.compiled) == {"ten_new"}


def test_reload_leaves_the_graph_when_enablement_matches(monkeypatch: Any) -> None:
    import interlace.scheduler.daemon as daemon

    previous = daemon.reload_if_stale
    state = _State(_Compiled([("tenant:ten_abc", "tenant")]))
    calls: list[float] = []

    async def original(current: _State) -> None:
        calls.append(current.source_mtime)

    monkeypatch.setattr(daemon, "reload_if_stale", original)
    monkeypatch.setattr(tenant_reload, "enablement_drifted", lambda *_: False)
    try:
        install_tenant_reload("seccl")
        asyncio.run(daemon.reload_if_stale(state))
    finally:
        _restore_reload(previous)

    assert calls == [10.0]
