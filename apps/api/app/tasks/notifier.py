"""Task notices — the worker scan that turns an assigned or nearly-due task into an EVENT.

A task notice is a NOTIFICATION, not a system message, so this scan sends nothing. It appends one
``audit.actions`` row per task — ``task.assigned`` from the first scan, ``task.due_soon`` from the
reminder scan — and both tails of that stream pick it up: the bell
(``app/notifications/consumer.py``) and the email subscriber
(``app/notifications/email_consumer.py``). The copy, the channels, the recipients, the firm's own
off-switch and each person's preference therefore live in one place, the notification catalogue
(``app/notifications/catalogue.py``), exactly as they do for a document notice.

The scan runs inside the background worker (``app/worker.py``) on a service_role session, and it
lives here rather than on the creation route for two reasons: every creation path (API, the outbox
dispatcher, a flow action) funnels through the same two stamps, and "nearly due" is a fact about the
clock, not about an act anybody performed.

``notified_at`` / ``due_soon_notified_at`` (0028) stay the once-only guarantee — now for EMITTING
rather than for sending. The event is written on the same transaction as the stamp, so a
rolled-back scan re-emits and a committed one never repeats. The reminder still waits for the
assign notice (``notified_at IS NOT NULL``), so a task due tomorrow does not produce two notices in
one minute.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit_actions as actions

logger = logging.getLogger("task_notifier")

#: How close (in days) a due date must be before the reminder fires. Overdue always qualifies.
DUE_SOON_DAYS = 2
#: Worker-side scan cadence (seconds) — the worker throttles calls to this interval.
SCAN_INTERVAL_SECONDS = 60.0
_BATCH = 25

#: Route templates for the two notices. Synthetic, like the worker render's ``/documents/{uid}/
#: render``: ``audit.actions.route`` is NOT NULL and names the act, and no request performed this
#: one.
_ASSIGNED_ROUTE = "/tasks/{uid}/assigned"
_DUE_SOON_ROUTE = "/tasks/{uid}/due-soon"

_ROW_COLS = (
    "id, uid, tenant_id, assignee_kind, assignee_user_id, assignee_role_id, "
    "assignee_contact_id, due_date::text AS due_date"
)


async def _subject(session: AsyncSession, row: dict[str, Any]) -> dict[str, Any]:
    """``metadata.subject`` for one notice: what names the task, plus what
    ``notifications.registry._resolve_task_assignee`` needs to reach its assignee.

    A role is carried as its SLUG. The scan's row holds ``assignee_role_id``, and the resolver keys
    on the slug — carrying the id would leave every role-assigned task reaching nobody.

    Ids, one enum and a date only: the task's title and description are free text, which the audit
    stream deliberately refuses (``app/audit_actions.py`` → subject capture). The notice
    copy still names them — ``notifications/context.py`` reads them from ``tasks.tasks``
    off ``task_id``, which is also what keeps a renamed task correct at delivery time.
    """
    kind = str(row["assignee_kind"])
    subject: dict[str, Any] = {
        "task_id": int(row["id"]),
        "task_uid": str(row["uid"]),
        "assignee_kind": kind,
    }
    if row.get("due_date"):
        subject["due_date"] = str(row["due_date"])
    if kind == "user" and row["assignee_user_id"]:
        subject["assignee_user_id"] = str(row["assignee_user_id"])
    elif kind == "contact" and row["assignee_contact_id"] is not None:
        subject["assignee_contact_id"] = int(row["assignee_contact_id"])
    elif kind == "role" and row["assignee_role_id"]:
        slug = (
            await session.execute(
                text("SELECT slug FROM auth.roles WHERE id = :r"),
                {"r": int(row["assignee_role_id"])},
            )
        ).scalar_one_or_none()
        if slug:
            subject["assignee_role_slug"] = str(slug)
    return subject


async def _scan(
    session: AsyncSession,
    *,
    where: str,
    stamp_col: str,
    event_type: str,
    route: str,
) -> int:
    """One claim-emit-stamp pass. Both writes are on this transaction, so the stamp and the event
    cannot disagree about whether the notice was raised."""
    rows = (
        (
            await session.execute(
                text(
                    f"SELECT {_ROW_COLS} FROM tasks.tasks "  # noqa: S608 — module-literal parts
                    f"WHERE {where} ORDER BY id FOR UPDATE SKIP LOCKED LIMIT {_BATCH}"
                )
            )
        )
        .mappings()
        .all()
    )
    for r in rows:
        row = dict(r)
        await actions.record_task_action(
            session,
            event_type=event_type,
            tenant_id=int(row["tenant_id"]),
            route=route,
            task_uid=str(row["uid"]),
            subject=await _subject(session, row),
        )
        await session.execute(
            text(f"UPDATE tasks.tasks SET {stamp_col} = now() WHERE id = :id"),  # noqa: S608
            {"id": row["id"]},
        )
    if rows:
        logger.info("emitted %d %s notice(s)", len(rows), event_type)
    return len(rows)


async def notify_once(session: AsyncSession) -> int:
    """Both scans on one service_role transaction. Returns events emitted."""
    emitted = await _scan(
        session,
        where="notified_at IS NULL AND status IN ('open', 'in_progress')",
        stamp_col="notified_at",
        event_type=actions.TASK_ASSIGNED_EVENT,
        route=_ASSIGNED_ROUTE,
    )
    emitted += await _scan(
        session,
        where=(
            "due_soon_notified_at IS NULL AND notified_at IS NOT NULL "
            "AND status IN ('open', 'in_progress') AND due_date IS NOT NULL "
            f"AND due_date <= CURRENT_DATE + {DUE_SOON_DAYS}"
        ),
        stamp_col="due_soon_notified_at",
        event_type=actions.TASK_DUE_SOON_EVENT,
        route=_DUE_SOON_ROUTE,
    )
    return emitted
