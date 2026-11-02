"""Recompile the daemon when a firm's enablement changes.

interlace compiles the project at startup and again when a model file's mtime moves.
Enablement is a row in the book, not a file, so a firm attached after startup has no
`tenant:{uid}` tag. Refresh posts that tag, and interlace answers 400 (`tag … matches
no models`) instead of running anything.

Each provider's `models/tenants.py` installs a wrapper around
`interlace.scheduler.daemon.reload_if_stale`. The
wrapper still honours file edits, then compares the compiled `tenant:` tags with
`read_enabled`. A difference forces the same recompile a file edit would, which
re-runs `register` and so re-reads the book. A failed read is ignored: it must not
look like every firm was turned off.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("book_sink.tenant_reload")

_TENANT_TAG = "tenant:"


def registered_tenant_uids(compiled: Any) -> set[str]:
    """Uids of the per-firm models already in the compiled project."""
    uids: set[str] = set()
    for model in compiled.models.values():
        for tag in model.tags:
            if tag.startswith(_TENANT_TAG):
                uid = tag[len(_TENANT_TAG) :]
                if uid:
                    uids.add(uid)
    return uids


def enablement_drifted(provider: str, compiled: Any) -> bool:
    """Whether the book's enabled firms differ from the compiled `tenant:` tags.

    False when the book cannot be read, so a blip does not rebuild the graph.

    The reader is looked up on the tenants module rather than imported here. Model
    discovery re-executes `models/tenants.py` inside an already-running daemon, and that
    file imports this module. A top-level import of a function added in the same change
    would fail against the copy of `book_sink.tenants` that process already loaded.
    """
    import importlib

    from book_sink import tenants as tenants_mod

    # A daemon that imported `tenants` before `read_enabled` existed keeps that
    # module until restart. Reload it once so this process can see new firms;
    # a normal start already has the attribute and skips this.
    if getattr(tenants_mod, "read_enabled", None) is None:
        tenants_mod = importlib.reload(tenants_mod)
    live = tenants_mod.read_enabled(provider)
    if live is None:
        return False
    return {slice_.uid for slice_ in live} != registered_tenant_uids(compiled)


def install_tenant_reload(provider: str) -> None:
    """Wrap the daemon's reload so the next request picks up a new or removed firm.

    Idempotent: model discovery re-executes `tenants.py` on every recompile, and a
    second wrap would recurse.

    As of interlace 2.8.2 the function lives on ``interlace.scheduler.daemon``.
    The HTTP routes bind that name when they are imported, which is before model
    discovery, so the wrapper is copied onto any module that already holds it.
    """
    import sys

    import interlace.scheduler.daemon as daemon

    from book_sink.stream_restore import install_stream_restore

    # Before the early return: a recompile hits that return, and the stream
    # wrapper has to be in place from the first discovery.
    install_stream_restore()

    current = daemon.reload_if_stale
    if getattr(current, "_watches_tenants", False):
        return

    async def reload_if_stale(state: Any) -> None:
        await current(state)
        async with state.reload_lock:
            if not enablement_drifted(provider, state.compiled):
                return
            # `reload_if_stale` recompiles only when this is older than the newest
            # model file. Forcing it backwards reuses that path, which re-imports
            # `models/tenants.py` and so re-reads enablement.
            logger.info("book_sink: tenant enablement changed for %s; reloading models", provider)
            state.source_mtime = -1.0
        await current(state)

    reload_if_stale._watches_tenants = True  # type: ignore[attr-defined]
    daemon.reload_if_stale = reload_if_stale
    for module in list(sys.modules.values()):
        if getattr(module, "reload_if_stale", None) is current:
            module.reload_if_stale = reload_if_stale  # type: ignore[attr-defined]
