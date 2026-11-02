"""Account actions on baton: account.create reads the book then runs the Seccl leaf, with the
Seccl open faked. Driven through the engine against the real database."""

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
from app.actions.hooks import GrapheneHooks
from app.actions.redact import firm_visible

pytestmark = pytest.mark.asyncio


def _engine(url: str) -> Engine:
    eng = create_async_engine(url)
    hooks = GrapheneHooks()
    store = PostgresStore(eng, schema="baton", hooks=hooks, clock=FakeClock())
    return Engine(
        store, default_registry, hooks=hooks, redact=firm_visible, default_budget_seconds=1000
    )


async def _seed_account(url: str, *, custodian: str) -> tuple[int, int, str]:
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    n = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES "
                    "('ten_' || :n, 'acc-' || :n, 'Account Firm') RETURNING id"
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
                    "INSERT INTO platform.accounts "
                    "(tenant_id, uid, contact_id, custodian, status) "
                    "VALUES (:t, :u, :c, CAST(:cu AS platform.provider_code), 'active') "
                    "RETURNING id"
                ),
                {"t": tid, "u": f"acc_{n}", "c": cid, "cu": custodian},
            )
        ).scalar_one()
    await eng.dispose()
    return int(tid), int(aid), f"acc_{n}"


async def _start_advance(engine, url, action_key, *, tid, aid, auid, key):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await engine.start(
            engine.store.bind(s),
            engine.registry.require(action_key),
            tenant_id=tid,
            params={"account_id": aid},
            idempotency_key=key,
            subjects=[Subject("account", auid, aid)],
        )
    await eng.dispose()
    return sr, await engine.advance(sr.run.uid)


async def test_account_bank_sends_to_seccl(db, monkeypatch):
    # account.bank on a Seccl account PATCHes the account's bank at Seccl (the send is wired).
    tid, aid, auid = await _seed_account(db["app"], custodian="seccl")
    engine = _engine(db["app"])

    async def fake_set_account_bank(session, *, tenant_id, account_id):
        return {"id": "SC-A", "request": {"bankDetails": {}}, "response": {"ok": True}}

    monkeypatch.setattr("app.integrations.seccl.instruct.set_account_bank", fake_set_account_bank)
    sr, res = await _start_advance(
        engine, db["app"], "account.bank", tid=tid, aid=aid, auid=auid, key=f"ab:{auid}"
    )
    assert res.outcome == "completed", res.outcome
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert [c.run.action_key for c in detail.children] == [
        "load.account_book",
        "seccl.account_bank",
    ]


async def test_fee_attach_dispatches_to_the_sei_leaf(db, monkeypatch):
    # fee.attach on a SEI account assigns the account's fee package at SEI (feePackageProfileId),
    # the SEI equivalent of Seccl's adviserFeeId.
    from app.actions.attempt import Attempt

    tid, aid, auid = await _seed_account(db["app"], custodian="sei")
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        return Attempt(
            ok=True,
            method="POST",
            url="/feepackageassignmentaccount/feePackageAssignmentAccount",
            response={"status": "SUCCESS"},
        )

    monkeypatch.setattr("app.integrations.sei.actions.sei_attach_fee", fake)
    sr, res = await _start_advance(
        engine, db["app"], "fee.attach", tid=tid, aid=aid, auid=auid, key=f"fa:{auid}"
    )
    assert res.outcome == "completed", res.outcome
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert [c.run.action_key for c in detail.children] == ["load.account_book", "sei.attach_fee"]


async def test_account_pay_to_refuses_a_non_sei_account(db):
    # pay-to is a SEI concept; on a Seccl account the composite refuses before any send.
    tid, aid, auid = await _seed_account(db["app"], custodian="seccl")
    engine = _engine(db["app"])
    sr, res = await _start_advance(
        engine, db["app"], "account.pay_to", tid=tid, aid=aid, auid=auid, key=f"pt:{auid}"
    )
    assert res.outcome == "failed"


async def test_account_create_runs_the_seccl_leaf(db, monkeypatch):
    tid, aid, auid = await _seed_account(db["app"], custodian="seccl")
    engine = _engine(db["app"])
    calls: list[int] = []

    async def fake_create_account(session, *, tenant_id, account_id):
        calls.append(account_id)
        return {"request": {"wrapper": "GIA"}, "accountId": "SC-NEW", "status": "created"}

    monkeypatch.setattr("app.integrations.seccl.enrol.create_account", fake_create_account)

    sr, res = await _start_advance(
        engine,
        db["app"],
        "account.create",
        tid=tid,
        aid=aid,
        auid=auid,
        key=f"m:account.create:{auid}",
    )
    assert res.outcome == "completed", res.outcome
    assert calls == [aid]
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert [c.run.action_key for c in detail.children] == [
        "load.account_book",
        "seccl.create_account",
    ]


async def test_account_create_is_one_open_per_subject(db, monkeypatch):
    from baton import SubjectBusy

    tid, aid, auid = await _seed_account(db["app"], custodian="seccl")
    engine = _engine(db["app"])

    async def fake_create_account(session, *, tenant_id, account_id):
        return {"status": "created"}

    monkeypatch.setattr("app.integrations.seccl.enrol.create_account", fake_create_account)

    # first run holds the subject while it is open
    sr, res = await _start_advance(
        engine, db["app"], "account.create", tid=tid, aid=aid, auid=auid, key="open1"
    )
    assert res.outcome == "completed"  # drains inline, so the subject is released
    # a run that is still open blocks a second: force that by starting without advancing
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await engine.start(
            engine.store.bind(s),
            engine.registry.require("account.create"),
            tenant_id=tid,
            params={"account_id": aid},
            idempotency_key="open2",
            subjects=[Subject("account", auid, aid)],
        )
        with pytest.raises(SubjectBusy):
            await engine.start(
                engine.store.bind(s),
                engine.registry.require("account.create"),
                tenant_id=tid,
                params={"account_id": aid},
                idempotency_key="open3",
                subjects=[Subject("account", auid, aid)],
            )
    await eng.dispose()


async def test_account_create_sei_dispatches_to_the_enrol_chain(db):
    # SEI is migrated: account.create for a SEI account opens the enrol chain rather than
    # refusing. With SEI unconfigured in the test it parks, but the sei.enrol child is created.
    tid, aid, auid = await _seed_account(db["app"], custodian="sei")
    engine = _engine(db["app"])
    sr, res = await _start_advance(
        engine, db["app"], "account.create", tid=tid, aid=aid, auid=auid, key="sei1"
    )
    assert res.outcome == "failed"
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert "sei.enrol" in [c.run.action_key for c in detail.children]


async def test_model_attach_runs_the_seccl_leaf(db, monkeypatch):
    tid, aid, auid = await _seed_account(db["app"], custodian="seccl")
    engine = _engine(db["app"])

    async def fake_attach_model(session, *, tenant_id, account_id):
        return {"event": "assetAllocation", "status": "created"}

    monkeypatch.setattr("app.integrations.seccl.instruct.attach_model", fake_attach_model)

    sr, res = await _start_advance(
        engine, db["app"], "model.attach", tid=tid, aid=aid, auid=auid, key=f"m:model.attach:{auid}"
    )
    assert res.outcome == "completed", res.outcome
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    assert [c.run.action_key for c in detail.children] == [
        "load.account_book",
        "seccl.attach_model",
    ]
