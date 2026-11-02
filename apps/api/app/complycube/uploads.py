"""Identity photos uploaded for a portal client, and the check run on them.

Graphene operations upload them in the AML console (``POST /aml/clients/{key}/identity-photos``):
the client sent a new document by email, their document or name changed, or they sent better
photos of a try. A firm never uploads them. The photos go to ComplyCube in the
request and an identity check starts on them; our sealed copy is kept until the check is decided,
as the backup of what ComplyCube holds (``app/idv/held.py``). A photo left out is one ComplyCube
already holds for the client: the document, or the live photo, of their latest identity check that
names one.

The check is a portal check, and its result is routed like any other (``app.complycube.routing``):

* when a journey step waits on the client's identity, the check answers it. The step gets a new try
  whose photos are all held and which is ``submitted``, so the client is asked for nothing, and a
  phone link for the step stops working. A try held for review is closed under the operator, as
  asking for new photos closes it;
* otherwise the check takes the identity kyc row of the client's undecided check, else a new one,
  so a clear makes the client verified again from today.

ComplyCube refusing a photo is the uploader's to fix: the upload answers it at once
(``UploadRefused``) and nothing is recorded.

Every statement names its client or tenant.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from fastapi import UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import crypto, storage
from app.complycube import clients, identity, routing, tasks
from app.complycube.client import ScreeningClient
from app.complycube.outcomes import TECHNICAL_FAILURE, next_attempt
from app.ids import new_id
from app.idv import capture
from app.idv import held as idv_held
from app.idv import phone as idv_phone
from app.idv.held import Photo

logger = logging.getLogger(__name__)

#: ComplyCube's document types back to the portal's.
PORTAL_TYPES: dict[str, str] = {
    provider: portal for portal, provider in identity.PORTAL_DOCUMENT_TYPES.items()
}

#: What an operator reads about each photo.
TITLES: dict[str, str] = {
    capture.FRONT: "The document's front",
    capture.BACK: "The document's back",
    capture.SELFIE: "The selfie",
}

#: The photos an upload carries, as the console names them.
Part = Literal["document", "selfie"]


class UploadBlocked(Exception):
    """Operations cannot upload photos for this client now (409)."""


class UploadRefused(Exception):
    """A photo cannot be used: the wrong file, a missing side, or ComplyCube refused it (422)."""


def photo_from(key: str, data: bytes) -> Photo:
    """One uploaded photo, checked against what ComplyCube takes for it (``capture.Shot``), read
    from the SNIFFED type. ``UploadRefused`` with an operator's wording otherwise."""
    shot = capture.SHOT_BY_KEY[key]
    title = TITLES[key]
    try:
        content_type = storage.sniff_document_type(data[:4096])
    except ValueError:
        content_type = ""
    if content_type not in shot.content_types:
        kinds = "a JPG, PNG or PDF" if key != capture.SELFIE else "a JPG or PNG"
        raise UploadRefused(f"{title} must be {kinds}.")
    if not capture.MIN_PHOTO_BYTES <= len(data) <= capture.MAX_PHOTO_BYTES:
        raise UploadRefused(f"{title} must be between 34 KB and 4 MB.")
    return Photo(data=data, content_type=content_type)


async def read_photos(files: dict[str, UploadFile | None]) -> dict[str, Photo]:
    """The uploaded photos by item key, each read within ComplyCube's size and checked
    (``photo_from``). ``UploadRefused`` for the first that cannot be used."""
    photos: dict[str, Photo] = {}
    for key, file in files.items():
        if file is None:
            continue
        try:
            data = await storage.read_capped(file, limit=capture.MAX_PHOTO_BYTES)
        except storage.PayloadTooLarge as exc:
            raise UploadRefused(f"{TITLES[key]} must be between 34 KB and 4 MB.") from exc
        photos[key] = photo_from(key, data)
    return photos


@dataclass(frozen=True)
class Kept:
    """What ComplyCube holds of the client that an upload can keep: the document and the live photo
    of the client's latest identity check naming each. ``document_type`` is the portal's."""

    document_id: str | None = None
    document_type: str | None = None
    live_photo_id: str | None = None


@dataclass(frozen=True)
class WaitingStep:
    """The client's live identity step with a photo capture, and the kyc row it opened."""

    step: capture.CaptureStep
    kyc_check_id: int


@dataclass(frozen=True)
class Standing:
    """Where the client's identity stands for an upload: whether one can be made, what it keeps,
    and the journey step it would answer."""

    blocked: str | None
    kept: Kept
    waiting: WaitingStep | None
    latest: dict[str, Any] | None
    contact: dict[str, Any] | None


