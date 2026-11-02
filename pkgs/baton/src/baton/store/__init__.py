"""Persistence seam. The engine speaks only to a ``Store``; two implement it.

``PostgresStore`` is the real one (schema-parameterised, ``FOR UPDATE SKIP LOCKED`` claims, a
fencing token on every holder write). ``MemoryStore`` is a dict-backed fake with a ``FakeClock``
for unit-testing the replay algorithm without a database.

A ``Store`` hands out a ``Unit`` of work (a transaction). The engine does all its reads and
writes through the unit, and the store commits on a clean exit and rolls back on an exception.
``begin_session`` on the host's :class:`~baton.hooks.Hooks` runs first inside every unit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from baton.model import AttemptRow, EventRow, RunRow, SubjectRow
from baton.states import State


@dataclass(frozen=True, slots=True)
class NewRun:
    uid: str
    tenant_id: int
    action_key: str
    idempotency_key: str
    params: dict[str, Any]
    params_hash: str | None
    meta: dict[str, Any]
    state: State
    kind: str = "action"
    scheduled_at: datetime | None = None
    requested_by: str | None = None
    contact_id: int | None = None
    subject_kind: str | None = None
    subject_uid: str | None = None
    case_no: int | None = None
    justification: str | None = None
    approver_role_ids: list[int] = field(default_factory=list)
    # child-only
    parent_run_id: int | None = None
    root_run_id: int | None = None
    depth: int = 0
    key: str | None = None
    position: int | None = None
    after_keys: list[str] = field(default_factory=list)
    on_failure: str | None = None
    # gate-only
    gate_roles: list[int] | None = None
    gate_message: str | None = None


@dataclass(frozen=True, slots=True)
class NewAttempt:
    run_id: int
    root_run_id: int
    tenant_id: int
    attempt_no: int
    worker: str | None
    outcome: str
    started_at: datetime
    ended_at: datetime
    duration_ms: int | None
    error: str | None = None
    error_type: str | None = None
    method: str | None = None
    url: str | None = None
    status_code: int | None = None
    request: Any = None
    response: Any = None


@dataclass(frozen=True, slots=True)
class SettleFields:
    """Optional columns a settle may also write."""

    result: Any = None
    write_result: bool = False
    wait_started_at: datetime | None = None
    write_wait_started_at: bool = False
    leaf: bool | None = None
    error: str | None = None
    decision: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    case_no: int | None = None
    approver_role_ids: list[int] | None = None


@dataclass(slots=True)
class SweepReport:
    crashed_leaves: int = 0
    resumed_runs: int = 0
    timed_out: int = 0


@runtime_checkable
class Unit(Protocol):
    """One transaction. ``raw`` is the backing session a leaf body writes through."""

    @property
    def raw(self) -> Any: ...


class Store(Protocol):
    schema: str

    def unit(self, run: RunRow | None = None) -> Any:
        """An async context manager yielding a :class:`Unit`. Commits on clean exit."""
        ...

    def bind(self, session: Any) -> Unit:
        """Wrap a caller-owned session as a unit, so Engine.start/decide can run inside the
        caller's transaction. The caller owns commit and rollback."""
        ...

    # ---- run lifecycle ----
    async def insert_run(self, u: Unit, new: NewRun) -> RunRow | None: ...
    async def run_by_key(self, u: Unit, tenant_id: int, idempotency_key: str) -> RunRow | None: ...
    async def run_by_uid(
        self, u: Unit, uid: str, *, tenant_id: int | None = None, for_update: bool = False
    ) -> RunRow | None: ...
    async def run_by_id(self, u: Unit, run_id: int) -> RunRow | None: ...
    async def claim_run(
        self,
        u: Unit,
        *,
        worker_id: str,
        token: str,
        lease_seconds: float,
        now: datetime,
        run_id: int | None = None,
        timeout_seconds: float | None = None,
    ) -> RunRow | None: ...
    async def renew_lease(
        self, u: Unit, run_id: int, token: str, lease_seconds: float, now: datetime
    ) -> bool: ...
    async def settle_run(
        self,
        u: Unit,
        run_id: int,
        token: str | None,
        *,
        state: State,
        scheduled_at: datetime | None = None,
        ended: bool = False,
        fields: SettleFields | None = None,
        now: datetime,
    ) -> RunRow | None: ...
    async def mark_passes(self, u: Unit, run_id: int, leaf: bool) -> None: ...
    async def set_attempt_count(self, u: Unit, run_id: int, count: int) -> None: ...
    async def revive(self, u: Unit, run_id: int, *, now: datetime) -> None: ...
    async def bump_attempt(self, u: Unit, run_id: int, token: str, now: datetime) -> int: ...

    # ---- children ----
    async def children(self, u: Unit, parent_run_id: int) -> list[RunRow]: ...
    async def open_children_exist(self, u: Unit, parent_run_id: int) -> bool: ...
    async def wake_parent(
        self, u: Unit, parent_run_id: int, *, state: State, now: datetime
    ) -> RunRow | None: ...
    async def next_case_no(self, u: Unit, tenant_id: int) -> int: ...

    # ---- subjects / events / attempts ----
    async def insert_subjects(
        self, u: Unit, run: RunRow, subjects: Sequence[Any], *, blocking: bool
    ) -> None: ...
    async def release_subjects(self, u: Unit, run_id: int) -> None: ...
    async def subjects_of(self, u: Unit, run_id: int) -> list[SubjectRow]: ...
    async def insert_event(
        self,
        u: Unit,
        *,
        run: RunRow,
        event_type: str,
        actor_id: str | None = None,
        payload: dict[str, Any] | None = None,
        now: datetime,
    ) -> EventRow: ...
    async def events_of(self, u: Unit, root_run_id: int) -> list[EventRow]: ...
    async def insert_attempt(self, u: Unit, new: NewAttempt) -> AttemptRow: ...
    async def last_attempt(self, u: Unit, run_id: int) -> AttemptRow | None: ...
    async def attempts_of(self, u: Unit, run_ids: Sequence[int]) -> list[AttemptRow]: ...
    async def retries_used(self, u: Unit, run_id: int) -> int: ...

    # ---- worker ----
    async def sweep(self, u: Unit, *, now: datetime, registry_idempotent: Any) -> SweepReport: ...
    async def notify(self, u: Unit, run_id: int) -> None: ...
    async def due_run_ids(self, u: Unit, *, now: datetime, limit: int) -> list[int]: ...


__all__ = [
    "NewAttempt",
    "NewRun",
    "SettleFields",
    "Store",
    "SweepReport",
    "Unit",
]
