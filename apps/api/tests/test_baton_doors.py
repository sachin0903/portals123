"""The money door on baton: with the flag on, lodge_instruction creates a baton run keyed on the
instruction, and the worker sends it. With the flag off it stays on the legacy engine."""

from __future__ import annotations

import uuid

import pytest
from baton import Engine, Worker
from baton.registry import default_registry
from baton.store.memory import FakeClock
from baton.store.postgres import PostgresStore
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.actions.defs  # noqa: F401 - registers the migrated actions
from app.actions import service
from app.actions.attempt import Attempt
from app.actions.hooks import GrapheneHooks
from app.actions.money import lodge_instruction
from app.actions.redact import firm_visible

pytestmark = pytest.mark.asyncio


def _engine(url: str) -> Engine:
    eng = create_async_engine(url)
    hooks = GrapheneHooks()
    store = PostgresStore(eng, schema="baton", hooks=hooks, clock=FakeClock())
    return Engine(
        store, default_registry, hooks=hooks, redact=firm_visible, default_budget_seconds=1000
    )


async def _seed_account(url: str) -> tuple[int, int]:
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    n = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES "
                    "('ten_' || :n, 'dr-' || :n, 'Door Firm') RETURNING id"
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
                    "VALUES (:t, :u, :c, CAST('seccl' AS platform.provider_code), 'active') "
                    "RETURNING id"
                ),
                {"t": tid, "u": f"acc_{n}", "c": cid},
            )
        ).scalar_one()
    await eng.dispose()
    return int(tid), int(aid)


async def _seed_contact(url: str) -> tuple[int, int, str]:
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    n = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES "
                    "('ten_' || :n, 'sy-' || :n, 'Sync Firm') RETURNING id"
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
    await eng.dispose()
    return int(tid), int(cid), f"con_{n}"


async def test_sync_door_starts_a_client_sync_run_and_the_worker_syncs(db, monkeypatch):
    tid, cid, _cuid = await _seed_contact(db["app"])
    engine = _engine(db["app"])
    monkeypatch.setattr(service, "get_engine", lambda: engine)

    synced: list[int] = []

    async def fake_sync(session, *, payload, subjects):
        synced.append(payload.get("contact_id"))
        return Attempt(ok=True, method="POST", url="/dynamics", response={"recorded": True})

    monkeypatch.setattr("app.integrations.morgan_lloyd.actions.sync_morgan_lloyd", fake_sync)

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        result = await service.start_sync(s, tenant_id=tid, contact_id=cid)
    await eng.dispose()
    assert result.created
    assert result.run.state.type.value == "scheduled"  # the worker will run it
    assert synced == []

    worker = Worker(engine, poll_seconds=1)
    while await worker.run_once():
        pass
    assert synced == [cid]


async def test_sync_door_refuses_a_concurrent_sync_for_the_same_client(db, monkeypatch):
    tid, cid, _cuid = await _seed_contact(db["app"])
    engine = _engine(db["app"])
    monkeypatch.setattr(service, "get_engine", lambda: engine)

    # one_open_per_subject: the first run is still open, so a second press is refused with 409
    # rather than opening a second sync against the same client in a real back-office.
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await service.start_sync(s, tenant_id=tid, contact_id=cid)
        with pytest.raises(service.ActionError) as caught:
            await service.start_sync(s, tenant_id=tid, contact_id=cid)
    await eng.dispose()
    assert caught.value.status == 409


