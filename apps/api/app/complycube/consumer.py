"""The worker's ComplyCube event consumer: claim, fetch, record, route.

One pass is one transaction. ``pg_try_advisory_xact_lock`` makes it single-writer across rolling
deploy overlap or more worker replicas (the ``app/poller.py::_claim_environment`` shape): it is
released by the pass's own COMMIT, so a crashed process cannot strand it, and a second process
skips the pass rather than waiting.

Each event runs in a savepoint, so one bad event does not undo the rest of the batch:

* the provider is struggling (a transport error, 429 or 5xx after the client's own retries): the
  row stays unprocessed without counting a failure, and the pass stops — the next tick retries;
* anything else (a 4xx, an event with no id, a bug): the row's ``attempts`` goes up with the
  error in ``last_error``. After ``MAX_ATTEMPTS`` it is no longer claimed and waits for an
  operator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.complycube import risk, routing
from app.complycube.client import RETRYABLE_STATUSES, ScreeningClient
from app.complycube.sync import build_plan, claim_events, mark_failed, record_event

logger = logging.getLogger(__name__)

# Advisory-lock namespace (``pg_try_advisory_xact_lock(classid, objid)`` takes two int4s).
# Arbitrary but fixed; it only has to be unique against the other advisory locks here.
LOCK_CLASSID = 0x63_63_62_31  # "ccb1"
LOCK_OBJID = 1
BATCH = 20
MAX_ATTEMPTS = 5


@dataclass
class DrainResult:
    claimed: int = 0
    applied: int = 0
    routed: int = 0
    failed: int = 0
    deferred: int = 0


async def claim_consumer(session: AsyncSession) -> bool:
    """Become the single consumer for this transaction. False when another process is."""
    return bool(
        (
            await session.execute(
                text("SELECT pg_try_advisory_xact_lock(:classid, :objid)"),
                {"classid": LOCK_CLASSID, "objid": LOCK_OBJID},
            )
        ).scalar_one()
    )


def _transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUSES
    return isinstance(exc, httpx.TransportError)


def _describe(exc: BaseException) -> str:
    """One line for ``last_error`` and the log. Only the first line of the message: a database
    error's later lines carry the statement's parameters, which can hold a client's details."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"provider answered {exc.response.status_code} for {exc.request.url.path}"
    first = str(exc).splitlines()[0] if str(exc) else ""
    return f"{type(exc).__name__}: {first}"[:500]


async def drain_once(
    session: AsyncSession, client: ScreeningClient, *, limit: int = BATCH
) -> DrainResult | None:
    """Apply up to ``limit`` inbox rows inside the caller's transaction.

    None when another process holds the consumer lock.
    """
    if not await claim_consumer(session):
        return None
    result = DrainResult()
    rows = await claim_events(session, limit=limit, max_attempts=MAX_ATTEMPTS)
    result.claimed = len(rows)
    for event_pk, event in rows:
        savepoint = await session.begin_nested()
        try:
            plan = await build_plan(client, event)
            change = await record_event(session, plan, event_pk=event_pk)
            if plan.check_pk is not None:
                check = await routing.load_check(
                    session,
                    plan.check_pk,
                    event_type=plan.event_type,
                    provider_event_id=plan.provider_event_id,
                )
                if check is not None and await routing.route_result(
                    session, check, change.old, change.new
                ):
                    result.routed += 1
                if change.old != change.new:
                    await risk.refresh_for_check(session, client, plan.check_pk)
            await savepoint.commit()
            result.applied += 1
        except Exception as exc:  # noqa: BLE001 - one event must not stop the batch
            await savepoint.rollback()
            if _transient(exc):
                result.deferred += 1
                logger.warning(
                    "screening provider unavailable; event %s left for the next pass: %s",
                    event_pk,
                    _describe(exc),
                    extra={"event": "complycube_event_deferred"},
                )
                break
            await mark_failed(session, event_pk, _describe(exc))
            result.failed += 1
            logger.error(
                "screening event %s could not be applied: %s",
                event_pk,
                _describe(exc),
                extra={"event": "complycube_event_failed"},
            )
    return result
