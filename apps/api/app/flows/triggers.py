"""The trigger registry — the curated projection of our event stream that automations arm against.

Two jobs, deliberately in one small module because they are the same contract seen from two sides:

1. **The registry.** ``TRIGGERS`` is the Python mirror of ``flow-contract.json``'s ``triggers``
   map (plus the ``safety``/``caps``/``permissions`` blocks the engine enforces). Authors pick a
   friendly key — ``client.document_uploaded`` — and the consumer resolves it to the real event
   type it must match on ``audit.actions``. The indirection is Crisp's most important product
   decision, and it is also an operational one: when a route is renamed, the registry entry is
   re-pointed and every firm's armed automations keep working, because their rows store the KEY.

2. **The dispatch seam.** ``AutomationTarget`` + ``TARGETS`` is how a firing turns into work. This
   wave registers one target (``"flow"`` — start a case through the existing engine); the rule
   tier registers ``"action"`` against the same protocol without touching the consumer loop.

**The API container ships without pkgs/ui**, so Python cannot read the contract JSON at runtime —
the literals below are the mirror, and ``tests/test_flow_contract.py`` pins them to the JSON so
the two cannot drift. This is exactly the ``app/branding.py::default_pages`` precedent; change the
JSON and this file together, never one.

**The tripwire that makes the registry durable** lives in the same test module: every
``source="action"`` entry must resolve to a LIVE route's event type, enumerated off the FastAPI
app the way ``tests/test_actions_tripwire.py`` does. Rename an endpoint without re-pointing the
registry and the suite fails loudly — rather than every firm's automations silently disarming,
which is the failure mode that has no symptom until a client doesn't get chased.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------------------------
# The registry (mirror of flow-contract.json → triggers)
# ---------------------------------------------------------------------------------------------

#: Where a trigger's signal comes from, and therefore which part of the worker serves it.
#: ``action``   — a row on ``audit.actions`` (the cursor-tailed stream; 0041).
#: ``domain``   — a ``flows.domain_events`` fact. Registered so the builder can offer it, but see
#:                ``PENDING_DOMAIN_EVENTS`` below: the engine does not emit these names yet.
#: ``schedule`` — no event at all; the worker's schedule pass evaluates it in the firm's timezone.
SOURCES: frozenset[str] = frozenset({"action", "domain", "schedule"})

#: What a case started from this trigger is ABOUT. Drives subject resolution (which contact /
#: account / case the firing names) and therefore the re-arm window's identity.
SUBJECT_KINDS: frozenset[str] = frozenset({"contact", "account", "application", "task", "case"})


@dataclass(frozen=True)
class TriggerSpec:
    """One entry in the curated menu."""

    key: str
    label: str
    source: str
    #: The real event type the consumer matches. ``None`` for ``source="schedule"``.
    event: str | None
    subject: str


def _t(key: str, label: str, source: str, event: str | None, subject: str) -> TriggerSpec:
    return TriggerSpec(key=key, label=label, source=source, event=event, subject=subject)


TRIGGERS: dict[str, TriggerSpec] = {
    t.key: t
    for t in (
        # -- action-sourced: 'action.' + the route's OpenAPI operation id ('{tag}-{route name}',
        # main.py::custom_generate_unique_id). Every one of these is tripwire-pinned to a live
        # route; all fifteen were verified against the route table when this landed.
        _t(
            "client.document_uploaded",
            "A client uploads a document",
            "action",
            "document_request.item_fulfilled",
            "contact",
        ),
        _t(
            "client.form_submitted",
            "A client submits a form",
            "action",
            "document_request.form_submitted",
            "contact",
        ),
        _t(
            "document.generated",
            "A document is generated",
            "action",
            "document.generated",
            "contact",
        ),
        _t(
            "document.sent",
            "A document is sent to a client",
            "action",
            "document.sent",
            "contact",
        ),
        _t(
            "document.requested",
            "A document is requested from a client",
            "action",
            "document_request.created",
            "contact",
        ),
        _t(
            "contact.created",
            "A client record is created",
            "action",
            "client.created",
            "contact",
        ),
        _t(
            "contact.login_linked",
            "A client login is linked",
            "action",
            "client.portal_user_linked",
            "contact",
        ),
        _t(
            "contact.adviser_assigned",
            "An adviser is assigned",
            "action",
            "client.adviser_assigned",
            "contact",
        ),
        _t(
            "contact.bank_details_changed",
            "Bank details change",
            "action",
            "client.bank_account_updated",
            "contact",
        ),
        _t(
            "account.model_changed",
            "An account's model changes",
            "action",
            "account.model_updated",
            "account",
        ),
        _t(
            "instruction.created",
            "An instruction is raised",
            "action",
            "instruction.created",
            "contact",
        ),
        _t(
            "instruction.cancelled",
            "An instruction is cancelled",
            "action",
            "instruction.cancelled",
            "contact",
        ),
        _t("task.created", "A task is raised", "action", "task.created", "task"),
        _t(
            "task.completed",
            "A task is completed",
            "action",
            "task.completed",
            "task",
        ),
        _t(
            "application.started",
            "An application starts",
            "action",
            "journey.started",
            "application",
        ),
        # -- domain-sourced: facts on flows.domain_events / the flows outbox.
        _t("case.completed", "A journey completes", "domain", "flows.case.completed", "case"),
        _t(
            "case.step_completed",
            "A journey step completes",
            "domain",
            "flows.case.step_completed",
            "case",
        ),
        _t(
            "screening.completed",
            "A screening result arrives",
            "domain",
            "aml.screen.completed",
            "contact",
        ),
        _t("tag.applied", "A tag is applied", "domain", "tags.applied", "contact"),
        # -- schedule-sourced: no event; the worker's schedule pass drives it.
        _t("schedule.recurring", "On a schedule", "schedule", None, "contact"),
    )
}

#: Reverse map: event type → the trigger keys that match it. A list because two friendly triggers
#: may legitimately project the same underlying event with different subjects later.
TRIGGERS_BY_EVENT: dict[str, tuple[str, ...]] = {}
for _spec in TRIGGERS.values():
    if _spec.event:
        TRIGGERS_BY_EVENT[_spec.event] = (*TRIGGERS_BY_EVENT.get(_spec.event, ()), _spec.key)

#: Sources the consumer can actually serve TODAY. A ``domain``-sourced automation is storable and
#: authorable, but nothing fires it yet — the engine's fact log currently speaks
#: ``execution.started``/``execution.cancelled``/``step.*``, not the contract's ``flows.case.*``
#: names, and ``tags.applied`` has no producer at all (tags arrive with the rule tier). Rather
#: than quietly accept an automation that can never fire, the consumer records the reason and
#: ``tests/test_flow_contract.py`` pins this set — so the day Phase B emits those facts, the test
#: is what reminds us to widen it.
SUPPORTED_SOURCES: frozenset[str] = frozenset({"action", "schedule"})

#: The domain events the contract promises but nothing emits yet, each with the wave that will.
#: Pinned by test so an entry cannot be forgotten once its producer lands.
PENDING_DOMAIN_EVENTS: dict[str, str] = {
    "flows.case.completed": "Phase B — the orchestrator's case-completion fact",
    "flows.case.step_completed": "Phase B — the orchestrator's step-completion fact",
    "aml.screen.completed": "Phase B — app/aml/screening.py's result callback",
    # PRODUCED (Phase A rule tier): app/tags.py::apply appends this fact in the same transaction
    # as the tag row, so the producer half is done. Still listed because the entry means "this
    # trigger cannot fire yet", and the missing half is now the CONSUMER: `domain` is not in
    # SUPPORTED_SOURCES, so nothing reads flows.domain_events looking for automations to fire.
    # Phase B's domain-event pass is what ticks this off — at which point the fact is already
    # there waiting for it.
    "tags.applied": "Phase B — the domain-event consumer pass (the fact itself is emitted now)",
}

# ---------------------------------------------------------------------------------------------
# safety / caps (mirror of flow-contract.json → safety, caps)
# ---------------------------------------------------------------------------------------------

#: The contract's DEFAULTS. Per-tenant overrides live on ``flows.automation_settings`` and are
#: clamped by ``CEILINGS`` — an override may tune a guard, never disarm one.
SAFETY: dict[str, Any] = {
    "automationEventsTriggerByDefault": False,
    "maxChainDepth": 5,
    "caseTriggerBudget": 50,
    "defaultRearmMinutes": 1440,
    "tenantExecutionsPerMinute": 60,
    "flowConcurrency": 20,
    "lookupTimeoutMs": 10000,
    # The AI gates' bounds (``app/flows/ai.py``). Not tunable per tenant: what a firm gets to
    # decide is WHETHER it may use them at all, and that is an operator's row in
    # ``flows.ai_tenants`` — see the contract's ``$comment_ai``.
    "aiTimeoutMs": 10000,
    "aiMaxLabels": 12,
    "aiMaxInputFields": 6,
    "aiMaxInputChars": 6000,
    "aiUnknownLabel": "unknown",
}

CAPS: dict[str, int] = {
    "maxNodesPerFlow": 200,
    "maxBranchesPerNode": 8,
    "maxTriggersPerFlow": 4,
    "maxRulesPerTenant": 200,
    "maxWaitDays": 90,
    "maxConditionsPerGroup": 20,
}

#: Hard ceilings on the per-tenant overrides. NOT in the contract, and deliberately so: the
#: contract states the defaults a firm may tune, this states how far. A settings row is written
#: through a permissioned endpoint, but "permissioned" is not "trusted to set 1,000,000
#: executions a minute" — a guard whose bound is caller-supplied is not a guard.
CEILINGS: dict[str, int] = {
    "maxChainDepth": 10,
    "caseTriggerBudget": 500,
    "tenantExecutionsPerMinute": 600,
    "flowConcurrency": 200,
}

#: The permission slugs this programme registers, mirrored for the pin test. The declarations
#: themselves live in app/permissions.py (the single source of truth for RBAC).
PERMISSIONS: dict[str, str] = {
    "automations:read": "platform",
    "automations:write": "platform",
    "automations:pause": "platform",
    "automations:global_pause": "operations",
}


def resolve(trigger_key: str) -> TriggerSpec | None:
    """The spec for an armed automation's key, or ``None`` if the key is not in the registry.

    Returns rather than raises: an unknown key is a stored row referring to a trigger this
    deployment does not have (a rollback, a hand-edited row), and the consumer must record that
    and carry on rather than wedge the loop for every other tenant.
    """
    return TRIGGERS.get(trigger_key)


def keys_for_event(event_type: str) -> tuple[str, ...]:
    """The trigger keys an ``audit.actions`` event type can fire. Empty for the overwhelming
    majority of events — most of the stream is not on the curated menu, which is the point."""
    return TRIGGERS_BY_EVENT.get(event_type, ())


def supported(spec: TriggerSpec) -> bool:
    """Can the consumer actually fire this trigger today? See ``SUPPORTED_SOURCES``."""
    return spec.source in SUPPORTED_SOURCES


# ---------------------------------------------------------------------------------------------
# Listener deliverability (mirror of flow-contract.json → listeners)
# ---------------------------------------------------------------------------------------------
#
# ``supported`` answers the RULE tier's question — can this trigger arm an automation. A Flows v3
# ``wait_for_event`` node asks a different and strictly narrower one, and the answer belongs here
# rather than in each validator, because publish, the arm path and the builder must give the same
# answer or they drift apart again. See ``flow-contract.json → $comment_listeners``.

#: Sources whose signal can reach a case parked on a ``wait_for_event`` node. Only the
#: ``audit.actions`` consumer releases listeners (``automation.handle_event`` →
#: ``timers.release_listeners``), so only ``action`` qualifies. ``schedule`` has ``event = None``,
#: so no event type can ever resolve to it and ``schedule_once`` never touches listeners at all;
#: ``domain`` has no consumer yet (``SUPPORTED_SOURCES``) and joins this set the day one lands.
LISTENER_SOURCES: frozenset[str] = frozenset({"action"})

#: Subject spaces a running case can be scoped to when it arms a listener — the branches
#: ``timers._case_subject`` can honestly answer. ``task`` is deliberately absent: a case is about a
#: contact or an account (and an application through its execution), never a task, so a listener
#: for a task trigger has no id to scope to. See the contract's ``$comment_listeners``.
LISTENER_SUBJECTS: frozenset[str] = frozenset({"contact", "account", "application", "case"})


def listener_refusal(spec: TriggerSpec) -> str | None:
    """Why a ``wait_for_event`` node cannot wait for this trigger, or ``None`` if it can.

    A sentence fragment, phrased to complete "…waits for X, which …" — publish quotes it verbatim
    and the arm path logs it, so the author and the operator read the same reason.
    """
    if spec.source not in LISTENER_SOURCES:
        if spec.source == "schedule":
            return (
                "is a schedule rather than an event, so nothing would ever arrive to move the "
                "journey on"
            )
        return "is not an event this engine delivers yet, so the journey would wait for ever"
    if spec.subject not in LISTENER_SUBJECTS:
        return (
            f"is about a {spec.subject}, and a journey's case is never about a {spec.subject} — "
            f"there is no {spec.subject} of its own for the wait to watch"
        )
    return None


def listenable(trigger_key: str) -> bool:
    """Can a ``wait_for_event`` node on ``trigger_key`` ever be released? Unknown keys are ``False``
    — an unknown trigger is refused separately, with its own (better) message."""
    spec = TRIGGERS.get(trigger_key)
    return spec is not None and listener_refusal(spec) is None


# ---------------------------------------------------------------------------------------------
# The dispatch seam
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AutomationContext:
    """Everything a dispatch target is told about one firing.

    Frozen and dataclass-shaped rather than a bare dict so a target cannot quietly grow a
    dependency on a key the consumer never promised. ``facts`` is the SAME mapping the entry
    conditions were evaluated against (``app/flows/conditions.py``'s flat dotted-path context), so
    a target that wants to interpolate the event into its own config reads it from there instead
    of re-querying.
    """

    tenant_id: int
    automation_id: int
    automation_uid: str
    automation_name: str
    trigger_key: str
    #: The originating ``audit.actions.id``; ``None`` on the scheduled path.
    event_id: int | None
    event_type: str | None
    subject_kind: str | None
    subject_id: int | None
    #: The case the firing happened inside, when the event names one — the identity the per-case
    #: trigger budget is counted over.
    case_uid: str | None
    #: 0 for a human-sourced event; +1 per automation link in the chain (capped).
    chain_depth: int
    #: The acting ``auth.users`` id on the originating event, when there was a human.
    actor_id: str | None
    #: Target-specific parameters authored on the automation (``flows.automations.config``).
    config: Mapping[str, Any] = field(default_factory=dict)
    facts: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DispatchResult:
    """What a target reports back. A target that cannot do its job returns ``ok=False`` with a
    reason (recorded as a ``failed`` delivery) rather than raising — an exception is reserved for
    genuinely unexpected faults, which the loop catches and dead-letters the same way."""

    ok: bool
    detail: str | None = None
    #: The case this firing created or acted on, if any — recorded on the delivery so the per-case
    #: trigger budget can count the chain that follows.
    case_uid: str | None = None


class AutomationTarget(Protocol):
    """The one thing a dispatch target must be.

    ``session`` is a ``service_role`` session inside the worker's transaction for THIS firing (a
    savepoint, so a target that raises rolls back only its own work). ``spec`` is the target's own
    payload: the ``action`` object for the rule tier, or ``{"flow_id": …}`` for a flow start.
    """

    async def run(
        self, session: AsyncSession, ctx: AutomationContext, spec: Mapping[str, Any]
    ) -> DispatchResult: ...


#: kind → target. ``"flow"`` is registered by ``app/flows/automation.py``; ``"action"`` is the
#: rule-tier executor's seam — register it at import of that module and the consumer picks it up
#: with no change here.
TARGETS: dict[str, AutomationTarget] = {}


def register_target(kind: str, target: AutomationTarget) -> None:
    """Register a dispatch target. Idempotent re-registration (module re-import under test)
    overwrites, deliberately: the last definition of a kind wins, and there is only ever one."""
    TARGETS[kind] = target


def target_for(kind: str) -> AutomationTarget | None:
    return TARGETS.get(kind)
