"""The two record-write reviews wired in response to testing (vulnerability + client details):
hold → inbox → approve → the worker applies → the record changes. The details case carries a date
field to prove the JSON snapshot/payload round-trips back through ``update_contact`` cleanly.
"""

from __future__ import annotations

import uuid

import pytest
from baton import Engine, Worker
from baton.registry import default_registry
from baton.store.memory import FakeClock
from baton.store.postgres import PostgresStore
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.actions.defs  # noqa: F401 - registers the book actions
from app.actions.hooks import GrapheneHooks
from app.actions.redact import firm_visible
from app.reviews import baton as baton_reviews
from app.reviews.apply import handler
from app.reviews.catalogue import require_wired

pytestmark = pytest.mark.asyncio


def _engine(url: str) -> Engine:
    eng = create_async_engine(url)
    hooks = GrapheneHooks()
    store = PostgresStore(eng, schema="baton", hooks=hooks, clock=FakeClock())
    return Engine(
        store, default_registry, hooks=hooks, redact=firm_visible, default_budget_seconds=1000
    )


async def _seed(url: str) -> dict:
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    n = uuid.uuid4().hex[:8]
    proposer, approver = str(uuid.uuid4()), str(uuid.uuid4())
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES "
                    "('ten_' || :n, 'rc-' || :n, 'Reviews Firm') RETURNING id"
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
        for who, wos in ((proposer, f"wos_prop_{n}"), (approver, f"wos_appr_{n}")):
            await s.execute(
                text(
                    "INSERT INTO auth.users (id, uid, tenant_id, email, workos_user_id) "
                    "VALUES (CAST(:id AS uuid), :uid, :t, :email, :wos)"
                ),
                {
                    "id": who,
                    "uid": f"usr_{who[:8]}",
                    "t": tid,
                    "email": f"{who[:6]}@x.io",
                    "wos": wos,
                },
            )
    await eng.dispose()
    return {
        "tid": int(tid),
        "cid": int(cid),
        "cuid": f"con_{n}",
        "proposer": proposer,
        "approver": approver,
        "approver_wos": f"wos_appr_{n}",
    }


async def _hold(engine, url, env, *, key, payload):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        before = await handler(key).capture_before(s, tenant_id=env["tid"], payload=payload)
        result = await baton_reviews.hold(
            s,
            tenant_id=env["tid"],
            spec=require_wired(key),
            policy={"reviewer_role_id": 7, "reviewer_role_slug": "reviewer"},
            subject_type="contact",
            subject_id=env["cuid"],
            contact_id=env["cid"],
            payload=payload,
            before=before,
            justification="Client asked us to",
            idempotency_key=f"{key}:{env['cuid']}",
            actor=env["proposer"],
            workos_user_id=f"wos_prop_{env['cuid'][4:]}",
        )
    await eng.dispose()
    return result


async def _approve_and_drain(engine, url, env, uid):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        out = await baton_reviews.approve(
            s,
            tenant_id=env["tid"],
            uid=uid,
            workos_user_id=env["approver_wos"],
            note=None,
            role_ids=[7],
        )
    await eng.dispose()
    worker = Worker(engine, poll_seconds=1)
    while await worker.run_once():
        pass
    return out


async def _contact(url, cid, cols):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        row = (
            (
                await s.execute(
                    text(f"SELECT {cols} FROM crm.contacts WHERE id = :c"),  # noqa: S608 - fixed cols
                    {"c": cid},
                )
            )
            .mappings()
            .one()
        )
    await eng.dispose()
    return dict(row)


async def test_vulnerability_hold_then_approve_applies(db, monkeypatch):
    env = await _seed(db["app"])
    engine = _engine(db["app"])
    monkeypatch.setattr("app.reviews.baton.get_engine", lambda: engine)

    payload = {
        "contact_id": env["cid"],
        "vulnerability": {"is_vulnerable": True, "reasons": ["health"], "notes": "temporary"},
    }
    held = await _hold(engine, db["app"], env, key="client.vulnerability_updated", payload=payload)
    assert held.held
    # not applied until the worker runs (seeded contact has no vulnerability recorded yet)
    assert (await _contact(db["app"], env["cid"], "is_vulnerable"))["is_vulnerable"] is not True

    out = await _approve_and_drain(engine, db["app"], env, held.review_uid)
    assert out["status"] == "approved"
    assert out["summary"] == "Change the vulnerability record"
    row = await _contact(db["app"], env["cid"], "is_vulnerable, vulnerability_reasons")
    assert row["is_vulnerable"] is True
    assert list(row["vulnerability_reasons"]) == ["health"]