async def test_decide_approves_a_held_baton_run_and_the_worker_runs_it(db, monkeypatch):
    from baton import Approval, Subject

    tid, cid, cuid = await _seed_contact(db["app"])
    engine = _engine(db["app"])
    monkeypatch.setattr(service, "get_engine", lambda: engine)

    approver = str(uuid.uuid4())
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "INSERT INTO auth.users (id, uid, tenant_id, email) "
                "VALUES (CAST(:id AS uuid), :uid, :t, :email)"
            ),
            {
                "id": approver,
                "uid": f"usr_{approver[:8]}",
                "t": tid,
                "email": f"a{approver[:6]}@x.io",
            },
        )
        sr = await engine.start(
            engine.store.bind(s),
            engine.registry.require("client.sync"),
            tenant_id=tid,
            params={"contact_id": cid},
            idempotency_key=f"heldsync:{cid}",
            subjects=[Subject("contact", cuid, cid)],
            approval=Approval(roles=[7]),
            requested_by=None,
        )
    assert sr.run.state.type.value == "paused"

    # the decide endpoint routes on this: a baton run goes through the engine
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        assert await service.owns_run(s, tenant_id=tid, run_uid=sr.run.uid)
        assert not await service.owns_run(s, tenant_id=tid, run_uid="not-a-run")

    # a role that is not an approver is refused
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        with pytest.raises(service.ActionError) as caught:
            await service.decide(
                s,
                run_uid=sr.run.uid,
                decision="approve",
                actor_id=approver,
                role_ids=[99],
                note=None,
            )
    assert caught.value.status == 403

    # an approver releases the run
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await service.decide(
            s,
            run_uid=sr.run.uid,
            decision="approve",
            actor_id=approver,
            role_ids=[7],
            note=None,
        )
    await eng.dispose()

    async def fake_sync(session, *, payload, subjects):
        return Attempt(ok=True, method="POST", url="/dynamics", response={"ok": True})

    monkeypatch.setattr("app.integrations.morgan_lloyd.actions.sync_morgan_lloyd", fake_sync)
    worker = Worker(engine, poll_seconds=1)
    while await worker.run_once():
        pass

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        row = (
            await s.execute(
                text("SELECT state_type FROM baton.runs WHERE uid = :u"), {"u": sr.run.uid}
            )
        ).scalar_one()
    await eng.dispose()
    assert row == "completed"


async def test_setup_door_starts_a_client_setup_run_the_journey_tracks(db, monkeypatch):
    from app.actions.setup import settle_account_setup

    tid, aid = await _seed_account(db["app"])
    engine = _engine(db["app"])
    monkeypatch.setattr(service, "get_engine", lambda: engine)

    calls: list[str] = []

    async def fake_client(session, *, tenant_id, contact_id):
        calls.append("client")
        return {"clientId": "SC-C", "status": "created"}

    async def fake_account(session, *, tenant_id, account_id):
        calls.append("account")
        return {"accountId": "SC-A", "status": "created"}

    monkeypatch.setattr("app.integrations.seccl.enrol.create_client", fake_client)
    monkeypatch.setattr("app.integrations.seccl.enrol.create_account", fake_account)

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        settled = await settle_account_setup(s, tenant_id=tid, account_id=aid)
    await eng.dispose()

    # the journey step records this uid and waits; the worker runs the plan
    assert settled["status"] == "waiting"
    assert settled["run_uid"]
    assert settled["run_status"] == "queued"
    assert settled["platform_sync"]["action_run_uid"] == settled["run_uid"]
    assert calls == []  # nothing sent synchronously

    worker = Worker(engine, poll_seconds=1)
    while await worker.run_once():
        pass
    assert calls == ["client", "account"]

    # the run is a client.setup run keyed on the standalone account
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        row = (
            (
                await s.execute(
                    text(
                        "SELECT state_type, state_name FROM baton.runs "
                        "WHERE uid = :u AND action_key = 'client.setup'"
                    ),
                    {"u": settled["run_uid"]},
                )
            )
            .mappings()
            .one()
        )
    await eng.dispose()
    assert row["state_type"] == "completed"


async def test_money_door_routes_to_baton_when_the_flag_is_on(db, monkeypatch):
    tid, aid = await _seed_account(db["app"])
    engine = _engine(db["app"])
    monkeypatch.setattr(service, "get_engine", lambda: engine)

    sent: list[str] = []

    async def fake_payment(session, *, payload, subjects):
        sent.append(payload.get("instruction_uid"))
        return Attempt(ok=True, method="POST", url="/ptg", response={"id": "PTG"})

    monkeypatch.setattr("app.integrations.seccl.actions.seccl_payment", fake_payment)

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        out = await lodge_instruction(
            s,
            tenant_id=tid,
            kind="expected_in",
            instruction_uid="I-1",
            account_id=aid,
            enforce_invoke=False,
        )
    await eng.dispose()
    assert out is None  # baton path created the run; nothing sent synchronously
    assert sent == []  # not sent until the worker runs

    # the worker drains the run and the send happens
    worker = Worker(engine, poll_seconds=1)
    while await worker.run_once():
        pass
    assert sent == ["I-1"]
