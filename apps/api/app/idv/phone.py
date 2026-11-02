"""A one-time link so the identity photos can be taken on a phone.

The client is signed in on a computer. The camera they want is on their phone. The desktop
asks for a short link (``POST /applications/me/identity-capture/phone``); the phone opens it
and photographs the document and their face without signing in. The link is the whole of the
phone's authority: one step, the photos that step is waiting for, and then nothing.

Two secrets, and the table holds neither of them:

* the **code** is what the URL carries. It works once. Opening it spends ``code_hash`` and
  mints the session token in the same statement, so two phones racing the same link cannot
  both come away with a session;
* the **token** is what the phone page keeps in an httpOnly cookie after that. It can read
  the capture and send the photos until the window ends, or until the link is withdrawn —
  a new link for the same step withdraws the previous one, and so does going back to the
  computer.

``expires_at`` is the whole window, set when the link is made. Thirty minutes covers finding
the phone, scanning the code and taking the photos. After that both secrets are dead even if
nobody spent them.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import segno
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_actions import redact
from app.config import settings
from app.logging import current_correlation_id

#: Prefix on the session token, so a token presented anywhere else is refused rather than
#: half-recognised. The code in the URL has no prefix: it is short on purpose.
TOKEN_PREFIX = "idvphone_"  # noqa: S105 - a routing prefix, not a secret

#: The activity-log name the signed-in door already uses, so photos sent from the phone are the
#: same act as photos sent from the computer. Who did it, and that it was the phone, is on the
#: row.
PHOTOS_SUBMITTED = "journey.identity_photos_submitted"

_STEP_SQL = (
    "SELECT cs.case_id, cs.step_key, cs.participant_subject_id, cs.status AS step_status, "
    "       c.uid AS case_uid, c.execution_id, "
    "       cs.data ->> 'kyc_check_id' AS kyc_check_id, "
    "       cs.data ->> 'identity_choice' AS identity_choice, "
    "       cs.data -> 'identity_capture' AS identity_capture, "
    "       k.status AS check_status "
    "FROM flows.case_steps cs "
    "JOIN flows.cases c ON c.id = cs.case_id "
    "LEFT JOIN crm.kyc_checks k "
    "  ON k.id = CAST(NULLIF(cs.data ->> 'kyc_check_id', '') AS bigint) "
    "WHERE cs.tenant_id = :t AND cs.case_id = :c AND cs.step_key = :k "
    "  AND cs.participant_subject_id = :contact"
)


def _hash(value: str) -> str:
    """SHA-256 of a code or token. A preimage is random bytes, so a slow hash would buy
    nothing and cost one on every photo."""
    return hashlib.sha256(value.encode()).hexdigest()


def acceptable_code(code: str) -> bool:
    """Whether ``code`` is even the shape of a link we make. Anything else is refused before
    it is hashed: a request body is not a place to do unbounded work."""
    return 8 <= len(code) <= 64 and code.isascii() and " " not in code


_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def lan_address() -> str | None:
    """An address a phone on this network can open.

    A UDP connect does not send a packet; it only asks the stack which local address it
    would use for a route off the machine. Loopback is not that address.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            address = sock.getsockname()[0]
    except OSError:
        return None
    if not isinstance(address, str) or not address or address.startswith("127."):
        return None
    return address


def reachable_base(base: str) -> str:
    """A portal base a phone can open.

    The desktop is on localhost, which a phone cannot route to. When the configured base is
    a loopback host, the link uses this machine's LAN address and keeps the scheme and port,
    so it is the same dev server. Any other host is left as it was.
    """
    trimmed = base.strip().rstrip("/")
    parts = urlsplit(trimmed if "://" in trimmed else f"http://{trimmed}")
    host = (parts.hostname or "").lower()
    if host not in _LOOPBACK:
        return trimmed
    lan = lan_address()
    if lan is None:
        return trimmed
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme or "http", f"{lan}{port}", "", "", "")).rstrip("/")


