"""``/automations`` — the rule tier's authoring surface.

The consumer's own behaviour is ``test_automations.py``'s job. This file owns the AUTHORING
contract, and it is written around the three promises the router makes:

* **The gate is per endpoint, and the pause switch is its own grant.** ``automations:read`` reads,
  ``automations:write`` authors, ``automations:pause`` hits the kill switch — and because the pause
  fields and the safety overrides share one settings endpoint, the gate is chosen by what the BODY
  touches. A rule author must not acquire the switch by writing a rule, and an incident responder
  must not need the authoring grant to stop the estate.
* **A rule that could never fire never gets stored.** Every refusal here is a refusal at authoring
  time, which is the only time anybody is looking.
* **The dry run performs nothing.** Pinned structurally: a recording dispatch target is registered
  for the duration of the test and asserted never to be called, and the delivery table is asserted
  empty afterwards. This is the load-bearing safety property of the whole feature — an author has to
  be able to point a draft at a real client without that being the thing that fires it.

Endpoints are called as functions with a hand-built ``FirmContext`` (the ``test_firm_audit_actions``
pattern) — the dependency wiring is FastAPI's business, the gate and the SQL are ours. The bed is
per-test-nonce and never truncates, so no assertion here can be made true or false by another
suite's leftovers.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.db import FirmContext
from app.flows import platform_pause, triggers
from app.flows.triggers import AutomationContext, DispatchResult
from app.routers import automations as api

# tests/ -> apps/api -> apps -> the repo root (the test_flows_triggers path convention).
_CONTRACT_PATH = (
    Path(__file__).resolve().parents[3]
    / "pkgs"
    / "ui"
    / "src"
    / "lib"
    / "components"
    / "book"
    / "journeys"
    / "flow-contract.json"
)

#: A registered action-sourced trigger whose subject is a contact — the cleanest thing to author
#: against, and the one the dry run can resolve without inventing an account.
TRIGGER = "contact.bank_details_changed"
#: A registered DOMAIN-sourced trigger. Storable, but nothing emits it yet
#: (``triggers.SUPPORTED_SOURCES``) — the surface must say so rather than pretend it is armed.
UNSUPPORTED_TRIGGER = "case.completed"
SCHEDULED_TRIGGER = "schedule.recurring"


# ---------------------------------------------------------------------------------------------
# Bed
# ---------------------------------------------------------------------------------------------


class Bed:
    """One firm, its client, and a published journey to point rules at."""

    def __init__(
        self, session: AsyncSession, tenant_id: int, contact_id: int, flow_uid: str, nonce: str
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.contact_id = contact_id
        self.flow_uid = flow_uid
        self.nonce = nonce

    def ctx(self, *perms: str) -> FirmContext:
        """The context ``firm_session`` would yield for a firm principal holding ``perms``."""
        return FirmContext(
            session=self.session,
            tenant_id=self.tenant_id,
            principal=Principal(
                user_id=f"wos_{self.nonce}",
                organisation_id=f"org_{self.nonce}",
                permissions=list(perms),
            ),
        )

    def action(self, **config: Any) -> api.ActionSpec:
        return api.ActionSpec(type="apply_tag", config={"tag": "chased", **config})

    async def arm(self, name: str, **kwargs: Any) -> dict[str, Any]:
        body = api.CreateAutomation(name=name, trigger_key=TRIGGER, action=self.action(), **kwargs)
        return await api.create_automation(body=body, ctx=self.ctx("automations:write"))


async def _tenant(session: AsyncSession, nonce: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"aua_{nonce}", "a": f"aua-{nonce}", "n": f"Automations API {nonce}"},
            )
        ).scalar_one()
    )


async def _grant_contacts_write(session: AsyncSession, tenant_id: int) -> None:
    """Give the firm a role holding ``contacts:write`` — the permission ``apply_tag``'s human
    equivalent needs.

    A3's ``effects.preflight`` refuses an action whose authority NO role in the firm holds
    ("the automation would be doing something nobody here is allowed to do"). That is correct, and
    it is exercised on its own below; every other case here needs a firm that has granted it,
    otherwise the whole file would be testing one refusal.
    """
    role_id = int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                    "VALUES (:t, 'adviser', 'Adviser', 'platform') RETURNING id"
                ),
                {"t": tenant_id},
            )
        ).scalar_one()
    )
    await session.execute(
        text(
            "INSERT INTO auth.role_permissions (role_id, permission_id) "
            "SELECT :r, id FROM auth.permissions WHERE slug = 'contacts:write' "
            "ON CONFLICT DO NOTHING"
        ),
        {"r": role_id},
    )


@pytest_asyncio.fixture
async def bed(service_session: AsyncSession) -> Bed:
    nonce = uuid.uuid4().hex[:8]
    tenant_id = await _tenant(service_session, nonce)
    contact_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, name, type, service_type, "
                    "  nationality) "
                    "VALUES (:t, :u, 'Ada Client', 'individual', 'advised', ARRAY['GB']) "
                    "RETURNING id"
                ),
                {"t": tenant_id, "u": f"con_{nonce}"},
            )
        ).scalar_one()
    )
    await _grant_contacts_write(service_session, tenant_id)

    flow_uid = f"flw_{nonce}"
    flow_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
                    "VALUES (:u, :t, :c, 'Welcome', 'published') RETURNING id"
                ),
                {"u": flow_uid, "t": tenant_id, "c": f"welcome_{nonce}"},
            )
        ).scalar_one()
    )
    await service_session.execute(
        text(
            "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status, "
            "  definition, published_at) "
            "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now())"
        ),
        {
            "u": f"flv_{nonce}",
            "t": tenant_id,
            "f": flow_id,
            "d": json.dumps({"steps": [], "milestones": []}),
        },
    )
    return Bed(service_session, tenant_id, contact_id, flow_uid, nonce)


class _Recorder:
    """A dispatch target that records every call. Registered around the dry-run tests purely so
    "the dry run performs nothing" is an assertion about behaviour and not about our reading of
    the code."""

    def __init__(self) -> None:
        self.calls: list[AutomationContext] = []

    async def run(self, session: AsyncSession, ctx: AutomationContext, spec: Any) -> DispatchResult:
        self.calls.append(ctx)
        return DispatchResult(ok=True)


@pytest.fixture
def recorder() -> Iterator[_Recorder]:
    rec = _Recorder()
    previous = triggers.TARGETS.get("action")
    triggers.register_target("action", rec)
    yield rec
    if previous is None:
        triggers.TARGETS.pop("action", None)
    else:
        triggers.register_target("action", previous)


# ---------------------------------------------------------------------------------------------
# The contract pin
# ---------------------------------------------------------------------------------------------


@pytest.mark.pkgs_ui_pin
def test_the_action_type_mirror_matches_the_contract() -> None:
    """``ACTION_CONFIG_KEYS`` is a Python mirror of flow-contract.json's ``actionTypes`` (the API
    container ships without pkgs/ui). Drift here means the builder offers a config key the API
    refuses, or the API accepts one the executor will never read — change the two together."""
    contract = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    expected = {kind: frozenset(spec["config"]) for kind, spec in contract["actionTypes"].items()}
    assert api.ACTION_CONFIG_KEYS == expected


@pytest.mark.pkgs_ui_pin
def test_the_rule_cap_comes_from_the_contract() -> None:
    contract = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    assert api.MAX_RULES_PER_TENANT == contract["caps"]["maxRulesPerTenant"]


# ---------------------------------------------------------------------------------------------
# Pure validation — no DB
# ---------------------------------------------------------------------------------------------


def test_trigger_issues_refuses_a_key_outside_the_registry() -> None:
    assert api.trigger_issues(TRIGGER) == []
    assert api.trigger_issues("contact.abducted") != []


def test_action_issues_refuses_unknown_types_and_unknown_config_keys() -> None:
    assert (
        api.action_issues({"type": "apply_tag", "config": {"tag": "x", "target": "contact"}}) == []
    )
    assert api.action_issues(None) == []
    assert api.action_issues({"type": "teleport", "config": {}}) != []
    # The typo case this exists for: `assignee` for `assign_to` would otherwise store a task
    # action that runs and quietly assigns nobody.
    issues = api.action_issues({"type": "raise_task", "config": {"assignee": "u"}})
    assert issues and "assignee" in issues[0]


def test_target_issues_demands_exactly_one_target() -> None:
    assert api.target_issues(flow_uid="flw_1", action=None) == []
    assert api.target_issues(flow_uid=None, action={"type": "apply_tag"}) == []
    assert api.target_issues(flow_uid="flw_1", action={"type": "apply_tag"}) != []
    assert api.target_issues(flow_uid=None, action=None) != []


def test_schedule_issues_pairs_a_schedule_with_a_scheduled_trigger() -> None:
    scheduled = triggers.resolve(SCHEDULED_TRIGGER)
    evented = triggers.resolve(TRIGGER)
    assert api.schedule_issues({"cron": "0 9 * * 1-5"}, spec=scheduled) == []
    assert api.schedule_issues(None, spec=scheduled) != []  # a schedule trigger needs one
    assert api.schedule_issues({"cron": "0 9 * * *"}, spec=evented) != []  # and only it takes one
    assert api.schedule_issues(None, spec=evented) == []


def test_schedule_issues_uses_the_same_cron_parser_the_worker_will() -> None:
    """A builder that accepts `@daily` and a worker whose parser returns None for it is a rule
    that silently never runs — the worst possible failure for this feature."""
    scheduled = triggers.resolve(SCHEDULED_TRIGGER)
    assert api.schedule_issues({"cron": "@daily"}, spec=scheduled) != []
    assert api.schedule_issues({"cron": "0 6-18/6 * * 1-5"}, spec=scheduled) == []
    assert (
        api.schedule_issues({"cron": "0 9 * * *", "timezone": "Mars/Olympus"}, spec=scheduled) != []
    )


def test_clamp_overrides_holds_the_ceilings() -> None:
    """The settings page must show what the engine will actually use. `limits_for` clamps on read
    anyway; clamping on write is what stops the console lying to the firm."""
    clamped = api.clamp_overrides(
        {"executions_per_minute": 100_000, "flow_concurrency": 5, "timezone": "Europe/London"}
    )
    assert clamped["executions_per_minute"] == triggers.CEILINGS["tenantExecutionsPerMinute"]
    assert clamped["flow_concurrency"] == 5  # under the ceiling: untouched
    assert clamped["timezone"] == "Europe/London"  # not a numeric override: passed through


# ---------------------------------------------------------------------------------------------
# The permission matrix
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reads_need_read_and_writes_need_write(bed: Bed) -> None:
    armed = await bed.arm("Gate check")
    uid = str(armed["uid"])

    # read endpoints: read is enough, and a wholly unrelated grant is not
    assert await api.list_automations(ctx=bed.ctx("automations:read")) is not None
    for call in (
        lambda: api.list_automations(ctx=bed.ctx("members:read")),
        lambda: api.get_automation(uid=uid, ctx=bed.ctx("members:read")),
        lambda: api.list_deliveries(uid=uid, ctx=bed.ctx("members:read")),
        lambda: api.get_settings(ctx=bed.ctx("members:read")),
    ):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 403

    # write endpoints refuse a read-only principal
    read_only = bed.ctx("automations:read")
    with pytest.raises(HTTPException) as exc:
        await api.create_automation(
            body=api.CreateAutomation(name="X", trigger_key=TRIGGER, action=bed.action()),
            ctx=read_only,
        )
    assert exc.value.status_code == 403
    for call in (
        lambda: api.update_automation(uid=uid, body=api.UpdateAutomation(name="Y"), ctx=read_only),
        lambda: api.delete_automation(uid=uid, ctx=read_only),
        lambda: api.enable_automation(uid=uid, ctx=read_only),
        lambda: api.disable_automation(uid=uid, ctx=read_only),
        lambda: api.reorder_automations(body=api.ReorderAutomations(uids=[uid]), ctx=read_only),
        lambda: api.test_automation(uid=uid, body=api.TestAutomation(), ctx=read_only),
    ):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_write_implies_read_and_system_bypasses(bed: Bed) -> None:
    await bed.arm("Implication")
    assert len(await api.list_automations(ctx=bed.ctx("automations:write"))) == 1
    assert len(await api.list_automations(ctx=bed.ctx("system"))) == 1


@pytest.mark.asyncio
async def test_the_pause_switch_is_its_OWN_grant_on_a_shared_endpoint(bed: Bed) -> None:
    """The load-bearing split. ``automations:pause`` is narrower than ``automations:write`` on
    purpose: an incident responder stops the estate without gaining authoring rights, and a rule
    author does not silently acquire the kill switch. One endpoint, gated by what the body touches.
    """
    pauser = bed.ctx("automations:pause")
    writer = bed.ctx("automations:write")

    # pause-only may pause…
    settings = await api.set_settings(
        body=api.SetSettings(paused=True, paused_reason="incident"), ctx=pauser
    )
    assert settings["paused"] is True
    assert settings["paused_reason"] == "incident"

    # …but may not retune the safety numbers
    with pytest.raises(HTTPException) as exc:
        await api.set_settings(body=api.SetSettings(executions_per_minute=10), ctx=pauser)
    assert exc.value.status_code == 403

    # write may retune…
    tuned = await api.set_settings(body=api.SetSettings(executions_per_minute=10), ctx=writer)
    assert tuned["effective"]["executions_per_minute"] == 10
    # …and the pause it did not touch survives (a PUT must not be a settings-wipe: that is exactly
    # the button somebody mashes in an incident).
    assert tuned["paused"] is True

    # …but may not release the switch
    with pytest.raises(HTTPException) as exc:
        await api.set_settings(body=api.SetSettings(paused=False), ctx=writer)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_settings_clamp_an_override_to_the_ceiling(bed: Bed) -> None:
    tuned = await api.set_settings(
        body=api.SetSettings(executions_per_minute=100_000), ctx=bed.ctx("automations:write")
    )
    ceiling = triggers.CEILINGS["tenantExecutionsPerMinute"]
    assert tuned["effective"]["executions_per_minute"] == ceiling
    # And what is STORED is the clamped value, so the console shows the truth rather than the ask.
    assert tuned["stored"]["executions_per_minute"] == ceiling


@pytest.mark.asyncio
async def test_an_unknown_timezone_is_refused(bed: Bed) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.set_settings(
            body=api.SetSettings(timezone="Mars/Olympus"), ctx=bed.ctx("automations:write")
        )
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------------------------
# Validation refusals
# ---------------------------------------------------------------------------------------------


def _issues(exc: HTTPException) -> list[str]:
    detail = exc.value.detail if hasattr(exc, "value") else exc.detail  # type: ignore[union-attr]
    assert isinstance(detail, dict)
    return list(detail["issues"])


@pytest.mark.asyncio
async def test_an_unknown_trigger_key_is_refused(bed: Bed) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.create_automation(
            body=api.CreateAutomation(
                name="Bad trigger", trigger_key="contact.abducted", action=bed.action()
            ),
            ctx=bed.ctx("automations:write"),
        )
    assert exc.value.status_code == 422
    assert any("abducted" in issue for issue in _issues(exc))


@pytest.mark.asyncio
async def test_broken_conditions_are_refused_with_the_WHOLE_issue_list(bed: Bed) -> None:
    """Every red field at once. An author fixing a rule should not have to re-submit four times to
    discover four problems, and the condition builder renders the issues per row."""
    with pytest.raises(HTTPException) as exc:
        await api.create_automation(
            body=api.CreateAutomation(
                name="Bad conditions",
                trigger_key=TRIGGER,
                action=bed.action(),
                conditions={
                    "logic": "and",
                    "conditions": [
                        {"field": "contact.invented", "operator": "equals", "value": "x"},
                        {"field": "contact.type", "operator": "teleports", "value": "x"},
                        {"field": "contact.tags", "operator": "in", "value": "not-a-list"},
                    ],
                },
            ),
            ctx=bed.ctx("automations:write"),
        )
    assert exc.value.status_code == 422
    issues = _issues(exc)
    assert len(issues) >= 3
    assert any("invented" in i for i in issues)
    assert any("teleports" in i for i in issues)


@pytest.mark.asyncio
async def test_both_targets_and_neither_target_are_both_refused(bed: Bed) -> None:
    write = bed.ctx("automations:write")
    with pytest.raises(HTTPException) as exc:
        await api.create_automation(
            body=api.CreateAutomation(
                name="Both", trigger_key=TRIGGER, flow_uid=bed.flow_uid, action=bed.action()
            ),
            ctx=write,
        )
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        await api.create_automation(
            body=api.CreateAutomation(name="Neither", trigger_key=TRIGGER), ctx=write
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_an_unpublished_journey_cannot_be_armed(bed: Bed) -> None:
    """Starting a case of a draft definition is the 3am failure this surface exists to prevent."""
    draft_uid = f"flwd_{bed.nonce}"
    await bed.session.execute(
        text(
            "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
            "VALUES (:u, :t, :c, 'Draft', 'draft')"
        ),
        {"u": draft_uid, "t": bed.tenant_id, "c": f"draft_{bed.nonce}"},
    )
    with pytest.raises(HTTPException) as exc:
        await api.create_automation(
            body=api.CreateAutomation(name="Draft", trigger_key=TRIGGER, flow_uid=draft_uid),
            ctx=bed.ctx("automations:write"),
        )
    assert exc.value.status_code == 422
    assert any("published" in i for i in _issues(exc))


@pytest.mark.asyncio
async def test_a_bad_cron_is_refused_and_a_good_one_is_stored(bed: Bed) -> None:
    write = bed.ctx("automations:write")
    with pytest.raises(HTTPException) as exc:
        await api.create_automation(
            body=api.CreateAutomation(
                name="Bad cron",
                trigger_key=SCHEDULED_TRIGGER,
                action=bed.action(),
                schedule=api.ScheduleSpec(cron="@daily"),
            ),
            ctx=write,
        )
    assert exc.value.status_code == 422
    row = await api.create_automation(
        body=api.CreateAutomation(
            name="Good cron",
            trigger_key=SCHEDULED_TRIGGER,
            action=bed.action(),
            schedule=api.ScheduleSpec(cron="0 9 * * 1-5", timezone="Europe/London"),
        ),
        ctx=write,
    )
    assert row["schedule"] == {"cron": "0 9 * * 1-5", "timezone": "Europe/London"}


@pytest.mark.asyncio
async def test_the_rule_cap_is_enforced(bed: Bed, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cap-at-2 rather than seeding 200 rows: the cap value itself is pinned against the contract
    in its own test, so what needs proving here is that the count gate fires."""
    monkeypatch.setattr(api, "MAX_RULES_PER_TENANT", 2)
    await bed.arm("One")
    await bed.arm("Two")
    with pytest.raises(HTTPException) as exc:
        await bed.arm("Three")
    assert exc.value.status_code == 422
    assert any("most allowed is 2" in i for i in _issues(exc))


