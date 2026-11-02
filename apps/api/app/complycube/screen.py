"""Run a ComplyCube AML screen for a portal client: one path for the journey and the console.

A portal journey's AML step starts once identity passes, and the screen then runs by itself: the
step's adapter queues ``aml.screen.run`` on the flows outbox in the step's own transaction
(``enqueue``) and the worker runs it (``handle``). The console's Screen / Screen again buttons call
the same ``run_portal_screen``, so both behave alike:

* **Idempotent.** A client with an open AML check (pending, or a refer nobody has settled) gets that
  check back, and nothing is paid for. Callers serialise on a per-contact advisory lock, so a
  journey screen and an operator's click on the same client find one check.
* The new check takes the client's pending AML kyc row (a row whose only checks failed counts as
  waiting) and the attempt number that row is on, and closes the "Screen {client}" task. A client
  with none waiting — screened from the console with no journey's AML step, or screened again after
  their details changed — gets a new pending AML kyc row, so the screen's result is the client's
  AML record wherever it is read, and a later journey reuses a pass.

When the worker cannot run the screen for good — ComplyCube is not configured, ComplyCube refuses
the client, or the client record conflicts — it raises the "Screen {client}" operations task
instead, so a person picks the screen up from the console. A provider hiccup (429, 5xx, transport)
is left to the outbox's own retry.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from logging import getLogger
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.complycube import clients, routing, tasks
from app.complycube.client import (
    RETRYABLE_STATUSES,
    ComplyCubeNotConfigured,
    ScreeningClient,
    monitoring_of,
    require_client,
)
from app.complycube.outcomes import TECHNICAL_FAILURE, next_attempt
from app.flows.dispatcher import PermanentDispatchError
from app.ids import new_id
from app.logging import report_error

logger = getLogger(__name__)

EVENT = "aml.screen.run"
#: Ongoing monitoring for a new screen: always on (docs/aml.md §8). The console turns it back on
#: for a screen that has it off; it never turns it off.
DEFAULT_MONITORING = True


class ScreenConflict(Exception):
    """The screen cannot run as asked; the message says why (a console maps it to 409)."""


def screen_task_ref(contact_id: int, kyc_check_id: int | None) -> str:
    """The ``source_ref`` of a client's "Screen {client}" task."""
    return f"screen:{contact_id}:{kyc_check_id if kyc_check_id is not None else 'na'}"


def screen_task_refs(contact_id: int, kyc_check_id: int | None) -> list[str]:
    """Every ref a new screen closes: the kyc row's own, and the client's general one. Closing a
    ref closes the refs nested under it too, such as a re-screen task (``rescreen.py``)."""
    refs = [f"screen:{contact_id}:na"]
    if kyc_check_id is not None:
        refs.insert(0, screen_task_ref(contact_id, kyc_check_id))
    return refs


