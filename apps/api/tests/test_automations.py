"""The automation consumer, against a real Postgres.

Everything here is the safety spine, and the reason it is tested against a live database rather
than mocks is that most of the guards ARE database facts: the delivery table's unique key is what
makes redelivery idempotent, the re-arm window is a range scan over it, and the cursor's
durability is a committed row. A mocked version of any of those would prove nothing.

The bed is deliberately per-test-nonce and never truncates (the ``test_aml_rls`` reasoning):
another suite's leftovers cannot make these assertions pass or fail, because every one of them is
scoped to a tenant created moments earlier.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.flows import automation, triggers
from app.flows.triggers import AutomationContext, DispatchResult
from tests.conftest import SECCL_PLATFORM_SQL, principal_conn

Maker = async_sessionmaker[AsyncSession]

#: A real registered trigger with a path-param subject — the cleanest thing to drive the loop
#: with, because the subject resolves straight off the event with no joins.
TRIGGER = "contact.bank_details_changed"
EVENT_TYPE = "client.bank_account_updated"
ROUTE = "/crm/contacts/{contact_id}/bank-account"


# ---------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    """An async session factory on the app role — the consumer's own connection, separate from
    the fixtures that seed it, so every seed has to be COMMITTED to be visible. That is not
    incidental: it is what makes these tests exercise the same cross-transaction reality the
    worker lives in."""
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class _Recorder:
    """A stand-in dispatch target. The rule-tier executor is a sibling's work; what this file has
    to prove is that the CONSUMER decides correctly, so the target only records that it was
    called."""

    def __init__(self, *, ok: bool = True, raises: bool = False) -> None:
        self.calls: list[AutomationContext] = []
        self.ok = ok
        self.raises = raises

    async def run(self, session: AsyncSession, ctx: AutomationContext, spec: Any) -> DispatchResult:
        self.calls.append(ctx)
        if self.raises:
            raise RuntimeError("the target exploded")
        return DispatchResult(ok=self.ok, detail="recorded" if self.ok else "refused")


@pytest.fixture
def recorder() -> Iterator[_Recorder]:
    """Register the recorder as the ``action`` dispatch kind for the duration of one test, then
    put the registry back exactly as it was (the rule tier may have registered a real one)."""
    rec = _Recorder()
    previous = triggers.TARGETS.get("action")
    triggers.register_target("action", rec)
    yield rec
    if previous is None:
        triggers.TARGETS.pop("action", None)
    else:
        triggers.register_target("action", previous)


class _Bed:
    """One firm with one client, plus the helpers to arm automations and post events at it."""

    def __init__(self, maker: Maker, tenant_id: int, contact_id: int, nonce: str) -> None:
        self.maker = maker
        self.tenant_id = tenant_id
        self.contact_id = contact_id
        self.nonce = nonce
        self._n = 0

    async def arm(
        self,
        *,
        name: str | None = None,
        priority: int = 100,
        conditions: dict[str, Any] | None = None,
        trigger_key: str = TRIGGER,
        rearm_minutes: int | None = 0,
        allow_automation_source: bool = False,
        enabled: bool = True,
        flow_id: int | None = None,
        schedule: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> int:
        """Arm one automation. Defaults to an ACTION automation with the re-arm window off, so a
        test opts INTO the window it wants to exercise rather than tripping over it."""
        self._n += 1
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            return int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO flows.automations (uid, tenant_id, name, trigger_key, "
                            "  conditions, flow_id, action, config, priority, enabled, "
                            "  rearm_minutes, schedule, allow_automation_source) "
                            "VALUES (:uid, :t, :n, :k, CAST(:c AS jsonb), :f, "
                            "  CAST(:a AS jsonb), CAST(:cfg AS jsonb), :p, :e, :rm, "
                            "  CAST(:sch AS jsonb), :aas) RETURNING id"
                        ),
                        {
                            "uid": f"aut_{self.nonce}_{self._n}",
                            "t": self.tenant_id,
                            "n": name or f"Automation {self._n}",
                            "k": trigger_key,
                            "c": json.dumps(conditions) if conditions else None,
                            "f": flow_id,
                            "a": None if flow_id else json.dumps({"type": "apply_tag"}),
                            "cfg": json.dumps(config or {}),
                            "p": priority,
                            "e": enabled,
                            "rm": rearm_minutes,
                            "sch": json.dumps(schedule) if schedule else None,
                            "aas": allow_automation_source,
                        },
                    )
                ).scalar_one()
            )

    async def reset_cursor(self) -> None:
        """Park the cursor at the current head, so only the events this test posts are consumed.
        Upsert, because the consumer creates the row lazily on its first run — the suite must not
        depend on whether some earlier test happened to have run the loop already."""
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            await s.execute(
                text(
                    "INSERT INTO flows.automation_cursor (consumer, position) "
                    "VALUES (:c, COALESCE((SELECT max(id) FROM audit.actions), 0)) "
                    "ON CONFLICT (consumer) DO UPDATE SET position = EXCLUDED.position"
                ),
                {"c": automation.CONSUMER},
            )

    async def rewind_cursor(self, by: int = 1) -> None:
        """Pretend the last consume never committed — the at-least-once redelivery a crash
        between the dispatch and the cursor advance would produce."""
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            await s.execute(
                text(
                    "UPDATE flows.automation_cursor SET position = position - :n "
                    "WHERE consumer = :c"
                ),
                {"n": by, "c": automation.CONSUMER},
            )

    async def post_event(
        self,
        *,
        event_type: str = EVENT_TYPE,
        route: str = ROUTE,
        path_params: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        target_tenant_id: int | None = None,
        age_seconds: int = 60,
    ) -> int:
        """Append one row to the action stream, aged past the consumer's read lag."""
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            return int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO audit.actions (event_type, tenant_id, target_tenant_id, "
                            "  actor_sub, method, route, status, path_params, metadata, "
                            "  created_at) "
                            "VALUES (:et, :t, :tt, :sub, 'PUT', :r, 200, CAST(:pp AS jsonb), "
                            "  CAST(:md AS jsonb), now() - make_interval(secs => :age)) "
                            "RETURNING id"
                        ),
                        {
                            "et": event_type,
                            "t": self.tenant_id,
                            "tt": target_tenant_id,
                            "sub": f"wos_{self.nonce}",
                            "r": route,
                            "pp": json.dumps(
                                path_params
                                if path_params is not None
                                else {"contact_id": self.contact_id}
                            ),
                            "md": json.dumps(metadata or {}),
                            "age": age_seconds,
                        },
                    )
                ).scalar_one()
            )

    async def deliveries(self, automation_id: int | None = None) -> list[dict[str, Any]]:
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            rows = (
                (
                    await s.execute(
                        text(
                            "SELECT automation_id, event_key, outcome, detail, error, "
                            "       chain_depth, attempts, subject_id, case_uid "
                            "FROM flows.automation_deliveries "
                            "WHERE tenant_id = :t "
                            "  AND (CAST(:a AS int) IS NULL OR automation_id = :a) ORDER BY id"
                        ),
                        {"t": self.tenant_id, "a": automation_id},
                    )
                )
                .mappings()
                .all()
            )
            return [dict(r) for r in rows]

    async def ops_tasks(self) -> list[dict[str, Any]]:
        """The safety-cap escalations THIS bed's automations raised.

        Scoped by the nonce rather than by tenant, because an escalation deliberately lands in
        Graphene's OPERATIONS tenant rather than the firm's — so there is no tenant_id here to
        filter on. Every escalation's ``source_ref`` embeds the automation uid (``aut_<nonce>_n``)
        or the case uid (``cas_<nonce>``), both nonce-scoped, which restores this file's own rule:
        another suite's rows must not be able to make an assertion here pass or fail. (It used to
        select every ``source_kind = 'automation'`` task in the database, so the rule-tier
        executor's tests — which raise automation-sourced tasks of their own, at normal priority —
        broke the `all(priority == 'high')` assertion below.)
        """
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            rows = (
                (
                    await s.execute(
                        text(
                            "SELECT title, source_ref, priority FROM tasks.tasks "
                            "WHERE source_kind = 'automation' AND source_ref LIKE :like "
                            "ORDER BY id"
                        ),
                        {"like": f"%{self.nonce}%"},
                    )
                )
                .mappings()
                .all()
            )
            return [dict(r) for r in rows]

    async def pause(self, *, paused: bool = True, **overrides: Any) -> None:
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            await s.execute(
                text(
                    "INSERT INTO flows.automation_settings (tenant_id, paused, timezone, "
                    "  executions_per_minute, flow_concurrency, case_trigger_budget, "
                    "  max_chain_depth, default_rearm_minutes) "
                    "VALUES (:t, :p, :tz, :epm, :fc, :ctb, :mcd, :drm) "
                    "ON CONFLICT (tenant_id) DO UPDATE SET paused = EXCLUDED.paused, "
                    "  timezone = EXCLUDED.timezone, "
                    "  executions_per_minute = EXCLUDED.executions_per_minute, "
                    "  flow_concurrency = EXCLUDED.flow_concurrency, "
                    "  case_trigger_budget = EXCLUDED.case_trigger_budget, "
                    "  max_chain_depth = EXCLUDED.max_chain_depth, "
                    "  default_rearm_minutes = EXCLUDED.default_rearm_minutes"
                ),
                {
                    "t": self.tenant_id,
                    "p": paused,
                    "tz": overrides.get("timezone", "Europe/London"),
                    "epm": overrides.get("executions_per_minute"),
                    "fc": overrides.get("flow_concurrency"),
                    "ctb": overrides.get("case_trigger_budget"),
                    "mcd": overrides.get("max_chain_depth"),
                    "drm": overrides.get("default_rearm_minutes"),
                },
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
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"aut_{nonce}", "a": f"aut-{nonce}", "n": f"Automations {nonce}"},
                )
            ).scalar_one()
        )
        contact_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, name, type, service_type) "
                        "VALUES (:t, :u, 'Ada Client', 'individual', 'advised') RETURNING id"
                    ),
                    {"t": tenant_id, "u": f"con_{nonce}"},
                )
            ).scalar_one()
        )
    return _Bed(maker, tenant_id, contact_id, nonce)


