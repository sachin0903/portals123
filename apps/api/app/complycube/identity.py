"""Send a portal client's identity photos to ComplyCube and start the check.

The client's submit (``POST /applications/me/identity-capture/submit``) holds the photos it carries
encrypted (``app/idv/held.py``) and enqueues ``identity.check.requested``; the outbox dispatcher
calls ``submit`` for it. The try's held photos go to the contact's ComplyCube client as a document
(front, and back unless it is a passport) and a live photo, and an identity check is started and
registered as a portal check. The result arrives by webhook and is routed by
``app.complycube.routing``.

``compliance.documents`` and ``compliance.live_photos`` hold ComplyCube's ids only, and the AML
console reads each image from ComplyCube when it is shown (``app.complycube.images``). Our
encrypted copy goes once the identity step finishes.

ComplyCube refusing an upload (400/422) is not one of the client's tries. What it refused, and
every photo neither it nor our held copy has, is asked for again at the same attempt; what it
accepted is kept on the capture (``capture.PROGRESS_KEY``) and the next send reuses it. A document
ComplyCube no longer holds is made again from our copy. Any other provider failure
(``ProviderUnavailable``) keeps what ComplyCube accepted and raises, so the outbox sends the row
again later. When the outbox gives up on the row (``gave_up``), the client is asked to send the
photos again, so no step waits on a send that will never happen and no copy is held for one.

A try ComplyCube does not clear goes to Graphene operations (``app.complycube.routing``). When an
operator asks for new photos, the client is told one of ``PHOTO_REQUEST_REASONS`` or nothing:
photo quality or an expired document only. Nothing a fraud signal says ever reaches them.

Every statement names its tenant.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.complycube import clients, routing
from app.complycube.client import ComplyCubeNotConfigured, ScreeningClient, require_client
from app.flows.dispatcher import PermanentDispatchError
from app.idv import capture
from app.idv import held as idv_held
from app.idv.held import Photo

logger = logging.getLogger(__name__)

#: The outbox event the client's submit enqueues.
EVENT = "identity.check.requested"

#: The portal's document types to ComplyCube's.
PORTAL_DOCUMENT_TYPES: dict[str, str] = {
    "passport": "passport",
    "driving_licence": "driving_license",
    "national_id": "national_identity_card",
}

#: The provider statuses that mean ComplyCube refused the photos themselves (unreadable, wrong
#: format). The client takes them again and the try does not count.
REFUSED_UPLOAD = frozenset({400, 422})
#: How much of ComplyCube's refusal message is kept for operations.
PROVIDER_ERROR_LIMIT = 300
#: The file extension ComplyCube is told for each content type a photo may have.
FILE_EXTENSIONS: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "application/pdf": "pdf",
}

#: The photos asked for again when ComplyCube refuses the call named, beside every photo it does
#: not hold yet. A refused document, or either of its images, reopens both sides of the document; a
#: refused live photo, the selfie alone. Anything else (the client record, the check itself) asks
#: for the whole try again.
REOPEN_ON_REFUSAL: dict[str, tuple[str, ...]] = capture.PHOTO_ITEMS

#: The reason an operator gives the client when asking for new photos (the AML console's "Ask for
#: new photos"), by its code.
PhotoReason = Literal["blurry", "cropped", "glare", "face", "expired", "none"]

#: What the client reads for each reason. Photo quality or an expired document only: none of them
#: describes a fraud signal or a face that does not match, which would tip the client off. ``none``
#: tells them nothing, and their portal shows its generic "try again". The admin console mirrors
#: this list word for word (``apps/admin/src/lib/aml.ts``).
PHOTO_REQUEST_REASONS: dict[PhotoReason, str | None] = {
    "blurry": "The photo was not clear enough. Please retake it in good light.",
    "cropped": "Part of your document was cut off. Make sure all four corners are in the photo.",
    "glare": "There was glare on your document. Please avoid reflections.",
    "face": "Your face was not clearly visible in the selfie. Please retake it in good light.",
    "expired": "Your document has expired. Please use an in-date document.",
    "none": None,
}


def refused_upload(response: httpx.Response) -> dict[str, Any]:
    """Why ComplyCube refused an upload, as ``identity_capture.last_provider_error`` holds it:
    the response's ``message`` (at most ``PROVIDER_ERROR_LIMIT`` characters), the HTTP status and
    when. For Graphene operations only; the client is told nothing from it."""
    message: str | None = None
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        message = body["message"].strip()[:PROVIDER_ERROR_LIMIT] or None
    return {
        "message": message,
        "status_code": response.status_code,
        "at": datetime.now(UTC).isoformat(),
    }


def issuing_country(contact: dict[str, Any]) -> str | None:
    """The country named as the document's issuer: the first declared nationality, else the
    address country. The provider reads the document's own issuer; this is the declared one."""
    nationalities = contact.get("nationality") or []
    if isinstance(nationalities, str):
        nationalities = [nationalities]
    first = next((n for n in nationalities if isinstance(n, str) and n.strip()), None)
    if first:
        return first.strip().upper()
    address = clients.contact_address(contact)
    country = address.get("country")
    return str(country).strip().upper() if isinstance(country, str) and country.strip() else None


