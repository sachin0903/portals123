# mypy: ignore-errors
"""The Postgres store against a real database: claims, fencing, sweep, subjects, concurrency."""

from __future__ import annotations

import uuid

import pytest

from baton import NotReady, Subject, SubjectBusy, action

pytestmark = pytest.mark.asyncio


async def _start(engine, store, defn, key, **params):
    async with store.unit() as u:
        sr = await engine.start(u, defn, tenant_id=1, params=params, idempotency_key=key)
    return sr.run.uid


async def test_leaf_completes_and_persists(pg_engine_obj, pg_store, registry):
    @action(key="pg.ok", name="Ok", registry=registry)
    async def ok(ctx, *, msg):
        ctx.evidence(method="POST", url="/x", response={"got": msg})
        return {"echo": msg}

    uid = await _start(pg_engine_obj, pg_store, ok, "k1", msg="hi")
    res = await pg_engine_obj.advance(uid)
    assert res.outcome == "completed" and res.run.result == {"echo": "hi"}
    async with pg_store.unit() as u:
        detail = await pg_engine_obj.get(u, uid, tenant_id=1)
    assert [a.outcome for a in detail.attempts] == ["ok"]
    assert detail.attempts[0].response == {"got": "hi"}


async def test_composite_with_children(pg_engine_obj, pg_store, registry):
    @action(key="pg.leaf", name="Leaf", registry=registry)
    async def leaf(ctx, *, v):
        return {"v": v}

    @action(key="pg.parent", name="Parent", registry=registry)
    async def parent(ctx):
        a = await ctx.run(leaf, v=1)
        b = await ctx.run(leaf, v=a["v"] + 1)
        return {"a": a, "b": b}

    uid = await _start(pg_engine_obj, pg_store, parent, "p1")
    res = await pg_engine_obj.advance(uid)
    assert res.outcome == "completed"
    assert res.run.result == {"a": {"v": 1}, "b": {"v": 2}}


async def test_idempotent_start_collapses(pg_engine_obj, pg_store, registry):
    @action(key="pg.idem", name="Idem", registry=registry)
    async def idem(ctx):
        return {"ok": True}

    async with pg_store.unit() as u:
        a = await pg_engine_obj.start(u, idem, tenant_id=1, params={}, idempotency_key="same")
    async with pg_store.unit() as u:
        b = await pg_engine_obj.start(u, idem, tenant_id=1, params={}, idempotency_key="same")
    assert a.created and not b.created and a.run.uid == b.run.uid


async def test_claim_is_exclusive(pg_engine_obj, pg_store, registry):
    @action(key="pg.claim", name="Claim", wait_timeout_seconds=100, registry=registry)
    async def claim(ctx):
        raise NotReady("hold", delay=100)

    uid = await _start(pg_engine_obj, pg_store, claim, "c1")
    # claim it under one token, leaving the lease live, then a second advance finds it busy
    async with pg_store.unit() as u:
        run = await pg_store.run_by_uid(u, uid)
        claimed = await pg_store.claim_run(
            u,
            worker_id="w1",
            token=uuid.uuid4().hex,
            lease_seconds=60,
            now=pg_store.clock.now(),
            run_id=run.id,
        )
    assert claimed is not None
    res = await pg_engine_obj.advance(uid)
    assert res.outcome == "busy"


async def test_fencing_blocks_a_lost_holder(pg_engine_obj, pg_store, registry):
    @action(key="pg.fence", name="Fence", wait_timeout_seconds=100, registry=registry)
    async def fence(ctx):
        raise NotReady("hold", delay=100)

    uid = await _start(pg_engine_obj, pg_store, fence, "f1")
    async with pg_store.unit() as u:
        run = await pg_store.run_by_uid(u, uid)
        await pg_store.claim_run(
            u,
            worker_id="w1",
            token="a" * 32,
            lease_seconds=60,
            now=pg_store.clock.now(),
            run_id=run.id,
        )
    # a settle with the wrong token writes nothing
    async with pg_store.unit() as u:
        from baton.states import SCHEDULED

        settled = await pg_store.settle_run(
            u,
            run.id,
            "b" * 32,
            state=SCHEDULED,
            scheduled_at=pg_store.clock.now(),
            now=pg_store.clock.now(),
        )
    assert settled is None


