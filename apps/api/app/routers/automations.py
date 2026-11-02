"""The rule tier's authoring surface — ``/automations``.

Flows v3 splits the engine into two tiers. The **flow builder** authors a journey graph; the
**rule tier** arms single "when X, do Y" automations. This router is the rule tier's whole API:
list, create, edit, order, arm/disarm, the tenant kill switch, the run log, and a dry run.

Three things drive every design decision here.

**One order, so "which one won" has an answer.** ``flows.automations`` is ONE table with a
nullable ``flow_id`` and a nullable ``action`` and a CHECK that exactly one is set, because the
consumer's contract is first-satisfiable-wins per (event, tenant). That means ordering is a
first-class operation, not a display preference — hence ``POST /automations/reorder`` and a UI
that drags rows. The endpoint rewrites priorities as a dense sequence; ``(priority, id)`` is the
total order the consumer reads.

**Refuse at authoring, never at 3am.** Everything the consumer would have to skip over is checked
on the write: the trigger key against the registry, the conditions against A2's
``condition_issues`` (the same function the flow publisher uses, so an author learns one set of
messages), exactly one target, the action's type and config keys against the contract, the cron
against ``app/cron.py``'s parser, the per-tenant rule cap, and — when the sibling executor has
landed — its own permission preflight. A rule that could never fire is an authoring bug, and an
authoring bug should be a red field in a sheet.

**The dry run performs nothing.** ``POST /automations/{uid}/test`` resolves a real subject, builds
the SAME fact context the consumer builds (``automation.build_facts``), evaluates the conditions
leaf by leaf and reports what *would* happen. It never touches ``triggers.TARGETS``, never writes a
delivery, and never opens a case. It is the feature that makes an automation authorable with
confidence, and it is only worth anything if it is impossible for it to fire — so it is pinned by a
test that registers a recording target and asserts it was never called.

Scoping is ``firm_session``: the tenant comes from the caller's verified identity, never from the
wire, and every statement here forces ``tenant_id``. That matters more than usual because the
session runs as ``service_role`` — ``flows.automation_deliveries`` has no ``authenticated`` grant
at all (0043), so RLS is not the backstop on this surface, the WHERE clauses are.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import cron
from app.auth import require_permission
from app.db import FirmContext
from app.db import target_firm_session as firm_session
from app.flows import automation, platform_pause, rule_caps, triggers
from app.flows import conditions as conditions_mod
from app.ids import new_id
from app.listing import DEFAULT_LIMIT
from app.permissions import AUTOMATIONS_PAUSE, AUTOMATIONS_READ, AUTOMATIONS_WRITE

router = APIRouter(prefix="/automations", tags=["automations"])

# ---------------------------------------------------------------------------------------------
# The action-type mirror
# ---------------------------------------------------------------------------------------------
# The contract's `actionTypes` block, mirrored for write validation the same way
# app/flows/triggers.py mirrors `triggers` — the API container ships without pkgs/ui, so Python
# cannot read the JSON at runtime, and tests/test_automations_api.py pins this to the JSON.
#
# The sibling wave (A3) owns the EXECUTOR; this is only the authoring vocabulary, and the two are
# deliberately separable: an action type is storable the moment the contract lists it, and the
# executor's own `preflight_action` (imported below when it exists) adds the checks that need to
# know how it runs. Config keys are validated as a closed set per type because a typo'd key
# ("assignee" for "assign_to") is otherwise a rule that runs and quietly does the wrong thing.
ACTION_CONFIG_KEYS: dict[str, frozenset[str]] = {
    "apply_tag": frozenset({"tag", "target"}),
    "remove_tag": frozenset({"tag", "target"}),
    "raise_task": frozenset(
        {
            "title",
            "description",
            "assign_to",
            "assignee_role_slug",
            "category",
            "priority",
            "due_in_days",
        }
    ),
    "send_email": frozenset({"template_code", "to", "variables"}),
    "assign_adviser": frozenset({"adviser_id", "role"}),
    "set_case_status": frozenset({"status"}),
    "request_document": frozenset({"category", "title", "target_contact"}),
    "generate_document": frozenset({"template_uid", "dataset"}),
    "send_document": frozenset({"document_uid"}),
    "webhook_out": frozenset({"endpoint_key", "payload_keys"}),
    "lookup": frozenset({"endpoint_key", "request_mapping", "response_mapping"}),
    "classify": frozenset({"input_fields", "question", "labels", "remember_as"}),
    "draft_message": frozenset(
        {"input_fields", "brief", "title", "assignee_role_slug", "due_in_days"}
    ),
}

#: The rule cap, from the contract (``caps.maxRulesPerTenant``). Counted over ALL rows, enabled or
#: not: a disabled rule still occupies an author's mental model and a row in the order.
MAX_RULES_PER_TENANT = triggers.CAPS["maxRulesPerTenant"]

#: Bounds on the authored numbers. Not in the contract — the contract states the defaults a firm
#: may tune, these state what the *column* will accept, which is a different question (a guard
#: whose bound is caller-supplied is not a guard, the ``triggers.CEILINGS`` reasoning).
MAX_REARM_MINUTES = 60 * 24 * 366  # a year: longer is indistinguishable from "never re-fire"
MAX_PRIORITY = 10_000
MAX_NAME_LENGTH = 120

#: Cursor page size for the run log — the platform-wide listing default (``listing.DEFAULT_LIMIT``).
DELIVERIES_PAGE = DEFAULT_LIMIT
MAX_DELIVERIES_PAGE = 200


# ---------------------------------------------------------------------------------------------
# The sibling executor's preflight (A3)
# ---------------------------------------------------------------------------------------------


def _executor_preflight() -> Any | None:
    """The action executor's preflight callable, or ``None`` when this build has none.

    ``effects.preflight(session, *, tenant_id, action) -> str | None`` — the refusal reason, or
    None when the action could actually run. The import stays guarded (rather than a module-level
    import) so a deployment without the executor degrades to shape-only validation instead of 500ing
    every write on this surface; ``_preflight_available`` then tells the console it checked shape
    only, rather than letting it imply an action was verified when it wasn't.
    """
    try:
        from app.flows import effects
    except ImportError:
        return None
    return effects.preflight


async def _executor_issues(
    session: AsyncSession, tenant_id: int, action: Mapping[str, Any]
) -> list[str]:
    """The executor's own publish preflight, as an issue list.

    It answers the question this module cannot: does the tenant hold a role carrying the permission
    the action's human equivalent needs, and is the thing the action names (a template, a status)
    actually usable? An action that could never run must fail at AUTHORING, not at 3am in a
    client's journey (flow-contract.json, ``$comment_actionTypes``).
    """
    preflight = _executor_preflight()
    if preflight is None:
        return []
    result = await preflight(session, tenant_id=tenant_id, action=dict(action))
    # One refusal reason, or none — normalised to this surface's issue-list contract so the
    # condition builder renders executor refusals beside its own shape errors.
    return [] if result is None else [str(result)]


def _preflight_available() -> bool:
    """Whether the executor's preflight ran. Rides out on the settings payload so the console can
    be honest that an action was checked for SHAPE only when it hasn't."""
    return _executor_preflight() is not None


