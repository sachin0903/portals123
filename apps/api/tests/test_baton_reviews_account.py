"""Account-record four-eye reviews wired in the reconciliation. Fee packages: hold → approve →
the worker applies the new set. Exercises a review whose apply re-runs the live account writer.
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
                    "('ten_' || :n, 'ra-' || :n, 'Reviews Firm') RETURNING id"
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
        # two fee packages narrowed to nothing (compatible with any account), enabled
        fee_ids = []
        for i in (1, 2):
            fid = (
                await s.execute(
                    text(
                        "INSERT INTO platform.fee_packages "
                        "(tenant_id, name, category, frequency, fee_type, rule, enabled) VALUES "
                        "(:t, :nm, 'platform', 'ongoing', 'fixed', '{}'::jsonb, true) RETURNING id"
                    ),
                    {"t": tid, "nm": f"Pkg {i}"},
                )
            ).scalar_one()
            fee_ids.append(int(fid))
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
        "aid": int(aid),
        "auid": f"acc_{n}",
        "fee_ids": fee_ids,
        "proposer": proposer,
        "approver": approver,
        "approver_wos": f"wos_appr_{n}",
        "proposer_wos": f"wos_prop_{n}",
    }


async def test_fee_packages_hold_then_approve_applies(db, monkeypatch):
    """Hold → approve → the worker writes the packages, then starts the custodian push
    (`fee.attach`) as its own run WITHOUT a second gate — the approver has already agreed."""
    from app.actions.attempt import Attempt

    env = await _seed(db["app"])
    engine = _engine(db["app"])
    monkeypatch.setattr("app.reviews.baton.get_engine", lambda: engine)
    monkeypatch.setattr("app.actions.service.get_engine", lambda: engine)

    async def fake_attach(session, *, payload, subjects):
        return Attempt(ok=True, method="PATCH", url="/account", response={"fee": "attached"})

    monkeypatch.setattr("app.integrations.seccl.actions.seccl_attach_fee", fake_attach)

    key = "account.fee_packages_changed"
    payload = {"account_id": env["aid"], "fee_package_ids": env["fee_ids"]}

    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        before = await handler(key).capture_before(s, tenant_id=env["tid"], payload=payload)
        held = await baton_reviews.hold(
            s,
            tenant_id=env["tid"],
            spec=require_wired(key),
            policy={"reviewer_role_id": 7, "reviewer_role_slug": "reviewer"},
            subject_type="account",
            subject_id=env["auid"],
            contact_id=None,
            payload=payload,
            before=before,
            justification="New fee schedule",
            idempotency_key=f"{key}:{env['auid']}",
            actor=env["proposer"],
            workos_user_id=env["proposer_wos"],
        )
    await eng.dispose()
    assert held.held

    async def _count() -> int:
        e = create_async_engine(db["app"])
        m = async_sessionmaker(e, expire_on_commit=False)
        async with m() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            c = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM platform.account_fee_packages WHERE account_id = :a"
                    ),
                    {"a": env["aid"]},
                )
            ).scalar_one()
        await e.dispose()
        return int(c)

    assert await _count() == 0  # not applied until the worker runs

    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        out = await baton_reviews.approve(
            s,
            tenant_id=env["tid"],
            uid=held.review_uid,
            workos_user_id=env["approver_wos"],
            note=None,
            role_ids=[7],
        )
    assert out["status"] == "approved"
    assert out["summary"] == "Change the fee packages"
    # the snapshot carries the names, so the reviewer reads packages, not a count
    assert out["before_label"] == "None"
    assert out["after_label"] == "Pkg 1, Pkg 2"
    assert out["before"]["names"] == {str(i): f"Pkg {n}" for n, i in enumerate(env["fee_ids"], 1)}

    worker = Worker(engine, poll_seconds=1)
    while await worker.run_once():
        pass
    assert await _count() == 2

    # the push followed the apply as its own run, and went straight to the custodian leaf
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        pushes = (
            (
                await s.execute(
                    text(
                        "SELECT uid, state_type, approver_role_ids FROM baton.runs "
                        "WHERE tenant_id = :t AND action_key = 'fee.attach' "
                        "  AND parent_run_id IS NULL"
                    ),
                    {"t": env["tid"]},
                )
            )
            .mappings()
            .all()
        )
    await eng.dispose()
    assert len(pushes) == 1
    assert pushes[0]["state_type"] == "completed", pushes[0]["state_type"]
    assert list(pushes[0]["approver_role_ids"] or []) == []
