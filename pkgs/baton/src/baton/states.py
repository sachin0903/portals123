"""Run states: a small typed set the engine reasons about, and a free label people read.

The split is Prefect's. ``StateType`` drives orchestration (what is claimable, what is
terminal, what a parent does with a child in this state). ``State.name`` is bookkeeping for a
screen: ``AwaitingRetry`` and ``Waiting`` are both ``scheduled``; ``NeedsAttention`` and
``Conflicted`` are both ``failed``. A host app colours by type and labels by name.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class StateType(StrEnum):
    SCHEDULED = "scheduled"
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CRASHED = "crashed"


TERMINAL: frozenset[StateType] = frozenset(
    {StateType.COMPLETED, StateType.FAILED, StateType.CANCELLED}
)


@dataclass(frozen=True, slots=True)
class State:
    type: StateType
    name: str
    message: str | None = None

    @property
    def terminal(self) -> bool:
        return self.type in TERMINAL

    def with_message(self, message: str | None) -> State:
        return replace(self, message=message)


# scheduled — claimable once ``scheduled_at`` has passed
SCHEDULED = State(StateType.SCHEDULED, "Scheduled")
AWAITING_RETRY = State(StateType.SCHEDULED, "AwaitingRetry")
WAITING = State(StateType.SCHEDULED, "Waiting")
RESUMED = State(StateType.SCHEDULED, "Resumed")
# pending — created, not yet claimable (reserved; the engine creates children scheduled)
PENDING = State(StateType.PENDING, "Pending")
# running — a holder has the lease
RUNNING = State(StateType.RUNNING, "Running")
# paused — waiting on something that is not time: a decision, or children
AWAITING_APPROVAL = State(StateType.PAUSED, "AwaitingApproval")
AWAITING_CHILDREN = State(StateType.PAUSED, "AwaitingChildren")
# completed
COMPLETED = State(StateType.COMPLETED, "Completed")
PARTIAL = State(StateType.COMPLETED, "Partial")
SKIPPED = State(StateType.COMPLETED, "Skipped")
APPROVED = State(StateType.COMPLETED, "Approved")
# failed
FAILED = State(StateType.FAILED, "Failed")
NEEDS_ATTENTION = State(StateType.FAILED, "NeedsAttention")
CONFLICTED = State(StateType.FAILED, "Conflicted")
TIMED_OUT = State(StateType.FAILED, "TimedOut")
# cancelled
CANCELLED = State(StateType.CANCELLED, "Cancelled")
REJECTED = State(StateType.CANCELLED, "Rejected")
# crashed — a leaf whose attempt lease expired mid-flight
CRASHED = State(StateType.CRASHED, "Crashed")
