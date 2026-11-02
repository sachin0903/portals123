# mypy: ignore-errors
"""Leaf runs: success, retry, durable waits, guards, timeouts, and the attempt boundary."""

from __future__ import annotations

import pytest

from baton import Engine, NotReady, Permanent, Retry, Skip, action
from baton.store.memory import MemoryStore


async def _start(engine: Engine, store: MemoryStore, defn, key="k", **params):
    async with store.unit() as u:
        sr = await engine.start(u, defn, tenant_id=1, params=params, idempotency_key=key)
    return sr.run.uid


@pytest.mark.asyncio
async def test_leaf_completes_and_records_one_attempt(engine, store, registry):
    @action(key="t.ok", name="Ok", registry=registry)
    async def ok(ctx, *, msg):
        ctx.evidence(method="POST", url="/x", response={"got": msg})
        return {"echo": msg}

    uid = await _start(engine, store, ok, msg="hi")
    res = await engine.advance(uid)
    assert res.outcome == "completed"
    assert res.run.result == {"echo": "hi"}
    async with store.unit() as u:
        detail = await engine.get(u, uid, tenant_id=1)
    assert [a.outcome for a in detail.attempts] == ["ok"]
    assert detail.attempts[0].response == {"got": "hi"}


@pytest.mark.asyncio
async def test_safe_retry_three_attempts_in_one_pass(engine, store, registry):
    tries = {"n": 0}

    @action(key="t.flaky", name="Flaky", retries=2, registry=registry)
    async def flaky(ctx):
        tries["n"] += 1
        if tries["n"] < 3:
            raise RuntimeError(f"fail {tries['n']}")
        return {"n": tries["n"]}

    uid = await _start(engine, store, flaky)
    res = await engine.advance(uid)
    assert res.outcome == "completed"
    assert res.run.result == {"n": 3}
    async with store.unit() as u:
        detail = await engine.get(u, uid, tenant_id=1)
    assert [a.outcome for a in detail.attempts] == ["retry", "retry", "ok"]


@pytest.mark.asyncio
async def test_manual_leaf_parks_as_needs_attention(engine, store, registry):
    @action(key="t.manual", name="Manual", on_failure="needs_attention", registry=registry)
    async def manual(ctx):
        raise RuntimeError("nope")

    uid = await _start(engine, store, manual)
    res = await engine.advance(uid)
    assert res.outcome == "failed"
    assert res.run.state.name == "NeedsAttention"


@pytest.mark.asyncio
async def test_permanent_stops_retrying(engine, store, registry):
    tries = {"n": 0}

    @action(key="t.perm", name="Perm", retries=5, registry=registry)
    async def perm(ctx):
        tries["n"] += 1
        raise Permanent("do not retry")

    uid = await _start(engine, store, perm)
    res = await engine.advance(uid)
    assert res.outcome == "failed" and res.run.state.name == "Failed"
    assert tries["n"] == 1


@pytest.mark.asyncio
async def test_not_ready_waits_then_resumes(engine, store, registry, clock):
    seen = {"attempts": 0}

    @action(key="t.wait", name="Wait", retries=0, wait_timeout_seconds=100, registry=registry)
    async def wait(ctx):
        seen["attempts"] += 1
        if seen["attempts"] == 1:
            raise NotReady("not open yet", delay=5)
        return {"waited": ctx.waited.total_seconds()}

    uid = await _start(engine, store, wait)
    res = await engine.advance(uid)
    assert res.outcome == "suspended" and res.run.state.name == "Waiting"
    # not yet due
    res = await engine.advance(uid)
    assert res.outcome == "noop"
    clock.advance(6)
    res = await engine.advance(uid)
    assert res.outcome == "completed"
    assert res.run.result == {"waited": 6.0}


@pytest.mark.asyncio
async def test_wait_budget_times_out(engine, store, registry, clock):
    @action(
        key="t.waitbudget",
        name="WaitBudget",
        wait_timeout_seconds=10,
        on_failure="needs_attention",
        registry=registry,
    )
    async def wb(ctx):
        raise NotReady("never", delay=5)

    uid = await _start(engine, store, wb)
    await engine.advance(uid)  # wait
    clock.advance(6)
    await engine.advance(uid)  # wait again, now 6s elapsed
    clock.advance(6)  # 12s > 10s budget
    res = await engine.advance(uid)
    assert res.outcome == "failed" and res.run.state.name == "NeedsAttention"