# ---------------------------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------------------------

Name = Annotated[str, Field(min_length=1, max_length=MAX_NAME_LENGTH)]


class ActionSpec(BaseModel):
    """``{type, config}`` — the contract's action shape, exactly as it is stored."""

    type: str
    config: dict[str, Any] = {}


class ScheduleSpec(BaseModel):
    """``{cron, timezone}``. The timezone is optional: absent means the firm's own
    (``flows.automation_settings.timezone``), which is what an author almost always means."""

    cron: str
    timezone: str | None = None


class CreateAutomation(BaseModel):
    name: Name
    trigger_key: str
    conditions: dict[str, Any] | None = None
    #: Exactly one of these two. Enforced in Python (a clearer 422 than the CHECK's 500) AND by
    #: ``automations_target_check`` in the database.
    flow_uid: str | None = None
    action: ActionSpec | None = None
    config: dict[str, Any] = {}
    priority: int = Field(default=100, ge=0, le=MAX_PRIORITY)
    enabled: bool = True
    rearm_minutes: int | None = Field(default=None, ge=0, le=MAX_REARM_MINUTES)
    schedule: ScheduleSpec | None = None
    allow_automation_source: bool = False


class UpdateAutomation(BaseModel):
    """PATCH semantics: an absent field is left alone.

    ``conditions``/``rearm_minutes``/``schedule`` are genuinely nullable, so "absent" and "set to
    null" have to be distinguishable — hence ``model_fields_set`` rather than a ``None`` sentinel.
    Switching the target (flow ⇄ action) is allowed and clears the other side, because the
    alternative is deleting a rule and losing its run log to say the same thing.
    """

    name: Name | None = None
    trigger_key: str | None = None
    conditions: dict[str, Any] | None = None
    flow_uid: str | None = None
    action: ActionSpec | None = None
    config: dict[str, Any] | None = None
    priority: int | None = Field(default=None, ge=0, le=MAX_PRIORITY)
    enabled: bool | None = None
    rearm_minutes: int | None = Field(default=None, ge=0, le=MAX_REARM_MINUTES)
    schedule: ScheduleSpec | None = None
    allow_automation_source: bool | None = None


class ReorderAutomations(BaseModel):
    """The uids in their new precedence order, first fires first."""

    uids: list[str] = Field(min_length=1)


class SetSettings(BaseModel):
    """The tenant's automation settings. Two permissions land on one endpoint deliberately — see
    ``set_settings``: the pause fields need ``automations:pause``, everything else needs
    ``automations:write``, and a body that touches both needs both."""

    paused: bool | None = None
    paused_reason: str | None = Field(default=None, max_length=500)
    timezone: str | None = None
    executions_per_minute: int | None = Field(default=None, ge=1)
    flow_concurrency: int | None = Field(default=None, ge=1)
    case_trigger_budget: int | None = Field(default=None, ge=1)
    max_chain_depth: int | None = Field(default=None, ge=0)
    default_rearm_minutes: int | None = Field(default=None, ge=0, le=MAX_REARM_MINUTES)


class TestAutomation(BaseModel):
    """A dry run against one subject.

    ``conditions``/``trigger_key`` may be OVERRIDDEN so the create/edit sheet can test the draft
    the author is looking at rather than the last saved version — testing something other than
    what is on screen is worse than not testing at all.
    """

    subject_id: int | None = None
    conditions: dict[str, Any] | None = None
    trigger_key: str | None = None
    #: Explicit because ``conditions: null`` is a legitimate override meaning "unconditional".
    override_conditions: bool = False


# ---------------------------------------------------------------------------------------------
# Validation (pure where it can be — unit-tested by import)
# ---------------------------------------------------------------------------------------------