async def test_sweep_crashes_a_stale_leaf(pg_engine_obj, pg_store, registry, clock):
    @action(
        key="pg.stall", name="Stall", idempotent=False, wait_timeout_seconds=100, registry=registry
    )
    async def stall(ctx):
        raise NotReady("hold", delay=100)

    uid = await _start(pg_engine_obj, pg_store, stall, "s1")
    async with pg_store.unit() as u:
        run = await pg_store.run_by_uid(u, uid)
        await pg_store.claim_run(
            u,
            worker_id="w1",
            token=uuid.uuid4().hex,
            lease_seconds=60,
            now=clock.now(),
            run_id=run.id,
        )
        await pg_store.mark_passes(u, run.id, leaf=True)
    clock.advance(120)  # lease (60s) expired
    async with pg_store.unit() as u:
        report = await pg_store.sweep(u, now=clock.now(), registry_idempotent=lambda k: False)
    assert report.crashed_leaves == 1
    async with pg_store.unit() as u:
        row = await pg_store.run_by_id(u, run.id)
    assert row.state.name == "Crashed"


async def test_sweep_after_a_wait_numbers_the_crashed_attempt_without_colliding(
    pg_engine_obj, pg_store, registry, clock
):
    # A leaf that already recorded an attempt (a wait) and then crashes: the crashed attempt must
    # be numbered attempt_count+1, or it collides with the prior row on UNIQUE(run_id, attempt_no)
    # and aborts the whole sweep transaction.
    @action(
        key="pg.waitcrash",
        name="WaitCrash",
        idempotent=True,
        wait_timeout_seconds=1000,
        registry=registry,
    )
    async def waitcrash(ctx):
        raise NotReady("hold", delay=5)

    uid = await _start(pg_engine_obj, pg_store, waitcrash, "wc1")
    await pg_engine_obj.advance(uid)  # records attempt 1 (wait) and reschedules at now+5
    async with pg_store.unit() as u:
        run = await pg_store.run_by_uid(u, uid)
    assert run.attempt_count == 1
    clock.advance(10)  # past the wait so the run is due to be claimed again
    async with pg_store.unit() as u:
        await pg_store.claim_run(
            u,
            worker_id="w1",
            token=uuid.uuid4().hex,
            lease_seconds=60,
            now=clock.now(),
            run_id=run.id,
        )
        await pg_store.mark_passes(u, run.id, leaf=True)
    clock.advance(120)  # lease (60s) lapses
    async with pg_store.unit() as u:
        report = await pg_store.sweep(u, now=clock.now(), registry_idempotent=lambda k: True)
    assert report.crashed_leaves == 1
    async with pg_store.unit() as u:
        detail = await pg_engine_obj.get(u, uid, tenant_id=1)
    assert [a.attempt_no for a in detail.attempts] == [1, 2]
    assert [a.outcome for a in detail.attempts] == ["wait", "crashed"]


async def test_sweep_crashes_a_stale_child_before_it_settles(pg_store, clock):
    # Money-safety: a non-idempotent child leaf (e.g. seccl.payment) that crashes mid-send still
    # has leaf=NULL — it never reached a settle. The sweep must send it to CRASHED (its parent then
    # parks it for review), NOT Resumed; resuming re-runs the body and re-sends the payment.
    from baton import states
    from baton.store import NewRun

    async with pg_store.unit() as u:
        parent = await pg_store.insert_run(
            u,
            NewRun(
                uid="root-pay",
                tenant_id=1,
                action_key="pg.pay.parent",
                idempotency_key="pay-root",
                params={},
                params_hash=None,
                meta={},
                state=states.AWAITING_CHILDREN,
            ),
        )
        child = await pg_store.insert_run(
            u,
            NewRun(
                uid="child-pay",
                tenant_id=1,
                action_key="pg.pay.leaf",
                idempotency_key="pay-root/child#0",
                params={},
                params_hash=None,
                meta={},
                state=states.SCHEDULED,
                scheduled_at=clock.now(),
                parent_run_id=parent.id,
                root_run_id=parent.id,
                depth=1,
                key="child#0",
                position=0,
            ),
        )
        # Claim the child (now running, leaf still NULL — it has never settled), then let its lease
        # lapse to stand in for a worker crash part-way through the send.
        await pg_store.claim_run(
            u,
            worker_id="w1",
            token=uuid.uuid4().hex,
            lease_seconds=60,
            now=clock.now(),
            run_id=child.id,
        )
    clock.advance(120)
    async with pg_store.unit() as u:
        await pg_store.sweep(u, now=clock.now(), registry_idempotent=lambda k: False)
        swept = await pg_store.run_by_id(u, child.id)
    assert swept.state.name == "Crashed"  # not "Resumed": the body will not auto re-run/re-send