# ---------------------------------------------------------------------------------------------
# Matching, priority and the one-fires-per-event rule
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_highest_priority_automation_fires_and_only_that_one(
    bed: _Bed, recorder: _Recorder
) -> None:
    """First-satisfiable-wins. Two armed automations on the same trigger, one event: the lower
    ``priority`` number runs, the other is never dispatched at all — not even recorded, because
    the turn never reached it."""
    low = await bed.arm(name="second", priority=200)
    high = await bed.arm(name="first", priority=10)
    await bed.reset_cursor()
    await bed.post_event()

    await automation.consume_once(bed.maker)

    assert len(recorder.calls) == 1
    assert recorder.calls[0].automation_id == high
    outcomes = {d["automation_id"]: d["outcome"] for d in await bed.deliveries()}
    assert outcomes == {high: "dispatched"}
    assert low not in outcomes


@pytest.mark.asyncio
async def test_an_unmatched_event_fires_nothing(bed: _Bed, recorder: _Recorder) -> None:
    await bed.arm()
    await bed.reset_cursor()
    # A real event type, but not one any registered trigger projects.
    await bed.post_event(event_type="client.updated", route="/firm/contacts/{id}")

    await automation.consume_once(bed.maker)

    assert recorder.calls == []
    assert await bed.deliveries() == []


@pytest.mark.asyncio
async def test_a_proxied_ops_action_fires_the_TARGET_firms_automations(
    bed: _Bed, maker: Maker, recorder: _Recorder
) -> None:
    """A Graphene operator acting on a firm's book with ``?tenant=`` changed THAT firm's records,
    so it is that firm's automations which must respond — not the operations tenant's."""
    nonce = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        ops_tenant = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"ops_{nonce}", "a": f"ops-{nonce}", "n": f"Ops {nonce}"},
                )
            ).scalar_one()
        )
    armed = await bed.arm()
    await bed.reset_cursor()
    # actor tenant = operations; book target = the firm.
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "INSERT INTO audit.actions (event_type, tenant_id, target_tenant_id, actor_sub, "
                "  method, route, status, path_params, created_at) "
                "VALUES (:et, :ops, :firm, 'wos_operator', 'PUT', :r, 200, "
                "  CAST(:pp AS jsonb), now() - interval '1 minute')"
            ),
            {
                "et": EVENT_TYPE,
                "ops": ops_tenant,
                "firm": bed.tenant_id,
                "r": ROUTE,
                "pp": json.dumps({"contact_id": bed.contact_id}),
            },
        )

    await automation.consume_once(bed.maker)

    assert [c.automation_id for c in recorder.calls] == [armed]


# ---------------------------------------------------------------------------------------------
# Entry conditions
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_conditions_gate_the_firing(bed: _Bed, recorder: _Recorder) -> None:
    """The condition is evaluated against facts read from the SUBJECT, not just the event — an
    automation for advised clients must not fire for an execution-only one."""
    matching = await bed.arm(
        name="advised",
        priority=10,
        conditions={"field": "contact.service_type", "operator": "equals", "value": "advised"},
    )
    await bed.arm(
        name="execution-only",
        priority=5,
        conditions={"field": "contact.service_type", "operator": "equals", "value": "execution"},
    )
    await bed.reset_cursor()
    await bed.post_event()

    await automation.consume_once(bed.maker)

    assert [c.automation_id for c in recorder.calls] == [matching]
    outcomes = {d["automation_id"]: d["outcome"] for d in await bed.deliveries()}
    # The higher-priority one was CONSIDERED and recorded as skipped — that record is the answer
    # to "why didn't my automation fire?", which is why skips are stored, not just dispatches.
    assert outcomes[matching] == "dispatched"
    assert set(outcomes.values()) == {"dispatched", "skipped_conditions"}


# ---------------------------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_same_event_twice_fires_once(bed: _Bed, recorder: _Recorder) -> None:
    """At-least-once delivery is only safe because of the deliveries table's unique key. Rewind
    the cursor and consume the same event again: no second case, no second call."""
    await bed.arm()
    await bed.reset_cursor()
    await bed.post_event()

    await automation.consume_once(bed.maker)
    await bed.rewind_cursor()
    await automation.consume_once(bed.maker)

    assert len(recorder.calls) == 1
    assert [d["outcome"] for d in await bed.deliveries()] == ["dispatched"]


