"""Live push for the notification centre — the cross-process half messaging never needed.

Notification rows are written by the WORKER process (the tail consumer), while the SSE
subscribers live in the API process, so a notification cannot be pushed in-process the way a
message can. Instead the worker ANNOUNCES each delivery on a Postgres LISTEN/NOTIFY channel — in
the SAME transaction as the insert, so a rolled-back batch announces nothing — and every API
instance's listener fans it out to the subscribers it holds. This is the exact evolution
``app/live_push.py``'s own docstring names for scaling a live channel past one process.

The subscriber registry + ticket/push primitives are REUSED from ``app/live_push``: the routing
keys here are ``ntf:``-prefixed so they never collide with messaging's ``client:``/``staff:`` keys,
and a notification stream is a separate subscription from a messaging one. The browser is told only
THAT something changed (``{"type": "notification"}``), never what — it refetches the unread count,
the same "invalidate, don't trust the payload" discipline the messaging client uses, which is why a
stale or duplicated NOTIFY is harmless.
"""

from __future__ import annotations

import asyncio
import logging

import psycopg
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import live_push
from app.config import settings
from app.logging import report_error
from app.notifications.registry import Recipient

logger = logging.getLogger(__name__)

#: The LISTEN/NOTIFY channel the worker announces on and every API instance listens to.
CHANNEL = "notifications_realtime"
#: How long the API listener waits before reconnecting after a connection failure.
RECONNECT_SECONDS = 5
#: The contentless nudge a browser receives — see the module docstring for why it carries no data.
_PUSH: dict[str, object] = {"type": "notification"}


def user_key(user_id: str) -> str:
    """A staff recipient's key — per USER (a notification names one person, where a messaging staff
    push is per tenant)."""
    return f"ntf:user:{user_id}"


def contact_key(contact_id: int) -> str:
    """A client recipient's key — per contact."""
    return f"ntf:contact:{contact_id}"


def key_for(recipient: Recipient) -> str | None:
    """The routing key a delivered notification's recipient listens on (None if unroutable)."""
    if recipient.kind == "user" and recipient.user_id:
        return user_key(str(recipient.user_id))
    if recipient.kind == "contact" and recipient.contact_id is not None:
        return contact_key(int(recipient.contact_id))
    return None


async def announce(session: AsyncSession, recipients: list[Recipient]) -> None:
    """Announce delivered notifications on the channel, from the consumer's OWN transaction so the
    NOTIFY fires on COMMIT (a rolled-back batch announces nothing). Deduped per key — one nudge is
    enough however many rows a recipient just got, since the browser refetches regardless."""
    keys = {k for r in recipients if (k := key_for(r)) is not None}
    for key in keys:
        await session.execute(text("SELECT pg_notify(:c, :k)"), {"c": CHANNEL, "k": key})


def _listen_dsn() -> str:
    """The libpq conninfo for the raw psycopg listener — the app DSN with SQLAlchemy's ``+psycopg``
    driver marker removed."""
    return settings.database_url.replace("+psycopg", "", 1)


async def listen_forever() -> None:
    """The API-side bridge: hold one psycopg connection open, LISTEN on the channel, and push every
    announced key to the SSE subscribers THIS process holds. Reconnects on failure so a dropped
    listener never silently ends realtime for the whole instance — the client's poll fallback
    covers the gap until it recovers."""
    listen = sql.SQL("LISTEN {}").format(sql.Identifier(CHANNEL))
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(
                _listen_dsn(), autocommit=True
            ) as conn:
                await conn.execute(listen)
                logger.info("notifications realtime bridge listening on %s", CHANNEL)
                async for note in conn.notifies():
                    await live_push.push(note.payload, dict(_PUSH))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the bridge must outlive any single failure
            report_error(
                "notifications realtime bridge failed; reconnecting",
                log=logger,
                event="notifications_realtime_failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            await asyncio.sleep(RECONNECT_SECONDS)
