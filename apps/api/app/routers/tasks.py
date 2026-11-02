"""Tasks — standalone action items (design record: docs/tasks.md).

Staff manage the firm's tasks here (``tenant_session``, gated ``tasks:*``): list/filter, create,
edit/reassign, cancel. The "my action items" list carries NO permission gate — RLS already
scopes it to what the caller may touch (a client only ever sees tasks assigned to their own
contacts, exactly like ``/applications/me``). Completion is split: clients stay gate-free for
the same reason, but staff must hold ``tasks:write`` (see ``complete_task``).

Creation goes through ``app.tasks.service.create_task`` — the same primitive the phase-2 outbox
dispatcher uses — so validation and source_ref idempotency live in one place.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import listing
from app.auth import Principal, get_current_principal, require_permission
from app.db import book_session
from app.db import target_tenant_session as tenant_session
from app.listing import DEFAULT_LIMIT
from app.permissions import TASKS_READ, TASKS_WRITE
from app.tasks import service

router = APIRouter(prefix="/tasks", tags=["tasks"])

#: The persisted lifecycle statuses (tasks.tasks CHECK) — the allowlist for the list filter.
_TASK_STATUSES = ("open", "in_progress", "done", "cancelled")

_SELECT = """
SELECT t.id, t.uid, t.subject_type, t.subject_id, t.source_kind, t.source_ref,
       t.assignee_kind, t.assignee_user_id, t.assignee_role_id, t.assignee_contact_id,
       COALESCE(au.name, au.email, ar.name,
                ac.name, NULLIF(TRIM(CONCAT_WS(' ', ac.first_name, ac.surname)), ''),
                ac.email) AS assignee_name,
       -- The client-record route addresses a contact by UID, never by the serial primary key, so
       -- `assignee_contact_id`/`subject_id` alone could not be turned into an href: the task
       -- surfaces had the client's NAME and no way to link it. Both uids ride along on the row
       -- rather than costing the surfaces a second round trip per task.
       ac.uid AS assignee_contact_uid,
       sc.uid AS subject_contact_uid,
       -- The subject's name, resolved the same way the assignee's is: a task ABOUT a client used
       -- to name nothing at all, so a link there would have had no text to be.
       COALESCE(sc.name, NULLIF(TRIM(CONCAT_WS(' ', sc.first_name, sc.surname)), ''),
                sc.email) AS subject_contact_name,
       t.category, t.title, t.description, t.due_date, t.priority, t.status,
       t.created_by, t.completed_by, t.completed_at, t.completion_data,
       t.created_at, t.updated_at
