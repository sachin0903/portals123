"""Per-tenant DuckLake access, in-process (docs/datalake.md).

The lake is a library, not a service: API code acquires a tenant-scoped, read-only DuckDB
connection from the pool and gets results back as Arrow tables or pandas DataFrames — no
serialisation boundary. Joining lake data with OLTP data is a first-class pattern: fetch
the OLTP rows through the normal RLS-bound session, then ``register()`` the frame on the
lake connection and join in SQL.

    async with lake.connection(tenant_uid) as conn:
        await conn.register("platform_clients", oltp_dataframe)  # RLS-fetched
        table = await conn.arrow(
            "SELECT c.*, s.valuation FROM platform_clients c "
            "JOIN seccl.clients s ON s.client_ref = c.seccl_ref"
        )

Isolation: one tenant per pooled connection (READ_ONLY attach + bucket-scoped S3 secret) —
a connection cannot reach another tenant's lake regardless of the SQL it runs. Plain
OLTP-only queries don't come near this module; use the usual sessions.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.config import settings
from app.lake import attach
from app.lake.attach import LakeUnavailable, clear_config_cache, open_tenant_connection
from app.lake.pool import TenantPool

__all__ = [
    "LakeConnection",
    "LakeUnavailable",
    "clear_config_cache",
    "connection",
    "evict_tenant",
    "query",
]

_pool: TenantPool | None = None


def _get_pool() -> TenantPool:
    global _pool
    if _pool is None:
        _pool = TenantPool(
            open_tenant_connection,
            max_total=settings.lake_pool_max_connections,
            max_per_tenant=settings.lake_pool_max_per_tenant,
        )
    return _pool


async def reset_pool() -> None:
    """Testing/shutdown hook: close every pooled connection and drop the pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
    clear_config_cache()


async def evict_tenant(uid: str) -> None:
    """Suspension hook: drop the tenant's cached attach config and pooled connections so
    a suspended tenant stops resolving immediately, not after the config TTL."""
    attach.invalidate_config(uid)
    if _pool is not None:
        await _pool.evict_tenant(uid)


