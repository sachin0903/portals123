"""The estate-wide automation kill switch — the thing ``automations:global_pause`` now guards.

The permission was registered, mirrored into ``triggers.PERMISSIONS`` and into the contract, and
checked by nothing at all: no endpoint, no flag, no consumer. So the tests here are less about
"the code works" than about the three properties that make the control real:

1. **The permission gates it.** An operations principal without the slug cannot read the switch,
   let alone press it. A grant that authorises nothing is the defect; a grant that authorises
   everyone is the way it gets re-introduced.
2. **The consumer honours it.** Not "the row is written" — the automation loop's gate is what
   actually stops a firm's rule from emailing a client, so it is driven directly.
3. **Resume does not replay the backlog.** Events that arrive while the platform is paused are
   dropped, matching the deployment-level switch (``app/config.py::automations_enabled``, whose
   own comment explains why: a night of held client emails arriving at once is a worse incident
   than the one the switch was pulled for).

DB writes hit the real testcontainer Postgres as ``service_role``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import Principal, get_current_principal
from app.db import admin_session
from app.flows import automation, platform_pause
from app.main import app

Maker = async_sessionmaker[AsyncSession]

SYSTEM = Principal(user_id="user_ops", permissions=["system"])
PAUSER = Principal(user_id="user_pauser", permissions=["automations:global_pause"])
#: An operations principal with a real but unrelated grant — the case a coarse "is this an ops
#: session?" gate would wave through.
BYSTANDER = Principal(user_id="user_reader", permissions=["automations:read", "tenants:read"])


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _reset(maker: Maker) -> AsyncIterator[None]:
    """The switch is a singleton, so unlike every other bed here it cannot be nonce-scoped. Reset
    it around each test rather than leaving a paused platform behind for the next one."""
    yield
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "UPDATE flows.automation_platform_pause "
                "SET paused = false, reason = NULL, resume_from_event_id = NULL, "
                "    paused_at = NULL, paused_by = NULL WHERE id"
            )
        )


def _client(maker: Maker, principal: Principal) -> AsyncClient:
    async def _session() -> AsyncIterator[AsyncSession]:
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            yield session

    app.dependency_overrides[admin_session] = _session
    app.dependency_overrides[get_current_principal] = lambda: principal
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _clear() -> None:
    app.dependency_overrides.pop(admin_session, None)
    app.dependency_overrides.pop(get_current_principal, None)


# ---------------------------------------------------------------------------------------------
# The permission
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_switch_is_gated_on_the_permission_that_names_it(maker: Maker) -> None:
    """``automations:global_pause`` had nothing behind it. Both ends of the endpoint now check it,
    the read as well as the write: "automations are off across the estate, since 03:12, because of
    the Meridian chaser" is incident detail, not a status page."""
    try:
        async with _client(maker, BYSTANDER) as client:
            assert (await client.get("/admin/automations/pause")).status_code == 403
            resp = await client.put("/admin/automations/pause", json={"paused": True})
            assert resp.status_code == 403
    finally:
        _clear()

    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        assert (await platform_pause.read(s)).paused is False


@pytest.mark.asyncio
async def test_an_operator_holding_the_slug_can_stop_and_restart_the_platform(
    maker: Maker,
) -> None:
    try:
        async with _client(maker, PAUSER) as client:
            paused = await client.put(
                "/admin/automations/pause",
                json={"paused": True, "reason": "Meridian chaser is emailing clients"},
            )
            assert paused.status_code == 200
            assert paused.json()["paused"] is True
            assert paused.json()["reason"] == "Meridian chaser is emailing clients"
            assert paused.json()["paused_at"] is not None

            assert (await client.get("/admin/automations/pause")).json()["paused"] is True

            resumed = await client.put("/admin/automations/pause", json={"paused": False})
            assert resumed.status_code == 200
            assert resumed.json()["paused"] is False
    finally:
        _clear()


@pytest.mark.asyncio
async def test_pausing_names_the_operator_who_did_it(maker: Maker) -> None:
    """ "Who turned the safety off" is the first question after an incident, and it must be
    answerable from the switch itself rather than only from the audit trail."""
    nonce = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tenant_id = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"gp_{nonce}", "a": f"gp-{nonce}", "n": f"Ops {nonce}"},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO auth.users (uid, tenant_id, workos_user_id, email, first_name, "
                "  last_name) VALUES (:uid, :t, :w, :e, 'Rae', 'Operator')"
            ),
            {
                "uid": f"usr_{nonce}",
                "t": tenant_id,
                "w": "user_pauser",
                "e": f"rae.{nonce}@example.test",
            },
        )

    try:
        async with _client(maker, PAUSER) as client:
            body = (
                await client.put(
                    "/admin/automations/pause", json={"paused": True, "reason": "incident 42"}
                )
            ).json()
            assert body["paused_by_name"] == "Rae Operator"
    finally:
        _clear()


