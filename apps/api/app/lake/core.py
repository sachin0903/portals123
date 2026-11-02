"""Read-only access to a provider's CORE catalogue — the whole-firm warehouse.

The sibling of `attach.py`, which opens a single TENANT's own catalogue. This one opens the
provider's, where every firm's conformed data lives side by side and a firm's slice is a SCHEMA
(`tenant_{uid}.{relation}`) computed by that provider's own chain. Isolation is that schema
name, not a connection per firm.

It lived in `app/platforms.py` as a private `_core_connect_sync`, and `app/platform_entities.py`
reached across a module boundary to import it while its own docstring claimed to read "through
`app.lake`'s read-only attach". Here, that sentence is true.

READ-ONLY, as `lake_reader`. Nothing in the request path may write the warehouse: the provider's
daemon owns it, and a second writer is how two systems come to disagree about a firm's slice.

DuckDB connections are not thread-safe, and these reads run on worker threads, so a connection
is checked out to one thread at a time. Opening a new database on every call is a different
cost — extensions, the S3 secret, the catalogue attach — and that is what the pool avoids.
`connect` remains for a caller that truly needs a private connection.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Iterable
from typing import Any

from app import providers
from app.config import settings
from app.lake.attach import attach_ducklake_sql, open_memory_db, s3_secret_sql
from app.lake.pool import CorePool

logger = logging.getLogger(__name__)

# Home gathers holdings, transactions and the value series at once. Each holds its own
# connection, so a smaller cap only queues that page.
_POOL_FLOOR = 3

_pool: CorePool | None = None
_live: dict[int, Any] = {}
_live_lock = threading.Lock()


class CoreUnavailable(RuntimeError):
    """The core catalogue is not configured, or cannot be opened."""


def connect(provider: str) -> Any:
    """A fresh in-memory DuckDB attached READ_ONLY to the provider's core catalogue.

    The pool's opener, and any caller that needs a connection the pool must not see again.
    Request paths borrow via :func:`checkout` instead of calling this on every read.
    """
    if not settings.datalake_catalog_dsn:
        raise CoreUnavailable("DATALAKE_CATALOG_DSN not configured")
    conn = open_memory_db()
    for ext in ("ducklake", "postgres", "httpfs"):
        conn.execute(f"INSTALL {ext}; LOAD {ext};")
    conn.execute(
        s3_secret_sql(
            "core_s3",
            key_id=settings.s3_access_key_id,
            secret=settings.s3_secret_access_key,
            scope="s3://" + settings.s3_core_bucket if settings.s3_core_bucket else "",
            endpoint=settings.s3_endpoint_url,
        )
    )
    conn.execute(
        attach_ducklake_sql(
            "core",
            settings.datalake_catalog_dsn,
            metadata_schema=providers.provider_schema(provider),
        )
    )
    conn.execute("USE core")
    return conn


def _opener(provider: str) -> Any:
    # Looked up at call time so a test can replace ``connect``.
    return connect(provider)


def _get_pool() -> CorePool:
    global _pool
    if _pool is None:
        _pool = CorePool(_opener, max_total=max(_POOL_FLOOR, settings.lake_core_pool_max))
    return _pool


def reset_pool() -> None:
    """Drop every pooled core connection. Tests, and process shutdown."""
    global _pool
    with _live_lock:
        _live.clear()
    if _pool is not None:
        _pool.close()
        _pool = None


def warm_order() -> tuple[str, ...]:
    """The providers to pre-open at startup, custodians first.

    The custodians' catalogues serve the request path — every cash and growth figure on the
    clients and accounts listings is `tenant_{uid}.positions` in one of them — so they are
    warmed before a scheme administrator's, whose catalogue only the admin console reads.
    Bounded by the pool cap: a provider past it would only evict one just opened.
    """
    custodians = [p for p in providers.PROVIDERS if p in providers.custodians()]
    rest = [p for p in providers.PROVIDERS if p not in custodians]
    cap = max(_POOL_FLOOR, settings.lake_core_pool_max)
    return tuple((custodians + rest)[:cap])


def warm(for_providers: Iterable[str]) -> dict[str, bool]:
    """Open one pooled connection per provider ahead of the first read. Best-effort.

    The first `connect` in a fresh process pays for everything a later one does not: `INSTALL`
    downloads ducklake, postgres and httpfs when the container has no extension cache (about a
    second each, measured), then the S3 secret and the catalogue attach. Left to the request
    path, that is the first clients or accounts page after every deploy sitting on its
    skeleton for several seconds while the rest of the page is already on screen — and it
    reads as the listing being slow rather than as the process being new.

    Each provider is opened and released, so the connection is IDLE in the pool and the first
    checkout for that provider reuses it. Sequential on purpose: two connections installing the
    same extension at once would race on the one cache file. Never raises — an unconfigured
    catalogue, an unreachable warehouse or a provider whose schema does not exist yet is logged
    and skipped, because startup must not depend on the lake, which the reading endpoints
    already degrade without. Returns which providers were warmed, for the log and the tests.
    """
    wanted = list(for_providers)
    warmed: dict[str, bool] = dict.fromkeys(wanted, False)
    # The one pool for the whole pass: a `reset_pool` racing this thread must not have a
    # connection released into a pool it was never acquired from.
    pool = _get_pool()
    for provider in wanted:
        started = time.monotonic()
        try:
            pooled = pool.acquire(provider)
        except CoreUnavailable as exc:
            # Not configured is a state, not a fault: say so once, quietly, and stop — every
            # other provider would fail the same way for the same reason.
            logger.info("core catalogue pool not warmed: %s", exc)
            break
        except Exception:  # noqa: BLE001 - best-effort: nothing here may fail startup
            logger.warning("could not warm the %s core catalogue", provider, exc_info=True)
            continue
        pool.release(pooled)
        warmed[provider] = True
        logger.info(
            "warmed the %s core catalogue in %d ms",
            provider,
            int((time.monotonic() - started) * 1000),
        )
    return warmed


def checkout(provider: str) -> Any:
    """The provider's catalogue, for this thread only, until :func:`checkin`."""
    pooled = _get_pool().acquire(provider)
    with _live_lock:
        _live[id(pooled.conn)] = pooled
    return pooled.conn


def checkin(conn: Any, *, discard: bool) -> None:
    """Return a checked-out connection, or close it when ``discard`` is set.

    A connection this module did not check out — a test stub, or a private
    :func:`connect` — is closed. That is the contract the callers used to meet
    with ``conn.close()`` in a ``finally``.
    """
    with _live_lock:
        pooled = _live.pop(id(conn), None)
    if pooled is None:
        _close_quietly(conn)
        return
    _get_pool().release(pooled, discard=discard)


def _close_quietly(conn: Any) -> None:
    if conn is not None:
        with contextlib.suppress(Exception):
            conn.close()