class ProviderUnavailable(Exception):
    """ComplyCube could not take the photos now. What it accepted is kept; nothing else moved."""


class SendBlocked(Exception):
    """The check cannot start for this client (a ComplyCube client record conflict). Operations
    must look; sending again will not help."""


SendOutcome = Literal["started", "refused"]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class _Sent:
    """What ComplyCube holds of this try so far: the progress kept on the capture."""

    def __init__(self, document_type: str, state: dict[str, Any]) -> None:
        self.document_type = document_type
        self.slots = capture.item_uids(state)
        held = capture.held_keys(state)
        progress = capture.progress_of(state)
        self.document_id: str | None = None
        self.accepted: dict[str, str] = {}
        sides = held & {capture.FRONT, capture.BACK}
        if sides and progress.get("document_type") == document_type:
            self.document_id = str(progress["document_id"])
            self.accepted.update({key: self.slots[key] for key in sides})
        self.live_photo_id: str | None = None
        if capture.SELFIE in held:
            self.live_photo_id = str(progress["live_photo_id"])
            self.accepted[capture.SELFIE] = self.slots[capture.SELFIE]
        self.stage = "client"

    def progress(self) -> dict[str, Any] | None:
        """The ``capture.PROGRESS_KEY`` value to keep, or None when ComplyCube holds nothing."""
        out: dict[str, Any] = {"accepted": dict(self.accepted)}
        if self.document_id is not None:
            out["document_id"] = self.document_id
            out["document_type"] = self.document_type
        if self.live_photo_id is not None:
            out["live_photo_id"] = self.live_photo_id
        return out if len(out) > 1 else None

    def forget_document(self) -> None:
        self.document_id = None
        for key in (capture.FRONT, capture.BACK):
            self.accepted.pop(key, None)


