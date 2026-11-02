"""A reset must leave the stream landing tables in place.

The wipe drops the ``streams`` schema. The overview and the scheduler both read
``streams._watermarks``, so a rebuild that stops there turns the platform page into
a 500. The installer wraps the wipe and creates the tables again.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from interlace.dsl.decorators import REGISTRY, StreamDef
from interlace.engines.base import EngineAdapter
from interlace.state.janitor import ResetResult
from interlace.state.store import SqliteStateStore

from book_sink.stream_restore import install_stream_restore


def _unwrap(previous: Any) -> None:
    import interlace.state.janitor as janitor

    janitor.reset = previous


def test_a_reset_recreates_the_stream_tables(monkeypatch: Any) -> None:
    import interlace.state.janitor as janitor

    previous = janitor.reset
    created: list[tuple[list[str], object]] = []
    engine = cast(EngineAdapter, object())
    state = cast(SqliteStateStore, object())

    async def original(*_args: object, **_kwargs: object) -> ResetResult:
        return ResetResult()

    async def ensure(targets: list[StreamDef], adapter: object) -> None:
        created.append(([target.name for target in targets], adapter))

    monkeypatch.setattr(janitor, "reset", original)
    monkeypatch.setattr("book_sink.stream_restore.ensure_stream_tables", ensure)
    REGISTRY.streams["clients"] = StreamDef(name="clients", schema={"id": "text"})
    try:
        install_stream_restore()
        install_stream_restore()
        result = asyncio.run(janitor.reset(state, engines={"default": engine}))
    finally:
        REGISTRY.streams.pop("clients", None)
        _unwrap(previous)

    assert result.dry_run is False
    assert created == [(["clients"], engine)]


def test_a_dry_run_does_not_create_tables(monkeypatch: Any) -> None:
    import interlace.state.janitor as janitor

    previous = janitor.reset
    created: list[object] = []

    async def original(*_args: object, dry_run: bool = False, **_kwargs: object) -> ResetResult:
        return ResetResult(dry_run=dry_run)

    async def ensure(*_args: object, **_kwargs: object) -> None:
        created.append(True)

    monkeypatch.setattr(janitor, "reset", original)
    monkeypatch.setattr("book_sink.stream_restore.ensure_stream_tables", ensure)
    REGISTRY.streams["clients"] = StreamDef(name="clients", schema={"id": "text"})
    try:
        install_stream_restore()
        result = asyncio.run(
            janitor.reset(
                cast(SqliteStateStore, object()),
                engines={"default": cast(EngineAdapter, object())},
                dry_run=True,
            )
        )
    finally:
        REGISTRY.streams.pop("clients", None)
        _unwrap(previous)

    assert result.dry_run is True
    assert created == []
