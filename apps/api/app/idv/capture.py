"""The client's own identity photos: the digital identity check.

The client photographs an identity document (its front, and its back unless it is a passport) and
their face in the portal, and sends them all in one submit
(``POST /applications/me/identity-capture/submit``), which names the document type. The submit
holds the photos ENCRYPTED (``app/idv/held.py``) and queues the send; the worker sends them to
ComplyCube (``app/complycube/identity.py``) and retries while ComplyCube is down. The copy goes
as soon as nothing needs it: when its try reopens the photo, and when the identity step finishes.
Nobody views a photo from our copy: the AML console reads them from ComplyCube
(``app/complycube/images.py``). A photo is never written to object storage or a log.

Each photo the check asks for is a SLOT on the step, recorded as ``case_steps.data.
identity_capture``: ``{item key: slot uid}`` for ``KEYS``. A slot is held once ComplyCube has the
photo for it (``provider_progress``); until then it is open, and the client sends it. One set of
slots is one TRY. There is no limit on tries, because every new try is an operator's choice:

* a try ComplyCube does not clear goes to operations (``review``) and the step waits. An operator
  clears it, rejects it, or asks for new photos: a new try at the next attempt that reopens only the
  photos chosen (``PHOTO_ITEMS``) and keeps the rest, which ComplyCube holds;
* a technical failure (ComplyCube could not run the check) sends the try's photos again by
  themselves, at the SAME attempt: the client did not get a try, and the photos were not at fault.
  The client is asked for nothing;
* ComplyCube refusing an upload reopens, at the SAME attempt, what it refused (the selfie, or both
  sides of the document) and every photo neither it nor our held copy has. A photo that was fine
  is kept.

``identity_capture`` holds the slot uids by key plus ``attempt`` (1-based, the try the client is
on), ``status`` (``capturing`` | ``submitted`` | ``retake`` | ``review``), ``last_reason`` (a
client-safe reason an operator chose, or null) and ``document_type`` once submitted. ``passed`` is
never stored: it is read from the check.

``last_provider_error`` (``{"message", "status_code", "at"}``) is why ComplyCube last refused a
try's upload. It is for Graphene operations only: the client's and the firm's reads never return
it. It stays until a later refusal replaces it.

``provider_progress`` (``{"document_id", "document_type", "accepted", "live_photo_id"}``) is what
ComplyCube holds of the try's photos: its document, the portal document type it was made for,
``{item key: slot uid}`` for each photo it accepted, and the live photo once the selfie is
accepted. It is never returned to the client. A try that reopens a photo drops what it held of it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ids import new_id
from app.idv import held as idv_held

#: ``case_steps.data`` key holding the capture.
CAPTURE_KEY = "identity_capture"
#: The capture key holding why ComplyCube last refused an upload. Operations only.
PROVIDER_ERROR_KEY = "last_provider_error"
#: The capture key holding what ComplyCube holds of the try's photos. Never the client's.
PROGRESS_KEY = "provider_progress"
#: The capture key counting how often the try's photos were sent again by themselves after
#: ComplyCube could not run the check (``routing.retry_portal_identity``). A new try starts at 0.
RESENDS_KEY = "resends"
#: What the client has taken on a phone and not sent yet: ``{"document_type", "taken": {item key:
#: slot uid}}``. Nothing of the photos themselves, which go together when they are sent. It lets
#: the computer waiting on the phone show each photo arrive.
PHONE_KEY = "phone_progress"

FRONT = "idv_document_front"
BACK = "idv_document_back"
SELFIE = "idv_selfie"
#: Every item key, in the order the photos are taken.
KEYS: tuple[str, ...] = (FRONT, BACK, SELFIE)
#: The items behind each photo the client can be asked for again: the document is both its sides.
PHOTO_ITEMS: dict[str, tuple[str, ...]] = {"document": (FRONT, BACK), "selfie": (SELFIE,)}

CAPTURING = "capturing"
SUBMITTED = "submitted"
RETAKE = "retake"
REVIEW = "review"
PASSED = "passed"

#: The documents the client can photograph. A passport has no back.
DOCUMENT_TYPES: tuple[str, ...] = ("passport", "driving_licence", "national_id")

#: What ComplyCube takes for a side of the document, and for the live photo.
DOCUMENT_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "application/pdf"})
PHOTO_CONTENT_TYPES = frozenset({"image/jpeg", "image/png"})

#: ComplyCube takes each image only "between 34 KB and 4 MB"; each bound is read on the safe side
#: of either unit (the portal's browser keeps every photo inside it, ``camera-capture``).
MIN_PHOTO_BYTES = 34 * 1024
MAX_PHOTO_BYTES = 4_000_000


@dataclass(frozen=True)
class Shot:
    """One photo the client is asked for, and the files ComplyCube takes for it."""

    key: str
    title: str
    content_types: frozenset[str]
    refusal: str

    @property
    def size_refusal(self) -> str:
        """What the client is told about a photo outside ComplyCube's size window."""
        return f"{self.title} must be between 34 KB and 4 MB."

    def refusal_for(self, content_type: str, size: int) -> str | None:
        """What the client is told about a file of the SNIFFED ``content_type`` and ``size``, or
        None when ComplyCube takes it."""
        if content_type not in self.content_types:
            return self.refusal
        if not MIN_PHOTO_BYTES <= size <= MAX_PHOTO_BYTES:
            return self.size_refusal
        return None


