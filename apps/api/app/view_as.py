"""View-as sessions: a member of staff opening the client portal as one of their clients.

The purpose is to see what the client sees. A staff login already reaches its book through
``auth.contact_scope()``, so the data is not new access -- what is new is the *shape*: the client
portal's own pages, its empty states, its onboarding journey, its wording. A member of staff
answering "what does my screen say?" on the telephone needs the client's screen, not a staff
rendering of the same figures.

The session is a row in ``auth.view_as_sessions`` and the row IS the credential. The client
portal holds an opaque token in an httpOnly cookie; every request it makes is resolved back to
the row by the token's SHA-256. That is the reason for a table rather than a self-contained
signed token: a signed token cannot be withdrawn before it expires and leaves nothing to read
afterwards, and both of those matter for a session in which one person acts inside another
person's account.

Four properties hold by construction, and each one is somewhere specific:

* **It reads and nothing else.** ``permits`` below refuses every method that could change
  something. Seeing a client's screen is the purpose; acting for them belongs on the staff
  portal under the person's own name, where the surfaces for it exist and say what they are.
* **It can only narrow.** ``auth.resolve_view_as_claims`` takes the client id and refuses unless
  that client is already reachable as staff (assigned book, or firm-wide ``contacts:read_all``),
  then collapses the whole session to that one client. The check is in the claim minter because
  that is the function RLS trusts.
* **No staff authority comes along.** The principal built here carries no permissions, so every
  ``require_permission`` gate refuses it and ``auth.can_read_all()`` is false. The session can
  reach exactly what the client can reach and nothing else.
* **The actor is the member of staff.** ``Principal.user_id`` is their own WorkOS subject, so
  ``audit.actions`` and ``audit.log`` name them, not the client. A session that recorded the
  client as the actor would put words in their mouth.

The handoff is separate and deliberately short. Carrying the session token itself in the URL that
opens the new tab would leave a live credential in browser history, so the staff portal receives
a single-use ticket instead. Redeeming that ticket is what MINTS the token -- it is generated at
redemption rather than handed over from storage -- which means the row never holds a recoverable
credential at any point, only hashes, and there is nothing to decrypt.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

#: Bearer prefix marking a view-as session token, so the request layer can route it to this
#: module instead of putting it to WorkOS's JWKS. A token is opaque: the prefix is the only part
#: that carries meaning.
TOKEN_PREFIX = "viewas_"  # noqa: S105 - a routing prefix on the token, not a secret

#: Prefix on the single-use handoff ticket. A different prefix from the session token so that a
#: ticket presented as a bearer is refused rather than half-recognised.
HANDOFF_PREFIX = "viewas-handoff_"


def _hash(value: str) -> str:
    """The stored form of a token. SHA-256 is right here where a preimage is 32 random bytes:
    there is no dictionary to search, so a slow KDF would buy nothing and cost a hash on every
    request."""
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class ViewAsSession:
    """A live view-as session, as the request layer needs it."""

    uid: str
    tenant_id: int
    #: The member of staff, as WorkOS knows them -- this is what the audit trail records.
    adviser_sub: str
    adviser_name: str
    contact_id: int
    contact_name: str
    reason: str
    started_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class MintedSession:
    """What minting returns: the ticket the staff portal puts in a URL, and the window."""

    uid: str
    handoff_ticket: str
    expires_at: datetime


async def mint(
    session: AsyncSession,
    *,
    tenant_id: int,
    adviser_sub: str,
    contact_id: int,
    reason: str,
) -> MintedSession:
    """Open a session for ``adviser_sub`` to act as ``contact_id``, and return its handoff ticket.

    Runs elevated (``service_role``): the caller has already established, through
    ``auth.contact_scope()``, that this client is theirs. The authoritative re-check happens on
    every subsequent request inside ``auth.resolve_view_as_claims``, so a book that changes
    mid-session closes it at the next request rather than leaving it open on stale authority.
    """
    ticket = HANDOFF_PREFIX + secrets.token_urlsafe(32)
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO auth.view_as_sessions "
                    "  (tenant_id, adviser_id, contact_id, reason, handoff_hash, "
                    "   handoff_expires_at, expires_at) "
                    "SELECT u.tenant_id, u.id, :contact_id, :reason, :handoff_hash, "
                    "       now() + make_interval(secs => :handoff_secs), "
                    "       now() + make_interval(mins => :session_mins) "
                    "  FROM auth.users u "
                    " WHERE u.workos_user_id = :sub AND u.tenant_id = :tenant_id "
                    "   AND u.deleted_at IS NULL "
                    "RETURNING uid, expires_at"
                ),
                {
                    "tenant_id": tenant_id,
                    "contact_id": contact_id,
                    "reason": reason.strip(),
                    "handoff_hash": _hash(ticket),
                    "handoff_secs": settings.view_as_handoff_seconds,
                    "session_mins": settings.view_as_session_minutes,
                    "sub": adviser_sub,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("no local user for this login")
    # No session token exists yet: redeeming the ticket is what creates it. Until then the row
    # is a booking, not a credential.
    return MintedSession(uid=str(row["uid"]), handoff_ticket=ticket, expires_at=row["expires_at"])


async def redeem_handoff(session: AsyncSession, ticket: str) -> str | None:
    """Spend a single-use handoff ticket and mint the session's token, or ``None`` if the ticket
    cannot be spent.

    The token is created here rather than retrieved, which is what keeps the row free of any
    credential a reader could use: before this call there is no token, and after it there is only
    a hash. One statement both spends the ticket and records the new hash, so two portals racing
    on the same URL cannot both come away with a session -- the loser matches no row.
    """
    if not ticket.startswith(HANDOFF_PREFIX):
        return None
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    redeemed = (
        await session.execute(
            text(
                "UPDATE auth.view_as_sessions "
                "   SET token_hash = :token_hash, handoff_hash = NULL, "
                "       handoff_expires_at = NULL "
                " WHERE handoff_hash = :h AND handoff_expires_at > now() "
                "   AND ended_at IS NULL AND expires_at > now() "
                "RETURNING uid"
            ),
            {"h": _hash(ticket), "token_hash": _hash(token)},
        )
    ).scalar_one_or_none()
    return None if redeemed is None else token


_LIVE_SELECT = (
    "SELECT v.uid, v.tenant_id, v.contact_id, v.reason, v.started_at, v.expires_at, "
    "       u.workos_user_id AS adviser_sub, "
    "       COALESCE(NULLIF(btrim(concat_ws(' ', u.first_name, u.last_name)), ''), u.email) "
    "         AS adviser_name, "
    "       c.name AS contact_name "
    "  FROM auth.view_as_sessions v "
    "  JOIN auth.users u ON u.id = v.adviser_id "
    "  JOIN crm.contacts c ON c.id = v.contact_id "
)


def _from_row(row: object) -> ViewAsSession:
    r = dict(row)  # type: ignore[call-overload]
    return ViewAsSession(
        uid=str(r["uid"]),
        tenant_id=int(r["tenant_id"]),
        adviser_sub=str(r["adviser_sub"]),
        adviser_name=str(r["adviser_name"] or ""),
        contact_id=int(r["contact_id"]),
        contact_name=str(r["contact_name"] or ""),
        reason=str(r["reason"]),
        started_at=r["started_at"],
        expires_at=r["expires_at"],
    )


async def resolve(session: AsyncSession, token: str) -> ViewAsSession | None:
    """The live session a token names, or ``None`` when it is unknown, ended or past its window.

    Expiry is evaluated by the database rather than against a timestamp carried in the token, so
    there is one clock and ending a session takes effect on the next request.
    """
    if not token.startswith(TOKEN_PREFIX):
        return None
    row = (
        (
            await session.execute(
                text(
                    _LIVE_SELECT + " WHERE v.token_hash = :h AND v.ended_at IS NULL "
                    "   AND v.expires_at > now() "
                    "   AND u.deleted_at IS NULL AND c.deleted_at IS NULL"
                ),
                {"h": _hash(token)},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or not row["adviser_sub"]:
        return None
    return _from_row(row)


async def by_uid(session: AsyncSession, session_uid: str) -> ViewAsSession | None:
    """Re-read a session by its public id -- the banner's refresh. Whatever RLS the session runs
    under applies: ``auth.view_as_sessions`` lets a login read the sessions it opened."""
    row = (
        (await session.execute(text(_LIVE_SELECT + " WHERE v.uid = :uid"), {"uid": session_uid}))
        .mappings()
        .one_or_none()
    )
    return None if row is None else _from_row(row)


async def end(session: AsyncSession, session_uid: str) -> bool:
    """Close a session now. True when this call was the one that closed it.

    Idempotent from the caller's point of view: leaving a session that has already lapsed is not
    an error, it just changes nothing.
    """
    closed = (
        await session.execute(
            text(
                "UPDATE auth.view_as_sessions SET ended_at = now() "
                " WHERE uid = :uid AND ended_at IS NULL "
                "RETURNING uid"
            ),
            {"uid": session_uid},
        )
    ).scalar_one_or_none()
    return closed is not None


def seconds_remaining(expires_at: datetime) -> int:
    """Whole seconds left in the window, floored at zero -- what the banner counts down."""
    delta = (expires_at - datetime.now(UTC)).total_seconds()
    return max(0, int(delta))


#: Methods a view-as session may use: the ones that only read.
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: The one write it may make. Leaving a session is a change, and refusing it would be the one
#: refusal that traps somebody inside the thing being restricted.
_LEAVE_PATH = "/view-as/end"


def permits(method: str, path: str) -> bool:
    """Whether a view-as session may make this request. Reads only, plus leaving.

    The session exists so that a member of staff can SEE what a client sees. Acting is a
    different thing and belongs on the staff portal under their own name, where the surfaces
    for it are built and say what they are: a client's terms accepted from inside their own
    portal would sit in the record looking exactly like the client accepting them.

    A blanket check on the method, rather than a list of endpoints to protect. Any mutating
    route that ships later is covered the day it lands, including one nobody thought about
    while reading this.
    """
    return method.upper() in _READ_METHODS or path.rstrip("/") == _LEAVE_PATH
