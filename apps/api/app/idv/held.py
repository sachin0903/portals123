"""A portal client's identity photos, held encrypted until their identity check is decided.

The client's submit holds each photo it carries (``hold``) and queues the send; the worker reads
them back (``photos_of``) to send them to ComplyCube, so a ComplyCube outage is a retry rather than
a lost send. A photo uploaded for the client (``app/complycube/uploads.py``) is held for its check
(``hold_for_check``). Each photo is sealed with the API's master key (``app.crypto.seal``) in
``compliance.identity_photos``, which only ``service_role`` reaches and the audit trigger never
copies.

The copy is Graphene's backup of what ComplyCube holds: the AML console and the firm's compliance
page show it for any photo ComplyCube cannot give (``photo_for_check``,
``app/complycube/decisions.py``). Once a check starts, its photos name it (``attach_check``).

A photo is deleted as soon as nothing needs it:

* its try reopens it (``release_reopened``, from ``capture._raise_try``): the client takes it again;
* its identity step is no longer open AND its check is decided — cleared or rejected — or it never
  got one (``release_finished``). ``routing.settle_portal_check`` runs it when a check settles the
  step, the manual choice runs it for its step, and the worker's ComplyCube pass sweeps every firm
  as the backstop. A check held for review keeps its photos even if its journey was cancelled.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import crypto
from app.ids import new_id


@dataclass(frozen=True)
class Photo:
    """One photo the client sent: its bytes and their sniffed content type."""

    data: bytes
    content_type: str


@dataclass(frozen=True)
class StepRef:
    """The identity step a photo belongs to."""

    tenant_id: int
    case_id: int
    step_key: str
    contact_id: int


async def hold(
    session: AsyncSession, step: StepRef, slots: dict[str, str], photos: dict[str, Photo]
) -> None:
    """Hold ``photos`` (by item key) sealed, each under its slot uid in ``slots``. A photo sent
    again for the same slot replaces the one held."""
    for key, photo in photos.items():
        await session.execute(
            text(
                "INSERT INTO compliance.identity_photos "
                "  (tenant_id, case_id, step_key, contact_id, slot_uid, item_key, content_type, "
                "   sealed) "
                "VALUES (:t, :c, :k, :psid, :slot, :key, :ct, :sealed) "
                "ON CONFLICT (slot_uid) DO UPDATE SET content_type = EXCLUDED.content_type, "
                "  sealed = EXCLUDED.sealed, held_at = now()"
            ),
            {
                "t": step.tenant_id,
                "c": step.case_id,
                "k": step.step_key,
                "psid": step.contact_id,
                "slot": slots[key],
                "key": key,
                "ct": photo.content_type,
                "sealed": crypto.seal(photo.data),
            },
        )


async def hold_for_check(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    check_id: int,
    photos: dict[str, Photo],
    step: StepRef | None = None,
    slots: dict[str, str] | None = None,
) -> None:
    """Hold ``photos`` (by item key) uploaded for ``check_id``: under the try's slot when a step
    waits on them (``step`` and ``slots``), else under the check alone."""
    for key, photo in photos.items():
        await session.execute(
            text(
                "INSERT INTO compliance.identity_photos "
                "  (tenant_id, case_id, step_key, contact_id, slot_uid, item_key, content_type, "
                "   sealed, check_id) "
                "VALUES (:t, :c, :k, :psid, :slot, :key, :ct, :sealed, :check) "
                "ON CONFLICT (slot_uid) DO UPDATE SET content_type = EXCLUDED.content_type, "
                "  sealed = EXCLUDED.sealed, check_id = EXCLUDED.check_id, held_at = now()"
            ),
            {
                "t": tenant_id,
                "c": step.case_id if step else None,
                "k": step.step_key if step else None,
                "psid": contact_id,
                "slot": slots[key] if slots and key in slots else new_id(),
                "key": key,
                "ct": photo.content_type,
                "sealed": crypto.seal(photo.data),
                "check": check_id,
            },
        )


async def attach_check(session: AsyncSession, *, slots: dict[str, str], check_id: int) -> None:
    """Name ``check_id`` on the photos held for ``slots`` (``{item key: slot uid}``): the check
    their send started."""
    if not slots:
        return
    await session.execute(
        text(
            "UPDATE compliance.identity_photos SET check_id = :check WHERE slot_uid = ANY(:slots)"
        ),
        {"check": check_id, "slots": list(slots.values())},
    )


_FOR_CHECK = (
    "SELECT DISTINCT ON (item_key) item_key, content_type, sealed "
    "FROM compliance.identity_photos "
    "WHERE check_id = :check AND (CAST(:key AS text) IS NULL OR item_key = :key) "
    "ORDER BY item_key, held_at DESC, id DESC"
)


async def kinds_for_check(session: AsyncSession, check_id: int) -> dict[str, str]:
    """``{item key: content type}`` of our copy of the photos ``check_id`` was run on. Nothing is
    unsealed."""
    rows = (await session.execute(text(_FOR_CHECK), {"check": check_id, "key": None})).mappings()
    return {str(row["item_key"]): str(row["content_type"]) for row in rows}


async def photo_for_check(session: AsyncSession, check_id: int, item_key: str) -> Photo | None:
    """Our copy of one photo ``check_id`` was run on, or None when we hold none."""
    row = (
        (await session.execute(text(_FOR_CHECK), {"check": check_id, "key": item_key}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return Photo(data=crypto.unseal(bytes(row["sealed"])), content_type=str(row["content_type"]))


async def photos_of(
    session: AsyncSession, *, tenant_id: int, slots: dict[str, str]
) -> dict[str, Photo]:
    """The held photos for ``slots`` (``{item key: slot uid}``), by item key. A slot with no
    photo held is left out."""
    if not slots:
        return {}
    rows = (
        (
            await session.execute(
                text(
                    "SELECT slot_uid, content_type, sealed FROM compliance.identity_photos "
                    "WHERE tenant_id = :t AND slot_uid = ANY(:slots)"
                ),
                {"t": tenant_id, "slots": list(slots.values())},
            )
        )
        .mappings()
        .all()
    )
    by_slot = {str(row["slot_uid"]): row for row in rows}
    out: dict[str, Photo] = {}
    for key, slot in slots.items():
        row = by_slot.get(slot)
        if row is not None:
            out[key] = Photo(
                data=crypto.unseal(bytes(row["sealed"])), content_type=str(row["content_type"])
            )
    return out


async def keys_held(session: AsyncSession, *, slots: dict[str, str]) -> frozenset[str]:
    """The item keys of ``slots`` (``{item key: slot uid}``) we hold a photo for. Nothing is
    unsealed."""
    if not slots:
        return frozenset()
    held = (
        (
            await session.execute(
                text("SELECT slot_uid FROM compliance.identity_photos WHERE slot_uid = ANY(:s)"),
                {"s": list(slots.values())},
            )
        )
        .scalars()
        .all()
    )
    found = {str(slot) for slot in held}
    return frozenset(key for key, slot in slots.items() if slot in found)


async def release_reopened(
    session: AsyncSession, *, case_id: int, step_key: str, contact_id: int, slots: dict[str, str]
) -> int:
    """Delete the step's photos held for any slot but ``slots``, the try's current ones."""
    result = await session.execute(
        text(
            "DELETE FROM compliance.identity_photos "
            "WHERE case_id = :c AND step_key = :k AND contact_id = :psid "
            "  AND slot_uid <> ALL(:slots)"
        ),
        {"c": case_id, "k": step_key, "psid": contact_id, "slots": list(slots.values())},
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def release_finished(session: AsyncSession, *, tenant_id: int | None = None) -> int:
    """Delete every photo nothing needs: its identity step is no longer an open digital capture
    (passed, failed, cancelled or skipped, handed to a person, or it has none) AND its check is
    decided, or it has none. ``tenant_id`` narrows it to one firm."""
    result = await session.execute(
        text(
            "DELETE FROM compliance.identity_photos p "
            "WHERE (CAST(:t AS int) IS NULL OR p.tenant_id = CAST(:t AS int)) "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM flows.case_steps cs "
            "     WHERE cs.case_id = p.case_id AND cs.step_key = p.step_key "
            "       AND cs.participant_subject_id = p.contact_id "
            "       AND cs.status IN ('pending', 'waiting') "
            "       AND cs.data ->> 'identity_choice' IS NULL"
            "  ) "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM compliance.checks ch "
            "     WHERE ch.id = p.check_id AND ch.decided_at IS NULL "
            "       AND ch.status NOT IN ('clear', 'rejected')"
            "  )"
        ),
        {"t": tenant_id},
    )
    return int(getattr(result, "rowcount", 0) or 0)
