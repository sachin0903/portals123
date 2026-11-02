"""The approval gate on a provider action, end to end: a held run tells its approvers and its
requester (``app/actions/approvals.py`` through the engine hooks), the listing carries the gate's
facts (who may decide, how many could, since when, how much), a rejection needs a reason, a
reminder goes out once an hour, and a gated child of a composite reads as awaiting approval on
its root and lists as its own case in the Reviews queue."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from baton import Approval, Engine, Subject
from baton.registry import default_registry
from baton.store.memory import FakeClock
from baton.store.postgres import PostgresStore
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.actions.defs  # noqa: F401 - registers the actions
from app.actions import approvals, queries, service
from app.actions.hooks import GrapheneHooks
from app.actions.redact import firm_visible
from app.actions.runs import ActionError
from app.reviews import baton as baton_reviews

pytestmark = pytest.mark.asyncio


def _engine(url: str) -> Engine:
    eng = create_async_engine(url)
    hooks = GrapheneHooks()
    store = PostgresStore(eng, schema="baton", hooks=hooks, clock=FakeClock())
    return Engine(
        store, default_registry, hooks=hooks, redact=firm_visible, default_budget_seconds=1000
    )


async def _seed(url: str) -> dict:
    """A firm with a client, an SEI account, a Compliance role held by one approver, and an
    Adviser role held by the proposer."""
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
                    "('ten_' || :n, 'ap-' || :n, 'Approvals Firm') RETURNING id"
                ),
                {"n": n},
            )
        ).scalar_one()
        cid = (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                    "VALUES (:t, :u, 'individual', 'Wade', 'Wilson') RETURNING id"
                ),
                {"t": tid, "u": f"con_{n}"},
            )
        ).scalar_one()
        aid = (
            await s.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, uid, contact_id, custodian, name, "
                    "status) VALUES (:t, :u, :c, CAST('sei' AS platform.provider_code), "
                    "'Wilson GIA', 'active') RETURNING id"
                ),
                {"t": tid, "u": f"acc_{n}", "c": cid},
            )
        ).scalar_one()
        roles: dict[str, int] = {}
        for slug, name in (("compliance", "Compliance"), ("adviser", "Adviser")):
            roles[slug] = int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                            "VALUES (:t, :s, :nm, 'platform') RETURNING id"
                        ),
                        {"t": tid, "s": slug, "nm": name},
                    )
                ).scalar_one()
            )
        for who, wos, role in (
            (proposer, f"wos_prop_{n}", roles["adviser"]),
            (approver, f"wos_appr_{n}", roles["compliance"]),
        ):
            await s.execute(
                text(
                    "INSERT INTO auth.users (id, uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (CAST(:id AS uuid), :uid, :t, :email, :wos, true)"
                ),
                {
                    "id": who,
                    "uid": f"usr_{who[:8]}",
                    "t": tid,
                    "email": f"{who[:6]}@x.io",
                    "wos": wos,
                },
            )
            mid = (
                await s.execute(
                    text(
                        "INSERT INTO auth.organisation_memberships "
                        "  (user_id, tenant_id, portal, role_id, status) "
                        "VALUES (CAST(:u AS uuid), :t, 'platform', :r, 'active') RETURNING id"
                    ),
                    {"u": who, "t": tid, "r": role},
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO auth.membership_roles (membership_id, role_id) "
                    "VALUES (:m, :r) ON CONFLICT DO NOTHING"
                ),
                {"m": int(mid), "r": role},
            )
    await eng.dispose()
    return {
        "tid": int(tid),
        "cid": int(cid),
        "cuid": f"con_{n}",
        "aid": int(aid),
        "auid": f"acc_{n}",
        "proposer": proposer,
        "approver": approver,
        "compliance": roles["compliance"],
        "adviser": roles["adviser"],
        "n": n,
    }


async def _hold(engine, url, env, *, key, requested_by, roles, amount=None):
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        result = await engine.start(
            engine.store.bind(s),
            engine.registry.require("transaction.payment"),
            tenant_id=env["tid"],
            params={"account_id": env["aid"], "instruction_uid": f"ins_{key}"},
            idempotency_key=f"instruction:{key}",
            requested_by=requested_by,
            contact_id=env["cid"],
            subjects=[Subject("account", env["auid"], env["aid"])],
            approval=Approval(roles=roles),
            meta={"input": {"kind": "expected_in", "amount": amount}} if amount else None,
        )
    await eng.dispose()
    return result.run


async def _review_events(url, tid) -> list[dict]:
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        rows = (
            await s.execute(
                text(
                    "SELECT event_type, metadata FROM audit.actions "
                    "WHERE tenant_id = :t AND event_type LIKE 'review.%' ORDER BY id"
                ),
                {"t": tid},
            )
        ).mappings()
        out = []
        for row in rows:
            meta = row["metadata"]
            meta = json.loads(meta) if isinstance(meta, str) else meta
            out.append({"type": row["event_type"], "subject": (meta or {}).get("subject") or {}})
    await eng.dispose()
    return out


# ---- the lifecycle ------------------------------------------------------------------------


async def test_a_held_provider_action_tells_its_approvers_and_then_its_requester(db):
    """A payment an approver role holds used to sit silent: the engine wrote `run.held` and nothing
    told the approvers, and a decision told nobody. Both now ride the review.* events the
    notification rules already listen for."""
    env = await _seed(db["app"])
    engine = _engine(db["app"])
    run = await _hold(
        engine, db["app"], env, key="n1", requested_by=env["proposer"], roles=[env["compliance"]]
    )
    events = await _review_events(db["app"], env["tid"])
    assert [e["type"] for e in events] == ["review.requested"]
    requested = events[0]["subject"]
    assert requested["review_uid"] == run.uid
    assert requested["action_key"] == "transaction.payment"
    assert requested["reviewer_role_slugs"] == ["compliance"]
    assert requested["reviewer_role_slug"] == "compliance"
    assert requested["proposed_by"] == env["proposer"]
    assert requested["notify_in_app"] is True
    assert "reminder" not in requested

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await engine.decide(
            engine.store.bind(s),
            run.uid,
            decision="reject",
            actor_id=env["approver"],
            role_ids=[env["compliance"]],
            note="Not this month",
        )
    await eng.dispose()
    events = await _review_events(db["app"], env["tid"])
    assert [e["type"] for e in events] == ["review.requested", "review.rejected"]
    assert events[1]["subject"]["review_uid"] == run.uid
    assert events[1]["subject"]["proposed_by"] == env["proposer"]


async def test_a_held_book_review_is_not_told_twice(db, monkeypatch):
    """The reviews fold records its own review.requested when it holds; the engine hook must not
    add a second one for the same gate."""
    from app.reviews.catalogue import require_wired

    env = await _seed(db["app"])
    engine = _engine(db["app"])
    monkeypatch.setattr("app.reviews.baton.get_engine", lambda: engine)
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text('UPDATE crm.contacts SET bank_account = \'{"name": "Old"}\' WHERE id = :c'),
            {"c": env["cid"]},
        )
        held = await baton_reviews.hold(
            s,
            tenant_id=env["tid"],
            spec=require_wired("contact.bank_details_changed"),
            policy={"reviewer_role_id": env["compliance"], "reviewer_role_slug": "compliance"},
            subject_type="contact",
            subject_id=env["cuid"],
            contact_id=env["cid"],
            payload={"contact_id": env["cid"], "bank_account": {"name": "New"}},
            before={"value": {"name": "Old"}},
            justification=None,
            idempotency_key=f"contact.bank_details_changed:{env['cuid']}",
            actor=env["proposer"],
            workos_user_id=f"wos_prop_{env['n']}",
        )
    await eng.dispose()
    events = await _review_events(db["app"], env["tid"])
    assert [e["type"] for e in events] == ["review.requested"]
    assert events[0]["subject"]["review_uid"] == held.review_uid


# ---- the listing's gate facts --------------------------------------------------------------


async def test_the_listing_carries_the_gates_facts_and_the_held_filters(db):
    env = await _seed(db["app"])
    engine = _engine(db["app"])
    # A: raised by the adviser, decidable by the one Compliance member, £2,500 at stake.
    a = await _hold(
        engine,
        db["app"],
        env,
        key="la",
        requested_by=env["proposer"],
        roles=[env["compliance"]],
        amount=2500.0,
    )
    # B: raised by the only Compliance member, so nobody else can decide it.
    b = await _hold(
        engine, db["app"], env, key="lb", requested_by=env["approver"], roles=[env["compliance"]]
    )
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        page = await queries.list_all_runs(
            s,
            tenant_id=env["tid"],
            state="awaiting_approval",
            caller_id=env["approver"],
            caller_role_ids=[env["compliance"]],
        )
        stuck = await queries.list_all_runs(s, tenant_id=env["tid"], held="undecidable")
        money = await queries.list_all_runs(s, tenant_id=env["tid"], held="money")
        old = await queries.list_all_runs(s, tenant_id=env["tid"], held="over_day")
        approvals_queue = await queries.list_pending_approvals(
            s, tenant_id=env["tid"], caller_id=env["approver"], caller_role_ids=[env["compliance"]]
        )
    await eng.dispose()

    rows = {row["uid"]: row for row in page["items"]}
    assert set(rows) == {a.uid, b.uid}
    assert rows[a.uid]["approver_roles"] == ["Compliance"]
    assert rows[a.uid]["eligible_approvers"] == 1
    assert rows[a.uid]["held_uid"] == a.uid
    assert rows[a.uid]["held_since"] is not None
    assert rows[a.uid]["held_amount"] == 2500.0
    assert rows[a.uid]["can_decide"] is True
    assert rows[b.uid]["eligible_approvers"] == 0
    assert rows[b.uid]["held_amount"] is None
    assert rows[b.uid]["can_decide"] is False  # the caller raised it
    # the approvals seat's own counters
    stats = page["stats"]
    assert stats["awaiting_approval"] == 2
    assert stats["held_undecidable"] == 1
    assert stats["money_held"] == 2500.0
    assert stats["held_over_day"] == 0
    assert stats["decided_7d"] == 0
    assert {"approved_7d", "rejected_7d"} <= set(stats)
    # and its filters
    assert [r["uid"] for r in stuck["items"]] == [b.uid]
    assert [r["uid"] for r in money["items"]] == [a.uid]
    assert old["total"] == 0
    # the approvals queue says the same about who could decide
    by_uid = {row["uid"]: row for row in approvals_queue}
    assert by_uid[a.uid]["eligible_approvers"] == 1
    assert by_uid[b.uid]["eligible_approvers"] == 0


async def test_the_reviews_queue_says_who_decides_and_whether_anyone_can(db):
    env = await _seed(db["app"])
    engine = _engine(db["app"])
    a = await _hold(
        engine, db["app"], env, key="ra", requested_by=env["proposer"], roles=[env["compliance"]]
    )
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        queue = await baton_reviews.list_requests(
            s,
            tenant_id=env["tid"],
            scope="all",
            caller_id=env["approver"],
            caller_role_ids=[env["compliance"]],
        )
    await eng.dispose()
    row = next(r for r in queue if r["uid"] == a.uid)
    assert row["approver_roles"] == ["Compliance"]
    assert row["eligible_approvers"] == 1
    assert row["source"] == "action"
    assert row["can_decide"] is True


async def test_action_name_reads_the_reviews_catalogue_and_the_retired_keys():
    """The Runs tab showed `client.details_changed` as a run's NAME: the actions catalogue does
    not list the book keys, and the lookup stopped there."""
    assert queries.action_name("client.details_changed") == "Edit client details"
    assert queries.action_name("client.vulnerability_updated") == (
        "Update a client's vulnerability record"
    )
    assert queries.action_name("instruction.raised") == "Raise a money-movement instruction"
    assert queries.action_name("transaction.payment") == "Payment"
    assert queries.action_name("no.such_key") == "no.such_key"


async def test_status_word_reads_a_held_child_as_awaiting_approval():
    assert status_word_of("paused", "AwaitingChildren", held_below=True) == "awaiting_approval"
    assert (
        status_word_of("paused", "AwaitingChildren", held_below=True, waiting_below=True)
        == "awaiting_approval"
    )
    assert status_word_of("paused", "AwaitingChildren") == "running"


def status_word_of(state_type: str, state_name: str, **kw) -> str:
    return queries.status_word(state_type, state_name, **kw)


# ---- the rules on a decision and a reminder ---------------------------------------------


async def test_a_rejection_needs_a_reason():
    with pytest.raises(ActionError) as caught:
        await service.decide(
            None,  # type: ignore[arg-type] - refused before the engine is touched
            run_uid="run_x",
            decision="reject",
            actor_id="u",
            role_ids=[1],
            note="   ",
        )
    assert caught.value.status == 422
    assert "why" in str(caught.value)


async def test_a_reminder_tells_the_approvers_again_once_an_hour(db):
    # `remind` and the detail go through the app's own engine, which the suite binds to this
    # database; the run is created on a test engine so the hold itself is under this test's hand.
    env = await _seed(db["app"])
    engine = _engine(db["app"])
    run = await _hold(
        engine, db["app"], env, key="rm1", requested_by=env["proposer"], roles=[env["compliance"]]
    )
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        # a bystander with no part in it may not nudge
        with pytest.raises(ActionError) as refused:
            await approvals.remind(
                s, tenant_id=env["tid"], run_uid=run.uid, actor_id=env["approver"], may_manage=False
            )
        assert refused.value.status == 403
        # the requester may
        out = await approvals.remind(
            s, tenant_id=env["tid"], run_uid=run.uid, actor_id=env["proposer"], may_manage=False
        )
        assert out["run_uid"] == run.uid and out["reminded_at"]
        # but not twice within the hour
        with pytest.raises(ActionError) as again:
            await approvals.remind(
                s, tenant_id=env["tid"], run_uid=run.uid, actor_id=env["proposer"], may_manage=True
            )
        assert again.value.status == 429
    events = await _review_events(db["app"], env["tid"])
    assert [e["type"] for e in events] == ["review.requested", "review.requested"]
    assert events[1]["subject"]["reminder"] is True
    # the detail reads the reminder back
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        detail = await queries.run_detail(
            s,
            tenant_id=env["tid"],
            run_uid=run.uid,
            caller_id=env["approver"],
            caller_role_ids=[env["compliance"]],
        )
        # an hour later the approvers may be nudged again
        await s.execute(
            text(
                "UPDATE baton.events SET created_at = created_at - interval '2 hours' "
                "WHERE event_type = :e"
            ),
            {"e": approvals.REMINDED_EVENT},
        )
        await approvals.remind(
            s, tenant_id=env["tid"], run_uid=run.uid, actor_id=env["proposer"], may_manage=True
        )
    await eng.dispose()
    assert detail["approval"]["reminders"] == 1
    assert detail["approval"]["reminded_at"] is not None
    assert detail["approval"]["eligible_approvers"] == 1
    assert detail["approval"]["held_uid"] == run.uid
    assert detail["approval"]["can_decide"] is True
    assert detail["change"] is None


async def test_a_reminder_is_refused_once_the_run_has_moved_on(db):
    env = await _seed(db["app"])
    engine = _engine(db["app"])
    run = await _hold(
        engine, db["app"], env, key="rm2", requested_by=env["proposer"], roles=[env["compliance"]]
    )
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await engine.decide(
            engine.store.bind(s),
            run.uid,
            decision="reject",
            actor_id=env["approver"],
            role_ids=[env["compliance"]],
            note="No",
        )
        with pytest.raises(ActionError) as caught:
            await approvals.remind(
                s, tenant_id=env["tid"], run_uid=run.uid, actor_id=env["proposer"], may_manage=True
            )
    await eng.dispose()
    assert caught.value.status == 409


# ---- a gated child of a composite -------------------------------------------------------


async def _raw_run(s, env, **cols):
    """One baton.runs row written by hand — a composite's gated child cannot be made through a
    door in this test without the whole onboarding fixture, and the projection only reads rows."""
    base = {
        "uid": f"run_{uuid.uuid4().hex[:10]}",
        "tenant_id": env["tid"],
        "action_key": "client.setup",
        "idempotency_key": f"raw:{uuid.uuid4().hex}",
        "kind": "action",
        "params": "{}",
        "meta": "{}",
        "parent_run_id": None,
        "root_run_id": None,
        "depth": 0,
        "key": None,
        "position": None,
        "requested_by": env["proposer"],
        "requested_at": datetime(2026, 10, 20, 9, 0, tzinfo=UTC),
        "contact_id": env["cid"],
        "subject_kind": "account",
        "subject_uid": env["auid"],
        "state_type": "paused",
        "state_name": "AwaitingChildren",
        "case_no": None,
        "approver_role_ids": [],
        "gate_roles": None,
    }
    base.update(cols)
    row = (
        (
            await s.execute(
                text(
                    "INSERT INTO baton.runs (uid, tenant_id, action_key, idempotency_key, "
                    "  kind, params, meta, parent_run_id, root_run_id, depth, key, position, "
                    "  requested_by, requested_at, contact_id, subject_kind, subject_uid, "
                    "  state_type, state_name, case_no, approver_role_ids, gate_roles) "
                    "VALUES (:uid, :tenant_id, :action_key, :idempotency_key, :kind, "
                    "  CAST(:params AS jsonb), CAST(:meta AS jsonb), :parent_run_id, "
                    "  :root_run_id, :depth, :key, :position, CAST(:requested_by AS uuid), "
                    "  :requested_at, :contact_id, :subject_kind, :subject_uid, :state_type, "
                    "  :state_name, :case_no, CAST(:approver_role_ids AS int[]), "
                    "  CAST(:gate_roles AS int[])) "
                    "RETURNING id, uid"
                ),
                base,
            )
        )
        .mappings()
        .one()
    )
    return int(row["id"]), str(row["uid"])


async def test_a_gated_child_reads_on_its_root_and_lists_as_its_own_case(db):
    """A firm that gates account opening holds onboarding's `account.create` CHILD. The root used
    to read "Running" (paused on its children) and the child never reached the Reviews queue
    (roots only). Now the root reads awaiting approval and names the child as the node to decide,
    and the child lists in Reviews as its own case."""
    env = await _seed(db["app"])
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        root_id, root_uid = await _raw_run(s, env)
        child_id, child_uid = await _raw_run(
            s,
            env,
            action_key="account.create",
            parent_run_id=root_id,
            root_run_id=root_id,
            depth=1,
            key="account.create#0",
            position=1,
            state_name="AwaitingApproval",
            case_no=77,
            approver_role_ids=[env["compliance"]],
        )
        await _raw_run(
            s,
            env,
            action_key="baton.gate",
            kind="gate",
            parent_run_id=child_id,
            root_run_id=root_id,
            depth=2,
            key="approval",
            position=0,
            state_name="AwaitingApproval",
            gate_roles=[env["compliance"]],
        )
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        page = await queries.list_all_runs(
            s,
            tenant_id=env["tid"],
            caller_id=env["approver"],
            caller_role_ids=[env["compliance"]],
        )
        held = await queries.list_all_runs(s, tenant_id=env["tid"], state="awaiting_approval")
        running = await queries.list_all_runs(s, tenant_id=env["tid"], state="running")
        queue = await baton_reviews.list_requests(
            s,
            tenant_id=env["tid"],
            scope="inbox",
            caller_id=env["approver"],
            caller_role_ids=[env["compliance"]],
        )
        detail = await queries.run_detail(
            s,
            tenant_id=env["tid"],
            run_uid=root_uid,
            caller_id=env["approver"],
            caller_role_ids=[env["compliance"]],
        )
    await eng.dispose()

    root = next(r for r in page["items"] if r["uid"] == root_uid)
    assert root["status"] == "awaiting_approval"
    assert root["held_uid"] == child_uid
    assert root["approver_roles"] == ["Compliance"]
    assert root["can_decide"] is True
    assert root["eligible_approvers"] == 1
    assert [r["uid"] for r in held["items"]] == [root_uid]
    assert running["total"] == 0
    assert page["stats"]["awaiting_approval"] == 1
    # the child is its own case in the Reviews queue, decidable by its uid
    case = next(r for r in queue if r["uid"] == child_uid)
    assert case["case_no"] == 77
    assert case["source"] == "action"
    assert case["label"] == "Create account"
    assert case["can_decide"] is True
    # the detail names the node to decide
    assert detail["status"] == "awaiting_approval"
    assert detail["approval"]["held_uid"] == child_uid
    assert detail["approval"]["held_name"] == "Create account"
    assert detail["approval"]["can_decide"] is True
    assert detail["approval"]["roles"] == ["Compliance"]
