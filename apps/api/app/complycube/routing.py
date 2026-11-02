"""Where a check's new status goes.

``route_result`` runs once per status change: the consumer calls it with the ``(old, new)`` pair
``record_event`` returned, and a reviewer's decision calls it with the status it decided from and
the verdict (``("refer", "clear")``, ``("failed", "rejected")``). The same status twice is not a
change, and neither is a check this event created (``old`` None): that row was written before the
code that started the check committed it, and that code settles it (``register_check``, or the
firm API's own create response).

* A technical failure (``failed``) is not a verdict. Nothing is settled and nothing goes to the
  firm: the kyc row stays ``pending`` and the firm API reads ``pending``. A portal AML check raises
  an operations task to run it again; a portal identity check sends the same photos again by
  itself, once per try (``retry_portal_identity``), and raises the operations task after that.
* A monitoring alert (a decided or monitored check referred again) raises an operations task.
* A check leaving ``refer`` or ``failed`` for a verdict closes the tasks raised for it.
* ``portal`` origin releases the flow step waiting on the check's kyc row:

  * the provider rejecting or referring an identity try (ComplyCube ``rejected`` or
    ``attention``), on any try, holds it as ``refer`` for an operator (``hold_portal_identity``):
    the client sees "a person is checking" and the kyc row stays ``pending``. The operator clears
    it, rejects it, or, while the client has tries left, asks for new photos
    (``ask_for_photos``);
  * an AML ``refer`` waits for an operator, and the kyc row stays ``pending``;
  * any other verdict is ``settle_portal_check``: passed, referred or failed, through
    ``orchestrator.on_kyc_result``.

* ``partner`` origin queues one ``compliance.webhook_deliveries`` row when the firm has a webhook
  and the firm-visible status changed.

Runs as ``service_role`` in the worker or a request's elevated session.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from logging import getLogger
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.complycube import tasks
from app.complycube.outcomes import (
    DECIDED,
    TECHNICAL_FAILURE,
    KycOutcome,
    next_attempt,
    partner_status,
    to_kyc_status,
)
from app.complycube.sync import MONITORING_EVENTS
from app.flows import kyc as kyc_rules
from app.flows.dispatcher import PermanentDispatchError
from app.logging import report_error

logger = getLogger(__name__)

PARTNER_EVENT = "check.updated"
#: The statuses an operator acts on. Leaving one for a verdict closes the check's tasks.
REVIEWABLE = frozenset({"refer", TECHNICAL_FAILURE})


@dataclass(frozen=True)
class CheckRecord:
    """The ``compliance.checks`` columns routing reads, plus the event that changed it."""

    id: int
    uid: str
    tenant_id: int | None
    kind: str
    origin: str
    status: str
    kyc_check_id: int | None
    contact_id: int | None
    monitoring: bool
    provider_check_id: str | None
    event_type: str | None = None
    provider_event_id: str | None = None
    attempt: int = 1


_LOAD_CHECK = text(
    """
    SELECT ch.id, ch.uid, ch.tenant_id, ch.kind, ch.origin, ch.status, ch.kyc_check_id,
           cl.contact_id, ch.monitoring, ch.provider_check_id, ch.attempt
      FROM compliance.checks ch
      JOIN compliance.clients cl ON cl.id = ch.client_id
     WHERE ch.id = :id
    """
)


def _record(row: Any, *, event_type: str | None, provider_event_id: str | None) -> CheckRecord:
    return CheckRecord(
        id=int(row.id),
        uid=str(row.uid),
        tenant_id=int(row.tenant_id) if row.tenant_id is not None else None,
        kind=str(row.kind),
        origin=str(row.origin),
        status=str(row.status),
        kyc_check_id=int(row.kyc_check_id) if row.kyc_check_id is not None else None,
        contact_id=int(row.contact_id) if row.contact_id is not None else None,
        monitoring=bool(row.monitoring),
        provider_check_id=row.provider_check_id,
        event_type=event_type,
        provider_event_id=provider_event_id,
        attempt=int(row.attempt),
    )


async def load_check(
    session: AsyncSession,
    check_pk: int,
    *,
    event_type: str | None = None,
    provider_event_id: str | None = None,
) -> CheckRecord | None:
    row = (await session.execute(_LOAD_CHECK, {"id": check_pk})).first()
    if row is None:
        return None
    return _record(row, event_type=event_type, provider_event_id=provider_event_id)


def is_monitoring_alert(check: CheckRecord, old: str | None, new: str | None) -> bool:
    """A finished check referred again: by a monitoring event, or while monitoring is on."""
    if new != "refer" or old == "refer":
        return False
    if check.event_type in MONITORING_EVENTS:
        return True
    return check.monitoring and old in DECIDED


async def route_result(
    session: AsyncSession, check: CheckRecord, old: str | None, new: str | None
) -> list[str]:
    """Send one status change where it belongs. Returns what was done, for logs and tests.

    A no-op when ``old == new``, when ``old`` is None (see the module docstring), or when there is
    no new status.
    """
    if old is None or new is None or old == new:
        return []
    done: list[str] = []
    portal_identity = check.origin == "portal" and check.kind == "identity"
    if new == TECHNICAL_FAILURE:
        if portal_identity and await retry_portal_identity(session, check):
            done.append("photos_resent")
        elif check.origin == "portal" and await raise_failure_task(session, check):
            done.append("failure_task")
        return done
    if (
        portal_identity
        and old == "refer"
        and new in DECIDED
        and not await _decided_by_operator(session, check)
    ):
        # A referred identity check is decided by an operator only. The provider restating its
        # verdict (a second completion event for the same check) never overrides them.
        await _set_status(session, check, "refer")
        return done
    if is_monitoring_alert(check, old, new) and await raise_monitoring_task(session, check):
        done.append("monitoring_task")
    if old in REVIEWABLE and new in DECIDED:
        if await tasks.close_screening_task(session, tasks.check_task_ref(check.uid)):
            done.append("task_closed")
    if check.origin == "portal":
        if portal_identity and (new == "refer" or (new == "rejected" and old not in REVIEWABLE)):
            # The provider did not clear an identity try: Graphene reviews it, on every try. An
            # operator's own rejection comes from ``refer`` or ``failed`` and settles below.
            await hold_portal_identity(session, check, new)
            done.append("portal_review")
        # An AML refer starts a review; it is not the review's result. The kyc row keeps waiting
        # (and a monitored client who had passed stays passed) until the console decides: a
        # confirmed match settles it as referred there, and all matches dismissed settle it here
        # as clear.
        elif new != "refer" and await settle_portal_check(session, check, to_kyc_status(new)):
            done.append("portal")
    elif await queue_partner_delivery(session, check, old, new):
        done.append("partner")
    return done


async def _decided_by_operator(session: AsyncSession, check: CheckRecord) -> bool:
    """Whether an operator's verdict is on the check: ``decided_by`` with a clear or rejected
    status, which the console writes before it routes.

    A check held for review has no ``decided_by``, and one an operator asked new photos for is
    still ``refer``, so the provider restating its verdict on either decides nothing.
    """
    found = (
        await session.execute(
            text("SELECT decided_by, status FROM compliance.checks WHERE id = :id"),
            {"id": check.id},
        )
    ).first()
    return found is not None and found.decided_by is not None and found.status in DECIDED


async def _set_status(session: AsyncSession, check: CheckRecord, status: str) -> None:
    await session.execute(
        text("UPDATE compliance.checks SET status = :st, updated_at = now() WHERE id = :id"),
        {"st": status, "id": check.id},
    )


async def raise_monitoring_task(session: AsyncSession, check: CheckRecord) -> bool:
    """An operations task for a monitoring alert. False when the client is unknown or there is
    nowhere to put it; the check is still in the ``refer`` queue either way."""
    if check.contact_id is None or check.tenant_id is None:
        return False
    who, firm = await tasks.client_label(session, check.contact_id)
    suffix = check.provider_event_id or "alert"
    try:
        await tasks.raise_screening_task(
            session,
            contact_id=check.contact_id,
            tenant_id=check.tenant_id,
            title=f"Review a monitoring alert for {who}",
            description=(
                f"Ongoing monitoring referred an AML check for {who} ({firm}) again. "
                "Review the new matches in the AML console."
            ),
            source_ref=f"{tasks.check_task_ref(check.uid)}:{suffix}",
        )
    except PermanentDispatchError as exc:
        report_error(
            "monitoring alert task could not be raised; the check stays referred",
            exc=exc,
            log=logger,
            event="complycube_monitoring_task_failed",
        )
        return False
    return True


async def raise_failure_task(session: AsyncSession, check: CheckRecord) -> bool:
    """An operations task for a portal check the provider could not run.

    False when the client is unknown or there is nowhere to put it; the check is still in the
    ``refer`` queue either way.
    """
    if check.contact_id is None or check.tenant_id is None:
        return False
    who, firm = await tasks.client_label(session, check.contact_id)
    if check.kind == "aml":
        title = f"Screen for {who} failed — run it again"
        description = (
            f"ComplyCube could not run the AML screen for {who} ({firm}). This is not a result. "
            "Run the screen again from the AML console."
        )
    else:
        title = f"Identity check for {who} could not run"
        description = (
            f"ComplyCube could not run the identity check for {who} ({firm}). This is not a "
            "result. Review it in the AML console."
        )
    try:
        await tasks.raise_screening_task(
            session,
            contact_id=check.contact_id,
            tenant_id=check.tenant_id,
            title=title,
            description=description,
            source_ref=f"{tasks.check_task_ref(check.uid)}:failed",
        )
    except PermanentDispatchError as exc:
        report_error(
            "screening failure task could not be raised; the check stays in the queue",
            exc=exc,
            log=logger,
            event="complycube_failure_task_failed",
        )
        return False
    return True


async def settle_portal_check(
    session: AsyncSession, check: CheckRecord, outcome: KycOutcome
) -> bool:
    """Settle a portal check's verdict: the kyc row and the flow step waiting on it.

    ``passed`` completes the step (a clear screen, a cleared identity check, or a journey an
    operator allowed past a confirmed AML match); ``failed`` (an operator rejecting an identity
    check) fails it, so the case blocks visibly. A confirmed AML match never reaches here on its
    own: it holds the step until an operator allows it (``routers/aml.py::_settle_if_decided``).
    The step is released through ``orchestrator.on_kyc_result``, which records the outcome on the
    kyc row too. A kyc row no live step owns is written here directly.

    Returns False when the check has no kyc row or the outcome is still pending. A technical
    failure never reaches here (``route_result``), and its outcome is ``pending`` in any case.
    """
    if check.kyc_check_id is None or check.tenant_id is None or outcome.status == "pending":
        return False
    from app.flows import orchestrator

    released = await orchestrator.on_kyc_result(
        session,
        check.kyc_check_id,
        kind=check.kind,
        outcome=outcome.status,
        external_ref=check.provider_check_id,
        raw={"provider": "complycube", "check_uid": check.uid},
    )
    if check.kind == "identity":
        # The step has finished, so the photos held for it go.
        from app.idv import held

        await held.release_finished(session, tenant_id=check.tenant_id)
    if released:
        return True
    return await _record_kyc(session, check, outcome)


async def _record_kyc(session: AsyncSession, check: CheckRecord, outcome: KycOutcome) -> bool:
    """Write a verdict on a kyc row no live step owns. False when there is no such row."""
    passed = outcome.status == "passed"
    verified_at = (await session.execute(text("SELECT now()"))).scalar_one()
    valid_until = kyc_rules.compute_valid_until(verified_at, check.kind) if passed else None
    updated = await session.execute(
        text(
            "UPDATE crm.kyc_checks SET status = :st, "
            "  verified_at = COALESCE(:verified, verified_at), "
            "  valid_until = COALESCE(:vu, valid_until), "
            "  external_ref = COALESCE(:ref, external_ref), updated_at = now() "
            "WHERE id = :id AND tenant_id = :t "
            "RETURNING id"
        ),
        {
            "st": outcome.status,
            "verified": verified_at if passed else None,
            "vu": valid_until,
            "ref": check.provider_check_id,
            "id": check.kyc_check_id,
            "t": check.tenant_id,
        },
    )
    return updated.first() is not None


async def _capture_step(session: AsyncSession, check: CheckRecord) -> Any:
    """The live identity step with a photo capture that waits on this check, or None."""
    if check.kyc_check_id is None:
        return None
    from app.idv import capture

    step = await capture.step_for_check(session, check.kyc_check_id)
    if step is None or step.manual:
        return None
    return step


async def hold_portal_identity(session: AsyncSession, check: CheckRecord, verdict: str) -> None:
    """An identity try the provider did not clear waits for an operator, on any try.

    The check is held as ``refer`` and undecided, so the console marks its client as needing
    attention; the provider's own outcome stays in ``result``. The kyc row is untouched (pending).
    When a capture step waits on it, the client sees "a person is checking" (``review``) and the
    firm sees the step with Graphene for review.
    """
    if verdict != "refer":
        await _set_status(session, check, "refer")
    step = await _capture_step(session, check)
    if step is None:
        return
    from app.idv import capture

    await capture.set_status(session, step, capture.REVIEW)


async def _held_by_check(
    session: AsyncSession, check: CheckRecord, state: dict[str, Any]
) -> dict[str, Any] | None:
    """What ComplyCube holds of a try it checked (``capture.PROGRESS_KEY``): the check's document
    and live photo, held for each photo slot the try's document type needs. ``new_try`` keeps only
    what a kept slot holds. None when the check names neither."""
    from app.idv import capture

    row = (
        (
            await session.execute(
                text(
                    "SELECT provider_document_id, provider_live_photo_id "
                    "FROM compliance.checks WHERE id = :id"
                ),
                {"id": check.id},
            )
        )
        .mappings()
        .one_or_none()
    )
    document_type = state.get("document_type")
    if row is None or document_type not in capture.DOCUMENT_TYPES:
        return None
    slots = capture.item_uids(state)
    held: dict[str, Any] = {"accepted": {}}
    if row["provider_document_id"]:
        held["document_id"] = str(row["provider_document_id"])
        held["document_type"] = str(document_type)
        for key in capture.needed_keys(str(document_type)):
            if key != capture.SELFIE and key in slots:
                held["accepted"][key] = slots[key]
    if row["provider_live_photo_id"] and capture.SELFIE in slots:
        held["live_photo_id"] = str(row["provider_live_photo_id"])
        held["accepted"][capture.SELFIE] = slots[capture.SELFIE]
    return held if held["accepted"] else None


async def ask_for_photos(
    session: AsyncSession,
    check: CheckRecord,
    *,
    reopen: tuple[str, ...],
    last_reason: str | None,
) -> bool:
    """An operator asks the client for new photos of an identity try held for review.

    A new try opens at the next attempt that reopens only the ``reopen`` items and keeps the rest,
    which ComplyCube holds: the next send reuses a kept document or live photo, and a reopened
    document is made again, because its image was judged. ``last_reason`` is the client-safe
    reason the operator chose, or None for the portal's generic text. The check's operations
    tasks close. Nothing is sent to ComplyCube: this is not a decision on the check.

    False when no capture step waits on this check at this try in review.
    """
    from app.idv import capture

    step = await _capture_step(session, check)
    if (
        step is None
        or step.state.get("attempt") != check.attempt
        or step.state.get("status") != capture.REVIEW
    ):
        return False
    progress = await _held_by_check(session, check, step.state)
    await capture.new_try(
        session,
        step,
        attempt=next_attempt(check.attempt, check.status),
        last_reason=last_reason,
        reopen=reopen,
        progress=progress,
    )
    await tasks.close_screening_task(session, tasks.check_task_ref(check.uid))
    return True


#: How often a try's photos are sent again by themselves after ComplyCube could not run the check.
#: After that, operations look: a check that keeps failing would otherwise be paid for again and
#: again.
RESENDS_PER_TRY = 1


async def retry_portal_identity(session: AsyncSession, check: CheckRecord) -> bool:
    """ComplyCube could not run a portal identity check: the try's photos are sent again by
    themselves, at the SAME attempt, and the failed check is closed so it leaves the operators'
    queue. The client is asked for nothing: ComplyCube still holds the document and the live photo
    (and our encrypted copy can make the document again), so the photos were not at fault.

    False when no capture step waits on this check at this try, or the try's photos were already
    sent again ``RESENDS_PER_TRY`` times; the caller then raises the operations task.
    """
    from app.flows import notifications
    from app.idv import capture

    step = await _capture_step(session, check)
    if (
        step is None
        or check.kyc_check_id is None
        or step.state.get("attempt") != check.attempt
        or step.state.get("status") != capture.SUBMITTED
    ):
        return False

    resends = step.state.get(capture.RESENDS_KEY)
    document_type = step.state.get("document_type")
    if (isinstance(resends, int) and resends >= RESENDS_PER_TRY) or (
        document_type not in capture.DOCUMENT_TYPES
    ):
        return False
    progress = await _held_by_check(session, check, step.state) or {}
    resent = await capture.resend(session, step, progress=progress)
    await notifications.enqueue(
        session,
        tenant_id=step.tenant_id,
        event_type=RESEND_EVENT,
        payload={
            "contact_id": step.contact_id,
            "case_id": step.case_id,
            "step_key": step.step_key,
            "kyc_check_id": int(check.kyc_check_id),
            "attempt": resent.get("attempt"),
            "document_type": document_type,
            "items": capture.item_uids(resent),
        },
    )
    await session.execute(
        text(
            "UPDATE compliance.checks SET decided_at = now(), decision_comment = :comment, "
            "  updated_at = now() "
            "WHERE id = :id AND decided_at IS NULL"
        ),
        {"comment": FRESH_TRY_COMMENT, "id": check.id},
    )
    return True


#: The outbox event that sends a try's photos (``app.complycube.identity.EVENT``).
RESEND_EVENT = "identity.check.requested"

#: What the console shows on a failed portal identity check whose photos were sent again.
FRESH_TRY_COMMENT = "ComplyCube could not run this check. The client's photos were sent again."


async def queue_partner_delivery(
    session: AsyncSession, check: CheckRecord, old: str, new: str
) -> bool:
    """Queue ``check.updated`` for the firm's webhook. False when the firm has none, or the
    firm-visible status did not change (``failed`` and ``pending`` read the same)."""
    if check.tenant_id is None or partner_status(old) == partner_status(new):
        return False
    webhook_id = (
        await session.execute(
            text("SELECT id FROM compliance.webhooks WHERE tenant_id = :t"),
            {"t": check.tenant_id},
        )
    ).scalar_one_or_none()
    if webhook_id is None:
        return False
    payload = {
        "event": PARTNER_EVENT,
        "uid": check.uid,
        "kind": check.kind,
        "status": partner_status(new),
    }
    await session.execute(
        text(
            "INSERT INTO compliance.webhook_deliveries (tenant_id, webhook_id, event, payload) "
            "VALUES (:t, :w, :e, CAST(:p AS jsonb))"
        ),
        {"t": check.tenant_id, "w": int(webhook_id), "e": PARTNER_EVENT, "p": json.dumps(payload)},
    )
    return True


_REGISTER_CHECK = text(
    """
    INSERT INTO compliance.checks (
      tenant_id, client_id, kind, status, provider_check_id, origin, kyc_check_id, attempt,
      monitoring
    ) VALUES (
      :tenant_id, :client_id, :kind, 'pending', :provider_check_id, :origin, :kyc_check_id,
      :attempt, :monitoring
    )
    ON CONFLICT (provider_check_id) WHERE provider_check_id IS NOT NULL
    DO UPDATE SET origin = EXCLUDED.origin,
                  tenant_id = EXCLUDED.tenant_id,
                  client_id = EXCLUDED.client_id,
                  kind = EXCLUDED.kind,
                  kyc_check_id = EXCLUDED.kyc_check_id,
                  attempt = EXCLUDED.attempt,
                  monitoring = EXCLUDED.monitoring,
                  updated_at = now()
     WHERE compliance.checks.tenant_id IS NULL
        OR compliance.checks.tenant_id = EXCLUDED.tenant_id
    RETURNING id
    """
)


async def register_check(
    session: AsyncSession,
    *,
    origin: str,
    tenant_id: int,
    client_id: int,
    kind: str,
    provider_check_id: str,
    kyc_check_id: int | None,
    attempt: int = 1,
    monitoring: bool = False,
) -> CheckRecord:
    """Store a check we just started at the provider, and route it if its result is in.

    ``client_id`` is the ``compliance.clients`` row. The provider's webhook can land before this
    insert commits; the consumer then stores the row first (as partner origin, routing nothing).
    This claims it for ``origin`` and, when the provider has already finished, routes
    ``pending → status`` itself so the result is not left unrouted.

    Raises ``LookupError`` when the provider check id is held by another firm's row.
    """
    saved = await session.execute(
        _REGISTER_CHECK,
        {
            "origin": origin,
            "tenant_id": tenant_id,
            "client_id": client_id,
            "kind": kind,
            "provider_check_id": provider_check_id,
            "kyc_check_id": kyc_check_id,
            "attempt": attempt,
            "monitoring": monitoring,
        },
    )
    check_pk = saved.scalar()
    if check_pk is None:
        raise LookupError("that provider check belongs to another firm")
    record = await load_check(session, int(check_pk))
    if record is None:
        raise LookupError("the check row was not found after it was stored")
    if record.status != "pending":
        await route_result(session, record, "pending", record.status)
    return record


async def register_portal_check(
    session: AsyncSession,
    *,
    tenant_id: int,
    client_id: int,
    kind: str,
    provider_check_id: str,
    kyc_check_id: int | None,
    attempt: int = 1,
    monitoring: bool = False,
) -> CheckRecord:
    """Store a check the portal just started at the provider, and route it if its result is in.

    ``register_check`` with ``origin='portal'``: when the provider has already finished, the
    result is routed here rather than left waiting.
    """
    return await register_check(
        session,
        origin="portal",
        tenant_id=tenant_id,
        client_id=client_id,
        kind=kind,
        provider_check_id=provider_check_id,
        kyc_check_id=kyc_check_id,
        attempt=attempt,
        monitoring=monitoring,
    )
