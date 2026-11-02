"""The four-eye reviews fold on baton (Phase 3, part 2): a held review is a baton run with an
approval gate. hold → inbox → approve → the worker applies → the record changes. reject closes it
without applying. Driven through app.reviews.baton (what service.py delegates to behind the flag).
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
from app.reviews.catalogue import require_wired

pytestmark = pytest.mark.asyncio

_KEY = "contact.bank_details_changed"


def _engine(url: str) -> Engine:
    eng = create_async_engine(url)
    hooks = GrapheneHooks()
    store = PostgresStore(eng, schema="baton", hooks=hooks, clock=FakeClock())
    return Engine(
        store, default_registry, hooks=hooks, redact=firm_visible, default_budget_seconds=1000
    )


async def _seed(url: str, *, bank: str) -> dict:
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
                    "('ten_' || :n, 'rv-' || :n, 'Reviews Firm') RETURNING id"
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
        "proposer_wos": f"wos_prop_{n}",
        "approver": approver,
        "approver_wos": f"wos_appr_{n}",
    }


async def _hold(engine, url, env, payload, before, *, key_suffix=""):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        result = await baton_reviews.hold(
            s,
            tenant_id=env["tid"],
            spec=require_wired(_KEY),
            policy={"reviewer_role_id": 7, "reviewer_role_slug": "reviewer"},
            subject_type="contact",
            subject_id=env["cuid"],
            contact_id=env["cid"],
            payload=payload,
            before=before,
            justification=None,
            idempotency_key=f"{_KEY}:{env['cuid']}{key_suffix}",
            actor=env["proposer"],
            workos_user_id=env["proposer_wos"],
        )
    await eng.dispose()
    return result


async def _bank(url, env):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        row = (
            await s.execute(
                text("SELECT bank_account FROM crm.contacts WHERE id = :c"), {"c": env["cid"]}
            )
        ).scalar_one()
    await eng.dispose()
    return row


async def test_hold_then_approve_applies_on_the_worker(db, monkeypatch):
    env = await _seed(
        db["app"], bank='{"name": "Old", "sort_code": "11-11-11", "account_number": "11111111"}'
    )
    engine = _engine(db["app"])
    monkeypatch.setattr("app.reviews.baton.get_engine", lambda: engine)

    payload = {
        "contact_id": env["cid"],
        "bank_account": {"name": "New", "sort_code": "22-22-22", "account_number": "22222222"},
    }
    before = {"value": {"name": "Old", "sort_code": "11-11-11", "account_number": "11111111"}}
    held = await _hold(engine, db["app"], env, payload, before)
    assert held.held
    uid = held.review_uid

    # the review is in the inbox, pending, decidable by the approver's role
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        got = await baton_reviews.get_request(
            s, uid=uid, caller_id=env["approver"], caller_role_ids=[7]
        )
    await eng.dispose()
    assert got["status"] == "pending"
    assert got["can_decide"] is True
    assert got["summary"] == "Change bank details"

    # approve: the gate releases, the apply is scheduled, nothing applied yet
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
    assert out["status"] == "approved"
    assert (await _bank(db["app"], env))["name"] == "Old"  # worker has not run yet

    worker = Worker(engine, poll_seconds=1)
    while await worker.run_once():
        pass
    assert (await _bank(db["app"], env))["name"] == "New"


async def test_requester_cannot_approve_their_own_review(db, monkeypatch):
    env = await _seed(db["app"], bank='{"name": "Old"}')
    engine = _engine(db["app"])
    monkeypatch.setattr("app.reviews.baton.get_engine", lambda: engine)

    held = await _hold(
        engine,
        db["app"],
        env,
        {"contact_id": env["cid"], "bank_account": {"name": "New"}},
        {"value": {"name": "Old"}},
    )
    from app.reviews.service import ReviewError

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        with pytest.raises(ReviewError) as caught:
            # the proposer's own login cannot approve → ReviewError(403)
            await baton_reviews.approve(
                s,
                tenant_id=env["tid"],
                uid=held.review_uid,
                workos_user_id=env["proposer_wos"],  # the proposer's own login
                note=None,
                role_ids=[7],
            )
    await eng.dispose()
    assert caught.value.status == 403


async def test_reject_closes_without_applying(db, monkeypatch):
    env = await _seed(db["app"], bank='{"name": "Old"}')
    engine = _engine(db["app"])
    monkeypatch.setattr("app.reviews.baton.get_engine", lambda: engine)

    held = await _hold(
        engine,
        db["app"],
        env,
        {"contact_id": env["cid"], "bank_account": {"name": "New"}},
        {"value": {"name": "Old"}},
    )
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        out = await baton_reviews.reject(
            s,
            tenant_id=env["tid"],
            uid=held.review_uid,
            workos_user_id=env["approver_wos"],
            note="Wrong account",
            role_ids=[7],
        )
    await eng.dispose()
    assert out["status"] == "rejected"

    worker = Worker(engine, poll_seconds=1)
    while await worker.run_once():
        pass
    assert (await _bank(db["app"], env))["name"] == "Old"  # never applied

    # a reject records review.rejected, never a spurious review.conflicted (the terminal hook only
    # treats a failed APPLY as a conflict, not a reject/cancel)
    eng2 = create_async_engine(db["app"])
    maker2 = async_sessionmaker(eng2, expire_on_commit=False)
    async with maker2() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        events = (
            (
                await s.execute(
                    text("SELECT event_type FROM audit.actions WHERE tenant_id = :t"),
                    {"t": env["tid"]},
                )
            )
            .scalars()
            .all()
        )
    await eng2.dispose()
    assert "review.rejected" in events
    assert "review.conflicted" not in events


async def test_a_held_review_shows_in_the_approvals_queue(db, monkeypatch):
    # The Approvals tab is one queue for everything awaiting approval, so a held book review appears
    # there (flagged is_review for the page to route its decision), not only in Runs.
    from app.actions import queries

    env = await _seed(db["app"], bank='{"name": "Old"}')
    engine = _engine(db["app"])
    monkeypatch.setattr("app.reviews.baton.get_engine", lambda: engine)
    held = await _hold(
        engine,
        db["app"],
        env,
        {"contact_id": env["cid"], "bank_account": {"name": "New"}},
        {"value": {"name": "Old"}},
    )
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        approvals = await queries.list_pending_approvals(
            s, tenant_id=env["tid"], caller_id=env["approver"], caller_role_ids=[7]
        )
    await eng.dispose()
    row = next(a for a in approvals if a["uid"] == held.review_uid)
    assert row["is_review"] is True
    assert row["can_decide"] is True
    assert row["name"] == "Update client bank account"


async def test_only_the_proposer_can_cancel(db, monkeypatch):
    env = await _seed(db["app"], bank='{"name": "Old"}')
    engine = _engine(db["app"])
    monkeypatch.setattr("app.reviews.baton.get_engine", lambda: engine)
    held = await _hold(
        engine,
        db["app"],
        env,
        {"contact_id": env["cid"], "bank_account": {"name": "New"}},
        {"value": {"name": "Old"}},
    )
    from app.reviews.service import ReviewError

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    # a non-proposer (here the approver) may NOT cancel someone else's pending review
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        with pytest.raises(ReviewError) as caught:
            await baton_reviews.cancel(
                s, tenant_id=env["tid"], uid=held.review_uid, workos_user_id=env["approver_wos"]
            )
    assert caught.value.status == 403
    # the proposer may
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        out = await baton_reviews.cancel(
            s, tenant_id=env["tid"], uid=held.review_uid, workos_user_id=env["proposer_wos"]
        )
    await eng.dispose()
    assert out["status"] == "cancelled"


async def test_queue_lists_a_held_provider_action_with_its_input(db, monkeypatch):
    """The Reviews queue is the firm's whole approval queue: a payment an approver role holds
    sits beside the bank-details reviews, numbered, decidable through the run's own route
    (`source: 'action'`), with the input its door recorded as its body. The record strips stay
    book-review only."""
    from baton import Approval, Subject

    env = await _seed(db["app"], bank='{"name": "Old"}')
    engine = _engine(db["app"])
    monkeypatch.setattr("app.reviews.baton.get_engine", lambda: engine)
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    auid = f"acc_{uuid.uuid4().hex[:8]}"
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        aid = (
            await s.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, uid, contact_id, custodian, "
                    "  name, status) VALUES (:t, :u, :c, CAST('sei' AS platform.provider_code), "
                    "  'Oakden SIPP', 'active') RETURNING id"
                ),
                {"t": env["tid"], "u": auid, "c": env["cid"]},
            )
        ).scalar_one()
        held = await engine.start(
            engine.store.bind(s),
            engine.registry.require("transaction.payment"),
            tenant_id=env["tid"],
            params={"account_id": int(aid), "instruction_uid": "ins_x"},
            idempotency_key="instruction:ins_x",
            requested_by=env["proposer"],
            contact_id=env["cid"],
            subjects=[Subject("account", auid, int(aid))],
            approval=Approval(roles=[7]),
            meta={"input": {"kind": "withdrawal", "amount": 1000.0, "due_date": "2026-10-30"}},
        )
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        queue = await baton_reviews.list_requests(
            s,
            tenant_id=env["tid"],
            scope="all",
            caller_id=env["approver"],
            caller_role_ids=[7],
        )
        inbox_other = await baton_reviews.list_requests(
            s,
            tenant_id=env["tid"],
            scope="inbox",
            caller_id=env["approver"],
            caller_role_ids=[8],
        )
        one = await baton_reviews.get_request(
            s, uid=held.run.uid, caller_id=env["approver"], caller_role_ids=[7]
        )
        strip = await baton_reviews.pending_for_contact(
            s, tenant_id=env["tid"], contact_id=env["cid"], caller_id=env["approver"]
        )
    await eng.dispose()

    assert [r["uid"] for r in queue] == [held.run.uid]
    row = queue[0]
    assert row["source"] == "action"
    assert row["label"] == "Payment"
    assert row["status"] == "pending"
    assert row["case_no"] == held.run.case_no is not None
    assert row["can_decide"] is True
    assert row["account_uid"] == auid
    assert row["account_name"] == "Oakden SIPP"
    assert row["contact_uid"] == env["cuid"]
    assert row["input"] == {"kind": "withdrawal", "amount": 1000.0, "due_date": "2026-10-30"}
    # the proposer gave no reason; the Why column must not be fed a stock sentence
    assert not row["justification"]
    assert inbox_other == []
    assert one is not None and one["uid"] == held.run.uid and one["source"] == "action"
    assert strip == []

    # the book reviews' own decide path does not decide a provider action: that is the run's route
    from app.reviews.service import ReviewError

    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        with pytest.raises(ReviewError) as refused:
            await baton_reviews.approve(
                s,
                tenant_id=env["tid"],
                uid=held.run.uid,
                workos_user_id=env["approver_wos"],
                note=None,
                role_ids=[7],
            )
    assert refused.value.status == 404

    # approved through its run: the gate releases, the composite is scheduled, and the queue
    # reads it APPROVED — not pending with an Approve button that would 409 — whatever the run
    # does next
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await engine.decide(
            engine.store.bind(s),
            held.run.uid,
            decision="approve",
            actor_id=env["approver"],
            role_ids=[7],
        )
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        after = await baton_reviews.list_requests(
            s, tenant_id=env["tid"], scope="all", caller_id=env["approver"], caller_role_ids=[7]
        )
        inbox = await baton_reviews.list_requests(
            s, tenant_id=env["tid"], scope="inbox", caller_id=env["approver"], caller_role_ids=[7]
        )
    await eng.dispose()
    assert after[0]["status"] == "approved"
    assert after[0]["can_decide"] is False
    assert inbox == []


async def test_a_second_edit_while_a_case_waits_updates_the_case(db, monkeypatch):
    """An edit to a card with an open case used to be refused ("already waiting for review
    (RV-3)"). Now it lands ON the case: the proposal is merged, the snapshot retaken, the
    approvers told again, and the door answers the same case number. Once the case is approved
    and landing, a further edit is still refused — the worker is applying what was agreed."""
    from app.reviews.catalogue import require_wired

    env = await _seed(db["app"], bank='{"name": "Old"}')
    engine = _engine(db["app"])
    monkeypatch.setattr("app.reviews.baton.get_engine", lambda: engine)
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)

    async def propose(fields, *, key_suffix):
        async with maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            payload = {"contact_id": env["cid"], "fields": fields}
            before = await _details_before(s, env, payload)
            return await baton_reviews.hold(
                s,
                tenant_id=env["tid"],
                spec=require_wired("client.details_changed"),
                policy={"reviewer_role_id": 7, "reviewer_role_slug": "reviewer"},
                subject_type="contact",
                subject_id=env["cuid"],
                contact_id=env["cid"],
                payload=payload,
                before=before,
                justification=None,
                idempotency_key=f"client.details_changed:{env['cuid']}:{key_suffix}",
                actor=env["proposer"],
                workos_user_id=env["proposer_wos"],
            )

    first = await propose({"first_name": "Adaeze"}, key_suffix="a")
    assert first.held and first.body and first.body.get("amended") is not True
    # the same field again, and a new one: the case takes both
    second = await propose({"first_name": "Ada-Marie", "surname": "Okonkwo-Bell"}, key_suffix="b")
    assert second.review_uid == first.review_uid
    assert second.body and second.body["amended"] is True
    assert second.body["case_no"] == first.body["case_no"]
    # a field on another card, touching nothing the case holds, opens its own case
    third = await propose({"occupation": "Architect"}, key_suffix="c")
    assert third.review_uid != first.review_uid

    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        got = await baton_reviews.get_request(
            s, uid=first.review_uid, caller_id=env["approver"], caller_role_ids=[7]
        )
        told = (
            await s.execute(
                text(
                    "SELECT count(*) FROM audit.actions WHERE tenant_id = :t "
                    "  AND event_type = 'review.requested' "
                    "  AND metadata -> 'subject' ->> 'review_uid' = :u"
                ),
                {"t": env["tid"], "u": first.review_uid},
            )
        ).scalar_one()
    assert got["status"] == "pending"
    assert got["payload"]["fields"] == {"first_name": "Ada-Marie", "surname": "Okonkwo-Bell"}
    assert set(got["before"]) == {"first_name", "surname"}
    assert told == 2  # the hold, then the amendment

    # approved and landing: the next edit is refused until it lands
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await baton_reviews.approve(
            s,
            tenant_id=env["tid"],
            uid=first.review_uid,
            workos_user_id=env["approver_wos"],
            note=None,
            role_ids=[7],
        )
    from app.reviews.service import ReviewError

    with pytest.raises(ReviewError) as refused:
        await propose({"first_name": "Again"}, key_suffix="d")
    assert refused.value.status == 409
    assert "already approved and being applied" in str(refused.value)
    await eng.dispose()


async def _details_before(s, env, payload):
    from app.reviews.apply import handler

    return await handler("client.details_changed").capture_before(
        s, tenant_id=env["tid"], payload=payload
    )
