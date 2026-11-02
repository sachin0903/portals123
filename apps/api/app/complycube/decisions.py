"""Clearing a portal identity check held for review, and the photos a person decides it on.

A portal identity try ComplyCube does not clear is held as an undecided ``refer``
(``routing.hold_portal_identity``). Only a Graphene operator clears it, in the AML console.
``clear_portal_identity`` is the one clear path: it validates the check at ComplyCube as
``confirmed``, records the verdict under the operator who decided, and routes ``refer → clear``,
which passes the journey step and closes the check's tasks. The check row is locked first, so of
two decisions at once the first wins and the other is refused.

``identity_images`` lists the check's images and ``identity_image`` reads one for a viewer: from
ComplyCube first, and from Graphene's encrypted copy, kept until the check is decided
(``app/idv/held.py``), for any image ComplyCube cannot give. Images only: never ComplyCube's
result, outcome or breakdown.

Runs in the console's ``service_role`` session: ``compliance.*`` is read past the firm's column
grants here, so every query carries its tenant by hand.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.complycube import images, routing
from app.complycube.client import ComplyCubeNotConfigured, require_client
from app.idv import capture
from app.idv import held as idv_held

logger = logging.getLogger(__name__)

#: ComplyCube's validate outcome that makes an identity check ``clear``.
CLEAR_OUTCOME = "confirmed"


class IdentityDecisionError(Exception):
    """A refusal the caller answers with ``status_code`` and this message."""

    status_code = 409

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotHeldForReview(IdentityDecisionError):
    """The check is not an undecided portal identity ``refer``. The caller answers 409."""


class ProviderNotConfigured(IdentityDecisionError):
    """No usable ComplyCube key. The caller answers 503."""

    status_code = 503


class ProviderRefused(IdentityDecisionError):
    """ComplyCube refused the validation or did not answer. The caller answers 502."""

    status_code = 502


@dataclass(frozen=True)
class HeldCheck:
    """The columns of a held check a decision reads."""

    id: int
    uid: str
    tenant_id: int
    client_id: int
    status: str
    origin: str
    kind: str
    decided_at: object
    provider_check_id: str | None
    document_id: int | None
    provider_document_id: str | None
    provider_live_photo_id: str | None


_HELD = """
SELECT id, uid, tenant_id, client_id, status, origin, kind, decided_at, provider_check_id,
       document_id, provider_document_id, provider_live_photo_id
  FROM compliance.checks
 WHERE id = :id
"""


async def load_held(session: AsyncSession, check_pk: int, *, lock: bool = False) -> HeldCheck:
    """The check, or ``NotHeldForReview`` when there is none. ``lock`` holds the row for a write."""
    row = (
        (await session.execute(text(_HELD + (" FOR UPDATE" if lock else "")), {"id": check_pk}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise NotHeldForReview("This identity check is not waiting for review.")
    return HeldCheck(
        id=int(row["id"]),
        uid=str(row["uid"]),
        tenant_id=int(row["tenant_id"]),
        client_id=int(row["client_id"]),
        status=str(row["status"]),
        origin=str(row["origin"]),
        kind=str(row["kind"]),
        decided_at=row["decided_at"],
        provider_check_id=row["provider_check_id"],
        document_id=int(row["document_id"]) if row["document_id"] is not None else None,
        provider_document_id=row["provider_document_id"],
        provider_live_photo_id=row["provider_live_photo_id"],
    )


def is_held(check: HeldCheck) -> bool:
    """An undecided portal identity ``refer``: the only check ``clear_portal_identity`` clears."""
    return (
        check.kind == "identity"
        and check.origin == "portal"
        and check.status == "refer"
        and check.decided_at is None
    )


def provider_refusal(response: httpx.Response) -> str:
    """ComplyCube's refusal, with its own message when it gave one."""
    message = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        raw = body.get("message") or body.get("error") or ""
        message = str(raw).strip()[:300]
    suffix = f": {message}" if message else "."
    return f"ComplyCube refused the request ({response.status_code}){suffix}"


