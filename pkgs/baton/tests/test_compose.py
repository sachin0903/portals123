# mypy: ignore-errors
"""Composites: child rollup, gather, tolerance, gates, children, divergence, subjects, budget."""

from __future__ import annotations

import pytest

from baton import (
    Approval,
    NotReady,
    Permanent,
    ProgrammingError,
    Subject,
    SubjectBusy,
    action,
)


async def _start(engine, store, defn, key="k", approval=None, subjects=(), **params):
    async with store.unit() as u:
        sr = await engine.start(
            u,
            defn,
            tenant_id=1,
            params=params,
            idempotency_key=key,
            requested_by="proposer",
            approval=approval,
            subjects=list(subjects),
        )
    return sr


@pytest.fixture
def leaf(registry):
    @action(key="c.leaf", name="Leaf", registry=registry)
    async def leaf(ctx, *, v):
        return {"v": v}

    return leaf


@pytest.mark.asyncio
async def test_sequential_children_roll_up(engine, store, registry, leaf):
    @action(key="c.seq", name="Seq", registry=registry)
    async def seq(ctx):
        a = await ctx.run(leaf, v=1)
        b = await ctx.run(leaf, v=a["v"] + 1)
        return {"a": a, "b": b}

    sr = await _start(engine, store, seq)
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "completed"
    assert res.run.result == {"a": {"v": 1}, "b": {"v": 2}}


@pytest.mark.asyncio
async def test_a_gated_child_holds_for_approval(store, registry, leaf):
    # before_child returning an Approval must gate the child (not run it ungated): the child holds
    # paused/AwaitingApproval with its own gate, the parent suspends on it, and approving it lets
    # the parent complete.
    from baton import Engine
    from baton.hooks import NoopHooks

    @action(key="c.gatedparent", name="GatedParent", registry=registry)
    async def parent(ctx):
        a = await ctx.run(leaf, v=1)
        return {"a": a}

    heard: list[tuple[str, str]] = []

    class GateLeaf(NoopHooks):
        async def before_child(self, u, parent_run, child_def, params):
            return Approval(roles=(7,)) if child_def.key == "c.leaf" else None

        async def on_event(self, u, event, run):
            heard.append((event.event_type, run.uid))

    engine = Engine(
        store, registry, hooks=GateLeaf(), lease_seconds=60, default_budget_seconds=1000
    )
    sr = await _start(engine, store, parent, key="gp1")

    res = await engine.advance(sr.run.uid)
    assert res.outcome == "suspended"  # parent paused on the held child, child NOT run
    async with store.unit() as u:
        detail = await engine.get(u, sr.run.uid, tenant_id=1)
    child = detail.children[0].run
    assert (child.state.type.value, child.state.name) == ("paused", "AwaitingApproval")
    assert child.approver_role_ids == [7]
    # the app hears the hold the way it hears a held root, so the approvers can be told
    assert ("run.held", child.uid) in heard

    from baton import Worker

    async with store.unit() as u:
        await engine.decide(u, child.uid, decision="approve", actor_id="approver", role_ids=[7])
    worker = Worker(engine, poll_seconds=1)
    while await worker.run_once():
        pass
    async with store.unit() as u:
        done = await engine.get(u, sr.run.uid, tenant_id=1)
    assert done.run.state.type.value == "completed"
    assert done.children[0].run.state.type.value == "completed"


@pytest.mark.asyncio
async def test_gather_fans_out_and_sets_fan_in_edges(engine, store, registry, leaf):
    @action(key="c.fan", name="Fan", registry=registry)
    async def fan(ctx):
        results = await ctx.gather(
            ctx.run(leaf, v=1, key="a"),
            ctx.run(leaf, v=2, key="b"),
            ctx.run(leaf, v=3, key="c"),
        )
        tail = await ctx.run(leaf, v=sum(r["v"] for r in results), key="sum")
        return tail

    sr = await _start(engine, store, fan)
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "completed" and res.run.result == {"v": 6}
    async with store.unit() as u:
        detail = await engine.get(u, sr.run.uid, tenant_id=1)
    by_key = {c.run.key: c for c in detail.children}
    assert set(by_key["sum"].after_keys) == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_hard_child_failure_fails_parent(engine, store, registry):
    @action(key="c.bad", name="Bad", registry=registry)
    async def bad(ctx):
        raise Permanent("boom")

    @action(key="c.parent", name="Parent", registry=registry)
    async def parent(ctx):
        await ctx.run(bad)
        return {"never": True}

    sr = await _start(engine, store, parent)
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "failed"
    assert res.run.state.name == "Failed"