# ---------------------------------------------------------------------------------------------
# The self-trigger guard and the chain-depth cap
# ---------------------------------------------------------------------------------------------

#: The marker the action executor stamps on events it produces. Frozen shape — agreed across the
#: programme, and pinned here so a change to it fails a test rather than quietly disarming the
#: single most important guard in the system.
MARKER = {"source": "rule", "id": "aut_upstream", "chain_depth": 0}


@pytest.mark.asyncio
async def test_an_automation_sourced_event_does_not_fire_automations(
    bed: _Bed, recorder: _Recorder
) -> None:
    """THE guard. Our stream emits an event for everything an automation does, so without this
    an automation that touches a client is an automation that re-triggers itself for ever."""
    armed = await bed.arm()
    await bed.reset_cursor()
    await bed.post_event(metadata={"automation": MARKER})

    await automation.consume_once(bed.maker)

    assert recorder.calls == []
    assert [d["outcome"] for d in await bed.deliveries(armed)] == ["skipped_automation"]


@pytest.mark.asyncio
async def test_an_opted_in_automation_does_fire_on_an_automation_sourced_event(
    bed: _Bed, recorder: _Recorder
) -> None:
    armed = await bed.arm(allow_automation_source=True)
    await bed.reset_cursor()
    await bed.post_event(metadata={"automation": MARKER})

    await automation.consume_once(bed.maker)

    assert [c.automation_id for c in recorder.calls] == [armed]
    # Depth is one more than the marker's — the chain is being counted even though the firing was
    # allowed, which is what keeps the cap meaningful for opted-in automations.
    assert recorder.calls[0].chain_depth == 1
    assert [d["chain_depth"] for d in await bed.deliveries(armed)] == [1]


@pytest.mark.asyncio
async def test_the_chain_depth_cap_stops_the_chain_and_raises_an_operations_task(
    bed: _Bed, recorder: _Recorder, ops_tenant: int
) -> None:
    """Past the cap nothing runs, and a human is told. A cap that fails silently is a cap nobody
    finds out about until a client has had forty emails."""
    armed = await bed.arm(allow_automation_source=True)
    await bed.reset_cursor()
    deep = {**MARKER, "chain_depth": triggers.SAFETY["maxChainDepth"] + 1}
    await bed.post_event(metadata={"automation": deep})

    await automation.consume_once(bed.maker)

    assert recorder.calls == []
    rows = await bed.deliveries(armed)
    assert [d["outcome"] for d in rows] == ["depth_exceeded"]
    tasks = await bed.ops_tasks()
    assert any("chain depth" in t["title"].lower() for t in tasks), tasks
    assert all(t["priority"] == "high" for t in tasks)


# ---------------------------------------------------------------------------------------------
# The re-arm window
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_rearm_window_suppresses_a_second_firing_for_the_same_subject(
    bed: _Bed, recorder: _Recorder
) -> None:
    armed = await bed.arm(rearm_minutes=60)
    await bed.reset_cursor()
    await bed.post_event()
    await automation.consume_once(bed.maker)
    await bed.post_event()
    await automation.consume_once(bed.maker)

    assert len(recorder.calls) == 1
    assert [d["outcome"] for d in await bed.deliveries(armed)] == ["dispatched", "skipped_rearm"]


@pytest.mark.asyncio
async def test_the_rearm_window_is_per_subject(
    bed: _Bed, maker: Maker, recorder: _Recorder
) -> None:
    """ "Don't chase the same client twice today" must not become "don't chase anybody else
    either" — the window is keyed on the subject, not just the automation."""
    await bed.arm(rearm_minutes=60)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        other = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, name, type) "
                        "VALUES (:t, :u, 'Bob Client', 'individual') RETURNING id"
                    ),
                    {"t": bed.tenant_id, "u": f"con2_{bed.nonce}"},
                )
            ).scalar_one()
        )
    await bed.reset_cursor()
    await bed.post_event()
    await bed.post_event(path_params={"contact_id": other})

    await automation.consume_once(bed.maker)

    assert sorted(c.subject_id or 0 for c in recorder.calls) == sorted([bed.contact_id, other])


# ---------------------------------------------------------------------------------------------
# The kill switch
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_paused_tenant_dispatches_nothing(bed: _Bed, recorder: _Recorder) -> None:
    armed = await bed.arm()
    await bed.pause()
    await bed.reset_cursor()
    await bed.post_event()

    await automation.consume_once(bed.maker)

    assert recorder.calls == []
    assert [d["outcome"] for d in await bed.deliveries(armed)] == ["skipped_paused"]


