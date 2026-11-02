"""Polling ComplyCube for changes, against a real Postgres.

What it must do: settle a check ComplyCube has finished, leave one still running alone apart from
the stamp, see a monitoring alert on a finished check, route each change once even when a webhook
turns up later, and step back when ComplyCube is struggling.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest_asyncio
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.complycube import poller
from tests.complycube_support import (
    Bed,
    FakeProvider,
    Maker,
    accept,
    drain,
    event,
    fetch,
    insert_check,
    screening_check,
    seed_bed,
)


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def firm(maker: Maker) -> AsyncIterator[Bed]:
    """A partner firm with a webhook, so a routed change is visible as a delivery."""
    seeded = await seed_bed(maker, webhook=True)
    yield seeded
    await seeded.cleanup()


def _age(engine: Engine, provider_check_id: str, minutes: int = 10) -> None:
    """Make the check look ``minutes`` old. An UPDATE cannot backdate ``updated_at`` (a trigger
    stamps it), so triggers are off for this one statement, as the superuser."""
    with engine.connect() as conn:
        conn.execute(text("SET session_replication_role = replica"))
        try:
            conn.execute(
                text(
                    "UPDATE compliance.checks "
                    "SET updated_at = now() - make_interval(mins => :m) "
                    "WHERE provider_check_id = :p"
                ),
                {"m": minutes, "p": provider_check_id},
            )
        finally:
            conn.execute(text("SET session_replication_role = origin"))


async def _poll(bed: Bed, provider: FakeProvider) -> poller.PollResult:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return await poller.poll_once(s, provider.client())


async def _check(bed: Bed, provider_check_id: str) -> dict[str, object]:
    rows = await fetch(
        bed.maker,
        "SELECT ch.status, ch.updated_at, p.polled_at FROM compliance.checks ch "
        "LEFT JOIN compliance.check_polls p ON p.check_id = ch.id "
        "WHERE ch.provider_check_id = :p",
        {"p": provider_check_id},
    )
    return rows[0]


async def _deliveries(bed: Bed) -> int:
    rows = await fetch(
        bed.maker,
        "SELECT count(*) AS n FROM compliance.webhook_deliveries WHERE tenant_id = :t",
        {"t": bed.tenant_id},
    )
    return int(rows[0]["n"])


async def test_a_finished_check_no_webhook_reported_is_settled(
    firm: Bed, super_engine: Engine
) -> None:
    chk = firm.ref("done")
    await insert_check(firm, chk)
    _age(super_engine, chk)
    provider = FakeProvider(firm)
    provider.checks[chk] = screening_check(firm, chk, outcome="clear")

    result = await _poll(firm, provider)

    assert (result.asked, result.changed, result.routed) == (1, 1, 1)
    assert (await _check(firm, chk))["status"] == "clear"
    assert await _deliveries(firm) == 1
    # Settled, so it is never asked about again.
    assert (await _poll(firm, provider)).asked == 0


async def test_a_check_still_running_is_only_stamped(firm: Bed, super_engine: Engine) -> None:
    chk = firm.ref("running")
    await insert_check(firm, chk)
    _age(super_engine, chk)
    provider = FakeProvider(firm)
    provider.checks[chk] = screening_check(firm, chk, status="pending", outcome=None)
    before = await _check(firm, chk)

    result = await _poll(firm, provider)

    assert (result.asked, result.changed) == (1, 0)
    row = await _check(firm, chk)
    assert row["status"] == "pending"
    assert row["polled_at"] is not None
    # The question touches no column of the check: no audit row, and no date a firm sees moves.
    assert row["updated_at"] == before["updated_at"]
    assert await _deliveries(firm) == 0
    # Just asked, so it waits before the next question.
    assert (await _poll(firm, provider)).asked == 0


async def test_a_fresh_check_is_left_to_its_webhook(firm: Bed) -> None:
    chk = firm.ref("fresh")
    await insert_check(firm, chk)
    provider = FakeProvider(firm)
    provider.checks[chk] = screening_check(firm, chk, outcome="clear")

    assert (await _poll(firm, provider)).asked == 0
    assert provider.calls == []


async def test_a_webhook_after_the_poll_routes_nothing_more(
    firm: Bed, super_engine: Engine
) -> None:
    chk = firm.ref("late")
    await insert_check(firm, chk)
    _age(super_engine, chk)
    provider = FakeProvider(firm)
    provider.checks[chk] = screening_check(firm, chk, outcome="clear")
    assert (await _poll(firm, provider)).routed == 1

    await accept(firm.maker, event(firm, "late", "check.completed", chk))
    await drain(firm.maker, provider)

    assert (await _check(firm, chk))["status"] == "clear"
    assert await _deliveries(firm) == 1


async def test_a_struggling_provider_leaves_the_check_for_the_next_pass(
    firm: Bed, super_engine: Engine
) -> None:
    chk = firm.ref("busy")
    await insert_check(firm, chk)
    _age(super_engine, chk)
    provider = FakeProvider(firm)
    provider.checks[chk] = screening_check(firm, chk, outcome="clear")
    provider.status_override = 503

    result = await _poll(firm, provider)

    assert (result.asked, result.changed, result.failed) == (1, 0, 0)
    row = await _check(firm, chk)
    assert row["status"] == "pending"
    assert row["polled_at"] is None


async def test_a_check_the_provider_cannot_find_does_not_hold_the_queue(
    firm: Bed, super_engine: Engine
) -> None:
    chk = firm.ref("gone")
    await insert_check(firm, chk)
    _age(super_engine, chk)
    provider = FakeProvider(firm)  # no such check: the provider answers 404

    result = await _poll(firm, provider)

    assert result.failed == 1
    assert (await _check(firm, chk))["polled_at"] is not None


async def test_a_monitoring_alert_on_a_cleared_check_is_seen(
    firm: Bed, super_engine: Engine
) -> None:
    chk = firm.ref("alert")
    await insert_check(firm, chk, status="clear", monitoring=True)
    _age(super_engine, chk, minutes=2 * 24 * 60)
    provider = FakeProvider(firm)
    provider.checks[chk] = screening_check(
        firm,
        chk,
        outcome="attention",
        matches=[{"id": "m_new", "name": "Ada Lovelace", "matchTypes": ["pep"], "score": 91}],
        monitoring=True,
    )

    result = await _poll(firm, provider)

    assert (result.asked, result.changed, result.routed) == (1, 1, 1)
    assert (await _check(firm, chk))["status"] == "refer"


async def test_a_monitored_check_with_nothing_new_is_only_stamped(
    firm: Bed, super_engine: Engine
) -> None:
    chk = firm.ref("quiet")
    await insert_check(firm, chk, status="clear", monitoring=True)
    _age(super_engine, chk, minutes=2 * 24 * 60)
    provider = FakeProvider(firm)
    provider.checks[chk] = screening_check(firm, chk, outcome="clear", monitoring=True)

    result = await _poll(firm, provider)

    assert (result.asked, result.changed) == (1, 0)
    row = await _check(firm, chk)
    assert row["status"] == "clear"
    assert row["polled_at"] is not None
    # Asked today, so not again until tomorrow.
    assert (await _poll(firm, provider)).asked == 0


async def test_an_unmonitored_cleared_check_is_never_asked_about(
    firm: Bed, super_engine: Engine
) -> None:
    chk = firm.ref("done_quiet")
    await insert_check(firm, chk, status="clear", monitoring=False)
    _age(super_engine, chk, minutes=2 * 24 * 60)
    provider = FakeProvider(firm)

    assert (await _poll(firm, provider)).asked == 0
    # The risk backfill may ask for the client's profile; nothing asks about the check.
    assert not [call for call in provider.calls if call.startswith("GET /checks/")]


def test_the_poll_event_id_names_one_provider_state() -> None:
    assert poller.poll_event_id("chk_1", "complete", "clear") == "poll:chk_1:complete:clear"
    assert poller.poll_event_id("chk_1", "failed", None) == "poll:chk_1:failed:-"
    assert (
        poller.poll_event_id("chk_1", "complete", "attention", "2026-10-02T09:00:00Z")
        == "poll:chk_1:complete:attention:2026-10-02T09:00:00Z"
    )
    assert timedelta(0) < poller.PENDING_WAIT < poller.MONITORED_WAIT < poller.GIVE_UP