async def test_sweep_resumes_a_stale_root_composite(pg_store, clock):
    # A root composite that crashes mid-pass also has leaf=NULL, but it is safe to replay (it only
    # re-dispatches children), so the sweep resumes it rather than parking it. This is the other
    # half of the leaf/parent distinction the child test above pins down.
    from baton import states
    from baton.store import NewRun

    async with pg_store.unit() as u:
        root = await pg_store.insert_run(
            u,
            NewRun(
                uid="root-comp",
                tenant_id=1,
                action_key="pg.comp",
                idempotency_key="comp-root",
                params={},
                params_hash=None,
                meta={},
                state=states.SCHEDULED,
                scheduled_at=clock.now(),
            ),
        )
        await pg_store.claim_run(
            u,
            worker_id="w1",
            token=uuid.uuid4().hex,
            lease_seconds=60,
            now=clock.now(),
            run_id=root.id,
        )
    clock.advance(120)
    async with pg_store.unit() as u:
        report = await pg_store.sweep(u, now=clock.now(), registry_idempotent=lambda k: False)
        swept = await pg_store.run_by_id(u, root.id)
    assert swept.state.name == "Resumed"
    assert report.resumed_runs == 1


async def test_subject_busy(pg_engine_obj, pg_store, registry):
    @action(
        key="pg.excl",
        name="Excl",
        subject_kinds=("account",),
        one_open_per_subject=True,
        wait_timeout_seconds=100,
        registry=registry,
    )
    async def excl(ctx):
        raise NotReady("hold", delay=100)

    async with pg_store.unit() as u:
        await pg_engine_obj.start(
            u,
            excl,
            tenant_id=1,
            params={},
            idempotency_key="e1",
            subjects=[Subject("account", "acc1", 1)],
        )
    await pg_engine_obj.advance(await _run_uid(pg_store, "e1"))
    async with pg_store.unit() as u:
        with pytest.raises(SubjectBusy):
            await pg_engine_obj.start(
                u,
                excl,
                tenant_id=1,
                params={},
                idempotency_key="e2",
                subjects=[Subject("account", "acc1", 1)],
            )


async def test_wait_resume_on_pg(pg_engine_obj, pg_store, registry, clock):
    seen = {"n": 0}

    @action(key="pg.wait", name="Wait", wait_timeout_seconds=100, registry=registry)
    async def wait(ctx):
        seen["n"] += 1
        if seen["n"] == 1:
            raise NotReady("later", delay=10)
        return {"n": seen["n"]}

    uid = await _start(pg_engine_obj, pg_store, wait, "w1")
    res = await pg_engine_obj.advance(uid)
    assert res.outcome == "suspended" and res.run.state.name == "Waiting"
    assert (await pg_engine_obj.advance(uid)).outcome == "noop"
    clock.advance(11)
    res = await pg_engine_obj.advance(uid)
    assert res.outcome == "completed" and res.run.result == {"n": 2}


async def test_case_numbers_are_sequential(pg_engine_obj, pg_store, registry):
    @action(key="pg.held", name="Held", registry=registry)
    async def held(ctx):
        return {}

    from baton import Approval

    nums = []
    for i in range(5):
        async with pg_store.unit() as u:
            sr = await pg_engine_obj.start(
                u,
                held,
                tenant_id=1,
                params={},
                idempotency_key=f"h{i}",
                approval=Approval(roles=[1]),
            )
        nums.append(sr.run.case_no)
    assert nums == [1, 2, 3, 4, 5]


