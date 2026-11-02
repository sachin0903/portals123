"""Flows v3 timers — durable waits, the ``wait_for_event`` listener, and the loop that fires them.

Against a real Postgres, because almost everything worth asserting here IS a database fact. The
durability claim is "a committed row survives the process"; the concurrency claim is
``FOR UPDATE SKIP LOCKED``; the subject-scoping claim is a NOT NULL pair compared in a predicate.
A mocked version of any of the three would prove nothing at all — which is the same reasoning
``tests/test_automations.py`` sets out, and this file follows its bed conventions: a per-test nonce
and no truncation, so another suite's leftovers can never make an assertion here pass or fail.

The one thing deliberately faked is ``orchestrator.resume_from_timer`` — B2's seam. These tests own
the machinery that CALLS it, not what it does, and stubbing it is also how the loop stays testable
before that sibling lands.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.flows import orchestrator, timers
from tests.conftest import principal_conn

Maker = async_sessionmaker[AsyncSession]

NOW = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)

#: A registered trigger whose subject is a CONTACT — the shape a parked case normally listens on.
CONTACT_TRIGGER = "client.document_uploaded"
#: …and one whose subject is an ACCOUNT, for the id-space confusion test.
ACCOUNT_TRIGGER = "account.model_changed"


# ---------------------------------------------------------------------------------------------
# resolve_fire_at — pure, no database
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "ctx", "expected"),
    [
        # -- durations ------------------------------------------------------------------------
        ({"duration_days": 7}, {}, NOW + timedelta(days=7)),
        ({"duration_hours": 6}, {}, NOW + timedelta(hours=6)),
        # both authored: they sum, they do not fight
        ({"duration_days": 1, "duration_hours": 2}, {}, NOW + timedelta(days=1, hours=2)),
        # a builder text field hands us a string
        ({"duration_days": "3"}, {}, NOW + timedelta(days=3)),
        # fractional hours are legitimate (a 90-minute cooling-off)
        ({"duration_hours": 1.5}, {}, NOW + timedelta(hours=1.5)),
        # -- until_date_field, flat dotted context (the planner's shape) ------------------------
        (
            {"until_date_field": "memory.review_date"},
            {"memory.review_date": "2026-08-20T10:30:00Z"},
            datetime(2026, 8, 20, 10, 30, tzinfo=UTC),
        ),
        # …and the nested shape (the trigger/branch contexts). ONE resolver reads both.
        (
            {"until_date_field": "memory.review_date"},
            {"memory": {"review_date": date(2026, 8, 20)}},
            datetime(2026, 8, 20, tzinfo=UTC),
        ),
        # a real datetime straight out of a query, naive — read as UTC, never guessed as local
        (
            {"until_date_field": "contact.renewal"},
            {"contact.renewal": datetime(2026, 9, 1, 12, 0)},
            datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        ),
        # -- fire-immediately cases (None) ------------------------------------------------------
        ({}, {}, None),  # nothing authored at all
        ({"duration_days": 0}, {}, None),  # a zero wait is not a wait
        ({"duration_days": -3}, {}, None),  # negative: junk, and already in the past
        ({"duration_days": "soon"}, {}, None),  # unparseable
        ({"duration_days": True}, {}, None),  # a boolean is not a 1
        ({"until_date_field": "memory.missing"}, {}, None),  # the field never got populated
        ({"until_date_field": "memory.d"}, {"memory.d": "not a date"}, None),
        ({"until_date_field": "memory.d"}, {"memory.d": "2020-01-01"}, None),  # in the past
        ({"until_date_field": ""}, {}, None),
    ],
)
def test_resolve_fire_at(config: dict[str, Any], ctx: dict[str, Any], expected: Any) -> None:
    assert timers.resolve_fire_at(config, now=NOW, ctx=ctx) == expected


@pytest.mark.pkgs_ui_pin
def test_resolve_fire_at_clamps_to_the_contract_cap() -> None:
    """`caps.maxWaitDays` is the structural ceiling. Publish refuses an over-cap authored DURATION;
    this is the backstop for a date field, whose value publish cannot see."""
    ceiling = NOW + timedelta(days=timers.MAX_WAIT_DAYS)
    assert (
        timers.resolve_fire_at(
            {"until_date_field": "memory.d"}, now=NOW, ctx={"memory.d": "2030-01-01"}
        )
        == ceiling
    )
    assert timers.resolve_fire_at({"duration_days": 10_000}, now=NOW, ctx={}) == ceiling


def test_resolve_fire_at_never_raises_on_junk_config() -> None:
    """Total by contract: this runs inside a state transition, and an exception here would fail the
    whole transition rather than degrading to "move on now"."""
    for junk in (
        None,
        [],
        "wait",
        {"duration_days": {"nested": 1}},
        {"duration_hours": float("nan")},
    ):
        assert timers.resolve_fire_at(junk, now=NOW, ctx={}) is None  # type: ignore[arg-type]


def test_backoff_is_exponential_and_capped() -> None:
    assert timers.backoff_seconds(1) == 60
    assert timers.backoff_seconds(2) == 120
    assert timers.backoff_seconds(3) == 240
    assert timers.backoff_seconds(99) == 3600


# ---------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class _Resumes:
    """A stand-in for B2's ``orchestrator.resume_from_timer``. Records the (case, node, kind) it
    was handed, which is the entire contract this module is responsible for honouring."""

    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[tuple[int, str, str]] = []
        self.raises = raises

    async def __call__(
        self, session: AsyncSession, *, case_id: int, node_id: str, kind: str
    ) -> None:
        self.calls.append((case_id, node_id, kind))
        if self.raises:
            raise RuntimeError("the engine exploded")


@pytest.fixture
def resumes(monkeypatch: pytest.MonkeyPatch) -> _Resumes:
    """Install the seam. ``raising=False`` because the whole point of the guarded lazy import is
    that this module ships and is testable BEFORE B2's orchestrator grows the function."""
    hook = _Resumes()
    monkeypatch.setattr(orchestrator, "resume_from_timer", hook, raising=False)
    return hook