@pytest.mark.asyncio
async def test_a_duplicate_name_is_a_409(bed: Bed) -> None:
    await bed.arm("Chase the client")
    with pytest.raises(HTTPException) as exc:
        await bed.arm("Chase the client")
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_an_unsupported_trigger_stores_but_is_flagged(bed: Bed) -> None:
    """A domain-sourced trigger is authorable and nothing emits it yet. Refusing it would blank
    half the builder's menu; accepting it silently would leave a firm believing a rule is armed."""
    row = await api.create_automation(
        body=api.CreateAutomation(
            name="Chain on completion", trigger_key=UNSUPPORTED_TRIGGER, action=bed.action()
        ),
        ctx=bed.ctx("automations:write"),
    )
    assert row["supported"] is False
    assert row["trigger_label"] == "A journey completes"


# ---------------------------------------------------------------------------------------------
# CRUD + ordering
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_listing_is_in_precedence_order_not_creation_order(bed: Bed) -> None:
    await bed.arm("Third", priority=300)
    await bed.arm("First", priority=100)
    await bed.arm("Second", priority=200)
    names = [r["name"] for r in await api.list_automations(ctx=bed.ctx("automations:read"))]
    assert names == ["First", "Second", "Third"]


@pytest.mark.asyncio
async def test_reorder_rewrites_precedence_and_demands_the_whole_set(bed: Bed) -> None:
    a = await bed.arm("A", priority=100)
    b = await bed.arm("B", priority=200)
    c = await bed.arm("C", priority=300)
    write = bed.ctx("automations:write")

    rows = await api.reorder_automations(
        body=api.ReorderAutomations(uids=[c["uid"], a["uid"], b["uid"]]), ctx=write
    )
    assert [r["name"] for r in rows] == ["C", "A", "B"]
    # Dense, spaced priorities so a future "insert between" has somewhere to go.
    assert [r["priority"] for r in rows] == [10, 20, 30]

    # A partial reorder has no honest answer for where the unnamed rows go.
    with pytest.raises(HTTPException) as exc:
        await api.reorder_automations(body=api.ReorderAutomations(uids=[a["uid"]]), ctx=write)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_enable_and_disable_flip_only_the_arming(bed: Bed) -> None:
    armed = await bed.arm("Toggle")
    write = bed.ctx("automations:write")
    off = await api.disable_automation(uid=str(armed["uid"]), ctx=write)
    assert off["enabled"] is False
    assert off["name"] == "Toggle"
    on = await api.enable_automation(uid=str(armed["uid"]), ctx=write)
    assert on["enabled"] is True