async def load_contact(
    session: AsyncSession,
    *,
    contact_uid: str | None = None,
    contact_id: int | None = None,
    tenant_id: int | None = None,
) -> Mapping[str, Any] | None:
    """A live portal contact, by uid or id, optionally held to one firm."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, tenant_id, type, name, first_name, surname, dob, "
                    "       nationality, email, address "
                    "FROM crm.contacts "
                    "WHERE deleted_at IS NULL "
                    "  AND (CAST(:uid AS text) IS NULL OR uid = CAST(:uid AS text)) "
                    "  AND (CAST(:id AS int) IS NULL OR id = CAST(:id AS int)) "
                    "  AND (CAST(:t AS int) IS NULL OR tenant_id = CAST(:t AS int))"
                ),
                {"uid": contact_uid, "id": contact_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


async def run_portal_screen(
    session: AsyncSession, screening: ScreeningClient, contact: Mapping[str, Any]
) -> str:
    """Screen one portal contact, or return the open check they already have. Returns the
    ``compliance.checks`` uid.

    Raises ``ScreenConflict`` for a client record that cannot be bound or a check that cannot be
    registered, and lets ``ComplyCubeNotConfigured`` and the provider's HTTP errors through for the
    caller to map.
    """
    contact_id = int(contact["id"])
    tenant_id = int(contact["tenant_id"])
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"aml-screen:{contact_id}"}
    )
    open_check = (
        await session.execute(
            text(
                "SELECT ch.uid FROM compliance.checks ch "
                "JOIN compliance.clients cl ON cl.id = ch.client_id "
                "WHERE cl.tenant_id = :t AND cl.contact_id = :c AND ch.kind = 'aml' "
                "  AND (ch.status = 'pending' OR (ch.status = 'refer' AND ch.decided_at IS NULL)) "
                "ORDER BY ch.id DESC LIMIT 1"
            ),
            {"t": tenant_id, "c": contact_id},
        )
    ).scalar_one_or_none()
    if open_check is not None:
        return str(open_check)

    try:
        client_pk = await clients.bind_portal_client(session, screening, dict(contact))
    except clients.ClientConflict as exc:
        raise ScreenConflict(str(exc)) from exc
    kyc = (
        await session.execute(
            text(
                "SELECT k.id FROM crm.kyc_checks k "
                "WHERE k.tenant_id = :t AND k.contact_id = :c AND k.kind = 'aml' "
                "  AND k.status = 'pending' "
                "  AND NOT EXISTS (SELECT 1 FROM compliance.checks x "
                "                   WHERE x.kyc_check_id = k.id AND x.status <> :failed) "
                "ORDER BY k.created_at DESC, k.id DESC LIMIT 1"
            ),
            {"t": tenant_id, "c": contact_id, "failed": TECHNICAL_FAILURE},
        )
    ).scalar_one_or_none()
    kyc_check_id = int(kyc) if kyc is not None else None
    attempt = 1
    if kyc_check_id is None:
        kyc_check_id = int(
            (
                await session.execute(
                    text(
                        "INSERT INTO crm.kyc_checks "
                        "  (uid, tenant_id, contact_id, kind, status, provider) "
                        "VALUES (:u, :t, :c, 'aml', 'pending', 'complycube') RETURNING id"
                    ),
                    {"u": new_id(), "t": tenant_id, "c": contact_id},
                )
            ).scalar_one()
        )
    else:
        previous = (
            await session.execute(
                text(
                    "SELECT attempt, status FROM compliance.checks WHERE kyc_check_id = :k "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"k": kyc_check_id},
            )
        ).first()
        if previous is not None:
            attempt = next_attempt(int(previous.attempt), str(previous.status))

    provider_client_id = await clients.provider_client_id(session, client_pk)
    if not provider_client_id:
        raise ScreenConflict("This client has no ComplyCube record.")
    created = await screening.create_screening(provider_client_id, monitoring=DEFAULT_MONITORING)
    try:
        record = await routing.register_portal_check(
            session,
            tenant_id=tenant_id,
            client_id=client_pk,
            kind="aml",
            provider_check_id=str(created["id"]),
            kyc_check_id=kyc_check_id,
            attempt=attempt,
            monitoring=monitoring_of(created, DEFAULT_MONITORING),
        )
    except LookupError as exc:
        raise ScreenConflict(str(exc)) from exc
    for ref in screen_task_refs(contact_id, kyc_check_id):
        await tasks.close_screening_task(session, ref)
    return record.uid


async def enqueue(
    session: AsyncSession, *, tenant_id: int, contact_id: int, kyc_check_id: int | None
) -> None:
    """Queue the screen in the caller's transaction, so it runs exactly when the step starts."""
    await session.execute(
        text(
            "INSERT INTO flows.outbox (tenant_id, event_type, payload) "
            "VALUES (:t, :et, CAST(:p AS jsonb))"
        ),
        {
            "t": tenant_id,
            "et": EVENT,
            "p": json.dumps({"contact_id": contact_id, "kyc_check_id": kyc_check_id}),
        },
    )


async def raise_screen_task(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    kyc_check_id: int | None,
    reason: str,
) -> None:
    """Ask operations to screen the client from the console, saying why it did not run itself."""
    who, firm = await tasks.client_label(session, contact_id)
    try:
        await tasks.raise_screening_task(
            session,
            contact_id=contact_id,
            tenant_id=tenant_id,
            title=f"Screen {who}",
            description=(
                f"{firm} is onboarding {who} and their journey is waiting on an AML screen "
                f"that could not run by itself ({reason}). Screen them from the AML console, "
                "then decide any matches."
            ),
            source_ref=screen_task_ref(contact_id, kyc_check_id),
        )
    except PermanentDispatchError as exc:
        # The client still shows in the console as due a screen, so the screen is not lost.
        report_error(
            "AML screen task could not be raised; the client shows as due a screen in the console",
            exc=exc,
            log=logger,
            event="complycube_screen_task_failed",
        )


async def handle(session: AsyncSession, tenant_id: int, payload: dict[str, Any]) -> None:
    """Run one queued ``aml.screen.run``. A provider hiccup raises (the outbox retries); anything
    that will not succeed on a retry raises the operations task instead."""
    contact_id = payload.get("contact_id")
    if not isinstance(contact_id, int):
        raise PermanentDispatchError(f"{EVENT}: no contact_id in the payload")
    raw_kyc = payload.get("kyc_check_id")
    kyc_check_id = raw_kyc if isinstance(raw_kyc, int) else None
    contact = await load_contact(session, contact_id=contact_id, tenant_id=tenant_id)
    if contact is None:
        raise PermanentDispatchError(f"{EVENT}: contact {contact_id} not found")
    reason: str
    try:
        uid = await run_portal_screen(session, require_client(), contact)
    except ComplyCubeNotConfigured:
        reason = "ComplyCube is not configured"
    except ScreenConflict as exc:
        reason = str(exc).rstrip(".")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in RETRYABLE_STATUSES:
            raise
        reason = f"ComplyCube refused it with HTTP {exc.response.status_code}"
    else:
        logger.info(
            "complycube: AML screen started for contact %s (check %s)",
            contact_id,
            uid,
            extra={"event": "complycube_screen_started"},
        )
        return
    logger.warning(
        "complycube: AML screen for contact %s did not run (%s); operations is asked to screen",
        contact_id,
        reason,
        extra={"event": "complycube_screen_handed_to_ops"},
    )
    await raise_screen_task(
        session,
        tenant_id=tenant_id,
        contact_id=contact_id,
        kyc_check_id=kyc_check_id,
        reason=reason,
    )
