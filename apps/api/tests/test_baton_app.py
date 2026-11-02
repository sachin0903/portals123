"""baton wired into the app: the engine against the real database, its RLS and service_role, and
the firm-policy hooks. Proves the 0261 schema, the PostgresStore and the app hooks work together
before any door is moved over."""

from __future__ import annotations

import uuid

import pytest
from baton import Approval, ApprovalError, Engine, Worker, action
from baton.registry import Registry
from baton.store.memory import FakeClock
from baton.store.postgres import PostgresStore
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.actions.hooks import GrapheneHooks
from app.actions.redact import firm_visible

pytestmark = pytest.mark.asyncio

REG = Registry()


@action(key="t.leaf", name="Leaf", registry=REG)
async def leaf(ctx, *, msg):
    ctx.evidence(method="POST", url="/echo", request={"msg": msg}, response={"ok": True})
    return {"echoed": msg}


@action(key="t.child", name="Child", registry=REG)
async def child(ctx, *, v):
    return {"v": v}


@action(key="t.parent", name="Parent", registry=REG)
async def parent(ctx):
    a = await ctx.run(child, v=1)
    b = await ctx.run(child, v=a["v"] + 1)
    return {"a": a, "b": b}


@action(key="t.gated", name="Gated", registry=REG)
async def gated(ctx, *, amount):
    return await ctx.run(leaf, msg="sent " + str(amount))


def _engine(url: str, clock: FakeClock) -> Engine:
    eng = create_async_engine(url)
    hooks = GrapheneHooks()
    store = PostgresStore(eng, schema="baton", hooks=hooks, clock=clock)
    return Engine(store, REG, hooks=hooks, redact=firm_visible, default_budget_seconds=1000)


async def _tenant_and_user(url: str) -> tuple[int, str]:
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    nonce = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES "
                    "('ten_' || :n, 'baton-' || :n, 'Baton Firm') RETURNING id"
                ),
                {"n": nonce},
            )
        ).scalar_one()
        uid = (
            await s.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, workos_user_id, email, first_name) "
                    "VALUES (:u, :t, :w, :e, 'Ada') RETURNING id"
                ),
                {"u": f"usr_{nonce}", "t": tid, "w": f"wos_{nonce}", "e": f"ada.{nonce}@ex.test"},
            )
        ).scalar_one()
    await eng.dispose()
    return int(tid), str(uid)


async def _start(
    engine: Engine,
    url: str,
    defn,
    *,
    tenant_id,
    key,
    requested_by=None,
    approval=None,
    subjects=(),
    **params,
):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await engine.start(
            engine.store.bind(s),
            defn,
            tenant_id=tenant_id,
            params=params,
            idempotency_key=key,
            requested_by=requested_by,
            approval=approval,
            subjects=list(subjects),
        )
    await eng.dispose()
    return sr


async def test_leaf_runs_against_the_app_db(db):
    tid, _ = await _tenant_and_user(db["app"])
    engine = _engine(db["app"], FakeClock())
    sr = await _start(engine, db["app"], leaf, tenant_id=tid, key="k1", msg="hi")
    assert sr.created
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "completed"
    assert res.run.result == {"echoed": "hi"}
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert [a.outcome for a in detail.attempts] == ["ok"]
    assert detail.attempts[0].response == {"ok": True}


async def test_composite_rolls_up(db):
    tid, _ = await _tenant_and_user(db["app"])
    engine = _engine(db["app"], FakeClock())
    sr = await _start(engine, db["app"], parent, tenant_id=tid, key="p1")
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "completed"
    assert res.run.result == {"a": {"v": 1}, "b": {"v": 2}}
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert len(detail.children) == 2


async def test_gate_hold_and_approve(db):
    tid, requester = await _tenant_and_user(db["app"])
    engine = _engine(db["app"], FakeClock())
    sr = await _start(
        engine,
        db["app"],
        gated,
        tenant_id=tid,
        key="g1",
        requested_by=requester,
        approval=Approval(roles=[7]),
        amount=100,
    )
    assert sr.run.state.name == "AwaitingApproval"
    assert sr.run.case_no == 1
    assert (await engine.advance(sr.run.uid)).outcome == "noop"

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    # the requester cannot approve their own run
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        with pytest.raises(ApprovalError):
            await engine.decide(
                engine.store.bind(s),
                sr.run.uid,
                decision="approve",
                actor_id=requester,
                role_ids=[7],
            )
    # a holder of the role approves
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        view = await engine.decide(
            engine.store.bind(s), sr.run.uid, decision="approve", actor_id=None, role_ids=[7]
        )
    await eng.dispose()
    assert view.state.type.value == "scheduled"
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "completed"
    assert res.run.result == {"echoed": "sent 100"}


async def test_idempotent_start(db):
    tid, _ = await _tenant_and_user(db["app"])
    engine = _engine(db["app"], FakeClock())
    a = await _start(engine, db["app"], leaf, tenant_id=tid, key="same", msg="x")
    b = await _start(engine, db["app"], leaf, tenant_id=tid, key="same", msg="x")
    assert a.created and not b.created and a.run.uid == b.run.uid


async def test_worker_drains_a_scheduled_run(db):
    """The worker claims a runnable baton run and advances it — the background path a door relies
    on when it returns 202."""
    tid, _ = await _tenant_and_user(db["app"])
    engine = _engine(db["app"], FakeClock())
    sr = await _start(engine, db["app"], leaf, tenant_id=tid, key="wk1", msg="bg")
    # do not advance inline; the worker picks it up
    worker = Worker(engine, poll_seconds=1)
    # drain everything runnable (the session db is shared, so other tests may leave runs too)
    drained = 0
    while await worker.run_once():
        drained += 1
    assert drained >= 1
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert detail.run.state.name == "Completed"
    assert await worker.run_once() is False
