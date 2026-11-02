"""Operations tasks about a firm's screening.

A screening task belongs to OPERATIONS, not to the firm whose client it is about: the firm cannot
act on it and must not see one of its clients described as a possible sanctions match sitting in
its own queue. The client is named in the copy, and ``source_ref`` identifies what the task is
about. ``source_ref`` is also the idempotency key (``tasks_source_ref_key``), so at-least-once
callers raise one task.

Refs nest: ``check_task_ref(uid)`` names a check, and a task raised for one event on that check
uses ``f"{check_task_ref(uid)}:{suffix}"``. ``close_screening_task`` closes the ref and every ref
under it.

Runs as ``service_role``: no request principal, so every write names its tenant explicitly.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import environments
from app.flows.dispatcher import PermanentDispatchError
from app.tasks import service as tasks_service

#: The role a screening task is assigned to, within the operations tenant.
COMPLIANCE_ROLE_SLUG = "compliance"
#: ``tasks.tasks.source_kind`` for every screening task.
SOURCE_KIND = "aml"

#: A contact's name over ``c`` (crm.contacts), as the client record prints it (``clientName`` in
#: @graphene/ui): title, first name and surname, else the record's own ``name``. An organisation is
#: its ``name``. ``name`` is written once at journey kickoff and the fact-find never updates it, so
#: it must not win over the parts. NULL when the contact holds none of them.
CONTACT_NAME = (
    "CASE WHEN c.type = 'organisation' THEN NULLIF(trim(c.name), '') "
    "WHEN COALESCE(NULLIF(trim(c.first_name), ''), NULLIF(trim(c.surname), '')) IS NOT NULL "
    "THEN concat_ws(' ', NULLIF(trim(c.title), ''), NULLIF(trim(c.first_name), ''), "
    "NULLIF(trim(c.surname), '')) "
    "ELSE NULLIF(trim(c.name), '') END"
)


def check_task_ref(check_uid: str) -> str:
    """The ``source_ref`` root for tasks about one ``compliance.checks`` row."""
    return f"complycube:check:{check_uid}"


async def operations_tenant_id(session: AsyncSession) -> int | None:
    """Graphene's own tenant, or None when the core environment is not registered.

    ``environments.operations_tenant_id`` raises when there is no core environment (a dev box that
    never registered one). It becomes None here so the caller can raise a permanent failure with
    an explanatory message, rather than a bare LookupError being retried and dead-lettered.
    """
    try:
        return await environments.operations_tenant_id(session)
    except LookupError:
        return None


async def raise_screening_task(
    session: AsyncSession,
    *,
    contact_id: int,
    tenant_id: int,
    title: str,
    description: str,
    source_ref: str,
) -> None:
    """Ask a Graphene operator to act, in the operations tenant's own task inbox.

    The task has no subject: ``tasks_service`` validates a subject against the TASK's tenant, and
    this task lives in operations while the contact belongs to the firm. ``contact_id`` and
    ``tenant_id`` name the client for the caller's copy; the task carries neither.

    Raises ``PermanentDispatchError`` when there is no operations tenant or it has no compliance
    role.
    """
    del contact_id, tenant_id
    ops_tenant = await operations_tenant_id(session)
    if ops_tenant is None:
        raise PermanentDispatchError(
            "no operations tenant is configured, so an AML screening task cannot be raised"
        )
    role_id = (
        await session.execute(
            text("SELECT id FROM auth.roles WHERE tenant_id = :t AND slug = :s"),
            {"t": ops_tenant, "s": COMPLIANCE_ROLE_SLUG},
        )
    ).scalar_one_or_none()
    if role_id is None:
        raise PermanentDispatchError(
            f"the operations tenant has no {COMPLIANCE_ROLE_SLUG!r} role, so an AML screening "
            "task cannot be assigned"
        )
    await tasks_service.create_task(
        session,
        tenant_id=ops_tenant,
        assignee_kind="role",
        assignee_role_id=int(role_id),
        title=title,
        category="aml",
        description=description,
        source_kind=SOURCE_KIND,
        source_ref=source_ref,
        priority="high",
    )


async def close_screening_task(session: AsyncSession, source_ref: str) -> int:
    """Mark the open screening task with this ref, and any raised under it, done.

    Returns how many were closed. Zero when there is no operations tenant or nothing was open.
    """
    ops_tenant = await operations_tenant_id(session)
    if ops_tenant is None:
        return 0
    closed = await session.execute(
        text(
            "UPDATE tasks.tasks SET status = 'done', completed_at = now() "
            "WHERE tenant_id = :t AND source_kind = :sk "
            "  AND (source_ref = :sr OR starts_with(source_ref, :prefix)) "
            "  AND status IN ('open', 'in_progress') "
            "RETURNING id"
        ),
        {"t": ops_tenant, "sk": SOURCE_KIND, "sr": source_ref, "prefix": f"{source_ref}:"},
    )
    return len(closed.all())


async def client_label(session: AsyncSession, contact_id: int) -> tuple[str, str]:
    """``(client name, firm name)`` for task copy. Falls back to ids rather than failing."""
    row = (
        (
            await session.execute(
                text(
                    f"SELECT {CONTACT_NAME} AS who, t.name AS firm "  # noqa: S608 - fixed SQL
                    "FROM crm.contacts c JOIN auth.tenants t ON t.id = c.tenant_id "
                    "WHERE c.id = :id"
                ),
                {"id": contact_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return f"contact {contact_id}", "an unknown firm"
    return (str(row["who"] or f"contact {contact_id}"), str(row["firm"] or "an unknown firm"))