FROM tasks.tasks t
  LEFT JOIN auth.users au ON au.id = t.assignee_user_id
  LEFT JOIN auth.roles ar ON ar.id = t.assignee_role_id
  LEFT JOIN crm.contacts ac ON ac.id = t.assignee_contact_id
  -- The subject needs its OWN join: `subject_id` is a polymorphic text column (an execution's
  -- uid, a document's uuid, a contact's or account's serial), so it has no FK to ride on and it
  -- is unrelated to the assignee join above — a task can be about one client and assigned to a
  -- colleague. The CASE, not a bare CAST, is what keeps that safe: CASE evaluates its branches
  -- in order, so 'exe_9' is never fed to `int` and the digits guard means a malformed subject
  -- yields NULL instead of failing the whole listing.
  LEFT JOIN crm.contacts sc
    ON sc.id = CASE
         WHEN t.subject_type = 'contact' AND t.subject_id ~ '^[0-9]+$'
         THEN CAST(t.subject_id AS int)
       END
"""


class TaskOut(BaseModel):
    id: int
    uid: str
    subject_type: str | None
    subject_id: str | None
    source_kind: str
    source_ref: str | None
    assignee_kind: str
    assignee_user_id: str | None
    assignee_role_id: int | None
    assignee_contact_id: int | None
    assignee_name: str | None
    #: The assignee contact's UID, and the subject contact's when the task is ABOUT a client.
    #: Both default to None so a caller that builds a task without the joins (the create path
    #: re-reads through `_SELECT`, but tests and narrower rows do not) still validates.
    assignee_contact_uid: str | None = None
    subject_contact_uid: str | None = None
    subject_contact_name: str | None = None
    category: str
    title: str
    description: str | None
    due_date: date | None
    priority: str
    status: str
    created_by: str | None
    completed_by: str | None
    completed_at: datetime | None
    completion_data: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


def _out(row: dict[str, Any]) -> TaskOut:
    return TaskOut(
        **{
            k: (str(v) if k.endswith(("_user_id", "created_by", "completed_by")) and v else v)
            for k, v in row.items()
            if k in TaskOut.model_fields
        },
        **({"assignee_name": None} if "assignee_name" not in row else {}),
    )


async def _tenant_uid(session: AsyncSession) -> tuple[int, str | None]:
    row = (
        (await session.execute(text("SELECT auth.tenant_id() AS t, auth.uid() AS u")))
        .mappings()
        .one()
    )
    return int(row["t"]), (str(row["u"]) if row["u"] else None)


# ============================================================================
# The caller's own action items (client portal; no permission — RLS-scoped)
# ============================================================================
@router.get("/me", name="my-tasks")
async def my_tasks(
    contact_id: Annotated[
        int | None, Query(description="Narrow to ONE linked contact — the active-contact selector")
    ] = None,
    session: AsyncSession = Depends(tenant_session),
) -> list[TaskOut]:
    """Open action items assigned to the caller's own contacts (the client surface).

    ``contact_id`` narrows to ONE of them, for a login linked to more than one client — the same
    parameter ``GET /notifications``, ``/crm/accounts`` and ``/documents/requests`` already take,
    and the portal sends it on every read. Without it the action-items block listed one client's
    tasks under another client's name, because ``contact_scope()`` is EVERY linked contact.

    ANDed on top of the scope rather than replacing it: an id the caller does not hold matches
    nothing, so the filter can only ever narrow.
    """
    rows = (
        (
            await session.execute(
                text(
                    _SELECT + "WHERE t.assignee_contact_id = ANY (auth.contact_scope()) "
                    "  AND (CAST(:cid AS int) IS NULL "
                    "       OR t.assignee_contact_id = CAST(:cid AS int)) "
                    "  AND t.status IN ('open', 'in_progress') "
                    "ORDER BY t.due_date NULLS LAST, t.id"
                ),
                {"cid": contact_id},
            )
        )
        .mappings()
        .all()
    )
    return [_out(dict(r)) for r in rows]


# ============================================================================
# Staff surface (tasks:read / tasks:write)
# ============================================================================
@router.get("", name="list-tasks")
async def list_tasks(
    session: AsyncSession = Depends(book_session),
    principal: Principal = Depends(get_current_principal),
    task_status: Annotated[list[str] | None, Query()] = None,
    assignee: str | None = None,
    subject_type: str | None = None,
    subject_id: str | None = None,
    category: Annotated[list[str] | None, Query()] = None,
    priority: Annotated[list[str] | None, Query()] = None,
    q: str | None = None,
    # WHEN it is due, as a window rather than a boolean. `overdue=true` answered one question and
    # the inbox is read with three — "what is late", "what lands today", "what lands this week" —
    # so a flag could not express the last two and a second flag per window would not combine.
    # Several windows are an OR: "overdue or due today" is the morning's list.
    due: Annotated[list[str] | None, Query()] = None,
    before_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[TaskOut]:
    """List/filter the firm's tasks. ``assignee`` accepts ``me`` (assigned to the caller or a
    role they hold), ``user:{uuid}``, ``role:{id}`` or ``contact:{id}``. Keyset pagination via
    ``before_id``; ``limit`` clamped to 200.

    ``q`` searches the title and the description, and is server-side because this listing PAGES:
    the inbox holds one 200-row window and keysets backwards through the rest, so searching the
    rows in hand would answer "no task about the Ashworth transfer" when it means "not in the most
    recent 200". ``priority`` narrows to one of ``service.PRIORITIES``, allowlisted like
    ``task_status`` and ``category`` above so a typo is a 422 rather than an empty list nobody can
    tell from "no tasks".

    ``task_status`` is multi-valued (OR): the dashboard asks ``open`` and ``in_progress`` together
    so it does not have to fetch 200 of the caller's newest tasks and filter in Python. A typo
    still 422s rather than returning an empty list, which is indistinguishable from "no tasks"."""
    require_permission(principal, TASKS_READ)
    limit = max(1, min(limit, 200))
    where: list[str] = ["TRUE"]
    params: dict[str, Any] = {"limit": limit}
    # Allowlisted, not free text, and MULTI-VALUED: the filter bar offers several and the question
    # is an OR. A typo still errors rather than returning an empty list, which is
    # indistinguishable from "no tasks".
    priorities = listing.clean_many(priority) or []
    if priorities:
        bad = [p for p in priorities if p not in service.PRIORITIES]
        if bad:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"priority must be one of {', '.join(service.PRIORITIES)}",
            )
        where.append("t.priority = ANY(:prio)")
        params["prio"] = priorities
    if q is not None and q.strip():
        # The Spotlight bar's match over `_search` (uid, title, category, subject, source ref),
        # plus the description, which is prose and matched by containment only. The term is
        # escaped, so "Chase 100% of the shortfall" does not match every task in the firm.
        await session.execute(text(listing.FUZZY_THRESHOLD_SQL))
        where.append(listing.search_sql("t._search", also=("coalesce(t.description, '')",)))
        params.update(listing.search_params(q))
    statuses = listing.clean_many(task_status) or []
    if statuses:
        # Allowlisted, not free text: a typo used to return an empty list (indistinguishable from
        # "no tasks") instead of telling the caller the filter was wrong.
        bad_status = [s for s in statuses if s not in _TASK_STATUSES]
        if bad_status:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"status must be one of {', '.join(_TASK_STATUSES)}",
            )
        where.append("t.status = ANY(:st)")
        params["st"] = statuses
    categories = listing.clean_many(category) or []
    if categories:
        bad_categories = [c for c in categories if c not in service.CATEGORIES]
        if bad_categories:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"category must be one of {', '.join(service.CATEGORIES)}",
            )
        where.append("t.category = ANY(:cat)")
        params["cat"] = categories
    if subject_type is not None:
        where.append("t.subject_type = :sty")
        params["sty"] = subject_type
    if subject_id is not None:
        where.append("t.subject_id = :sid")
        params["sid"] = subject_id
    # Each window is a half-open range on `due_date`, OR-ed together. All of them exclude finished
    # work: a closed task that was late is history, not something the inbox should still be asking
    # anybody to do.
    windows = {
        "overdue": "t.due_date < CURRENT_DATE",
        "today": "t.due_date = CURRENT_DATE",
        "week": "t.due_date >= CURRENT_DATE AND t.due_date < CURRENT_DATE + 7",
    }
    wanted = [windows[w] for w in (listing.clean_many(due) or []) if w in windows]
    if wanted:
        clause = " OR ".join(f"({w})" for w in wanted)
        where.append(f"({clause}) AND t.status IN ('open', 'in_progress')")
    if before_id is not None:
        where.append("t.id < :before")
        params["before"] = before_id
    if assignee == "me":
        where.append(
            "(t.assignee_user_id = auth.uid() OR t.assignee_role_id = ANY (auth.role_ids()))"
        )
    elif assignee is not None:
        kind, _, ident = assignee.partition(":")
        if kind == "user" and ident:
            where.append("t.assignee_user_id = CAST(:au AS uuid)")
            params["au"] = ident
        elif kind == "role" and ident.isdigit():
            where.append("t.assignee_role_id = :ar")
            params["ar"] = int(ident)
        elif kind == "contact" and ident.isdigit():
            where.append("t.assignee_contact_id = :ac")
            params["ac"] = int(ident)
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="assignee must be 'me', 'user:{uuid}', 'role:{id}' or 'contact:{id}'",
            )
    sql = _SELECT + "WHERE " + " AND ".join(where) + " ORDER BY t.id DESC LIMIT :limit"
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [_out(dict(r)) for r in rows]


class CreateTask(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    assignee_kind: str
    assignee_user_id: str | None = None
    assignee_role_id: int | None = None
    # Role assignment may come by slug (how roles travel in the UI/definitions — the firm roles
    # endpoint exposes slugs, not ids); resolved to the tenant's auth.roles row server-side.
    assignee_role_slug: str | None = None
    assignee_contact_id: int | None = None
    category: str = "generic"
    description: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    due_date: date | None = None
    priority: str = "normal"


@router.post("", name="create-task", status_code=status.HTTP_201_CREATED)
async def create_task(
    body: CreateTask,
    session: AsyncSession = Depends(tenant_session),
    principal: Principal = Depends(get_current_principal),
) -> TaskOut:
    require_permission(principal, TASKS_WRITE)
    tenant_id, uid = await _tenant_uid(session)
    role_id = body.assignee_role_id
    if body.assignee_kind == "role" and role_id is None and body.assignee_role_slug:
        role_id = (
            await session.execute(
                text("SELECT id FROM auth.roles WHERE tenant_id = auth.tenant_id() AND slug = :s"),
                {"s": body.assignee_role_slug},
            )
        ).scalar_one_or_none()
        if role_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"This firm has no role '{body.assignee_role_slug}'.",
            )
    try:
        row = await service.create_task(
            session,
            tenant_id=tenant_id,
            assignee_kind=body.assignee_kind,
            assignee_user_id=body.assignee_user_id,
            assignee_role_id=int(role_id) if role_id is not None else None,
            assignee_contact_id=body.assignee_contact_id,
            title=body.title,
            category=body.category,
            description=body.description,
            subject_type=body.subject_type,
            subject_id=body.subject_id,
            due_date=body.due_date.isoformat() if body.due_date else None,
            priority=body.priority,
            created_by=uid,
        )
    except service.TaskError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return await get_task(row["uid"], session=session, principal=principal)


@router.get("/{uid}", name="get-task")
async def get_task(
    uid: str,
    session: AsyncSession = Depends(book_session),
    principal: Principal = Depends(get_current_principal),
) -> TaskOut:
    require_permission(principal, TASKS_READ)
    row = (
        (await session.execute(text(_SELECT + "WHERE t.uid = :uid"), {"uid": uid}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return _out(dict(row))


class UpdateTask(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    category: str | None = None
    due_date: date | None = None
    priority: str | None = None
    task_status: str | None = Field(default=None, alias="status")
    # Reassignment: provide assignee_kind + the matching id. A role may come by SLUG (how roles
    # travel in the UI and in flow definitions — ids are tenant-local), resolved server-side.
    assignee_kind: str | None = None
    assignee_user_id: str | None = None
    assignee_role_id: int | None = None
    assignee_role_slug: str | None = None
    assignee_contact_id: int | None = None


@router.patch("/{uid}", name="update-task")
async def update_task(
    uid: str,
    body: UpdateTask,
    session: AsyncSession = Depends(tenant_session),
    principal: Principal = Depends(get_current_principal),
) -> TaskOut:
    """Edit fields, move open ⇄ in_progress, cancel, or reassign (staff)."""
    require_permission(principal, TASKS_WRITE)
    tenant_id, _ = await _tenant_uid(session)
    role_id = body.assignee_role_id
    if body.assignee_kind == "role" and role_id is None and body.assignee_role_slug:
        role_id = (
            await session.execute(
                text("SELECT id FROM auth.roles WHERE tenant_id = auth.tenant_id() AND slug = :s"),
                {"s": body.assignee_role_slug},
            )
        ).scalar_one_or_none()
        if role_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"This firm has no role '{body.assignee_role_slug}'.",
            )
    try:
        if body.assignee_kind is not None:
            await service.reassign_task(
                session,
                uid,
                tenant_id=tenant_id,
                assignee_kind=body.assignee_kind,
                assignee_user_id=body.assignee_user_id,
                assignee_role_id=int(role_id) if role_id is not None else None,
                assignee_contact_id=body.assignee_contact_id,
            )
        if body.task_status == "cancelled":
            await service.cancel_task(session, uid)
        elif body.task_status in ("open", "in_progress"):
            moved = (
                await session.execute(
                    text(
                        "UPDATE tasks.tasks SET status = :st "
                        "WHERE uid = :uid AND status IN ('open', 'in_progress') RETURNING id"
                    ),
                    {"st": body.task_status, "uid": uid},
                )
            ).scalar_one_or_none()
            if moved is None:
                raise service.TaskError("Task not found, not visible, or already settled")
        elif body.task_status is not None:
            raise service.TaskError("status may only move to open, in_progress or cancelled here")
    except service.TaskError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    sets: list[str] = []
    params: dict[str, Any] = {"uid": uid}
    for col, val in (
        ("title", body.title.strip() if body.title else None),
        ("description", body.description),
        ("category", body.category),
        ("priority", body.priority),
    ):
        if val is not None:
            sets.append(f"{col} = :{col}")
            params[col] = val
    if body.due_date is not None:
        # A moved due date re-arms the near-due reminder — otherwise the single stamp means the
        # assignee is warned about the OLD date and never about the new one.
        sets.append("due_date = CAST(:due AS date)")
        sets.append("due_soon_notified_at = NULL")
        params["due"] = body.due_date.isoformat()
    if sets:
        updated = (
            await session.execute(
                text(f"UPDATE tasks.tasks SET {', '.join(sets)} WHERE uid = :uid RETURNING id"),  # noqa: S608
                params,
            )
        ).scalar_one_or_none()
        if updated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return await get_task(uid, session=session, principal=principal)


class TaskActivityOut(BaseModel):
    id: int
    created_at: datetime
    action: str
    detail: dict[str, Any] | None
    actor_email: str | None
    actor_name: str | None


@router.get("/{uid}/activity", name="task-activity")
async def task_activity(
    uid: str,
    session: AsyncSession = Depends(book_session),
    principal: Principal = Depends(get_current_principal),
) -> list[TaskActivityOut]:
    """The task's row-level audit trail (the 0001 spine — created/reassigned/settled), oldest
    first. Reads audit.log under the caller's tenant-scoped RLS."""
    require_permission(principal, TASKS_READ)
    task_id = (
        await session.execute(text("SELECT id FROM tasks.tasks WHERE uid = :uid"), {"uid": uid})
    ).scalar_one_or_none()
    if task_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    rows = (
        (
            await session.execute(
                text(
                    "SELECT l.id, l.created_at, l.action, l.detail, "
                    "  u.email AS actor_email, u.name AS actor_name "
                    "FROM audit.log l LEFT JOIN auth.users u ON u.id = l.user_id "
                    "WHERE l.entity_type = 'tasks.tasks' AND l.entity_id = :tid "
                    "ORDER BY l.id"
                ),
                {"tid": int(task_id)},
            )
        )
        .mappings()
        .all()
    )
    return [TaskActivityOut(**dict(r)) for r in rows]


