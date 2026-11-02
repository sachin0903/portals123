"""The generic orchestration state machine — engine-neutral, pure, unit-testable.

The engine knows only these seven states; it has **no** onboarding vocabulary (``with_client`` /
``identity_under_review`` / … are *derived labels* the onboarding config computes for the UI and
the ``crm.applications`` projection — see ``app/flows/projection.py``). This is what lets the same
engine run a compliance review, an annual suitability review, or corporate onboarding with no code
change (docs/flows.md §0).

Both a **case** (a work container for one subject) and an **execution** (the version-pinned group of
cases) move through the same states:

    draft ──▶ active ──▶ completed
      │        │  ▲        (terminal, latched)
      │        │  └── waiting / blocked ──┘
      ├──────▶ cancelled  (terminal, latched)
      └──────▶ failed      (terminal, latched)

- ``draft``     — created, not yet started.
- ``active``    — in progress; at least one step is workable.
- ``blocked``   — gated by a dependency (e.g. an account case awaiting its participants' KYC).
- ``waiting``   — awaiting an external signal (a provider webhook, a manual review).
- ``completed`` / ``cancelled`` / ``failed`` — terminal; never transition out (latched).
"""

from __future__ import annotations

from enum import StrEnum


class State(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    BLOCKED = "blocked"
    WAITING = "waiting"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


#: Terminal states never transition out — a finished case/execution is latched.
TERMINAL: frozenset[State] = frozenset({State.COMPLETED, State.CANCELLED, State.FAILED})

#: The non-terminal working states, freely interchangeable as facts change.
_WORKING: frozenset[State] = frozenset({State.DRAFT, State.ACTIVE, State.BLOCKED, State.WAITING})

#: Legal transitions. A working state may move to any other working state (the engine recomputes
#: from facts each event) or to any terminal state; terminal states are sinks. Same-state is always
#: allowed (a no-op). This table is the single source of truth for ``can_transition``.
_LEGAL: dict[State, frozenset[State]] = {
    **dict.fromkeys(_WORKING, _WORKING | TERMINAL),
    **{s: frozenset() for s in TERMINAL},
}


class IllegalTransition(ValueError):
    """Raised when a caller attempts a transition the machine forbids (e.g. out of a terminal
    state). Surfaces as a 409 at the API, never a silent status rewrite."""


def can_transition(current: State, target: State) -> bool:
    """True if ``current → target`` is legal. Same-state is always legal (idempotent no-op);
    leaving a terminal state is never legal."""
    if current == target:
        return True
    return target in _LEGAL.get(current, frozenset())


def transition(current: State, target: State) -> State:
    """Return ``target`` if the move is legal, else raise ``IllegalTransition``. Same-state is a
    no-op that returns ``current``."""
    if current == target:
        return current
    if not can_transition(current, target):
        raise IllegalTransition(f"illegal transition: {current} → {target}")
    return target


def is_terminal(state: State) -> bool:
    return state in TERMINAL