@pytest.mark.asyncio
async def test_a_wait_does_not_consume_the_retry_budget(engine, store, registry, clock):
    calls = {"n": 0}

    @action(
        key="t.waitthenretry",
        name="WaitThenRetry",
        retries=1,
        wait_timeout_seconds=1000,
        registry=registry,
    )
    async def body(ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            raise NotReady("not open yet", delay=5)  # a durable wait, not a retry
        if calls["n"] == 2:
            raise Retry("transient")  # the one retry the budget allows
        return {"ok": True}

    uid = await _start(engine, store, body)
    await engine.advance(uid)  # wait -> scheduled
    clock.advance(6)
    res = await engine.advance(uid)  # Retry -> retry_now -> succeeds in-pass
    assert res.outcome == "completed", res.outcome
    assert calls["n"] == 3
    async with store.unit() as u:
        detail = await engine.get(u, uid, tenant_id=1)
    assert [a.outcome for a in detail.attempts] == ["wait", "retry", "ok"]


@pytest.mark.asyncio
async def test_guard_skips_without_running_body(engine, store, registry):
    ran = {"body": False}

    async def guard(ctx):
        return "already done"

    @action(key="t.guarded", name="Guarded", guard=guard, registry=registry)
    async def guarded(ctx):
        ran["body"] = True
        return {"did": "work"}

    uid = await _start(engine, store, guarded)
    res = await engine.advance(uid)
    assert res.outcome == "completed" and res.run.state.name == "Skipped"
    assert ran["body"] is False
    async with store.unit() as u:
        detail = await engine.get(u, uid, tenant_id=1)
    assert detail.attempts[0].outcome == "skipped"


@pytest.mark.asyncio
async def test_explicit_skip_completes_skipped(engine, store, registry):
    @action(key="t.skip", name="Skip", registry=registry)
    async def sk(ctx):
        raise Skip("nothing to send")

    uid = await _start(engine, store, sk)
    res = await engine.advance(uid)
    assert res.outcome == "completed" and res.run.state.name == "Skipped"


@pytest.mark.asyncio
async def test_retry_with_delay_suspends_then_runs(engine, store, registry, clock):
    tries = {"n": 0}

    @action(key="t.delay", name="Delay", retries=2, retry_delay=[10, 20], registry=registry)
    async def delay(ctx):
        tries["n"] += 1
        if tries["n"] < 2:
            raise Retry("later")
        return {"n": tries["n"]}

    uid = await _start(engine, store, delay)
    res = await engine.advance(uid)
    assert res.outcome == "suspended" and res.run.state.name == "AwaitingRetry"
    clock.advance(11)
    res = await engine.advance(uid)
    assert res.outcome == "completed" and res.run.result == {"n": 2}


@pytest.mark.asyncio
async def test_failed_leaf_rolls_back_but_records_attempt(engine, store, registry):
    # the attempt row survives; the run's result does not
    @action(key="t.boom", name="Boom", registry=registry)
    async def boom(ctx):
        ctx.evidence(method="POST", url="/send", request={"amount": 100})
        raise Permanent("vendor refused", status_code=422, response={"code": "X"})

    uid = await _start(engine, store, boom)
    res = await engine.advance(uid)
    assert res.outcome == "failed"
    async with store.unit() as u:
        detail = await engine.get(u, uid, tenant_id=1)
    att = detail.attempts[0]
    assert att.outcome == "failed" and att.status_code == 422 and att.response == {"code": "X"}
    assert detail.run.result is None


@pytest.mark.asyncio
async def test_attempt_bodies_are_json_normalised_before_they_are_stored(engine, store, registry):
    # A vendor body with a Decimal or a datetime in it must land in the row as text, the same way
    # the Postgres store would write it — not raise in the middle of recording the attempt.
    from datetime import UTC, datetime
    from decimal import Decimal

    @action(key="t.money", name="Money", registry=registry)
    async def money(ctx):
        ctx.evidence(
            method="POST",
            url="/x",
            request={"amount": Decimal("1.50")},
            response={"when": datetime(2026, 1, 1, tzinfo=UTC)},
        )
        return {"ok": True}

    uid = await _start(engine, store, money)
    res = await engine.advance(uid)
    assert res.outcome == "completed"
    async with store.unit() as u:
        detail = await engine.get(u, uid, tenant_id=1)
    assert detail.attempts[0].request == {"amount": "1.50"}
    assert detail.attempts[0].response == {"when": "2026-01-01T00:00:00+00:00"}


@pytest.mark.asyncio
async def test_a_refusal_carrying_a_decimal_is_recorded(engine, store, registry):
    from decimal import Decimal

    @action(key="t.refused", name="Refused", on_failure="needs_attention", registry=registry)
    async def refused(ctx):
        raise Permanent("refused", status_code=422, response={"owed": Decimal("2.25")})

    uid = await _start(engine, store, refused)
    res = await engine.advance(uid)
    assert res.outcome == "failed" and res.run.state.name == "NeedsAttention"
    async with store.unit() as u:
        detail = await engine.get(u, uid, tenant_id=1)
    assert detail.attempts[0].outcome == "failed"
    assert detail.attempts[0].response == {"owed": "2.25"}


@pytest.mark.asyncio
async def test_a_failure_while_recording_the_failure_parks_the_run(registry, clock):
    # The attempt failed AND the store refused the attempt row. The run must not be left leased
    # 'running' for the sweep to crash a minute later; it parks for a person with both messages.
    from baton import Engine
    from baton.store.memory import MemoryStore

    class BrokenStore(MemoryStore):
        async def insert_attempt(self, u, new):
            if new.outcome != "ok":
                raise RuntimeError("disk full")
            return await super().insert_attempt(u, new)

    broken = BrokenStore(clock=clock)
    engine = Engine(broken, registry, lease_seconds=60, default_budget_seconds=1000)

    @action(key="t.unrecordable", name="Unrecordable", registry=registry)
    async def unrecordable(ctx):
        raise Permanent("refused")

    uid = await _start(engine, broken, unrecordable)
    res = await engine.advance(uid)
    assert res.outcome == "failed"
    assert res.run.state.name == "NeedsAttention"
    assert "refused" in (res.run.state.message or "")
    assert "could not be recorded" in (res.run.state.message or "")
    assert "disk full" in (res.run.state.message or "")
    assert res.run.ended_at is not None


@pytest.mark.asyncio
async def test_sweep_numbers_a_second_crash_after_the_first_and_the_rerun_after_both(
    engine, store, registry, clock
):
    # A leaf that crashed, was revived by retry and crashed again: the second crashed attempt is
    # numbered after the first (the sweep used attempt_count + 1, which it never bumped, so the
    # second sweep collided on UNIQUE(run_id, attempt_no) and aborted every sweep from then on),
    # and the real attempt that follows a revive is numbered after both.
    import uuid

    @action(key="t.twice", name="Twice", idempotent=True, registry=registry)
    async def twice(ctx):
        return {"ok": True}

    uid = await _start(engine, store, twice)

    async def crash_once():
        async with store.unit() as u:
            run = await store.run_by_uid(u, uid)
            await store.claim_run(
                u,
                worker_id="w1",
                token=uuid.uuid4().hex,
                lease_seconds=60,
                now=clock.now(),
                run_id=run.id,
            )
            await store.mark_passes(u, run.id, leaf=True)
        clock.advance(120)
        async with store.unit() as u:
            report = await store.sweep(u, now=clock.now(), registry_idempotent=lambda k: True)
        assert report.crashed_leaves == 1
        return run

    run = await crash_once()
    async with store.unit() as u:
        await engine.retry(u, uid)
    await crash_once()
    async with store.unit() as u:
        row = await store.run_by_id(u, run.id)
        detail = await engine.get(u, uid, tenant_id=1)
    assert [a.attempt_no for a in detail.attempts] == [1, 2]
    assert [a.outcome for a in detail.attempts] == ["crashed", "crashed"]
    assert row.attempt_count == 2
    # revived again, the real attempt is number 3 and the run completes
    async with store.unit() as u:
        await engine.retry(u, uid)
    res = await engine.advance(uid)
    assert res.outcome == "completed"
    async with store.unit() as u:
        detail = await engine.get(u, uid, tenant_id=1)
    assert [a.attempt_no for a in detail.attempts] == [1, 2, 3]
    assert detail.attempts[-1].outcome == "ok"
