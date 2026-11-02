"""Firm webhook deliveries: the worker's pass over ``compliance.webhook_deliveries``.

``routing.queue_partner_delivery`` queues one row per firm-visible status change. One pass:

1. claims up to ``BATCH`` due rows (``pending`` and ``next_attempt_at <= now()``) with
   ``FOR UPDATE SKIP LOCKED`` and leases them by moving ``next_attempt_at`` ``LEASE_SECONDS``
   ahead, in one short transaction. Two workers never send the same row at once, and a worker
   that dies mid-send leaves the row due again rather than lost;
2. sends each row outside any transaction, all of the batch at once. The body is
   ``{"event", "uid", "kind", "status"}`` and is signed with the firm's current secret
   (``X-Graphene-Signature``, ``signature.sign``). It goes to the firm's current URL over the
   pinned transport (``webhook_url``), with ``TIMEOUT_SECONDS`` for the whole attempt, DNS
   included, and redirects are not followed;
3. records every outcome in a second short transaction. A 2xx is ``sent`` with ``sent_at``.
   Anything else counts an attempt and waits ``BACKOFF_SECONDS[attempts - 1]``; when the waits
   run out the row is ``failed`` with the reason in ``last_error``. A row whose firm has no
   webhook is ``failed`` at once.

Delivery is at least once: a firm can see the same ``X-Graphene-Delivery`` twice, and two
changes to one check can arrive out of order. ``GET /checks/{uid}`` stays the source of truth.

Runs as ``service_role``. The response body is never read.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import crypto
from app.complycube.signature import sign
from app.complycube.webhook_url import (
    PinnedTransport,
    Resolver,
    UnsafeWebhookUrl,
    check_syntax,
    resolve_public,
)
from app.config import settings

logger = logging.getLogger(__name__)

BATCH = 20
TIMEOUT_SECONDS = 10.0
#: Longer than one whole batch takes, so a row is never leased twice by a live worker.
LEASE_SECONDS = 120
#: The wait after the 1st, 2nd, ... failed attempt. One more failure after the last is final.
BACKOFF_SECONDS: tuple[int, ...] = (60, 300, 1800, 7200, 21600, 86400)
MAX_ATTEMPTS = len(BACKOFF_SECONDS) + 1
USER_AGENT = "Graphene-Webhooks/1"
SIGNATURE_HEADER = "X-Graphene-Signature"
DELIVERY_HEADER = "X-Graphene-Delivery"
NO_WEBHOOK = "The firm has no webhook."

TransportFactory = Callable[[str], httpx.AsyncBaseTransport]

_CLAIM = text(
    """
    WITH due AS (
      SELECT id
        FROM compliance.webhook_deliveries
       WHERE status = 'pending' AND next_attempt_at <= now()
       ORDER BY next_attempt_at, id
       LIMIT :limit
       FOR UPDATE SKIP LOCKED
    )
    UPDATE compliance.webhook_deliveries d
       SET next_attempt_at = now() + make_interval(secs => :lease)
      FROM due
     WHERE d.id = due.id
    RETURNING d.id, d.uid, d.tenant_id, d.webhook_id, d.event, d.payload, d.attempts
    """
)

_TARGETS = text(
    """
    SELECT d.id, w.url, w.secret_ct, ch.kind
      FROM compliance.webhook_deliveries d
      LEFT JOIN compliance.webhooks w
        ON w.id = d.webhook_id AND w.tenant_id = d.tenant_id
      LEFT JOIN compliance.checks ch
        ON ch.uid = d.payload ->> 'uid' AND ch.tenant_id = d.tenant_id
     WHERE d.id = ANY(:ids)
    """
)

_SENT = text(
    """
    UPDATE compliance.webhook_deliveries
       SET status = 'sent', attempts = :attempts, sent_at = now(), last_error = NULL,
           next_attempt_at = now()
     WHERE id = :id
    """
)

_FAILED = text(
    """
    UPDATE compliance.webhook_deliveries
       SET status = CASE WHEN :final THEN 'failed' ELSE 'pending' END,
           attempts = :attempts,
           last_error = :error,
           next_attempt_at = now() + make_interval(secs => :wait)
     WHERE id = :id
    """
)


@dataclass(frozen=True)
class Delivery:
    """One claimed row and where it goes."""

    id: int
    uid: str
    event: str
    check_uid: str | None
    kind: str | None
    status: str | None
    attempts: int
    url: str | None
    secret_ct: str | None


@dataclass(frozen=True)
class Outcome:
    delivered: bool
    error: str | None = None
    #: True when no retry can help (the firm has no webhook).
    final: bool = False


@dataclass
class PassResult:
    claimed: int = 0
    sent: int = 0
    retrying: int = 0
    failed: int = 0


def signing_configured() -> bool:
    """A delivery is signed with a secret sealed under ``SECRETS_MASTER_KEY``."""
    return bool(settings.secrets_master_key.strip())


def body_for(delivery: Delivery) -> bytes:
    """The exact bytes sent and signed."""
    payload = {
        "event": delivery.event,
        "uid": delivery.check_uid,
        "kind": delivery.kind,
        "status": delivery.status,
    }
    return json.dumps(payload, separators=(",", ":")).encode()


def next_step(attempts: int) -> tuple[bool, int]:
    """After ``attempts`` failed tries: whether that was the last, and the wait before the next."""
    if attempts >= MAX_ATTEMPTS:
        return True, 0
    return False, BACKOFF_SECONDS[attempts - 1]


def _describe(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError | httpx.TimeoutException):
        return f"The endpoint did not answer within {TIMEOUT_SECONDS:g} seconds."
    if isinstance(exc, UnsafeWebhookUrl):
        return f"The webhook URL was refused: {exc}"
    if isinstance(exc, crypto.InvalidToken):
        return "The webhook signing secret could not be read."
    if isinstance(exc, httpx.HTTPError):
        return f"The endpoint could not be reached ({type(exc).__name__})."
    first = str(exc).splitlines()[0] if str(exc) else ""
    return f"{type(exc).__name__}: {first}"[:500]


def _status_error(code: int) -> str:
    if 300 <= code < 400:
        return f"The endpoint answered {code}. Redirects are not followed."
    return f"The endpoint answered {code}."


def _pinned(address: str) -> httpx.AsyncBaseTransport:
    return PinnedTransport(address)


async def send(
    delivery: Delivery,
    *,
    resolver: Resolver | None = None,
    transport: TransportFactory = _pinned,
    clock: Callable[[], float] = time.time,
) -> Outcome:
    """One attempt. Never raises; the outcome says what happened."""
    if not delivery.url or not delivery.secret_ct:
        return Outcome(False, NO_WEBHOOK, final=True)
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            secret = crypto.decrypt(delivery.secret_ct)
            parsed = check_syntax(delivery.url)
            addresses = await resolve_public(parsed.host, parsed.port or 443, resolver=resolver)
            body = body_for(delivery)
            headers = {
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                SIGNATURE_HEADER: sign(secret, body, int(clock())),
                DELIVERY_HEADER: delivery.uid,
            }
            async with (
                httpx.AsyncClient(
                    transport=transport(addresses[0]),
                    timeout=httpx.Timeout(TIMEOUT_SECONDS),
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream("POST", parsed, content=body, headers=headers) as response,
            ):
                code = response.status_code
    except Exception as exc:  # noqa: BLE001 - every failure is a recorded attempt
        return Outcome(False, _describe(exc))
    if 200 <= code < 300:
        return Outcome(True)
    return Outcome(False, _status_error(code))


def _delivery(claimed: Any, target: Any) -> Delivery:
    payload = claimed.payload if isinstance(claimed.payload, dict) else {}
    kind = payload.get("kind") or (target.kind if target is not None else None)
    return Delivery(
        id=int(claimed.id),
        uid=str(claimed.uid),
        event=str(claimed.event),
        check_uid=payload.get("uid"),
        kind=kind,
        status=payload.get("status"),
        attempts=int(claimed.attempts),
        url=target.url if target is not None else None,
        secret_ct=target.secret_ct if target is not None else None,
    )


async def claim(session: AsyncSession, *, limit: int = BATCH) -> list[Delivery]:
    """Lease up to ``limit`` due rows inside the caller's transaction."""
    claimed = (await session.execute(_CLAIM, {"limit": limit, "lease": LEASE_SECONDS})).all()
    if not claimed:
        return []
    ids = [int(row.id) for row in claimed]
    targets = {int(row.id): row for row in (await session.execute(_TARGETS, {"ids": ids})).all()}
    return [_delivery(row, targets.get(int(row.id))) for row in claimed]