async def test_details_hold_then_approve_applies_with_a_date(db, monkeypatch):
    env = await _seed(db["app"])
    engine = _engine(db["app"])
    monkeypatch.setattr("app.reviews.baton.get_engine", lambda: engine)

    payload = {
        "contact_id": env["cid"],
        "fields": {"first_name": "Bob", "dob": "1990-05-05"},
    }
    held = await _hold(engine, db["app"], env, key="client.details_changed", payload=payload)
    assert held.held
    assert (await _contact(db["app"], env["cid"], "first_name"))["first_name"] == "Ada"

    out = await _approve_and_drain(engine, db["app"], env, held.review_uid)
    assert out["status"] == "approved"
    # the ISO date string in the stored payload applies cleanly to the date column
    row = await _contact(db["app"], env["cid"], "first_name, dob")
    assert row["first_name"] == "Bob"
    assert str(row["dob"]) == "1990-05-05"


async def _hold_keyed(engine, url, env, *, key, payload, idem):
    """``_hold`` with its own idempotency key, so a second proposal is a second run."""
    from app.reviews.service import ReviewError

    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            before = await handler(key).capture_before(s, tenant_id=env["tid"], payload=payload)
            return await baton_reviews.hold(
                s,
                tenant_id=env["tid"],
                spec=require_wired(key),
                policy={"reviewer_role_id": 7, "reviewer_role_slug": "reviewer"},
                subject_type="contact",
                subject_id=env["cuid"],
                contact_id=env["cid"],
                payload=payload,
                before=before,
                justification="Client asked us to",
                idempotency_key=idem,
                actor=env["proposer"],
                workos_user_id=f"wos_prop_{env['cuid'][4:]}",
            )
    except ReviewError as exc:
        return exc
    finally:
        await eng.dispose()


async def test_detail_holds_wait_per_card_and_a_second_edit_joins_its_case(db, monkeypatch):
    """Five cards share `client.details_changed`. A hold on one card must not stop another card
    being edited (each snapshots only its own fields), and an edit to a field already waiting
    JOINS that case — the same case number comes back, flagged amended, with the merged fields —
    rather than being refused."""
    env = await _seed(db["app"])
    engine = _engine(db["app"])
    monkeypatch.setattr("app.reviews.baton.get_engine", lambda: engine)
    key = "client.details_changed"

    first = await _hold_keyed(
        engine,
        db["app"],
        env,
        key=key,
        payload={"contact_id": env["cid"], "fields": {"first_name": "Bob"}},
        idem=f"{key}:{env['cuid']}:contact-card",
    )
    assert first.held
    second = await _hold_keyed(
        engine,
        db["app"],
        env,
        key=key,
        payload={"contact_id": env["cid"], "fields": {"dob": "1990-05-05"}},
        idem=f"{key}:{env['cuid']}:personal-card",
    )
    assert second.held and second.review_uid != first.review_uid

    joined = await _hold_keyed(
        engine,
        db["app"],
        env,
        key=key,
        payload={"contact_id": env["cid"], "fields": {"first_name": "Carol", "email": "c@x.io"}},
        idem=f"{key}:{env['cuid']}:contact-card-again",
    )
    assert joined.held and joined.review_uid == first.review_uid
    assert joined.body["case_no"] == first.body["case_no"]
    assert joined.body["amended"] is True
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        case = await baton_reviews.get_request(
            s, uid=first.review_uid, caller_id=env["approver"], caller_role_ids=[7]
        )
    await eng.dispose()
    assert case["payload"]["fields"] == {"first_name": "Carol", "email": "c@x.io"}
    assert second.review_uid != joined.review_uid  # the personal card's own case stands


async def test_a_second_vulnerability_hold_replaces_the_open_case(db, monkeypatch):
    env = await _seed(db["app"])
    engine = _engine(db["app"])
    monkeypatch.setattr("app.reviews.baton.get_engine", lambda: engine)
    key = "client.vulnerability_updated"
    payload = {"contact_id": env["cid"], "vulnerability": {"is_vulnerable": True, "reasons": []}}
    first = await _hold_keyed(
        engine, db["app"], env, key=key, payload=payload, idem=f"{key}:{env['cuid']}:one"
    )
    assert first.held
    again = await _hold_keyed(
        engine,
        db["app"],
        env,
        key=key,
        payload={**payload, "vulnerability": {"is_vulnerable": False, "reasons": []}},
        idem=f"{key}:{env['cuid']}:two",
    )
    # one hold per record for this card: the second proposal IS the case now
    assert again.held and again.review_uid == first.review_uid
    assert again.body["amended"] is True
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        case = await baton_reviews.get_request(
            s, uid=first.review_uid, caller_id=env["approver"], caller_role_ids=[7]
        )
    await eng.dispose()
    assert case["payload"]["vulnerability"]["is_vulnerable"] is False
