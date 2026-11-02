"""Where a ComplyCube status change goes, against a real Postgres.

The rule under test above all: one status change routes once. The same status twice — a
redelivered event, a ``check.updated`` that changes nothing — is not a change.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.complycube import routing, tasks
from app.complycube.routing import CheckRecord, route_result
from tests.complycube_support import (
    Bed,
    FakeProvider,
    Maker,
    accept,
    as_json,
    drain,
    event,
    fetch,
    insert_check,
    insert_kyc_check,
    make_tenant,
    screening_check,
    seed_bed,
)

MATCH = {"id": "m_1", "name": "Ada Lovelace", "matchTypes": ["sanction"], "score": 88}


class _Untouchable:
    """A session that fails the test if routing reaches the database."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("routing touched the database for a non-change")


def _record(**overrides: Any) -> CheckRecord:
    values: dict[str, Any] = {
        "id": 1,
        "uid": "checkuid0001",
        "tenant_id": 7,
        "kind": "aml",
        "origin": "partner",
        "status": "clear",
        "kyc_check_id": None,
        "contact_id": 3,
        "monitoring": False,
        "provider_check_id": "chk_1",
    }
    values.update(overrides)
    return CheckRecord(**values)


@pytest.mark.parametrize(
    ("old", "new"),
    [("clear", "clear"), ("refer", "refer"), (None, "clear"), ("pending", None)],
)
async def test_a_non_change_routes_nothing(old: str | None, new: str | None) -> None:
    session: Any = _Untouchable()
    assert await route_result(session, _record(), old, new) == []


def test_a_monitoring_alert_is_a_finished_check_referred_again() -> None:
    monitored = _record(monitoring=True)
    assert routing.is_monitoring_alert(monitored, "clear", "refer")
    assert routing.is_monitoring_alert(monitored, "rejected", "refer")
    assert not routing.is_monitoring_alert(monitored, "pending", "refer")
    assert not routing.is_monitoring_alert(_record(), "clear", "refer")
    alert = _record(event_type="check.monitoring.attention")
    assert routing.is_monitoring_alert(alert, "clear", "refer")
    assert not routing.is_monitoring_alert(alert, "clear", "clear")


# ---- DB -----------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def firm(maker: Maker) -> AsyncIterator[Bed]:
    """A partner firm that has registered a webhook."""
    seeded = await seed_bed(maker, webhook=True)
    yield seeded
    await seeded.cleanup()


@pytest_asyncio.fixture
async def bed(maker: Maker) -> AsyncIterator[Bed]:
    seeded = await seed_bed(maker)
    yield seeded
    await seeded.cleanup()


async def _deliveries(bed: Bed) -> list[dict[str, Any]]:
    rows = await fetch(
        bed.maker,
        "SELECT event, payload, status, webhook_id FROM compliance.webhook_deliveries "
        "WHERE tenant_id = :t ORDER BY id",
        {"t": bed.tenant_id},
    )
    return [{**row, "payload": as_json(row["payload"])} for row in rows]


async def _check_uid(bed: Bed, provider_check_id: str) -> str:
    rows = await fetch(
        bed.maker,
        "SELECT uid FROM compliance.checks WHERE provider_check_id = :p",
        {"p": provider_check_id},
    )
    return str(rows[0]["uid"])


async def _apply(bed: Bed, provider: FakeProvider, name: str, event_type: str, chk: str) -> None:
    await accept(bed.maker, event(bed, name, event_type, chk))
    await drain(bed.maker, provider)


async def test_the_same_status_twice_routes_once(firm: Bed) -> None:
    chk = firm.ref("chk")
    await insert_check(firm, chk)
    provider = FakeProvider(firm)
    provider.checks[chk] = screening_check(firm, chk, outcome="clear")
    await _apply(firm, provider, "one", "check.completed", chk)
    await _apply(firm, provider, "two", "check.completed.clear", chk)
    await _apply(firm, provider, "three", "check.updated", chk)
    deliveries = await _deliveries(firm)
    assert deliveries == [
        {
            "event": "check.updated",
            "payload": {
                "event": "check.updated",
                "uid": await _check_uid(firm, chk),
                "kind": "aml",
                "status": "clear",
            },
            "status": "pending",
            "webhook_id": firm.webhook_id,
        }
    ]