async def record(session: AsyncSession, delivery: Delivery, outcome: Outcome) -> str:
    """Write one outcome. Returns the row's new status."""
    attempts = delivery.attempts + 1
    if outcome.delivered:
        await session.execute(_SENT, {"id": delivery.id, "attempts": attempts})
        return "sent"
    final, wait = (True, 0) if outcome.final else next_step(attempts)
    await session.execute(
        _FAILED,
        {
            "id": delivery.id,
            "attempts": attempts,
            "error": outcome.error,
            "final": final,
            "wait": wait,
        },
    )
    return "failed" if final else "pending"


async def deliver_once(
    maker: async_sessionmaker[AsyncSession],
    *,
    limit: int = BATCH,
    resolver: Resolver | None = None,
    transport: TransportFactory = _pinned,
    clock: Callable[[], float] = time.time,
) -> PassResult:
    """Claim, send and record one batch."""
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        batch = await claim(session, limit=limit)
    result = PassResult(claimed=len(batch))
    if not batch:
        return result
    outcomes = await asyncio.gather(
        *(send(item, resolver=resolver, transport=transport, clock=clock) for item in batch)
    )
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        for item, outcome in zip(batch, outcomes, strict=True):
            status = await record(session, item, outcome)
            if status == "sent":
                result.sent += 1
            elif status == "failed":
                result.failed += 1
                logger.warning(
                    "firm webhook delivery %s failed for good: %s",
                    item.uid,
                    outcome.error,
                    extra={"event": "firm_webhook_failed", "attempts": item.attempts + 1},
                )
            else:
                result.retrying += 1
    return result
