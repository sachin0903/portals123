"""Screen a client again when the details their AML screen searched have changed.

A change to a client's name, date of birth or nationality — the details ComplyCube screens — makes
their latest AML screen a screen of someone else's details. Migration 0253's trigger on
``crm.contacts`` marks that check stale (``compliance.checks.stale_at``), expires the pass it gave
(its kyc row), so no journey reuses it, and queues one ``aml.rescreen.requested`` outbox row with
``{contact_id, check_uid}``. It is a trigger so that every write path reaches it: the client
record, a flow's form write-back, the partner API and the provider pipelines.

The outbox dispatcher calls ``rescreen`` for that row:

* After a clear screen, or one Graphene let continue past its confirmed matches, the client is
  screened again by itself (``screen.run_portal_screen``), on the details they have now. When the
  screen cannot run for good — ComplyCube is not configured, refuses the client, or the client
  record conflicts — operations is asked to screen them instead; a provider hiccup is left to the
  outbox's own retry.
* After a rejected screen, or one still under review, nothing is screened by itself: a new clear
  screen must never undo Graphene's rejection or step round its review. Operations is asked to
  review the client and screen them again.

The task's ``source_ref`` is ``screen:{contact_id}:na:stale:{check_uid}``. It sits under
``screen:{contact_id}:na``, which a screen closes with every ref under it, so screening the client
closes it. The check uid keeps a later stale screen from colliding with this one on the
``tasks_source_ref_key`` idempotency index, and makes a redelivered row raise nothing new.

A row whose check has been screened again (a later AML check exists), or no longer exists, does
nothing: there is nothing left to screen again.

Runs as ``service_role``: no request principal, so every write names its tenant explicitly.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.complycube import screen
from app.complycube.client import RETRYABLE_STATUSES, ComplyCubeNotConfigured, require_client
from app.complycube.outcomes import allowed_past_match
from app.complycube.tasks import client_label, raise_screening_task
from app.flows.dispatcher import PermanentDispatchError

logger = logging.getLogger(__name__)

#: The ``flows.outbox.event_type`` migration 0253's trigger queues.
EVENT = "aml.rescreen.requested"


def rescreen_task_ref(contact_id: int, check_uid: str) -> str:
    """The ``source_ref`` of the task to screen this client again after this check went stale."""
    return f"screen:{contact_id}:na:stale:{check_uid}"


async def rescreen(session: AsyncSession, tenant_id: int, payload: dict[str, Any]) -> None:
    """Handle one ``aml.rescreen.requested`` outbox row."""
    contact_id = payload.get("contact_id")
    if isinstance(contact_id, bool) or not isinstance(contact_id, int):
        raise PermanentDispatchError(f"{EVENT} carried no contact_id")
    check_uid = payload.get("check_uid")
    if not isinstance(check_uid, str) or not check_uid:
        raise PermanentDispatchError(f"{EVENT} carried no check_uid")

    stale = (
        await session.execute(
            text(
                "SELECT ch.status, ch.origin, ch.decided_at FROM compliance.checks ch "
                "JOIN compliance.clients cl ON cl.id = ch.client_id "
                "WHERE ch.uid = :uid AND cl.contact_id = :c AND cl.tenant_id = :t "
                "  AND ch.stale_at IS NOT NULL "
                "  AND NOT EXISTS (SELECT 1 FROM compliance.checks later "
                "                   WHERE later.client_id = ch.client_id "
                "                     AND later.kind = 'aml' AND later.id > ch.id)"
            ),
            {"uid": check_uid, "c": contact_id, "t": tenant_id},
        )
    ).one_or_none()
    if stale is None:
        logger.info(
            "aml: stale check %s for contact %s was screened again or is gone; nothing to do",
            check_uid,
            contact_id,
        )
        return

    status = str(stale.status)
    if status == "clear" or allowed_past_match(
        status, stale.decided_at is not None, str(stale.origin)
    ):
        reason = await _screen_again(session, tenant_id, contact_id)
        if reason is None:
            return
        await _ask_operations(
            session,
            tenant_id,
            contact_id,
            check_uid,
            "changed after their AML screen, and the new screen could not run by itself "
            f"({reason}). Screen them again from the AML console.",
        )
        return
    when = (
        "changed after a rejected AML screen."
        if status == "rejected"
        else "changed while their AML screen is under review."
    )
    await _ask_operations(
        session,
        tenant_id,
        contact_id,
        check_uid,
        f"{when} A new screen does not run by itself: review the client and screen them again "
        "from the AML console.",
    )


async def _screen_again(session: AsyncSession, tenant_id: int, contact_id: int) -> str | None:
    """Screen the client on their current details. None when the screen started, else why it
    cannot run on a retry either. A provider hiccup raises, for the outbox to retry."""
    contact = await screen.load_contact(session, contact_id=contact_id, tenant_id=tenant_id)
    if contact is None:
        raise PermanentDispatchError(f"{EVENT}: contact {contact_id} not found")
    try:
        uid = await screen.run_portal_screen(session, require_client(), contact)
    except ComplyCubeNotConfigured:
        return "ComplyCube is not configured"
    except screen.ScreenConflict as exc:
        return str(exc).rstrip(".")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in RETRYABLE_STATUSES:
            raise
        return f"ComplyCube refused it with HTTP {exc.response.status_code}"
    logger.info(
        "aml: contact %s changed after their screen; screened again (check %s)",
        contact_id,
        uid,
        extra={"event": "complycube_rescreen_started"},
    )
    return None


async def _ask_operations(
    session: AsyncSession, tenant_id: int, contact_id: int, check_uid: str, what: str
) -> None:
    who, firm = await client_label(session, contact_id)
    await raise_screening_task(
        session,
        contact_id=contact_id,
        tenant_id=tenant_id,
        title=f"Screen {who} again",
        description=f"The name, date of birth or nationality of {who} ({firm}) {what}",
        source_ref=rescreen_task_ref(contact_id, check_uid),
    )
    logger.info("aml: contact %s changed after their screen; operations asked", contact_id)