async def test_each_real_change_is_delivered_in_the_firms_vocabulary(firm: Bed) -> None:
    chk = firm.ref("chk")
    await insert_check(firm, chk)
    provider = FakeProvider(firm)
    provider.checks[chk] = screening_check(firm, chk, outcome="attention", matches=[MATCH])
    await _apply(firm, provider, "refer", "check.completed.attention", chk)
    provider.checks[chk] = screening_check(firm, chk, status="failed", outcome=None)
    await _apply(firm, provider, "fail", "check.failed", chk)
    statuses = [row["payload"]["status"] for row in await _deliveries(firm)]
    # A technical failure is not a verdict, so the firm is not told about it; the match names
    # never leave.
    assert statuses == ["refer"]
    for row in await _deliveries(firm):
        assert set(row["payload"]) == {"event", "uid", "kind", "status"}


async def test_a_technical_failure_is_not_delivered_to_the_firm(firm: Bed) -> None:
    chk = firm.ref("chk")
    check_pk = await insert_check(firm, chk, status="failed")
    async with firm.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        record = await routing.load_check(s, check_pk)
        assert record is not None
        assert await route_result(s, record, "pending", "failed") == []
    assert await _deliveries(firm) == []


async def test_failed_then_rejected_is_a_verdict_the_firm_is_told(firm: Bed) -> None:
    """``failed`` reads as pending to the firm, so a reviewer rejecting it is a real change."""
    chk = firm.ref("chk")
    check_pk = await insert_check(firm, chk, status="failed")
    async with firm.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        record = await routing.load_check(s, check_pk)
        assert record is not None
        assert await route_result(s, record, "failed", "rejected") == ["partner"]
    assert [row["payload"]["status"] for row in await _deliveries(firm)] == ["rejected"]


async def test_a_check_this_event_created_is_not_routed(firm: Bed) -> None:
    """The row was stored before the code that started the check committed. That code settles
    it; the consumer must not deliver a status for a check the firm has not been told about."""
    chk = firm.ref("early")
    provider = FakeProvider(firm)
    provider.checks[chk] = screening_check(firm, chk, outcome="clear")
    await _apply(firm, provider, "early", "check.completed", chk)
    assert await _deliveries(firm) == []


async def test_a_firm_without_a_webhook_gets_no_delivery(bed: Bed) -> None:
    chk = bed.ref("chk")
    await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, outcome="clear")
    await _apply(bed, provider, "x", "check.completed", chk)
    assert await _deliveries(bed) == []


async def _kyc(bed: Bed, kyc_id: int) -> dict[str, Any]:
    rows = await fetch(
        bed.maker,
        "SELECT status, verified_at, valid_until, external_ref FROM crm.kyc_checks WHERE id = :k",
        {"k": kyc_id},
    )
    return rows[0]


@pytest.mark.parametrize(
    ("status", "outcome", "kyc_status"),
    [
        ("complete", "clear", "passed"),
        # A refer starts Graphene's review: the kyc row waits for the console's decision.
        ("complete", "attention", "pending"),
        ("complete", "rejected", "failed"),
        # A technical failure is not a verdict: the kyc row keeps waiting.
        ("failed", None, "pending"),
    ],
)
async def test_a_portal_check_settles_its_kyc_row(
    bed: Bed, status: str, outcome: str | None, kyc_status: str
) -> None:
    kyc_id = await insert_kyc_check(bed)
    chk = bed.ref("portal")
    await insert_check(bed, chk, origin="portal", kyc_check_id=kyc_id)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, status=status, outcome=outcome)
    await _apply(bed, provider, "p", "check.completed", chk)
    kyc = await _kyc(bed, kyc_id)
    assert kyc["status"] == kyc_status
    assert kyc["external_ref"] == (None if kyc_status == "pending" else chk)
    if kyc_status == "passed":
        assert kyc["verified_at"] is not None
        # A pass does not lapse: no horizon, and it stands.
        assert kyc["valid_until"] is None
    else:
        assert kyc["verified_at"] is None
    # A portal check never queues a firm-API delivery.
    assert await _deliveries(bed) == []