@pytest.mark.asyncio
async def test_the_global_switch_advances_the_cursor_without_dispatching(
    bed: _Bed, recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kill switch that queued the night's events into a stampede on release would be worse
    than the problem it was pulled for, so the cursor keeps moving while it is off."""
    from app.config import settings

    monkeypatch.setattr(settings, "automations_enabled", False)
    await bed.arm()
    await bed.reset_cursor()
    event_id = await bed.post_event()

    await automation.consume_once(bed.maker)

    assert recorder.calls == []
    assert await bed.deliveries() == []
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        position = (
            await s.execute(
                text("SELECT position FROM flows.automation_cursor WHERE consumer = :c"),
                {"c": automation.CONSUMER},
            )
        ).scalar_one()
    assert position == event_id


# ---------------------------------------------------------------------------------------------
# Rate limiting, deferral and the per-case budget
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_overflow_defers_and_the_deferred_firing_later_runs(
    bed: _Bed, recorder: _Recorder
) -> None:
    """Deferred, never dropped: the firing is queued with a retry time and the queue drains it —
    a rate limit that loses a client's chaser is worse than one that runs it a minute late."""
    await bed.arm()
    await bed.pause(paused=False, executions_per_minute=1)
    await bed.reset_cursor()
    await bed.post_event()
    await bed.post_event(age_seconds=59)
    await automation.consume_once(bed.maker)

    outcomes = [d["outcome"] for d in await bed.deliveries()]
    assert outcomes == ["dispatched", "deferred"]
    assert len(recorder.calls) == 1

    # Drain the deferral once the window has rolled (simulated by asking a minute later).
    later = datetime.now(UTC) + timedelta(minutes=2)
    await automation.run_deferred_once(bed.maker, now=later)

    assert len(recorder.calls) == 2
    assert [d["outcome"] for d in await bed.deliveries()] == ["dispatched", "dispatched"]


@pytest.mark.asyncio
async def test_an_exhausted_case_budget_refuses_and_raises_an_operations_task(
    bed: _Bed, maker: Maker, recorder: _Recorder, ops_tenant: int
) -> None:
    """Two automations triggering each other inside one client's journey is the failure this
    counts. At the budget nothing runs and an operator is told which case it was."""
    armed = await bed.arm()
    await bed.pause(paused=False, case_trigger_budget=2)
    case_uid = await _seed_live_case(maker, bed)
    # Two prior firings already attributed to that case.
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        for i in range(2):
            await s.execute(
                text(
                    "INSERT INTO flows.automation_deliveries (automation_id, tenant_id, "
                    "  event_key, outcome, case_uid) "
                    "VALUES (:a, :t, :k, 'dispatched', :c)"
                ),
                {"a": armed, "t": bed.tenant_id, "k": f"prior:{i}", "c": case_uid},
            )
    await bed.reset_cursor()
    await bed.post_event()

    await automation.consume_once(bed.maker)

    assert recorder.calls == []
    rows = [d for d in await bed.deliveries(armed) if d["event_key"].startswith("action:")]
    assert [d["outcome"] for d in rows] == ["budget_exhausted"]
    assert any(case_uid in t["title"] for t in await bed.ops_tasks())


async def _seed_live_case(maker: Maker, bed: _Bed) -> str:
    """A live case for the bed's client — the identity the per-case budget is counted over."""
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        flow_id, version_id = await _seed_flow(s, bed)
        uid = f"cas_{bed.nonce}"
        await s.execute(
            text(
                "INSERT INTO flows.cases (uid, tenant_id, flow_id, flow_version_id, "
                "  subject_contact_id, status) VALUES (:u, :t, :f, :v, :c, 'in_progress')"
            ),
            {"u": uid, "t": bed.tenant_id, "f": flow_id, "v": version_id, "c": bed.contact_id},
        )
    return uid


_FLOW_DEFINITION: dict[str, Any] = {
    "steps": [
        {
            "id": "greet",
            "type": "task",
            "title": "Say hello",
            "scope": "participant",
            "assignee": "staff",
            "config": {},
        }
    ],
    "milestones": [],
}


async def _seed_flow(s: AsyncSession, bed: _Bed) -> tuple[int, int]:
    """A published one-step flow for the bed's firm. Returns (flow_id, version_id)."""
    flow_id = int(
        (
            await s.execute(
                text(
                    "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
                    "VALUES (:u, :t, :c, 'Welcome', 'published') RETURNING id"
                ),
                {"u": f"flw_{bed.nonce}", "t": bed.tenant_id, "c": f"welcome_{bed.nonce}"},
            )
        ).scalar_one()
    )
    version_id = int(
        (
            await s.execute(
                text(
                    "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status, "
                    "  definition, published_at) "
                    "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id"
                ),
                {
                    "u": f"flv_{bed.nonce}",
                    "t": bed.tenant_id,
                    "f": flow_id,
                    "d": json.dumps(_FLOW_DEFINITION),
                },
            )
        ).scalar_one()
    )
    await s.execute(
        text("UPDATE flows.flows SET current_published_version_id = :v WHERE id = :f"),
        {"v": version_id, "f": flow_id},
    )
    return flow_id, version_id


# ---------------------------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_target_that_raises_is_dead_lettered_and_the_loop_carries_on(
    bed: _Bed, maker: Maker
) -> None:
    """One bad automation must never wedge the consumer for a whole estate: the dispatch runs in a
    savepoint, the failure is recorded on the delivery, and the cursor still advances."""
    exploding = _Recorder(raises=True)
    previous = triggers.TARGETS.get("action")
    triggers.register_target("action", exploding)
    try:
        armed = await bed.arm()
        await bed.reset_cursor()
        event_id = await bed.post_event()
        await automation.consume_once(bed.maker)
    finally:
        if previous is None:
            triggers.TARGETS.pop("action", None)
        else:
            triggers.register_target("action", previous)

    rows = await bed.deliveries(armed)
    assert [d["outcome"] for d in rows] == ["failed"]
    assert "RuntimeError" in (rows[0]["error"] or "")
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        position = (
            await s.execute(
                text("SELECT position FROM flows.automation_cursor WHERE consumer = :c"),
                {"c": automation.CONSUMER},
            )
        ).scalar_one()
    assert position == event_id


# ---------------------------------------------------------------------------------------------
# The cursor
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_cursor_advances_durably_and_a_restart_resumes(
    bed: _Bed, maker: Maker, db: dict[str, str], recorder: _Recorder
) -> None:
    """Durable means committed, not in-process: a brand-new session factory (the "restarted
    worker") must pick up where the last one left off and NOT re-consume."""
    await bed.arm()
    await bed.reset_cursor()
    first = await bed.post_event()

    await automation.consume_once(bed.maker)
    assert len(recorder.calls) == 1

    # A genuinely new engine + factory: the "restarted worker". Nothing in process memory carries
    # over, so resuming can only come from the committed cursor row.
    engine = create_async_engine(db["app"])
    restarted = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # Nothing new: the restarted worker consumes nothing at all.
        assert await automation.consume_once(restarted) is False
        assert len(recorder.calls) == 1
        # A new event, on the OTHER side of the persisted cursor, is picked up exactly once.
        second = await bed.post_event(path_params={"contact_id": bed.contact_id})
        assert second > first
        await automation.consume_once(restarted)
    finally:
        await engine.dispose()

    keys = [d["event_key"] for d in await bed.deliveries()]
    assert keys == [f"action:{first}", f"action:{second}"]


# ---------------------------------------------------------------------------------------------
# Scheduled triggers
# ---------------------------------------------------------------------------------------------


def test_schedule_slot_is_computed_in_the_firms_timezone() -> None:
    """DST is the whole reason this is a local-time calculation. 09:00 London is 09:00 UTC in
    January and 08:00 UTC in July; a "09:00" review sweep must land at 09:00 in the firm's own
    morning on both sides of the clock change."""
    from zoneinfo import ZoneInfo

    london = ZoneInfo("Europe/London")
    daily_nine = {"cron": "0 9 * * *"}
    winter = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)  # 09:00 UTC = 09:00 London
    summer = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)  # 08:00 UTC = 09:00 London
    assert automation.schedule_slot(daily_nine, tz=london, now=winter) == "2026-01-15T09:00"
    assert automation.schedule_slot(daily_nine, tz=london, now=summer) == "2026-07-15T09:00"
    # The same instant read in UTC is 08:00, so a UTC firm's 09:00 slot is not due yet.
    assert automation.schedule_slot(daily_nine, tz=ZoneInfo("UTC"), now=summer) is None


def test_schedule_slot_respects_the_grace_window_the_weekday_and_the_cron_subset() -> None:
    from zoneinfo import ZoneInfo

    london = ZoneInfo("Europe/London")
    daily_nine = {"cron": "0 9 * * *"}
    # Fourteen hours late is not "the 09:00 firing" by any reading.
    assert (
        automation.schedule_slot(daily_nine, tz=london, now=datetime(2026, 1, 15, 23, tzinfo=UTC))
        is None
    )
    # 15 Jan 2026 is a Thursday — cron day-of-week is 0=Sunday, so Thursday is 4.
    on_time = datetime(2026, 1, 15, 9, 5, tzinfo=UTC)
    assert automation.schedule_slot({"cron": "0 9 * * 4"}, tz=london, now=on_time)
    assert automation.schedule_slot({"cron": "0 9 * * 1"}, tz=london, now=on_time) is None
    # Outside app/cron.py's supported subset ⇒ no slot, never a guess.
    assert automation.schedule_slot({"cron": "@daily"}, tz=london, now=on_time) is None
    assert automation.schedule_slot({}, tz=london, now=on_time) is None