async def _run_uid(store, key):
    async with store.unit() as u:
        run = await store.run_by_key(u, 1, key)
    return run.uid


async def test_two_claims_one_wins(pg_engine_obj, pg_store, registry, clock):
    @action(key="pg.conc", name="Conc", registry=registry)
    async def conc(ctx):
        return {"ok": True}

    uid = await _start(pg_engine_obj, pg_store, conc, "cc1")
    async with pg_store.unit() as u:
        run = await pg_store.run_by_uid(u, uid)
    import asyncio

    a, b = await asyncio.gather(
        pg_store_claim(pg_store, run.id, "wa", clock),
        pg_store_claim(pg_store, run.id, "wb", clock),
    )
    assert (a is None) != (b is None)  # exactly one claimed


async def pg_store_claim(store, run_id, worker, clock):
    import uuid as _uuid

    async with store.unit() as u:
        return await store.claim_run(
            u,
            worker_id=worker,
            token=_uuid.uuid4().hex,
            lease_seconds=60,
            now=clock.now(),
            run_id=run_id,
        )


async def test_concurrent_claims_spread_across_due_runs(pg_engine_obj, pg_store, registry):
    # claim_and_advance claims any due run with FOR UPDATE SKIP LOCKED, so two concurrent claims
    # take DIFFERENT runs rather than contending on the same top one.
    import asyncio

    @action(key="pg.spread", name="Spread", registry=registry)
    async def spread(ctx):
        return {"ok": True}

    uid1 = await _start(pg_engine_obj, pg_store, spread, "sp1")
    uid2 = await _start(pg_engine_obj, pg_store, spread, "sp2")
    r1, r2 = await asyncio.gather(
        pg_engine_obj.claim_and_advance(worker_id="w1"),
        pg_engine_obj.claim_and_advance(worker_id="w2"),
    )
    claimed = {r.run.uid for r in (r1, r2) if r is not None}
    assert claimed == {uid1, uid2}


async def test_sweep_numbers_a_second_crash_after_the_first(
    pg_engine_obj, pg_store, registry, clock
):
    # The dev worker hit this: a child crashed, its root was retried, the child crashed again, and
    # the sweep's INSERT of the second crashed attempt collided with the first on
    # UNIQUE(run_id, attempt_no) because attempt_count was never bumped — aborting every sweep.
    @action(key="pg.twice", name="Twice", idempotent=True, registry=registry)
    async def twice(ctx):
        return {"ok": True}

    uid = await _start(pg_engine_obj, pg_store, twice, "twice1")

    async def crash_once():
        async with pg_store.unit() as u:
            run = await pg_store.run_by_uid(u, uid)
            await pg_store.claim_run(
                u,
                worker_id="w1",
                token=uuid.uuid4().hex,
                lease_seconds=60,
                now=clock.now(),
                run_id=run.id,
            )
            await pg_store.mark_passes(u, run.id, leaf=True)
        clock.advance(120)
        async with pg_store.unit() as u:
            report = await pg_store.sweep(u, now=clock.now(), registry_idempotent=lambda k: True)
        assert report.crashed_leaves == 1
        return run

    run = await crash_once()
    async with pg_store.unit() as u:
        await pg_engine_obj.retry(u, uid)
    await crash_once()
    async with pg_store.unit() as u:
        row = await pg_store.run_by_id(u, run.id)
        detail = await pg_engine_obj.get(u, uid, tenant_id=1)
    assert [a.attempt_no for a in detail.attempts] == [1, 2]
    assert [a.outcome for a in detail.attempts] == ["crashed", "crashed"]
    assert row.attempt_count == 2
    async with pg_store.unit() as u:
        await pg_engine_obj.retry(u, uid)
    res = await pg_engine_obj.advance(uid)
    assert res.outcome == "completed"
    async with pg_store.unit() as u:
        detail = await pg_engine_obj.get(u, uid, tenant_id=1)
    assert [a.attempt_no for a in detail.attempts] == [1, 2, 3]