# ---------------------------------------------------------------------------------------------
# The consumer honours it
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_gate_halts_the_consumer_while_paused(maker: Maker) -> None:
    """``gate`` is what ``worker._automations_loop`` consults before any of the three passes. All
    three are entered from that one place, so this IS the enforcement — there is no second
    production caller to slip past it."""
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        assert await platform_pause.gate(s) is False
        await platform_pause.set_paused(s, paused=True, reason="stop", actor_id=None)

    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        assert await platform_pause.gate(s) is True


@pytest.mark.asyncio
async def test_resuming_skips_the_backlog_rather_than_replaying_it(maker: Maker) -> None:
    """The property that makes "pause" mean pause.

    A resume records the stream head it wants skipped past; the worker's gate then parks the
    consumer's cursor there. Without it, an operator who stopped the platform at 03:00 would
    release four hours of held client emails the moment they turned it back on — the failure the
    deployment-level switch already avoids by advancing its cursor while off.
    """
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "INSERT INTO flows.automation_cursor (consumer, position) VALUES (:c, 0) "
                "ON CONFLICT (consumer) DO UPDATE SET position = 0"
            ),
            {"c": automation.CONSUMER},
        )
        await platform_pause.set_paused(s, paused=True, reason="stop", actor_id=None)

    # Events keep arriving while the platform is paused.
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        for _ in range(3):
            await s.execute(
                text(
                    "INSERT INTO audit.actions (event_type, actor_sub, method, route, status) "
                    "VALUES ('action.test-during-pause', 'user_someone', 'POST', '/test', 200)"
                )
            )
        head = (await s.execute(text("SELECT max(id) FROM audit.actions"))).scalar_one()

    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await platform_pause.set_paused(s, paused=False, reason=None, actor_id=None)

    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        assert await platform_pause.gate(s) is False
        position = (
            await s.execute(
                text("SELECT position FROM flows.automation_cursor WHERE consumer = :c"),
                {"c": automation.CONSUMER},
            )
        ).scalar_one()
        assert position == head, "the consumer must resume at the head, not replay the pause"
        # Cleared, so a later pause/resume cycle cannot be swallowed by a stale skip target.
        assert (await platform_pause.read(s)).resume_from_event_id is None


@pytest.mark.asyncio
async def test_the_resume_marker_never_drags_the_cursor_backwards(maker: Maker) -> None:
    """A worker that resumed, consumed a batch and only then observed the marker must not be sent
    back over events it has already dispatched — a replay is exactly what this switch exists to
    prevent, and doing it on the way OUT would be a nastier version of it."""
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "INSERT INTO flows.automation_cursor (consumer, position) VALUES (:c, 0) "
                "ON CONFLICT (consumer) DO UPDATE SET position = 0"
            ),
            {"c": automation.CONSUMER},
        )
        await platform_pause.set_paused(s, paused=True, reason="stop", actor_id=None)
        await platform_pause.set_paused(s, paused=False, reason=None, actor_id=None)
        ahead = (
            await s.execute(text("SELECT COALESCE(max(id), 0) + 1000 FROM audit.actions"))
        ).scalar_one()
        await s.execute(
            text("UPDATE flows.automation_cursor SET position = :p WHERE consumer = :c"),
            {"p": ahead, "c": automation.CONSUMER},
        )
        await platform_pause.gate(s)
        position = (
            await s.execute(
                text("SELECT position FROM flows.automation_cursor WHERE consumer = :c"),
                {"c": automation.CONSUMER},
            )
        ).scalar_one()
        assert position == ahead


@pytest.mark.asyncio
async def test_a_second_pause_keeps_the_first_ones_clock(maker: Maker) -> None:
    """ "How long has this been off" must keep its answer when a second operator adds their note."""
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        first = await platform_pause.set_paused(s, paused=True, reason="one", actor_id=None)
        second = await platform_pause.set_paused(s, paused=True, reason="two", actor_id=None)
        assert second.paused_at == first.paused_at
        assert second.reason == "two"


# ---------------------------------------------------------------------------------------------
# The table's posture
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_switch_is_service_role_only(maker: Maker) -> None:
    """No ``authenticated`` grant and no policy (0048), the ``flows.automation_cursor`` posture:
    withholding the privilege beats an always-false policy. A firm principal must not be able to
    read whether Graphene has stopped the estate, let alone write it."""
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE authenticated"))
        with pytest.raises(Exception, match="permission denied"):
            await s.execute(text("SELECT paused FROM flows.automation_platform_pause"))


@pytest.mark.asyncio
async def test_the_singleton_is_a_singleton(maker: Maker) -> None:
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        with pytest.raises(Exception, match="duplicate key|violates check constraint"):
            await s.execute(
                text("INSERT INTO flows.automation_platform_pause (id, paused) VALUES (true, true)")
            )