def test_several_missed_slots_collapse_to_the_latest() -> None:
    """A worker that was down for twenty minutes must not fire a per-minute schedule twenty
    times to "catch up" — catching up on a chaser is just spamming a client."""
    from zoneinfo import ZoneInfo

    london = ZoneInfo("Europe/London")
    now = datetime(2026, 1, 15, 9, 20, tzinfo=UTC)
    assert automation.schedule_slot({"cron": "* * * * *"}, tz=london, now=now) == "2026-01-15T09:20"


@pytest.mark.asyncio
async def test_a_scheduled_automation_fires_once_per_slot_in_the_firms_timezone(
    bed: _Bed, recorder: _Recorder
) -> None:
    armed = await bed.arm(
        trigger_key="schedule.recurring", schedule={"cron": "0 9 * * *"}, rearm_minutes=0
    )
    await bed.pause(paused=False, timezone="Europe/London")
    # 08:05 UTC in July is 09:05 in London — inside the grace window for the 09:00 slot.
    at = datetime(2026, 7, 15, 8, 5, tzinfo=UTC)

    await automation.schedule_once(bed.maker, now=at)
    await automation.schedule_once(bed.maker, now=at + timedelta(minutes=10))

    assert len(recorder.calls) == 1
    rows = await bed.deliveries(armed)
    assert [d["event_key"] for d in rows] == ["schedule:2026-07-15T09:00"]
    assert rows[0]["outcome"] == "dispatched"


@pytest.mark.asyncio
async def test_a_scheduled_automation_does_not_fire_for_a_paused_firm(
    bed: _Bed, recorder: _Recorder
) -> None:
    await bed.arm(trigger_key="schedule.recurring", schedule={"cron": "0 9 * * *"})
    await bed.pause(paused=True, timezone="Europe/London")

    await automation.schedule_once(bed.maker, now=datetime(2026, 7, 15, 8, 5, tzinfo=UTC))

    assert recorder.calls == []
    assert await bed.deliveries() == []


# ---------------------------------------------------------------------------------------------
# The flow dispatch target (the real engine, end to end)
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_flow_automation_starts_a_real_case_through_the_engine(
    bed: _Bed, maker: Maker
) -> None:
    """No shortcut engine: the automation path goes through ``orchestrator.start_execution``, so
    an automation-started journey is planned, gated and instantiated exactly like an
    adviser-started one — and says who started it."""
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        flow_id, _ = await _seed_flow(s, bed)
        provider = str(
            (
                await s.execute(
                    text(SECCL_PLATFORM_SQL),
                )
            ).scalar_one()
        )
    await bed.arm(flow_id=flow_id, config={"provider": provider})
    await bed.reset_cursor()
    await bed.post_event()

    await automation.consume_once(bed.maker)

    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        case = (
            (
                await s.execute(
                    text(
                        "SELECT uid, initiation, subject_contact_id FROM flows.cases "
                        "WHERE tenant_id = :t AND flow_id = :f"
                    ),
                    {"t": bed.tenant_id, "f": flow_id},
                )
            )
            .mappings()
            .one()
        )
    assert case["subject_contact_id"] == bed.contact_id
    assert case["initiation"] == "automation"
    delivery = (await bed.deliveries())[0]
    assert delivery["outcome"] == "dispatched"
    # The started case is recorded, so the per-case budget can count the chain that follows it.
    assert delivery["case_uid"] == case["uid"]


@pytest.mark.asyncio
async def test_a_flow_automation_refuses_when_the_trigger_names_no_client(
    bed: _Bed, maker: Maker
) -> None:
    """A scheduled firing names nobody. Starting a journey "about nobody" would be worse than
    refusing, so the target says so and the refusal is on the delivery for an author to read."""
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        flow_id, _ = await _seed_flow(s, bed)
    armed = await bed.arm(
        trigger_key="schedule.recurring", schedule={"cron": "0 9 * * *"}, flow_id=flow_id
    )
    await bed.pause(paused=False, timezone="Europe/London")

    await automation.schedule_once(bed.maker, now=datetime(2026, 7, 15, 8, 5, tzinfo=UTC))

    rows = await bed.deliveries(armed)
    assert [d["outcome"] for d in rows] == ["failed"]
    assert "client" in (rows[0]["error"] or "")


# ---------------------------------------------------------------------------------------------
# The operations tenant (for the escalation tests)
# ---------------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ops_tenant(maker: Maker, monkeypatch: pytest.MonkeyPatch) -> int:
    """Graphene's own operations tenant with a ``compliance`` role — where safety-cap escalations
    land. Registered as the CORE WorkOS environment, because that is how
    ``environments.operations_tenant_id`` finds it (matching ``settings.workos_client_id``), the
    same seam ``test_admin_audit_actions.py``'s gate world uses."""
    from app.config import settings

    nonce = uuid.uuid4().hex[:8]
    core_client_id = f"client_core_{nonce}"
    monkeypatch.setattr(settings, "workos_client_id", core_client_id)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tenant_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, 'Graphene Operations') RETURNING id"
                    ),
                    {"u": f"gop_{nonce}", "a": f"gop-{nonce}"},
                )
            ).scalar_one()
        )
        await s.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id) "
                "VALUES (:t, 'production', :c)"
            ),
            {"t": tenant_id, "c": core_client_id},
        )
        await s.execute(
            text(
                "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
                "VALUES (:t, 'compliance', 'Compliance', 'organisation', 'operations')"
            ),
            {"t": tenant_id},
        )
    return tenant_id