@pytest.mark.parametrize("outcome", ["rejected", "attention"])
async def test_a_portal_identity_result_that_is_not_clear_waits_for_graphene(
    bed: Bed, outcome: str
) -> None:
    """Never sent back and never settled by the provider: held as an undecided refer, in the
    console's Refer queue, with the kyc row still pending."""
    kyc_id = await insert_kyc_check(bed, kind="identity")
    chk = bed.ref(f"id_{outcome}")
    await insert_check(bed, chk, kind="identity", origin="portal", kyc_check_id=kyc_id)
    provider = FakeProvider(bed)
    found = screening_check(bed, chk, outcome=outcome)
    found["type"] = "identity_check"
    provider.checks[chk] = found
    await _apply(bed, provider, "i1", "check.completed", chk)
    rows = await fetch(
        bed.maker,
        "SELECT status, decided_at, decided_by FROM compliance.checks WHERE provider_check_id = :p",
        {"p": chk},
    )
    assert rows == [{"status": "refer", "decided_at": None, "decided_by": None}]
    assert (await _kyc(bed, kyc_id))["status"] == "pending"


async def test_register_portal_check_settles_a_result_that_arrived_first(bed: Bed) -> None:
    kyc_id = await insert_kyc_check(bed)
    chk = bed.ref("race")
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, outcome="clear")
    # The webhook lands before the portal stored its row: the consumer stores it, routes nothing.
    await _apply(bed, provider, "race", "check.completed", chk)
    assert (await _kyc(bed, kyc_id))["status"] == "pending"
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        record = await routing.register_portal_check(
            s,
            tenant_id=bed.tenant_id,
            client_id=bed.client_pk,
            kind="aml",
            provider_check_id=chk,
            kyc_check_id=kyc_id,
            attempt=2,
        )
    assert record.origin == "portal"
    assert record.status == "clear"
    assert (await _kyc(bed, kyc_id))["status"] == "passed"
    rows = await fetch(
        bed.maker,
        "SELECT count(*) AS n, max(attempt) AS attempt FROM compliance.checks "
        "WHERE provider_check_id = :p",
        {"p": chk},
    )
    assert rows == [{"n": 1, "attempt": 2}]


async def test_register_portal_check_on_a_new_check_routes_nothing(bed: Bed) -> None:
    kyc_id = await insert_kyc_check(bed)
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        record = await routing.register_portal_check(
            s,
            tenant_id=bed.tenant_id,
            client_id=bed.client_pk,
            kind="aml",
            provider_check_id=bed.ref("fresh"),
            kyc_check_id=kyc_id,
        )
    assert record.status == "pending"
    assert record.kyc_check_id == kyc_id
    assert (await _kyc(bed, kyc_id))["status"] == "pending"


