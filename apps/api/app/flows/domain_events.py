"""Domain events — the internal spine of the orchestrator.

A ``StepHandler`` never mutates orchestration state or calls recompute directly; it returns a
``StepEvent`` describing what happened to a step, and the orchestrator consumes it: update the step,
re-derive the case + execution state, append an immutable fact to ``flows.domain_events``, and
enqueue any external notifications to ``flows.outbox``. This keeps handlers ignorant of
orchestration and makes a run replayable from its fact log (docs/flows.md §0, §4).

Two separate concerns (see the plan, §4):
- **domain_events** — immutable facts (this module's ``StepEvent`` + the appended log rows).
- **outbox** — *delivery* state (attempts/next_retry/dead_letter), never mixed into the facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class StepSignal(StrEnum):
    """The outcome a ``StepHandler`` reports for a step instance."""

    COMPLETED = "step.completed"
    BLOCKED = "step.blocked"  # gated — cannot proceed yet (a dependency/gate is closed)
    WAITING = "step.waiting"  # kicked off, awaiting an external signal (webhook / manual review)
    FAILED = "step.failed"  # a hard failure (e.g. provider returned a decline) — may compensate


class StepStatus(StrEnum):
    """Per-step persisted status. A superset of the original ``pending/fulfilled/skipped/cancelled``
    with ``blocked/waiting/failed`` for the generic model. ``skipped`` = visibility/dedup elided it;
    ``cancelled`` = the case was cancelled."""

    PENDING = "pending"
    FULFILLED = "fulfilled"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    WAITING = "waiting"
    FAILED = "failed"


#: How a handler's signal maps onto the step's persisted status.
SIGNAL_TO_STATUS: dict[StepSignal, StepStatus] = {
    StepSignal.COMPLETED: StepStatus.FULFILLED,
    StepSignal.BLOCKED: StepStatus.BLOCKED,
    StepSignal.WAITING: StepStatus.WAITING,
    StepSignal.FAILED: StepStatus.FAILED,
}


@dataclass(frozen=True)
class StepEvent:
    """Emitted by a handler; consumed by the orchestrator. ``participant_subject_id`` identifies the
    fan-out instance (which person) for participant-scoped steps; None for single-instance steps."""

    signal: StepSignal
    case_id: int
    step_key: str
    participant_subject_id: int | None = None
    reason: str | None = None  # e.g. "already_verified", "aml_decline"
    detail: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class OutboxIntent:
    """A request to deliver an external notification, emitted alongside a state transition. The
    orchestrator writes it to ``flows.outbox`` (delivery state lives there, not on the fact log).
    ``idempotency_key`` dedupes redelivery (``tenant_id, event_type, idempotency_key`` unique)."""

    event_type: str  # e.g. "application.completed", "applicant.aml.failed"
    idempotency_key: str
    payload: dict[str, object] = field(default_factory=dict)
