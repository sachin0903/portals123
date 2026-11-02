"""The notification outbox — reliable external delivery for orchestration events.

The engine writes an outbox row in the SAME transaction as a state transition (transactional
outbox — no lost/duplicate notifications on the concurrency paths). A deliverer drains it out of
band, reusing the worker idiom (`FOR UPDATE SKIP LOCKED`, backoff-retry, dead-letter) and
`app.email.send_email`. Facts live in `flows.domain_events`; *delivery* state lives here.

The backoff/dead-letter decisions are pure + unit-tested. **There is exactly ONE drain, and it
lives in ``app/flows/dispatcher.py``** (`dispatch_due`/`dispatch_once`, joined by the worker's
outbox loop), which routes by ``event_type`` — task requests to ``tasks.tasks``, anything
addressable to ``_send``. Do not add a second one here: two loops over the same queue race, and a
duplicate's own dead-letter reasons make the error provenance meaningless.
"""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.comms import ledger
from app.email import transport as email_mod

_BASE_DELAY_S = 60
_MAX_DELAY_S = 3600


def next_retry_seconds(attempts: int) -> int:
    """Exponential backoff (60s, 120s, 240s, … capped at 1h) for the Nth failed attempt."""
    return int(min(_BASE_DELAY_S * (2 ** max(0, attempts)), _MAX_DELAY_S))


def should_dead_letter(attempts: int, max_attempts: int) -> bool:
    return attempts >= max_attempts


async def enqueue(
    session: AsyncSession,
    *,
    tenant_id: int,
    event_type: str,
    payload: dict[str, object],
    domain_event_id: int | None = None,
) -> None:
    """Enqueue an external notification (same tx as the transition). ``payload`` carries ``to`` +
    ``subject`` + ``html`` (built by the caller from the branded template)."""
    await session.execute(
        text(
            "INSERT INTO flows.outbox (tenant_id, domain_event_id, event_type, payload) "
            "VALUES (:t, :de, :et, CAST(:p AS jsonb))"
        ),
        {"t": tenant_id, "de": domain_event_id, "et": event_type, "p": json.dumps(payload)},
    )


async def _send(tenant_id: int, payload: dict[str, object]) -> bool:
    to = payload.get("to")
    subject = str(payload.get("subject") or "Update on your application")
    html = str(payload.get("html") or "")
    if not isinstance(to, str) or not to or not html:
        return (
            True  # nothing addressable — treat as delivered (don't retry a malformed row forever)
        )
    # The envelope identity comes OUT OF THE PAYLOAD, which the producer resolved against the DB
    # (``documents/service.py::queue_client_email``). It cannot be re-derived here — this runs in
    # the worker process, which has no request-path session, and ``sender_for``'s cache is cold
    # there for good (see its docstring). A row queued before the payload carried these keys still
    # has to dispatch, so an absent value falls back rather than raising.
    sender = payload.get("sender")
    reply_to = payload.get("reply_to")
    return await email_mod.send_email(
        to=to,
        subject=subject,
        html=html,
        sender=sender if isinstance(sender, str) and sender else email_mod.sender_for(tenant_id),
        reply_to=reply_to if isinstance(reply_to, str) and reply_to else None,
        ctx=_send_context(tenant_id, payload),
    )


def _int_or_none(value: object) -> int | None:
    """A payload value as an int, or None. The payload is JSON we wrote, but it has been through a
    jsonb round trip and may predate the key entirely, so nothing here may raise."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _send_context(tenant_id: int, payload: dict[str, object]) -> ledger.SendContext:
    """The outbound-email ledger's context, read out of the outbox payload.

    Read rather than derived, for the reason ``_send`` already reads ``sender``/``reply_to`` from
    the payload: this runs in the WORKER, which has no request session and cannot resolve a rule, a
    template or a recipient. The producer resolved all of it (``notifications/email_consumer.py``)
    and put it in the row.

    A row queued before the ``comms`` key existed has none, and still dispatches — it just records
    with the tenant alone. ``source`` is always ``worker`` here, because there is only one drain and
    it is the worker's.
    """
    meta = payload.get("comms")
    if not isinstance(meta, dict):
        return ledger.SendContext(tenant_id=tenant_id, source="worker")
    return ledger.SendContext(
        tenant_id=tenant_id,
        template_code=_str_or_none(meta.get("template_code")),
        event_type=_str_or_none(meta.get("event_type")),
        rule_uid=_str_or_none(meta.get("rule_uid")),
        category=_str_or_none(meta.get("category")),
        recipient_contact_id=_int_or_none(meta.get("recipient_contact_id")),
        recipient_user_id=_str_or_none(meta.get("recipient_user_id")),
        source="worker",
    )


# There is deliberately no HTML shell in this module. Task notices — the only thing that ever used
# one here — render through the branded store/render path instead (`app/tasks/notifier.py` →
# `email_render.render_or_default` on `task_assigned` or `task_reminder`), so a firm's own template
# for that code overrides them and the shipped default carries the firm's brand rather than a
# hardcoded "Graphene" wordmark. A future `notify.*` producer must build its HTML the same branded
# way, not resurrect an unbranded shell.
