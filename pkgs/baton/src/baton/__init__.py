"""baton — a small durable-execution runtime.

There is one unit of work, the **action**. An action that does its own work (``ctx.session``)
is a *leaf*; an action that calls other actions is a *composite*. A run of an action replays its
body each time the engine advances it: completed child calls return their stored result, a child
that is not ready suspends the parent, and the rest execute. State lives in Postgres; a leaf
retries and waits durably; a gate pauses a run for a human decision.

See the README for the model, the determinism contract, and the host-app DDL contract.
"""

from __future__ import annotations

from pathlib import Path

from baton.context import Approval, GateDecision, RunContext, Subject
from baton.definitions import ActionDefinition, ActionOptions, action
from baton.engine import Engine
from baton.errors import (
    ApprovalError,
    BatonError,
    ChildFailed,
    Conflicted,
    LeaseBusy,
    NotFound,
    NotReady,
    Permanent,
    ProgrammingError,
    Rejected,
    Retry,
    Skip,
    SubjectBusy,
    Wait,
)
from baton.hooks import Hooks, NoopHooks
from baton.model import AdvanceResult, AttemptView, RunDetail, RunView, StartResult
from baton.policy import exponential_backoff
from baton.registry import GATE_KEY, Registry, RegistryError, default_registry
from baton.states import State, StateType
from baton.worker import Worker

__all__ = [
    "GATE_KEY",
    "ActionDefinition",
    "ActionOptions",
    "AdvanceResult",
    "Approval",
    "ApprovalError",
    "AttemptView",
    "BatonError",
    "ChildFailed",
    "Conflicted",
    "Engine",
    "GateDecision",
    "Hooks",
    "LeaseBusy",
    "NoopHooks",
    "NotFound",
    "NotReady",
    "Permanent",
    "ProgrammingError",
    "Registry",
    "RegistryError",
    "Rejected",
    "Retry",
    "RunContext",
    "RunDetail",
    "RunView",
    "Skip",
    "StartResult",
    "State",
    "StateType",
    "Subject",
    "SubjectBusy",
    "Wait",
    "Worker",
    "action",
    "ddl",
    "default_registry",
    "exponential_backoff",
]


def ddl(schema: str) -> str:
    """The CREATE TABLE statements for baton's tables, rendered into ``schema``.

    The host migration runs this, then adds its own RLS, grants, FKs, uid defaults and triggers
    around the tables (see the README's DDL contract). ``schema`` must be a trusted identifier,
    not user input: it is substituted literally.
    """
    if not schema.isidentifier():
        raise ValueError(f"schema must be a bare SQL identifier, got {schema!r}")
    template = (Path(__file__).parent / "store" / "sql" / "baton.sql").read_text(encoding="utf-8")
    return template.replace("{{s}}", schema)
