"""The phone half of an identity-photo link.

The client portal calls these, server to server, with the internal token. The browser never
does: the code is redeemed into an httpOnly cookie, and the cookie's token is what these
routes are shown. What they will do is the photos for the one step the link was made for —
read where they stand, and send them — and nothing else.

See ``app/idv/phone.py`` for why the link works once, and ``docs/flows.md`` for the desktop
that waits on it.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.db import FirmContext, _refuse_if_suspended
from app.idv import capture as idv_capture
from app.idv import phone
from app.routers import applications
from app.routers.internal import internal_session, require_internal_token

router = APIRouter(
    prefix="/identity-phone",
    tags=["identity-phone"],
    include_in_schema=False,
    dependencies=[Depends(require_internal_token)],
)

#: One sentence for every way a link can fail to open. Which way it was is not the phone's
#: to know: a used code, an expired one and a guessed one all read the same.
GONE = "This link has already been used or has expired."


class RedeemIn(BaseModel):
    code: str


class Redeemed(BaseModel):
    token: str
    seconds_remaining: int


class TakenIn(BaseModel):
    keys: list[str]
    document_type: Literal["passport", "driving_licence", "national_id"] | None = None


def _principal(link: phone.PhoneLink) -> Principal:
    return Principal(
        user_id=link.workos_user_id,
        role="client",
        portal="client",
        permissions=[],
    )


def _context(session: AsyncSession, link: phone.PhoneLink) -> FirmContext:
    return FirmContext(session=session, tenant_id=link.tenant_id, principal=_principal(link))


async def _live(
    x_identity_phone_token: str = Header(default=""),
    session: AsyncSession = Depends(internal_session),
) -> phone.PhoneLink:
    """The session the cookie names, with the client's claims published for the upload."""
    link = await phone.resolve(session, x_identity_phone_token)
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=GONE)
    await phone.publish_claims(session, link)
    await _refuse_if_suspended(session, link.tenant_id)
    return link


async def _step(
    session: AsyncSession, link: phone.PhoneLink
) -> tuple[dict[str, Any], dict[str, Any]]:
    row = await phone.step_row(session, link)
    state = (
        idv_capture.state_of({idv_capture.CAPTURE_KEY: row["identity_capture"]})
        if row is not None
        else None
    )
    if (
        row is None
        or state is None
        or not idv_capture.item_uids(state)
        or row["identity_choice"] is not None
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=GONE)
    return row, state


@router.post("/redeem", name="redeem-identity-phone")
async def redeem_identity_phone(
    body: RedeemIn,
    session: AsyncSession = Depends(internal_session),
) -> Redeemed:
    """Spend the one-time code and return the session token.

    Called by the client portal when the phone opens the link, never by the browser. A code
    that has already been opened, or has expired, or was never made, is the same answer.
    """
    token = await phone.redeem(session, body.code.strip())
    link = await phone.resolve(session, token) if token else None
    if token is None or link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=GONE)
    return Redeemed(token=token, seconds_remaining=phone.seconds_remaining(link.expires_at))


@router.get("/capture", name="identity-phone-capture")
async def identity_phone_capture(
    link: phone.PhoneLink = Depends(_live),
    session: AsyncSession = Depends(internal_session),
) -> dict[str, object]:
    """Where this link's photos stand. The same shape the signed-in page reads.

    Still answered after the photos have been sent, so the phone can show that it is done
    for the rest of the window. It never answers a different step from the one the link
    was made for.
    """
    row, state = await _step(session, link)
    body = await applications._capture_body(_context(session, link), row, state)
    body["status"] = applications._capture_status(state, row["check_status"])
    return body


@router.post("/taken", name="identity-phone-taken")
async def identity_phone_taken(
    body: TakenIn,
    link: phone.PhoneLink = Depends(_live),
    session: AsyncSession = Depends(internal_session),
) -> dict[str, bool]:
    """The photos the phone holds now, and the document they are of, so the computer waiting on
    it shows each one arrive. Nothing of the photos: they are sent together (``/submit``). Ignored
    once they have been sent."""
    row, state = await _step(session, link)
    if applications._capture_status(state, row["check_status"]) not in (
        idv_capture.CAPTURING,
        idv_capture.RETAKE,
    ):
        return {"ok": False}
    await idv_capture.record_phone_progress(
        session,
        idv_capture.CaptureStep(
            tenant_id=link.tenant_id,
            case_id=link.case_id,
            step_key=link.step_key,
            contact_id=link.contact_id,
            state=state,
            manual=False,
        ),
        keys=[key for key in body.keys if key in idv_capture.KEYS],
        document_type=body.document_type,
    )
    return {"ok": True}


@router.post("/submit", name="submit-identity-phone")
async def submit_identity_phone(
    document_type: Literal["passport", "driving_licence", "national_id"] = Form(...),
    idv_document_front: UploadFile | None = File(None),
    idv_document_back: UploadFile | None = File(None),
    idv_selfie: UploadFile | None = File(None),
    link: phone.PhoneLink = Depends(_live),
    session: AsyncSession = Depends(internal_session),
) -> dict[str, object]:
    """The phone sends the photos and names the document, in one request: they are held and sent
    on to the check exactly as the signed-in page's are
    (``applications.submit_my_identity_capture``)."""
    result = await applications.submit_my_identity_capture(
        document_type=document_type,
        step_key=link.step_key,
        case_uid=link.case_uid,
        idv_document_front=idv_document_front,
        idv_document_back=idv_document_back,
        idv_selfie=idv_selfie,
        principal=_principal(link),
        ctx=_context(session, link),
    )
    await phone.record(
        session,
        link,
        event_type=phone.PHOTOS_SUBMITTED,
        route="/identity-phone/submit",
        subject={"document_type": document_type},
    )
    return result
