"""The approval gate on a PROVIDER action — the lifecycle around it that the engine does not do.

A book review (``app/reviews``) records ``review.requested`` when it is held and ``review.approved``
/ ``review.rejected`` when it is decided, and the notifications tail those: the approver roles hear
that a case is waiting, the proposer hears the outcome. A provider action held at its gate — a
payment an approver role holds, an account opening gated as a composite's child — went through
``engine.start`` / ``before_child`` and recorded nothing of the kind, so its approvers were never
told and its requester never heard the decision. This module is that missing half: the same thin
lifecycle events, written from the engine's own ``run.held`` / ``run.approved`` / ``run.rejected``
hooks, plus the two facts an approvals screen needs about a gate — whether anyone can actually
decide it, and a reminder to the people who can.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.runs import ActionError

#: How often the approvers of one held run may be reminded. A reminder is a second notice to the
#: same people; one an hour is a nudge, one a minute is noise.
REMIND_INTERVAL = timedelta(hours=1)

#: The engine's event for a reminder — read back by the run detail (``approval.reminded_at``).
REMINDED_EVENT = "run.reminded"


def eligible_sql(roles_expr: str, requester_expr: str) -> str:
    """The SQL for "how many people, other than the requester, could decide this gate": the
    active platform members holding any of the approver roles. NULL when the gate names no role
    (anyone but the requester may decide), so a screen can tell "anyone" from "nobody".

    Both expressions are SQL over the row being read (``held.approver_role_ids`` and
    ``r.requested_by`` on the listing; the run's own columns on the approvals queue)."""
    return (  # noqa: S608 - both expressions are the module's own column names, never input
        f"CASE WHEN cardinality({roles_expr}) = 0 THEN NULL ELSE ("  # noqa: S608 - column names
        "  SELECT count(DISTINCT m.user_id) FROM auth.organisation_memberships m "
        "  JOIN auth.membership_roles mr ON mr.membership_id = m.id "
        "  JOIN auth.users u ON u.id = m.user_id "
        "  WHERE m.tenant_id = r.tenant_id AND m.portal = 'platform' AND m.status = 'active' "
        f"   AND u.enabled AND mr.role_id = ANY({roles_expr}) "
        f"   AND ({requester_expr} IS NULL OR m.user_id <> {requester_expr})"
        ") END"
    )


async def eligible_approvers(
    session: AsyncSession,
    *,
    tenant_id: int,
    role_ids: list[int],
    exclude_user_id: str | None,
) -> int | None:
    """How many people other than ``exclude_user_id`` hold one of ``role_ids`` — the same count
    the enable preflight makes, over every role at once. None when no role is named."""
    if not role_ids:
        return None
    row = (
        await session.execute(
            text(
                "SELECT count(DISTINCT m.user_id) FROM auth.organisation_memberships m "
                "JOIN auth.membership_roles mr ON mr.membership_id = m.id "
                "JOIN auth.users u ON u.id = m.user_id "
                "WHERE m.tenant_id = :t AND m.portal = 'platform' AND m.status = 'active' "
                "  AND u.enabled AND mr.role_id = ANY(CAST(:roles AS int[])) "
                "  AND (CAST(:x AS uuid) IS NULL OR m.user_id <> CAST(:x AS uuid))"
            ),
            {"t": tenant_id, "roles": list(role_ids), "x": exclude_user_id},
        )
    ).scalar_one()
    return int(row or 0)


async def role_slugs(session: AsyncSession, role_ids: list[int]) -> list[str]:
    if not role_ids:
        return []
    rows = (
        await session.execute(
            text("SELECT slug FROM auth.roles WHERE id = ANY(CAST(:ids AS int[])) ORDER BY slug"),
            {"ids": list(role_ids)},
        )
    ).scalars()
    return [str(slug) for slug in rows if slug]


async def notify_channels(
    session: AsyncSession, *, tenant_id: int, action_key: str
) -> tuple[bool, bool]:
    """The channels (in-app, email) the firm set on this action's policy for an approval notice.
    In-app only when no row says otherwise."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT notify_in_app, notify_email FROM actions.policies "
                    "WHERE tenant_id = :t AND action_key = :k AND provider IS NULL"
                ),
                {"t": tenant_id, "k": action_key},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return True, False
    return bool(row["notify_in_app"]), bool(row["notify_email"])


def _subject_of(run: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    subject: dict[str, Any] = {
        "action_key": run.action_key,
        "review_uid": run.uid,
        "subject_type": run.subject_kind,
        "subject_id": run.subject_uid,
    }
    if run.contact_id is not None:
        subject["contact_id"] = int(run.contact_id)
    if run.requested_by:
        subject["proposed_by"] = str(run.requested_by)
    if extra:
        subject.update({k: v for k, v in extra.items() if v is not None})
    return subject


async def notify_held(session: AsyncSession, run: Any, *, reminder: bool = False) -> None:
    """A provider action is resting at its gate: tell the approver roles, through the same
    ``review.requested`` event a held book review writes, so one notification rule covers every
    kind of hold. ``reminder`` marks a second notice for the same gate."""
    from app.reviews.events import record_review_action

    slugs = await role_slugs(session, list(run.approver_role_ids or []))
    in_app, email = await notify_channels(
        session, tenant_id=run.tenant_id, action_key=str(run.action_key)
    )
    proposer = str(run.requested_by) if run.requested_by else None
    await record_review_action(
        session,
        event_type="review.requested",
        tenant_id=run.tenant_id,
        actor_id=proposer,
        actor_sub=f"user:{proposer}" if proposer else "system:baton",
        subject=_subject_of(
            run,
            {
                # The first slug keeps the older single-role readers working; the list is what the
                # audience resolver fans out over.
                "reviewer_role_slug": slugs[0] if slugs else None,
                "reviewer_role_slugs": slugs or None,
                "notify_in_app": in_app,
                "notify_email": email,
                "reminder": True if reminder else None,
            },
        ),
    )


async def notify_decided(
    session: AsyncSession, run: Any, event_type: str, *, actor_id: str | None
) -> None:
    """A gate was decided on a provider action: tell the requester, through the ``review.*``
    decision events the proposer's notification already listens for."""
    from app.reviews.events import record_review_action

    outcome = "review.approved" if event_type == "run.approved" else "review.rejected"
    await record_review_action(
        session,
        event_type=outcome,
        tenant_id=run.tenant_id,
        actor_id=actor_id,
        actor_sub=f"user:{actor_id}" if actor_id else "system:baton",
        subject=_subject_of(run),
    )


async def last_reminded_at(session: AsyncSession, *, run_id: int) -> datetime | None:
    return (
        await session.execute(
            text("SELECT max(created_at) FROM baton.events WHERE run_id = :r AND event_type = :e"),
            {"r": run_id, "e": REMINDED_EVENT},
        )
    ).scalar_one_or_none()


async def remind(
    session: AsyncSession,
    *,
    tenant_id: int,
    run_uid: str,
    actor_id: str | None,
    may_manage: bool,
) -> dict[str, Any]:
    """Send the approvers of a held run their notice again. Open to the person who raised the run
    and to anyone who may manage actions; refused when the run is not at its gate, and rate
    limited to one reminder an hour per run."""
    from app.actions.baton_engine import get_engine

    engine = get_engine()
    unit = engine.store.bind(session)
    run = await engine.store.run_by_uid(unit, run_uid, tenant_id=tenant_id)
    if run is None or run.kind == "gate":
        raise ActionError("That run was not found.", status=404)
    if run.state.type.value != "paused" or run.state.name != "AwaitingApproval":
        raise ActionError("This run is not waiting for approval.", status=409)
    if not may_manage and (actor_id is None or str(run.requested_by) != str(actor_id)):
        raise ActionError(
            "Only the person who raised this, or someone who manages actions, can send a reminder.",
            status=403,
        )
    now = engine.now()
    last = await last_reminded_at(session, run_id=run.id)
    if last is not None and now - last < REMIND_INTERVAL:
        minutes = max(1, int((now - last).total_seconds() // 60))
        raise ActionError(
            f"The approvers were reminded {minutes} minute{'' if minutes == 1 else 's'} ago. "
            "Try again in an hour.",
            status=429,
        )
    await engine.store.insert_event(
        unit, run=run, event_type=REMINDED_EVENT, actor_id=actor_id, now=now
    )
    # A book review's first notice went through the reviews fold; the reminder rides the same
    # event, to the same roles, whatever kind of hold it is.
    await notify_held(session, engine.view(run), reminder=True)
    return {
        "run_uid": run.uid,
        "reminded_at": now.isoformat(),
        "roles": list(run.approver_role_ids),
    }
