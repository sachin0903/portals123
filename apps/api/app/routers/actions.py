"""Firm action policies, the runs listing and detail, and the acts on a run: decide, retry,
cancel (docs/actions.md, docs/baton.md)."""

from __future__ import annotations

from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.actions import service
from app.actions.runs import (
    ActionError,
    caller_ids,
    list_settings,
    set_policy,
)
from app.auth import require_permission
from app.db import FirmContext, firm_book_session, firm_book_write_session
from app.permissions import ACTIONS_READ, ACTIONS_WRITE

router = APIRouter(prefix="/actions", tags=["actions"])


class SetActionPolicy(BaseModel):
    action_key: str
    provider: str | None = None
    enabled: bool = False
    approval_enabled: bool = False
    approval_role_slug: str | None = None
    approval_role_slugs: list[str] = Field(default_factory=list)
    role_slugs: list[str] = Field(default_factory=list)
    require_justification: bool = False
    manual: bool = False
    #: Which channels an approval notice uses. In-app on, email off by default (in-app only).
    notify_in_app: bool = True
    notify_email: bool = False


class StartRun(BaseModel):
    action_key: str
    entity_type: str
    entity_uid: str


class DecisionBody(BaseModel):
    decision: str
    note: str | None = None


class CancelBody(BaseModel):
    note: str | None = None


def _raise(exc: ActionError) -> NoReturn:
    raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.get("/catalogue", name="list-action-catalogue")
async def catalogue(
    ctx: FirmContext = Depends(firm_book_session),
) -> dict[str, Any]:
    require_permission(ctx.principal, ACTIONS_READ)
    return await list_settings(ctx.session, tenant_id=ctx.tenant_id)