@pytest.mark.asyncio
async def test_patch_leaves_absent_fields_alone_and_can_switch_the_target(bed: Bed) -> None:
    armed = await bed.arm(
        "Switchable",
        conditions={"field": "contact.type", "operator": "equals", "value": "individual"},
    )
    uid = str(armed["uid"])
    write = bed.ctx("automations:write")

    renamed = await api.update_automation(
        uid=uid, body=api.UpdateAutomation(name="Renamed"), ctx=write
    )
    assert renamed["name"] == "Renamed"
    assert renamed["conditions"] == armed["conditions"]  # untouched
    assert renamed["action"]["type"] == "apply_tag"

    # action -> flow: the other side is cleared rather than tripping "not both", because the
    # alternative is deleting the rule and losing its run log to say the same thing.
    switched = await api.update_automation(
        uid=uid, body=api.UpdateAutomation(flow_uid=bed.flow_uid), ctx=write
    )
    assert switched["action"] is None
    assert switched["flow_uid"] == bed.flow_uid
    assert switched["flow_name"] == "Welcome"

    # …and back again
    back = await api.update_automation(
        uid=uid, body=api.UpdateAutomation(action=bed.action(tag="reverted")), ctx=write
    )
    assert back["flow_uid"] is None
    assert back["action"]["config"]["tag"] == "reverted"