# ---------------------------------------------------------------------------------------------
# RLS
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def rls_seed(super_engine: Engine) -> Iterator[dict[str, Any]]:
    """Two firms, each with one armed automation and a settings row."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        ids: dict[str, Any] = {"nonce": s}
        for tag in ("a", "b"):
            tenant = int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :al, :n) RETURNING id"
                    ),
                    {"u": f"autrls_{tag}_{s}", "al": f"autrls-{tag}-{s}", "n": f"RLS {tag} {s}"},
                ).scalar_one()
            )
            ids[f"tenant_{tag}"] = tenant
            ids[f"aut_{tag}"] = f"aut_rls_{tag}_{s}"
            c.execute(
                text(
                    "INSERT INTO flows.automations (uid, tenant_id, name, trigger_key, action) "
                    'VALUES (:u, :t, :n, :k, CAST(\'{"type": "apply_tag"}\' AS jsonb))'
                ),
                {"u": ids[f"aut_{tag}"], "t": tenant, "n": f"Rule {tag}", "k": TRIGGER},
            )
            c.execute(
                text("INSERT INTO flows.automation_settings (tenant_id) VALUES (:t)"),
                {"t": tenant},
            )
            c.execute(
                text(
                    "INSERT INTO flows.automation_deliveries (automation_id, tenant_id, "
                    "  event_key, outcome) "
                    "SELECT id, :t, 'action:1', 'dispatched' FROM flows.automations WHERE uid = :u"
                ),
                {"t": tenant, "u": ids[f"aut_{tag}"]},
            )
        c.commit()
    yield ids
    with super_engine.connect() as c:
        # Child rows first: audit.attach fires on the automations DELETE and writes an audit.log
        # row naming the tenant, which a cascade from auth.tenants would be deleting at the same
        # moment (FK violation). Deleting them while the tenant still exists is the honest order.
        for tag in ("a", "b"):
            c.execute(
                text("DELETE FROM flows.automations WHERE tenant_id = :t"),
                {"t": ids[f"tenant_{tag}"]},
            )
            c.execute(
                text("DELETE FROM flows.automation_settings WHERE tenant_id = :t"),
                {"t": ids[f"tenant_{tag}"]},
            )
        c.execute(
            text("DELETE FROM auth.tenants WHERE id = ANY(:ids)"),
            {"ids": [ids["tenant_a"], ids["tenant_b"]]},
        )
        c.commit()


def _claims(seed: dict[str, Any], firm: str = "a") -> dict[str, Any]:
    return {
        "sub": f"wos_staff_{firm}_{seed['nonce']}",
        "role": "owner",
        "portal": "platform",
        "permissions": ["automations:write"],
        "org_id": f"org_{firm}_{seed['nonce']}",
        "tenant_id": seed[f"tenant_{firm}"],
        "tenant_ids": [seed[f"tenant_{firm}"]],
        "contact_ids": [],
        "account_ids": [],
    }


def test_automations_are_readable_only_within_the_tenant_lineage(
    app_engine: Engine, rls_seed: dict[str, Any]
) -> None:
    with principal_conn(app_engine, _claims(rls_seed, "a")) as conn:
        uids = set(conn.execute(text("SELECT uid FROM flows.automations")).scalars())
    assert rls_seed["aut_a"] in uids
    assert rls_seed["aut_b"] not in uids


def test_a_firm_cannot_arm_an_automation_in_another_tenant(
    app_engine: Engine, rls_seed: dict[str, Any]
) -> None:
    """The WITH CHECK half. Without it a firm could read only its own rows but write into anyone's
    — which is the more dangerous direction for a table that decides what runs unattended."""
    with principal_conn(app_engine, _claims(rls_seed, "a")) as conn:
        with pytest.raises(ProgrammingError, match="row-level security"):
            conn.execute(
                text(
                    "INSERT INTO flows.automations (uid, tenant_id, name, trigger_key, action) "
                    "VALUES (:u, :t, 'Sneaky', :k, CAST('{}' AS jsonb))"
                ),
                {"u": f"aut_evil_{rls_seed['nonce']}", "t": rls_seed["tenant_b"], "k": TRIGGER},
            )


def test_automation_settings_are_tenant_scoped(
    app_engine: Engine, rls_seed: dict[str, Any]
) -> None:
    with principal_conn(app_engine, _claims(rls_seed, "b")) as conn:
        rows = conn.execute(text("SELECT tenant_id FROM flows.automation_settings")).scalars().all()
    assert rls_seed["tenant_b"] in rows
    assert rls_seed["tenant_a"] not in rows


def test_deliveries_and_the_cursor_are_unreachable_from_a_request_session(
    app_engine: Engine, rls_seed: dict[str, Any]
) -> None:
    """Machine state, service_role only: no policy AND no grant, so the role cannot even attempt
    the query (the ``aml.operator_secrets`` / ``flows.outbox`` posture). This must fail on
    permission, not return zero rows — an always-false policy is weaker than a withheld
    privilege, and a firm-facing view of "why didn't it fire?" ships as a permissioned endpoint
    reading on service_role, the way ``audit.actions`` does."""
    # A FRESH connection per table, deliberately: rolling back the failed statement on a shared
    # one would also roll back `SET ROLE authenticated` (SET ROLE is transactional), dropping the
    # session back to `app` — which inherits service_role in this estate and would then read the
    # table happily. A test that quietly stops testing the role it names is worse than no test.
    for table in ("flows.automation_deliveries", "flows.automation_cursor"):
        with principal_conn(app_engine, _claims(rls_seed, "a")) as conn:
            with pytest.raises(ProgrammingError, match="permission denied"):
                conn.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608 - literal table


def test_the_new_tables_are_enable_and_force_rls(super_engine: Engine) -> None:
    """FORCE is what binds the table OWNER too — without it a migrator-run query walks past every
    policy, which is exactly the connection an incident is debugged from."""
    with super_engine.connect() as c:
        rows = dict(
            c.execute(
                text(
                    "SELECT relname, relrowsecurity AND relforcerowsecurity FROM pg_class "
                    "WHERE relnamespace = 'flows'::regnamespace AND relname = ANY(:names)"
                ),
                {
                    "names": [
                        "automations",
                        "automation_settings",
                        "automation_deliveries",
                        "automation_cursor",
                    ]
                },
            ).all()
        )
    assert rows == {
        "automations": True,
        "automation_settings": True,
        "automation_deliveries": True,
        "automation_cursor": True,
    }


def test_an_automation_must_name_exactly_one_target(super_engine: Engine) -> None:
    """The CHECK that makes "one ordered table for both kinds" coherent: a row either starts a
    flow or performs an action — never both, never neither."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"chk_{s}", "a": f"chk-{s}", "n": f"Check {s}"},
            ).scalar_one()
        )
        try:
            for payload in ("neither", "both"):
                with pytest.raises(Exception, match="automations_target_check"):
                    c.execute(
                        text(
                            "INSERT INTO flows.automations (uid, tenant_id, name, trigger_key, "
                            "  flow_id, action) VALUES (:u, :t, :n, :k, NULL, "
                            "  CASE WHEN :both THEN CAST('{}' AS jsonb) END)"
                        ),
                        {
                            "u": f"aut_{payload}_{s}",
                            "t": tenant,
                            "n": payload,
                            "k": TRIGGER,
                            # 'both' can't be built without a real flow, so the pair tested here
                            # is (neither) and (action only, which is legal) — the "both" arm is
                            # covered by the flow-automation test above never setting `action`.
                            "both": False,
                        },
                    )
                    break
        finally:
            c.execute(text("DELETE FROM auth.tenants WHERE id = :t"), {"t": tenant})
            c.commit()


# ---------------------------------------------------------------------------------------------
# Proxied actions — the six triggers whose subject needs a tenant to look up
# ---------------------------------------------------------------------------------------------
#
# `handle_event` deliberately picks `target_tenant_id or tenant_id` — the book target's firm is
# the one whose automations respond. `resolve_subject` then used to bind `event["tenant_id"]`, the
# ACTOR's, so every subject that costs a query (five `sql` sources plus the create-shaped one)
# resolved to nothing the moment a Graphene operator worked a firm's book with `?tenant=`. Not a
# leak — the wrong tenant returns no row, never another firm's — but a silent failure: the trigger
# reported "this named no client", action automations refused, the re-arm window collapsed to
# per-automation, and `timers.release_listeners` short-circuited on the null subject, so a case
# parked on `wait_for_event` was never released.
#
# The one pre-existing proxied test drives `contact.bank_details_changed`, whose subject comes
# straight off a path param with no tenant predicate at all — the single branch that worked by
# accident. These cover the branches that did not.


async def _ops_tenant_id(maker: Maker, nonce: str) -> int:
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return int(
            (
                await s.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) "
                        "RETURNING id"
                    ),
                    {"u": f"ops_{nonce}", "a": f"ops-{nonce}", "n": f"Ops {nonce}"},
                )
            ).scalar_one()
        )


