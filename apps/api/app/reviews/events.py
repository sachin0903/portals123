"""Thin review-lifecycle and post-apply happened events.

These write on the CALLER's session (the hold / approve transaction) so a rolled-back
decision leaves no trail claiming it happened. Subject fields are ids and closed enums
only — never the command payload.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_actions import mark_lifecycle_event_written, redact
from app.logging import current_correlation_id


async def record_review_action(
    session: AsyncSession,
    *,
    event_type: str,
    tenant_id: int,
    actor_id: str | None,
    actor_sub: str,
    subject: dict[str, Any],
) -> None:
    metadata: dict[str, Any] = {
        "correlation_id": current_correlation_id(),
        "subject": redact(subject),
    }
    await session.execute(
        text(
            "INSERT INTO audit.actions "
            "  (event_type, tenant_id, target_tenant_id, actor_id, actor_sub, "
            "   method, route, status, path_params, metadata) "
            "VALUES (:event_type, :tenant_id, NULL, CAST(:actor_id AS uuid), :actor_sub, "
            "  'POST', :route, 200, CAST(:path_params AS jsonb), CAST(:metadata AS jsonb))"
        ),
        {
            "event_type": event_type,
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "actor_sub": actor_sub,
            "route": "/reviews",
            "path_params": json.dumps({"uid": subject.get("review_uid")}, default=str),
            "metadata": json.dumps(metadata, default=str),
        },
    )
    mark_lifecycle_event_written()


async def record_applied_action(
    session: AsyncSession,
    *,
    event_type: str,
    tenant_id: int,
    actor_id: str,
    actor_sub: str,
    review_uid: str,
    subject_type: str,
    subject_id: str,
) -> None:
    """The door's happened-event, written only after apply, attributed to the proposer.

    Automations and notifications tail this name (``client.bank_account_updated``, …).
    Bank digits stay off the subject — same redact decision as the live door.
    """
    metadata: dict[str, Any] = {
        "correlation_id": current_correlation_id(),
        "review_uid": review_uid,
        "subject": redact({"subject_type": subject_type, "subject_id": subject_id}),
    }
    await session.execute(
        text(
            "INSERT INTO audit.actions "
            "  (event_type, tenant_id, target_tenant_id, actor_id, actor_sub, "
            "   method, route, status, path_params, metadata) "
            "VALUES (:event_type, :tenant_id, NULL, CAST(:actor_id AS uuid), :actor_sub, "
            "  'POST', :route, 200, CAST(:path_params AS jsonb), CAST(:metadata AS jsonb))"
        ),
        {
            "event_type": event_type,
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "actor_sub": actor_sub,
            "route": f"/reviews/{review_uid}/approve",
            "path_params": json.dumps({"uid": review_uid}),
            "metadata": json.dumps(metadata, default=str),
        },
    )