@pytest.mark.asyncio
async def test_resumable_child_makes_parent_needs_attention(engine, store, registry):
    @action(key="c.bad_na", name="BadNA", on_failure="needs_attention", registry=registry)
    async def bad(ctx):
        raise Permanent("boom")

    @action(key="c.parent_na", name="ParentNA", registry=registry)
    async def parent(ctx):
        await ctx.run(bad)
        return {"never": True}

    sr = await _start(engine, store, parent)
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "failed"
    assert res.run.state.name == "NeedsAttention"
    # retry revives the child and the parent
    async with store.unit() as u:
        await engine.retry(u, sr.run.uid)
    async with store.unit() as u:
        detail = await engine.get(u, sr.run.uid, tenant_id=1)
    assert detail.run.state.type.value == "scheduled"


@pytest.mark.asyncio
async def test_child_failure_tolerated_is_partial(engine, store, registry):
    @action(key="c.bad2", name="Bad2", registry=registry)
    async def bad(ctx):
        raise Permanent("boom")

    @action(key="c.tol", name="Tol", registry=registry)
    async def tol(ctx):
        await ctx.run(bad.with_options(on_failure="continue"))
        return {"ok": True}

    sr = await _start(engine, store, tol)
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "completed" and res.run.state.name == "Partial"
    assert res.run.result == {"ok": True}


@pytest.mark.asyncio
async def test_child_not_ready_pauses_then_worker_resumes(engine, store, registry, clock):
    attempts = {"n": 0}

    @action(key="c.slow", name="Slow", wait_timeout_seconds=100, registry=registry)
    async def slow(ctx):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise NotReady("later", delay=10)
        return {"done": True}

    @action(key="c.waits", name="Waits", registry=registry)
    async def waits(ctx):
        r = await ctx.run(slow)
        return {"child": r}

    sr = await _start(engine, store, waits)
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "suspended" and res.run.state.name == "AwaitingChildren"
    clock.advance(11)
    # advancing the parent is a noop while paused; the child is what's scheduled
    assert (await engine.advance(sr.run.uid)).outcome == "noop"
    async with store.unit() as u:
        detail = await engine.get(u, sr.run.uid, tenant_id=1)
    child_uid = detail.children[0].run.uid
    assert (await engine.advance(child_uid)).outcome == "completed"
    # the child's completion rescheduled the parent
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "completed"
    assert res.run.result == {"child": {"done": True}}


@pytest.mark.asyncio
async def test_run_level_gate_approve(engine, store, registry, leaf):
    @action(key="c.gated", name="Gated", registry=registry)
    async def gated(ctx):
        return await ctx.run(leaf, v=7)

    sr = await _start(engine, store, gated, approval=Approval(roles=[5]))
    assert sr.run.state.name == "AwaitingApproval"
    assert sr.run.case_no == 1
    # the body has not run
    assert (await engine.advance(sr.run.uid)).outcome == "noop"
    # requester cannot approve
    from baton import ApprovalError

    async with store.unit() as u:
        with pytest.raises(ApprovalError):
            await engine.decide(
                u, sr.run.uid, decision="approve", actor_id="proposer", role_ids=[5]
            )
    # wrong role
    async with store.unit() as u:
        with pytest.raises(ApprovalError):
            await engine.decide(u, sr.run.uid, decision="approve", actor_id="other", role_ids=[9])
    async with store.unit() as u:
        view = await engine.decide(
            u, sr.run.uid, decision="approve", actor_id="other", role_ids=[5]
        )
    assert view.state.type.value == "scheduled"
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "completed" and res.run.result == {"v": 7}


@pytest.mark.asyncio
async def test_run_level_gate_reject(engine, store, registry, leaf):
    @action(key="c.gated2", name="Gated2", registry=registry)
    async def gated(ctx):
        return await ctx.run(leaf, v=1)

    sr = await _start(engine, store, gated, approval=Approval(roles=[5]))
    async with store.unit() as u:
        view = await engine.decide(
            u, sr.run.uid, decision="reject", actor_id="other", role_ids=[5], note="no"
        )
    assert view.state.name == "Rejected"
    assert (await engine.advance(sr.run.uid)).outcome == "noop"


