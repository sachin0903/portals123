"""Client classifications — catalogue, pin, and the signed-in client's resolved view."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import classifications as classifications_mod
from app.addressing import book_read_contact_id, book_write_contact_id
from app.auth import require_any_permission, require_permission
from app.classifications import ClassificationIn
from app.db import FirmContext, book_session, firm_book_session, firm_book_write_session
from app.permissions import BRANDING_READ, BRANDING_WRITE, CONTACTS_READ, CONTACTS_WRITE

router = APIRouter(prefix="/crm", tags=["crm"])


class PinBody(BaseModel):
    classification_uid: str | None = None


@router.get("/classifications", name="list-client-classifications")
async def list_classifications(
    ctx: FirmContext = Depends(firm_book_session),
) -> list[dict[str, Any]]:
    # Advisers pin a book on the record; they should not need the branding studio
    # permission just to see the catalogue.
    require_any_permission(ctx.principal, BRANDING_READ, CONTACTS_READ)
    return await classifications_mod.list_classifications(ctx.session, ctx.tenant_id)


@router.post(
    "/classifications", name="create-client-classification", status_code=status.HTTP_201_CREATED
)
async def create_classification(
    body: ClassificationIn,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    require_permission(ctx.principal, BRANDING_WRITE)
    try:
        return await classifications_mod.create_classification(ctx.session, ctx.tenant_id, body)
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "That slug is already in use.") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/classifications/mine", name="my-client-classification")
async def my_classification(
    contact_id: int | None = None,
    session: AsyncSession = Depends(book_session),
) -> dict[str, Any]:
    """The signed-in client's resolved classification — what the portal hook uses
    to pick a design set. No match rules. ``classification`` is null when the
    firm has not defined any, so branding falls back to ``default_template_id``.

    ``contact_id`` is WHICH linked contact, for a login that acts for more than one.
    Without it the book is the login's own (``capacity = 'self'``, else the first in
    scope), so a delegate viewing someone else kept seeing their own shopfront. An id
    the session does not hold is refused: quietly resolving a different person would
    answer 200 for the wrong book."""
    ids = (
        (
            await session.execute(
                text(
                    "SELECT auth.tenant_id() AS tenant_id, "
                    "       auth.contact_scope() AS scope, "
                    "       COALESCE("
                    "         (SELECT cu.contact_id FROM crm.contact_users cu"
                    "           WHERE cu.user_id = auth.uid()"
                    "           ORDER BY (cu.capacity = 'self') DESC, cu.contact_id"
                    "           LIMIT 1),"
                    "         (auth.contact_scope())[1]) AS contact_id"
                )
            )
        )
        .mappings()
        .one()
    )
    tenant_id = ids["tenant_id"]
    scope = {int(c) for c in (ids["scope"] or [])}
    if contact_id is not None:
        if contact_id not in scope:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "That contact is not linked to this session"
            )
        chosen = contact_id
    else:
        chosen = ids["contact_id"]
    if tenant_id is None or chosen is None:
        return {"classification": None}
    resolved = await classifications_mod.resolve_for_contact(session, int(tenant_id), int(chosen))
    if resolved is None:
        return {"classification": None}
    row, source = resolved
    return {"classification": classifications_mod.public_view(row, source=source)}


@router.patch("/classifications/{uid}", name="update-client-classification")
async def update_classification(
    uid: str,
    body: ClassificationIn,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    require_permission(ctx.principal, BRANDING_WRITE)
    try:
        row = await classifications_mod.update_classification(ctx.session, ctx.tenant_id, uid, body)
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "That slug is already in use.") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Classification not found.")
    return row


@router.delete(
    "/classifications/{uid}",
    name="delete-client-classification",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_classification(
    uid: str,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> None:
    require_permission(ctx.principal, BRANDING_WRITE)
    if not await classifications_mod.delete_classification(ctx.session, ctx.tenant_id, uid):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Classification not found.")


@router.get("/contacts/{contact_uid}/classification", name="get-contact-classification")
async def get_contact_classification(
    contact_id: Annotated[int, Depends(book_read_contact_id)],
    ctx: FirmContext = Depends(firm_book_session),
) -> dict[str, Any]:
    require_any_permission(ctx.principal, BRANDING_READ, CONTACTS_READ)
    resolved = await classifications_mod.resolve_for_contact(ctx.session, ctx.tenant_id, contact_id)
    if resolved is None:
        return {"classification": None}
    row, source = resolved
    return {"classification": classifications_mod.public_view(row, source=source)}


@router.put("/contacts/{contact_uid}/classification", name="pin-contact-classification")
async def pin_contact_classification(
    contact_id: Annotated[int, Depends(book_write_contact_id)],
    body: PinBody,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    require_permission(ctx.principal, CONTACTS_WRITE)
    out = await classifications_mod.pin_contact(
        ctx.session, ctx.tenant_id, contact_id, body.classification_uid
    )
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Classification not found.")
    return out
