"""Four-eye checks — policy configuration and the review workqueue (docs/reviews.md)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.auth import require_permission
from app.db import FirmContext, firm_book_session, firm_book_write_session
from app.permissions import ACTIONS_WRITE
from app.reviews import service
from app.reviews.hold import http_error
from app.reviews.service import ReviewError

router = APIRouter(prefix="/reviews", tags=["reviews"])


class SetPolicyBody(BaseModel):
    mode: str
    reviewer_role_id: int | None = None
    reviewer_role_slug: str | None = None


class DecisionBody(BaseModel):
    note: str | None = None


def _raise(exc: ReviewError) -> None:
    # FastAPI stringifies a dict detail as a nested object; the portals read `detail` as a
    # sentence, so keep the human message at the top and put the field beside it.
    payload = http_error(exc)
    raise HTTPException(status_code=exc.status, detail=payload.get("detail") or exc.detail) from exc


async def _caller_ids(session: Any) -> tuple[str | None, list[int]]:
    row = (
        (
            await session.execute(
                text("SELECT auth.jwt() ->> 'user_uuid' AS user_uuid, auth.role_ids() AS role_ids")
            )
        )
        .mappings()
        .one()
    )
    raw_roles = row["role_ids"] or []
    roles = [int(r) for r in raw_roles]
    return row["user_uuid"], roles


@router.get("/policies", name="list-review-policies")
async def list_policies(
    ctx: FirmContext = Depends(firm_book_session),
) -> list[dict[str, Any]]:
    # Readable by any staff so a save form can ask for a justification. Turning
    # a check on is the Actions page, which uses actions:write.
    return await service.list_policies(ctx.session, tenant_id=ctx.tenant_id)


@router.put("/policies/{action_key}", name="set-review-policy")
async def set_policy(
    action_key: str,
    body: SetPolicyBody,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    require_permission(ctx.principal, ACTIONS_WRITE)
    caller, _ = await _caller_ids(ctx.session)
    try:
        return await service.set_policy(
            ctx.session,
            tenant_id=ctx.tenant_id,
            action_key=action_key,
            mode=body.mode,
            reviewer_role_id=body.reviewer_role_id,
            reviewer_role_slug=body.reviewer_role_slug,
            actor_user_id=caller,
        )
    except ReviewError as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.get("", name="list-reviews")
async def list_reviews(
    scope: str = Query("inbox", pattern="^(inbox|mine|all)$"),
    status: str | None = None,
    action_key: str | None = None,
    contact_uid: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    ctx: FirmContext = Depends(firm_book_session),
) -> list[dict[str, Any]]:
    caller, roles = await _caller_ids(ctx.session)
    return await service.list_requests(
        ctx.session,
        tenant_id=ctx.tenant_id,
        scope=scope,
        caller_id=caller,
        caller_role_ids=roles,
        status=status,
        action_key=action_key,
        contact_uid=contact_uid,
        limit=limit,
    )


@router.get("/pending", name="list-pending-reviews")
async def list_pending(
    subject_type: str | None = None,
    subject_id: str | None = None,
    contact_uid: str | None = None,
    ctx: FirmContext = Depends(firm_book_session),
) -> list[dict[str, Any]]:
    """Pending reviews for a record — the client/account pending strip.

    The proposer sees their own request on the record they just submitted.
    RLS plus ``is_mine`` keep the strip to what the caller may already see.
    """
    caller, roles = await _caller_ids(ctx.session)
    if contact_uid:
        contact_id = (
            await ctx.session.execute(
                text("SELECT id FROM crm.contacts WHERE uid = :u AND tenant_id = :t"),
                {"u": contact_uid, "t": ctx.tenant_id},
            )
        ).scalar_one_or_none()
        if contact_id is None:
            return []
        return await service.pending_for_contact(
            ctx.session,
            tenant_id=ctx.tenant_id,
            contact_id=int(contact_id),
            caller_id=caller,
            caller_role_ids=roles,
        )
    if subject_type and subject_id:
        return await service.pending_for_subject(
            ctx.session,
            tenant_id=ctx.tenant_id,
            subject_type=subject_type,
            subject_id=subject_id,
            caller_id=caller,
            caller_role_ids=roles,
        )
    return []


@router.get("/{uid}", name="get-review")
async def get_review(
    uid: str,
    ctx: FirmContext = Depends(firm_book_session),
) -> dict[str, Any]:
    caller, roles = await _caller_ids(ctx.session)
    row = await service.get_request(ctx.session, uid=uid, caller_id=caller, caller_role_ids=roles)
    if row is None:
        raise HTTPException(status_code=404, detail="Review not found.")
    return row


@router.post("/{uid}/approve", name="approve-review")
async def approve_review(
    uid: str,
    body: DecisionBody | None = None,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    _, roles = await _caller_ids(ctx.session)
    try:
        return await service.approve(
            ctx.session,
            tenant_id=ctx.tenant_id,
            uid=uid,
            workos_user_id=ctx.principal.user_id,
            note=body.note if body else None,
            role_ids=roles,
        )
    except ReviewError as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.post("/{uid}/reject", name="reject-review")
async def reject_review(
    uid: str,
    body: DecisionBody,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    _, roles = await _caller_ids(ctx.session)
    try:
        return await service.reject(
            ctx.session,
            tenant_id=ctx.tenant_id,
            uid=uid,
            workos_user_id=ctx.principal.user_id,
            note=body.note or "",
            role_ids=roles,
        )
    except ReviewError as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.post("/{uid}/cancel", name="cancel-review")
async def cancel_review(
    uid: str,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    # The proposer cancels. The service only requires they own the request.
    try:
        return await service.cancel(
            ctx.session,
            tenant_id=ctx.tenant_id,
            uid=uid,
            workos_user_id=ctx.principal.user_id,
        )
    except ReviewError as exc:
        _raise(exc)
        raise  # pragma: no cover