@pytest.mark.asyncio
async def test_divergence_when_a_child_key_changes_at_a_position(engine, store, registry, leaf):
    flip = {"v": False}

    @action(key="c.div", name="Div", registry=registry)
    async def div(ctx):
        first = "b" if flip["v"] else "a"
        await ctx.run(leaf, v=1, key=first)  # position 0 (after the gate at position... none here)
        await ctx.gate("g", roles=[1])  # pauses the parent so a second pass happens
        return {"done": True}

    sr = await _start(engine, store, div)
    res = await engine.advance(sr.run.uid)
    # a mid-body gate pauses the parent as AwaitingChildren (the gate child is AwaitingApproval)
    assert res.outcome == "suspended" and res.run.state.name == "AwaitingChildren"
    flip["v"] = True  # a redeploy changes the first child's key
    async with store.unit() as u:
        await engine.decide(u, sr.run.uid, decision="approve", actor_id="other", role_ids=[1])
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "failed" and res.run.state.name == "Conflicted"


@pytest.mark.asyncio
async def test_leaf_touching_session_cannot_call_child(engine, store, registry, leaf):
    @action(key="c.mix", name="Mix", registry=registry)
    async def mix(ctx):
        _ = ctx.session  # marks a leaf
        await ctx.run(leaf, v=1)

    sr = await _start(engine, store, mix)
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "failed"
    assert isinstance(res.error, ProgrammingError)


@pytest.mark.asyncio
async def test_one_open_per_subject(engine, store, registry):
    @action(
        key="c.excl",
        name="Excl",
        subject_kinds=("account",),
        one_open_per_subject=True,
        wait_timeout_seconds=100,
        registry=registry,
    )
    async def excl(ctx):
        raise NotReady("hold open", delay=100)

    sr = await _start(engine, store, excl, key="e1", subjects=[Subject("account", "acc1", 1)])
    await engine.advance(sr.run.uid)  # now Waiting (open)
    async with store.unit() as u:
        with pytest.raises(SubjectBusy):
            await engine.start(
                u,
                excl,
                tenant_id=1,
                params={},
                idempotency_key="e2",
                subjects=[Subject("account", "acc1", 1)],
            )


@pytest.mark.asyncio
async def test_lost_wakeup_reschedules_a_paused_parent_with_no_open_children(
    store, registry, clock, leaf
):
    # The lost-wakeup race: the awaited child settles — and fires its own wake_parent, a no-op
    # because the parent is still 'running' — between the parent's pass reading the child snapshot
    # and the parent writing 'paused'. Without the guard the parent would sleep on AwaitingChildren
    # forever. We simulate it by making open_children_exist report no open child at settle time;
    # the engine must then reschedule the parent (Resumed) instead of leaving it paused.
    from baton import Engine, NotReady
    from baton.store.memory import MemoryStore

    class RacyStore(MemoryStore):
        async def open_children_exist(self, u, parent_run_id):
            return False

    racy = RacyStore(clock=clock)
    racy_engine = Engine(racy, registry, lease_seconds=60, default_budget_seconds=1000)

    @action(key="c.slowchild", name="SlowChild", wait_timeout_seconds=100, registry=registry)
    async def slowchild(ctx):
        raise NotReady("later", delay=10)

    @action(key="c.racyparent", name="RacyParent", registry=registry)
    async def racyparent(ctx):
        return await ctx.run(slowchild)

    async with racy.unit() as u:
        sr = await racy_engine.start(u, racyparent, tenant_id=1, params={}, idempotency_key="race1")
    res = await racy_engine.advance(sr.run.uid)
    # paused was written then re-checked under the row lock: no open child -> rescheduled
    assert res.run.state.type.value == "scheduled"
    assert res.run.state.name == "Resumed"


@pytest.mark.asyncio
async def test_budget_stops_between_children(engine, store, registry, leaf):
    @action(key="c.three", name="Three", registry=registry)
    async def three(ctx):
        await ctx.run(leaf, v=1, key="a")
        await ctx.run(leaf, v=2, key="b")
        await ctx.run(leaf, v=3, key="c")
        return {"done": True}

    sr = await _start(engine, store, three)
    # zero budget: the first child cannot be created inline -> budget
    res = await engine.advance(sr.run.uid, budget_seconds=0)
    assert res.outcome in ("budget", "suspended")
    # a full-budget advance finishes it
    res = await engine.advance(sr.run.uid, budget_seconds=1000)
    assert res.outcome == "completed"


