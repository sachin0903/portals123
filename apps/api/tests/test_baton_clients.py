"""The first migrated domain action on baton: client.details and its siblings, as composites that
resolve the provider then run its leaf. Driven through the policy-aware service against the real
database, with the Seccl call faked."""

from __future__ import annotations

import uuid

import pytest
from baton import Engine, Subject
from baton.registry import default_registry
from baton.store.memory import FakeClock
from baton.store.postgres import PostgresStore
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.actions.defs  # noqa: F401 - registers the migrated actions onto default_registry
from app.actions import service
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


async def _seed_linked_contact(url: str, provider: str) -> tuple[int, int, str]:
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    n = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES "
                    "('ten_' || :n, 'cli-' || :n, 'Client Firm') RETURNING id"
                ),
                {"n": n},
            )
        ).scalar_one()
        contact = (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                    "VALUES (:t, :u, 'individual', 'Ada', 'Okonkwo') RETURNING id"
                ),
                {"t": tid, "u": f"con_{n}"},
            )
        ).scalar_one()
        # one primary link, so the provider resolves to it
        await s.execute(
            text(
                "INSERT INTO platform.links "
                "(tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                "VALUES (:t, 'contact', :e, CAST(:p AS platform.provider_code), 'primary', :x)"
            ),
            {"t": tid, "e": contact, "p": provider, "x": f"X-{n}"},
        )
    await eng.dispose()
    return int(tid), int(contact), f"con_{n}"


async def _seed_seccl_contact(url: str) -> tuple[int, int, str]:
    return await _seed_linked_contact(url, "seccl")


async def test_client_details_dispatches_to_the_seccl_leaf(db, monkeypatch):
    tid, cid, cuid = await _seed_seccl_contact(db["app"])
    engine = _engine(db["app"])
    monkeypatch.setattr(service, "get_engine", lambda: engine)

    calls: list[dict] = []

    async def fake_update_client(session, *, tenant_id, contact_id, part):
        calls.append({"tenant_id": tenant_id, "contact_id": contact_id, "part": part})
        return {"url": "/client", "part": part, "status": "accepted"}

    monkeypatch.setattr("app.integrations.seccl.instruct.update_client", fake_update_client)

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await service.start(
            s,
            action_key="client.details",
            tenant_id=tid,
            params={"contact_id": cid},
            idempotency_key=f"manual:client.details:{cuid}",
            subjects=[Subject("contact", cuid, cid)],
        )
    await eng.dispose()
    assert sr.created

    res = await service.advance_inline(sr.run.uid, budget_seconds=1000)
    assert res.outcome == "completed", res.outcome
    assert res.run.result == {"url": "/client", "part": "details", "status": "accepted"}
    # the real Seccl update was called once, for the details block
    assert calls == [{"tenant_id": tid, "contact_id": cid, "part": "details"}]

    # the graph: resolve_contact_provider -> seccl.update_client
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await service.detail(s, run_uid=sr.run.uid, tenant_id=tid)
    await eng.dispose()
    keys = [c.run.action_key for c in detail.children]
    assert keys == ["resolve.contact_provider", "seccl.update_client"]
    # the Seccl leaf recorded its call as an attempt
    seccl_child = detail.children[1]
    assert seccl_child.attempts[0].method == "PATCH"
    assert seccl_child.attempts[0].response == {
        "url": "/client",
        "part": "details",
        "status": "accepted",
    }


async def test_client_circumstances_uses_the_circumstances_call(db, monkeypatch):
    tid, cid, cuid = await _seed_seccl_contact(db["app"])
    engine = _engine(db["app"])
    monkeypatch.setattr(service, "get_engine", lambda: engine)

    seen: list[str] = []

    async def fake_circ(session, *, tenant_id, contact_id):
        seen.append("circumstances")
        return {"url": "/client", "ok": True}

    monkeypatch.setattr("app.integrations.seccl.instruct.update_circumstances", fake_circ)

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await service.start(
            s,
            action_key="client.circumstances",
            tenant_id=tid,
            params={"contact_id": cid},
            idempotency_key=f"manual:client.circumstances:{cuid}",
            subjects=[Subject("contact", cuid, cid)],
        )
    await eng.dispose()
    res = await service.advance_inline(sr.run.uid, budget_seconds=1000)
    assert res.outcome == "completed"
    assert seen == ["circumstances"]


async def test_client_create_runs_the_seccl_leaf(db, monkeypatch):
    tid, cid, cuid = await _seed_seccl_contact(db["app"])
    engine = _engine(db["app"])
    monkeypatch.setattr(service, "get_engine", lambda: engine)

    made: list[int] = []

    async def fake_create_client(session, *, tenant_id, contact_id):
        made.append(contact_id)
        return {"request": {"type": "individual"}, "clientId": "SC-C1", "status": "created"}

    monkeypatch.setattr("app.integrations.seccl.enrol.create_client", fake_create_client)

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await service.start(
            s,
            action_key="client.create",
            tenant_id=tid,
            params={"contact_id": cid},
            idempotency_key=f"m:client.create:{cuid}",
            subjects=[Subject("contact", cuid, cid)],
        )
    await eng.dispose()
    res = await service.advance_inline(sr.run.uid, budget_seconds=1000)
    assert res.outcome == "completed"
    assert made == [cid]


async def test_client_create_defers_for_sei_without_failing(db, monkeypatch):
    # SEI (and Morgan Lloyd) enrol the client when the account is opened, so client.create has
    # nothing to do up front. It is a COMPOSITE (it resolves the provider first), so it must RETURN
    # to say "nothing to do" — a Skip raised from a composite fails the run, which is what broke
    # client.setup against SEI.
    tid, cid, cuid = await _seed_linked_contact(db["app"], "sei")
    engine = _engine(db["app"])
    monkeypatch.setattr(service, "get_engine", lambda: engine)

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await service.start(
            s,
            action_key="client.create",
            tenant_id=tid,
            params={"contact_id": cid},
            idempotency_key=f"m:client.create:{cuid}",
            subjects=[Subject("contact", cuid, cid)],
        )
    await eng.dispose()
    res = await service.advance_inline(sr.run.uid, budget_seconds=1000)
    assert res.outcome == "completed"  # NOT "failed"
    assert res.run.state.name == "Completed"


async def test_start_from_entity_runs_the_action(db, monkeypatch):
    """The generic Run door on baton: resolve the contact, start client.details, drain it."""
    tid, cid, cuid = await _seed_seccl_contact(db["app"])
    engine = _engine(db["app"])
    monkeypatch.setattr(service, "get_engine", lambda: engine)

    async def fake_update_client(session, *, tenant_id, contact_id, part):
        return {"url": "/client", "part": part, "status": "accepted"}

    monkeypatch.setattr("app.integrations.seccl.instruct.update_client", fake_update_client)

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await service.start_from_entity(
            s,
            action_key="client.details",
            tenant_id=tid,
            entity_type="contact",
            entity_uid=cuid,
            requested_by=None,
            role_ids=[],
        )
    await eng.dispose()
    assert sr.created
    # the worker advances it
    res = await service.advance_inline(sr.run.uid, budget_seconds=1000)
    assert res.outcome == "completed"
    assert res.run.result == {"url": "/client", "part": "details", "status": "accepted"}
