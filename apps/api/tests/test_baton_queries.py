"""The run projections behind the actions screens: a run as its tree (``project_node`` /
``run_detail``), the server-paged listing (``list_all_runs``), the approvals queue, and the one
status fold the Python side and the SQL side must agree on."""

from __future__ import annotations

import uuid

import pytest
from baton import Engine, Subject, states
from baton.registry import default_registry
from baton.store.memory import FakeClock
from baton.store.postgres import PostgresStore
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.actions.defs  # noqa: F401 - registers the migrated actions
from app.actions.attempt import Attempt
from app.actions.hooks import GrapheneHooks
from app.actions.queries import (
    _STATUS_SQL,
    first_problem,
    project_node,
    status_word,
    summarise_failure,
)
from app.actions.redact import firm_visible

pytestmark = pytest.mark.asyncio


def _engine(url: str) -> Engine:
    eng = create_async_engine(url)
    hooks = GrapheneHooks()
    store = PostgresStore(eng, schema="baton", hooks=hooks, clock=FakeClock())
    return Engine(
        store, default_registry, hooks=hooks, redact=firm_visible, default_budget_seconds=1000
    )


async def _seed_account(url: str) -> tuple[int, int, str]:
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    n = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES "
                    "('ten_' || :n, 'q-' || :n, 'Query Firm') RETURNING id"
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
    return int(tid), int(aid), f"acc_{n}"


async def _detail(engine, url, tid, aid, auid, key):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await engine.start(
            engine.store.bind(s),
            engine.registry.require("transaction.payment"),
            tenant_id=tid,
            params={"account_id": aid},
            idempotency_key=key,
            subjects=[Subject("account", auid, aid)],
        )
    await engine.advance(sr.run.uid)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await engine.get(engine.store.bind(s), sr.run.uid, tenant_id=tid)
    await eng.dispose()
    return detail


# ---- the fold ----------------------------------------------------------------------------


def _every_state():
    return [
        value
        for value in vars(states).values()
        if isinstance(value, states.State) and value.type is not states.StateType.PENDING
    ] + [states.PENDING]


async def test_status_word_agrees_with_the_sql_fold(db):
    """One fold, two implementations: every (state_type, state_name) baton can write must land on
    the same word in Python and in the listing's CASE."""
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s:
        for state in _every_state():
            sql = (
                f"SELECT ({_STATUS_SQL}) AS word FROM "  # noqa: S608 - a module constant
                "(VALUES (CAST(-1 AS bigint), CAST(:type AS text), CAST(:name AS text))) "
                "AS r(id, state_type, state_name)"
            )
            word = (
                await s.execute(text(sql), {"type": state.type.value, "name": state.name})
            ).scalar_one()
            assert word == status_word(state.type.value, state.name), state
    await eng.dispose()


def test_status_word_folds_each_family():
    assert status_word("completed", "Completed") == "succeeded"
    assert status_word("completed", "Skipped") == "skipped"
    assert status_word("completed", "Partial") == "partial"
    assert status_word("failed", "NeedsAttention") == "needs_attention"
    assert status_word("failed", "TimedOut") == "timed_out"
    assert status_word("cancelled", "Cancelled") == "cancelled"
    assert status_word("cancelled", "Rejected") == "rejected"
    assert status_word("paused", "AwaitingApproval") == "awaiting_approval"
    assert status_word("paused", "AwaitingChildren") == "running"
    assert status_word("paused", "AwaitingChildren", waiting_below=True) == "waiting"
    assert status_word("scheduled", "Waiting") == "waiting"
    assert status_word("scheduled", "AwaitingRetry") == "waiting"
    assert status_word("scheduled", "Resumed") == "running"
    assert status_word("crashed", "Crashed") == "running"


# ---- the provider's reason ---------------------------------------------------------------