def qr_svg(url: str) -> str:
    """An SVG QR code of ``url``, with a white ground so it scans in either colour scheme.

    The URL is not written into the SVG as text. The desktop shows the link beside the code;
    the image is only the code.
    """
    return segno.make(url, error="m").svg_inline(scale=4, light="#fff", dark="#000", svgclass=None)


@dataclass(frozen=True)
class MintedLink:
    """The code, once. The caller builds the URL and then forgets the code."""

    code: str
    expires_at: datetime


@dataclass(frozen=True)
class PhoneLink:
    """A live phone session: the step it may photograph, and the person who asked for it."""

    id: int
    uid: str
    tenant_id: int
    case_id: int
    case_uid: str
    step_key: str
    contact_id: int
    user_id: str
    workos_user_id: str
    expires_at: datetime


def seconds_remaining(expires_at: datetime) -> int:
    """Whole seconds left in the window, floored at zero."""
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return max(0, int((expires_at - datetime.now(UTC)).total_seconds()))


async def withdraw(
    session: AsyncSession,
    *,
    tenant_id: int,
    case_id: int,
    step_key: str,
    contact_id: int,
) -> bool:
    """Spend every live link for this step, codes and sessions alike. True when one was live.

    A new link does this first, so an old QR stops working the moment a new one is on screen.
    Going back to the computer does it on its own, so the phone cannot send a photo after the
    client has decided to take them here instead.
    """
    spent = (
        await session.execute(
            text(
                "UPDATE auth.identity_phone_links "
                "   SET spent_at = now(), code_hash = NULL, token_hash = NULL "
                " WHERE tenant_id = :t AND case_id = :c AND step_key = :k "
                "   AND contact_id = :contact AND spent_at IS NULL "
                "RETURNING id"
            ),
            {"t": tenant_id, "c": case_id, "k": step_key, "contact": contact_id},
        )
    ).first()
    return spent is not None


async def mint(
    session: AsyncSession,
    *,
    tenant_id: int,
    case_id: int,
    step_key: str,
    contact_id: int,
    user_id: str,
) -> MintedLink:
    """Make a link for this step, withdrawing any it replaces.

    The code is returned and not stored. What is stored is its hash, and the moment the
    phone opens the link that hash is cleared.
    """
    await withdraw(
        session,
        tenant_id=tenant_id,
        case_id=case_id,
        step_key=step_key,
        contact_id=contact_id,
    )
    code = secrets.token_urlsafe(12)
    row = (
        await session.execute(
            text(
                "INSERT INTO auth.identity_phone_links "
                "  (tenant_id, case_id, step_key, contact_id, user_id, code_hash, expires_at) "
                "VALUES (:t, :c, :k, :contact, CAST(:user AS uuid), :hash, "
                "        now() + make_interval(mins => :mins)) "
                "RETURNING expires_at"
            ),
            {
                "t": tenant_id,
                "c": case_id,
                "k": step_key,
                "contact": contact_id,
                "user": user_id,
                "hash": _hash(code),
                "mins": settings.identity_phone_minutes,
            },
        )
    ).scalar_one()
    return MintedLink(code=code, expires_at=row)


async def redeem(session: AsyncSession, code: str) -> str | None:
    """Spend ``code`` and mint the phone's session token, or None when the code cannot be spent.

    One statement does both, so the loser of a race matches no row. The token is created here
    rather than retrieved: until this call there is no token, and after it the code is gone.
    """
    if not acceptable_code(code):
        return None
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    redeemed = (
        await session.execute(
            text(
                "UPDATE auth.identity_phone_links "
                "   SET token_hash = :token_hash, code_hash = NULL, redeemed_at = now() "
                " WHERE code_hash = :h AND spent_at IS NULL AND expires_at > now() "
                "RETURNING id"
            ),
            {"h": _hash(code), "token_hash": _hash(token)},
        )
    ).scalar_one_or_none()
    return None if redeemed is None else token


