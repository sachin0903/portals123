"""TenantPool (app/lake/pool): one tenant per connection, reuse-first, caps + LRU
eviction, failure paths."""

import asyncio

import pytest

from app.lake.pool import TenantPool


class FakeConn:
    def __init__(self, uid: str) -> None:
        self.uid = uid
        self.closed = False

    def close(self) -> None:
        self.closed = True


def make_opener(opened: list[str]):  # type: ignore[no-untyped-def]
    async def opener(uid: str) -> FakeConn:
        opened.append(uid)
        return FakeConn(uid)

    return opener


async def test_reuses_idle_same_tenant_connection() -> None:
    opened: list[str] = []
    pool = TenantPool(make_opener(opened), max_total=4, max_per_tenant=2)
    first = await pool.acquire("ten_a")
    await pool.release(first)
    second = await pool.acquire("ten_a")
    assert second is first  # same pooled connection, no second open
    assert opened == ["ten_a"]


async def test_never_shares_a_connection_across_tenants() -> None:
    opened: list[str] = []
    pool = TenantPool(make_opener(opened), max_total=4, max_per_tenant=2)
    a = await pool.acquire("ten_a")
    await pool.release(a)
    b = await pool.acquire("ten_b")  # idle ten_a conn exists but must NOT be reused
    assert b is not a
    assert b.conn.uid == "ten_b"
    assert opened == ["ten_a", "ten_b"]


async def test_per_tenant_cap_waits_for_release() -> None:
    opened: list[str] = []
    pool = TenantPool(make_opener(opened), max_total=4, max_per_tenant=1)
    held = await pool.acquire("ten_a")

    async def second() -> None:
        pooled = await pool.acquire("ten_a")
        await pool.release(pooled)

    task = asyncio.create_task(second())
    await asyncio.sleep(0.05)
    assert not task.done()  # blocked on the per-tenant cap
    await pool.release(held)
    await asyncio.wait_for(task, 1)
    assert opened == ["ten_a"]  # the waiter reused the released connection


async def test_global_cap_evicts_lru_idle() -> None:
    opened: list[str] = []
    pool = TenantPool(make_opener(opened), max_total=2, max_per_tenant=2)
    a = await pool.acquire("ten_a")
    b = await pool.acquire("ten_b")
    await pool.release(a)  # a becomes LRU idle
    await asyncio.sleep(0.01)
    await pool.release(b)
    c = await pool.acquire("ten_c")  # at cap: evicts ten_a (older idle)
    assert a.conn.closed is True
    assert b.conn.closed is False
    assert pool.stats()["total"] == 2
    await pool.release(c)


async def test_opener_failure_frees_the_slot() -> None:
    calls = {"n": 0}

    async def flaky(uid: str) -> FakeConn:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("attach failed")
        return FakeConn(uid)

    pool = TenantPool(flaky, max_total=1, max_per_tenant=1)
    with pytest.raises(RuntimeError):
        await pool.acquire("ten_a")
    assert pool.stats() == {"total": 0, "in_use": 0}  # placeholder cleaned up
    pooled = await pool.acquire("ten_a")  # slot is usable again
    assert pooled.conn.uid == "ten_a"


async def test_discard_drops_broken_connection() -> None:
    opened: list[str] = []
    pool = TenantPool(make_opener(opened), max_total=2, max_per_tenant=2)
    pooled = await pool.acquire("ten_a")
    await pool.release(pooled, discard=True)
    assert pooled.conn.closed is True
    assert pool.stats()["total"] == 0
    again = await pool.acquire("ten_a")
    assert again is not pooled


async def test_evict_tenant_closes_idle_and_retires_in_use() -> None:
    opened: list[str] = []
    pool = TenantPool(make_opener(opened), max_total=4, max_per_tenant=2)
    idle = await pool.acquire("ten_a")
    busy = await pool.acquire("ten_a")  # second connection (max_per_tenant=2)
    other = await pool.acquire("ten_b")
    await pool.release(idle)  # idle now parked; busy stays in flight

    await pool.evict_tenant("ten_a")

    assert idle.conn.closed  # idle connection dropped immediately
    assert not busy.conn.closed and busy.retired  # in-flight one flagged instead
    assert not other.conn.closed  # other tenants untouched

    await pool.release(busy)  # release of a retired connection discards it
    assert busy.conn.closed
    fresh = await pool.acquire("ten_a")
    assert fresh is not busy and fresh is not idle  # nothing stale survived
    await pool.release(other)
    await pool.release(fresh)
    await pool.close()


# ---- connection() lifecycle: registered frames must not survive release ----


class RecordingConn:
    def __init__(self) -> None:
        self.registered: dict[str, object] = {}

    def register(self, name: str, frame: object) -> None:
        self.registered[name] = frame

    def unregister(self, name: str) -> None:
        del self.registered[name]

    def close(self) -> None:
        pass


async def test_connection_unregisters_frames_before_pool_reuse(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.lake as lake

    conn = RecordingConn()

    async def opener(uid: str) -> RecordingConn:
        return conn

    pool = TenantPool(opener, max_total=2, max_per_tenant=2)
    monkeypatch.setattr(lake, "_pool", pool)

    async with lake.connection("ten_a") as lc:
        await lc.register("platform_clients", object())
        assert "platform_clients" in conn.registered
    # Released back to the pool: the RLS-scoped frame must be gone for the next request.
    assert conn.registered == {}
