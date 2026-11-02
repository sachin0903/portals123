"""The seams where a host app meets the generic engine.

baton knows tenants only as an integer column and knows nothing of roles, permissions or audit
streams. The app supplies a ``Hooks`` whose methods run inside the engine's own transactions:
set the database role, decide a firm's policy on a child before it is created, mirror events to
an audit stream, and react when a root run settles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from baton.context import Approval
    from baton.definitions import ActionDefinition
    from baton.errors import Skip
    from baton.model import EventRow, RunRow, RunView


class Hooks(Protocol):
    async def begin_session(self, session: AsyncSession, run: RunRow | None) -> None:
        """Run once on every engine-opened transaction, before any baton statement. The portals
        app issues ``SET LOCAL ROLE service_role`` here."""
        ...

    async def before_child(
        self,
        session: AsyncSession,
        parent: RunRow,
        child: ActionDefinition[object],
        params: dict[str, object],
    ) -> Skip | Approval | None:
        """The firm's policy on a child about to be created. Return ``Skip`` to complete it
        without running (switched off), ``Approval`` to gate it, or ``None`` to run it."""
        ...

    async def on_event(self, session: AsyncSession, event: EventRow, run: RunView) -> None:
        """A lifecycle event was just written. The app mirrors the ones it cares about to its
        own audit stream, in this same transaction."""
        ...

    async def on_run_terminal(self, session: AsyncSession, run: RunView, error: str | None) -> None:
        """A **root** run reached a terminal state. The portals app releases the journey step
        waiting on it here."""
        ...


class NoopHooks:
    async def begin_session(self, session: AsyncSession, run: RunRow | None) -> None:
        return None

    async def before_child(
        self,
        session: AsyncSession,
        parent: RunRow,
        child: ActionDefinition[object],
        params: dict[str, object],
    ) -> None:
        return None

    async def on_event(self, session: AsyncSession, event: EventRow, run: RunView) -> None:
        return None

    async def on_run_terminal(self, session: AsyncSession, run: RunView, error: str | None) -> None:
        return None
