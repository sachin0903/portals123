"""Save a form without fulfilling the step.

Submit inserts a new response and marks the request item done. Save upserts the latest
response for that item and leaves the item pending, so a long fact find can be continued.
Scoring and ``remember`` run on submit, not here.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ids import new_id


async def upsert_draft(
    session: AsyncSession,
    *,
    tenant_id: int,
    request_item_id: int,
    form_uid: str,
    contact_id: int | None,
    answers: dict[str, Any],
    submitted_by: str | None,
) -> None:
    """Write ``answers`` onto the item's latest response, or insert one when none exists.

    Does not change the request item. A later submit still appends its own row; the reader
    takes the latest, so the submission wins.
    """
    existing = (
        await session.execute(
            text(
                "SELECT id FROM documents.form_responses WHERE request_item_id = :i "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"i": request_item_id},
        )
    ).scalar_one_or_none()
    payload = json.dumps(answers)
    if existing is not None:
        await session.execute(
            text(
                "UPDATE documents.form_responses "
                "SET answers = CAST(:answers AS jsonb), submitted_by = :by, submitted_at = now() "
                "WHERE id = :id"
            ),
            {"answers": payload, "by": submitted_by, "id": int(existing)},
        )
        return
    await session.execute(
        text(
            "INSERT INTO documents.form_responses "
            "  (uid, tenant_id, request_item_id, form_uid, contact_id, answers, submitted_by) "
            "VALUES (:uid, :t, :iid, :form, :cid, CAST(:answers AS jsonb), :by)"
        ),
        {
            "uid": new_id(),
            "t": tenant_id,
            "iid": request_item_id,
            "form": form_uid,
            "cid": contact_id,
            "answers": payload,
            "by": submitted_by,
        },
    )
