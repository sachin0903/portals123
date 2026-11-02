"""ComplyCube's risk profile for each client, kept in ``compliance.client_risk``.

ComplyCube rates every client it holds (``GET /clients/{id}/riskProfile``): an ``overall`` level
(``low``, ``medium``, ``high`` or ``not_set``) and a breakdown by country, political exposure,
occupation and watchlist. The profile moves when a check of that client changes, so the worker
fetches it then (``refresh_for_check``), and a pass also fills in clients that have finished a
check but have no profile yet (``backfill``).

A profile is written only when it changed (ComplyCube's ``updatedAt`` or the level), so a repeat
fetch writes no audit row. Operations only: a firm has no access to ``compliance.client_risk``
(0227), because a rating can say a client is politically exposed or on a watchlist.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.complycube.client import ScreeningClient

logger = logging.getLogger(__name__)

#: Clients filled in per backfill pass.
BACKFILL_BATCH = 5

_CLIENT_OF_CHECK = text(
    "SELECT cl.id, cl.tenant_id, cl.provider_client_id FROM compliance.checks ch "
    "JOIN compliance.clients cl ON cl.id = ch.client_id WHERE ch.id = :check"
)

_UPSERT = text(
    """
    INSERT INTO compliance.client_risk
      (client_id, tenant_id, overall, profile, provider_updated_at)
    VALUES (:client, :tenant, :overall, CAST(:profile AS jsonb), CAST(:updated AS timestamptz))
    ON CONFLICT (client_id) DO UPDATE
       SET tenant_id = EXCLUDED.tenant_id,
           overall = EXCLUDED.overall,
           profile = EXCLUDED.profile,
           provider_updated_at = EXCLUDED.provider_updated_at
     WHERE compliance.client_risk.overall IS DISTINCT FROM EXCLUDED.overall
        OR compliance.client_risk.provider_updated_at IS DISTINCT FROM EXCLUDED.provider_updated_at
    RETURNING client_id
    """
)

_WITHOUT_PROFILE = text(
    """
    SELECT cl.id, cl.tenant_id, cl.provider_client_id
      FROM compliance.clients cl
     WHERE cl.provider_client_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM compliance.client_risk r WHERE r.client_id = cl.id)
       AND EXISTS (
         SELECT 1 FROM compliance.checks ch
          WHERE ch.client_id = cl.id AND ch.status NOT IN ('pending', 'failed')
       )
     ORDER BY cl.id
     LIMIT :limit
    """
)


def overall_of(profile: dict[str, Any]) -> str:
    """ComplyCube's overall level (``low``, ``medium``, ``high``, ``very_high`` or ``not_set``), as
    given, or ``not_set`` when it gave none. ComplyCube's reference names the field ``overallRisk``
    and its example ``overall``; both are read."""
    for key in ("overall", "overallRisk"):
        value = profile.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "not_set"


async def _store(
    session: AsyncSession,
    client: ScreeningClient,
    *,
    client_pk: int,
    tenant_id: int | None,
    provider_client_id: str,
) -> bool:
    """Fetch and keep one client's profile. True when the stored profile changed. A client
    ComplyCube has no profile for (404) is kept as ``not_set``, so it is not asked about again."""
    try:
        fetched = await client.get_risk_profile(provider_client_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        fetched = {}
    profile = fetched if isinstance(fetched, dict) else {}
    updated = profile.get("updatedAt")
    written = await session.execute(
        _UPSERT,
        {
            "client": client_pk,
            "tenant": tenant_id,
            "overall": overall_of(profile),
            "profile": json.dumps(profile),
            "updated": updated if isinstance(updated, str) and updated else None,
        },
    )
    return written.first() is not None


async def refresh_for_check(session: AsyncSession, client: ScreeningClient, check_pk: int) -> bool:
    """Refresh the profile of the client a check belongs to. Never raises: a profile is not worth
    undoing the check's own change for, so a failure is logged and the next change tries again."""
    row = (await session.execute(_CLIENT_OF_CHECK, {"check": check_pk})).first()
    if row is None or not row.provider_client_id:
        return False
    savepoint = await session.begin_nested()
    try:
        changed = await _store(
            session,
            client,
            client_pk=int(row.id),
            tenant_id=row.tenant_id,
            provider_client_id=str(row.provider_client_id),
        )
        await savepoint.commit()
        return changed
    except Exception as exc:  # noqa: BLE001 - the check's change must stand without the profile
        await savepoint.rollback()
        logger.warning(
            "risk profile for client %s could not be refreshed: %s",
            row.id,
            type(exc).__name__,
            extra={"event": "complycube_risk_refresh_failed"},
        )
        return False


async def backfill(
    session: AsyncSession, client: ScreeningClient, *, limit: int = BACKFILL_BATCH
) -> int:
    """Fetch a profile for clients that have finished a check but have none yet. Returns how many
    were stored. Stops at the first failure; the next pass tries again."""
    rows = (await session.execute(_WITHOUT_PROFILE, {"limit": limit})).all()
    stored = 0
    for row in rows:
        savepoint = await session.begin_nested()
        try:
            await _store(
                session,
                client,
                client_pk=int(row.id),
                tenant_id=row.tenant_id,
                provider_client_id=str(row.provider_client_id),
            )
            await savepoint.commit()
            stored += 1
        except Exception as exc:  # noqa: BLE001 - a missing profile is retried next pass
            await savepoint.rollback()
            logger.warning(
                "risk profile backfill stopped at client %s: %s",
                row.id,
                type(exc).__name__,
                extra={"event": "complycube_risk_backfill_failed"},
            )
            break
    return stored
