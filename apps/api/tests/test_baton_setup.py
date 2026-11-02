"""client.setup on baton: the composite-of-composites. It loads the plan, creates each client,
opens each account, and (per intent) attaches money/model. Seccl calls are faked."""

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
        store, default_registry, hooks=hooks, redact=firm_visible, default_budget_seconds=5000
    )


async def _seed(url: str, *, payment: bool = False) -> tuple[int, int, str]:
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    n = uuid.uuid4().hex[:8]
    data = '{"initial_payment": true}' if payment else "{}"
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES "
                    "('ten_' || :n, 'set-' || :n, 'Setup Firm') RETURNING id"
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
                    "(tenant_id, uid, contact_id, custodian, status, data) "
                    "VALUES (:t, :u, :c, CAST('seccl' AS platform.provider_code), 'active', "
                    "        CAST(:d AS jsonb)) RETURNING id"
                ),
                {"t": tid, "u": f"acc_{n}", "c": cid, "d": data},
            )
        ).scalar_one()
    await eng.dispose()
    return int(tid), int(aid), f"acc_{n}"


def _fake_seccl(monkeypatch, calls):
    async def fake_client(session, *, tenant_id, contact_id):
        calls.append(("client", contact_id))
        return {"clientId": "SC-C", "status": "created"}

    async def fake_account(session, *, tenant_id, account_id):
        calls.append(("account", account_id))
        return {"accountId": "SC-A", "status": "created"}

    async def fake_payment(session, *, payload, subjects):
        from app.actions.attempt import Attempt

        calls.append(("payment", payload["account_id"]))
        return Attempt(ok=True, method="POST", url="/ptg", response={"id": "PTG"})

    monkeypatch.setattr("app.integrations.seccl.enrol.create_client", fake_client)
    monkeypatch.setattr("app.integrations.seccl.enrol.create_account", fake_account)
    monkeypatch.setattr("app.integrations.seccl.actions.seccl_payment", fake_payment)


async def _run_setup(engine, url, tid, aid, auid, key):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await engine.start(
            engine.store.bind(s),
            engine.registry.require("client.setup"),
            tenant_id=tid,
            params={"account_id": aid},
            idempotency_key=key,
            subjects=[Subject("account", auid, aid)],
        )
    await eng.dispose()
    return sr, await engine.advance(sr.run.uid)


async def test_setup_creates_the_client_then_opens_the_account(db, monkeypatch):
    tid, aid, auid = await _seed(db["app"])
    engine = _engine(db["app"])
    calls: list = []
    _fake_seccl(monkeypatch, calls)

    sr, res = await _run_setup(engine, db["app"], tid, aid, auid, "setup1")
    assert res.outcome == "completed", res.outcome
    assert res.run.result["clients"] == 1 and res.run.result["accounts"] == 1
    # client created before the account
    assert [c[0] for c in calls] == ["client", "account"]

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    keys = [c.run.action_key for c in detail.children]
    assert keys == ["load.plan", "client.create", "account.create"]


async def test_setup_sends_the_opening_payment_when_captured(db, monkeypatch):
    tid, aid, auid = await _seed(db["app"], payment=True)
    engine = _engine(db["app"])
    calls: list = []
    _fake_seccl(monkeypatch, calls)

    sr, res = await _run_setup(engine, db["app"], tid, aid, auid, "setup2")
    assert res.outcome == "completed", res.outcome
    assert [c[0] for c in calls] == ["client", "account", "payment"]
