"""The estate-wide automation kill switch — the operations surface behind
``automations:global_pause``.

Two endpoints, both on ``/admin/automations/pause``. They exist because the permission did:
``automations:global_pause`` has been registered, contract-mirrored and reserved to the operations
portal since 0043 with nothing anywhere checking it. A grant that authorises nothing is a trap —
it reads to whoever grants it as a control the platform has.

**Its own module, not a section of ``routers/automations.py``.** That router is the firm-facing
authoring surface: ``firm_session``, ``automations:*`` platform slugs, one tenant. This is the
operations surface: ``admin_session``, an operations slug, no tenant at all. The two share a URL
prefix and nothing else, and the firm router's own comment already said the global switch "belongs
on the admin surface".

**Why a pause needs a reason.** The state is visible on every operator's screen and in every
firm's silence; an incident switch with no note is the one the next operator daren't touch.

The mutating route authenticates through ``get_current_principal`` (via ``admin_session``), so it
lands its own ``automation.platform_pause_updated`` row like every other act — and the
underlying table is on ``audit.attach`` (0048), so the before/after is in ``audit.log`` too. Both
were deliberate: "who turned the safety off?" is the first question after any incident, and the
answer should not depend on one best-effort stream.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, get_current_principal, require_permission
from app.db import admin_session
from app.flows import platform_pause
from app.permissions import AUTOMATIONS_GLOBAL_PAUSE

router = APIRouter(prefix="/admin/automations", tags=["admin"])


class SetPlatformPause(BaseModel):
    paused: bool
    #: Free text an operator writes for the next operator. Capped rather than validated: the point
    #: is a sentence, and a schema that argued about its shape at 03:00 would be the wrong tool.
    reason: str | None = Field(default=None, max_length=500)


async def _actor_id(session: AsyncSession, principal: Principal) -> str | None:
    """The acting user's ``auth.users`` id, resolved from the verified WorkOS subject rather than
    trusted from the wire. ``None`` for a system caller with no mirrored row."""
    return (
        await session.execute(
            text("SELECT id::text FROM auth.users WHERE workos_user_id = :w"),
            {"w": principal.user_id},
        )
    ).scalar_one_or_none()


async def _payload(session: AsyncSession) -> dict[str, Any]:
    state = await platform_pause.read(session)
    row = (
        (
            await session.execute(
                text(
                    "SELECT u.first_name, u.last_name, u.email FROM auth.users u "
                    "WHERE u.id = CAST(:i AS uuid)"
                ),
                {"i": state.paused_by},
            )
        )
        .mappings()
        .one_or_none()
        if state.paused_by
        else None
    )
    name = None
    if row is not None:
        name = " ".join(p for p in (row["first_name"], row["last_name"]) if p) or row["email"]
    return {**state.as_dict(), "paused_by_name": name}


@router.get("/pause", name="get-automation-platform-pause")
async def get_platform_pause(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> dict[str, Any]:
    """The switch position, with the operator who set it named.

    Gated on the same slug as the write. A read-only view of the kill switch is not something to
    hand out more widely than the switch itself: "automations are off across the estate, since
    03:12, because of the Meridian chaser" is incident detail.
    """
    require_permission(principal, AUTOMATIONS_GLOBAL_PAUSE)
    return await _payload(session)


@router.put("/pause", name="set-automation-platform-pause")
async def set_platform_pause(
    body: SetPlatformPause,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(admin_session),
) -> dict[str, Any]:
    """Stop — or restart — every automation on the platform.

    While paused the worker's automation loop runs no pass at all: nothing is consumed, nothing is
    dispatched, for any firm. Resuming does NOT replay the backlog — the consumer's cursor is
    parked at the stream head, matching the deployment-level switch, because a night of held
    client emails arriving at once is a worse incident than the one the switch was pulled for.
    See ``app/flows/platform_pause.py``.

    A firm's own switch (``automations:pause``) is untouched by this in both directions: a firm
    that paused itself stays paused when the platform resumes.
    """
    require_permission(principal, AUTOMATIONS_GLOBAL_PAUSE)
    await platform_pause.set_paused(
        session,
        paused=body.paused,
        reason=(body.reason or "").strip() or None,
        actor_id=await _actor_id(session, principal),
    )
    return await _payload(session)
