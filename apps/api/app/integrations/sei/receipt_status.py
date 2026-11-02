"""Read a lodged SEI activity back and keep the local row current.

``createCurrencyReceipt`` and ``createCurrencyDisbursement`` return an activity id and
an event id. The activity is the lifecycle (``GET /activitymanagementservices/activity/{id}``).
The event id is the scheduled occurrence. Neither is a posted transaction: ``posted`` is a
credit the feed has matched. This records SEI's own state on the Graphene transaction
(``data.sei``) and, when a contribution expectation carries the same activity id, on that
expectation's response. The status column is left alone.

The nightly ``raw.api_transactions`` pull is a per-account history backfill. It is the wrong
shape for "did this receipt move off Waiting for Matching", so open activities are re-read
here, immediately after the lodge and again every fifteen minutes.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.sei.cash_receipts import SeiNotConfigured, _settings, configured

logger = logging.getLogger("app.sei")

#: How often the worker re-reads activities we have lodged and not yet matched.
POLL_SECONDS = 15 * 60


def activity_snapshot(body: dict[str, Any]) -> dict[str, Any]:
    """The status fields an activity read actually carries, with blanks dropped."""
    raw = {
        "activity_id": body.get("activityId") or body.get("activity_id"),
        "activity_status": body.get("activityStatus") or body.get("activity_status"),
        "activity_status_description": body.get("activityStatusDescription")
        or body.get("activity_status_description"),
        "activity_state": body.get("activityState") or body.get("activity_state"),
        "activity_state_description": body.get("activityStateDescription")
        or body.get("activity_state_description"),
    }
    return {key: str(value) for key, value in raw.items() if value not in (None, "")}


async def note_lodged(
    session: AsyncSession,
    *,
    activity_id: str,
    event_id: str | None = None,
) -> dict[str, Any] | None:
    """Look the activity up once and write it onto the rows that carry this id.

    A lookup failure leaves the lodge as it was. The receipt was accepted; the status
    read can be retried on the next pass.
    """
    snapshot = await fetch_activity(activity_id)
    if snapshot is None:
        return None
    if event_id:
        snapshot["event_id"] = str(event_id)
    snapshot["checked_at"] = datetime.now(UTC).isoformat()
    await store_activity(session, activity_id, snapshot)
    return snapshot


async def fetch_activity(activity_id: str) -> dict[str, Any] | None:
    """One activity read. ``None`` when SEI is not configured or the read fails."""
    if not activity_id or not configured():
        return None
    from sei import SeiApiError, SeiAuthError, SeiClient, SeiConfigError

    try:
        async with SeiClient(_settings()) as api:
            body = await api.activity(activity_id)
    except (SeiConfigError, SeiAuthError, SeiApiError, SeiNotConfigured) as exc:
        logger.warning("sei activity %s could not be read: %s", activity_id, exc)
        return None
    if not isinstance(body, dict):
        return None
    return activity_snapshot(body)


async def refresh_submitted(session: AsyncSession) -> int:
    """Re-read every open SEI activity we lodged and write the new state back.

    Open means a Graphene transaction still ``submitted``, or a contribution expectation
    still ``instructed``. One client for the batch. One refusal does not stop the rest.
    """
    if not configured():
        return 0
    ids = await _open_activity_ids(session)
    if not ids:
        return 0
    from sei import SeiApiError, SeiAuthError, SeiClient, SeiConfigError

    updated = 0
    try:
        client = SeiClient(_settings())
    except SeiConfigError as exc:
        logger.warning("sei receipt refresh skipped: %s", exc)
        return 0
    async with client as api:
        for activity_id in ids:
            try:
                body = await api.activity(activity_id)
            except (SeiAuthError, SeiApiError, SeiNotConfigured) as exc:
                logger.warning("sei activity %s could not be re-read: %s", activity_id, exc)
                continue
            if not isinstance(body, dict):
                continue
            snapshot = activity_snapshot(body)
            snapshot["checked_at"] = datetime.now(UTC).isoformat()
            await store_activity(session, activity_id, snapshot)
            updated += 1
    return updated


async def store_activity(session: AsyncSession, activity_id: str, snapshot: dict[str, Any]) -> None:
    """Write ``snapshot`` onto the Graphene rows and instructed expectations for this id."""
    payload = json.dumps(snapshot)
    await session.execute(
        text(
            "UPDATE platform.transactions t SET updated_at = now(), "
            "  data = jsonb_set(COALESCE(t.data, '{}'::jsonb), '{sei}', "
            "                   COALESCE(t.data -> 'sei', '{}'::jsonb) || CAST(:sei AS jsonb), "
            "                   true) "
            "WHERE t.source_system = 'graphene' "
            "  AND t.id IN ("
            "    SELECT l.entity_id FROM platform.links l "
            "    WHERE l.provider = 'sei' AND l.entity_type = 'transaction' "
            "      AND l.link_type = 'primary' AND l.external_id = :activity "
            "      AND l.tenant_id = t.tenant_id"
            "  )"
        ),
        {"sei": payload, "activity": activity_id},
    )
    await session.execute(
        text(
            "UPDATE platform.transaction_expectations SET updated_at = now(), "
            "  response_payload = jsonb_set(COALESCE(response_payload, '{}'::jsonb), "
            "    '{status}', COALESCE(response_payload -> 'status', '{}'::jsonb) "
            "                || CAST(:sei AS jsonb), true) "
            "WHERE provider = 'sei' AND status = 'instructed' "
            "  AND provider_reference = :activity"
        ),
        {"sei": payload, "activity": activity_id},
    )


async def _open_activity_ids(session: AsyncSession) -> list[str]:
    rows = (
        await session.execute(
            text(
                "SELECT l.external_id AS activity_id "
                "FROM platform.links l "
                "JOIN platform.transactions t "
                "  ON t.tenant_id = l.tenant_id AND t.id = l.entity_id "
                "WHERE l.provider = 'sei' AND l.entity_type = 'transaction' "
                "  AND l.link_type = 'primary' AND t.source_system = 'graphene' "
                "  AND t.status = 'submitted' "
                "UNION "
                "SELECT provider_reference "
                "FROM platform.transaction_expectations "
                "WHERE provider = 'sei' AND status = 'instructed' "
                "  AND provider_reference IS NOT NULL"
            )
        )
    ).all()
    return [str(row[0]) for row in rows if row[0]]