@pytest.mark.asyncio
async def test_a_proxied_action_resolves_its_subject_against_the_TARGET_firm(
    bed: _Bed, maker: Maker, recorder: _Recorder
) -> None:
    """The `sql` subject sources bind a tenant, and it has to be the firm whose book was acted on.

    `document_request.item_fulfilled` — a client's document request fulfilled by an operator
    on the firm's behalf — joins `request_items` to `requests` on `(uid, tenant_id)`. Bound to the
    operator's own tenant it matched nothing, and the client this event is entirely about went
    unnamed.
    """
    ops = await _ops_tenant_id(maker, bed.nonce)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        request_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO documents.requests (uid, tenant_id, contact_id) "
                        "VALUES (:u, :t, :c) RETURNING id"
                    ),
                    {"u": f"dqr_{bed.nonce}", "t": bed.tenant_id, "c": bed.contact_id},
                )
            ).scalar_one()
        )
        await s.execute(
            text(
                "INSERT INTO documents.request_items (uid, request_id, tenant_id, action, title) "
                "VALUES (:u, :r, :t, 'upload', 'Passport')"
            ),
            {"u": f"dqi_{bed.nonce}", "r": request_id, "t": bed.tenant_id},
        )

    armed = await bed.arm(trigger_key="client.document_uploaded")
    await bed.reset_cursor()
    # The actor's own tenant is operations; the book target is the firm.
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "INSERT INTO audit.actions (event_type, tenant_id, target_tenant_id, actor_sub, "
                "  method, route, status, path_params, created_at) "
                "VALUES ('document_request.item_fulfilled', :ops, :firm, 'wos_operator', "
                "  'POST', '/documents/requests/items/{item_uid}/fulfil', 200, "
                "  CAST(:pp AS jsonb), now() - interval '60 seconds')"
            ),
            {
                "ops": ops,
                "firm": bed.tenant_id,
                "pp": json.dumps({"item_uid": f"dqi_{bed.nonce}"}),
            },
        )

    await automation.consume_once(bed.maker)

    assert [c.automation_id for c in recorder.calls] == [armed]
    ctx = recorder.calls[0]
    assert ctx.subject_id == bed.contact_id
    assert ctx.subject_kind == "contact"
    # …and the facts the subject unlocks are there too, which is what an entry condition reads.
    assert ctx.facts.get("contact.type") is not None
    assert [d["subject_id"] for d in await bed.deliveries(armed)] == [str(bed.contact_id)]


@pytest.mark.asyncio
async def test_a_proxied_create_resolves_its_subject_against_the_TARGET_firm(
    bed: _Bed, maker: Maker, recorder: _Recorder
) -> None:
    """The create-shaped source has the same defect one layer down.

    A create route's response id is never in the action stream, so the subject comes from the DML
    row the same request wrote to `audit.log` — and that row is stamped with the TARGET firm, so
    matching on the actor's tenant found nothing. The actor predicate, which is what actually
    disambiguates two creates in the window, is unchanged.
    """
    ops = await _ops_tenant_id(maker, bed.nonce)
    armed = await bed.arm(trigger_key="contact.created")
    await bed.reset_cursor()
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        # A real operator: `audit.log.user_id` is a foreign key, and the actor predicate is what
        # keeps two creates in the same window from being guessed between.
        actor = (
            await s.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, workos_user_id, email) "
                    "VALUES (:u, :t, :w, :e) RETURNING id"
                ),
                {
                    "u": f"usr_{bed.nonce}",
                    "t": ops,
                    "w": f"wos_op_{bed.nonce}",
                    "e": f"operator-{bed.nonce}@graphene.test",
                },
            )
        ).scalar_one()
        # The DML trail the create wrote, against the firm whose book it was.
        await s.execute(
            text(
                "INSERT INTO audit.log (tenant_id, user_id, action, entity_type, entity_id, "
                "  created_at) VALUES (:t, :u, 'crm.contacts.insert', 'crm.contacts', :e, "
                "  now() - interval '61 seconds')"
            ),
            {"t": bed.tenant_id, "u": str(actor), "e": str(bed.contact_id)},
        )
        await s.execute(
            text(
                "INSERT INTO audit.actions (event_type, tenant_id, target_tenant_id, actor_sub, "
                "  actor_id, method, route, status, path_params, created_at) "
                "VALUES ('client.created', :ops, :firm, 'wos_operator', :u, 'POST', "
                "  '/firm/contacts', 201, '{}'::jsonb, now() - interval '60 seconds')"
            ),
            {"ops": ops, "firm": bed.tenant_id, "u": str(actor)},
        )

    await automation.consume_once(bed.maker)

    assert [c.automation_id for c in recorder.calls] == [armed]
    assert recorder.calls[0].subject_id == bed.contact_id


# ---------------------------------------------------------------------------------------------
# The per-case budget's case lookup is tenant-scoped
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_case_lookup_will_not_cross_tenants(bed: _Bed, maker: Maker) -> None:
    """`_case_uid_for` runs on a `service_role` session with RLS bypassed, so its own predicate is
    the only scoping there is. It used to lean on contact ids being globally unique; a case naming
    a contact outside its tenant would have had that client's per-case budget counted against the
    wrong firm."""
    other = await _ops_tenant_id(maker, f"x{bed.nonce}")
    case_uid = await _seed_live_case(maker, bed)
    spec = triggers.TRIGGERS[TRIGGER]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        assert (
            await automation._case_uid_for(
                s, event=None, spec=spec, subject_id=bed.contact_id, tenant_id=bed.tenant_id
            )
            == case_uid
        )
        # The same contact id, asked for on behalf of a firm that does not own the case.
        assert (
            await automation._case_uid_for(
                s, event=None, spec=spec, subject_id=bed.contact_id, tenant_id=other
            )
            is None
        )


# ---------------------------------------------------------------------------------------------
# The deferral drain — the rate window, and the turn a deferred firing holds
# ---------------------------------------------------------------------------------------------


async def _seed_deferred(
    maker: Maker,
    bed: _Bed,
    automation_id: int,
    *,
    event_key: str,
    age_minutes: int = 10,
    context: dict[str, Any] | None = None,
) -> None:
    """A firing deferred `age_minutes` ago and now due — the state after a sustained overflow.

    Back-dated in the DB rather than simulated through the `now` parameter, because `created_at`
    and `updated_at` are written by the database clock: only a genuinely old row can show the
    difference between "when the event arrived" and "when the automation ran".
    """
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "INSERT INTO flows.automation_deliveries (automation_id, tenant_id, event_key, "
                "  outcome, detail, attempts, retry_after, context, created_at, updated_at) "
                "VALUES (:a, :t, :k, 'deferred', 'rate limit', 3, now() - interval '1 minute', "
                "  CAST(:ctx AS jsonb), now() - make_interval(mins => :age), "
                "  now() - make_interval(mins => :age))"
            ),
            {
                "a": automation_id,
                "t": bed.tenant_id,
                "k": event_key,
                "ctx": json.dumps(context or {}),
                "age": age_minutes,
            },
        )