def test_summarise_failure_reads_the_sei_reason():
    body = {
        "error": {
            "code": "swp.psmModelSoa.businessValidationException",
            "message": "Business Exception",
            "userMessage": "Business Exception",
            "trackingId": "09d61668-df8d-4704-86b4-75e49fe16fa9",
            "errors": [
                {
                    "field": "/modelId",
                    "location": "/PSM/model/assignment/investment",
                    "reason": "Provided model id is either invalid or cannot be associated "
                    "with the portfolio : 90269",
                }
            ],
            "transient": "false",
        }
    }
    message = (
        "SEI did not assign the model: POST /pmemodelassignmentservices/PSM/model/assignment/"
        'investment failed: 422 {"error":{"code":"swp.psmModelSoa.businessValidationException"}}'
    )
    out = summarise_failure(message, response=body, status_code=422)
    assert out["headline"].startswith("Provided model id is either invalid")
    assert out["detail"] == "Business Exception"
    assert out["code"] == "swp.psmModelSoa.businessValidationException"
    assert out["tracking_id"] == "09d61668-df8d-4704-86b4-75e49fe16fa9"
    assert out["transient"] is False
    assert out["status_code"] == 422


def test_summarise_failure_trims_a_json_tail_and_reads_plain_bodies():
    # the vendor's words lead even when they only arrived in the message's tail
    out = summarise_failure('Seccl refused: 400 {"message": "No cash"}', response=None)
    assert out["headline"] == "No cash"
    assert out["detail"] == "Seccl refused: 400"
    out = summarise_failure("Seccl refused: 400 [not json", response=None)
    assert out["headline"] == "Seccl refused: 400 [not json"
    out = summarise_failure("no cash", response={"error": "no cash"})
    assert out["headline"] == "no cash"
    assert out["detail"] is None
    out = summarise_failure("The custodian refused.", response={"message": "Account is closed."})
    assert out["headline"] == "Account is closed."
    assert out["detail"] == "The custodian refused."
    assert summarise_failure(None)["headline"] is None


# ---- the tree, on an in-memory engine ----------------------------------------------------


