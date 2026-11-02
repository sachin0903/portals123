"""Opening, carrying and closing a view-as session.

Four calls, in the order they happen: a member of staff opens one from a client's record; the
client portal redeems the handoff ticket the new tab arrives with; the portal's banner asks whose
portal this is; and the session is closed, either by the person leaving it or by its own clock.

The authorisation is in two places and both are necessary. Opening requires ``contacts:read``
AND that the client is already reachable -- in the caller's ``auth.contact_scope()``, or via
firm-wide read (``auth.can_read_all()`` / ``contacts:read_all``). Every request thereafter
re-checks the same thing inside ``auth.resolve_view_as_claims``, which is what makes a session
close when the book (or the firm-wide grant) changes underneath it rather than run to its expiry
on authority nobody holds any more.

See ``app/view_as.py`` for the session itself and why it is a table rather than a signed token.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import environments, view_as
from app.auth import Principal, get_current_principal, require_permission
from app.config import settings
from app.db import FirmContext, firm_session, portal_session, tenant_session
from app.permissions import CONTACTS_READ
from app.routers.internal import internal_session, require_internal_token

router = APIRouter(prefix="/view-as", tags=["view-as"])


class OpenRequest(BaseModel):
    """Why this session is being opened. Recorded against it and shown to whoever reads the
    trail later, so it is required and cannot be blank."""

    reason: str = Field(min_length=3, max_length=500)


class OpenedSession(BaseModel):
    """What the staff portal needs to send the person on their way: where the client portal
    lives, and the ticket that identifies the session when they arrive. The session token is not
    here -- it reaches the client portal's server directly, never a browser."""

    uid: str
    handoff_ticket: str
    expires_at: str
    #: The client portal's base URL for this firm (host-keyed per tenant), or null when the firm
    #: has no client portal registered -- in which case there is nowhere to send the tab.
    portal_url: str | None


class SessionDetail(BaseModel):
    """The banner's content: who is looking, whose portal this is, and how long is left."""

    uid: str
    adviser_name: str
    contact_id: int
    contact_name: str
    reason: str
    expires_at: str
    seconds_remaining: int


class RedeemRequest(BaseModel):
    handoff_ticket: str


class RedeemedSession(BaseModel):
    token: str
    detail: SessionDetail


def _detail(live: view_as.ViewAsSession) -> SessionDetail:
    return SessionDetail(
        uid=live.uid,
        adviser_name=live.adviser_name,
        contact_id=live.contact_id,
        contact_name=live.contact_name,
        reason=live.reason,
        expires_at=live.expires_at.isoformat(),
        seconds_remaining=view_as.seconds_remaining(live.expires_at),
    )


async def _require_view_as(
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    """Refuse anything that is not a live view-as session. Guards the two calls that only make
    sense from inside one."""
    if principal.view_as_contact_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a view-as session",
        )
    return principal


async def _contact_in_scope(session: AsyncSession, tenant_id: int, contact_uid: str) -> int:
    """The client's id, provided this session already reaches them. 404 otherwise.

    Reach means the same thing RLS uses for the client record: the caller's assigned book, or
    firm-wide read (``contacts:read_all``). 404 rather than 403 because the two are the same
    answer here: a client outside that reach is a client they have no way to know exists, and
    saying "you may not" about a named record tells them it does.
    """
    row = (
        await session.execute(
            text(
                "SELECT id FROM crm.contacts "
                " WHERE uid = :uid AND tenant_id = :tenant_id AND deleted_at IS NULL "
                "   AND (auth.can_read_all() OR id = ANY(auth.contact_scope()))"
            ),
            {"uid": contact_uid, "tenant_id": tenant_id},
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such client")
    return int(row)


@router.post("/open/{contact_uid}", name="open-view-as", status_code=status.HTTP_201_CREATED)
async def open_view_as(
    contact_uid: str,
    body: OpenRequest,
    ctx: FirmContext = Depends(firm_session),
) -> OpenedSession:
    """Open a read-only view of one of your clients' portals, and return the ticket that carries
    the session to it.

    Only from the staff portal, only for a client you already reach (your book, or the whole firm
    with ``contacts:read_all``), and only with a reason -- which is recorded against the session
    alongside who you are and when you opened it.

    The session shows what the client sees and nothing more: every request that would change
    anything is refused. Acting for a client is done from their record here in the staff portal,
    under your own name, on surfaces built to say so.

    It lasts an hour and can be closed at any point before that, by you when you leave it.
    """
    if ctx.principal.view_as_contact_id is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A view-as session cannot open another one",
        )
    if ctx.principal.portal != "platform":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the staff portal can open a view-as session",
        )
    require_permission(ctx.principal, CONTACTS_READ)
    contact_id = await _contact_in_scope(ctx.session, ctx.tenant_id, contact_uid)
    try:
        minted = await view_as.mint(
            ctx.session,
            tenant_id=ctx.tenant_id,
            adviser_sub=ctx.principal.user_id,
            contact_id=contact_id,
            reason=body.reason,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No resolvable identity for this login",
        ) from exc
    # Where the tab goes. The configured base URL (dev: the local client portal) beats the
    # tenant's registered client-portal host, the same precedence the email links use.
    portal_url = settings.client_portal_base_url or await environments.portal_base_url(
        ctx.session, ctx.tenant_id, "client"
    )
    return OpenedSession(
        uid=minted.uid,
        handoff_ticket=minted.handoff_ticket,
        expires_at=minted.expires_at.isoformat(),
        portal_url=portal_url.rstrip("/") if portal_url else None,
    )


@router.post(
    "/redeem",
    name="redeem-view-as",
    include_in_schema=False,
    dependencies=[Depends(require_internal_token)],
)
async def redeem(
    body: RedeemRequest,
    session: AsyncSession = Depends(internal_session),
) -> RedeemedSession:
    """Exchange the single-use handoff ticket for the session token. Called by the client
    portal's own server when the new tab opens, never by a browser -- which is the point: the
    token goes into an httpOnly cookie without ever having been in a URL."""
    token = await view_as.redeem_handoff(session, body.handoff_ticket)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This link has already been used or has expired",
        )
    live = await view_as.resolve(session, token)
    if live is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This view-as session is no longer live",
        )
    return RedeemedSession(token=token, detail=_detail(live))


@router.get("/session", name="view-as-session")
async def current_session(
    principal: Principal = Depends(_require_view_as),
    session: AsyncSession = Depends(tenant_session),
) -> SessionDetail:
    """Whose portal this is and how long is left in the session. The banner across the top of
    the client portal reads this, and reads it again as the window runs down.

    Read under RLS: ``auth.view_as_sessions`` lets a login see the sessions it opened, and the
    view-as principal's uuid IS the adviser's, so this needs no elevation to find its own."""
    live = await view_as.by_uid(session, str(principal.view_as_session_uid))
    if live is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such view-as session")
    return _detail(live)


@router.post("/end", name="end-view-as", status_code=status.HTTP_204_NO_CONTENT)
async def end_session(
    principal: Principal = Depends(_require_view_as),
    ctx: FirmContext = Depends(portal_session),
) -> None:
    """Leave the session now rather than waiting for it to lapse. Idempotent: a session that has
    already ended is not an error."""
    await view_as.end(ctx.session, str(principal.view_as_session_uid))