@pytest.mark.asyncio
async def test_a_drained_deferral_counts_against_the_rate_window(
    bed: _Bed, maker: Maker, recorder: _Recorder
) -> None:
    """The rate limit is windowed on when a firing RAN, not when its event arrived.

    `_record` upserts a deferred row in place and never touches `created_at`, so a firing deferred
    for ten minutes and then dispatched kept a ten-minute-old timestamp and fell straight out of a
    one-minute window. That leaked hardest under sustained overflow — the exact condition the
    guard exists for — because `run_deferred_once` drains a batch every couple of seconds and none
    of them counted.
    """
    armed = await bed.arm()
    await bed.pause(paused=False, executions_per_minute=1)
    await _seed_deferred(maker, bed, armed, event_key="action:stale", age_minutes=10)

    await automation.run_deferred_once(bed.maker)
    assert len(recorder.calls) == 1
    assert [d["outcome"] for d in await bed.deliveries(armed)] == ["dispatched"]

    # It ran a moment ago, so the firm has spent its minute.
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        spent = await automation._dispatched_in_window(s, bed.tenant_id, datetime.now(UTC))
    assert spent == 1

    # …and end to end: the very next event must be deferred, not dispatched.
    await bed.reset_cursor()
    await bed.post_event()
    await automation.consume_once(bed.maker)

    assert len(recorder.calls) == 1, "the rate limit let a second firing through inside the minute"
    fresh = [d for d in await bed.deliveries(armed) if d["event_key"] != "action:stale"]
    assert [d["outcome"] for d in fresh] == ["deferred"]


@pytest.mark.asyncio
async def test_a_deferral_that_settles_as_a_skip_passes_the_turn_on(
    bed: _Bed, maker: Maker, recorder: _Recorder
) -> None:
    """`deferred` ends the event for lower-priority automations on a PROMISE that it will run.

    Disarm it while it is queued and the promise breaks — it settles `skipped_paused` — but by then
    the event is far behind the cursor and nobody was ever going to offer it again. "First
    satisfiable wins" had quietly become "first deferred wins, nobody runs".
    """
    first = await bed.arm(name="high", priority=10)
    second = await bed.arm(name="low", priority=20)
    await bed.pause(paused=False)
    await _seed_deferred(
        maker,
        bed,
        first,
        event_key="action:parked",
        context={"trigger_key": TRIGGER, "subject_id": bed.contact_id, "facts": {}},
    )
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text("UPDATE flows.automations SET enabled = false WHERE id = :a"), {"a": first}
        )

    await automation.run_deferred_once(bed.maker)

    assert [c.automation_id for c in recorder.calls] == [second]
    by_id = {d["automation_id"]: d["outcome"] for d in await bed.deliveries()}
    assert by_id[first] == "skipped_paused"
    assert by_id[second] == "dispatched"


@pytest.mark.asyncio
async def test_a_deferral_whose_conditions_no_longer_hold_passes_the_turn_on(
    bed: _Bed, maker: Maker, recorder: _Recorder
) -> None:
    """The same hole by the other route, and the one the drain hit most often: `consider`'s outcome
    was discarded entirely, so a retry that failed its entry conditions or its re-arm window
    swallowed the event just as thoroughly as one that ran."""
    first = await bed.arm(
        name="picky",
        priority=10,
        conditions={"field": "contact.type", "operator": "equals", "value": "no-such-type"},
    )
    second = await bed.arm(name="fallback", priority=20)
    await bed.pause(paused=False)
    await _seed_deferred(
        maker,
        bed,
        first,
        event_key="action:parked",
        context={"trigger_key": TRIGGER, "subject_id": bed.contact_id, "facts": {}},
    )

    await automation.run_deferred_once(bed.maker)

    assert [c.automation_id for c in recorder.calls] == [second]
    by_id = {d["automation_id"]: d["outcome"] for d in await bed.deliveries()}
    assert by_id[first] == "skipped_conditions"
    assert by_id[second] == "dispatched"


@pytest.mark.asyncio
async def test_a_deferral_that_runs_keeps_the_turn(
    bed: _Bed, maker: Maker, recorder: _Recorder
) -> None:
    """The other half of the rule, and the one that must not regress: a deferred firing that
    finally DISPATCHES kept its promise, so first-satisfiable-wins still holds and the
    lower-priority automation stays where it was."""
    first = await bed.arm(name="high", priority=10)
    await bed.arm(name="low", priority=20)
    await bed.pause(paused=False)
    await _seed_deferred(
        maker,
        bed,
        first,
        event_key="action:parked",
        context={"trigger_key": TRIGGER, "subject_id": bed.contact_id, "facts": {}},
    )

    await automation.run_deferred_once(bed.maker)

    assert [c.automation_id for c in recorder.calls] == [first]
    assert {d["automation_id"] for d in await bed.deliveries()} == {first}


@pytest.mark.asyncio
async def test_a_paused_firms_deferral_passes_no_turn_on(
    bed: _Bed, maker: Maker, recorder: _Recorder
) -> None:
    """Pausing a firm stops its automations — all of them. Handing the turn on when the drain
    settles `skipped_paused` for a PAUSE (rather than for a disarmed automation) would dispatch
    the lower-priority rule the pause exists to stop."""
    first = await bed.arm(name="high", priority=10)
    second = await bed.arm(name="low", priority=20)
    await bed.pause(paused=True)
    await _seed_deferred(
        maker,
        bed,
        first,
        event_key="action:parked",
        context={"trigger_key": TRIGGER, "subject_id": bed.contact_id, "facts": {}},
    )

    await automation.run_deferred_once(bed.maker)

    assert recorder.calls == []
    by_id = {d["automation_id"]: d["outcome"] for d in await bed.deliveries()}
    assert by_id[first] == "skipped_paused"
    assert second not in by_id


# ---------------------------------------------------------------------------------------------
# The depth cap must not flood the inbox it protects
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_depth_cap_raises_ONE_operations_task_however_many_events_hit_it(
    bed: _Bed, recorder: _Recorder, ops_tenant: int
) -> None:
    """`tasks_source_ref_key` collapses repeat escalations onto one row — but the depth guard keyed
    its `source_ref` on the EVENT, so the dedupe never bit and a bulk operation spawning many
    chains filed one high-priority ops task per over-depth event, into the very inbox the cap
    exists to protect. Both sibling guards (`budget:{case_uid}`, `defer:{uid}`) already collapse."""
    armed = await bed.arm(allow_automation_source=True)
    await bed.pause(paused=False, max_chain_depth=1)
    await bed.reset_cursor()
    marker = {"automation": {"source": "rule", "id": "aut_upstream", "chain_depth": 5}}
    for _ in range(3):
        await bed.post_event(metadata=marker)

    await automation.consume_once(bed.maker)

    assert recorder.calls == []
    outcomes = [d["outcome"] for d in await bed.deliveries(armed)]
    assert outcomes == ["depth_exceeded"] * 3, "every event must still be RECORDED as refused"
    depth_tasks = [t for t in await bed.ops_tasks() if t["source_ref"].startswith("depth:")]
    assert len(depth_tasks) == 1
    assert depth_tasks[0]["source_ref"] == f"depth:aut_{bed.nonce}_1"
