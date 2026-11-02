"""What a running body sees.

One class, two roles. A **leaf** body reaches for ``ctx.session`` and does its own work; the
first access opens the attempt transaction and marks the run a leaf. A **composite** body calls
other actions, ``ctx.gate(...)`` and ``ctx.gather(...)``. Doing both is a ``ProgrammingError``,
raised on the first pass before any vendor call: a run is one or the other.

Nothing here reads a clock, a setting or randomness, and a composite has no session. That is the
determinism contract in code: given the same params and the same child results, a body takes the
same path every pass.
"""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from baton.errors import ProgrammingError
from baton.registry import GATE_KEY

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from baton.definitions import ActionDefinition
    from baton.model import AttemptView, RunView
    from baton.replay import ReplayPass
    from baton.states import State


@dataclass(frozen=True, slots=True)
class Subject:
    kind: str
    uid: str
    entity_id: int | None = None


@dataclass(frozen=True, slots=True)
class Approval:
    roles: Sequence[int]
    message: str | None = None
    case_number: bool = True


@dataclass(frozen=True, slots=True)
class GateDecision:
    decided_by: str | None
    decided_at: datetime
    note: str | None


class RunContext:
    """The handle a body receives. Backed by the active :class:`ReplayPass`."""

    __slots__ = ("_pass",)

    def __init__(self, pass_: ReplayPass) -> None:
        self._pass = pass_

    # ---- identity, available to any body ----
    @property
    def run_view(self) -> RunView:
        return self._pass.run_view

    @property
    def params(self) -> dict[str, Any]:
        return self._pass.run.params

    @property
    def tenant_id(self) -> int:
        return self._pass.run.tenant_id

    @property
    def attempt_no(self) -> int:
        return self._pass.run.attempt_count + 1

    @property
    def previous(self) -> AttemptView | None:
        return self._pass.previous_attempt()

    @property
    def waited(self) -> timedelta:
        started = self._pass.run.wait_started_at
        return self._pass.engine.now() - started if started is not None else timedelta(0)

    # ---- leaf role: the body's own work ----
    @property
    def session(self) -> AsyncSession:
        return cast("AsyncSession", self._pass.leaf_session())

    def evidence(
        self,
        *,
        method: str | None = None,
        url: str | None = None,
        request: Any = None,
        response: Any = None,
        status_code: int | None = None,
    ) -> None:
        self._pass.set_evidence(
            method=method, url=url, request=request, response=response, status_code=status_code
        )

    async def heartbeat(self) -> None:
        await self._pass.heartbeat()

    async def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        await self._pass.emit(event_type, payload or {})

    # ---- composite role: compose children ----
    def gate(
        self, key: str = "approval", *, roles: Sequence[int], message: str | None = None
    ) -> Awaitable[GateDecision]:
        return self._pass.gate(key, roles=tuple(roles), message=message)

    def run(
        self,
        action: ActionDefinition[Any] | str,
        *,
        key: str | None = None,
        wait_for: Sequence[str] = (),
        **params: Any,
    ) -> Awaitable[Any]:
        defn = self._pass.resolve(action)
        return self._pass.make_call(defn, params, key=key, wait_for=tuple(wait_for))

    async def gather(self, *calls: Awaitable[Any]) -> list[Any]:
        return await self._pass.gather(calls)

    def state_of(self, key: str) -> State | None:
        return self._pass.state_of(key)

    def result_of(self, key: str) -> Any:
        return self._pass.result_of(key)


__all__ = ["Approval", "GateDecision", "RunContext", "Subject", "GATE_KEY", "ProgrammingError"]