_WAITING_STEP = (
    "SELECT cs.case_id, cs.step_key, cs.participant_subject_id, cs.data, c.tenant_id "
    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
    "WHERE c.execution_id IS NOT NULL AND c.tenant_id = :t "
    "  AND cs.step_type = 'verify_identity' AND cs.participant_subject_id = :contact "
    "  AND cs.status IN ('pending', 'waiting') "
    "  AND cs.data ? 'identity_capture' AND cs.data ->> 'kyc_check_id' IS NOT NULL "
    "  AND cs.data ->> 'identity_choice' IS NULL "
    "ORDER BY c.id LIMIT 1"
)


async def waiting_step(
    session: AsyncSession, *, tenant_id: int, contact_id: int, lock: bool = False
) -> WaitingStep | None:
    """The client's live digital identity step, oldest journey first; None when no step waits on
    the client's photos (none open, or they chose the manual check). ``lock`` holds the step row,
    as the client's own submit does."""
    sql = _WAITING_STEP + (" FOR UPDATE OF cs" if lock else "")
    row = (
        (await session.execute(text(sql), {"t": tenant_id, "contact": contact_id}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    data = row["data"] if isinstance(row["data"], dict) else json.loads(row["data"] or "{}")
    state = capture.state_of(data)
    if state is None or not capture.item_uids(state):
        return None
    return WaitingStep(
        step=capture.CaptureStep(
            tenant_id=int(row["tenant_id"]),
            case_id=int(row["case_id"]),
            step_key=str(row["step_key"]),
            contact_id=int(row["participant_subject_id"]),
            state=state,
            manual=False,
        ),
        kyc_check_id=int(data["kyc_check_id"]),
    )


async def kept(session: AsyncSession, client_pk: int | None) -> Kept:
    """The document and the live photo ComplyCube holds for the client: each from their latest
    identity check that names one. A document of a type the portal does not take is not kept."""
    if client_pk is None:
        return Kept()
    row = (
        (
            await session.execute(
                text(
                    "SELECT "
                    "  (SELECT ch.provider_document_id FROM compliance.checks ch "
                    "    WHERE ch.client_id = :client AND ch.kind = 'identity' "
                    "      AND ch.provider_document_id IS NOT NULL "
                    "    ORDER BY ch.id DESC LIMIT 1) AS document_id, "
                    "  (SELECT ch.provider_live_photo_id FROM compliance.checks ch "
                    "    WHERE ch.client_id = :client AND ch.kind = 'identity' "
                    "      AND ch.provider_live_photo_id IS NOT NULL "
                    "    ORDER BY ch.id DESC LIMIT 1) AS live_photo_id"
                ),
                {"client": client_pk},
            )
        )
        .mappings()
        .one()
    )
    document_id = row["document_id"]
    document_type: str | None = None
    if document_id:
        stored = (
            await session.execute(
                text(
                    "SELECT document_type FROM compliance.documents "
                    "WHERE client_id = :client AND provider_document_id = :pdoc"
                ),
                {"client": client_pk, "pdoc": document_id},
            )
        ).scalar_one_or_none()
        document_type = PORTAL_TYPES.get(str(stored or ""))
    return Kept(
        document_id=str(document_id) if document_id and document_type else None,
        document_type=document_type,
        live_photo_id=str(row["live_photo_id"]) if row["live_photo_id"] else None,
    )


async def _latest_check(session: AsyncSession, client_pk: int | None) -> dict[str, Any] | None:
    """The client's latest identity check, the one the console shows."""
    if client_pk is None:
        return None
    found = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, status, attempt, decided_at, kyc_check_id "
                    "FROM compliance.checks WHERE client_id = :client AND kind = 'identity' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"client": client_pk},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(found) if found is not None else None


async def _contact(
    session: AsyncSession, *, tenant_id: int, contact_id: int
) -> dict[str, Any] | None:
    found = (
        (
            await session.execute(
                text(
                    f"SELECT {clients.CONTACT_COLUMNS} FROM crm.contacts "  # noqa: S608 - a module constant
                    "WHERE id = :c AND tenant_id = :t AND deleted_at IS NULL"
                ),
                {"c": contact_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(found) if found is not None else None


#: Why an upload cannot be made now, as the console says it.
PARTNER_CLIENT = "This client's firm sends their images through the partner API."
DELETED_CLIENT = "This client's record has been deleted."
CHECK_RUNNING = "An identity check is running. Wait for its result."
PHOTOS_SENDING = "The client's photos are being sent to ComplyCube. Wait for the result."


async def standing(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int | None,
    client_pk: int | None,
    lock: bool = False,
) -> Standing:
    """Where the client's identity stands for an upload. ``lock`` holds the waiting step for the
    upload that follows."""
    if contact_id is None:
        return Standing(PARTNER_CLIENT, Kept(), None, None, None)
    contact = await _contact(session, tenant_id=tenant_id, contact_id=contact_id)
    if contact is None:
        return Standing(DELETED_CLIENT, Kept(), None, None, None)
    waiting = await waiting_step(session, tenant_id=tenant_id, contact_id=contact_id, lock=lock)
    latest = await _latest_check(session, client_pk)
    blocked: str | None = None
    if latest is not None and latest["status"] == "pending":
        blocked = CHECK_RUNNING
    elif waiting is not None and waiting.step.state.get("status") == capture.SUBMITTED:
        blocked = PHOTOS_SENDING
    return Standing(blocked, await kept(session, client_pk), waiting, latest, contact)


@dataclass(frozen=True)
class Upload:
    """What the operator sent. ``photos`` by item key; ``document_type`` names the document when
    its sides are among them; ``issuing_country`` overrides the client's own."""

    photos: dict[str, Photo]
    document_type: str | None
    issuing_country: str | None
    uploaded_by: str
    comment: str


@dataclass(frozen=True)
class Uploaded:
    check_uid: str
    parts: tuple[Part, ...]
    document_type: str


#: Each side of a document, by its item key.
_SIDES: tuple[tuple[str, Literal["front", "back"]], ...] = (
    (capture.FRONT, "front"),
    (capture.BACK, "back"),
)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _parts(upload: Upload) -> tuple[Part, ...]:
    keys = set(upload.photos)
    parts: list[Part] = []
    if keys & {capture.FRONT, capture.BACK}:
        parts.append("document")
    if capture.SELFIE in keys:
        parts.append("selfie")
    return tuple(parts)


def _plan(upload: Upload, kept_now: Kept) -> str:
    """The portal document type the check runs on, once the upload is known to be whole: a new
    document has every side its type has, and what is left out is held at ComplyCube."""
    sides = set(upload.photos) - {capture.SELFIE}
    if not upload.photos:
        raise UploadRefused("Upload at least one photo.")
    if sides:
        if upload.document_type not in capture.DOCUMENT_TYPES:
            raise UploadRefused("Choose the type of document.")
        document_type = str(upload.document_type)
        needed = set(capture.needed_keys(document_type)) - {capture.SELFIE}
        if capture.BACK in sides and capture.BACK not in needed:
            raise UploadRefused("A passport has no back.")
        missing = [TITLES[key] for key in capture.KEYS if key in needed - sides]
        if missing:
            raise UploadRefused(f"{missing[0]} is missing.")
    elif kept_now.document_id is None or kept_now.document_type is None:
        raise UploadBlocked(
            "ComplyCube holds no document for this client. Upload the document too."
        )
    else:
        document_type = str(kept_now.document_type)
    if capture.SELFIE not in upload.photos and kept_now.live_photo_id is None:
        raise UploadBlocked("ComplyCube holds no selfie for this client. Upload a selfie too.")
    return document_type


async def _send(
    screening: ScreeningClient,
    session: AsyncSession,
    *,
    contact: dict[str, Any],
    upload: Upload,
    kept_now: Kept,
    document_type: str,
    country: str | None,
) -> tuple[int, str, str, str]:
    """Send the upload to ComplyCube and start the check: the client record, the document (new,
    or the one held), the live photo (new, or the one held) and the identity check. Returns the
    compliance client, the document, the live photo and the check, by ComplyCube's ids.

    ``UploadRefused`` when ComplyCube refuses a photo or will not start the check on them;
    ``UploadBlocked`` when ComplyCube no longer holds what the upload keeps, or the client record
    conflicts. Any other provider error is raised unchanged."""
    stage = "client"
    try:
        client_pk = await clients.bind_portal_client(session, screening, contact)
        provider_client = await clients.provider_client_id(session, client_pk)
        if provider_client is not None and not await clients.held_at_provider(
            screening, provider_client
        ):
            # Made under another key, or deleted there: bound again, and nothing it held is kept.
            client_pk = await clients.rebind_portal_client(session, screening, client_pk, contact)
            provider_client = await clients.provider_client_id(session, client_pk)
        if provider_client is None:
            raise UploadBlocked("This client has no ComplyCube record.")
        if capture.FRONT in upload.photos:
            if country is None:
                raise UploadRefused("Choose the country that issued the document.")
            stage = "document"
            created = await screening.create_document(
                provider_client,
                document_type=identity.PORTAL_DOCUMENT_TYPES[document_type],
                issuing_country=country,
            )
            document_id = str(created["id"])
            for key, side in _SIDES:
                photo = upload.photos.get(key)
                if photo is None:
                    continue
                stage = key
                await screening.upload_document_image(
                    document_id,
                    side=side,
                    file_name=f"{side}.{identity.FILE_EXTENSIONS[photo.content_type]}",
                    data=_b64(photo.data),
                )
        else:
            stage = "kept_document"
            document_id = str(kept_now.document_id)
            await screening.get_document(document_id)
        selfie = upload.photos.get(capture.SELFIE)
        if selfie is not None:
            stage = capture.SELFIE
            live_photo_id = str(
                (await screening.create_live_photo(provider_client, data=_b64(selfie.data)))["id"]
            )
        else:
            stage = "kept_selfie"
            live_photo_id = str(kept_now.live_photo_id)
            await screening.get_live_photo(live_photo_id)
        stage = "check"
        check = await screening.create_identity_check(
            provider_client, document_id=document_id, live_photo_id=live_photo_id
        )
    except clients.ClientConflict as exc:
        raise UploadBlocked(str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if stage == "kept_document" and status_code == 404:
            raise UploadBlocked(
                "ComplyCube no longer holds the client's document. Upload the document too."
            ) from exc
        if stage == "kept_selfie" and status_code == 404:
            raise UploadBlocked(
                "ComplyCube no longer holds the client's selfie. Upload a selfie too."
            ) from exc
        if status_code not in identity.REFUSED_UPLOAD:
            raise
        message = identity.refused_upload(exc.response)["message"]
        what = {
            "client": "the client's details",
            "document": "the document",
            capture.FRONT: "the document's front",
            capture.BACK: "the document's back",
            capture.SELFIE: "the selfie",
        }.get(stage, "the check")
        suffix = f": {message}" if message else "."
        raise UploadRefused(f"ComplyCube refused {what}{suffix}") from exc
    return client_pk, document_id, live_photo_id, str(check["id"])


async def _kyc_row(
    session: AsyncSession, *, tenant_id: int, contact_id: int, latest: dict[str, Any] | None
) -> tuple[int, int]:
    """The identity kyc row a check with no waiting step settles, and the check's attempt: the row
    of the client's undecided check, at its next try, else a new pending row at the first."""
    if latest is not None and latest["decided_at"] is None and latest["kyc_check_id"] is not None:
        pending = (
            await session.execute(
                text(
                    "SELECT id FROM crm.kyc_checks "
                    "WHERE id = :id AND tenant_id = :t AND status = 'pending'"
                ),
                {"id": latest["kyc_check_id"], "t": tenant_id},
            )
        ).scalar_one_or_none()
        if pending is not None:
            return int(pending), next_attempt(int(latest["attempt"]), str(latest["status"]))
    created = (
        await session.execute(
            text(
                "INSERT INTO crm.kyc_checks (uid, tenant_id, contact_id, kind, status, provider) "
                "VALUES (:u, :t, :c, 'identity', 'pending', 'complycube') RETURNING id"
            ),
            {"u": new_id(), "t": tenant_id, "c": contact_id},
        )
    ).scalar_one()
    return int(created), 1


async def start(
    session: AsyncSession,
    screening: ScreeningClient,
    *,
    tenant_id: int,
    contact_id: int | None,
    client_pk: int | None,
    upload: Upload,
) -> Uploaded:
    """Start an identity check on the photos an operator uploaded for a portal client.

    Serialised per client, and the waiting step is held, so neither a second upload nor the
    client's own submit can cross it. ``UploadBlocked`` when no upload can be made now (a partner
    client, a check running, nothing held to keep); ``UploadRefused`` when the photos cannot be
    used. Nothing is recorded unless the check started.
    """
    if contact_id is not None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"identity-upload:{contact_id}"},
        )
    now = await standing(
        session,
        tenant_id=tenant_id,
        contact_id=contact_id,
        client_pk=client_pk,
        lock=True,
    )
    if now.blocked is not None or now.contact is None or contact_id is None:
        raise UploadBlocked(now.blocked or PARTNER_CLIENT)
    document_type = _plan(upload, now.kept)
    country: str | None = None
    if capture.FRONT in upload.photos:
        country = (upload.issuing_country or "").strip().upper() or identity.issuing_country(
            now.contact
        )
        if country is None:
            raise UploadRefused("Choose the country that issued the document.")
    client_pk, document_id, live_photo_id, provider_check_id = await _send(
        screening,
        session,
        contact=now.contact,
        upload=upload,
        kept_now=now.kept,
        document_type=document_type,
        country=country,
    )
    await identity.record_photos(
        session,
        tenant_id=tenant_id,
        client_pk=client_pk,
        document_id=document_id,
        document_type=document_type,
        country=country,
        live_photo_id=live_photo_id,
    )

    latest = now.latest
    if (
        latest is not None
        and latest["decided_at"] is None
        and (latest["status"] in {"refer", TECHNICAL_FAILURE})
    ):
        # The try this upload replaces: closed under the operator, as asking for new photos closes
        # it. Its status stands, and no verdict is written.
        await session.execute(
            text(
                "UPDATE compliance.checks SET decided_by = :by, decided_at = now(), "
                "  decision_comment = :comment, updated_at = now() WHERE id = :id"
            ),
            {"by": upload.uploaded_by, "comment": upload.comment, "id": latest["id"]},
        )
        await tasks.close_screening_task(session, tasks.check_task_ref(str(latest["uid"])))

    if now.waiting is not None:
        step = now.waiting.step
        kyc_check_id = now.waiting.kyc_check_id
        status = step.state.get("status")
        current = step.state.get("attempt")
        current = current if isinstance(current, int) and current >= 1 else 1
        attempt = current + 1 if status == capture.REVIEW else current
        # The step holds the try before the check is stored, so a result already in is routed
        # against it.
        sent_try = await capture.sent_for_client(
            session,
            step,
            attempt=attempt,
            document_type=document_type,
            document_id=document_id,
            live_photo_id=live_photo_id,
        )
        await idv_phone.withdraw(
            session,
            tenant_id=step.tenant_id,
            case_id=step.case_id,
            step_key=step.step_key,
            contact_id=step.contact_id,
        )
    else:
        sent_try = None
        kyc_check_id, attempt = await _kyc_row(
            session, tenant_id=tenant_id, contact_id=contact_id, latest=latest
        )

    record = await routing.register_portal_check(
        session,
        tenant_id=tenant_id,
        client_id=client_pk,
        kind="identity",
        provider_check_id=provider_check_id,
        kyc_check_id=kyc_check_id,
        attempt=attempt,
    )
    await session.execute(
        text(
            "UPDATE compliance.checks SET "
            "  document_id = (SELECT id FROM compliance.documents WHERE client_id = :client "
            "                  AND provider_document_id = :pdoc), "
            "  provider_document_id = :pdoc, provider_live_photo_id = :pphoto, "
            "  uploaded_by = :by, uploaded_via = 'operations', upload_comment = :comment "
            "WHERE id = :id"
        ),
        {
            "client": client_pk,
            "pdoc": document_id,
            "pphoto": live_photo_id,
            "by": upload.uploaded_by,
            "comment": upload.comment,
            "id": record.id,
        },
    )
    await _keep_copies(
        session,
        tenant_id=tenant_id,
        contact_id=contact_id,
        check_id=record.id,
        photos=upload.photos,
        step=now.waiting.step if now.waiting is not None else None,
        slots=capture.item_uids(sent_try) if sent_try is not None else None,
    )
    # A try this upload closed no longer needs our copy of its photos.
    await idv_held.release_finished(session, tenant_id=tenant_id)
    return Uploaded(check_uid=record.uid, parts=_parts(upload), document_type=document_type)


async def _keep_copies(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    check_id: int,
    photos: dict[str, Photo],
    step: capture.CaptureStep | None,
    slots: dict[str, str] | None,
) -> None:
    """Keep our sealed copy of the uploaded photos until the check is decided: the backup of what
    ComplyCube holds. Under the step's new try when one waits on them, else under the check. A copy
    that cannot be sealed (no master key) is logged and the check goes on: ComplyCube has the
    photos."""
    try:
        await idv_held.hold_for_check(
            session,
            tenant_id=tenant_id,
            contact_id=contact_id,
            check_id=check_id,
            photos=photos,
            step=(
                idv_held.StepRef(
                    tenant_id=step.tenant_id,
                    case_id=step.case_id,
                    step_key=step.step_key,
                    contact_id=step.contact_id,
                )
                if step is not None
                else None
            ),
            slots=slots,
        )
    except crypto.SecretsNotConfigured as exc:
        logger.error(
            "uploaded identity photos for check %s cannot be kept: %s",
            check_id,
            exc,
            extra={"event": "identity_upload_copy_unsealed"},
        )


def default_country(contact: dict[str, Any] | None) -> str | None:
    """The issuing country an upload names unless the operator chooses another."""
    return identity.issuing_country(contact) if contact is not None else None