class CompleteTask(BaseModel):
    completion_data: dict[str, Any] | None = None


@router.post("/{uid}/complete", name="complete-task")
async def complete_task(
    uid: str,
    body: CompleteTask | None = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> TaskOut:
    """Mark done. Clients carry NO permission gate — deliberately: RLS means a client can only
    ever reach tasks assigned to their own contacts, and completing their own action item is the
    whole point of the surface. STAFF, though, must hold ``tasks:write``: completion mutates the
    firm's task record, so a zero-permission staff login shouldn't be able to settle tasks just
    because tenant RLS makes them visible."""
    # Client detection mirrors search.py: derived in SQL from the caller's own claims (never a
    # request parameter), belt AND braces per 0004_portal_scope.sql — `auth.portal()` is NULL when
    # the tenant's environment classification is missing, and a NULL portal claim must not let a
    # client session fall through to the staff branch (nor vice versa).
    scope = (
        (await session.execute(text("SELECT auth.portal() AS portal, auth.role() AS role")))
        .mappings()
        .one()
    )
    is_client = scope["portal"] == "client" or scope["role"] == "client"
    if not is_client:
        require_permission(principal, TASKS_WRITE)
    _, caller = await _tenant_uid(session)
    try:
        row = await service.complete_task(
            session,
            uid,
            completed_by=caller,
            completion_data=body.completion_data if body else None,
        )
    except service.TaskError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    named = (
        (await session.execute(text(_SELECT + "WHERE t.uid = :uid"), {"uid": row["uid"]}))
        .mappings()
        .one()
    )
    return _out(dict(named))
