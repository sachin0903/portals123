"""Ask ComplyCube what has changed: the way results arrive while webhooks are off.

Polling is the main path for now; the webhook receiver stays built and, once ComplyCube is set to
call it, makes most of these questions find nothing new. Each pass asks about two sets of checks:

* **Pending** checks, once they have waited ``PENDING_WAIT`` since their last change or question.
* **Monitored** finished checks (clear, or a decided refer), once a day (``MONITORED_WAIT``), so a
  monitoring alert is seen without a webhook.

A change also refreshes the client's ComplyCube risk profile, and each pass fills in a few clients
that have none yet (``risk.py``).

For each, the check is fetched. When ComplyCube's status, mapped to ours, differs from the stored
one, the change is recorded through the same path a webhook takes, as a ``check.updated`` event
whose id names the provider's state (``poll_event_id``), and routed. Otherwise only the time of
the question is kept, in ``compliance.check_polls`` rather than on the check, so a question writes
no audit row and moves no date a firm sees.

It runs inside the event consumer's pass (``consumer.drain_once`` holds the advisory lock), so it
never records a change at the same time as the consumer. A pending check older than ``GIVE_UP`` is
no longer asked about; the console marks its client as needing attention, for a person.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.complycube import risk, routing
from app.complycube.client import RETRYABLE_STATUSES, ScreeningClient
from app.complycube.outcomes import map_status, provider_outcome
from app.complycube.sync import build_plan, record_event

logger = logging.getLogger(__name__)

BATCH = 20
#: How long a pending check waits, after its last change or question, before it is asked about.
PENDING_WAIT = timedelta(seconds=30)
#: How often a monitored finished check is asked about.
MONITORED_WAIT = timedelta(days=1)
#: A check pending this long is left for a person.
GIVE_UP = timedelta(days=7)

_SELECT_PENDING = text(
    """
    SELECT ch.id, ch.provider_check_id, ch.status
      FROM compliance.checks ch
      LEFT JOIN compliance.check_polls p ON p.check_id = ch.id
     WHERE ch.status = 'pending'
       AND ch.provider_check_id IS NOT NULL
       AND ch.created_at > now() - make_interval(secs => :give_up)
       AND GREATEST(p.polled_at, ch.updated_at) < now() - make_interval(secs => :wait)
     ORDER BY GREATEST(p.polled_at, ch.updated_at)
     LIMIT :limit
       FOR UPDATE OF ch SKIP LOCKED
    """
)

_SELECT_MONITORED = text(
    """
    SELECT ch.id, ch.provider_check_id, ch.status
      FROM compliance.checks ch
      LEFT JOIN compliance.check_polls p ON p.check_id = ch.id
     WHERE ch.monitoring
       AND ch.kind = 'aml'
       AND (ch.status = 'clear' OR (ch.status = 'refer' AND ch.decided_at IS NOT NULL))
       AND ch.provider_check_id IS NOT NULL
       AND GREATEST(p.polled_at, ch.updated_at) < now() - make_interval(secs => :wait)
     ORDER BY GREATEST(p.polled_at, ch.updated_at)
     LIMIT :limit
       FOR UPDATE OF ch SKIP LOCKED
    """
)

_STAMP = text(
    "INSERT INTO compliance.check_polls (check_id) VALUES (:id) "
    "ON CONFLICT (check_id) DO UPDATE SET polled_at = now()"
)


@dataclass
class PollResult:
    asked: int = 0
    changed: int = 0
    routed: int = 0
    failed: int = 0
    deferred: bool = False


def poll_event_id(
    provider_check_id: str,
    provider_status: str | None,
    outcome: str | None,
    updated_at: str | None = None,
) -> str:
    """The id of the event a poll records: one per provider state of a check. ComplyCube's own
    ``updatedAt`` tells two visits to the same outcome apart (a monitoring alert after a clear)."""
    state = f"{provider_status or '-'}:{outcome or '-'}"
    return (
        f"poll:{provider_check_id}:{state}:{updated_at}"
        if updated_at
        else f"poll:{provider_check_id}:{state}"
    )


def _transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUSES
    return isinstance(exc, httpx.TransportError)


def _text(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    return value if isinstance(value, str) and value else None


async def _ask(
    session: AsyncSession, client: ScreeningClient, row: Any, result: PollResult
) -> None:
    """Ask about one check and record what changed. Raises what the provider raised."""
    provider_check_id = str(row.provider_check_id)
    check = await client.get_check(provider_check_id)
    body = check if isinstance(check, dict) else {}
    status = _text(body, "status")
    outcome = provider_outcome(body)
    if map_status(status, outcome) != row.status:
        event = {
            "id": poll_event_id(provider_check_id, status, outcome, _text(body, "updatedAt")),
            "type": "check.updated",
            "payload": {"id": provider_check_id, "status": status},
        }
        plan = await build_plan(client, event)
        change = await record_event(session, plan)
        if change.old != change.new:
            result.changed += 1
        if plan.check_pk is not None:
            record = await routing.load_check(
                session,
                plan.check_pk,
                event_type=plan.event_type,
                provider_event_id=plan.provider_event_id,
            )
            if record is not None and await routing.route_result(
                session, record, change.old, change.new
            ):
                result.routed += 1
            if change.old != change.new:
                await risk.refresh_for_check(session, client, plan.check_pk)
    await session.execute(_STAMP, {"id": row.id})


async def _ask_each(
    session: AsyncSession, client: ScreeningClient, rows: list[Any], result: PollResult
) -> None:
    for row in rows:
        result.asked += 1
        savepoint = await session.begin_nested()
        try:
            await _ask(session, client, row, result)
            await savepoint.commit()
        except Exception as exc:  # noqa: BLE001 - one check must not stop the pass
            await savepoint.rollback()
            if _transient(exc):
                result.deferred = True
                logger.warning(
                    "screening provider unavailable; checks are asked about next pass",
                    extra={"event": "complycube_poll_deferred"},
                )
                return
            # Stamped anyway, so one check the provider will not answer for does not hold the
            # front of the queue on every pass.
            await session.execute(_STAMP, {"id": row.id})
            result.failed += 1
            logger.error(
                "check %s could not be asked about: %s",
                row.id,
                type(exc).__name__,
                extra={"event": "complycube_poll_failed"},
            )


async def _due(session: AsyncSession, query: TextClause, params: dict[str, Any]) -> list[Any]:
    return list((await session.execute(query, params)).all())


async def poll_once(
    session: AsyncSession,
    client: ScreeningClient,
    *,
    limit: int = BATCH,
    pending_wait: timedelta = PENDING_WAIT,
    monitored_wait: timedelta = MONITORED_WAIT,
) -> PollResult:
    """Ask about the due pending checks, then the due monitored ones, inside the caller's
    transaction. Up to ``limit`` of each."""
    result = PollResult()
    pending = await _due(
        session,
        _SELECT_PENDING,
        {
            "give_up": GIVE_UP.total_seconds(),
            "wait": pending_wait.total_seconds(),
            "limit": limit,
        },
    )
    await _ask_each(session, client, pending, result)
    if result.deferred:
        return result
    monitored = await _due(
        session,
        _SELECT_MONITORED,
        {"wait": monitored_wait.total_seconds(), "limit": limit},
    )
    await _ask_each(session, client, monitored, result)
    if not result.deferred:
        await risk.backfill(session, client)
    return result
