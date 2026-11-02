"""The four-eye book actions on baton: an apply leaf per wired review key. It re-reads the record
and applies the change, unless the record moved since the snapshot — then it parks rather than
applying a stale change. The hold/approve path is wired in a later increment; here we drive the
apply leaf directly."""

from __future__ import annotations

import uuid

import pytest
from baton import Engine, Subject
from baton.registry import default_registry
from baton.store.memory import FakeClock
from baton.store.postgres import PostgresStore
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.actions.defs  # noqa: F401 - registers the book actions
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


async def _seed_contact(url: str, *, bank: str) -> tuple[int, int, str]:
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    n = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES "
                    "('ten_' || :n, 'bk-' || :n, 'Book Firm') RETURNING id"
                ),
                {"n": n},
            )
        ).scalar_one()
        cid = (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, "
                    "bank_account) VALUES (:t, :u, 'individual', 'Ada', 'Okonkwo', "
                    "CAST(:bank AS jsonb)) RETURNING id"
                ),
                {"t": tid, "u": f"con_{n}", "bank": bank},
            )
        ).scalar_one()
    await eng.dispose()
    return int(tid), int(cid), f"con_{n}"


async def _run(engine, url, tid, cuid, cid, key, *, payload, before):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await engine.start(
            engine.store.bind(s),
            engine.registry.require("contact.bank_details_changed"),
            tenant_id=tid,
            params={"payload": payload, "before": before},
            idempotency_key=key,
            subjects=[Subject("contact", cuid, cid)],
        )
    res = await engine.advance(sr.run.uid)
    await eng.dispose()
    return res


async def _bank_of(url, tid, cid):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        row = (
            await s.execute(
                text("SELECT bank_account FROM crm.contacts WHERE id = :c AND tenant_id = :t"),
                {"c": cid, "t": tid},
            )
        ).scalar_one()
    await eng.dispose()
    return row


async def test_book_apply_writes_the_change_when_the_record_is_unchanged(db):
    old = '{"name": "Old", "sort_code": "11-11-11", "account_number": "11111111"}'
    tid, cid, cuid = await _seed_contact(db["app"], bank=old)
    engine = _engine(db["app"])

    before = {"value": {"name": "Old", "sort_code": "11-11-11", "account_number": "11111111"}}
    payload = {
        "contact_id": cid,
        "bank_account": {
            "name": "New",
            "sort_code": "22-22-22",
            "account_number": "22222222",
        },
    }
    res = await _run(engine, db["app"], tid, cuid, cid, "bk1", payload=payload, before=before)
    assert res.outcome == "completed", res.outcome

    bank = await _bank_of(db["app"], tid, cid)
    assert bank["name"] == "New"
    assert bank["account_number"] == "22222222"


async def test_book_apply_parks_a_stale_change_without_applying_it(db):
    old = '{"name": "Old", "sort_code": "11-11-11", "account_number": "11111111"}'
    tid, cid, cuid = await _seed_contact(db["app"], bank=old)
    engine = _engine(db["app"])

    # the snapshot does not match the live record: someone changed it since the proposal
    stale_before = {"value": {"name": "Someone else", "sort_code": "99-99-99"}}
    payload = {
        "contact_id": cid,
        "bank_account": {"name": "New", "sort_code": "22-22-22", "account_number": "22222222"},
    }
    res = await _run(engine, db["app"], tid, cuid, cid, "bk2", payload=payload, before=stale_before)
    assert res.outcome == "failed"
    assert res.run.state.name == "NeedsAttention"

    # the live record is untouched
    bank = await _bank_of(db["app"], tid, cid)
    assert bank["name"] == "Old"