async def test_project_node_badges_a_wait_as_a_wait_not_a_failure():
    """A NotReady wait is an attempt with outcome 'wait' under a node that then succeeds. The
    old projection folded that to ok=False, which the UI drew as a red Failed."""
    from baton import NotReady, action
    from baton.registry import Registry
    from baton.store.memory import MemoryStore

    registry = Registry()
    clock = FakeClock()
    store = MemoryStore(clock=clock)
    engine = Engine(store, registry, lease_seconds=60, default_budget_seconds=1000)
    calls = {"n": 0}

    @action(key="t.wait", name="Wait for it", wait_timeout_seconds=100, registry=registry)
    async def wait(ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            raise NotReady("account is Pre Open", delay=5)
        ctx.evidence(method="POST", url="/payto", response={"ok": True})
        return {"ok": True}

    async with store.unit() as u:
        sr = await engine.start(u, wait, tenant_id=1, params={}, idempotency_key="w1")
    await engine.advance(sr.run.uid)
    clock.advance(6)
    await engine.advance(sr.run.uid)
    async with store.unit() as u:
        detail = await engine.get(u, sr.run.uid, tenant_id=1)

    node = project_node(detail, registry=registry)
    assert [a["outcome"] for a in node["attempts"]] == ["wait", "ok"]
    assert node["attempts"][0]["error"] == "account is Pre Open"
    assert node["status"] == "succeeded"
    assert node["failure"] is None
    assert node["passes"] >= 2
    assert node["children"] == []


async def test_project_node_keeps_the_tree_and_points_at_the_deepest_failure():
    from baton import Permanent, action
    from baton.registry import Registry
    from baton.store.memory import MemoryStore

    registry = Registry()
    store = MemoryStore(clock=FakeClock())
    engine = Engine(store, registry, lease_seconds=60, default_budget_seconds=1000)

    @action(key="t.read", name="Read the book", registry=registry)
    async def read(ctx):
        return {"custodian": "sei"}

    @action(key="t.send", name="Send it", on_failure="needs_attention", registry=registry)
    async def send(ctx):
        ctx.evidence(method="POST", url="/thing", status_code=422, response={"message": "Not Open"})
        raise Permanent("SEI refused: 422")

    @action(key="t.inner", name="Inner", registry=registry)
    async def inner(ctx):
        await ctx.run(read)
        return await ctx.run(send)

    @action(key="t.outer", name="Outer", registry=registry)
    async def outer(ctx):
        return await ctx.run(inner)

    async with store.unit() as u:
        sr = await engine.start(u, outer, tenant_id=1, params={}, idempotency_key="tree1")
    await engine.advance(sr.run.uid)
    async with store.unit() as u:
        detail = await engine.get(u, sr.run.uid, tenant_id=1)

    root = project_node(detail, registry=registry)
    assert root["status"] == "needs_attention"
    (inner_node,) = root["children"]
    assert inner_node["depth"] == 1 and inner_node["name"] == "Inner"
    read_node, send_node = inner_node["children"]
    # a book read is a node with a result, not a step with "no address"
    assert read_node["result"] == {"custodian": "sei"}
    assert read_node["status"] == "succeeded"
    # the refused send carries its call and its parsed reason
    assert send_node["attempts"][0]["outcome"] == "failed"
    assert send_node["attempts"][0]["status_code"] == 422
    assert send_node["failure"]["headline"] == "Not Open"
    assert send_node["on_failure"] == "needs_attention"
    # the problem to point a person at is the leaf, not the composite reporting it
    assert first_problem(root)["uid"] == send_node["uid"]


# ---- against Postgres, through the real actions ------------------------------------------


async def test_run_detail_returns_the_tree_with_names_and_advice(db, monkeypatch):
    from app.actions import queries

    tid, aid, auid = await _seed_account(db["app"])
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        return Attempt(
            ok=True,
            method="POST",
            url="/portfoliotransactiongroup",
            response={"id": "PTG-1", "status": "submitted"},
        )

    monkeypatch.setattr("app.integrations.seccl.actions.seccl_payment", fake)
    detail = await _detail(engine, db["app"], tid, aid, auid, "qdet1")

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        out = await queries.run_detail(s, tenant_id=tid, run_uid=detail.run.uid)
    await eng.dispose()

    assert out["name"] == "Payment"
    assert out["status"] == "succeeded"
    assert out["subject_uid"] == auid
    # the records it is about, by name, so the header can link them
    assert out["contact"]["name"] == "Ada Okonkwo"
    assert out["account"]["uid"] == auid
    assert out["account"]["custodian"] == "seccl"
    # the tree: the book read, then the seccl send with its call
    keys = [child["action_key"] for child in out["children"]]
    assert keys == ["load.account_book", "seccl.payment"]
    send = out["children"][1]
    assert send["provider"] == "seccl"
    assert send["attempts"][0]["url"] == "/portfoliotransactiongroup"
    assert send["attempts"][0]["outcome"] == "ok"
    assert out["children"][0]["provider"] is None
    assert out["children"][0]["result"]["custodian"] == "seccl"
    # the advice a screen turns into one next action
    assert out["approval"] == {
        "required": False,
        "roles": [],
        "gate_uid": None,
        "held_uid": None,
        "held_name": None,
        "held_since": None,
        "can_decide": False,
        "eligible_approvers": None,
        "reminded_at": None,
        "reminders": 0,
    }
    assert out["change"] is None
    assert out["attention"] is None
    assert out["can_retry"] is False
    assert out["can_cancel"] is False
    assert out["timing"]["calls"] == 2
    # the lifecycle, from baton.events
    assert [e["type"] for e in out["events"]][0] == "run.created"
    assert "run.completed" in {e["type"] for e in out["events"]}


async def test_run_detail_names_the_refused_node(db, monkeypatch):
    from app.actions import queries

    tid, aid, auid = await _seed_account(db["app"])
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        return Attempt(
            ok=False,
            method="POST",
            url="/portfoliotransactiongroup",
            response={"error": "no cash"},
            error="no cash",
        )

    monkeypatch.setattr("app.integrations.seccl.actions.seccl_payment", fake)
    detail = await _detail(engine, db["app"], tid, aid, auid, "qpay2")

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        out = await queries.run_detail(s, tenant_id=tid, run_uid=detail.run.uid)
    await eng.dispose()

    assert out["status"] == "needs_attention"
    send = out["children"][1]
    assert send["status"] == "needs_attention"
    assert send["attempts"][0]["outcome"] == "failed"
    assert out["attention"]["node_uid"] == send["uid"]
    assert out["attention"]["headline"] == "no cash"
    assert out["attention"]["provider"] == "seccl"
    assert out["can_retry"] is True
    assert "run.needs_attention" in {e["type"] for e in out["events"]}


async def test_cancel_stops_a_held_run_and_the_detail_says_so(db):
    from baton import Approval

    from app.actions import queries

    tid, aid, auid = await _seed_account(db["app"])
    engine = _engine(db["app"])
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sr = await engine.start(
            engine.store.bind(s),
            engine.registry.require("transaction.payment"),
            tenant_id=tid,
            params={"account_id": aid},
            idempotency_key="cancel1",
            subjects=[Subject("account", auid, aid)],
            approval=Approval(roles=[7]),
        )
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        held = await queries.run_detail(s, tenant_id=tid, run_uid=sr.run.uid)
        assert held["status"] == "awaiting_approval"
        assert held["approval"]["required"] is True
        assert held["approval"]["gate_uid"] is not None
        assert held["can_cancel"] is True
        await engine.cancel(engine.store.bind(s), sr.run.uid, actor_id=None, note="raised twice")
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        out = await queries.run_detail(s, tenant_id=tid, run_uid=sr.run.uid)
    await eng.dispose()
    assert out["status"] == "cancelled"
    assert out["can_cancel"] is False
    # the gate went with it
    gate = next(child for child in out["children"] if child["kind"] == "gate")
    assert gate["status"] == "cancelled"
    assert "run.cancelled" in {e["type"] for e in out["events"]}


async def test_list_pending_approvals_lists_held_runs(db):
    from baton import Approval

    from app.actions import queries

    tid, aid, auid = await _seed_account(db["app"])
    engine = _engine(db["app"])

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await engine.start(
            engine.store.bind(s),
            engine.registry.require("transaction.payment"),
            tenant_id=tid,
            params={"account_id": aid},
            idempotency_key="appr1",
            subjects=[Subject("account", auid, aid)],
            approval=Approval(roles=[7]),
        )
    me = "11111111-1111-1111-1111-111111111111"
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        mine = await queries.list_pending_approvals(
            s, tenant_id=tid, caller_id=me, caller_role_ids=[7]
        )
        other = await queries.list_pending_approvals(
            s, tenant_id=tid, caller_id=me, caller_role_ids=[8]
        )
        # the listing agrees: the held row is awaiting approval and decidable by role 7
        listed = await queries.list_all_runs(
            s, tenant_id=tid, state="awaiting_approval", caller_id=me, caller_role_ids=[7]
        )
    await eng.dispose()

    assert len(mine) == 1
    row = mine[0]
    assert row["action_key"] == "transaction.payment"
    assert row["name"] == "Payment"
    assert row["subject_uid"] == auid
    assert row["can_decide"] is True
    assert other[0]["can_decide"] is False
    assert listed["total"] == 1
    assert listed["items"][0]["can_decide"] is True
    assert listed["items"][0]["children"][0]["kind"] == "gate"


async def test_list_all_runs_pages_filters_and_facets(db, monkeypatch):
    from app.actions import queries

    tid, aid, auid = await _seed_account(db["app"])
    engine = _engine(db["app"])

    async def fake(session, *, payload, subjects):
        return Attempt(ok=True, method="POST", url="/ptg", response={"id": "PTG"})

    monkeypatch.setattr("app.integrations.seccl.actions.seccl_payment", fake)
    for key in ("qall1", "qall2", "qall3"):
        await _detail(engine, db["app"], tid, aid, auid, key)

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        page = await queries.list_all_runs(s, tenant_id=tid, limit=2)
        rest = await queries.list_all_runs(s, tenant_id=tid, limit=2, offset=2)
        succeeded = await queries.list_all_runs(s, tenant_id=tid, state="succeeded")
        none = await queries.list_all_runs(s, tenant_id=tid, state="needs_attention")
        seccl = await queries.list_all_runs(s, tenant_id=tid, provider="seccl")
        sei = await queries.list_all_runs(s, tenant_id=tid, provider="sei")
    await eng.dispose()

    # paged: total counts the whole match set, the page is the slice
    assert page["total"] == 3 and len(page["items"]) == 2
    assert rest["total"] == 3 and len(rest["items"]) == 1
    assert {r["uid"] for r in page["items"]}.isdisjoint({r["uid"] for r in rest["items"]})
    row = page["items"][0]
    assert row["action_key"] == "transaction.payment"
    assert row["name"] == "Payment"
    assert row["status"] == "succeeded"
    assert row["providers"] == ["seccl"]
    # the step bar carries each child's name and status, not a bare tone
    assert [c["key"] for c in row["children"]] == ["load.account_book", "seccl.payment"]
    assert row["children"][1]["name"] == "Seccl payment"
    assert row["children"][1]["status"] == "succeeded"
    # the stat tiles: the runs seat's six, and the approvals seat's own
    assert set(page["stats"]) == {
        "running",
        "waiting",
        "awaiting_approval",
        "needs_attention",
        "failed_7d",
        "completed_24h",
        "held_over_day",
        "held_undecidable",
        "money_held",
        "decided_7d",
        "approved_7d",
        "rejected_7d",
    }
    # facets with labels
    assert "transaction.payment" in page["facets"]["action"]
    assert page["facet_labels"]["action"]["transaction.payment"] == "Payment"
    assert page["facets"]["provider"] == ["seccl"]
    assert page["facet_labels"]["provider"]["seccl"] == "Seccl"
    assert "succeeded" in page["facets"]["state"]
    # the filters narrow the rows
    assert all(r["status"] == "succeeded" for r in succeeded["items"])
    assert none["total"] == 0
    assert seccl["total"] == 3 and sei["total"] == 0


def test_summarise_failure_reads_the_body_carried_in_the_message_tail():
    """A leaf that raised Permanent(str(exc)) without recording evidence still carried the
    vendor's JSON in its message; the reason inside it is the headline, not the raw string."""
    message = (
        "SEI did not assign the model: POST /pmemodelassignmentservices/PSM/model/assignment/"
        'investment failed: 422 {"error":{"code":"swp.psmModelSoa.businessValidationException",'
        '"message":"Business Exception","userMessage":"Business Exception","trackingId":"09d6",'
        '"errors":[{"field":"/modelId","reason":"Provided model id is either invalid or cannot be '
        'associated with the portfolio : 90269"}],"transient":"false"}}'
    )
    out = summarise_failure(message, response=None, status_code=None)
    assert out["headline"].startswith("Provided model id is either invalid")
    assert out["detail"] == "Business Exception"
    assert out["tracking_id"] == "09d6"
    assert out["transient"] is False


def test_attempt_row_recovers_the_call_from_a_message_that_recorded_none():
    """A refusal that recorded no evidence (the leaves predating ctx.evidence on failure) still
    carried the call in its message; the row reads the method, path, code and body out of it
    rather than showing the leaf's params as the request and "no response body"."""
    from datetime import UTC, datetime

    from baton import AttemptView

    from app.actions.queries import _attempt_row

    when = datetime(2026, 10, 21, 14, 53, 4, tzinfo=UTC)
    bare = AttemptView(
        attempt_no=1,
        outcome="failed",
        started_at=when,
        ended_at=when,
        duration_ms=3200,
        error=(
            "Morgan Lloyd could not be reached: POST cam_createcontact failed: 400 "
            '{"error":{"code":"0x80040224","message":"ni already used"}}'
        ),
        error_type="Permanent",
        method=None,
        url=None,
        status_code=None,
        request={"enrol": False, "account_id": 10, "contact_id": 43},
        response=None,
    )
    row = _attempt_row(bare)
    assert row["kind"] == "call"
    assert row["recovered"] is True
    assert row["method"] == "POST"
    assert row["url"] == "cam_createcontact"
    assert row["status_code"] == 400
    assert row["response"] == {"error": {"code": "0x80040224", "message": "ni already used"}}
    # a function leaf stays a function: its params are its input, not a request
    read = AttemptView(
        attempt_no=1,
        outcome="ok",
        started_at=when,
        ended_at=when,
        duration_ms=2,
        error=None,
        error_type=None,
        method=None,
        url=None,
        status_code=None,
        request={"account_id": 10},
        response={"custodian": "sei"},
    )
    assert _attempt_row(read)["kind"] == "function"
    assert _attempt_row(read)["recovered"] is False
