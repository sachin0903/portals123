"""``/crm/money`` — the client portal's own money-movement door (``app/client_money.py``).

Client-session endpoints: everything that decides what the caller may see runs through the RLS
session (``book_session``), and the one write goes through ``instructions.create_instruction``
under a system transaction — the same control the adviser platform uses, reached without the
staff permission a client does not hold. Whether the PORTAL offers the journey at all is the
classification's ``money_movement`` capability and the account's ``execution_only`` service
(docs/client-classifications.md); the API's own gates are the account's status, the client's
status and the funding gate, which hold whatever the portal shows.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import client_money
from app.actions.money import disabled_money_actions
from app.actions.runs import ActionError
from app.auth import Principal, get_current_principal
from app.db import book_session

router = APIRouter(prefix="/crm/money", tags=["crm"])


class MoneyRequest(BaseModel):
    kind: Literal["deposit", "withdrawal"]
    account_uid: str = Field(min_length=1, max_length=64)
    amount: float = Field(gt=0)


@router.post("/preview", name="client-money-preview")
async def preview(
    body: MoneyRequest, session: AsyncSession = Depends(book_session)
) -> dict[str, object]:
    """What the review step shows: the plan (cash or a disinvestment), what is available, the
    safety-check flags, the allowance after, where the money goes and when. Writes nothing."""
    try:
        return await client_money.preview(
            session, kind=body.kind, account_uid=body.account_uid, amount=body.amount
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/instructions", name="client-money-raise", status_code=status.HTTP_201_CREATED)
async def raise_instruction(
    body: MoneyRequest,
    session: AsyncSession = Depends(book_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    """Raise the instruction. Re-decides from the account's current position rather than from
    anything the screen said; a refusal is a 400 with the reason in the client's words."""
    try:
        return await client_money.raise_instruction(
            session,
            principal_user_id=principal.user_id,
            kind=body.kind,
            account_uid=body.account_uid,
            amount=body.amount,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ActionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.get("/actions", name="client-money-actions")
async def money_actions(session: AsyncSession = Depends(book_session)) -> dict[str, list[str]]:
    """Money actions switched off for this client's firm. A missing policy is not off."""
    tenant_id = (await session.execute(text("SELECT auth.tenant_id()"))).scalar_one_or_none()
    if tenant_id is None:
        return {"disabled": []}
    return {"disabled": await disabled_money_actions(session, tenant_id=int(tenant_id))}


@router.get("/instructions", name="client-money-pending")
async def pending(
    contact_id: int | None = None, session: AsyncSession = Depends(book_session)
) -> list[dict[str, object]]:
    """The client's pending requests (RLS-scoped), newest first."""
    return await client_money.list_pending(session, contact_id)


@router.post("/instructions/{uid}/cancel", name="client-money-cancel")
async def cancel(uid: str, session: AsyncSession = Depends(book_session)) -> dict[str, object]:
    """Cancel one of the client's own pending requests. 404 for anything that is not theirs,
    not pending, or not raised by them; 409 when the custodian already holds the order."""
    try:
        ok = await client_money.cancel(session, uid=uid)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No pending request with that id."
        )
    return {"ok": True}