def _issues_response(issues: Sequence[str]) -> HTTPException:
    """A 422 carrying the whole issue list, not just the first.

    The flow publisher joins its issues into one string; this surface returns them structured
    because the condition builder shows them per row. ``message`` keeps the endpoint readable to
    anything that only renders ``detail``.
    """
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"message": "This automation isn’t ready to arm.", "issues": list(issues)},
    )


def trigger_issues(trigger_key: str) -> list[str]:
    """Refuse an unknown trigger key. A key outside the registry is a row the consumer would skip
    forever, so it never gets stored."""
    if triggers.resolve(trigger_key) is None:
        return [f"“{trigger_key}” isn’t a trigger the engine knows."]
    return []


def action_issues(action: Mapping[str, Any] | None) -> list[str]:
    """Shape-check an action against the contract's ``actionTypes``: known type, known config keys.

    Values are NOT type-checked here — what a `raise_task`'s `due_in_days` must look like is the
    executor's business, and duplicating it would give an author two sources of truth for one
    error message.
    """
    if action is None:
        return []
    kind = str(action.get("type") or "")
    if kind not in ACTION_CONFIG_KEYS:
        return [f"“{kind or '—'}” isn’t an action the engine can perform."]
    config = action.get("config") or {}
    if not isinstance(config, Mapping):
        return [f"The “{kind}” action’s settings must be an object."]
    unknown = sorted(set(map(str, config.keys())) - ACTION_CONFIG_KEYS[kind])
    if unknown:
        return [f"The “{kind}” action doesn’t take {', '.join(f'“{k}”' for k in unknown)}."]
    return []


def target_issues(*, flow_uid: str | None, action: Mapping[str, Any] | None) -> list[str]:
    """Exactly one target. Both is ambiguous, neither is inert — and the database's CHECK would
    turn either into a 500, so it is caught here with a sentence an author can act on."""
    if flow_uid and action:
        return ["An automation either starts a journey or performs an action — not both."]
    if not flow_uid and not action:
        return ["Choose what this automation does: start a journey, or perform an action."]
    return []


def schedule_issues(
    schedule: Mapping[str, Any] | None, *, spec: triggers.TriggerSpec | None
) -> list[str]:
    """A schedule is required by (and only accepted on) a ``source="schedule"`` trigger, and its
    expression must parse with the SAME parser the worker will use — a builder that accepts
    ``@daily`` and a worker that returns ``None`` for it is a rule that silently never runs."""
    scheduled = spec is not None and spec.source == "schedule"
    if schedule is None:
        return ["A scheduled automation needs a schedule."] if scheduled else []
    if not scheduled:
        return ["Only a scheduled trigger takes a schedule."]
    issues: list[str] = []
    expression = str(schedule.get("cron") or "").strip()
    if not expression:
        issues.append("The schedule needs a time.")
    elif cron.parse(expression) is None:
        issues.append(
            f"“{expression}” isn’t a schedule we can read "
            "(minute hour day month weekday, with *, numbers, lists, a-b ranges and */n steps)."
        )
    zone_name = schedule.get("timezone")
    if zone_name is not None and not _valid_zone(str(zone_name)):
        issues.append(f"“{zone_name}” isn’t a timezone we know.")
    return issues


def _valid_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def clamp_overrides(values: Mapping[str, Any]) -> dict[str, Any]:
    """Clamp the per-tenant safety overrides to ``triggers.CEILINGS`` at WRITE time.

    ``automation.limits_for`` already clamps on read, so this is not what makes the guard safe —
    it is what makes the settings page honest. A firm that asks for 5,000 executions a minute and
    is shown 5,000 while the engine quietly uses 600 has been lied to by its own console.
    """
    out = dict(values)
    for field_name, ceiling_key in (
        ("executions_per_minute", "tenantExecutionsPerMinute"),
        ("flow_concurrency", "flowConcurrency"),
        ("case_trigger_budget", "caseTriggerBudget"),
        ("max_chain_depth", "maxChainDepth"),
    ):
        value = out.get(field_name)
        if isinstance(value, int) and not isinstance(value, bool):
            out[field_name] = min(value, triggers.CEILINGS[ceiling_key])
    return out


# ---------------------------------------------------------------------------------------------
# Row shaping
# ---------------------------------------------------------------------------------------------

#: One automation with its target resolved to something displayable and its last firing joined on.
#: The LATERAL is the "last-fired" column the listing shows; without it the table would need one
#: request per row to answer the first question anybody asks of a rule ("is it doing anything?").
_SELECT = """
SELECT a.uid, a.name, a.trigger_key, a.conditions, a.action, a.config,
       a.priority, a.enabled, a.rearm_minutes, a.schedule, a.allow_automation_source,
       a.created_at, a.updated_at,
       f.uid AS flow_uid, f.code AS flow_code, f.name AS flow_name, f.status AS flow_status,
       d.created_at AS last_fired_at, d.outcome AS last_outcome, d.detail AS last_detail
FROM flows.automations a
LEFT JOIN flows.flows f ON f.id = a.flow_id
LEFT JOIN LATERAL (
  SELECT created_at, outcome, detail FROM flows.automation_deliveries
  WHERE automation_id = a.id ORDER BY id DESC LIMIT 1
) d ON true
WHERE a.tenant_id = :t
"""