SHOTS: tuple[Shot, ...] = (
    Shot(
        FRONT,
        "The photo of your document's front",
        DOCUMENT_CONTENT_TYPES,
        "Upload your document as a JPG, PNG or PDF.",
    ),
    Shot(
        BACK,
        "The photo of your document's back",
        DOCUMENT_CONTENT_TYPES,
        "Upload your document as a JPG, PNG or PDF.",
    ),
    Shot(
        SELFIE,
        "The photo of your face",
        PHOTO_CONTENT_TYPES,
        "The photo of your face must be a JPG or PNG.",
    ),
)
SHOT_BY_KEY: dict[str, Shot] = {shot.key: shot for shot in SHOTS}

_STEP_DATA_SQL = (
    "SELECT data FROM flows.case_steps "
    "WHERE case_id = :c AND step_key = :k "
    "  AND (CAST(:psid AS int) IS NULL OR participant_subject_id = CAST(:psid AS int))"
)

_MERGE_STEP_DATA_SQL = (
    "UPDATE flows.case_steps "
    "SET data = COALESCE(data, '{}'::jsonb) || CAST(:d AS jsonb) "
    "WHERE case_id = :c AND step_key = :k "
    "  AND (CAST(:psid AS int) IS NULL OR participant_subject_id = CAST(:psid AS int))"
)


def needed_keys(document_type: str) -> tuple[str, ...]:
    """The photos a document type needs: no back for a passport."""
    if document_type == "passport":
        return (FRONT, SELFIE)
    return KEYS


def state_of(data: object) -> dict[str, Any] | None:
    """The step's capture, or None when the step has none."""
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        return None
    state = data.get(CAPTURE_KEY)
    if isinstance(state, str):
        state = json.loads(state)
    return state if isinstance(state, dict) else None


def item_uids(state: dict[str, Any]) -> dict[str, str]:
    """``{key: slot uid}`` for the current try."""
    return {key: str(state[key]) for key in KEYS if isinstance(state.get(key), str)}


def progress_of(state: dict[str, Any]) -> dict[str, Any]:
    """What ComplyCube holds of the try's photos (``PROGRESS_KEY``), or an empty record."""
    progress = state.get(PROGRESS_KEY)
    return progress if isinstance(progress, dict) else {}


def held_keys(state: dict[str, Any]) -> frozenset[str]:
    """The slots ComplyCube holds the photo of for THIS try: a photo it accepted for a slot the
    try still has, and the selfie once it holds the live photo."""
    progress = progress_of(state)
    accepted = progress.get("accepted")
    if not isinstance(accepted, dict):
        return frozenset()
    current = item_uids(state)
    held = {key for key, uid in accepted.items() if key in current and current[key] == uid}
    if SELFIE in held and not progress.get("live_photo_id"):
        held.discard(SELFIE)
    if held & {FRONT, BACK} and not progress.get("document_id"):
        held -= {FRONT, BACK}
    return frozenset(held)