def _link_from(row: Any) -> PhoneLink:
    return PhoneLink(
        id=int(row["id"]),
        uid=str(row["uid"]),
        tenant_id=int(row["tenant_id"]),
        case_id=int(row["case_id"]),
        case_uid=str(row["case_uid"]),
        step_key=str(row["step_key"]),
        contact_id=int(row["contact_id"]),
        user_id=str(row["user_id"]),
        workos_user_id=str(row["workos_user_id"]),
        expires_at=row["expires_at"],
    )


_RESOLVE_SQL = (
    "SELECT l.id, l.uid, l.tenant_id, l.case_id, l.step_key, l.contact_id, "
    "       l.user_id, l.expires_at, c.uid AS case_uid, u.workos_user_id "
    "  FROM auth.identity_phone_links l "
    "  JOIN flows.cases c ON c.id = l.case_id "
    "  JOIN auth.users u ON u.id = l.user_id "
    "  JOIN crm.contacts ct ON ct.id = l.contact_id "
    " WHERE l.token_hash = :h AND l.spent_at IS NULL AND l.expires_at > now() "
    "   AND u.deleted_at IS NULL AND ct.deleted_at IS NULL"
)


async def resolve(session: AsyncSession, token: str) -> PhoneLink | None:
    """The live session ``token`` names, or None.

    None when it is unknown, withdrawn, or past its window.
    """
    if not token.startswith(TOKEN_PREFIX):
        return None
    row = (await session.execute(text(_RESOLVE_SQL), {"h": _hash(token)})).mappings().one_or_none()
    return None if row is None else _link_from(row)


async def publish_claims(session: AsyncSession, link: PhoneLink) -> None:
    """Publish the signed-in client's claims, narrowed to the one contact this link is for.

    The photo upload and the submit are the same functions the signed-in page uses, and those
    functions trust ``auth.contact_scope()`` and ``auth.uid()``. This is that scope: the person
    who asked for the link, and only the client the photos are of. ``portal`` is ``client`` so
    the fulfilment's chat echo treats the sender as the client.
    """
    claims = {
        "sub": link.workos_user_id,
        "user_uuid": link.user_id,
        "tenant_id": link.tenant_id,
        "contact_ids": [link.contact_id],
        "account_ids": [],
        "portal": "client",
        "role": "client",
        "permissions": [],
    }
    await session.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps(claims)},
    )


async def step_row(session: AsyncSession, link: PhoneLink) -> dict[str, Any] | None:
    """The step this link was made for, including one the check has since finished.

    Addressed by the link's own case, step and contact — never by a search of the client's
    steps — so the token cannot be aimed at a different journey.
    """
    row = (
        (
            await session.execute(
                text(_STEP_SQL),
                {
                    "t": link.tenant_id,
                    "c": link.case_id,
                    "k": link.step_key,
                    "contact": link.contact_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else dict(row)


async def record(
    session: AsyncSession,
    link: PhoneLink,
    *,
    event_type: str,
    route: str,
    subject: dict[str, Any] | None = None,
) -> None:
    """Append the activity row for a photo the phone sent.

    The phone routes are called by the portal's own server, which holds the session token and
    not a person's login, so the request middleware has nobody to name. The person is the one
    who asked for the link: their user is on the row, and this writes the same event the
    signed-in door would have written for the same act.
    """
    metadata: dict[str, Any] = {"correlation_id": current_correlation_id()}
    if subject:
        metadata["subject"] = redact(subject)
    await session.execute(
        text(
            "INSERT INTO audit.actions "
            "  (event_type, tenant_id, target_tenant_id, actor_id, actor_sub, "
            "   method, route, status, path_params, metadata) "
            "VALUES (:event_type, :tenant_id, NULL, CAST(:actor_id AS uuid), :actor_sub, "
            "  'POST', :route, 200, '{}'::jsonb, CAST(:metadata AS jsonb))"
        ),
        {
            "event_type": event_type,
            "tenant_id": link.tenant_id,
            "actor_id": link.user_id,
            "actor_sub": link.workos_user_id,
            "route": route,
            "metadata": json.dumps(metadata),
        },
    )