def _shape(row: Mapping[str, Any]) -> dict[str, Any]:
    """One wire row.

    ``supported`` is the honest bit. A ``domain``-sourced trigger is authorable today and nothing
    fires it yet (``triggers.SUPPORTED_SOURCES``) — refusing it at the API would block the
    builder's whole menu, and accepting it silently would leave a firm believing a rule is armed
    when it can never run. So it stores, and it carries a flag the listing badges.
    """
    spec = triggers.resolve(str(row["trigger_key"]))
    return {
        "uid": row["uid"],
        "name": row["name"],
        "trigger_key": row["trigger_key"],
        "trigger_label": spec.label if spec else None,
        "trigger_source": spec.source if spec else None,
        "subject_kind": spec.subject if spec else None,
        "supported": bool(spec and triggers.supported(spec)),
        "conditions": row["conditions"],
        "action": row["action"],
        "config": row["config"],
        "priority": row["priority"],
        "enabled": row["enabled"],
        "rearm_minutes": row["rearm_minutes"],
        "schedule": row["schedule"],
        "allow_automation_source": row["allow_automation_source"],
        "flow_uid": row["flow_uid"],
        "flow_code": row["flow_code"],
        "flow_name": row["flow_name"],
        "flow_status": row["flow_status"],
        "last_fired_at": row["last_fired_at"],
        "last_outcome": row["last_outcome"],
        "last_detail": row["last_detail"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def _row(session: AsyncSession, tenant_id: int, uid: str) -> dict[str, Any]:
    row = (
        (await session.execute(text(f"{_SELECT} AND a.uid = :u"), {"t": tenant_id, "u": uid}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Automation not found")
    return _shape(dict(row))


async def _internal_id(session: AsyncSession, tenant_id: int, uid: str) -> int:
    """The row's surrogate id, tenant-forced. 404 rather than 403 for another firm's uid — a
    cross-tenant uid must be indistinguishable from a nonexistent one."""
    row = (
        await session.execute(
            text("SELECT id FROM flows.automations WHERE uid = :u AND tenant_id = :t"),
            {"u": uid, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Automation not found")
    return int(row)


async def _flow_id_for(session: AsyncSession, tenant_id: int, flow_uid: str) -> int:
    """Resolve a flow target within the tenant.

    A DRAFT flow is refused: starting a case of an unpublished definition is exactly the 3am
    failure this surface exists to prevent, and "publish the journey first" is a sentence an
    author can act on. Archived likewise.
    """
    row = (
        (
            await session.execute(
                text("SELECT id, status FROM flows.flows WHERE uid = :u AND tenant_id = :t"),
                {"u": flow_uid, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise _issues_response(["That journey doesn’t exist."])
    if row["status"] != "published":
        raise _issues_response(
            [f"“{flow_uid}” isn’t published — publish the journey before arming it."]
        )
    return int(row["id"])


# ---------------------------------------------------------------------------------------------
# Listing + reading
# ---------------------------------------------------------------------------------------------


@router.get("", name="list-automations")
async def list_automations(ctx: FirmContext = Depends(firm_session)) -> list[dict[str, Any]]:
    """The firm's automations in PRECEDENCE order — the order the consumer evaluates them in, so
    the listing's row order is the product fact, not a sort preference."""
    require_permission(ctx.principal, AUTOMATIONS_READ)
    rows = (
        (
            await ctx.session.execute(
                text(f"{_SELECT} ORDER BY a.priority, a.id"), {"t": ctx.tenant_id}
            )
        )
        .mappings()
        .all()
    )
    return [_shape(dict(row)) for row in rows]


@router.get("/flows", name="list-automation-flow-targets")
async def list_flow_targets(ctx: FirmContext = Depends(firm_session)) -> list[dict[str, Any]]:
    """The published journeys an automation may start — the target picker's options.

    Gated on ``automations:read`` rather than ``flows:read`` on purpose: the two grants are
    separate (contract ``$comment_permissions``), and somebody arming rules should not need the
    flow AUTHORING grant to name a journey. Only the fields the picker renders are returned.
    """
    require_permission(ctx.principal, AUTOMATIONS_READ)
    rows = (
        (
            await ctx.session.execute(
                text(
                    "SELECT uid, code, name FROM flows.flows "
                    "WHERE tenant_id = :t AND status = 'published' ORDER BY name"
                ),
                {"t": ctx.tenant_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


@router.get("/settings", name="get-automation-settings")
async def get_settings(ctx: FirmContext = Depends(firm_session)) -> dict[str, Any]:
    """The tenant's kill switch, timezone and effective safety numbers.

    Both halves are returned: ``stored`` is what the firm authored (nulls where it is tracking the
    platform default) and ``effective`` is what the engine will actually use, resolved through
    ``automation.limits_for``. A settings page that shows only one of those either hides the
    clamping or hides the fact that a field is unset.
    """
    require_permission(ctx.principal, AUTOMATIONS_READ)
    return await _settings_payload(ctx)


async def _settings_payload(ctx: FirmContext) -> dict[str, Any]:
    """The settings body, ungated.

    Split out so ``set_settings`` can echo the new state back without re-gating on
    ``automations:read`` — a pause-only principal legitimately holds neither the read nor the write
    grant, and making the kill switch return 403 *after* successfully pausing the firm would be a
    lie about what happened.
    """
    stored = (
        (
            await ctx.session.execute(
                text(
                    "SELECT paused, paused_reason, timezone, executions_per_minute, "
                    "       flow_concurrency, case_trigger_budget, max_chain_depth, "
                    "       default_rearm_minutes, updated_at "
                    "FROM flows.automation_settings WHERE tenant_id = :t"
                ),
                {"t": ctx.tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    limits = await automation.limits_for(ctx.session, ctx.tenant_id)
    # The estate-wide switch. A firm may not set it and is not told WHY it is on — the reason is
    # Graphene's incident detail — but it must be told THAT it is on, because a settings page
    # reporting "running" while nothing runs is the worst of both. Read on this session, which is
    # already `service_role` (module header): 0048 gives the table no `authenticated` grant, and a
    # second transaction here would burn a connection per settings read AND read a different
    # snapshot than everything else on the page.
    platform_paused = (await platform_pause.read(ctx.session)).paused
    return {
        "stored": dict(stored) if stored else None,
        "paused": limits.paused,
        "platform_paused": platform_paused,
        "paused_reason": stored["paused_reason"] if stored else None,
        "timezone": limits.timezone,
        "effective": {
            "executions_per_minute": limits.executions_per_minute,
            "flow_concurrency": limits.flow_concurrency,
            "case_trigger_budget": limits.case_trigger_budget,
            "max_chain_depth": limits.max_chain_depth,
            "default_rearm_minutes": limits.default_rearm_minutes,
        },
        "defaults": dict(triggers.SAFETY),
        "ceilings": dict(triggers.CEILINGS),
        "max_rules": MAX_RULES_PER_TENANT,
        "preflight_available": _preflight_available(),
    }


@router.put("/settings", name="set-automation-settings")
async def set_settings(
    body: SetSettings, ctx: FirmContext = Depends(firm_session)
) -> dict[str, Any]:
    """Update the settings row (upsert — a tenant with no row is tracking every default).

    **Two gates on one endpoint, by what the body touches.** ``automations:pause`` is a narrower
    grant than ``automations:write`` on purpose: an operations lead should be able to hit the kill
    switch in an incident without also being able to re-author every rule, and a rule author
    should not silently acquire the switch. So the pause fields need ``pause``, the rest need
    ``write``, and a body carrying both needs both. (The GLOBAL switch is
    ``automations:global_pause``, an operations-portal slug — it belongs on the admin surface and
    is deliberately not built here.)
    """
    touched = body.model_fields_set
    if touched & {"paused", "paused_reason"}:
        require_permission(ctx.principal, AUTOMATIONS_PAUSE)
    if touched - {"paused", "paused_reason"}:
        require_permission(ctx.principal, AUTOMATIONS_WRITE)
    if not touched:
        require_permission(ctx.principal, AUTOMATIONS_READ)

    if body.timezone is not None and not _valid_zone(body.timezone):
        raise _issues_response([f"“{body.timezone}” isn’t a timezone we know."])

    values = clamp_overrides({key: getattr(body, key) for key in touched})
    # Upsert. The column list is built from what was touched so an untouched column keeps its
    # value — a PUT that reset every unmentioned override to NULL would make the pause switch a
    # settings-wipe, which is precisely the button somebody mashes in an incident.
    columns = sorted(values)
    if columns:
        assignments = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
        await ctx.session.execute(
            text(
                f"INSERT INTO flows.automation_settings "  # noqa: S608 - column names are ours
                f"  (tenant_id, updated_by, {', '.join(columns)}) "
                f"VALUES (:t, CAST(:u AS uuid), {', '.join(f':{c}' for c in columns)}) "
                f"ON CONFLICT (tenant_id) DO UPDATE SET {assignments}, "
                f"  updated_by = EXCLUDED.updated_by"
            ),
            {"t": ctx.tenant_id, "u": await _actor_id(ctx), **values},
        )
    return await _settings_payload(ctx)


async def _actor_id(ctx: FirmContext) -> str | None:
    """The acting user's ``auth.users`` id, for ``created_by``/``updated_by``.

    Resolved from the verified WorkOS subject rather than trusted from the wire; ``None`` when the
    principal is a system/service caller with no mirrored user row, which the columns allow.
    """
    return (
        await ctx.session.execute(
            text("SELECT id::text FROM auth.users WHERE workos_user_id = :w"),
            {"w": ctx.principal.user_id},
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------------------------


async def _validate(
    session: AsyncSession,
    tenant_id: int,
    *,
    trigger_key: str,
    conditions: Mapping[str, Any] | None,
    flow_uid: str | None,
    action: Mapping[str, Any] | None,
    schedule: Mapping[str, Any] | None,
    exclude_uid: str | None = None,
) -> None:
    """Everything a stored automation must satisfy, reported as ONE issue list.

    All of it, not the first failure: an author fixing a rule wants every red field at once, and
    the condition builder renders them per row.

    ``exclude_uid`` is the automation being edited, and only the cap checks need it — a rule that
    already targets a flow must not count itself towards that flow's cap, or the fourth one could
    never be edited again.
    """
    spec = triggers.resolve(trigger_key)
    issues = [
        *trigger_issues(trigger_key),
        *conditions_mod.condition_issues(conditions),
        *target_issues(flow_uid=flow_uid, action=action),
        *action_issues(action),
        *schedule_issues(schedule, spec=spec),
    ]
    if action is not None and not action_issues(action):
        issues.extend(await _executor_issues(session, tenant_id, action))
    # The one cap that needs a query. `TriggerPanel` counts on screen and stops offering a fifth,
    # which is a courtesy; this is the enforcement (app/flows/rule_caps.py explains the cap).
    cap_issue = await rule_caps.flow_trigger_cap_issue(
        session, tenant_id, flow_uid=flow_uid, exclude_uid=exclude_uid
    )
    if cap_issue:
        issues.append(cap_issue)
    if issues:
        raise _issues_response(issues)


@router.post("", name="create-automation", status_code=status.HTTP_201_CREATED)
async def create_automation(
    body: CreateAutomation, ctx: FirmContext = Depends(firm_session)
) -> dict[str, Any]:
    """Arm a new automation."""
    require_permission(ctx.principal, AUTOMATIONS_WRITE)
    count = int(
        (
            await ctx.session.execute(
                text("SELECT count(*) FROM flows.automations WHERE tenant_id = :t"),
                {"t": ctx.tenant_id},
            )
        ).scalar_one()
    )
    if count >= MAX_RULES_PER_TENANT:
        raise _issues_response(
            [
                f"This firm already has {count} automations — the most allowed is "
                f"{MAX_RULES_PER_TENANT}. Delete one first."
            ]
        )
    action = body.action.model_dump() if body.action else None
    schedule = body.schedule.model_dump(exclude_none=True) if body.schedule else None
    await _validate(
        ctx.session,
        ctx.tenant_id,
        trigger_key=body.trigger_key,
        conditions=body.conditions,
        flow_uid=body.flow_uid,
        action=action,
        schedule=schedule,
    )
    flow_id = (
        await _flow_id_for(ctx.session, ctx.tenant_id, body.flow_uid) if body.flow_uid else None
    )
    uid = new_id()
    try:
        await ctx.session.execute(
            text(
                "INSERT INTO flows.automations (uid, tenant_id, name, trigger_key, conditions, "
                "  flow_id, action, config, priority, enabled, rearm_minutes, schedule, "
                "  allow_automation_source, created_by) "
                "VALUES (:uid, :t, :name, :trigger, CAST(:conditions AS jsonb), :flow_id, "
                "  CAST(:action AS jsonb), CAST(:config AS jsonb), :priority, :enabled, "
                "  :rearm, CAST(:schedule AS jsonb), :allow, CAST(:by AS uuid))"
            ),
            {
                "uid": uid,
                "t": ctx.tenant_id,
                "name": body.name.strip(),
                "trigger": body.trigger_key,
                "conditions": json.dumps(body.conditions) if body.conditions else None,
                "flow_id": flow_id,
                "action": json.dumps(action) if action else None,
                "config": json.dumps(body.config or {}),
                "priority": body.priority,
                "enabled": body.enabled,
                "rearm": body.rearm_minutes,
                "schedule": json.dumps(schedule) if schedule else None,
                "allow": body.allow_automation_source,
                "by": await _actor_id(ctx),
            },
        )
        await ctx.session.flush()
    except IntegrityError as exc:
        raise _name_conflict(body.name) from exc
    return await _row(ctx.session, ctx.tenant_id, uid)


def _name_conflict(name: str) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail=f"An automation called “{name}” already exists. Names have to be unique.",
    )


@router.get("/{uid}", name="get-automation")
async def get_automation(uid: str, ctx: FirmContext = Depends(firm_session)) -> dict[str, Any]:
    require_permission(ctx.principal, AUTOMATIONS_READ)
    return await _row(ctx.session, ctx.tenant_id, uid)


@router.patch("/{uid}", name="update-automation")
async def update_automation(
    uid: str, body: UpdateAutomation, ctx: FirmContext = Depends(firm_session)
) -> dict[str, Any]:
    """Edit an automation. Absent fields are left alone; the whole row is re-validated."""
    require_permission(ctx.principal, AUTOMATIONS_WRITE)
    await _internal_id(ctx.session, ctx.tenant_id, uid)
    current = await _row(ctx.session, ctx.tenant_id, uid)
    touched = body.model_fields_set

    trigger_key = body.trigger_key if "trigger_key" in touched else str(current["trigger_key"])
    conditions = body.conditions if "conditions" in touched else current["conditions"]
    schedule_model = body.schedule.model_dump(exclude_none=True) if body.schedule else None
    schedule = schedule_model if "schedule" in touched else current["schedule"]

    # Switching target sides clears the other. Naming EITHER side counts as choosing it, so an
    # author who sets `action` on a flow-backed rule does not get "not both" thrown back at them.
    if "flow_uid" in touched or "action" in touched:
        flow_uid = body.flow_uid if "flow_uid" in touched else None
        action = body.action.model_dump() if ("action" in touched and body.action) else None
        if "flow_uid" in touched and body.flow_uid and "action" not in touched:
            action = None
        if "action" in touched and body.action and "flow_uid" not in touched:
            flow_uid = None
    else:
        flow_uid = current["flow_uid"]
        action = current["action"]

    await _validate(
        ctx.session,
        ctx.tenant_id,
        trigger_key=str(trigger_key),
        conditions=conditions,
        flow_uid=flow_uid,
        action=action,
        schedule=schedule,
        exclude_uid=uid,
    )
    flow_id = await _flow_id_for(ctx.session, ctx.tenant_id, flow_uid) if flow_uid else None

    params: dict[str, Any] = {
        "u": uid,
        "t": ctx.tenant_id,
        "name": (body.name or "").strip() if body.name is not None else None,
        "trigger": trigger_key,
        "conditions": json.dumps(conditions) if conditions else None,
        "flow_id": flow_id,
        "action": json.dumps(action) if action else None,
        "config": json.dumps(body.config if body.config is not None else current["config"]),
        "priority": body.priority,
        "enabled": body.enabled,
        "rearm": body.rearm_minutes,
        "schedule": json.dumps(schedule) if schedule else None,
        "allow": body.allow_automation_source,
    }
    sets = [
        "trigger_key = :trigger",
        "conditions = CAST(:conditions AS jsonb)",
        "flow_id = :flow_id",
        "action = CAST(:action AS jsonb)",
        "config = CAST(:config AS jsonb)",
        "schedule = CAST(:schedule AS jsonb)",
    ]
    if "name" in touched and body.name is not None:
        sets.append("name = :name")
    if "priority" in touched and body.priority is not None:
        sets.append("priority = :priority")
    if "enabled" in touched and body.enabled is not None:
        sets.append("enabled = :enabled")
    if "rearm_minutes" in touched:
        sets.append("rearm_minutes = :rearm")
    if "allow_automation_source" in touched and body.allow_automation_source is not None:
        sets.append("allow_automation_source = :allow")
    try:
        await ctx.session.execute(
            # The SET list is built from our own literals above; every VALUE is bound.
            text(
                f"UPDATE flows.automations SET {', '.join(sets)} "  # noqa: S608
                "WHERE uid = :u AND tenant_id = :t"
            ),
            params,
        )
        await ctx.session.flush()
    except IntegrityError as exc:
        raise _name_conflict(body.name or str(current["name"])) from exc
    return await _row(ctx.session, ctx.tenant_id, uid)


@router.delete("/{uid}", name="delete-automation", status_code=status.HTTP_204_NO_CONTENT)
async def delete_automation(uid: str, ctx: FirmContext = Depends(firm_session)) -> None:
    """Delete an automation (and, by cascade, its run log).

    A hard delete rather than a soft one: ``disabled`` already IS the soft state, and the audit
    trail keeps the before-image of the row (0043 puts ``flows.automations`` on ``audit.attach``),
    so "who deleted the rule that used to chase clients" is still answerable.
    """
    require_permission(ctx.principal, AUTOMATIONS_WRITE)
    await _internal_id(ctx.session, ctx.tenant_id, uid)
    await ctx.session.execute(
        text("DELETE FROM flows.automations WHERE uid = :u AND tenant_id = :t"),
        {"u": uid, "t": ctx.tenant_id},
    )


@router.post("/{uid}/enable", name="enable-automation")
async def enable_automation(uid: str, ctx: FirmContext = Depends(firm_session)) -> dict[str, Any]:
    return await _arm(uid, ctx, enabled=True)


@router.post("/{uid}/disable", name="disable-automation")
async def disable_automation(uid: str, ctx: FirmContext = Depends(firm_session)) -> dict[str, Any]:
    return await _arm(uid, ctx, enabled=False)


async def _arm(uid: str, ctx: FirmContext, *, enabled: bool) -> dict[str, Any]:
    """Arm/disarm. Its own endpoints rather than a PATCH so the toggle is one round trip that
    cannot accidentally carry the rest of an editing form with it — and so the audit trail reads
    "enabled this automation" rather than "updated it"."""
    require_permission(ctx.principal, AUTOMATIONS_WRITE)
    await _internal_id(ctx.session, ctx.tenant_id, uid)
    await ctx.session.execute(
        text("UPDATE flows.automations SET enabled = :e WHERE uid = :u AND tenant_id = :t"),
        {"e": enabled, "u": uid, "t": ctx.tenant_id},
    )
    return await _row(ctx.session, ctx.tenant_id, uid)


@router.post("/reorder", name="reorder-automations")
async def reorder_automations(
    body: ReorderAutomations, ctx: FirmContext = Depends(firm_session)
) -> list[dict[str, Any]]:
    """Rewrite precedence: the uids in their new order, first fires first.

    The whole set must be named. A partial reorder has no honest answer for where the unnamed rows
    go, and silently appending them is how a firm's most important rule ends up last. Priorities
    are rewritten densely (10, 20, 30…) rather than 1,2,3 so a future "insert between" has room.
    """
    require_permission(ctx.principal, AUTOMATIONS_WRITE)
    existing = [
        str(u)
        for u in (
            await ctx.session.execute(
                text(
                    "SELECT uid FROM flows.automations WHERE tenant_id = :t ORDER BY priority, id"
                ),
                {"t": ctx.tenant_id},
            )
        )
        .scalars()
        .all()
    ]
    if sorted(body.uids) != sorted(existing):
        raise _issues_response(
            [
                "The new order has to list every automation exactly once "
                f"({len(existing)} expected, {len(body.uids)} given)."
            ]
        )
    for index, uid in enumerate(body.uids):
        await ctx.session.execute(
            text("UPDATE flows.automations SET priority = :p WHERE uid = :u AND tenant_id = :t"),
            {"p": (index + 1) * 10, "u": uid, "t": ctx.tenant_id},
        )
    return await list_automations(ctx=ctx)


# ---------------------------------------------------------------------------------------------
# The run log
# ---------------------------------------------------------------------------------------------


@router.get("/{uid}/deliveries", name="list-automation-deliveries")
async def list_deliveries(
    uid: str,
    ctx: FirmContext = Depends(firm_session),
    before: Annotated[int | None, Query(description="Cursor: return rows with a lower id")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_DELIVERIES_PAGE)] = DELIVERIES_PAGE,
) -> dict[str, Any]:
    """One automation's run log, newest first, keyset-paginated on ``id``.

    Every CONSIDERED candidate lands a row, not only the ones that ran (0043) — so this is the
    answer to "why didn't my automation fire?", which is the question the rule tier generates most.
    Read on ``service_role`` (the table has no ``authenticated`` grant), tenant forced in the WHERE.
    """
    require_permission(ctx.principal, AUTOMATIONS_READ)
    automation_id = await _internal_id(ctx.session, ctx.tenant_id, uid)
    rows = (
        (
            await ctx.session.execute(
                text(
                    "SELECT id, event_id, event_key, subject_kind, subject_id, case_uid, "
                    "       outcome, detail, error, chain_depth, attempts, retry_after, "
                    "       created_at "
                    "FROM flows.automation_deliveries "
                    "WHERE automation_id = :a AND tenant_id = :t "
                    "  AND (CAST(:before AS bigint) IS NULL OR id < CAST(:before AS bigint)) "
                    "ORDER BY id DESC LIMIT :lim"
                ),
                {
                    "a": automation_id,
                    "t": ctx.tenant_id,
                    "before": before,
                    "lim": limit + 1,
                },
            )
        )
        .mappings()
        .all()
    )
    has_more = len(rows) > limit
    items = [dict(row) for row in rows[:limit]]
    return {
        "items": items,
        "has_more": has_more,
        "next_before": items[-1]["id"] if has_more and items else None,
    }


# ---------------------------------------------------------------------------------------------
# The dry run
# ---------------------------------------------------------------------------------------------


#: The per-leaf verdict tree the dry-run panel renders. It MOVED to ``conditions.explain`` when the
#: flow tier's simulator needed the identical shape for a branch's arms and a step's visibility —
#: one explanation implementation beside the one evaluator, rather than a second copy that could
#: disagree with it. Re-exported under the old name because this module's endpoint and its tests
#: both reach for ``automations.explain``.
explain = conditions_mod.explain


@router.post("/{uid}/test", name="test-automation")
async def test_automation(
    uid: str, body: TestAutomation, ctx: FirmContext = Depends(firm_session)
) -> dict[str, Any]:
    """**A DRY RUN. Nothing is performed, nothing is written, nothing is queued.**

    Given a subject (a client, an account — whatever the trigger is about), build the exact fact
    context the consumer would build for a real event and report whether the conditions hold, leaf
    by leaf, plus the other gates that would stand between the event and the target: the rule's own
    enabled flag and the tenant's pause switch.

    It reaches ``triggers.TARGETS`` never — there is no code path from here to a dispatch, and
    ``test_automations_api.py`` pins that by registering a recording target and asserting zero
    calls. That is the whole value of the feature: an author can point a rule at a real client and
    find out what it would do WITHOUT finding out the hard way.

    Gated on ``automations:write``: it is an authoring affordance (it reads a subject's facts to
    answer a question about a draft), and the read grant's job is the listing and the run log.
    """
    require_permission(ctx.principal, AUTOMATIONS_WRITE)
    row = await _row(ctx.session, ctx.tenant_id, uid)

    trigger_key = body.trigger_key or str(row["trigger_key"])
    spec = triggers.resolve(trigger_key)
    if spec is None:
        raise _issues_response(trigger_issues(trigger_key))
    condition = body.conditions if body.override_conditions else row["conditions"]
    issues = conditions_mod.condition_issues(condition)
    if issues:
        raise _issues_response(issues)

    limits = await automation.limits_for(ctx.session, ctx.tenant_id)
    now = datetime.now(UTC)
    facts = await automation.build_facts(
        ctx.session,
        spec=spec,
        event=None,
        tenant_id=ctx.tenant_id,
        subject_id=body.subject_id,
        tz=automation.zone(limits.timezone),
        now=now,
    )
    # Did the subject actually resolve? `build_facts` fills the subject's scope only when the row
    # exists in THIS tenant, so the presence of a scoped key is the honest test — and reporting it
    # matters, because unknown fields resolve to None and "no client found" would otherwise look
    # identical to "the client didn't match".
    scope = f"{spec.subject}."
    subject_found = any(key.startswith(scope) for key in facts)
    matched = conditions_mod.evaluate(condition, facts)

    blockers: list[str] = []
    if not row["enabled"]:
        blockers.append("This automation is switched off.")
    if limits.paused:
        blockers.append("All automations are paused for this firm.")
    if not row["supported"]:
        blockers.append("Nothing emits this trigger yet, so it can’t fire today.")
    if body.subject_id is not None and not subject_found:
        blockers.append(f"No {spec.subject} with that id in this firm.")

    return {
        "performed": False,  # structural: this endpoint has no path to a dispatch target
        "would_fire": bool(matched and not blockers),
        "matched": matched,
        "blockers": blockers,
        "subject": {
            "kind": spec.subject,
            "id": body.subject_id,
            "found": subject_found,
        },
        "trigger": {"key": trigger_key, "label": spec.label, "source": spec.source},
        "target": (
            {"kind": "flow", "flow_uid": row["flow_uid"], "name": row["flow_name"]}
            if row["flow_uid"]
            else {"kind": "action", "action": row["action"]}
        ),
        "explanation": explain(condition, facts),
        "facts": dict(facts),
        "evaluated_at": now,
    }