def items_of(state: dict[str, Any], copies: frozenset[str] = frozenset()) -> list[dict[str, str]]:
    """The current try's photos, in the order they are taken: ``fulfilled`` when ComplyCube holds
    the photo or we hold a copy of it (``copies``, item keys from ``held.keys_held``), else
    ``open``. Once the document is held, its type is fixed and a passport's back is left out;
    until then the client may photograph any document."""
    held = held_keys(state) | copies
    document_type = state.get("document_type")
    fixed = FRONT in held and document_type in DOCUMENT_TYPES
    keys = needed_keys(str(document_type)) if fixed else KEYS
    return [
        {"key": key, "item_uid": uid, "status": "fulfilled" if key in held else "open"}
        for key, uid in item_uids(state).items()
        if key in keys
    ]


async def _write_state(
    session: AsyncSession, *, case_id: int, step_key: str, contact_id: int, state: dict[str, Any]
) -> None:
    await session.execute(
        text(_MERGE_STEP_DATA_SQL),
        {"d": json.dumps({CAPTURE_KEY: state}), "c": case_id, "k": step_key, "psid": contact_id},
    )


def _kept_progress(progress: dict[str, Any] | None, kept: dict[str, str]) -> dict[str, Any]:
    """``progress`` narrowed to the photos a try keeps: an accepted photo whose slot is reopened is
    no longer held, and neither is a document or live photo the try holds no photo of."""
    if not progress:
        return {}
    accepted = progress.get("accepted")
    accepted = accepted if isinstance(accepted, dict) else {}
    held = {key: uid for key, uid in accepted.items() if kept.get(key) == uid}
    out: dict[str, Any] = {"accepted": held}
    if held.keys() & {FRONT, BACK} and progress.get("document_id"):
        out["document_id"] = progress["document_id"]
        out["document_type"] = progress.get("document_type")
    if SELFIE in held and progress.get("live_photo_id"):
        out["live_photo_id"] = progress["live_photo_id"]
    return out if len(out) > 1 else {}


async def _raise_try(
    session: AsyncSession,
    *,
    case_id: int,
    step_key: str,
    contact_id: int,
    attempt: int,
    status: str,
    last_reason: str | None,
    previous: dict[str, Any] | None,
    provider_error: dict[str, Any] | None = None,
    reopen: tuple[str, ...] = KEYS,
    progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Open a try's slots for the ``reopen`` keys and record them on the step.

    Every other key keeps the previous try's slot, and the try keeps its ``document_type``: only
    the reopened photos are asked for again. ``progress`` is what ComplyCube holds of the try; only
    what it holds of a kept slot is kept. ``provider_error`` replaces the previous try's
    ``last_provider_error``; without one, that is carried over.
    """
    previous_slots = item_uids(previous) if previous else {}
    state: dict[str, Any] = {}
    for key in KEYS:
        state[key] = (
            previous_slots[key] if key not in reopen and key in previous_slots else new_id()
        )
    kept = {key: uid for key, uid in previous_slots.items() if key not in reopen}
    held = _kept_progress(progress, kept)
    # The try keeps its document, named on the step once sent, else by what ComplyCube holds of it.
    document_type = (previous or {}).get("document_type") if kept else None
    if document_type not in DOCUMENT_TYPES:
        document_type = held.get("document_type")
    state.update(
        {
            "attempt": attempt,
            "status": status,
            "last_reason": last_reason,
            "document_type": document_type if document_type in DOCUMENT_TYPES else None,
        }
    )
    carried = provider_error or (previous or {}).get(PROVIDER_ERROR_KEY)
    if isinstance(carried, dict):
        state[PROVIDER_ERROR_KEY] = carried
    if held:
        state[PROGRESS_KEY] = held
    await _write_state(
        session, case_id=case_id, step_key=step_key, contact_id=contact_id, state=state
    )
    # A reopened photo is taken again, so the copy held of the one it replaces goes now.
    await idv_held.release_reopened(
        session, case_id=case_id, step_key=step_key, contact_id=contact_id, slots=item_uids(state)
    )
    return state


async def raise_request(
    session: AsyncSession, *, contact_id: int, case_id: int, step_key: str
) -> dict[str, Any]:
    """Ask this client for their first try's photos. Idempotent: a step that has asked already
    returns its capture."""
    recorded = (
        await session.execute(
            text(_STEP_DATA_SQL), {"c": case_id, "k": step_key, "psid": contact_id}
        )
    ).scalar_one_or_none()
    existing = state_of(recorded)
    if existing is not None and item_uids(existing):
        return existing
    return await _raise_try(
        session,
        case_id=case_id,
        step_key=step_key,
        contact_id=contact_id,
        attempt=1,
        status=CAPTURING,
        last_reason=None,
        previous=None,
    )


@dataclass(frozen=True)
class CaptureStep:
    """The live identity step that owns a check, with its capture."""

    tenant_id: int
    case_id: int
    step_key: str
    contact_id: int
    state: dict[str, Any]
    manual: bool


async def step_for_check(session: AsyncSession, kyc_check_id: int) -> CaptureStep | None:
    """The live ``verify_identity`` step that opened this kyc row and holds a capture."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT cs.case_id, cs.step_key, cs.participant_subject_id, cs.data, "
                    "       c.tenant_id "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE c.execution_id IS NOT NULL AND cs.step_type = 'verify_identity' "
                    "  AND cs.data ->> 'kyc_check_id' = CAST(:k AS text) "
                    "  AND cs.status IN ('pending', 'waiting') "
                    "LIMIT 1"
                ),
                {"k": str(int(kyc_check_id))},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["participant_subject_id"] is None:
        return None
    state = state_of(row["data"])
    if state is None:
        return None
    data = row["data"] if isinstance(row["data"], dict) else json.loads(row["data"] or "{}")
    return CaptureStep(
        tenant_id=int(row["tenant_id"]),
        case_id=int(row["case_id"]),
        step_key=str(row["step_key"]),
        contact_id=int(row["participant_subject_id"]),
        state=state,
        manual=str(data.get("identity_choice") or "") == "manual",
    )


