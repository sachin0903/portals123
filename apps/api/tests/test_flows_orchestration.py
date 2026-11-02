"""Unit tests for the generic orchestration core — the state machine + fact-based derivation.

Pure logic, no DB (the DB wiring is covered by test_flows_api.py under Docker). These lock the
engine's neutral behaviour: legal transitions, terminal latching, and how case/execution state
follows the "worst outstanding" step/child.
"""

import pytest

from app.flows.derivation import (
    CaseView,
    StepView,
    derive_case_state,
    derive_execution_state,
)
from app.flows.domain_events import (
    SIGNAL_TO_STATUS,
    StepSignal,
    StepStatus,
)
from app.flows.states import IllegalTransition, State, can_transition, is_terminal, transition


# ---- state machine ----
def test_working_states_interchange() -> None:
    assert can_transition(State.DRAFT, State.ACTIVE)
    assert can_transition(State.ACTIVE, State.BLOCKED)
    assert can_transition(State.BLOCKED, State.WAITING)
    assert can_transition(State.WAITING, State.COMPLETED)


def test_terminal_states_are_latched() -> None:
    for term in (State.COMPLETED, State.CANCELLED, State.FAILED):
        assert is_terminal(term)
        assert not can_transition(term, State.ACTIVE)
        assert not can_transition(term, State.DRAFT)
        # same-state is always a legal no-op
        assert can_transition(term, term)
        assert transition(term, term) == term


def test_illegal_transition_raises() -> None:
    with pytest.raises(IllegalTransition):
        transition(State.COMPLETED, State.ACTIVE)


def test_signal_status_mapping() -> None:
    assert SIGNAL_TO_STATUS[StepSignal.COMPLETED] == StepStatus.FULFILLED
    assert SIGNAL_TO_STATUS[StepSignal.BLOCKED] == StepStatus.BLOCKED
    assert SIGNAL_TO_STATUS[StepSignal.WAITING] == StepStatus.WAITING
    assert SIGNAL_TO_STATUS[StepSignal.FAILED] == StepStatus.FAILED


# ---- case-state derivation ----
def _s(status: StepStatus, required: bool = True, key: str = "x") -> StepView:
    return StepView(key=key, required=required, status=status)


def test_case_gate_closed_is_blocked() -> None:
    # An account case whose participants' KYC hasn't cleared: gate closed ⇒ blocked, regardless of
    # its own steps.
    steps = [_s(StepStatus.PENDING)]
    assert derive_case_state(State.DRAFT, steps, gate_open=False) == State.BLOCKED


def test_case_all_required_fulfilled_completes() -> None:
    steps = [_s(StepStatus.FULFILLED, key="a"), _s(StepStatus.FULFILLED, key="b")]
    assert derive_case_state(State.ACTIVE, steps) == State.COMPLETED


def test_case_optional_pending_still_completes() -> None:
    steps = [_s(StepStatus.FULFILLED, key="a"), _s(StepStatus.PENDING, required=False, key="b")]
    assert derive_case_state(State.ACTIVE, steps) == State.COMPLETED


def test_case_failed_step_blocks() -> None:
    steps = [_s(StepStatus.FULFILLED, key="a"), _s(StepStatus.FAILED, key="b")]
    assert derive_case_state(State.ACTIVE, steps) == State.BLOCKED


def test_case_waiting_and_blocked_propagate() -> None:
    assert derive_case_state(State.ACTIVE, [_s(StepStatus.WAITING)]) == State.WAITING
    assert derive_case_state(State.ACTIVE, [_s(StepStatus.BLOCKED)]) == State.BLOCKED


def test_case_progress_is_active_else_draft() -> None:
    assert (
        derive_case_state(
            State.DRAFT, [_s(StepStatus.FULFILLED, key="a"), _s(StepStatus.PENDING, key="b")]
        )
        == State.ACTIVE
    )
    assert derive_case_state(State.DRAFT, [_s(StepStatus.PENDING)]) == State.DRAFT


def test_case_terminal_latched() -> None:
    # Even with all steps fulfilled, a cancelled case stays cancelled.
    assert derive_case_state(State.CANCELLED, [_s(StepStatus.FULFILLED)]) == State.CANCELLED


def test_skipped_and_cancelled_steps_dont_block() -> None:
    steps = [
        _s(StepStatus.FULFILLED, key="a"),
        _s(StepStatus.SKIPPED, key="b"),
        _s(StepStatus.CANCELLED, key="c"),
    ]
    assert derive_case_state(State.ACTIVE, steps) == State.COMPLETED


# ---- execution-state derivation ----
def test_execution_completes_when_all_required_cases_complete() -> None:
    cases = [CaseView(State.COMPLETED), CaseView(State.COMPLETED)]
    assert derive_execution_state(State.ACTIVE, cases) == State.COMPLETED


def test_execution_blocks_on_failed_child() -> None:
    cases = [CaseView(State.COMPLETED), CaseView(State.FAILED)]
    assert derive_execution_state(State.ACTIVE, cases) == State.BLOCKED


def test_execution_waiting_child_propagates() -> None:
    cases = [CaseView(State.COMPLETED), CaseView(State.WAITING)]
    assert derive_execution_state(State.ACTIVE, cases) == State.WAITING


def test_execution_optional_case_doesnt_block() -> None:
    cases = [CaseView(State.COMPLETED), CaseView(State.ACTIVE, required=False)]
    assert derive_execution_state(State.ACTIVE, cases) == State.COMPLETED


def test_execution_terminal_latched() -> None:
    assert derive_execution_state(State.COMPLETED, [CaseView(State.ACTIVE)]) == State.COMPLETED