@pytest_asyncio.fixture
async def ops(bed: Bed, monkeypatch: pytest.MonkeyPatch) -> int:
    """Graphene's operations tenant with its compliance role, for the task paths."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        ops_id = await make_tenant(s, "cco", bed.nonce)
        await s.execute(
            text(
                "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                "VALUES (:t, :s, 'Compliance', 'operations')"
            ),
            {"t": ops_id, "s": tasks.COMPLIANCE_ROLE_SLUG},
        )
    bed.extra_tenants.append(ops_id)

    async def fake_ops_tenant(session: AsyncSession) -> int:
        del session
        return ops_id

    monkeypatch.setattr(tasks, "operations_tenant_id", fake_ops_tenant)
    return ops_id


async def _ops_tasks(bed: Bed, ops_id: int) -> list[dict[str, Any]]:
    return await fetch(
        bed.maker,
        "SELECT title, status, source_kind, source_ref, priority FROM tasks.tasks "
        "WHERE tenant_id = :t ORDER BY id",
        {"t": ops_id},
    )


async def test_a_monitoring_alert_raises_an_ops_task_and_a_verdict_closes_it(
    bed: Bed, ops: int
) -> None:
    chk = bed.ref("mon")
    check_pk = await insert_check(bed, chk, monitoring=True)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, outcome="clear", monitoring=True)
    await _apply(bed, provider, "m0", "check.completed", chk)
    # Monitoring finds a new match on a check that had come back clear.
    provider.checks[chk] = screening_check(
        bed, chk, outcome="attention", matches=[MATCH], monitoring=True
    )
    await _apply(bed, provider, "m1", "check.monitoring.attention", chk)
    uid = await _check_uid(bed, chk)
    raised = await _ops_tasks(bed, ops)
    assert len(raised) == 1
    assert raised[0]["status"] == "open"
    assert raised[0]["source_kind"] == "aml"
    assert raised[0]["priority"] == "high"
    assert raised[0]["source_ref"].startswith(tasks.check_task_ref(uid) + ":")
    assert "Ada Lovelace" in raised[0]["title"]
    # The same alert redelivered raises nothing new.
    await _apply(bed, provider, "m1b", "check.updated", chk)
    assert len(await _ops_tasks(bed, ops)) == 1
    # A reviewer clears the check: the decision path routes refer → clear and closes the task.
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "UPDATE compliance.checks SET status = 'clear', decided_by = 'ops', "
                "  decided_at = now() WHERE id = :c"
            ),
            {"c": check_pk},
        )
        record = await routing.load_check(s, check_pk)
        assert record is not None
        done = await route_result(s, record, "refer", "clear")
    assert "task_closed" in done
    assert [row["status"] for row in await _ops_tasks(bed, ops)] == ["done"]


async def test_a_referral_that_is_not_a_monitoring_alert_raises_no_task(bed: Bed, ops: int) -> None:
    chk = bed.ref("plain")
    await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, outcome="attention", matches=[MATCH])
    await _apply(bed, provider, "r", "check.completed.attention", chk)
    assert await _ops_tasks(bed, ops) == []


@pytest.mark.parametrize(
    ("kind", "title"),
    [
        ("aml", "Screen for Ada Lovelace failed — run it again"),
        ("identity", "Identity check for Ada Lovelace could not run"),
    ],
)
async def test_a_portal_check_the_provider_could_not_run_raises_one_ops_task(
    bed: Bed, ops: int, kind: str, title: str
) -> None:
    kyc_id = await insert_kyc_check(bed, kind=kind)
    chk = bed.ref(f"fail_{kind}")
    await insert_check(bed, chk, kind=kind, origin="portal", kyc_check_id=kyc_id)
    provider = FakeProvider(bed)
    failed = screening_check(bed, chk, status="failed", outcome=None)
    if kind == "identity":
        failed["type"] = "identity_check"
    provider.checks[chk] = failed
    await _apply(bed, provider, "f1", "check.failed", chk)
    # The same status again is not a change: still one task.
    await _apply(bed, provider, "f2", "check.updated", chk)
    uid = await _check_uid(bed, chk)
    raised = await _ops_tasks(bed, ops)
    assert raised == [
        {
            "title": title,
            "status": "open",
            "source_kind": "aml",
            "source_ref": f"{tasks.check_task_ref(uid)}:failed",
            "priority": "high",
        }
    ]
    assert (await _kyc(bed, kyc_id))["status"] == "pending"


async def test_a_partner_check_the_provider_could_not_run_raises_no_task(
    firm: Bed, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_ops_tenant(session: AsyncSession) -> int:
        raise AssertionError("a partner failure must not raise an ops task")

    monkeypatch.setattr(tasks, "operations_tenant_id", no_ops_tenant)
    chk = firm.ref("pfail")
    await insert_check(firm, chk)
    provider = FakeProvider(firm)
    provider.checks[chk] = screening_check(firm, chk, status="failed", outcome=None)
    await _apply(firm, provider, "pf", "check.failed", chk)
    assert await _deliveries(firm) == []


async def test_a_verdict_on_a_failed_check_closes_its_failure_task(bed: Bed, ops: int) -> None:
    kyc_id = await insert_kyc_check(bed, kind="identity")
    chk = bed.ref("idfail")
    check_pk = await insert_check(
        bed, chk, kind="identity", origin="portal", kyc_check_id=kyc_id, status="failed"
    )
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        record = await routing.load_check(s, check_pk)
        assert record is not None
        assert await route_result(s, record, "pending", "failed") == ["failure_task"]
        assert await route_result(s, record, "failed", "rejected") == ["task_closed", "portal"]
    assert [row["status"] for row in await _ops_tasks(bed, ops)] == ["done"]
    assert (await _kyc(bed, kyc_id))["status"] == "failed"
