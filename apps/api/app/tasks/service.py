"""The tasks service — the ONE creation/lifecycle primitive every producer calls.

The HTTP router (manual creation), document routers, and the phase-2 outbox dispatcher all go
through ``create_task``: it validates the assignee/subject against the tenant, generates the uid,
and collapses automated duplicates on the ``tasks_source_ref_key`` partial unique index
(``ON CONFLICT`` → return the existing row), so at-least-once delivery upstream needs no dedupe.

Runs on whatever session the caller holds: the RLS-scoped ``tenant_session`` for request-path
callers, a ``service_role`` session for the dispatcher. Row-level audit comes from the 0001 spine
(``audit.attach`` on ``tasks.tasks``) — no manual audit writes here.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ids import new_id

ASSIGNEE_KINDS = ("user", "role", "contact")
SUBJECT_TYPES = ("contact", "account", "execution", "document")
#: Mirrors the ``tasks_category_check`` CHECK — keep the two in step (0027 declared the first
#: five; 0044 added ``aml``, which app/aml/screening.py had been passing since 0037 against a
#: tuple that did not contain it, so every operator hand-off raised TaskError here before the
#: database was even asked).
CATEGORIES = ("review", "acknowledge", "upload", "follow_up", "generic", "aml")
PRIORITIES = ("low", "normal", "high")
OPEN_STATUSES = ("open", "in_progress")


class TaskError(ValueError):
    """A validation failure the caller should surface as a 422 (bad assignee/subject/state)."""


async def _validate_assignee(
    session: AsyncSession,
    tenant_id: int,
    kind: str,
    user_id: str | None,
    role_id: int | None,
    contact_id: int | None,
) -> None:
    """The assignee must exist IN THIS TENANT: a staff user needs an active platform membership,
    a role/contact must belong to the tenant. Exactly one id, matching the kind."""
    ids = {"user": user_id, "role": role_id, "contact": contact_id}
    if kind not in ASSIGNEE_KINDS:
        raise TaskError(f"Unknown assignee kind '{kind}'")
    if ids[kind] is None or any(v is not None for k, v in ids.items() if k != kind):
        raise TaskError(f"Assignee kind '{kind}' requires exactly its own id")

    if kind == "user":
        ok = (
            await session.execute(
                text(
                    "SELECT 1 FROM auth.organisation_memberships "
                    "WHERE user_id = :u AND tenant_id = :t AND portal = 'platform' "
                    "  AND status = 'active'"
                ),
                {"u": user_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
        if not ok:
            raise TaskError("Assignee user has no active platform membership in this tenant")
    elif kind == "role":
        ok = (
            await session.execute(
                text("SELECT 1 FROM auth.roles WHERE id = :r AND tenant_id = :t"),
                {"r": role_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
        if not ok:
            raise TaskError("Assignee role does not belong to this tenant")
    else:
        ok = (
            await session.execute(
                text("SELECT 1 FROM crm.contacts WHERE id = :c AND tenant_id = :t"),
                {"c": contact_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
        if not ok:
            raise TaskError("Assignee contact does not belong to this tenant")


_SUBJECT_CHECKS = {
    "contact": "SELECT 1 FROM crm.contacts WHERE id = CAST(:s AS int) AND tenant_id = :t",
    "account": "SELECT 1 FROM platform.accounts WHERE id = CAST(:s AS int) AND tenant_id = :t",
    "execution": "SELECT 1 FROM flows.executions WHERE uid = :s AND tenant_id = :t",
    "document": "SELECT 1 FROM documents.documents WHERE id = CAST(:s AS uuid) AND tenant_id = :t",
}


async def _validate_subject(
    session: AsyncSession, tenant_id: int, subject_type: str | None, subject_id: str | None
) -> None:
    if (subject_type is None) != (subject_id is None):
        raise TaskError("subject_type and subject_id must be provided together")
    if subject_type is None:
        return
    if subject_type not in SUBJECT_TYPES:
        raise TaskError(f"Unknown subject type '{subject_type}'")
    try:
        ok = (
            await session.execute(
                text(_SUBJECT_CHECKS[subject_type]), {"s": subject_id, "t": tenant_id}
            )
        ).scalar_one_or_none()
    except Exception as exc:  # bad cast (non-int / non-uuid subject_id)
        raise TaskError(f"Malformed subject_id for '{subject_type}'") from exc
    if not ok:
        raise TaskError(f"Subject {subject_type} '{subject_id}' not found in this tenant")


async def create_task(
    session: AsyncSession,
    *,
    tenant_id: int,
    assignee_kind: str,
    assignee_user_id: str | None = None,
    assignee_role_id: int | None = None,
    assignee_contact_id: int | None = None,
    title: str,
    category: str = "generic",
    description: str | None = None,
    subject_type: str | None = None,
    subject_id: str | None = None,
    source_kind: str = "manual",
    source_ref: str | None = None,
    due_date: str | None = None,
    priority: str = "normal",
    created_by: str | None = None,
) -> dict[str, Any]:
    """Create a task; idempotent for automated sources (same (tenant, source_kind, source_ref)
    returns the existing row instead of inserting a duplicate)."""
    title = title.strip()
    if not title:
        raise TaskError("Title is required")
    if category not in CATEGORIES:
        raise TaskError(f"Unknown category '{category}'")
    if priority not in PRIORITIES:
        raise TaskError(f"Unknown priority '{priority}'")
    await _validate_assignee(
        session, tenant_id, assignee_kind, assignee_user_id, assignee_role_id, assignee_contact_id
    )
    await _validate_subject(session, tenant_id, subject_type, subject_id)

    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO tasks.tasks (uid, tenant_id, subject_type, subject_id, "
                    "  source_kind, source_ref, assignee_kind, assignee_user_id, "
                    "  assignee_role_id, assignee_contact_id, category, title, description, "
                    "  due_date, priority, created_by) "
                    "VALUES (:uid, :t, :sty, :sid, :sk, :sr, :ak, :au, :ar, :ac, :cat, :title, "
                    "  :descr, CAST(:due AS date), :prio, :by) "
                    "ON CONFLICT (tenant_id, source_kind, source_ref) "
                    "  WHERE source_kind <> 'manual' AND source_ref IS NOT NULL DO NOTHING "
                    "RETURNING *"
                ),
                {
                    "uid": new_id(),
                    "t": tenant_id,
                    "sty": subject_type,
                    "sid": subject_id,
                    "sk": source_kind,
                    "sr": source_ref,
                    "ak": assignee_kind,
                    "au": assignee_user_id,
                    "ar": assignee_role_id,
                    "ac": assignee_contact_id,
                    "cat": category,
                    "title": title,
                    "descr": description,
                    "due": due_date,
                    "prio": priority,
                    "by": created_by,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:  # conflict path: the automated source already raised this task
        row = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM tasks.tasks "
                        "WHERE tenant_id = :t AND source_kind = :sk AND source_ref = :sr"
                    ),
                    {"t": tenant_id, "sk": source_kind, "sr": source_ref},
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


async def _transition(
    session: AsyncSession, uid: str, to_status: str, extra_sql: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Move an open task to a terminal/working status; 'gone or already settled' raises."""
    row = (
        (
            await session.execute(
                text(
                    f"UPDATE tasks.tasks SET status = :to{extra_sql} "  # noqa: S608 — literals
                    "WHERE uid = :uid AND status IN ('open', 'in_progress') RETURNING *"
                ),
                {"to": to_status, "uid": uid, **params},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise TaskError("Task not found, not visible, or already settled")
    return dict(row)


async def complete_task(
    session: AsyncSession,
    uid: str,
    *,
    completed_by: str | None,
    completion_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _transition(
        session,
        uid,
        "done",
        ", completed_by = :by, completed_at = now(), completion_data = CAST(:data AS jsonb)",
        {"by": completed_by, "data": json.dumps(completion_data) if completion_data else None},
    )


async def cancel_task(session: AsyncSession, uid: str) -> dict[str, Any]:
    return await _transition(session, uid, "cancelled", "", {})


async def reassign_task(
    session: AsyncSession,
    uid: str,
    *,
    tenant_id: int,
    assignee_kind: str,
    assignee_user_id: str | None = None,
    assignee_role_id: int | None = None,
    assignee_contact_id: int | None = None,
) -> dict[str, Any]:
    """Point an unsettled task at a different assignee (validated against the tenant).

    Clearing ``notified_at`` is the point: it is the notifier's "this assignee has been told" stamp,
    so leaving it set means a reassigned task never emails its NEW assignee. The near-due reminder
    stamp resets too — the new owner gets their own warning."""
    await _validate_assignee(
        session, tenant_id, assignee_kind, assignee_user_id, assignee_role_id, assignee_contact_id
    )
    row = (
        (
            await session.execute(
                text(
                    "UPDATE tasks.tasks SET assignee_kind = :ak, assignee_user_id = :au, "
                    "  assignee_role_id = :ar, assignee_contact_id = :ac, "
                    "  notified_at = NULL, due_soon_notified_at = NULL "
                    "WHERE uid = :uid AND status IN ('open', 'in_progress') RETURNING *"
                ),
                {
                    "ak": assignee_kind,
                    "au": assignee_user_id,
                    "ar": assignee_role_id,
                    "ac": assignee_contact_id,
                    "uid": uid,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise TaskError("Task not found, not visible, or already settled")
    return dict(row)