async def clear_portal_identity(
    session: AsyncSession,
    *,
    check_pk: int,
    decided_by: str,
    comment: str,
) -> routing.CheckRecord:
    """Clear a portal identity check held for review, and pass the step waiting on it.

    Validated at ComplyCube as ``confirmed`` first, so a refused validation records nothing. Then
    the check is ``clear`` with ``decided_by`` / ``decided_at`` / ``decision_comment``, and
    ``routing.route_result`` takes it ``refer → clear``: the kyc row passes, the journey step
    completes and the check's operations tasks close.

    ``NotHeldForReview`` when the check is not an undecided portal identity ``refer`` (decided
    already, or a partner check); ``ProviderNotConfigured`` without a ComplyCube
    key; ``ProviderRefused`` when ComplyCube refuses or does not answer.
    """
    check = await load_held(session, check_pk, lock=True)
    if not is_held(check):
        raise NotHeldForReview(
            "This identity check is not waiting for review."
            if check.decided_at is None
            else "This identity check has already been decided."
        )
    if not check.provider_check_id:
        raise NotHeldForReview("This identity check has no ComplyCube record.")
    try:
        screening = require_client()
        await screening.validate_check(check.provider_check_id, CLEAR_OUTCOME, comment=comment)
    except ComplyCubeNotConfigured as exc:
        raise ProviderNotConfigured(str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise ProviderRefused(provider_refusal(exc.response)) from exc
    except httpx.HTTPError as exc:
        raise ProviderRefused("ComplyCube did not answer. Try again.") from exc
    await session.execute(
        text(
            "UPDATE compliance.checks SET status = 'clear', decided_by = :by, decided_at = now(), "
            "  decision_comment = :comment, updated_at = now() WHERE id = :id"
        ),
        {"by": decided_by, "comment": comment, "id": check.id},
    )
    record = await routing.load_check(session, check.id)
    if record is None:
        raise NotHeldForReview("This identity check is not waiting for review.")
    await routing.route_result(session, record, "refer", "clear")
    logger.info(
        "portal identity check %s cleared by an operator",
        check.uid,
        extra={"event": "portal_identity_cleared"},
    )
    return record


#: How each image of a check is labelled for whoever views it.
IMAGE_LABELS: dict[images.ImagePart, str] = {
    "front": "ID front",
    "back": "ID back",
    "selfie": "Selfie",
}


def _served_type(value: object) -> str:
    """ComplyCube's ``contentType`` as a viewer reads it: it names a JPEG ``image/jpg``."""
    content_type = str(value or "").strip().lower()
    if content_type in ("image/jpg", "image/pjpeg", ""):
        return "image/jpeg"
    return content_type


async def _held_record(fetch: Awaitable[Any]) -> dict[str, Any] | None:
    """ComplyCube's record of a document or live photo, or None when it no longer holds it."""
    try:
        found = await fetch
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise
    return found if isinstance(found, dict) else None


#: Our copy's item key for each image part.
_ITEM_KEYS: dict[images.ImagePart, str] = {
    "front": capture.FRONT,
    "back": capture.BACK,
    "selfie": capture.SELFIE,
}

#: Why a check lists no images: it names none and we hold none (``none``); ComplyCube is not
#: configured here (``not_configured``), does not hold them — the check may have been run under
#: another key, live or test (``not_held``) — or did not answer (``unavailable``), and Graphene's
#: copy is gone.
ImagesMissing = Literal["none", "not_configured", "not_held", "unavailable"]


@dataclass(frozen=True)
class ImageListing:
    """A check's images, and why there are none when there are none."""

    images: list[dict[str, str]]
    missing: ImagesMissing | None


async def _from_complycube(
    check: HeldCheck,
) -> tuple[dict[str, dict[str, str]], ImagesMissing | None]:
    """The images ComplyCube holds for the check, by part, and why it gave none when it gave
    none. Read from ComplyCube's own record of the document and the live photo: no image is read
    here."""
    if not (check.provider_document_id or check.provider_live_photo_id):
        return {}, "none"
    try:
        screening = require_client()
    except ComplyCubeNotConfigured:
        return {}, "not_configured"
    found: dict[str, dict[str, str]] = {}
    try:
        if check.provider_document_id:
            document = await _held_record(screening.get_document(check.provider_document_id))
            listed = (document or {}).get("images")
            by_side = {
                str(image.get("documentSide")): image
                for image in (listed if isinstance(listed, list) else [])
                if isinstance(image, dict)
            }
            sides: tuple[images.ImagePart, ...] = ("front", "back")
            for side in sides:
                image = by_side.get(side)
                if image is not None:
                    found[side] = {
                        "part": side,
                        "label": IMAGE_LABELS[side],
                        "content_type": _served_type(image.get("contentType")),
                        "source": "complycube",
                    }
        if check.provider_live_photo_id:
            photo = await _held_record(screening.get_live_photo(check.provider_live_photo_id))
            if photo is not None:
                found["selfie"] = {
                    "part": "selfie",
                    "label": IMAGE_LABELS["selfie"],
                    "content_type": _served_type(photo.get("contentType")),
                    "source": "complycube",
                }
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "identity images for check %s could not be listed from ComplyCube: %s",
            check.uid,
            exc,
            extra={"event": "identity_images_unlisted"},
        )
        return {}, "unavailable"
    return found, None if found else "not_held"


async def identity_images(session: AsyncSession, check: HeldCheck) -> ImageListing:
    """The check's images: ``{part, label, content_type, source}`` for the document's front and
    back and the selfie. ``source`` is ``complycube`` for an image ComplyCube holds and ``graphene``
    for one only our copy holds (``app/idv/held.py``). A viewer fetches each one through its image
    route (``identity_image``). An image neither holds is left out; when none is left, ``missing``
    says why. Nothing of ComplyCube's result is read."""
    found, missing = await _from_complycube(check)
    ours = await idv_held.kinds_for_check(session, check.id)
    for part, key in _ITEM_KEYS.items():
        if part not in found and key in ours:
            found[part] = {
                "part": part,
                "label": IMAGE_LABELS[part],
                "content_type": ours[key],
                "source": "graphene",
            }
    listed = [found[part] for part in images.PARTS if part in found]
    return ImageListing(images=listed, missing=None if listed else missing or "none")


async def identity_image(
    session: AsyncSession, check: HeldCheck, part: images.ImagePart
) -> images.Image:
    """One image of the check for a viewer: from ComplyCube, else from our copy.
    ``images.ImageMissing`` when neither holds it; ``ProviderNotConfigured`` /
    ``ProviderRefused`` when ComplyCube cannot be asked and we hold no copy."""
    failure: Exception
    try:
        return await images.fetch(
            require_client(),
            part,
            document_id=check.provider_document_id,
            live_photo_id=check.provider_live_photo_id,
        )
    except images.ImageMissing as exc:
        failure = exc
    except ComplyCubeNotConfigured as exc:
        failure = ProviderNotConfigured(str(exc))
    except httpx.HTTPError:
        failure = ProviderRefused("ComplyCube did not send the image. Try again shortly.")
    ours = await idv_held.photo_for_check(session, check.id, _ITEM_KEYS[part])
    if ours is not None:
        return images.Image(data=ours.data, content_type=ours.content_type)
    raise failure