@pytest.mark.asyncio
async def test_cancel_cascades_to_the_open_children(engine, store, registry, clock):
    # Cancelling a composite paused on a child must cancel the child too, or a leaf mid-wait keeps
    # polling the provider — and eventually acts — for a run nobody wants any more.
    from baton import NotReady

    @action(key="c.waitchild", name="WaitChild", wait_timeout_seconds=1000, registry=registry)
    async def waitchild(ctx):
        raise NotReady("pre open", delay=10)

    @action(key="c.cancelparent", name="CancelParent", registry=registry)
    async def cancelparent(ctx):
        return await ctx.run(waitchild)

    sr = await _start(engine, store, cancelparent, key="cc1")
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "suspended"
    async with store.unit() as u:
        (child,) = await store.children(u, (await store.run_by_uid(u, sr.run.uid)).id)
    assert child.state.name == "Waiting"

    async with store.unit() as u:
        cancelled = await engine.cancel(u, sr.run.uid, actor_id="ops", note="raised in error")
    assert cancelled.state.type.value == "cancelled"
    async with store.unit() as u:
        child = await store.run_by_id(u, child.id)
    assert child.state.type.value == "cancelled"
    assert child.ended_at is not None
    # due or not, the child never runs again
    clock.advance(20)
    res = await engine.advance(child.uid)
    assert res.outcome == "noop"
    assert res.run.state.type.value == "cancelled"


@pytest.mark.asyncio
async def test_a_child_claimed_after_its_parent_was_cancelled_is_cancelled_not_run(
    engine, store, registry, clock
):
    # The orphan guard: the child that was RUNNING when the parent was cancelled (so the cascade
    # left it alone) reschedules itself, is claimed later, and must then be cancelled rather than
    # advanced. Simulated by settling the parent cancelled behind the engine's back.
    from baton import NotReady, states
    from baton.store import SettleFields

    calls = {"n": 0}

    @action(key="c.orphan", name="Orphan", wait_timeout_seconds=1000, registry=registry)
    async def orphan(ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            raise NotReady("pre open", delay=5)
        return {"opened": True}

    @action(key="c.orphanparent", name="OrphanParent", registry=registry)
    async def orphanparent(ctx):
        return await ctx.run(orphan)

    sr = await _start(engine, store, orphanparent, key="og1")
    await engine.advance(sr.run.uid)
    async with store.unit() as u:
        parent = await store.run_by_uid(u, sr.run.uid)
        (child,) = await store.children(u, parent.id)
        await store.settle_run(
            u,
            parent.id,
            None,
            state=states.CANCELLED,
            ended=True,
            fields=SettleFields(),
            now=clock.now(),
        )
    clock.advance(6)
    res = await engine.advance(child.uid)
    assert res.outcome == "noop"
    assert res.run.state.type.value == "cancelled"
    assert calls["n"] == 1  # the body did not run a second time


@pytest.mark.asyncio
async def test_a_failed_parents_open_child_still_runs_so_retry_can_finish_it(
    engine, store, registry, clock
):
    # Only a CANCELLED parent orphans its children. A failed parent is revived by retry and expects
    # its open children to carry on, so the guard must leave them alone.
    from baton import NotReady, states
    from baton.store import SettleFields

    calls = {"n": 0}

    @action(key="c.kept", name="Kept", wait_timeout_seconds=1000, registry=registry)
    async def kept(ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            raise NotReady("pre open", delay=5)
        return {"opened": True}

    @action(key="c.keptparent", name="KeptParent", registry=registry)
    async def keptparent(ctx):
        return await ctx.run(kept)

    sr = await _start(engine, store, keptparent, key="kp1")
    await engine.advance(sr.run.uid)
    async with store.unit() as u:
        parent = await store.run_by_uid(u, sr.run.uid)
        (child,) = await store.children(u, parent.id)
        await store.settle_run(
            u,
            parent.id,
            None,
            state=states.FAILED.with_message("sibling refused"),
            ended=True,
            fields=SettleFields(),
            now=clock.now(),
        )
    clock.advance(6)
    res = await engine.advance(child.uid)
    assert res.outcome == "completed"
    assert res.run.result == {"opened": True}
