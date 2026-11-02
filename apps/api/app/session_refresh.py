"""Copy one person's live WorkOS sessions into the local mirror.

The profile and security pages read ``auth.sessions`` and return. This runs after that
response, so ``list_sessions`` cannot hold the page. The worker's event stream still
applies ``session.created`` and ``session.revoked`` when it is running; this list is
what fills the table when that stream has not been consumed yet, and what corrects it.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from app import environments, session_mirror
from app.db import system_transaction

logger = logging.getLogger(__name__)


async def refresh_member_sessions(tenant_id: int, user_id: str) -> None:
    """List this person's live sessions and write them. Never raises.

    A miss, a firm with no WorkOS environment, or a provider error leaves the
    table as it was. The page that asked has already been sent.
    """
    try:
        async with system_transaction() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT workos_user_id, sessions_synced_at FROM auth.users "
                        "WHERE id = CAST(:u AS uuid) AND tenant_id = :t "
                        "  AND deleted_at IS NULL AND workos_user_id IS NOT NULL"
                    ),
                    {"u": user_id, "t": tenant_id},
                )
            ).one_or_none()
            if row is None:
                return
            workos_user_id, seen = row[0], row[1]
            try:
                workos = await environments.client_for_env(
                    session, await environments.primary_environment_id(session, tenant_id)
                )
            except LookupError:
                return
            listed_at = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
        page = await workos.user_management.list_sessions(str(workos_user_id))
        items = [item async for item in page.auto_paging_iter()]
        async with system_transaction() as session:
            await session_mirror.store_listed_sessions(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                items=items,
                not_before=listed_at,
                seen=seen,
            )
    except Exception:
        logger.exception("could not refresh sessions for %s", user_id)
