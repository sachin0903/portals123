"""Tenant-keyed DuckDB connection pool (docs/datalake.md §4).

Shape: **one tenant per pooled connection** — a connection only ever holds one tenant's
catalogue attach + S3 secret, so reusing it for another tenant is impossible by
construction (isolation does not depend on request auth). Attaching is the expensive step,
so connections are kept and reused per tenant: acquire prefers an idle connection already
attached to that tenant, opens a new one within the per-tenant and global caps (evicting
the least-recently-used idle connection of any tenant when full), and otherwise waits.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PooledConnection:
    tenant_uid: str
    conn: Any  # duckdb connection (None only while the slot is being opened)
    last_used: float = field(default_factory=time.monotonic)
    in_use: bool = False
    retired: bool = False  # evicted while in use — discard on release instead of reusing


class TenantPool:
    """asyncio pool of single-tenant DuckDB connections."""

    def __init__(
        self,
        opener: Callable[[str], Awaitable[Any]],
        *,
        max_total: int,
        max_per_tenant: int,
    ) -> None:
        self._opener = opener
        self._max_total = max_total
        self._max_per_tenant = max_per_tenant
        self._connections: list[PooledConnection] = []
        self._condition = asyncio.Condition()

    async def acquire(self, tenant_uid: str) -> PooledConnection:
        async with self._condition:
            while True:
                # 1) An idle connection already attached to this tenant — the cheap path.
                for pooled in self._connections:
                    if pooled.tenant_uid == tenant_uid and not pooled.in_use and pooled.conn:
                        pooled.in_use = True
                        return pooled

                tenant_count = sum(
                    1 for pooled in self._connections if pooled.tenant_uid == tenant_uid
                )
                if tenant_count < self._max_per_tenant:
                    # 2) Room for a new connection for this tenant. Make room globally by
                    # evicting the LRU idle connection (any tenant) if we're at the cap.
                    if len(self._connections) >= self._max_total:
                        idle = [p for p in self._connections if not p.in_use and p.conn]
                        if not idle:
                            await self._condition.wait()
                            continue
                        victim = min(idle, key=lambda p: p.last_used)
                        self._connections.remove(victim)
                        _close_quietly(victim.conn)
                    # Reserve the slot (marked in_use, conn pending) before the slow open,
                    # so concurrent acquires respect the caps without holding the lock.
                    placeholder = PooledConnection(tenant_uid=tenant_uid, conn=None, in_use=True)
                    self._connections.append(placeholder)
                    break

                # 3) Tenant at its cap with all its connections busy — wait for a release.
                await self._condition.wait()

        try:
            placeholder.conn = await self._opener(tenant_uid)
        except BaseException:
            async with self._condition:
                self._connections.remove(placeholder)
                self._condition.notify_all()
            raise
        return placeholder

    async def release(self, pooled: PooledConnection, *, discard: bool = False) -> None:
        """Return a connection to the pool; ``discard`` drops it (broken/errored)."""
        async with self._condition:
            if discard or pooled.retired:
                if pooled in self._connections:
                    self._connections.remove(pooled)
                _close_quietly(pooled.conn)
            else:
                pooled.in_use = False
                pooled.last_used = time.monotonic()
            self._condition.notify_all()

    async def evict_tenant(self, tenant_uid: str) -> None:
        """Drop every connection attached to this tenant (storage suspension hook): idle
        ones close now; in-flight ones are retired so release discards them."""
        async with self._condition:
            for pooled in list(self._connections):
                if pooled.tenant_uid != tenant_uid:
                    continue
                if pooled.in_use:
                    pooled.retired = True
                else:
                    self._connections.remove(pooled)
                    _close_quietly(pooled.conn)
            self._condition.notify_all()

    async def close(self) -> None:
        async with self._condition:
            for pooled in self._connections:
                _close_quietly(pooled.conn)
            self._connections.clear()
            self._condition.notify_all()

    def stats(self) -> dict[str, int]:
        return {
            "total": len(self._connections),
            "in_use": sum(1 for p in self._connections if p.in_use),
        }


@dataclass
class CoreConnection:
    """One provider's core catalogue, lent to a single thread while ``in_use``."""

    provider: str
    conn: Any  # duckdb connection (None only while the slot is being opened)
    last_used: float = field(default_factory=time.monotonic)
    in_use: bool = False


class CorePool:
    """Pool of provider-keyed DuckDB connections onto the core catalogue.

    Unlike :class:`TenantPool`, a connection is not tied to one firm. Every firm in a
    provider's catalogue is a schema (``tenant_{uid}``); isolation is that name, checked
    by the caller, not the attach. What the pool does guarantee is that a connection is
    in exactly one thread at a time — DuckDB connections are not thread-safe.

    The cap is global. ``/crm/home`` holds three connections at once (holdings,
    transactions, value series), so a cap below that only queues.
    """

    def __init__(self, opener: Callable[[str], Any], *, max_total: int) -> None:
        if max_total < 1:
            raise ValueError("core pool cap must be at least 1")
        self._opener = opener
        self._max_total = max_total
        self._connections: list[CoreConnection] = []
        self._condition = threading.Condition()

    def acquire(self, provider: str) -> CoreConnection:
        with self._condition:
            while True:
                for pooled in self._connections:
                    idle_for_provider = (
                        pooled.provider == provider
                        and not pooled.in_use
                        and pooled.conn is not None
                    )
                    if idle_for_provider:
                        pooled.in_use = True
                        pooled.last_used = time.monotonic()
                        return pooled
                if len(self._connections) >= self._max_total:
                    idle = [p for p in self._connections if not p.in_use and p.conn is not None]
                    if not idle:
                        self._condition.wait()
                        continue
                    # An idle connection for this provider was taken above. What is left
                    # belongs to another provider; drop the least recently used so this
                    # checkout can open.
                    victim = min(idle, key=lambda p: p.last_used)
                    self._connections.remove(victim)
                    _close_quietly(victim.conn)
                placeholder = CoreConnection(provider=provider, conn=None, in_use=True)
                self._connections.append(placeholder)
                break

        try:
            placeholder.conn = self._opener(provider)
        except BaseException:
            with self._condition:
                self._connections.remove(placeholder)
                self._condition.notify_all()
            raise
        return placeholder

    def release(self, pooled: CoreConnection, *, discard: bool = False) -> None:
        """Return a connection, or drop it when the body failed."""
        with self._condition:
            if discard or pooled.conn is None:
                if pooled in self._connections:
                    self._connections.remove(pooled)
                _close_quietly(pooled.conn)
            else:
                pooled.in_use = False
                pooled.last_used = time.monotonic()
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            for pooled in self._connections:
                _close_quietly(pooled.conn)
            self._connections.clear()
            self._condition.notify_all()

    def stats(self) -> dict[str, int]:
        return {
            "total": len(self._connections),
            "in_use": sum(1 for pooled in self._connections if pooled.in_use),
        }


def _close_quietly(conn: Any) -> None:
    if conn is not None:
        with contextlib.suppress(Exception):
            conn.close()