@router.put("/policies", name="set-action-policy")
async def put_policy(
    body: SetActionPolicy,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    require_permission(ctx.principal, ACTIONS_WRITE)
    try:
        return await set_policy(
            ctx.session,
            tenant_id=ctx.tenant_id,
            action_key=body.action_key,
            provider=body.provider,
            enabled=body.enabled,
            approval_enabled=body.approval_enabled,
            approval_role_slug=body.approval_role_slug,
            approval_role_slugs=body.approval_role_slugs,
            role_slugs=body.role_slugs,
            require_justification=body.require_justification,
            manual=body.manual,
            notify_in_app=body.notify_in_app,
            notify_email=body.notify_email,
        )
    except ActionError as exc:
        _raise(exc)


@router.get("/runs", name="list-action-runs")
async def get_runs(
    q: str | None = None,
    state: Annotated[list[str] | None, Query()] = None,
    action_key: Annotated[list[str] | None, Query()] = None,
    action: Annotated[list[str] | None, Query()] = None,
    provider: Annotated[list[str] | None, Query()] = None,
    requested_by: Annotated[list[str] | None, Query()] = None,
    contact_uid: Annotated[list[str] | None, Query()] = None,
    contact: Annotated[list[str] | None, Query()] = None,
    held: Annotated[list[str] | None, Query()] = None,
    limit: int | None = None,
    offset: int = 0,
    sort: str | None = None,
    dir: str | None = None,
    ctx: FirmContext = Depends(firm_book_session),
) -> dict[str, Any]:
    """The runs listing across every action — root runs only, server-paged, in the
    ``{items, total, facets, facet_labels, stats}`` envelope every book listing returns. The
    filters are the UI's listing keys (`state`, `action`, `provider`, `requested_by`, `contact`,
    and the approvals seat's `held`: `over_day` · `undecidable` · `money`); `action_key` and
    `contact_uid` are the same filters under their older names."""
    require_permission(ctx.principal, ACTIONS_READ)
    from app.actions import queries

    actor, roles = await caller_ids(ctx.session)
    return await queries.list_all_runs(
        ctx.session,
        tenant_id=ctx.tenant_id,
        q=q,
        state=state,
        action_key=[*(action_key or []), *(action or [])] or None,
        provider=provider,
        requested_by=requested_by,
        contact_uid=[*(contact_uid or []), *(contact or [])] or None,
        held=held,
        limit=limit,
        offset=offset,
        sort=sort,
        direction=dir,
        caller_id=actor,
        caller_role_ids=roles,
    )


@router.get("/approvals", name="list-action-approvals")
async def get_approvals(
    ctx: FirmContext = Depends(firm_book_session),
) -> dict[str, Any]:
    require_permission(ctx.principal, ACTIONS_READ)
    from app.actions import queries

    actor, roles = await caller_ids(ctx.session)
    approvals = await queries.list_pending_approvals(
        ctx.session, tenant_id=ctx.tenant_id, caller_id=actor, caller_role_ids=roles
    )
    return {"approvals": approvals}


@router.post("/runs", name="start-action-run")
async def post_run(
    body: StartRun,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    require_permission(ctx.principal, ACTIONS_WRITE)
    actor, roles = await caller_ids(ctx.session)
    # The run is created here and the worker advances it.
    try:
        result = await service.start_from_entity(
            ctx.session,
            action_key=body.action_key,
            tenant_id=ctx.tenant_id,
            entity_type=body.entity_type,
            entity_uid=body.entity_uid,
            requested_by=actor,
            role_ids=roles,
        )
    except ActionError as exc:
        _raise(exc)
    from app.actions.queries import status_word

    # the lowercase status word the doors and the listing return, not the baton State.name
    state = result.run.state
    return {"run_uid": result.run.uid, "status": status_word(state.type.value, state.name)}


@router.get("/runs/{run_uid}", name="get-action-run")
async def get_run(
    run_uid: str,
    ctx: FirmContext = Depends(firm_book_session),
) -> dict[str, Any]:
    """One run as its tree, with the people and roles named, the client and account it is about,
    the journey step that raised it, its lifecycle events, and whether this caller may decide it."""
    require_permission(ctx.principal, ACTIONS_READ)
    from app.actions import queries

    actor, roles = await caller_ids(ctx.session)
    try:
        return await queries.run_detail(
            ctx.session,
            tenant_id=ctx.tenant_id,
            run_uid=run_uid,
            caller_id=actor,
            caller_role_ids=roles,
        )
    except ActionError as exc:
        _raise(exc)


@router.post("/runs/{run_uid}/decision", name="decide-action-run")
async def post_decision(
    run_uid: str,
    body: DecisionBody,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    actor, roles = await caller_ids(ctx.session)
    try:
        await service.decide(
            ctx.session,
            run_uid=run_uid,
            decision=body.decision,
            actor_id=actor,
            role_ids=roles,
            note=body.note,
        )
    except ActionError as exc:
        _raise(exc)
    # An approved run is scheduled; the worker runs it. A rejected run is terminal.
    return {
        "status": "rejected" if body.decision == "reject" else "approved",
        "run_uid": run_uid,
    }


@router.post("/runs/{run_uid}/remind", name="remind-action-approvers")
async def post_remind(
    run_uid: str,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    """Send the approvers of a held run their notice again — the same ``review.requested``
    notification the hold sent, marked as a reminder. The person who raised the run may nudge,
    as may anyone who manages actions; once an hour per run."""
    from app.actions import approvals
    from app.auth import is_system
    from app.permissions import satisfies

    actor, _roles = await caller_ids(ctx.session)
    may_manage = is_system(ctx.principal) or satisfies(
        set(ctx.principal.permissions), ACTIONS_READ.slug
    )
    try:
        return await approvals.remind(
            ctx.session,
            tenant_id=ctx.tenant_id,
            run_uid=run_uid,
            actor_id=actor,
            may_manage=may_manage,
        )
    except ActionError as exc:
        _raise(exc)


@router.post("/runs/{run_uid}/retry", name="retry-action-run")
async def post_retry_run(
    run_uid: str,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    """Retry the run from where it stopped. The engine revives the run and every failed or crashed
    descendant and the worker replays it: completed children return their stored results, so
    nothing already sent is sent again, and the revived ones run."""
    require_permission(ctx.principal, ACTIONS_WRITE)
    try:
        await service.retry(ctx.session, run_uid=run_uid)
    except ActionError as exc:
        _raise(exc)
    return {"run_uid": run_uid, "status": "retrying"}


@router.post("/runs/{run_uid}/steps/{step_uid}/retry", name="retry-action-step")
async def post_retry(
    run_uid: str,
    step_uid: str,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    require_permission(ctx.principal, ACTIONS_WRITE)
    # A step is a child run; retry reschedules it and its ancestors, and the worker runs them.
    try:
        await service.retry(ctx.session, run_uid=step_uid)
    except ActionError as exc:
        _raise(exc)
    return {"run_uid": run_uid, "step_uid": step_uid, "status": "retrying"}


@router.post("/runs/{run_uid}/cancel", name="cancel-action-run")
async def post_cancel(
    run_uid: str,
    body: CancelBody | None = None,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    """Stop a run that has not finished: a run resting on a provider inside its wait budget, one
    paused on its children, or one still queued. Its open children are cancelled with it. A run
    that already finished is left as it is."""
    require_permission(ctx.principal, ACTIONS_WRITE)
    actor, _roles = await caller_ids(ctx.session)
    try:
        run = await service.cancel(
            ctx.session, run_uid=run_uid, actor_id=actor, note=body.note if body else None
        )
    except ActionError as exc:
        _raise(exc)
    from app.actions.queries import status_word

    return {"run_uid": run_uid, "status": status_word(run.state.type.value, run.state.name)}
