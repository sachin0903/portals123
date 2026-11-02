"""Put the stream landing tables back after a reset.

``POST /reset`` (and ``interlace reset``) drops the ``streams`` schema. The daemon
creates those tables only while it is starting, so a rebuild leaves ``GET /streams``
and the scheduler reading a schema that is no longer there. Model discovery is the
one startup every invocation shares, so the restore is installed from there, the
same way tenant reload is.
"""

from __future__ import annotations

from typing import Any

from interlace.dsl.decorators import REGISTRY
from interlace.engines.registry import as_registry
from interlace.streaming.materializer import ensure_stream_tables, quarantine_stream


def install_stream_restore() -> None:
    """Wrap ``janitor.reset`` so a real wipe recreates the stream tables.

    Idempotent: model discovery re-executes the installer on every recompile.
    A dry run reports the drop and must not create anything.
    """
    import interlace.state.janitor as janitor

    current = janitor.reset
    if getattr(current, "_restores_streams", False):
        return

    async def reset(*args: Any, **kwargs: Any) -> Any:
        result = await current(*args, **kwargs)
        if kwargs.get("dry_run"):
            return result
        streams = list(REGISTRY.streams.values())
        if not streams:
            return result
        shadows = [stream for stream in streams if stream.on_schema_drift == "quarantine"]
        targets = [*streams, *(quarantine_stream(stream) for stream in shadows)]
        registry = as_registry(kwargs.get("engine"), kwargs.get("engines"))
        await ensure_stream_tables(targets, registry.require(registry.default))
        return result

    reset._restores_streams = True  # type: ignore[attr-defined]
    janitor.reset = reset