class _Bed:
    """One firm, two clients, and a live case per client."""

    def __init__(self, maker: Maker, nonce: str, tenant_id: int) -> None:
        self.maker = maker
        self.nonce = nonce
        self.tenant_id = tenant_id
        self.contacts: dict[str, int] = {}
        self.cases: dict[str, int] = {}

    async def timer(self, uid: str) -> dict[str, Any]:
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            row = (
                (
                    await s.execute(
                        text(
                            "SELECT uid, state, kind, node_id, case_id, attempts, last_error, "
                            "       fire_at, fired_at, cancelled_at, payload "
                            "FROM flows.timers WHERE uid = :u"
                        ),
                        {"u": uid},
                    )
                )
                .mappings()
                .one()
            )
            return dict(row)

    async def listener(self, case_id: int, node_id: str) -> dict[str, Any] | None:
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            row = (
                (
                    await s.execute(
                        text(
                            "SELECT uid, state, subject_kind, subject_id, trigger_key, timer_id "
                            "FROM flows.case_listeners WHERE case_id = :c AND node_id = :n "
                            "ORDER BY id DESC LIMIT 1"
                        ),
                        {"c": case_id, "n": node_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            return dict(row) if row else None

    async def finish_case(self, case_id: int, *, status: str = "completed") -> None:
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            await s.execute(
                text("UPDATE flows.cases SET status = :st, completed_at = now() WHERE id = :c"),
                {"st": status, "c": case_id},
            )


@pytest_asyncio.fixture
async def bed(maker: Maker) -> _Bed:
    nonce = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tenant_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) "
                        "RETURNING id"
                    ),
                    {"u": f"tmr_{nonce}", "a": f"tmr-{nonce}", "n": f"Timers {nonce}"},
                )
            ).scalar_one()
        )
        flow_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
                        "VALUES (:u, :t, :c, 'Waiting flow', 'published') RETURNING id"
                    ),
                    {"u": f"flw_{nonce}", "t": tenant_id, "c": f"waiting_{nonce}"},
                )
            ).scalar_one()
        )
        version_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, "
                        "  status) VALUES (:u, :t, :f, 1, 'published') RETURNING id"
                    ),
                    {"u": f"flv_{nonce}", "t": tenant_id, "f": flow_id},
                )
            ).scalar_one()
        )
        bed = _Bed(maker, nonce, tenant_id)
        for who in ("a", "b"):
            contact_id = int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO crm.contacts (tenant_id, uid, name, type, service_type) "
                            "VALUES (:t, :u, :n, 'individual', 'advised') RETURNING id"
                        ),
                        {"t": tenant_id, "u": f"con_{nonce}_{who}", "n": f"Client {who.upper()}"},
                    )
                ).scalar_one()
            )
            execution_id = int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO flows.executions (uid, tenant_id, flow_id, "
                            "  flow_version_id, status) VALUES (:u, :t, :f, :v, 'active') "
                            "RETURNING id"
                        ),
                        {
                            "u": f"fex_{nonce}_{who}",
                            "t": tenant_id,
                            "f": flow_id,
                            "v": version_id,
                        },
                    )
                ).scalar_one()
            )
            case_id = int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO flows.cases (uid, tenant_id, flow_id, flow_version_id, "
                            "  execution_id, subject_contact_id, subject_type, subject_id, "
                            "  status) "
                            "VALUES (:u, :t, :f, :v, :e, :c, 'contact', :c, 'in_progress') "
                            "RETURNING id"
                        ),
                        {
                            "u": f"cas_{nonce}_{who}",
                            "t": tenant_id,
                            "f": flow_id,
                            "v": version_id,
                            "e": execution_id,
                            "c": contact_id,
                        },
                    )
                ).scalar_one()
            )
            bed.contacts[who] = contact_id
            bed.cases[who] = case_id
    return bed


