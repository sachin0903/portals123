"""Morgan Lloyd actions on baton: client.sync runs the ML sync leaf, and the client.* composites
reach a Morgan Lloyd contact through morgan-lloyd.update_client. The ML handlers are faked."""

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
from app.actions.attempt import Attempt
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


async def _seed_ml_contact(url: str) -> tuple[int, int, str]:
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    n = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES "
                    "('ten_' || :n, 'ml-' || :n, 'ML Firm') RETURNING id"
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
        await s.execute(
            text(
                "INSERT INTO platform.links "
                "(tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                "VALUES (:t, 'contact', :e, 'morgan-lloyd', 'primary', :x)"
            ),
            {"t": tid, "e": cid, "x": f"ML-{n}"},
        )
    await eng.dispose()
    return int(tid), int(cid), f"con_{n}"


async def _start_advance(engine, url, action_key, *, tid, cid, cuid, key, **params):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await engine.start(
            engine.store.bind(s),
            engine.registry.require(action_key),
            tenant_id=tid,
            params={"contact_id": cid, **params},
            idempotency_key=key,
            subjects=[Subject("contact", cuid, cid)],
        )
    await eng.dispose()
    return sr, await engine.advance(sr.run.uid)


async def test_client_sync_runs_the_ml_leaf(db, monkeypatch):
    tid, cid, cuid = await _seed_ml_contact(db["app"])
    engine = _engine(db["app"])

    seen: list[int] = []

    async def fake_sync(session, *, payload, subjects):
        seen.append(payload["contact_id"])
        return Attempt(
            ok=True,
            method="POST",
            url="morgan-lloyd/sync-client",
            response={"contact": "synced", "scheme": "ok"},
        )

    monkeypatch.setattr("app.integrations.morgan_lloyd.actions.sync_morgan_lloyd", fake_sync)
    sr, res = await _start_advance(
        engine, db["app"], "client.sync", tid=tid, cid=cid, cuid=cuid, key="sync1"
    )
    assert res.outcome == "completed", res.outcome
    assert res.run.result == {"contact": "synced", "scheme": "ok"}
    assert seen == [cid]


async def test_client_details_dispatches_to_morgan_lloyd(db, monkeypatch):
    tid, cid, cuid = await _seed_ml_contact(db["app"])
    engine = _engine(db["app"])

    seen: list[str] = []

    def fake_part(part):
        async def handler(session, *, payload, subjects):
            seen.append(part)
            return Attempt(
                ok=True, method="PATCH", url="contacts(ML-x)", response={"entity_id": "e1"}
            )

        return handler

    monkeypatch.setattr("app.integrations.morgan_lloyd.actions._ml_part", fake_part)
    sr, res = await _start_advance(
        engine, db["app"], "client.details", tid=tid, cid=cid, cuid=cuid, key="mld1"
    )
    assert res.outcome == "completed", res.outcome
    assert seen == ["details"]
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    keys = [c.run.action_key for c in detail.children]
    assert keys == ["resolve.contact_provider", "morgan-lloyd.update_client"]


async def test_a_refused_sync_records_the_failing_call_on_the_attempt(db, monkeypatch):
    # The sync RAISES — a refused `cam_createcontact`. The attempt used to show "No address
    # recorded" because the exception path never reached ctx.evidence; now the call the sync made
    # (its URL, what it sent, the 400 body) is captured off the scoped api-log recorder and shown.
    tid, cid, cuid = await _seed_ml_contact(db["app"])
    engine = _engine(db["app"])

    from api_log import ApiCall, default_recorder

    from app.actions.attempt import OperationError

    async def fake_sync(session, *, payload, subjects):
        rec = default_recorder()  # the scope `ml_sync` installed with `recording_into`
        assert rec is not None
        rec(
            ApiCall(
                provider="morgan-lloyd",
                method="POST",
                url="https://api/cam_createcontact",
                operation="create_contact",
                request={"external_reference": "ml-contact-1"},
                response={"error": {"code": "0x80040224"}},
                status_code=400,
                ok=False,
            )
        )
        raise OperationError(
            "Morgan Lloyd could not be reached: POST cam_createcontact failed: 400 "
            '{"error":{"code":"0x80040224"}}',
            permanent=True,
        )

    monkeypatch.setattr("app.integrations.morgan_lloyd.actions.sync_morgan_lloyd", fake_sync)
    sr, res = await _start_advance(
        engine, db["app"], "client.sync", tid=tid, cid=cid, cuid=cuid, key="syncfail1"
    )
    assert res.outcome != "completed", res.outcome

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    leaf = detail.children[0]
    assert leaf.run.action_key == "morgan-lloyd.sync"
    att = leaf.attempts[0]
    assert att.method == "POST"
    assert att.url == "https://api/cam_createcontact"
    assert att.status_code == 400
    assert att.request == {"external_reference": "ml-contact-1"}
    assert att.response == {"error": {"code": "0x80040224"}}
