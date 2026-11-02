"""SEI account open on baton: enrol -> pay_to -> currencies, with enrol's "still opening" turned
into a durable wait that resumes on the next pass by the business key. The SEI handlers are faked
at their boundary; their internals are covered by test_sei_enrol."""

from __future__ import annotations

import uuid

import pytest
from baton import Engine, Subject
from baton.registry import default_registry
from baton.store.memory import FakeClock
from baton.store.postgres import PostgresStore
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.actions.defs  # noqa: F401 - registers the migrated actions
from app.actions.attempt import Attempt, OperationError
from app.actions.hooks import GrapheneHooks
from app.actions.redact import firm_visible

pytestmark = pytest.mark.asyncio


def _engine(url: str, clock: FakeClock) -> Engine:
    eng = create_async_engine(url)
    hooks = GrapheneHooks()
    store = PostgresStore(eng, schema="baton", hooks=hooks, clock=clock)
    return Engine(
        store, default_registry, hooks=hooks, redact=firm_visible, default_budget_seconds=1000
    )


async def _seed_sei_account(url: str) -> tuple[int, int, str]:
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    n = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES "
                    "('ten_' || :n, 'sei-' || :n, 'SEI Firm') RETURNING id"
                ),
                {"n": n},
            )
        ).scalar_one()
        cid = (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                    "VALUES (:t, :u, 'individual', 'Ada', 'Okonkwo') RETURNING id"
                ),
                {"t": tid, "u": f"con_{n}"},
            )
        ).scalar_one()
        aid = (
            await s.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, uid, contact_id, custodian, status) "
                    "VALUES (:t, :u, :c, CAST('sei' AS platform.provider_code), 'active') "
                    "RETURNING id"
                ),
                {"t": tid, "u": f"acc_{n}", "c": cid},
            )
        ).scalar_one()
    await eng.dispose()
    return int(tid), int(aid), f"acc_{n}"


async def _start(engine, url, tid, aid, auid, key):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await engine.start(
            engine.store.bind(s),
            engine.registry.require("account.create"),
            tenant_id=tid,
            params={"account_id": aid},
            idempotency_key=key,
            subjects=[Subject("account", auid, aid)],
        )
    await eng.dispose()
    return sr


async def test_sei_account_open_waits_then_resumes(db, monkeypatch):
    tid, aid, auid = await _seed_sei_account(db["app"])
    clock = FakeClock()
    engine = _engine(db["app"], clock)

    calls = {"enrol": 0, "pay_to": 0, "currencies": 0, "resumed_with": None}

    async def fake_enrol(session, *, payload, subjects):
        calls["enrol"] += 1
        handed = payload.get("resume_business_key")
        if handed is None:
            # first pass: SEI accepted the POST but is still opening the account
            raise OperationError(
                "SEI has not finished this enrolment (BK-1)",
                permanent=False,
                response={"businessKey": "BK-1"},
            )
        # resumed pass: the account is open
        calls["resumed_with"] = handed
        return Attempt(
            ok=True,
            method="POST",
            url="/accountservice/accounts",
            response={"businessKey": handed, "accounts": ["9910042"]},
        )

    async def fake_pay_to(session, *, payload, subjects):
        calls["pay_to"] += 1
        return Attempt(ok=True, method="POST", url="/paytos", response={"profileId": "P1"})

    async def fake_currencies(session, *, payload, subjects):
        calls["currencies"] += 1
        return Attempt(ok=True, method="POST", url="/currencies", response={"added": ["USD"]})

    monkeypatch.setattr("app.integrations.sei.actions.create_sei_account", fake_enrol)
    monkeypatch.setattr("app.integrations.sei.actions.sei_account_pay_to", fake_pay_to)
    monkeypatch.setattr("app.integrations.sei.actions.sei_account_currencies", fake_currencies)

    async def _children(run_uid):
        eng = create_async_engine(db["app"])
        mk = async_sessionmaker(eng, expire_on_commit=False)
        async with mk() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            d = await engine.get(engine.store.bind(s), run_uid, tenant_id=tid)
        await eng.dispose()
        return {c.run.action_key: c.run.uid for c in d.children}

    sr = await _start(engine, db["app"], tid, aid, auid, "sei1")
    # first advance: enrol posts, SEI still opening -> the run waits
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "suspended", res.outcome
    assert calls["enrol"] == 1
    enrol_uid = (await _children(sr.run.uid))["sei.enrol"]
    # too soon, and the parent is paused on its child
    assert (await engine.advance(enrol_uid)).outcome == "noop"
    assert (await engine.advance(sr.run.uid)).outcome == "noop"
    clock.advance(20)
    # the worker claims the now-due child: it resumes by the business key and completes
    assert (await engine.advance(enrol_uid)).outcome == "completed"
    assert calls["enrol"] == 2 and calls["resumed_with"] == "BK-1"
    # the parent wakes and finishes with pay_to and currencies
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "completed", res.outcome
    assert calls["pay_to"] == 1 and calls["currencies"] == 1

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    keys = [c.run.action_key for c in detail.children]
    assert keys == ["load.account_book", "sei.enrol", "sei.pay_to", "sei.currencies"]
    # the enrol child made two attempts: the wait, then the resume
    enrol = next(c for c in detail.children if c.run.action_key == "sei.enrol")
    assert [a.outcome for a in enrol.attempts] == ["wait", "ok"]


async def test_sei_pay_to_with_no_bank_is_skipped(db, monkeypatch):
    tid, aid, auid = await _seed_sei_account(db["app"])
    engine = _engine(db["app"], FakeClock())

    async def fake_enrol(session, *, payload, subjects):
        return Attempt(
            ok=True,
            method="POST",
            url="/accountservice/accounts",
            response={"businessKey": "BK", "accounts": ["991"]},
        )

    async def fake_pay_to(session, *, payload, subjects):
        return Attempt(ok=True, skipped=True, response={"skipped": "no bank"})

    async def fake_currencies(session, *, payload, subjects):
        return Attempt(ok=True, method="POST", url="/currencies", response={"added": []})

    monkeypatch.setattr("app.integrations.sei.actions.create_sei_account", fake_enrol)
    monkeypatch.setattr("app.integrations.sei.actions.sei_account_pay_to", fake_pay_to)
    monkeypatch.setattr("app.integrations.sei.actions.sei_account_currencies", fake_currencies)

    sr = await _start(engine, db["app"], tid, aid, auid, "sei2")
    res = await engine.advance(sr.run.uid)
    assert res.outcome == "completed", res.outcome
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    pay_to = next(c for c in detail.children if c.run.action_key == "sei.pay_to")
    assert pay_to.run.state.name == "Skipped"