class LakeConnection:
    """A pooled, single-tenant, read-only lake connection. All calls run the sync DuckDB
    client in a thread. Results come back as Arrow (``arrow``), pandas (``df``), plain
    dicts (``rows``) or pydantic models (``models``)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._registered: set[str] = set()

    async def arrow(self, sql: str, params: list[Any] | None = None) -> Any:
        return await asyncio.to_thread(lambda: self._conn.execute(sql, params).fetch_arrow_table())

    async def df(self, sql: str, params: list[Any] | None = None) -> Any:
        """A pandas frame, materialised THROUGH ARROW rather than by DuckDB's own converter.

        One fetch path for the whole class: ``rows`` and ``models`` already go via ``arrow``, and
        this was the only method that did not — it called ``.df()``, which builds the frame from a
        dict of Python sequences (``DataFrame.from_dict``) and, under pandas 3, re-materialises
        every string column into a pyarrow array on the way. ``to_pandas`` on a table that is
        already Arrow skips that second conversion entirely.

        It is also the frame at the top of an intermittent SIGSEGV seen when the API suite runs
        alongside the rest of the monorepo's gates (``pandas.core.arrays.string_arrow
        ._from_sequence`` ← ``DataFrame.from_dict`` ← this lambda, on a worker thread). That crash
        has not been reproduced deliberately — five bare suite runs are clean and it has only
        appeared under a loaded ``moon run`` — so this is NOT claimed as its cure; what can be said
        is that the converter it happens inside is no longer reached, and that no application code
        calls this method at all (its only caller is ``tests/test_lake.py``).

        Both steps stay on the same worker thread: splitting them would put the pandas conversion
        back on the event loop, which is the one place a multi-second materialisation must not run.
        """
        return await asyncio.to_thread(
            lambda: self._conn.execute(sql, params).fetch_arrow_table().to_pandas()
        )

    async def rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        """Rows as plain dicts (Python natives via Arrow — dates, Decimals, etc.)."""
        table = await self.arrow(sql, params)
        return list(table.to_pylist())

    async def models[M](self, model: type[M], sql: str, params: list[Any] | None = None) -> list[M]:
        """Rows validated into a pydantic model — ready for a FastAPI ``response_model``
        (JSON when JSON is wanted; the frame surfaces stay for analytics)."""
        return [model.model_validate(row) for row in await self.rows(sql, params)]  # type: ignore[attr-defined]

    async def register(self, name: str, frame: Any) -> None:
        """Expose an Arrow table / pandas DataFrame (e.g. RLS-fetched OLTP rows) as a
        queryable relation on this connection, for lake↔OLTP joins. Registrations are
        connection-scoped, so ``connection()`` unregisters them before the connection
        returns to the pool — one request's RLS-scoped frame must never be queryable
        by the next request that reuses the connection."""
        await asyncio.to_thread(self._conn.register, name, frame)
        self._registered.add(name)

    async def unregister(self, name: str) -> None:
        await asyncio.to_thread(self._conn.unregister, name)
        self._registered.discard(name)

    async def _unregister_all(self) -> None:
        for name in list(self._registered):
            await self.unregister(name)

    async def _run_statements(self, statements: list[str]) -> None:
        def _run() -> None:
            for statement in statements:
                self._conn.execute(statement)

        await asyncio.to_thread(_run)

    async def _apply_claims(self, claims: dict[str, Any]) -> None:
        """Open the transaction-scoped RLS context and verify it took (the canary guards
        the silent failure mode: an OLTP connection without claims fails CLOSED — rows
        would just be missing, not error)."""
        await self._run_statements(attach.claims_statements(claims))
        seen = await asyncio.to_thread(
            lambda: self._conn.execute(
                "SELECT * FROM postgres_query('oltp', 'SELECT auth.tenant_id() AS tid')"
            ).fetchone()
        )
        expected = claims.get("tenant_id")
        if seen is None or seen[0] != expected:
            raise RuntimeError(
                f"OLTP claims canary mismatch (expected tenant {expected}, saw "
                f"{seen[0] if seen else None}) — discarding connection"
            )

    async def _end_claims(self) -> None:
        await self._run_statements(attach.end_claims_statements())


@asynccontextmanager
async def connection(
    tenant_uid: str, *, claims: dict[str, Any] | None = None
) -> AsyncIterator[LakeConnection]:
    """Acquire the tenant's pooled lake connection. Raises ``LakeUnavailable`` when the
    tenant has no active lake.

    With ``claims`` (the request's resolved claim bundle — what ``tenant_session``
    publishes) and ``DATALAKE_OLTP_DSN`` configured, the body runs inside ONE DuckDB
    transaction whose attached ``oltp`` catalog carries the caller's RLS context: OLTP
    tables can be joined directly (``oltp.platform.clients`` or
    ``postgres_query('oltp', …)``) and row-level security applies exactly as on the
    request path. The transaction is rolled back on exit (reads only), so the context
    can't outlive the block; any failure discards the connection entirely."""
    pool = _get_pool()
    pooled = await pool.acquire(tenant_uid)
    lake_conn = LakeConnection(pooled.conn)
    with_claims = claims is not None and bool(settings.datalake_oltp_dsn)
    try:
        if with_claims:
            await lake_conn._apply_claims(claims or {})
        yield lake_conn
        if with_claims:
            await lake_conn._end_claims()
        # Registered frames are connection-scoped (a ROLLBACK does not drop them), so
        # strip them before the connection is reused; failure discards the connection.
        await lake_conn._unregister_all()
    except BaseException:
        await pool.release(pooled, discard=True)
        raise
    else:
        await pool.release(pooled)


async def query(
    tenant_uid: str,
    sql: str,
    params: list[Any] | None = None,
    *,
    claims: dict[str, Any] | None = None,
) -> Any:
    """One-shot convenience: run a query on the tenant's lake, return an Arrow table."""
    async with connection(tenant_uid, claims=claims) as conn:
        return await conn.arrow(sql, params)
