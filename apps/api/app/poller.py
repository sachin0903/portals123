"""Background WorkOS Events consumer loop.

Dev/self-hosted deployments aren't reachable by WorkOS webhooks, so we poll the Events API
instead — incrementally, per environment, via a persisted cursor (see app.events). Every
``WORKOS_SYNC_POLL_SECONDS`` we drain new events for each registered environment into the
mirror; creates/updates/deletes all flow through. The same tick copies live sessions for
people who have never been listed, then leaves them to ``session.created`` and
``session.revoked``. 0 disables it.

The loop runs in the worker process. An advisory lock still makes it single-writer across rolling
deploy overlap or future worker replicas (see ``_claim_environment``). A per-environment
transaction isolates failures, and the loop is best-effort: it logs and continues.
"""

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import environments, events, session_mirror
from app.config import settings
from app.tracing import traced_job

logger = logging.getLogger(__name__)

# Advisory-lock namespace for the poller (``pg_try_advisory_xact_lock(classid, objid)`` takes two
# int4s; the objid is the environment id). Arbitrary but fixed — it only has to be unique against
# the other advisory locks in this database.
POLL_LOCK_CLASSID = 0x77_6F_73_31  # "wos1"


async def _claim_environment(session: AsyncSession, env_id: int) -> bool:
    """Try to become the single writer for this environment's cursor, for this transaction.

    The poller starts with the app, so N API replicas run N pollers and every tick they race the
    same ``auth.tenant_environments.events_cursor``: both read the same cursor, both page the same
    events out of WorkOS, both apply them, both write the cursor back. The row lock on that UPDATE
    serialises the writes but not the work — the visible effects are doubled WorkOS quota, a
    duplicated first-run ``sync_tenant`` backfill (unbounded), and every event applied twice. With
    ``WORKOS_SYNC_POLL_SECONDS`` now defaulting to 60 rather than 0 that is live, not theoretical.

    **Transaction-scoped (``pg_try_advisory_xact_lock``), deliberately.** It is released by the
    COMMIT that persists the cursor, so the lock's lifetime is exactly the critical section and a
    crashed process cannot strand it. A session-scoped ``pg_advisory_lock`` would also be wrong
    under PgBouncer transaction pooling, where the next transaction may land on a different backend
    (the same reason claims are published with ``is_local => true`` in ``db.py``).

    Returns False when another process holds it — the caller skips this environment for this tick
    rather than waiting, so a slow leader can't back the whole loop up."""
    return bool(
        (
            await session.execute(
                text("SELECT pg_try_advisory_xact_lock(:classid, :objid)"),
                {"classid": POLL_LOCK_CLASSID, "objid": int(env_id)},
            )
        ).scalar_one()
    )


async def _drain_environment(
    session: AsyncSession,
    env_id: int,
    *,
    heartbeat: Callable[[], None] | None = None,
) -> int | None:
    """Drain one environment's events inside the caller's transaction, if we can claim it.
    Returns the number of events applied, or None when another process is already draining it."""
    if not await _claim_environment(session, env_id):
        return None
    return await events.consume(session, env_id, progress=heartbeat)


async def _drain_once(
    maker: async_sessionmaker[AsyncSession],
    *,
    heartbeat: Callable[[], None] | None = None,
) -> None:
    async with maker.begin() as session:
        await session.execute(text("SET LOCAL ROLE service_role"))
        env_ids = (
            (await session.execute(text("SELECT id FROM auth.tenant_environments ORDER BY id")))
            .scalars()
            .all()
        )

    for env_id in env_ids:
        try:
            if heartbeat is not None:
                heartbeat()
            async with maker() as session, session.begin():
                await session.execute(text("SET LOCAL ROLE service_role"))
                n = await _drain_environment(session, int(env_id), heartbeat=heartbeat)
            if heartbeat is not None:
                heartbeat()
            if n is None:
                logger.debug("environment %s is being drained by another process", env_id)
            else:
                if n:
                    logger.info("applied %d WorkOS event(s) for environment %s", n, env_id)
                # Outside the cursor transaction, so a list_sessions call does not hold
                # the advisory lock. Only the drain holder runs it.
                await _backfill_sessions(maker, int(env_id))
        except Exception:
            logger.exception("event consume failed for environment %s", env_id)


async def _backfill_sessions(maker: async_sessionmaker[AsyncSession], env_id: int) -> None:
    """List sessions once for people this environment has never copied.

    Failures are logged and that person stays unlisted, so the next tick tries
    again. A person already copied is not listed again.
    """
    try:
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            tenant_id = (
                await session.execute(
                    text("SELECT tenant_id FROM auth.tenant_environments WHERE id = :e"),
                    {"e": env_id},
                )
            ).scalar_one_or_none()
            if tenant_id is None:
                return
            pending = await session_mirror.pending_backfill(
                session, int(tenant_id), session_mirror.BACKFILL_BATCH
            )
            if not pending:
                return
            workos = await environments.client_for_env(session, env_id)
        for user_id, workos_user_id in pending:
            async with maker() as session, session.begin():
                await session.execute(text("SET LOCAL ROLE service_role"))
                listed = (
                    await session.execute(
                        text(
                            "SELECT sessions_synced_at, clock_timestamp() FROM auth.users "
                            "WHERE id = CAST(:id AS uuid)"
                        ),
                        {"id": user_id},
                    )
                ).one_or_none()
                if listed is None:
                    continue
                seen, listed_at = listed[0], listed[1]
            try:
                page = await workos.user_management.list_sessions(workos_user_id)
                items = [item async for item in page.auto_paging_iter()]
            except Exception:
                logger.exception("session backfill could not list sessions for %s", user_id)
                continue
            async with maker() as session, session.begin():
                await session.execute(text("SET LOCAL ROLE service_role"))
                await session_mirror.store_listed_sessions(
                    session,
                    tenant_id=int(tenant_id),
                    user_id=user_id,
                    items=items,
                    not_before=listed_at,
                    seen=seen,
                )
    except Exception:
        logger.exception("session backfill failed for environment %s", env_id)


async def run_forever(
    maker: async_sessionmaker[AsyncSession],
    *,
    heartbeat: Callable[[], None] | None = None,
) -> None:
    """Drain WorkOS events every WORKOS_SYNC_POLL_SECONDS until cancelled. No-op when disabled."""
    interval = settings.workos_sync_poll_seconds
    if interval <= 0:
        return
    logger.info("WorkOS events poller running (every %ss)", interval)
    while True:
        if heartbeat is not None:
            heartbeat()
        try:
            await traced_job("workos_sync", _drain_once(maker, heartbeat=heartbeat))
        except Exception:
            logger.exception("WorkOS events poll iteration failed")
        if heartbeat is not None:
            heartbeat()
        await asyncio.sleep(interval)
