"""The ``StepHandler`` contract + registry — the seam between the generic orchestrator and the
independent modules (forms, documents, tasks, generate_document, identity/AML, accounts, terms).

Every step type is a handler; the engine never branches on ``step.type``. A handler:

- **preflight** — publish-time validation (required config + external refs exist). Blocking issues
  stop publish, so a broken flow can't explode at a customer's runtime.
- **instantiate** — create the step's artifact for a (case, participant) and report the initial
  signal (e.g. a manual review starts ``WAITING``; a dedup'd KYC step reports ``COMPLETED``).
- **handle** — react to an external signal (fulfilment / webhook) and report the new signal.
- **cancel** / **compensate** — undo the step's effect (withdraw the request, close the draft
  account, revoke the invite). Same hook serves cancellation and failure-compensation.

Handlers **return events, never mutate orchestration state** — the orchestrator consumes the
returned ``StepEvent``, transitions, projects, and enqueues the outbox (docs/flows.md §0).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.flows.domain_events import StepEvent, StepSignal


@dataclass
class StepContext:
    """Everything a handler needs to act on one step instance. Constructed by the orchestrator."""

    session: AsyncSession
    tenant_id: int
    execution_id: int
    case_id: int
    step_key: str
    config: dict[str, Any]
    # The case's subject (a contact for a KYC case, an account for an account case) and, for a
    # participant-fanned step, which participant contact this instance is for.
    subject_type: str | None = None
    subject_id: int | None = None
    participant_subject_id: int | None = None
    # The documents.requests this step's artifact hangs off (one request per contact per execution),
    # set by the orchestrator for request-backed steps (fill_form / accept_terms / upload_document).
    request_id: int | None = None
    data: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class StepHandler(Protocol):
    """A step-type module adapter. Implementations live in ``app/flows/step_handlers/``."""

    type: str
    assignee_default: str  # "client" | "staff"

    def config_keys(self) -> frozenset[str]:
        """The allowlisted config keys for this step type (moves per-type validation off
        ``definitions.py``)."""
        ...

    async def preflight(
        self, session: AsyncSession, tenant_id: int, config: dict[str, Any]
    ) -> list[str]:
        """Publish-time validation → a list of human-readable blocking issues (empty = OK)."""
        ...

    async def instantiate(self, ctx: StepContext) -> StepEvent | None:
        """Create the artifact for this step instance; return the initial event, or None to leave it
        pending until an external signal arrives."""
        ...

    async def handle(self, ctx: StepContext, detail: dict[str, Any]) -> StepEvent:
        """React to an external signal (fulfilment / webhook) and report the resulting event."""
        ...

    async def cancel(self, ctx: StepContext) -> None:
        """Withdraw/compensate this step's effect (idempotent)."""
        ...


# ---------------------------------------------------------------------------------------------
# Node kinds: human work vs engine work
# ---------------------------------------------------------------------------------------------
#
# v3 gave every node a ``kind``. ``kind: "step"`` is human work and its ``type`` names a step type;
# ``kind: "action"`` is unattended engine work and its ``type`` names an ACTION type
# (flow-contract.json → actionTypes). The two vocabularies overlap — ``generate_document`` is both
# a staff step and an automated action — so the kind is the discriminator, and it has to survive
# all the way to handler resolution or the collision comes back the moment a step is planned.
#
# It survives as a PREFIX on the persisted ``flows.case_steps.step_type``: the derived steps
# projection writes ``action:generate_document`` for an action node
# (``definitions._validate_graph_definition``), planning carries it as an opaque string, and this
# registry keys the action handler under the same prefixed name. So no plan-time or orchestration
# code has to learn about kinds, the discriminator is visible in the database when someone is
# debugging a case at 3am, and ``get_handler`` stays a dict lookup on one string.

#: The prefix that marks a persisted step type as engine work rather than human work.
ACTION_STEP_PREFIX = "action:"


def action_step_type(action_type: str) -> str:
    """The persisted ``step_type`` for an action node of this action type."""
    return f"{ACTION_STEP_PREFIX}{action_type}"


def action_type_of(step_type: str) -> str | None:
    """The action type behind a persisted step type, or None when it is ordinary human work."""
    if step_type.startswith(ACTION_STEP_PREFIX):
        return step_type[len(ACTION_STEP_PREFIX) :] or None
    return None


_REGISTRY: dict[str, StepHandler] = {}


def register(handler: StepHandler) -> StepHandler:
    """Register a handler by its ``type``. Raises on a duplicate (a programming error)."""
    if handler.type in _REGISTRY:
        raise ValueError(f"duplicate StepHandler for type {handler.type!r}")
    _REGISTRY[handler.type] = handler
    return handler


def get_handler(step_type: str) -> StepHandler | None:
    """The handler for a step type, or None if unknown (definition validation rejects unknowns)."""
    return _REGISTRY.get(step_type)


def registered_types() -> frozenset[str]:
    return frozenset(_REGISTRY)


class BaseHandler:
    """Convenience base: no-op instantiate (pending), a ``handle`` that maps any fulfilment to
    COMPLETED, and a no-op cancel. Concrete modules override what they need."""

    type: str = ""
    assignee_default: str = "client"
    _config_keys: frozenset[str] = frozenset()

    def config_keys(self) -> frozenset[str]:
        return self._config_keys

    async def preflight(
        self, session: AsyncSession, tenant_id: int, config: dict[str, Any]
    ) -> list[str]:
        return []

    async def instantiate(self, ctx: StepContext) -> StepEvent | None:
        return None

    async def handle(self, ctx: StepContext, detail: dict[str, Any]) -> StepEvent:
        return StepEvent(
            signal=StepSignal.COMPLETED,
            case_id=ctx.case_id,
            step_key=ctx.step_key,
            participant_subject_id=ctx.participant_subject_id,
        )

    async def cancel(self, ctx: StepContext) -> None:
        return None
