"""Stored rows and the read views the host app renders.

``*Row`` mirrors a table and is what the store returns. ``*View`` is the immutable projection
the engine hands a body and the app serialises. ``RunDetail`` is the recursive DAG the detail
page draws.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from baton.states import State, StateType


@dataclass(frozen=True, slots=True)
class RunRow:
    id: int
    uid: str
    tenant_id: int
    action_key: str
    idempotency_key: str
    kind: str
    leaf: bool | None
    params: dict[str, Any]
    result: Any
    meta: dict[str, Any]
    parent_run_id: int | None
    root_run_id: int | None
    depth: int
    key: str | None
    position: int | None
    after_keys: list[str]
    requested_by: str | None
    requested_at: datetime
    contact_id: int | None
    subject_kind: str | None
    subject_uid: str | None
    state: State
    scheduled_at: datetime | None
    lease_token: str | None
    lease_owner: str | None
    lease_until: datetime | None
    timeout_at: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    passes: int
    attempt_count: int
    wait_started_at: datetime | None
    error: str | None
    case_no: int | None
    justification: str | None
    approver_role_ids: list[int]
    gate_roles: list[int] | None
    gate_message: str | None
    decision: str | None
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    on_failure: str | None


@dataclass(frozen=True, slots=True)
class AttemptRow:
    id: int
    run_id: int
    root_run_id: int
    tenant_id: int
    attempt_no: int
    worker: str | None
    started_at: datetime
    ended_at: datetime | None
    duration_ms: int | None
    outcome: str
    error: str | None
    error_type: str | None
    method: str | None
    url: str | None
    status_code: int | None
    request: Any
    response: Any


@dataclass(frozen=True, slots=True)
class EventRow:
    id: int
    tenant_id: int
    run_id: int
    root_run_id: int
    event_type: str
    actor_id: str | None
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SubjectRow:
    kind: str
    uid: str
    entity_id: int | None
    blocking: bool


# ---- views -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunView:
    uid: str
    tenant_id: int
    action_key: str
    kind: str
    params: dict[str, Any]
    result: Any
    meta: dict[str, Any]
    state: State
    key: str | None
    position: int | None
    parent_uid: str | None
    root_uid: str | None
    depth: int
    requested_by: str | None
    requested_at: datetime
    contact_id: int | None
    subject_kind: str | None
    subject_uid: str | None
    case_no: int | None
    justification: str | None
    approver_role_ids: list[int]
    decision: str | None
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    attempt_count: int
    started_at: datetime | None
    ended_at: datetime | None
    # The scheduling and lineage facts a screen needs to say "polling again in 9 s", "waited 2m of
    # a 30m budget" or "best effort". Defaulted so the view stays constructible from older rows.
    scheduled_at: datetime | None = None
    wait_started_at: datetime | None = None
    passes: int = 0
    leaf: bool | None = None
    on_failure: str | None = None
    after_keys: list[str] = field(default_factory=list)
    gate_roles: list[int] | None = None
    gate_message: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AttemptView:
    attempt_no: int
    outcome: str
    started_at: datetime
    ended_at: datetime | None
    duration_ms: int | None
    error: str | None
    error_type: str | None
    method: str | None
    url: str | None
    status_code: int | None
    request: Any
    response: Any


@dataclass(frozen=True, slots=True)
class RunDetail:
    run: RunView
    after_keys: list[str]
    attempts: list[AttemptView]
    children: list[RunDetail] = field(default_factory=list)
    events: list[EventRow] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StartResult:
    run: RunView
    created: bool


@dataclass(frozen=True, slots=True)
class AdvanceResult:
    run: RunView
    outcome: str  # completed | failed | suspended | budget | busy | noop
    error: BaseException | None = None
    last_child: RunView | None = None


def _state(row_type: str, row_name: str, message: str | None) -> State:
    return State(StateType(row_type), row_name, message)
