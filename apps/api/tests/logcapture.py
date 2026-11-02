"""Capture log records from a named logger, robustly — a ``caplog`` replacement.

``caplog`` cannot be trusted in this suite. The DB-backed tests bootstrap the schema by running
Alembic IN THIS PROCESS, and Alembic's ``fileConfig`` call disables every logger that already
exists (``logging.config.fileConfig`` defaults to ``disable_existing_loggers=True``). From that
point on ``app.logging``, ``app.ratelimit`` and friends are ``disabled`` and propagate nowhere, so
a ``caplog``-based assertion captures nothing and fails — but only when the test order happens to
put a DB test first, which is how it passes locally and breaks in the full run.

This attaches a handler to the logger under test and re-enables it for the duration, restoring
everything afterwards. See ``tests/test_datasets_sql_security.py`` for the same workaround inline.

The underlying defect is worth fixing at the source: ``migrations/env.py`` should pass
``disable_existing_loggers=False`` to ``fileConfig``, since a migration has no business silencing
the application's loggers.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator


class _Collector(logging.Handler):
    def __init__(self, level: int) -> None:
        super().__init__(level=level)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextlib.contextmanager
def captured(name: str, *, level: int = logging.DEBUG) -> Iterator[list[logging.LogRecord]]:
    """Records emitted on logger ``name`` while the block runs, oldest first."""
    log = logging.getLogger(name)
    handler = _Collector(level)
    previous = (log.level, log.disabled, log.propagate)
    log.addHandler(handler)
    log.setLevel(level)
    log.disabled = False

    # AND EVERY DESCENDANT, because `fileConfig` disabled each of them individually and a disabled
    # logger emits nothing — it never reaches the point of propagating to this handler. So naming a
    # PARENT logger has to re-enable the ones beneath it, or the capture is silently empty for
    # exactly the tests that watch a feature rather than a single module.
    prefix = f"{name}."
    descendants = [
        child
        for child_name, child in logging.root.manager.loggerDict.items()
        if child_name.startswith(prefix) and isinstance(child, logging.Logger)
    ]
    was_disabled = [(child, child.disabled) for child in descendants]
    for child in descendants:
        child.disabled = False
    try:
        yield handler.records
    finally:
        log.removeHandler(handler)
        log.setLevel(previous[0])
        log.disabled = previous[1]
        log.propagate = previous[2]
        for child, disabled in was_disabled:
            child.disabled = disabled


def messages(records: list[logging.LogRecord]) -> list[str]:
    return [r.getMessage() for r in records]