async def new_try(
    session: AsyncSession,
    step: CaptureStep,
    *,
    attempt: int,
    last_reason: str | None,
    provider_error: dict[str, Any] | None = None,
    reopen: tuple[str, ...] = KEYS,
    progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send the client back for new photos at ``attempt``: the ``reopen`` slots open afresh and
    the rest are kept (``_raise_try``). ``last_reason`` is what the client is told, and only ever a
    client-safe reason. ``provider_error`` records why ComplyCube refused the upload, for
    operations only."""
    return await _raise_try(
        session,
        case_id=step.case_id,
        step_key=step.step_key,
        contact_id=step.contact_id,
        attempt=attempt,
        status=RETAKE,
        last_reason=last_reason,
        previous=step.state,
        provider_error=provider_error,
        reopen=reopen,
        progress=progress,
    )


async def sent_for_client(
    session: AsyncSession,
    step: CaptureStep,
    *,
    attempt: int,
    document_type: str,
    document_id: str,
    live_photo_id: str,
) -> dict[str, Any]:
    """A try whose photos Graphene operations sent to ComplyCube for the client
    (``app.complycube.uploads``): every slot opens afresh at ``attempt`` and is held at once, and
    the capture is ``submitted``. The client is asked for nothing, and what the phone had taken
    and any copy held for the step go."""
    opened = await _raise_try(
        session,
        case_id=step.case_id,
        step_key=step.step_key,
        contact_id=step.contact_id,
        attempt=attempt,
        status=SUBMITTED,
        last_reason=None,
        previous=step.state,
    )
    slots = item_uids(opened)
    progress = {
        "accepted": {key: slots[key] for key in needed_keys(document_type)},
        "document_id": document_id,
        "document_type": document_type,
        "live_photo_id": live_photo_id,
    }
    state = {**opened, "document_type": document_type, PROGRESS_KEY: progress}
    await _write_state(
        session,
        case_id=step.case_id,
        step_key=step.step_key,
        contact_id=step.contact_id,
        state=state,
    )
    return state


def phone_progress(state: dict[str, Any]) -> dict[str, Any]:
    """What the phone has taken of THIS try (``PHONE_KEY``): ``{document_type, taken: [item
    keys]}``. A photo taken for a slot the try has since reopened is not counted, and the document
    is named only while something is taken."""
    recorded = state.get(PHONE_KEY)
    recorded = recorded if isinstance(recorded, dict) else {}
    slots = recorded.get("taken")
    slots = slots if isinstance(slots, dict) else {}
    current = item_uids(state)
    taken = [key for key in KEYS if key in slots and current.get(key) == slots[key]]
    document_type = recorded.get("document_type")
    return {
        "document_type": document_type if taken and document_type in DOCUMENT_TYPES else None,
        "taken": taken,
    }


#: Writes ``PHONE_KEY`` alone, in one statement, and only while the client is still taking the
#: photos. The phone reports in the background, so a report can land after the photos were sent:
#: rewriting the whole capture from the report's earlier read would put a sent try back to
#: ``capturing``, and the worker would then drop the send as stale.
_PHONE_PROGRESS_SQL = (
    "UPDATE flows.case_steps "
    "SET data = CASE WHEN CAST(:p AS jsonb) IS NULL "
    "  THEN data #- '{identity_capture,phone_progress}' "
    "  ELSE jsonb_set(data, '{identity_capture,phone_progress}', CAST(:p AS jsonb)) END "
    "WHERE case_id = :c AND step_key = :k AND participant_subject_id = :psid "
    "  AND data -> 'identity_capture' ->> 'status' IN ('capturing', 'retake')"
)


async def record_phone_progress(
    session: AsyncSession, step: CaptureStep, *, keys: Iterable[str], document_type: str | None
) -> None:
    """Record the photos the phone holds now (``PHONE_KEY``), replacing what it said before. No
    keys clears it. Nothing is written once the photos are sent (``_PHONE_PROGRESS_SQL``)."""
    current = item_uids(step.state)
    wanted = set(keys)
    taken = {key: current[key] for key in KEYS if key in wanted and key in current}
    progress = (
        {
            "document_type": document_type if document_type in DOCUMENT_TYPES else None,
            "taken": taken,
        }
        if taken
        else None
    )
    await session.execute(
        text(_PHONE_PROGRESS_SQL),
        {
            "p": None if progress is None else json.dumps(progress),
            "c": step.case_id,
            "k": step.step_key,
            "psid": step.contact_id,
        },
    )


async def set_status(session: AsyncSession, step: CaptureStep, status: str) -> None:
    """Move the capture to ``status`` without opening new slots."""
    await _write_state(
        session,
        case_id=step.case_id,
        step_key=step.step_key,
        contact_id=step.contact_id,
        state={**step.state, "status": status},
    )


async def record_progress(
    session: AsyncSession, step: CaptureStep, progress: dict[str, Any] | None
) -> dict[str, Any]:
    """Keep what ComplyCube holds of the try (a send that failed part-way), without moving it on,
    so the client's next send reuses it. ``progress`` None keeps nothing."""
    state = {key: value for key, value in step.state.items() if key != PROGRESS_KEY}
    if progress:
        state[PROGRESS_KEY] = progress
    await _write_state(
        session,
        case_id=step.case_id,
        step_key=step.step_key,
        contact_id=step.contact_id,
        state=state,
    )
    return state


async def resend(
    session: AsyncSession, step: CaptureStep, *, progress: dict[str, Any]
) -> dict[str, Any]:
    """Send the try's photos again, unchanged, after ComplyCube could not run its check: the capture
    is ``submitted`` again with what ComplyCube holds of it, and counts the resend."""
    count = step.state.get(RESENDS_KEY)
    state = {
        **step.state,
        "status": SUBMITTED,
        PROGRESS_KEY: progress,
        RESENDS_KEY: (count if isinstance(count, int) else 0) + 1,
    }
    await _write_state(
        session,
        case_id=step.case_id,
        step_key=step.step_key,
        contact_id=step.contact_id,
        state=state,
    )
    return state


async def mark_submitted(
    session: AsyncSession,
    step: CaptureStep,
    *,
    document_type: str,
    progress: dict[str, Any],
) -> dict[str, Any]:
    """Record that ComplyCube holds every photo of this try for ``document_type`` and the check
    has started."""
    submitted = {
        **step.state,
        "status": SUBMITTED,
        "document_type": document_type,
        PROGRESS_KEY: progress,
    }
    await _write_state(
        session,
        case_id=step.case_id,
        step_key=step.step_key,
        contact_id=step.contact_id,
        state=submitted,
    )
    return submitted