@pytest.mark.asyncio
async def test_conditions_can_be_cleared_by_patching_them_to_null(bed: Bed) -> None:
    """`absent` and `set to null` have to be distinguishable, or an unconditional rule is
    unauthorable once it has ever had a condition."""
    armed = await bed.arm(
        "Clearable",
        conditions={"field": "contact.type", "operator": "equals", "value": "individual"},
    )
    cleared = await api.update_automation(
        uid=str(armed["uid"]),
        body=api.UpdateAutomation(conditions=None),
        ctx=bed.ctx("automations:write"),
    )
    assert cleared["conditions"] is None


@pytest.mark.asyncio
async def test_delete_removes_the_row(bed: Bed) -> None:
    armed = await bed.arm("Doomed")
    await api.delete_automation(uid=str(armed["uid"]), ctx=bed.ctx("automations:write"))
    assert await api.list_automations(ctx=bed.ctx("automations:read")) == []


# ---------------------------------------------------------------------------------------------
# Tenant scoping — the WHERE clauses ARE the boundary on this surface
# ---------------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def other_firm(service_session: AsyncSession) -> AsyncIterator[dict[str, Any]]:
    nonce = uuid.uuid4().hex[:8]
    tenant_id = await _tenant(service_session, nonce)
    await _grant_contacts_write(service_session, tenant_id)
    yield {"tenant_id": tenant_id, "nonce": nonce}


