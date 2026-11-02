"""Pure derivation of a case's and an execution's state from facts (no DB — unit-testable).

The orchestrator persists steps and cases; *what state they should be in* is decided here, so the
rules are testable in isolation. Two levels:

- ``derive_case_state`` — a case's state from its steps + whether its entry gate is open.
- ``derive_execution_state`` — the execution (group) state from its child cases.

Both obey the generic machine (``app/flows/states.py``): terminal states are latched; a case whose
gate is closed is ``blocked``; otherwise the state follows the "worst" outstanding step.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.flows.domain_events import StepStatus
from app.flows.states import TERMINAL, State


@dataclass(frozen=True)
class StepView:
    """The slice of a step the derivation needs."""

    key: str
    required: bool
    status: StepStatus


@dataclass(frozen=True)
class CaseView:
    """The slice of a case the execution-level derivation needs."""

    state: State
    required: bool = True  # an optional case doesn't block the execution completing


def _relevant(steps: list[StepView]) -> list[StepView]:
    """Required steps that still count — skipped/cancelled steps never block completion."""
    return [
        s
        for s in steps
        if s.required and s.status not in (StepStatus.SKIPPED, StepStatus.CANCELLED)
    ]


def derive_case_state(current: State, steps: list[StepView], *, gate_open: bool = True) -> State:
    """Derive a case's state from its steps. Rules, in order:

    1. terminal ⇒ unchanged (latched);
    2. gate closed ⇒ ``blocked`` (e.g. an account case awaiting its participants' KYC);
    3. all required steps fulfilled ⇒ ``completed``;
    4. a required step failed ⇒ ``blocked`` (awaits remediation/compensation — never silently done);
    5. a required step waiting ⇒ ``waiting``; a required step blocked ⇒ ``blocked``;
    6. anything fulfilled ⇒ ``active``; else ``draft``.
    """
    if current in TERMINAL:
        return current
    if not gate_open:
        return State.BLOCKED
    required = _relevant(steps)
    if required and all(s.status == StepStatus.FULFILLED for s in required):
        return State.COMPLETED
    if any(s.status == StepStatus.FAILED for s in required):
        return State.BLOCKED
    if any(s.status == StepStatus.WAITING for s in required):
        return State.WAITING
    if any(s.status == StepStatus.BLOCKED for s in required):
        return State.BLOCKED
    if any(s.status == StepStatus.FULFILLED for s in steps):
        return State.ACTIVE
    return State.DRAFT


def derive_execution_state(current: State, cases: list[CaseView]) -> State:
    """Derive the execution (group) state from its child cases — the same worst-outstanding logic a
    level up. All required child cases completed ⇒ the execution completes; a failed child blocks;
    waiting/blocked children propagate; else active while any child has progressed."""
    if current in TERMINAL:
        return current
    required = [c for c in cases if c.required and c.state != State.CANCELLED]
    if required and all(c.state == State.COMPLETED for c in required):
        return State.COMPLETED
    if any(c.state == State.FAILED for c in required):
        return State.BLOCKED
    if any(c.state == State.WAITING for c in required):
        return State.WAITING
    if any(c.state == State.BLOCKED for c in required):
        return State.BLOCKED
    if any(c.state in (State.ACTIVE, State.COMPLETED) for c in cases):
        return State.ACTIVE
    return State.DRAFT