async def _schedule(
    bed: _Bed,
    *,
    who: str = "a",
    node_id: str = "wait_1",
    kind: str = timers.KIND_WAIT,
    fire_at: datetime | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return await timers.schedule(
            s,
            tenant_id=bed.tenant_id,
            case_id=bed.cases[who],
            node_id=node_id,
            kind=kind,
            fire_at=fire_at or (datetime.now(UTC) - timedelta(minutes=1)),
            payload=payload,
        )


# ---------------------------------------------------------------------------------------------
# Durability + the loop
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_scheduled_timer_survives_a_worker_restart_and_fires(
    bed: _Bed, db: dict[str, str], resumes: _Resumes
) -> None:
    """THE point of the table. The timer is committed by one engine; a completely different engine
    — a restarted worker, in every way that matters here — claims and fires it."""
    uid = await _schedule(bed, fire_at=datetime.now(UTC) - timedelta(minutes=5))

    restarted = create_async_engine(db["app"])
    try:
        fired = await timers.run_due_once(async_sessionmaker(restarted, expire_on_commit=False))
    finally:
        await restarted.dispose()

    assert fired is True
    assert resumes.calls == [(bed.cases["a"], "wait_1", timers.KIND_WAIT)]
    row = await bed.timer(uid)
    assert row["state"] == "fired"
    assert row["fired_at"] is not None


@pytest.mark.asyncio
async def test_a_timer_not_yet_due_is_left_alone(bed: _Bed, resumes: _Resumes) -> None:
    uid = await _schedule(bed, fire_at=datetime.now(UTC) + timedelta(days=3))
    assert await timers.run_due_once(bed.maker) is False
    assert resumes.calls == []
    assert (await bed.timer(uid))["state"] == "pending"


@pytest.mark.asyncio
async def test_scheduling_the_same_node_twice_re_arms_rather_than_stacking(
    bed: _Bed, resumes: _Resumes
) -> None:
    """The engine's resume path is at-least-once, so ``schedule`` must be idempotent per node: two
    pending timers on one node would wake the same case twice — and on a ``wait_for_event`` node the
    second would take the timeout branch after the event had already released it."""
    first = await _schedule(bed, fire_at=datetime.now(UTC) + timedelta(days=1))
    second = await _schedule(bed, fire_at=datetime.now(UTC) - timedelta(minutes=1))
    assert first == second  # the SAME row, re-armed

    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        pending = (
            await s.execute(
                text("SELECT count(*) FROM flows.timers WHERE case_id = :c AND state = 'pending'"),
                {"c": bed.cases["a"]},
            )
        ).scalar_one()
    assert pending == 1

    await timers.run_due_once(bed.maker)
    assert len(resumes.calls) == 1


@pytest.mark.asyncio
async def test_two_workers_do_not_double_fire_the_same_timer(bed: _Bed, resumes: _Resumes) -> None:
    """``FOR UPDATE SKIP LOCKED``, proven the only way it can be: a second session tries to claim
    while the first still holds the row's lock in an open transaction, and must find nothing."""
    await _schedule(bed, fire_at=datetime.now(UTC) - timedelta(minutes=1))
    now = datetime.now(UTC)

    async with bed.maker() as first, first.begin():
        await first.execute(text("SET LOCAL ROLE service_role"))
        claimed_first = await timers.fire_due(first, now=now)
        # …and while that transaction is still open and holding the lock:
        async with bed.maker() as second, second.begin():
            await second.execute(text("SET LOCAL ROLE service_role"))
            claimed_second = await timers.fire_due(second, now=now)

    assert claimed_first is True
    assert claimed_second is False, "the second worker must skip the locked row, not wait for it"
    assert len(resumes.calls) == 1


@pytest.mark.asyncio
async def test_a_terminal_cases_timer_is_cancelled_not_fired(bed: _Bed, resumes: _Resumes) -> None:
    """A finished journey has no node to resume at. Firing would ask the engine to move a latched
    case; marking it 'fired' would claim we resumed something we did not."""
    uid = await _schedule(bed, fire_at=datetime.now(UTC) - timedelta(minutes=1))
    await bed.finish_case(bed.cases["a"])

    assert await timers.run_due_once(bed.maker) is True
    assert resumes.calls == []
    row = await bed.timer(uid)
    assert row["state"] == "cancelled"
    assert "terminal" in (row["last_error"] or "")


@pytest.mark.asyncio
async def test_cancel_for_case_retires_every_wait(bed: _Bed, resumes: _Resumes) -> None:
    await _schedule(bed, node_id="wait_1", fire_at=datetime.now(UTC) - timedelta(minutes=1))
    await _schedule(bed, node_id="wait_2", fire_at=datetime.now(UTC) - timedelta(minutes=1))
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await timers.arm_listener(
            s,
            tenant_id=bed.tenant_id,
            case_id=bed.cases["a"],
            node_id="listen_1",
            trigger_key=CONTACT_TRIGGER,
        )
        retired = await timers.cancel_for_case(s, case_id=bed.cases["a"])
    assert retired == 3  # two timers + one listener

    assert await timers.run_due_once(bed.maker) is False
    assert resumes.calls == []


@pytest.mark.asyncio
async def test_cancel_for_node_retires_only_that_node(bed: _Bed) -> None:
    kept = await _schedule(bed, node_id="wait_2", fire_at=datetime.now(UTC) + timedelta(days=1))
    await _schedule(bed, node_id="wait_1", fire_at=datetime.now(UTC) + timedelta(days=1))
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        retired = await timers.cancel_for_node(s, case_id=bed.cases["a"], node_id="wait_1")
    assert retired == 1
    assert (await bed.timer(kept))["state"] == "pending"


# ---------------------------------------------------------------------------------------------
# The timeout branch
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_wait_for_event_timeout_resumes_on_the_timeout_kind(
    bed: _Bed, resumes: _Resumes
) -> None:
    """The loop must not make the engine re-derive which transition it is. A ``wait`` resumes at the
    successor; this one takes the node's ``timeout_branch``, and the two are told apart by ``kind``.
    """
    uid = await _schedule(
        bed,
        node_id="listen_1",
        kind=timers.KIND_EVENT_TIMEOUT,
        fire_at=datetime.now(UTC) - timedelta(minutes=1),
        payload={"trigger": CONTACT_TRIGGER, "timeout_branch": "chase_the_client"},
    )
    await timers.run_due_once(bed.maker)

    assert resumes.calls == [(bed.cases["a"], "listen_1", timers.KIND_EVENT_TIMEOUT)]
    row = await bed.timer(uid)
    assert row["state"] == "fired"
    # The branch travels on the row, snapshotted at arm time: the flow may have been re-published
    # in the seven days since, and the case is pinned to the version it started on.
    assert row["payload"]["timeout_branch"] == "chase_the_client"


@pytest.mark.asyncio
async def test_enter_wait_for_event_arms_both_halves(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        listener_uid, timer_uid = await timers.enter_wait_for_event(
            s,
            tenant_id=bed.tenant_id,
            case_id=bed.cases["a"],
            node_id="listen_1",
            config={
                "trigger": CONTACT_TRIGGER,
                "timeout_days": 7,
                "timeout_branch": "chase_the_client",
            },
        )
    assert listener_uid and timer_uid
    listener = await bed.listener(bed.cases["a"], "listen_1")
    assert listener is not None
    assert listener["trigger_key"] == CONTACT_TRIGGER
    assert listener["subject_kind"] == "contact"
    assert listener["subject_id"] == bed.contacts["a"]
    assert listener["timer_id"] is not None, "the listener must point at its own deadline"
    assert (await bed.timer(timer_uid))["kind"] == timers.KIND_EVENT_TIMEOUT


@pytest.mark.asyncio
async def test_a_wait_for_event_node_without_a_timeout_still_listens(bed: _Bed) -> None:
    """``timeout_days`` is optional in the contract, which is exactly why the listener cannot live
    inside the timer row: this node has no deadline and must still be released by its event."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        listener_uid, timer_uid = await timers.enter_wait_for_event(
            s,
            tenant_id=bed.tenant_id,
            case_id=bed.cases["a"],
            node_id="listen_1",
            config={"trigger": CONTACT_TRIGGER},
        )
    assert listener_uid is not None
    assert timer_uid is None


# ---------------------------------------------------------------------------------------------
# Subject scoping — the correctness-critical part
# ---------------------------------------------------------------------------------------------


async def _arm(
    bed: _Bed,
    who: str,
    *,
    trigger: str = CONTACT_TRIGGER,
    node: str = "listen_1",
    conditions: Any = None,
) -> str | None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return await timers.arm_listener(
            s,
            tenant_id=bed.tenant_id,
            case_id=bed.cases[who],
            node_id=node,
            trigger_key=trigger,
            node_conditions=conditions,
        )


async def _release(
    bed: _Bed,
    *,
    subject_kind: str | None,
    subject_id: int | None,
    facts: dict[str, Any] | None = None,
) -> list[str]:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return await timers.release_listeners(
            s,
            tenant_id=bed.tenant_id,
            trigger_keys=(CONTACT_TRIGGER,),
            subject_kind=subject_kind,
            subject_id=subject_id,
            facts=facts,
        )


@pytest.mark.asyncio
async def test_client_as_event_does_not_release_client_bs_case(
    bed: _Bed, resumes: _Resumes
) -> None:
    """THE test. Both clients are parked on the same node of the same flow, waiting for the same
    trigger. A document uploaded for A must release A's case and ONLY A's."""
    await _arm(bed, "a")
    await _arm(bed, "b")

    released = await _release(bed, subject_kind="contact", subject_id=bed.contacts["a"])

    assert len(released) == 1
    assert resumes.calls == [(bed.cases["a"], "listen_1", timers.RESUME_EVENT)]
    assert (await bed.listener(bed.cases["a"], "listen_1"))["state"] == "released"  # type: ignore[index]
    assert (await bed.listener(bed.cases["b"], "listen_1"))["state"] == "active"  # type: ignore[index]


@pytest.mark.asyncio
async def test_an_account_event_cannot_release_a_contact_listener(
    bed: _Bed, resumes: _Resumes
) -> None:
    """Account 7 is not contact 7. Comparing the id alone would conflate two id spaces, so the
    KIND is compared too — and this test pins that by releasing on the very id the listener holds.
    """
    await _arm(bed, "a")
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        released = await timers.release_listeners(
            s,
            tenant_id=bed.tenant_id,
            trigger_keys=(CONTACT_TRIGGER, ACCOUNT_TRIGGER),
            subject_kind="account",
            subject_id=bed.contacts["a"],  # the same integer, the wrong space
            facts={},
        )
    assert released == []
    assert resumes.calls == []


@pytest.mark.asyncio
async def test_an_event_with_no_subject_releases_nothing(bed: _Bed, resumes: _Resumes) -> None:
    """An unscoped event is not a wildcard. If the consumer could not name who the event was
    about, no parked case may be released by it."""
    await _arm(bed, "a")
    assert await _release(bed, subject_kind="contact", subject_id=None) == []
    assert await _release(bed, subject_kind=None, subject_id=bed.contacts["a"]) == []
    assert resumes.calls == []


@pytest.mark.asyncio
async def test_another_firms_listener_is_never_released(
    bed: _Bed, maker: Maker, resumes: _Resumes
) -> None:
    """Tenant scoping on top of subject scoping: release is keyed on the tenant too, so a colliding
    contact id in another firm cannot reach across."""
    await _arm(bed, "a")
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        released = await timers.release_listeners(
            s,
            tenant_id=bed.tenant_id + 10_000,  # a tenant this bed's listener does not belong to
            trigger_keys=(CONTACT_TRIGGER,),
            subject_kind="contact",
            subject_id=bed.contacts["a"],
            facts={},
        )
    assert released == []
    assert resumes.calls == []


@pytest.mark.asyncio
async def test_a_listener_whose_conditions_are_unmet_stays_armed(
    bed: _Bed, resumes: _Resumes
) -> None:
    """The event happened, but it was not the one the author was waiting for — so the case stays
    parked for the next one (or for its timeout)."""
    await _arm(
        bed,
        "a",
        conditions={"field": "event.subject.category", "op": "equals", "value": "identity"},
    )

    assert (
        await _release(
            bed,
            subject_kind="contact",
            subject_id=bed.contacts["a"],
            facts={"event.subject.category": "bank_statement"},
        )
        == []
    )
    assert (await bed.listener(bed.cases["a"], "listen_1"))["state"] == "active"  # type: ignore[index]

    released = await _release(
        bed,
        subject_kind="contact",
        subject_id=bed.contacts["a"],
        facts={"event.subject.category": "identity"},
    )
    assert len(released) == 1
    assert resumes.calls == [(bed.cases["a"], "listen_1", timers.RESUME_EVENT)]


@pytest.mark.asyncio
async def test_releasing_cancels_the_paired_timeout(bed: _Bed, resumes: _Resumes) -> None:
    """Two ways to leave the node, but only one exit: the event releasing the case must retire the
    deadline, or the timeout branch fires later on a case that already moved on."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        _, timer_uid = await timers.enter_wait_for_event(
            s,
            tenant_id=bed.tenant_id,
            case_id=bed.cases["a"],
            node_id="listen_1",
            config={
                "trigger": CONTACT_TRIGGER,
                "timeout_days": 7,
                "timeout_branch": "chase_the_client",
            },
        )
    assert timer_uid is not None

    await _release(bed, subject_kind="contact", subject_id=bed.contacts["a"])

    assert (await bed.timer(timer_uid))["state"] == "cancelled"
    assert resumes.calls == [(bed.cases["a"], "listen_1", timers.RESUME_EVENT)]


@pytest.mark.asyncio
async def test_arming_is_refused_when_the_case_has_no_subject_in_that_space(bed: _Bed) -> None:
    """A contact-subject case cannot listen for an ACCOUNT trigger — there is no account to be
    about, and arming an unscoped listener would release on a stranger's event. Refusing leaves the
    node on its timeout, which is visible and safe rather than silent and wrong."""
    assert await _arm(bed, "a", trigger=ACCOUNT_TRIGGER) is None
    assert await _arm(bed, "a", trigger="not.a.real.trigger") is None
    assert await bed.listener(bed.cases["a"], "listen_1") is None


@pytest.mark.asyncio
async def test_arming_is_refused_for_a_trigger_nothing_could_ever_deliver(bed: _Bed) -> None:
    """A schedule is the dangerous one, because every OTHER guard here waves it through: it is a
    registered key, its source is "supported" for the rule tier, and its subject is a contact — so
    ``_case_subject`` cheerfully returns this case's contact id and the listener is armed against a
    trigger no event type resolves to. Nothing can release it, and a node without ``timeout_days``
    parks the case permanently.

    Publish refuses it now (``graph._wait_for_event_issues``), but publish is not retroactive, so
    the arm path reads the same registry answer. ``task.completed`` is the mirror case: a live
    action trigger with no subject a journey could be scoped to.
    """
    assert await _arm(bed, "a", trigger="schedule.recurring") is None
    assert await _arm(bed, "a", trigger="task.completed") is None
    assert await _arm(bed, "a", trigger="tag.applied") is None
    assert await bed.listener(bed.cases["a"], "listen_1") is None
    # And the ordinary case still arms — the guard must not have swallowed the working path.
    assert await _arm(bed, "a") is not None


# ---------------------------------------------------------------------------------------------
# Failure discipline
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_resume_backs_off_and_then_dead_letters(
    bed: _Bed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One poisonous timer must never wedge the loop: it retries on backoff, then dead-letters and
    stops being claimed. The backoff itself is what proves the bookkeeping survives — the resume's
    own writes roll back, the attempt count does not."""
    monkeypatch.setattr(orchestrator, "resume_from_timer", _Resumes(raises=True), raising=False)
    uid = await _schedule(bed, fire_at=datetime.now(UTC) - timedelta(minutes=1))

    assert await timers.run_due_once(bed.maker) is True
    row = await bed.timer(uid)
    assert row["state"] == "pending"
    assert row["attempts"] == 1
    assert "RuntimeError" in (row["last_error"] or "")
    assert row["fire_at"] > datetime.now(UTC), "it must be pushed out, not retried instantly"

    # It is not due any more, so a second pass finds nothing — no hot loop.
    assert await timers.run_due_once(bed.maker) is False

    # Drive it to the dead letter: pull it forward and fail it until max_attempts.
    for _ in range(20):
        async with bed.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            await s.execute(
                text("UPDATE flows.timers SET fire_at = now() - interval '1 minute' WHERE uid=:u"),
                {"u": uid},
            )
        await timers.run_due_once(bed.maker)
        if (await bed.timer(uid))["state"] == "dead_letter":
            break
    row = await bed.timer(uid)
    assert row["state"] == "dead_letter"
    assert row["attempts"] >= 8
    # And a dead letter is never claimed again.
    assert await timers.run_due_once(bed.maker) is False


@pytest.mark.asyncio
async def test_a_failing_release_leaves_the_listener_armed(
    bed: _Bed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The release runs inside the automation consumer's batch transaction, so it is savepointed:
    a raising resume must not poison every other firm's events in the batch, and the listener stays
    active so the next matching event — or the timeout — tries again."""
    monkeypatch.setattr(orchestrator, "resume_from_timer", _Resumes(raises=True), raising=False)
    await _arm(bed, "a")

    assert await _release(bed, subject_kind="contact", subject_id=bed.contacts["a"]) == []
    assert (await bed.listener(bed.cases["a"], "listen_1"))["state"] == "active"  # type: ignore[index]


@pytest.mark.asyncio
async def test_an_absent_resume_seam_leaves_the_timer_pending(
    bed: _Bed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This module ships and runs before B2's orchestrator grows ``resume_from_timer``. A missing
    seam must be a retryable failure — never a timer marked 'fired' against an engine that never
    woke the case, and never an ImportError that takes the worker's loop down at boot."""
    monkeypatch.delattr(orchestrator, "resume_from_timer", raising=False)
    uid = await _schedule(bed, fire_at=datetime.now(UTC) - timedelta(minutes=1))

    assert await timers.run_due_once(bed.maker) is True
    row = await bed.timer(uid)
    assert row["state"] == "pending"
    assert "resume_from_timer" in (row["last_error"] or "")


@pytest.mark.asyncio
async def test_schedule_refuses_an_unknown_kind(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        with pytest.raises(ValueError, match="unknown timer kind"):
            await timers.schedule(
                s,
                tenant_id=bed.tenant_id,
                case_id=bed.cases["a"],
                node_id="wait_1",
                kind="sleep_forever",
                fire_at=datetime.now(UTC),
            )


# ---------------------------------------------------------------------------------------------
# RLS / grant posture
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", ["flows.timers", "flows.case_listeners"])
def test_engine_state_is_not_reachable_by_a_request_principal(
    app_engine: Engine, table: str
) -> None:
    """No ``authenticated`` grant and no policy at all — withholding the privilege beats an
    always-false policy (the flows.outbox / aml.operator_secrets precedent). A firm-facing "what is
    this journey waiting for?" surface reads on service_role through a permissioned endpoint, the
    way audit.actions is read; it does NOT come from opening these tables up.
    """
    claims = {"sub": "wos_reader", "tenant_id": 1, "tenant_lineage": [1], "permissions": []}
    with principal_conn(app_engine, claims) as conn, pytest.raises(ProgrammingError) as excinfo:
        conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))  # noqa: S608 — parametrised literal
    assert "permission denied" in str(excinfo.value).lower()


def test_the_tables_have_no_policies_at_all(super_engine: Engine) -> None:
    with super_engine.connect() as conn:
        policies = (
            conn.execute(
                text(
                    "SELECT count(*) FROM pg_policies WHERE schemaname = 'flows' "
                    "  AND tablename IN ('timers', 'case_listeners')"
                )
            )
        ).scalar_one()
        forced = (
            conn.execute(
                text(
                    "SELECT bool_and(relforcerowsecurity) FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'flows' AND c.relname IN ('timers', 'case_listeners')"
                )
            )
        ).scalar_one()
    assert policies == 0
    assert forced is True, "FORCE RLS must bind the migrator owner too"
