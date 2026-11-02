"""The estate-wide automation kill switch — the thing ``automations:global_pause`` guards.

Three levers stop automations, and they are deliberately different things:

* ``settings.automations_enabled`` — DEPLOYMENT. No database, no permission, no working API; the
  lever you pull from the deploy config when you cannot reason about anything else.
* **This one** — PLATFORM. A row an operator flips through the operations portal, gated on
  ``automations:global_pause``, audited, visible in the UI. The 03:00 control: a firm's rule is
  emailing clients, you have a browser and a login, and you want everything stopped NOW without
  hunting for which firm's row is to blame.
* ``flows.automation_settings.paused`` — TENANT. A firm pausing itself (``automations:pause``).

**Semantics, matching the deployment lever exactly** (``app/config.py`` sets them out): while
paused nothing is consumed and nothing is dispatched, and on resume the consumer's cursor is
PARKED AT THE HEAD rather than replaying the backlog. A kill switch that quietly queued a night's
events into a stampede the moment it was released would be a worse failure than the one it was
pulled for. The difference from the deployment lever is only mechanical — that one advances the
cursor batch by batch while off, this one jumps it once on resume — and the observable behaviour
is the same: firings that would have happened during the pause do not happen at all.

**Why the cursor jump is stored rather than done in-process.** The worker can restart mid-pause,
and an in-memory "we were paused" flag would then be lost, replaying the whole backlog on resume —
the exact failure the design exists to prevent. So the resume records the stream head it wants
skipped past (``resume_from_event_id``) and the worker clears it once it has parked the cursor
there. Idempotent, and safe to observe late.

The read is on the worker's loop (every few seconds) and on one operations page. It is a single
primary-key row with no join; caching it would trade a trivial query for a window in which an
operator has pressed the switch and automations are still firing, which is the whole point of the
control.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.flows.automation import CONSUMER

logger = logging.getLogger("automations")


@dataclass(frozen=True)
class PauseState:
    """The switch as an operator sees it."""

    paused: bool
    reason: str | None
    paused_at: datetime | None
    paused_by: str | None
    #: The stream position a resume asked the consumer to skip to; ``None`` once it has.
    resume_from_event_id: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "reason": self.reason,
            "paused_at": self.paused_at,
            "paused_by": self.paused_by,
        }


_EMPTY = PauseState(
    paused=False, reason=None, paused_at=None, paused_by=None, resume_from_event_id=None
)


async def read(session: AsyncSession) -> PauseState:
    """The current switch position. Requires a ``service_role`` session — the table has no
    ``authenticated`` grant (0048).

    A missing row reads as "not paused" rather than raising. 0048 seeds it, so the only way to
    reach that branch is a hand-deleted row, and failing closed there would take every firm's
    automations down for a bookkeeping accident.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT paused, reason, paused_at, paused_by, resume_from_event_id "
                    "FROM flows.automation_platform_pause WHERE id"
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return _EMPTY
    return PauseState(
        paused=bool(row["paused"]),
        reason=row["reason"],
        paused_at=row["paused_at"],
        paused_by=str(row["paused_by"]) if row["paused_by"] else None,
        resume_from_event_id=(
            int(row["resume_from_event_id"]) if row["resume_from_event_id"] is not None else None
        ),
    )


async def set_paused(
    session: AsyncSession, *, paused: bool, reason: str | None, actor_id: str | None
) -> PauseState:
    """Flip the switch and return the new state. ``service_role`` session; the caller owns the
    permission gate.

    Resuming stamps ``resume_from_event_id`` with the CURRENT stream head, which is what makes the
    backlog get dropped rather than replayed (module header). Pausing clears it, so a
    pause/resume/pause sequence cannot leave a stale skip target behind to swallow live events
    once the last pause lifts.

    Idempotent: pausing an already-paused platform re-stamps the reason and the operator (the
    second operator's note is the current one) but does not move ``paused_at``, so "how long has
    this been off" keeps its answer.
    """
    resume_from = None
    if not paused:
        resume_from = (
            await session.execute(text("SELECT COALESCE(max(id), 0) FROM audit.actions"))
        ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO flows.automation_platform_pause "
            "  (id, paused, reason, resume_from_event_id, paused_at, paused_by) "
            "VALUES (true, :p, :r, :from_id, CASE WHEN :p THEN now() END, CAST(:by AS uuid)) "
            "ON CONFLICT (id) DO UPDATE SET "
            "  paused = EXCLUDED.paused, "
            "  reason = EXCLUDED.reason, "
            "  resume_from_event_id = EXCLUDED.resume_from_event_id, "
            "  paused_at = CASE "
            "    WHEN NOT EXCLUDED.paused THEN NULL "
            "    WHEN flows.automation_platform_pause.paused THEN "
            "         flows.automation_platform_pause.paused_at "
            "    ELSE now() END, "
            "  paused_by = EXCLUDED.paused_by"
        ),
        {"p": paused, "r": reason, "from_id": resume_from, "by": actor_id},
    )
    return await read(session)


async def gate(session: AsyncSession) -> bool:
    """``True`` when the consumer must not run this pass. Called once per loop iteration.

    Also performs the one piece of work a resume leaves behind: parking the consumer's cursor at
    the head the resume recorded, so the events that arrived while the platform was paused are
    skipped rather than replayed. It happens HERE, in the single-threaded worker loop, rather than
    in the endpoint — the consumer claims that cursor row ``FOR UPDATE SKIP LOCKED`` mid-pass, and
    a writer racing it from the API process is a lock queue nobody needs.

    ``GREATEST`` rather than a plain assignment: a cursor that has already moved past the resume
    point (a worker that resumed, consumed, and only then got here) must never be dragged
    backwards into a replay.
    """
    state = await read(session)
    if state.paused:
        return True
    if state.resume_from_event_id is not None:
        await session.execute(
            text(
                "UPDATE flows.automation_cursor "
                "SET position = GREATEST(position, :p), updated_at = now() "
                "WHERE consumer = :c"
            ),
            {"p": state.resume_from_event_id, "c": CONSUMER},
        )
        await session.execute(
            text("UPDATE flows.automation_platform_pause SET resume_from_event_id = NULL WHERE id")
        )
        logger.info(
            "automations resumed platform-wide; the consumer skips to the stream head",
            extra={"event": "automations_platform_resumed", "position": state.resume_from_event_id},
        )
    return False
