"""Client groups — catalogue, membership, and the signed-in client's groups."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import client_groups as groups_mod
from app.addressing import book_read_contact_id, book_write_contact_id
from app.auth import require_any_permission, require_permission
from app.client_groups import GroupIn
from app.db import FirmContext, book_session, firm_book_session, firm_book_write_session
from app.permissions import BRANDING_READ, BRANDING_WRITE, CONTACTS_READ, CONTACTS_WRITE

router = APIRouter(prefix="/crm", tags=["crm"])


class MembershipBody(BaseModel):
    group_uids: list[str] = Field(default_factory=list)


async def _acting_contact(
    session: AsyncSession, contact_id: int | None
) -> tuple[int | None, int | None]:
    """The person on screen, matching ``GET /crm/classifications/mine``.

    ``contact_id`` picks which linked contact, for a login that acts for more
    than one. Without it the groups are the login's own. An id the session does
    not hold is refused: quietly answering for a different person would hand
    back the wrong shopfront.
    """
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
    return (
        int(tenant_id) if tenant_id is not None else None,
        int(chosen) if chosen is not None else None,
    )


@router.get("/groups", name="list-client-groups")
async def list_groups(
    ctx: FirmContext = Depends(firm_book_session),
) -> list[dict[str, Any]]:
    # Advisers tag a client on the record; they should not need the branding
    # studio permission just to see the catalogue.
    require_any_permission(ctx.principal, BRANDING_READ, CONTACTS_READ)
    rows = await groups_mod.list_groups(ctx.session, ctx.tenant_id)
    return [groups_mod.public_view(row) for row in rows]


@router.post("/groups", name="create-client-group", status_code=status.HTTP_201_CREATED)
async def create_group(
    body: GroupIn,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    require_permission(ctx.principal, BRANDING_WRITE)
    try:
        return groups_mod.public_view(
            await groups_mod.create_group(ctx.session, ctx.tenant_id, body)
        )
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "That slug is already in use.") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/groups/mine", name="my-client-groups")
async def my_groups(
    contact_id: int | None = None,
    session: AsyncSession = Depends(book_session),
) -> dict[str, Any]:
    """The signed-in client's groups, in design-priority order. Empty when they
    wear none, so branding falls through to the book."""
    tenant_id, chosen = await _acting_contact(session, contact_id)
    if tenant_id is None or chosen is None:
        return {"groups": []}
    return {"groups": await groups_mod.groups_for_contact(session, tenant_id, chosen)}


@router.patch("/groups/{uid}", name="update-client-group")
async def update_group(
    uid: str,
    body: GroupIn,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    require_permission(ctx.principal, BRANDING_WRITE)
    try:
        row = await groups_mod.update_group(ctx.session, ctx.tenant_id, uid, body)
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "That slug is already in use.") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found.")
    return groups_mod.public_view(row)


@router.delete("/groups/{uid}", name="delete-client-group", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    uid: str,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> None:
    require_permission(ctx.principal, BRANDING_WRITE)
    if not await groups_mod.delete_group(ctx.session, ctx.tenant_id, uid):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found.")


@router.get("/contacts/{contact_uid}/groups", name="get-contact-groups")
async def get_contact_groups(
    contact_id: Annotated[int, Depends(book_read_contact_id)],
    ctx: FirmContext = Depends(firm_book_session),
) -> dict[str, Any]:
    require_any_permission(ctx.principal, BRANDING_READ, CONTACTS_READ)
    return {"groups": await groups_mod.groups_for_contact(ctx.session, ctx.tenant_id, contact_id)}


@router.put("/contacts/{contact_uid}/groups", name="set-contact-groups")
async def set_contact_groups(
    contact_id: Annotated[int, Depends(book_write_contact_id)],
    body: MembershipBody,
    ctx: FirmContext = Depends(firm_book_write_session),
) -> dict[str, Any]:
    require_permission(ctx.principal, CONTACTS_WRITE)
    try:
        groups = await groups_mod.set_contact_groups(
            ctx.session, ctx.tenant_id, contact_id, body.group_uids
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {"groups": groups}