@pytest.mark.asyncio
async def test_another_firms_automation_is_invisible_and_untouchable(
    bed: Bed, other_firm: dict[str, Any]
) -> None:
    """``firm_session`` runs as ``service_role``, so RLS is NOT the backstop here — the tenant
    filter in every statement is. A cross-tenant uid must be indistinguishable from a nonexistent
    one (404, never 403: a 403 confirms the row exists)."""
    theirs = FirmContext(
        session=bed.session,
        tenant_id=int(other_firm["tenant_id"]),
        principal=Principal(
            user_id=f"wos_{other_firm['nonce']}",
            organisation_id=f"org_{other_firm['nonce']}",
            permissions=["automations:write"],
        ),
    )
    armed = await api.create_automation(
        body=api.CreateAutomation(
            name="Theirs",
            trigger_key=TRIGGER,
            action=api.ActionSpec(type="apply_tag", config={"tag": "theirs"}),
        ),
        ctx=theirs,
    )
    uid = str(armed["uid"])

    assert await api.list_automations(ctx=bed.ctx("automations:read")) == []
    for call in (
        lambda: api.get_automation(uid=uid, ctx=bed.ctx("automations:read")),
        lambda: api.list_deliveries(uid=uid, ctx=bed.ctx("automations:read")),
        lambda: api.update_automation(
            uid=uid, body=api.UpdateAutomation(name="Hijacked"), ctx=bed.ctx("automations:write")
        ),
        lambda: api.delete_automation(uid=uid, ctx=bed.ctx("automations:write")),
        lambda: api.enable_automation(uid=uid, ctx=bed.ctx("automations:write")),
        lambda: api.test_automation(
            uid=uid, body=api.TestAutomation(), ctx=bed.ctx("automations:write")
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 404

    # The other firm's rule is still there — nothing above half-applied.
    assert [r["name"] for r in await api.list_automations(ctx=theirs)] == ["Theirs"]


@pytest.mark.asyncio
async def test_a_flow_belonging_to_another_firm_cannot_be_armed(
    bed: Bed, other_firm: dict[str, Any]
) -> None:
    foreign_uid = f"flwx_{other_firm['nonce']}"
    await bed.session.execute(
        text(
            "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
            "VALUES (:u, :t, :c, 'Foreign', 'published')"
        ),
        {"u": foreign_uid, "t": other_firm["tenant_id"], "c": f"foreign_{other_firm['nonce']}"},
    )
    with pytest.raises(HTTPException) as exc:
        await api.create_automation(
            body=api.CreateAutomation(name="Foreign", trigger_key=TRIGGER, flow_uid=foreign_uid),
            ctx=bed.ctx("automations:write"),
        )
    assert exc.value.status_code == 422
    assert any("doesn’t exist" in i for i in _issues(exc))


@pytest.mark.asyncio
async def test_the_flow_target_picker_lists_only_this_firms_published_flows(
    bed: Bed, other_firm: dict[str, Any]
) -> None:
    await bed.session.execute(
        text(
            "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
            "VALUES (:u, :t, :c, 'Foreign', 'published'), (:u2, :t2, :c2, 'Draft', 'draft')"
        ),
        {
            "u": f"flwp_{other_firm['nonce']}",
            "t": other_firm["tenant_id"],
            "c": f"fp_{other_firm['nonce']}",
            "u2": f"flwq_{bed.nonce}",
            "t2": bed.tenant_id,
            "c2": f"fq_{bed.nonce}",
        },
    )
    options = await api.list_flow_targets(ctx=bed.ctx("automations:read"))
    assert [o["uid"] for o in options] == [bed.flow_uid]


# ---------------------------------------------------------------------------------------------
# The run log
# ---------------------------------------------------------------------------------------------


async def _delivery(bed: Bed, uid: str, *, outcome: str, detail: str) -> int:
    automation_id = int(
        (
            await bed.session.execute(
                text("SELECT id FROM flows.automations WHERE uid = :u"), {"u": uid}
            )
        ).scalar_one()
    )
    return int(
        (
            await bed.session.execute(
                text(
                    "INSERT INTO flows.automation_deliveries (automation_id, tenant_id, "
                    "  event_key, outcome, detail) VALUES (:a, :t, :k, :o, :d) RETURNING id"
                ),
                {
                    "a": automation_id,
                    "t": bed.tenant_id,
                    "k": f"action:{uuid.uuid4().hex}",
                    "o": outcome,
                    "d": detail,
                },
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_the_run_log_is_newest_first_and_keyset_paginates(bed: Bed) -> None:
    """Every CONSIDERED candidate lands a row, not only the ones that ran — this listing is the
    answer to "why didn't my automation fire?", which is the question the rule tier generates most.
    """
    armed = await bed.arm("Logged")
    uid = str(armed["uid"])
    ids = [
        await _delivery(bed, uid, outcome="skipped_conditions", detail="first"),
        await _delivery(bed, uid, outcome="dispatched", detail="second"),
        await _delivery(bed, uid, outcome="deferred", detail="third"),
    ]
    read = bed.ctx("automations:read")

    page = await api.list_deliveries(uid=uid, ctx=read, limit=2)
    assert [r["id"] for r in page["items"]] == [ids[2], ids[1]]
    assert page["has_more"] is True
    assert page["next_before"] == ids[1]
    assert page["items"][0]["outcome"] == "deferred"

    tail = await api.list_deliveries(uid=uid, ctx=read, limit=2, before=int(page["next_before"]))
    assert [r["id"] for r in tail["items"]] == [ids[0]]
    assert tail["has_more"] is False


@pytest.mark.asyncio
async def test_the_listing_carries_the_last_firing(bed: Bed) -> None:
    armed = await bed.arm("Last fired")
    uid = str(armed["uid"])
    await _delivery(bed, uid, outcome="skipped_rearm", detail="older")
    await _delivery(bed, uid, outcome="dispatched", detail="newest")
    row = next(r for r in await api.list_automations(ctx=bed.ctx("automations:read")))
    assert row["last_outcome"] == "dispatched"
    assert row["last_detail"] == "newest"
    assert row["last_fired_at"] is not None


# ---------------------------------------------------------------------------------------------
# THE DRY RUN — it must be impossible for it to fire anything
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_dry_run_performs_nothing(bed: Bed, recorder: _Recorder) -> None:
    """The safety property the whole feature rests on. A recording target is armed as the `action`
    dispatch kind for the duration of this test; the dry run must not reach it, must not write a
    delivery, and must say so on the wire (`performed: false`)."""
    armed = await bed.arm("Dry")
    result = await api.test_automation(
        uid=str(armed["uid"]),
        body=api.TestAutomation(subject_id=bed.contact_id),
        ctx=bed.ctx("automations:write"),
    )
    assert result["performed"] is False
    assert recorder.calls == []
    count = (
        await bed.session.execute(
            text("SELECT count(*) FROM flows.automation_deliveries WHERE tenant_id = :t"),
            {"t": bed.tenant_id},
        )
    ).scalar_one()
    assert count == 0
    # …and no case was opened either.
    cases = (
        await bed.session.execute(
            text("SELECT count(*) FROM flows.cases WHERE tenant_id = :t"), {"t": bed.tenant_id}
        )
    ).scalar_one()
    assert cases == 0


@pytest.mark.asyncio
async def test_the_dry_run_reports_which_condition_failed(bed: Bed) -> None:
    """A yes/no verdict is the wrong granularity for an author staring at three rows. The
    per-leaf explanation is evaluated by the SAME evaluator as the whole tree, so the two can
    never disagree."""
    armed = await bed.arm(
        "Explain",
        conditions={
            "logic": "and",
            "conditions": [
                {"field": "contact.type", "operator": "equals", "value": "individual"},
                {"field": "contact.nationality", "operator": "contains", "value": "FR"},
            ],
        },
    )
    result = await api.test_automation(
        uid=str(armed["uid"]),
        body=api.TestAutomation(subject_id=bed.contact_id),
        ctx=bed.ctx("automations:write"),
    )
    assert result["matched"] is False
    assert result["would_fire"] is False
    assert result["subject"] == {"kind": "contact", "id": bed.contact_id, "found": True}
    leaves = result["explanation"]["conditions"]
    assert [leaf["passed"] for leaf in leaves] == [True, False]
    assert leaves[1]["field"] == "contact.nationality"
    assert leaves[1]["actual"] == ["GB"]  # the real value, so the author sees WHY


@pytest.mark.asyncio
async def test_the_dry_run_matches_when_the_conditions_hold(bed: Bed) -> None:
    armed = await bed.arm(
        "Matcher",
        conditions={"field": "contact.service_type", "operator": "equals", "value": "advised"},
    )
    result = await api.test_automation(
        uid=str(armed["uid"]),
        body=api.TestAutomation(subject_id=bed.contact_id),
        ctx=bed.ctx("automations:write"),
    )
    assert result["matched"] is True
    assert result["would_fire"] is True
    assert result["blockers"] == []
    assert result["target"] == {
        "kind": "action",
        "action": {"type": "apply_tag", "config": {"tag": "chased"}},
    }


@pytest.mark.asyncio
async def test_the_dry_run_names_the_gates_that_would_stop_it(bed: Bed) -> None:
    """Matching is not firing. A disabled rule, a paused firm and a trigger nothing emits are all
    reasons a matching rule stays still — and each is a different fix."""
    armed = await bed.arm("Blocked")
    uid = str(armed["uid"])
    await api.disable_automation(uid=uid, ctx=bed.ctx("automations:write"))
    await api.set_settings(body=api.SetSettings(paused=True), ctx=bed.ctx("automations:pause"))

    result = await api.test_automation(
        uid=uid,
        body=api.TestAutomation(subject_id=bed.contact_id),
        ctx=bed.ctx("automations:write"),
    )
    assert result["matched"] is True  # unconditional
    assert result["would_fire"] is False
    assert any("switched off" in b for b in result["blockers"])
    assert any("paused" in b for b in result["blockers"])


@pytest.mark.asyncio
async def test_the_dry_run_says_when_the_subject_does_not_exist(bed: Bed) -> None:
    """Unknown fields resolve to None, so "no client found" would otherwise be indistinguishable
    from "the client didn't match" — the two need different fixes."""
    armed = await bed.arm("Missing subject")
    result = await api.test_automation(
        uid=str(armed["uid"]),
        body=api.TestAutomation(subject_id=999_999_999),
        ctx=bed.ctx("automations:write"),
    )
    assert result["subject"]["found"] is False
    assert any("No contact with that id" in b for b in result["blockers"])


@pytest.mark.asyncio
async def test_the_dry_run_can_test_the_UNSAVED_draft(bed: Bed) -> None:
    """Testing something other than what is on screen is worse than not testing at all, so the
    sheet may override the conditions it is about to save."""
    armed = await bed.arm(
        "Draft test",
        conditions={"field": "contact.nationality", "operator": "contains", "value": "FR"},
    )
    result = await api.test_automation(
        uid=str(armed["uid"]),
        body=api.TestAutomation(
            subject_id=bed.contact_id,
            override_conditions=True,
            conditions={"field": "contact.nationality", "operator": "contains", "value": "GB"},
        ),
        ctx=bed.ctx("automations:write"),
    )
    assert result["matched"] is True
    # …and the override was NOT persisted.
    saved = await api.get_automation(uid=str(armed["uid"]), ctx=bed.ctx("automations:read"))
    assert saved["conditions"]["value"] == "FR"


@pytest.mark.asyncio
async def test_the_dry_run_refuses_a_broken_draft_condition(bed: Bed) -> None:
    armed = await bed.arm("Broken draft")
    with pytest.raises(HTTPException) as exc:
        await api.test_automation(
            uid=str(armed["uid"]),
            body=api.TestAutomation(
                subject_id=bed.contact_id,
                override_conditions=True,
                conditions={"field": "contact.type", "operator": "teleports", "value": "x"},
            ),
            ctx=bed.ctx("automations:write"),
        )
    assert exc.value.status_code == 422


def test_explain_walks_nested_groups() -> None:
    """Pure: the explanation mirrors the authored structure so the builder can line each verdict
    up with the row that produced it."""
    condition = {
        "logic": "or",
        "conditions": [
            {"field": "a", "operator": "equals", "value": 1},
            {
                "logic": "and",
                "conditions": [{"field": "b", "operator": "isEmpty"}],
            },
        ],
    }
    out = api.explain(condition, {"a": 2, "b": None})
    assert out["kind"] == "group"
    assert out["logic"] == "or"
    assert out["passed"] is True
    assert out["conditions"][0] == {
        "kind": "leaf",
        "field": "a",
        "operator": "equals",
        "value": 1,
        "actual": 2,
        "passed": False,
    }
    assert out["conditions"][1]["kind"] == "group"
    assert out["conditions"][1]["conditions"][0]["passed"] is True


def test_explain_treats_an_absent_condition_as_always() -> None:
    assert api.explain(None, {}) == {"kind": "always", "passed": True}


@pytest.mark.asyncio
async def test_an_action_no_role_in_the_firm_could_perform_is_refused(
    service_session: AsyncSession,
) -> None:
    """The executor's publish preflight, reached through this surface.

    A firm that has granted `contacts:write` to nobody has decided something, and an automation
    that tags clients would be routing around that decision. Refusing it at AUTHORING is the whole
    philosophy of this surface — the person who can fix it is looking at the screen right now.
    """
    nonce = uuid.uuid4().hex[:8]
    tenant_id = await _tenant(service_session, nonce)  # deliberately no roles at all
    ctx = FirmContext(
        session=service_session,
        tenant_id=tenant_id,
        principal=Principal(
            user_id=f"wos_{nonce}",
            organisation_id=f"org_{nonce}",
            permissions=["automations:write"],
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await api.create_automation(
            body=api.CreateAutomation(
                name="Ungranted",
                trigger_key=TRIGGER,
                action=api.ActionSpec(type="apply_tag", config={"tag": "x"}),
            ),
            ctx=ctx,
        )
    assert exc.value.status_code == 422
    assert any("contacts:write" in issue for issue in _issues(exc))


# ---------------------------------------------------------------------------------------------
# The per-flow trigger cap, enforced HERE
# ---------------------------------------------------------------------------------------------
# `app/flows/rule_caps.py` owns the rule and its reasoning; these pin that the router actually
# asks it. The cap had been checked in the builder only, which is a courtesy rather than
# enforcement — so the test that matters is the one that goes through the API.


@pytest.mark.asyncio
async def test_a_fifth_rule_cannot_be_pointed_at_one_journey(bed: Bed) -> None:
    cap = int(triggers.CAPS["maxTriggersPerFlow"])
    for i in range(cap):
        await api.create_automation(
            body=api.CreateAutomation(
                name=f"Start {i}", trigger_key=TRIGGER, flow_uid=bed.flow_uid
            ),
            ctx=bed.ctx("automations:write"),
        )
    with pytest.raises(HTTPException) as exc:
        await api.create_automation(
            body=api.CreateAutomation(
                name="One too many", trigger_key=TRIGGER, flow_uid=bed.flow_uid
            ),
            ctx=bed.ctx("automations:write"),
        )
    assert exc.value.status_code == 422
    assert any(str(cap) in i for i in _issues(exc))


@pytest.mark.asyncio
async def test_the_last_rule_on_a_journey_can_still_be_edited(bed: Bed) -> None:
    """The classic way an edit path inherits a create path's counting bug: a rule that already
    targets the flow counting ITSELF towards the cap, so the fourth one can never be renamed."""
    cap = int(triggers.CAPS["maxTriggersPerFlow"])
    last = {}
    for i in range(cap):
        last = await api.create_automation(
            body=api.CreateAutomation(
                name=f"Start {i}", trigger_key=TRIGGER, flow_uid=bed.flow_uid
            ),
            ctx=bed.ctx("automations:write"),
        )
    renamed = await api.update_automation(
        uid=str(last["uid"]),
        body=api.UpdateAutomation(name="Renamed at the cap"),
        ctx=bed.ctx("automations:write"),
    )
    assert renamed["name"] == "Renamed at the cap"


@pytest.mark.asyncio
async def test_an_action_rule_is_not_counted_against_a_journeys_cap(bed: Bed) -> None:
    """The cap counts ways a JOURNEY can be started, so action rules are irrelevant to it."""
    for i in range(int(triggers.CAPS["maxTriggersPerFlow"]) + 3):
        await api.create_automation(
            body=api.CreateAutomation(name=f"Tag {i}", trigger_key=TRIGGER, action=bed.action()),
            ctx=bed.ctx("automations:write"),
        )
    armed = await api.create_automation(
        body=api.CreateAutomation(name="Start it", trigger_key=TRIGGER, flow_uid=bed.flow_uid),
        ctx=bed.ctx("automations:write"),
    )
    assert armed["flow_uid"] == bed.flow_uid


@pytest.mark.asyncio
async def test_settings_tell_a_firm_when_the_whole_platform_is_paused(bed: Bed) -> None:
    """A firm may not set the estate-wide switch and is not told why it is on — but a settings
    page reporting "running" while nothing runs is worse than either."""
    before = await api.get_settings(ctx=bed.ctx("automations:read"))
    assert before["platform_paused"] is False

    # The switch is a SINGLETON, so unlike everything else in this bed it cannot be nonce-scoped —
    # it is estate-wide by definition. Put it back, or every later test in the session runs against
    # a paused platform (which is exactly how this test first broke its neighbours).
    try:
        await platform_pause.set_paused(bed.session, paused=True, reason="incident", actor_id=None)
        after = await api.get_settings(ctx=bed.ctx("automations:read"))
        assert after["platform_paused"] is True
        # The reason is Graphene's incident detail, not the firm's business.
        assert "incident" not in json.dumps(after, default=str)
    finally:
        await platform_pause.set_paused(bed.session, paused=False, reason=None, actor_id=None)
