"""``GET /firm/event-registry`` — the event-first view of what notifies whom.

The claim worth testing is not that it returns the catalogue (it does, unchanged) but that the two
COUNTS attach to the right event. The automation registry and the notification catalogue give the
same event different friendly keys — `flows.triggers` calls it `document.sent`, the notification
catalogue calls it `document.shared` — and the raw `event_type` is the only thing they agree on. A
count joined on the friendly key would silently read zero for every firm.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit_actions as actions
from app.auth import Principal
from app.db import FirmContext
from app.flows import triggers as flow_triggers
from app.notifications import catalogue as notif_catalogue
from app.routers import firm as firm_router

pytestmark = pytest.mark.asyncio


def _ctx(session: AsyncSession, tenant_id: int, *perms: str) -> FirmContext:
    return FirmContext(
        session=session,
        tenant_id=tenant_id,
        principal=Principal(user_id="u_er", organisation_id="o_er", permissions=list(perms)),
    )


async def _tenant(session: AsyncSession, uid: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :u, :u) RETURNING id"
                ),
                {"u": uid},
            )
        ).scalar_one()
    )


@pytest.fixture
async def firm(service_session: AsyncSession):
    tid = await _tenant(service_session, f"ten_er_{uuid.uuid4().hex[:8]}")
    yield tid
    for stmt in (
        "DELETE FROM comms.messages WHERE tenant_id = :t",
        "DELETE FROM flows.automations WHERE tenant_id = :t",
        "DELETE FROM notifications.rules WHERE tenant_id = :t",
        "DELETE FROM auth.tenants WHERE id = :t",
    ):
        await service_session.execute(text(stmt), {"t": tid})


def _entry(payload: dict, event_type: str) -> dict:
    """Rows are keyed by EVENT now, not by catalogue trigger — the registry lists every declared
    event and a trigger is one thing that may be attached to one."""
    return next(item for item in payload["items"] if item["event_type"] == event_type)


async def test_it_lists_every_declared_event(service_session: AsyncSession, firm: int) -> None:
    """ALL of them. An earlier version listed only the events a notification can currently be
    authored against, which is a shorter and less useful answer to "what happens in my firm" — the
    rows that cannot be assigned anything say so instead of being hidden."""
    payload = await firm_router.get_firm_event_registry(
        _ctx(service_session, firm, "notifications:read")
    )
    listed = {item["event_type"] for item in payload["items"]}
    declared = set(actions.EVENT_TYPES.values()) | set(actions.SYNTHETIC_EVENT_TYPES.values())
    assert listed == declared
    # And every event the catalogue covers is among them, marked assignable.
    covered = {e for t in notif_catalogue.CATALOGUE for e in t.event_types}
    assert covered <= listed
    for event in covered:
        assert _entry(payload, event)["state"] == "assignable"


async def test_each_event_reports_one_of_four_states(
    service_session: AsyncSession, firm: int
) -> None:
    """`assignable`, `ready`, `declarable` or `actor_only`.

    The distinction that matters — and that an earlier version got wrong — is between an event that
    RECORDS a subject and one a subject can be RESOLVED from. The first is
    `event_schema.REGISTRY`, the second is `automation._SUBJECTS`, and only the second reaches a
    recipient. `declarable` is the difference between them."""
    payload = await firm_router.get_firm_event_registry(
        _ctx(service_session, firm, "notifications:read")
    )
    summary = payload["summary"]
    assert summary["total"] == len(payload["items"])
    assert summary["total"] == sum(
        summary[state] for state in ("assignable", "ready", "declarable", "actor_only")
    )
    for item in payload["items"]:
        assert item["state"] in {"assignable", "ready", "declarable", "actor_only"}
        # A row that cannot be assigned must SAY why; one that can must not carry an excuse.
        if item["state"] == "assignable":
            assert item["unavailable_reason"] is None
            assert item["trigger"] is not None
        else:
            assert item["unavailable_reason"]
            assert item["trigger"] is None


async def test_an_actor_only_event_names_no_subject(
    service_session: AsyncSession, firm: int
) -> None:
    """No captured subject means no client or account to resolve — but the ACTOR is still reachable
    on every one of them, which is why this is a state rather than a dead end."""
    payload = await firm_router.get_firm_event_registry(
        _ctx(service_session, firm, "notifications:read")
    )
    actor_only = [i for i in payload["items"] if i["state"] == "actor_only"]
    assert actor_only, "some events genuinely name nobody"
    for item in actor_only:
        assert not set(item["subject_kinds"]) & flow_triggers.SUBJECT_KINDS
        assert item["actor_resolvable"] is True


async def test_a_declarable_event_captures_a_subject_nothing_can_resolve(
    service_session: AsyncSession, firm: int
) -> None:
    """The cheapest real work on this surface, and the state that only exists because the two sets
    differ: the event RECORDS a contact or account, and `automation._SUBJECTS` declares no source
    for it, so every generic resolver returns nobody."""
    from app.flows import automation as flow_automation

    payload = await firm_router.get_firm_event_registry(
        _ctx(service_session, firm, "notifications:read")
    )
    resolvable = flow_automation.events_with_subject_source()
    declarable = [i for i in payload["items"] if i["state"] == "declarable"]
    assert declarable, "some events capture a subject with no source declared"
    for item in declarable:
        assert set(item["subject_kinds"]) & flow_triggers.SUBJECT_KINDS
        assert item["event_type"] not in resolvable


async def test_every_subject_source_is_used_by_a_trigger(
    service_session: AsyncSession, firm: int
) -> None:
    """`ready` is empty today and that is worth pinning: every declared subject source already has
    a notification built on it, so there is no free win there. If this fails, someone added a
    source without a trigger — which is a five-tuple away from being offerable."""
    from app.flows import automation as flow_automation

    payload = await firm_router.get_firm_event_registry(
        _ctx(service_session, firm, "notifications:read")
    )
    ready = [i["event_type"] for i in payload["items"] if i["state"] == "ready"]
    assert ready == [], (
        f"these events can resolve a client but notify nobody: {ready}. Each needs a five-tuple in "
        "catalogue._OFFERED."
    )
    assert flow_automation.events_with_subject_source()


async def test_a_ready_event_names_a_usable_subject(
    service_session: AsyncSession, firm: int
) -> None:
    payload = await firm_router.get_firm_event_registry(
        _ctx(service_session, firm, "notifications:read")
    )
    for item in payload["items"]:
        if item["state"] == "ready":
            assert set(item["subject_kinds"]) & flow_triggers.SUBJECT_KINDS


async def test_it_needs_notifications_read(service_session: AsyncSession, firm: int) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        await firm_router.get_firm_event_registry(_ctx(service_session, firm))
    assert caught.value.status_code == 403


async def test_every_entry_carries_its_event_and_both_counts(
    service_session: AsyncSession, firm: int
) -> None:
    payload = await firm_router.get_firm_event_registry(
        _ctx(service_session, firm, "notifications:read")
    )
    for item in payload["items"]:
        assert item["event_type"], "every row names the event it is about"
        assert item["automation_count"] == 0
        assert item["email_count_30d"] == 0
        assert item["rules"] == []


async def test_a_stored_rule_is_attached_to_its_event(
    service_session: AsyncSession, firm: int
) -> None:
    await service_session.execute(
        text(
            "INSERT INTO notifications.rules (uid, tenant_id, trigger_key, audience, label, "
            "  title, authored_by, enabled) VALUES (:u, :t, 'document.shared', 'client', "
            "  'Document sent', 'Sent', 'firm', true)"
        ),
        {"u": f"nrule_{uuid.uuid4().hex[:6]}", "t": firm},
    )
    payload = await firm_router.get_firm_event_registry(
        _ctx(service_session, firm, "notifications:read")
    )
    entry = _entry(payload, "document.sent")
    assert [r["audience"] for r in entry["rules"]] == ["client"]
    # ...and only to its own event.
    assert _entry(payload, "task.created")["rules"] == []


async def test_an_automation_counts_against_the_event_not_its_friendly_key(
    service_session: AsyncSession, firm: int
) -> None:
    """The bridge this endpoint exists to build. The automation registry's key for this event is
    `document.sent`; the notification catalogue's is `document.shared`. Joining on either key alone
    counts nothing."""
    automation_key = "document.sent"
    spec = flow_triggers.resolve(automation_key)
    assert spec is not None and spec.event == "document.sent"
    catalogue_key = "document.shared"
    assert automation_key != catalogue_key, "the whole point — the two registries disagree"

    await service_session.execute(
        text(
            "INSERT INTO flows.automations (uid, tenant_id, name, trigger_key, enabled, action) "
            # `automations_target_check`: exactly one of flow_id / action. An action is the
            # cheaper target to seed — a flow_id would need a whole published flow.
            "VALUES (:u, :t, 'Chase', :k, true, '{\"type\": \"raise_task\"}'::jsonb)"
        ),
        {"u": f"auto_{uuid.uuid4().hex[:6]}", "t": firm, "k": automation_key},
    )
    payload = await firm_router.get_firm_event_registry(
        _ctx(service_session, firm, "notifications:read")
    )
    assert _entry(payload, "document.sent")["automation_count"] == 1


async def test_a_disabled_automation_is_not_counted(
    service_session: AsyncSession, firm: int
) -> None:
    """A switched-off automation is not something happening on that event."""
    await service_session.execute(
        text(
            "INSERT INTO flows.automations (uid, tenant_id, name, trigger_key, enabled, action) "
            "VALUES (:u, :t, 'Off', 'document.sent', false, '{\"type\": \"raise_task\"}'::jsonb)"
        ),
        {"u": f"auto_{uuid.uuid4().hex[:6]}", "t": firm},
    )
    payload = await firm_router.get_firm_event_registry(
        _ctx(service_session, firm, "notifications:read")
    )
    assert _entry(payload, "document.sent")["automation_count"] == 0


async def test_the_email_count_is_recent_and_this_firm_only(
    service_session: AsyncSession, firm: int
) -> None:
    """The count is what turns "this rule exists" into "this rule is doing something", so it has to
    describe the RECENT past and THIS firm — one seeding covers both, because a count that is wrong
    either way is wrong in the same place.

    Two recent sends, one 31 days old, and one from another firm: the answer is 2.
    """
    other = await _tenant(service_session, f"ten_er_o_{uuid.uuid4().hex[:8]}")

    async def _seed(tenant: int, prefix: str, age: str = "0 days") -> None:
        await service_session.execute(
            text(
                "INSERT INTO comms.messages (uid, tenant_id, recipient_kind, to_address, "
                "  to_domain, subject, status, event_type, created_at) "
                "VALUES (:u, :t, 'external', 'a@b.test', 'b.test', 's', 'sent', 'document.sent', "
                "  now() - CAST(:age AS interval))"
            ),
            {"u": f"{prefix}{uuid.uuid4().hex[:9]}"[:12], "t": tenant, "age": age},
        )

    try:
        await _seed(firm, "m0")
        await _seed(firm, "m1")
        await _seed(firm, "old", age="31 days")
        await _seed(other, "oth")

        payload = await firm_router.get_firm_event_registry(
            _ctx(service_session, firm, "notifications:read")
        )
        assert _entry(payload, "document.sent")["email_count_30d"] == 2
    finally:
        await service_session.execute(
            text("DELETE FROM comms.messages WHERE tenant_id = :t"), {"t": other}
        )
        await service_session.execute(text("DELETE FROM auth.tenants WHERE id = :t"), {"t": other})


async def test_an_unknown_stored_automation_key_is_skipped_not_fatal(
    service_session: AsyncSession, firm: int
) -> None:
    """`flows.triggers.resolve` returns None rather than raising for exactly this: a key the
    registry no longer knows must not wedge the whole page."""
    await service_session.execute(
        text(
            "INSERT INTO flows.automations (uid, tenant_id, name, trigger_key, enabled, action) "
            "VALUES (:u, :t, 'Legacy', 'retired.trigger', true, "
            '  \'{"type": "raise_task"}\'::jsonb)'
        ),
        {"u": f"auto_{uuid.uuid4().hex[:6]}", "t": firm},
    )
    payload = await firm_router.get_firm_event_registry(
        _ctx(service_session, firm, "notifications:read")
    )
    assert payload["summary"]["total"] == len(payload["items"])
