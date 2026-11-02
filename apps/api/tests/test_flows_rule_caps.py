"""``caps.maxTriggersPerFlow`` — the contract limit that nothing on the server checked.

The builder's ``TriggerPanel`` counts the rules pointing at the flow on screen and stops offering
a fifth. That is the whole of the enforcement as shipped, and a cap enforced by the client is not
enforced: anything holding ``automations:write`` — a script, a curl, a second UI — walks past it.

These tests drive the server-side check directly against a real Postgres. Nonce-scoped, never
truncating (``test_effects``'s reasoning).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.flows import rule_caps, triggers

Maker = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class _Bed:
    def __init__(self, maker: Maker, nonce: str, tenant_id: int, flow_uid: str) -> None:
        self.maker = maker
        self.nonce = nonce
        self.tenant_id = tenant_id
        self.flow_uid = flow_uid

    async def arm(self, n: int) -> list[str]:
        """``n`` automations pointing at the bed's flow. Returns their uids."""
        uids: list[str] = []
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            flow_id = (
                await s.execute(
                    text("SELECT id FROM flows.flows WHERE uid = :u"), {"u": self.flow_uid}
                )
            ).scalar_one()
            for i in range(n):
                uid = f"aut_{self.nonce}_{i}"
                await s.execute(
                    text(
                        "INSERT INTO flows.automations "
                        "  (uid, tenant_id, name, trigger_key, flow_id, enabled) "
                        "VALUES (:u, :t, :n, 'contact.created', :f, true)"
                    ),
                    {"u": uid, "t": self.tenant_id, "n": f"Rule {i} {self.nonce}", "f": flow_id},
                )
                uids.append(uid)
        return uids

    async def disable(self, uid: str) -> None:
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            await s.execute(
                text("UPDATE flows.automations SET enabled = false WHERE uid = :u"), {"u": uid}
            )

    async def issue(self, **kwargs: Any) -> str | None:
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            return await rule_caps.flow_trigger_cap_issue(
                s, self.tenant_id, flow_uid=self.flow_uid, **kwargs
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
                    {"u": f"cap_{nonce}", "a": f"cap-{nonce}", "n": f"Caps {nonce}"},
                )
            ).scalar_one()
        )
        flow_uid = f"flw_{nonce}"
        await s.execute(
            text(
                "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
                "VALUES (:u, :t, :c, 'Onboarding', 'published')"
            ),
            {"u": flow_uid, "t": tenant_id, "c": f"onboard_{nonce}"},
        )
    return _Bed(maker, nonce, tenant_id, flow_uid)


@pytest.mark.asyncio
async def test_under_the_cap_is_allowed(bed: _Bed) -> None:
    await bed.arm(rule_caps.MAX_TRIGGERS_PER_FLOW - 1)
    assert await bed.issue() is None


@pytest.mark.asyncio
async def test_the_cap_refuses_a_further_trigger_on_the_same_flow(bed: _Bed) -> None:
    await bed.arm(rule_caps.MAX_TRIGGERS_PER_FLOW)
    issue = await bed.issue()
    assert issue is not None
    assert str(rule_caps.MAX_TRIGGERS_PER_FLOW) in issue


@pytest.mark.asyncio
async def test_a_disabled_rule_still_counts(bed: _Bed) -> None:
    """A disabled automation is a rule one click from firing. Counting only enabled ones would make
    a structural cap depend on a toggle."""
    uids = await bed.arm(rule_caps.MAX_TRIGGERS_PER_FLOW)
    await bed.disable(uids[0])
    assert await bed.issue() is not None


@pytest.mark.asyncio
async def test_editing_one_of_the_capped_rules_is_not_refused_by_its_own_existence(
    bed: _Bed,
) -> None:
    """The counting bug an edit path inherits from a create path: without excluding the rule being
    saved, an author could not so much as rename the fourth automation on a journey."""
    uids = await bed.arm(rule_caps.MAX_TRIGGERS_PER_FLOW)
    assert await bed.issue(exclude_uid=uids[-1]) is None


@pytest.mark.asyncio
async def test_another_firms_rules_do_not_count(bed: _Bed, maker: Maker) -> None:
    """The count is tenant-scoped as well as flow-scoped. Flow uids are unique estate-wide today,
    so this is belt and braces — and it is the brace that keeps one firm from consuming another's
    cap if that ever stops being true."""
    await bed.arm(rule_caps.MAX_TRIGGERS_PER_FLOW)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        other = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {
                    "u": f"cap2_{bed.nonce}",
                    "a": f"cap2-{bed.nonce}",
                    "n": f"Other {bed.nonce}",
                },
            )
        ).scalar_one()
        assert await rule_caps.flow_trigger_cap_issue(s, int(other), flow_uid=bed.flow_uid) is None


@pytest.mark.asyncio
async def test_a_rule_with_no_flow_target_is_never_capped(bed: _Bed) -> None:
    """An action rule targets no flow at all; the cap has nothing to say about it."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        assert await rule_caps.flow_trigger_cap_issue(s, bed.tenant_id, flow_uid=None) is None


@pytest.mark.pkgs_ui_pin
def test_the_cap_is_the_contracts_number() -> None:
    """One source of truth. The builder reads it out of the contract JSON; this reads it out of the
    Python mirror, and ``test_flows_triggers`` pins the two together."""
    assert rule_caps.MAX_TRIGGERS_PER_FLOW == triggers.CAPS["maxTriggersPerFlow"]