async def _send_document(
    screening: ScreeningClient,
    sent: _Sent,
    *,
    provider_client: str,
    country: str,
    photos: dict[str, Photo],
    spare: dict[str, Photo],
) -> str:
    """The ComplyCube document for this try, with every side it needs: the held one when there is
    one (only the photos sent now are uploaded), else a new one. A held document ComplyCube no
    longer has is made again from ``spare``, our copies of the sides it held; without them its
    404 raises, and the caller treats it as a refusal."""
    if sent.document_id is not None:
        try:
            await screening.get_document(sent.document_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            needed_sides = set(capture.needed_keys(sent.document_type)) - {capture.SELFIE}
            remade = needed_sides <= set(photos) | set(spare)
            logger.info(
                "complycube: document %s is gone; %s",
                sent.document_id,
                "it is made again from our copy" if remade else "the client is asked for it again",
                extra={"event": "complycube_identity_document_gone"},
            )
            if not remade:
                raise
            sent.forget_document()
            photos.update({key: spare[key] for key in needed_sides if key not in photos})
    if sent.document_id is not None:
        document_id = sent.document_id
    else:
        created = await screening.create_document(
            provider_client,
            document_type=PORTAL_DOCUMENT_TYPES[sent.document_type],
            issuing_country=country,
        )
        document_id = str(created["id"])
        sent.document_id = document_id
    sides: tuple[tuple[str, Literal["front", "back"]], ...] = (
        (capture.FRONT, "front"),
        (capture.BACK, "back"),
    )
    for key, side in sides:
        photo = photos.get(key)
        if photo is None:
            continue
        await screening.upload_document_image(
            document_id,
            side=side,
            file_name=f"{side}.{FILE_EXTENSIONS[photo.content_type]}",
            data=_b64(photo.data),
        )
        sent.accepted[key] = sent.slots[key]
    return document_id


async def _send_selfie(
    screening: ScreeningClient, sent: _Sent, *, provider_client: str, photos: dict[str, Photo]
) -> str:
    """The try's live photo at ComplyCube: the selfie sent now, else the held one."""
    photo = photos.get(capture.SELFIE)
    if photo is None:
        if sent.live_photo_id is None:
            raise SendBlocked("the try has no selfie to send")
        return sent.live_photo_id
    created = await screening.create_live_photo(provider_client, data=_b64(photo.data))
    live_photo_id = str(created["id"])
    sent.live_photo_id = live_photo_id
    sent.accepted[capture.SELFIE] = sent.slots[capture.SELFIE]
    return live_photo_id


async def send(
    session: AsyncSession,
    step: capture.CaptureStep,
    *,
    kyc_check_id: int,
    contact: dict[str, Any],
    document_type: str,
    photos: dict[str, Photo],
    spare: dict[str, Photo] | None = None,
    rebound: bool = False,
) -> SendOutcome:
    """Send one try's photos to ComplyCube and start the check.

    ``photos`` are the needed photos ComplyCube does not hold yet; ``spare`` are our copies of the
    photos it does hold, to make the document again if ComplyCube has lost it, or the whole try if
    it has lost the client (``_send_to_new_client``; ``rebound`` once that is done).
    ``started``: the check runs and the capture is submitted. ``refused``: ComplyCube refused a
    photo, and the client is asked for it again without counting the try (``REOPEN_ON_REFUSAL``);
    its message is kept for operations (``refused_upload``). ``ProviderUnavailable`` when
    ComplyCube could not be reached or failed: what it accepted is kept, so the next send reuses
    it. ``SendBlocked`` when the check cannot start for this client.
    """
    country = issuing_country(contact)
    if country is None:
        raise SendBlocked(f"contact {step.contact_id} has no country to name the document's issuer")
    sent = _Sent(document_type, step.state)
    provider_client: str | None = None
    client_pk: int | None = None
    try:
        screening = require_client()
        was_bound = await clients.bound_provider_client(
            session, tenant_id=int(contact["tenant_id"]), contact_id=int(contact["id"])
        )
        client_pk = await clients.bind_portal_client(session, screening, contact)
        provider_client = await clients.provider_client_id(session, client_pk)
        if provider_client is None:
            raise SendBlocked(f"contact {step.contact_id} has no ComplyCube client")
        if not rebound and was_bound is not None and was_bound != provider_client:
            # Binding found ComplyCube no longer holds the client and bound one it holds: nothing
            # it held of the try is there either.
            return await _send_to_new_client(
                session,
                screening,
                step,
                kyc_check_id=kyc_check_id,
                contact=contact,
                document_type=document_type,
                client_pk=client_pk,
                copies={**(spare or {}), **photos},
                bound=True,
            )
        sent.stage = "document"
        provider_document_id = await _send_document(
            screening,
            sent,
            provider_client=provider_client,
            country=country,
            photos=dict(photos),
            spare=spare or {},
        )
        sent.stage = "selfie"
        provider_photo_id = await _send_selfie(
            screening, sent, provider_client=provider_client, photos=photos
        )
        sent.stage = "check"
        check = await screening.create_identity_check(
            provider_client, document_id=provider_document_id, live_photo_id=provider_photo_id
        )
    except clients.ClientConflict as exc:
        raise SendBlocked(str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        if (
            exc.response.status_code == 404
            and not rebound
            and client_pk is not None
            and provider_client is not None
            and await _client_gone(screening, provider_client)
        ):
            return await _send_to_new_client(
                session,
                screening,
                step,
                kyc_check_id=kyc_check_id,
                contact=contact,
                document_type=document_type,
                client_pk=client_pk,
                copies={**(spare or {}), **photos},
            )
        gone = exc.response.status_code == 404 and sent.stage == "document"
        if exc.response.status_code not in REFUSED_UPLOAD and not gone:
            await capture.record_progress(session, step, sent.progress())
            raise ProviderUnavailable(str(exc)) from exc
        await _refused(session, step, sent, exc.response, kyc_check_id=kyc_check_id)
        return "refused"
    except (httpx.TransportError, ComplyCubeNotConfigured) as exc:
        await capture.record_progress(session, step, sent.progress())
        raise ProviderUnavailable(str(exc)) from exc
    await _record_started(
        session,
        step,
        sent,
        kyc_check_id=kyc_check_id,
        client_pk=client_pk,
        country=country,
        provider_check_id=str(check["id"]),
    )
    return "started"


async def _client_gone(screening: ScreeningClient, provider_client: str) -> bool:
    """Whether a 404 means ComplyCube no longer holds the client itself. Unknown (ComplyCube did
    not answer) is not gone."""
    try:
        return not await clients.held_at_provider(screening, provider_client)
    except httpx.HTTPError:
        return False


async def _send_to_new_client(
    session: AsyncSession,
    screening: ScreeningClient,
    step: capture.CaptureStep,
    *,
    kyc_check_id: int,
    contact: dict[str, Any],
    document_type: str,
    client_pk: int,
    copies: dict[str, Photo],
    bound: bool = False,
) -> SendOutcome:
    """ComplyCube no longer holds the client — the record was made under another key, or deleted
    there — so nothing it held of the try is there either. The client is bound to one ComplyCube
    holds (unless binding already did, ``bound``) and the try is sent again whole, from our
    copies. Only a photo we hold no copy of is asked for again, at the same attempt: the client
    did not get a try."""
    if not bound:
        await clients.rebind_portal_client(session, screening, client_pk, contact)
    fresh = replace(
        step, state={key: value for key, value in step.state.items() if key != capture.PROGRESS_KEY}
    )
    needed = capture.needed_keys(document_type)
    missing = tuple(key for key in needed if key not in copies)
    if missing:
        logger.warning(
            "complycube: kyc check %s lost its ComplyCube client; the client is asked for %s again",
            kyc_check_id,
            ", ".join(missing),
            extra={"event": "complycube_identity_client_lost"},
        )
        await capture.new_try(
            session,
            fresh,
            attempt=_attempt(step.state),
            last_reason=None,
            reopen=missing,
            progress=None,
        )
        return "refused"
    return await send(
        session,
        fresh,
        kyc_check_id=kyc_check_id,
        contact=contact,
        document_type=document_type,
        photos={key: copies[key] for key in needed},
        rebound=True,
    )


def _attempt(state: dict[str, Any]) -> int:
    attempt = state.get("attempt")
    return attempt if isinstance(attempt, int) and attempt >= 1 else 1


async def _refused(
    session: AsyncSession,
    step: capture.CaptureStep,
    sent: _Sent,
    response: httpx.Response,
    *,
    kyc_check_id: int,
) -> None:
    """ComplyCube refused what ``sent.stage`` sent: ask for it again, and for every needed photo
    neither ComplyCube nor our copy holds, at the same attempt. A photo that was fine is not asked
    for again."""
    refusal = refused_upload(response)
    if sent.stage == "document":
        # Both sides are taken again, so nothing ComplyCube holds of them is kept.
        sent.forget_document()
    keep = sent.stage in REOPEN_ON_REFUSAL
    held = set(sent.accepted) if keep else set()
    copies = await idv_held.keys_held(session, slots=sent.slots)
    needed = set(capture.needed_keys(sent.document_type))
    refused = set(REOPEN_ON_REFUSAL.get(sent.stage, capture.KEYS))
    reopen = refused | (needed - held - copies)
    ordered = tuple(key for key in capture.KEYS if key in reopen)
    logger.warning(
        "complycube: ComplyCube refused the %s for kyc check %s (HTTP %s): %s; the client is "
        "asked for %s again",
        sent.stage,
        kyc_check_id,
        refusal["status_code"],
        refusal["message"],
        ", ".join(ordered),
        extra={"event": "complycube_identity_photos_refused"},
    )
    await capture.new_try(
        session,
        step,
        attempt=_attempt(step.state),
        last_reason=None,
        provider_error=refusal,
        reopen=ordered,
        progress=sent.progress() if keep else None,
    )


async def record_photos(
    session: AsyncSession,
    *,
    tenant_id: int,
    client_pk: int,
    document_id: str,
    document_type: str,
    country: str | None,
    live_photo_id: str,
) -> int:
    """Record ComplyCube's document and live photo for the client, by their ids only: we keep no
    image. ``document_type`` is the portal's. A document or live photo already recorded is kept as
    it is. Returns the ``compliance.documents`` row."""
    document_pk = (
        await session.execute(
            text(
                "INSERT INTO compliance.documents "
                "  (tenant_id, client_id, provider_document_id, document_type, issuing_country, "
                "   classification) "
                "VALUES (:t, :client, :pdoc, :dtype, :country, 'proof_of_identity') "
                "ON CONFLICT (provider_document_id) WHERE provider_document_id IS NOT NULL "
                "DO UPDATE SET updated_at = now() "
                "RETURNING id"
            ),
            {
                "t": tenant_id,
                "client": client_pk,
                "pdoc": document_id,
                "dtype": PORTAL_DOCUMENT_TYPES[document_type],
                "country": country,
            },
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO compliance.live_photos (tenant_id, client_id, provider_photo_id) "
            "VALUES (:t, :client, :pphoto) "
            "ON CONFLICT (provider_photo_id) WHERE provider_photo_id IS NOT NULL "
            "DO UPDATE SET updated_at = now()"
        ),
        {"t": tenant_id, "client": client_pk, "pphoto": live_photo_id},
    )
    return int(document_pk)


async def _record_started(
    session: AsyncSession,
    step: capture.CaptureStep,
    sent: _Sent,
    *,
    kyc_check_id: int,
    client_pk: int,
    country: str,
    provider_check_id: str,
) -> None:
    """Record the started check: ComplyCube's document and live photo (``record_photos``), the
    capture as submitted, and the portal check. The capture moves first, so a result already in
    when the check is stored is routed against the submitted try."""
    document_pk = await record_photos(
        session,
        tenant_id=step.tenant_id,
        client_pk=client_pk,
        document_id=str(sent.document_id),
        document_type=sent.document_type,
        country=country,
        live_photo_id=str(sent.live_photo_id),
    )
    await capture.mark_submitted(
        session, step, document_type=sent.document_type, progress=sent.progress() or {}
    )
    try:
        record = await routing.register_portal_check(
            session,
            tenant_id=step.tenant_id,
            client_id=client_pk,
            kind="identity",
            provider_check_id=provider_check_id,
            kyc_check_id=kyc_check_id,
            attempt=_attempt(step.state),
        )
    except LookupError as exc:
        raise SendBlocked(str(exc)) from exc
    # Our copy of the try's photos is kept for this check until it is decided: the backup of what
    # ComplyCube holds.
    slots = capture.item_uids(step.state)
    await idv_held.attach_check(
        session,
        slots={key: slots[key] for key in capture.needed_keys(sent.document_type) if key in slots},
        check_id=record.id,
    )
    await session.execute(
        text(
            "UPDATE compliance.checks SET document_id = COALESCE(document_id, :doc), "
            "  provider_document_id = COALESCE(provider_document_id, :pdoc), "
            "  provider_live_photo_id = COALESCE(provider_live_photo_id, :pphoto) "
            "WHERE id = :id"
        ),
        {
            "doc": document_pk,
            "pdoc": sent.document_id,
            "pphoto": sent.live_photo_id,
            "id": record.id,
        },
    )
    logger.info(
        "complycube: identity check %s started for contact %s (try %s)",
        record.uid,
        step.contact_id,
        _attempt(step.state),
        extra={"event": "complycube_identity_submitted"},
    )


#: What the client is told when the outbox gave up sending their photos.
SEND_FAILED_REASON = "We could not send your photos. Please send them again."


async def _waiting_step(
    session: AsyncSession, contact_id: int, kyc_check_id: int, items: dict[str, str]
) -> capture.CaptureStep | None:
    """The step whose sent try is this row's, or None when it has moved on (a new try, a manual
    choice, a cancelled journey)."""
    step = await capture.step_for_check(session, kyc_check_id)
    if (
        step is None
        or step.manual
        or step.contact_id != contact_id
        or capture.item_uids(step.state) != items
        or step.state.get("status") != capture.SUBMITTED
    ):
        return None
    return step


async def gave_up(session: AsyncSession, payload: dict[str, Any]) -> None:
    """The outbox gave up on a ``identity.check.requested`` row: ComplyCube stayed down past every
    retry, or the send can never be made. The client is asked to send the try's photos again at the
    same attempt, with ``SEND_FAILED_REASON``: each photo ComplyCube does not hold reopens, and its
    held copy goes with it. Nothing when the step has moved on."""
    try:
        contact_id, kyc_check_id, document_type, items = _payload(payload)
    except PermanentDispatchError:
        return
    step = await _waiting_step(session, contact_id, kyc_check_id, items)
    if step is None:
        return
    held = capture.held_keys(step.state)
    await capture.new_try(
        session,
        step,
        attempt=_attempt(step.state),
        last_reason=SEND_FAILED_REASON,
        reopen=tuple(key for key in capture.needed_keys(document_type) if key not in held),
        progress=capture.progress_of(step.state),
    )
    logger.warning(
        "complycube: the photos of kyc check %s could not be sent; the client is asked again",
        kyc_check_id,
        extra={"event": "complycube_identity_send_gave_up"},
    )


def _payload(payload: dict[str, Any]) -> tuple[int, int, str, dict[str, str]]:
    """The row's contact, kyc check, document type and the try's slots, or a permanent refusal."""
    contact_id = payload.get("contact_id")
    kyc_check_id = payload.get("kyc_check_id")
    document_type = payload.get("document_type")
    items = payload.get("items")
    if not (
        isinstance(contact_id, int)
        and isinstance(kyc_check_id, int)
        and document_type in PORTAL_DOCUMENT_TYPES
        and isinstance(items, dict)
    ):
        raise PermanentDispatchError(f"{EVENT} carried an incomplete payload")
    return contact_id, kyc_check_id, str(document_type), {str(k): str(v) for k, v in items.items()}


async def submit(session: AsyncSession, tenant_id: int, payload: dict[str, Any]) -> None:
    """Handle one ``identity.check.requested`` outbox row: send the try's held photos.

    A row for a try the step has moved past (a new try, a manual choice, a cancelled journey) is
    dropped: nothing waits on it. A photo the try needs that we no longer hold is asked for again
    at the same attempt. ComplyCube refusing a photo asks the client for it again (``send``). Any
    other provider failure raises, and the outbox sends the row again with back-off: the held
    photos stay until the step finishes.
    """
    contact_id, kyc_check_id, document_type, items = _payload(payload)
    step = await _waiting_step(session, contact_id, kyc_check_id, items)
    if step is None:
        logger.info(
            "complycube: identity submit for kyc check %s dropped — the step has moved on",
            kyc_check_id,
            extra={"event": "complycube_identity_submit_stale"},
        )
        return
    contact = (
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
    if contact is None:
        raise PermanentDispatchError(f"{EVENT}: contact {contact_id} not found")
    held = capture.held_keys(step.state)
    needed = capture.needed_keys(document_type)
    wanted = {key: items[key] for key in needed if key not in held}
    photos = await idv_held.photos_of(session, tenant_id=tenant_id, slots=wanted)
    missing = tuple(key for key in wanted if key not in photos)
    if missing:
        logger.warning(
            "complycube: the photos %s of kyc check %s are not held; the client is asked again",
            ", ".join(missing),
            kyc_check_id,
            extra={"event": "complycube_identity_photos_missing"},
        )
        await capture.new_try(
            session,
            step,
            attempt=_attempt(step.state),
            last_reason=None,
            reopen=missing,
            progress=capture.progress_of(step.state),
        )
        return
    # Our copies of the photos ComplyCube holds: to make the document again, or the whole try if
    # ComplyCube has lost the client.
    kept = {key: items[key] for key in needed if key in held}
    spare = await idv_held.photos_of(session, tenant_id=tenant_id, slots=kept)
    try:
        await send(
            session,
            step,
            kyc_check_id=kyc_check_id,
            contact=dict(contact),
            document_type=document_type,
            photos=photos,
            spare=spare,
        )
    except SendBlocked as exc:
        raise PermanentDispatchError(f"{EVENT}: {exc}") from exc
