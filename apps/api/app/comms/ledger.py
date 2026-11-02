"""The outbound-email ledger — one row per send, and the provider's callbacks against it.

Schema: ``0116_comms_messages``. This module is the ONLY writer, and it has exactly two callers:
``app.email.send_email`` (which records every outcome, including the ones where nothing was posted)
and ``app.routers.resend_webhook`` (which advances a row as Resend reports on it).

**Why a ledger at all.** ``send_email`` returned a bare ``bool``, discarded the id Resend answers
with, and wrote nothing. So the only durable evidence a client had been emailed was a
``flows.outbox`` row reading ``delivered`` — which means "Resend accepted it", not "it arrived" —
and a firm asking "did my client get the suitability report" had nothing to read at all.

**One INSERT, after the attempt.** Not a queued row updated on completion. ``flows.outbox`` already
IS the queued state for notification email, so a pre-POST row would buy only the window in which
the process dies mid-``httpx`` — and it would cost a second write on every send. That is the same
best-effort floor ``audit.actions`` accepts, and it is stated here rather than discovered later: a
process killed between the POST and the INSERT sends an email this ledger does not know about.

**It writes on its own connection.** ``record`` opens a ``system_transaction`` rather than taking
the caller's session, for the mirror of ``flows/ai_usage.py``'s reason. There the meter must survive
the rollback of the work it measured; here the mail has ALREADY GONE by the time we write, so a
ledger row that rolls back with its caller would be a record of a send that really happened being
erased because something unrelated failed afterwards. It also means ``send_email`` needs no session,
which matters because half its callers (the worker's loops) have none.

**It never raises.** An email must not fail because the ledger did. A write failure is reported and
swallowed; the cost is a missing row on a database blip, which is the right way round.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import system_transaction
from app.ids import new_id
from app.logging import report_error

logger = logging.getLogger(__name__)

#: A branded email is 20–60KB. Above this the body is stored as NULL rather than truncated: half an
#: email renders as a broken one, and a broken preview reads as corruption rather than as "too big".
MAX_HTML_BYTES = 256 * 1024

#: ``comms.messages.status``'s CHECK, split by what each group means.
#:
#: Statuses that ADVANCE as the provider reports, in rank order. A callback may only ever move a row
#: forwards through these — the ``contributions.ml_status`` rule, and for the same reason: Resend
#: delivers at least once and out of order, so a re-delivered ``email.sent`` arriving after
#: ``email.delivered`` must not walk the row backwards.
STATUS_RANK: dict[str, int] = {
    "sent": 1,
    # Between sent and delivered: a delay advances a send, and a delivery still supersedes it —
    # which is the usual ending, since a delayed message normally does arrive.
    "delayed": 2,
    "delivered": 3,
}

#: Set at INSERT and never advanced: nothing was posted, so there is no provider to have an opinion.
#: (In practice unreachable from the webhook anyway — a row with no ``provider_message_id`` cannot
#: be looked up — but the rule is written down rather than left to that coincidence.)
TERMINAL_AT_INSERT: frozenset[str] = frozenset({"unconfigured", "suppressed", "failed"})

#: These win from ANY rank. A bounce after a delivery is real — a deferred address that later hard
#: bounces — and it is the more important fact of the two, so it is not treated as "backwards".
OVERRIDING: frozenset[str] = frozenset({"bounced", "complained"})

#: Resend's event vocabulary → the status it implies.
#:
#: ``email.delivery_delayed`` was mapped to None on the reasoning that a delay is progress rather
#: than a state. A real mailbox disproved it: a delayed message can sit retrying for hours, and
#: reporting it as ``sent`` makes "it has not arrived and we are still trying" indistinguishable
#: from "handed over a second ago" — which is the one thing a firm chasing a client needs to see.
#: ``email.failed`` stays None: it is the provider giving up on a message we already recorded a
#: terminal status for, and it carries no state the bounce/complaint arms do not.
#:
#: Opens and clicks are absent BY DECISION, not by omission — see ``NOT_TRACKED``.
EVENT_STATUS: dict[str, str | None] = {
    "email.sent": "sent",
    "email.delivered": "delivered",
    "email.delivery_delayed": "delayed",
    "email.bounced": "bounced",
    "email.complained": "complained",
    "email.failed": None,
}

#: Callbacks this product does not record — READ RECEIPTS AND THE LIKE.
#:
#: An open is a tracking pixel loading, and it answers neither question a firm has: Apple Mail
#: Privacy Protection prefetches images for every Apple Mail user, so an "open" happens whether or
#: not a human looked, and a client that blocks images produces no open however carefully it was
#: read. So the signal is not evidence in either direction, while collecting it means putting a
#: tracking pixel in a client's email — the firm's decision to make, not ours, and not one worth
#: making for a figure nobody can rely on.
#:
#: Refused at the door rather than stored-and-ignored: these callbacks are dropped before any row
#: is written, so their payloads never enter the database. Resend only emits them when open/click
#: tracking is switched on in its dashboard, so with it off this set never fires at all.
NOT_TRACKED: frozenset[str] = frozenset({"email.opened", "email.clicked"})

#: status → the column that stamps when it happened. A CLOSED literal map, and the only reason it is
#: safe to interpolate a column name into SQL below: the key is a status this module derived from
#: ``EVENT_STATUS``, never a string off the wire.
STAMP_COLUMN: dict[str, str] = {
    "sent": "sent_at",
    "delayed": "delayed_at",
    "delivered": "delivered_at",
    "bounced": "bounced_at",
    "complained": "complained_at",
}


@dataclass(frozen=True)
class SendContext:
    """Why this email is being sent — everything the ledger needs that the envelope does not carry.

    One value rather than eight loose keyword arguments on ``send_email``, so adding a field is a
    change in one place and a call site that does not care carries nothing.

    Every field is optional because the send paths genuinely differ: a transactional invite has a
    ``template_code`` and no event; a notification email has both plus a ``rule_uid``; a flow's
    ad-hoc email action has neither. ``tenant_id`` is None for a Graphene-side send, and such a row
    is readable by Graphene operations alone.
    """

    tenant_id: int | None = None
    template_code: str | None = None
    event_type: str | None = None
    rule_uid: str | None = None
    category: str | None = None
    recipient_contact_id: int | None = None
    recipient_user_id: str | None = None
    source: str = "app"

    @property
    def recipient_kind(self) -> str:
        """Which of the three recipient shapes this is, derived rather than declared — so the kind
        and the ids can never disagree, which is what ``messages_recipient_ref`` refuses."""
        if self.recipient_contact_id is not None:
            return "contact"
        if self.recipient_user_id is not None:
            return "user"
        return "external"


def advance(current: str, incoming: str) -> str | None:
    """The monotonic status rule: the new status, or None when the row must not move.

    Pure, because this is the whole correctness of the delivery timeline and it has to be testable
    without a database or a webhook. Four rules, in order:

    1. Nothing was posted (``TERMINAL_AT_INSERT``) ⇒ never move.
    2. An overriding status (bounced/complained) wins from anywhere, unless it is already set.
    3. Once overriding, a ranked status never walks it back — an out-of-order ``delivered`` arriving
       after a bounce does not mean the mail arrived.
    4. Otherwise strictly forwards by rank.
    """
    if current in TERMINAL_AT_INSERT:
        return None
    if incoming in OVERRIDING:
        return None if incoming == current else incoming
    if current in OVERRIDING:
        return None
    if incoming not in STATUS_RANK:  # pragma: no cover - guarded by EVENT_STATUS
        return None
    return incoming if STATUS_RANK[incoming] > STATUS_RANK.get(current, 0) else None


def _capped(html: str | None) -> str | None:
    """The body as stored: whole, or absent. Never a fragment — see ``MAX_HTML_BYTES``."""
    if not html:
        return None
    return None if len(html.encode("utf-8")) > MAX_HTML_BYTES else html


#: What replaces the body's ``html`` when the request is too big to store whole. A MARKER, not a
#: removal: a Raw pane showing a POST with no ``html`` key reads as "we sent an empty email", which
#: is a worse answer than "the body was too big to keep" and is indistinguishable from a bug.
ELIDED_HTML = "[…elided: the body was over the ledger's size cap and is not stored here…]"


def _capped_request(request: dict[str, object] | None) -> str | None:
    """The wire request as stored JSON. Same whole-or-nothing rule as ``html``.

    If the serialised object is over the cap, the body's ``html`` is replaced by ``ELIDED_HTML``
    first — it is the part that blows the budget, and what remains (``from``, ``to``, ``subject``,
    ``reply_to``) is what an operator asking "what did we actually send" needs most. If it still
    does not fit, nothing is stored rather than a truncated request.

    ``default=str`` for the same reason ``apply_provider_event``'s dump has it: this runs inside
    ``record``'s try block, so a value JSON cannot serialise (a Decimal, a datetime, bytes on a
    future attachment) would raise, be swallowed there, and cost the send its ENTIRE ledger row —
    an optional diagnostic column taking the required record down with it.
    """
    if not request:
        return None
    dumped = json.dumps(request, default=str)
    if len(dumped.encode("utf-8")) <= MAX_HTML_BYTES:
        return dumped
    body = request.get("body")
    if isinstance(body, dict) and "html" in body:
        slim = {**request, "body": {**body, "html": ELIDED_HTML}}
        dumped = json.dumps(slim, default=str)
        if len(dumped.encode("utf-8")) <= MAX_HTML_BYTES:
            return dumped
    return None


async def record(
    ctx: SendContext | None,
    *,
    to: str,
    subject: str,
    status: str,
    html: str | None = None,
    request: dict[str, object] | None = None,
    provider_message_id: str | None = None,
    error: str | None = None,
) -> None:
    """Append one row for one send attempt, on this module's own connection. Never raises.

    ``ctx`` may be None — an un-threaded call site degrades to a Graphene-side row (no tenant)
    rather than to no row at all, so the ledger stays complete while the call sites are migrated
    and a forgotten one is visible in operations rather than silently absent.

    ``to`` must be the NORMALISED single address that was actually posted, not the caller's raw
    value; ``send_email`` passes ``_single_address``'s answer for exactly that reason.
    """
    ctx = ctx or SendContext()
    # Local import: `app.email.transport` imports this module at its top level, so importing it
    # here would close the cycle. Function-level is the same break `flows/dispatcher.py` uses for
    # the AML screening entry point, and it keeps ONE implementation of "the domain of an address"
    # rather than a second copy that can drift from the one the guard and the logs use.
    from app.email.transport import _domain

    try:
        async with system_transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO comms.messages "
                    "  (uid, tenant_id, provider_message_id, recipient_kind, recipient_contact_id, "
                    "   recipient_user_id, to_address, to_domain, subject, template_code, "
                    "   event_type, rule_uid, category, source, status, error, html, "
                    "   request, sent_at) "
                    "VALUES (:uid, :t, :pmid, :kind, :cid, :uidr, :addr, :dom, :subj, :tpl, "
                    "        :evt, :rule, :cat, :src, :status, :err, :html, "
                    "        CAST(:request AS jsonb), "
                    # The send stamp belongs to the row that says it was sent, and to no other.
                    "        CASE WHEN :status = 'sent' THEN now() ELSE NULL END)"
                ),
                {
                    "uid": new_id(),
                    "t": ctx.tenant_id,
                    "pmid": provider_message_id,
                    "kind": ctx.recipient_kind,
                    "cid": ctx.recipient_contact_id,
                    "uidr": ctx.recipient_user_id,
                    "addr": to,
                    "dom": _domain(to),
                    "subj": subject[:500],
                    "tpl": ctx.template_code,
                    "evt": ctx.event_type,
                    "rule": ctx.rule_uid,
                    "cat": ctx.category,
                    "src": ctx.source if ctx.source in ("app", "worker") else "app",
                    "status": status,
                    "err": error[:500] if error else None,
                    "html": _capped(html),
                    "request": _capped_request(request),
                },
            )
    except Exception as exc:  # noqa: BLE001 - a ledger must never fail the send it is recording
        report_error(
            "could not record an outbound email; the send still happened",
            exc=exc,
            log=logger,
            event="comms_ledger_write_failed",
            tenant_id=ctx.tenant_id,
            status=status,
            # The domain only. This line lands wherever logs land, and `app/email.py` spends a
            # paragraph on why a recipient does not belong in one.
            recipient_domain=_domain(to),
        )


async def apply_provider_event(
    session: AsyncSession,
    *,
    provider_message_id: str,
    event_type: str,
    provider_event_id: str | None,
    occurred_at: datetime | None,
    payload: dict[str, object],
) -> bool:
    """Record one provider callback against its message. True when a message was found.

    Takes a session because its caller (the webhook) already owns a ``system_transaction``, so one
    callback is one transaction and a redelivery cannot half-apply.

    Two things happen, in this order and deliberately not conditional on each other:

    * the callback is appended to ``comms.message_events`` (``ON CONFLICT DO NOTHING`` on the
      provider's own id — this is what makes redelivery a no-op);
    * the parent's status advances per :func:`advance`, and the matching stamp is filled with
      ``COALESCE`` so a later duplicate never overwrites the first time we were told.

    The stamp is written even when the status does NOT advance: an ``email.sent`` arriving after
    ``email.delivered`` still tells us when it was sent, and the timeline wants both. Refusing the
    stamp because the status did not move would throw away the one new fact in the callback.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, tenant_id, status FROM comms.messages "
                    "WHERE provider_message_id = :pmid "
                    # Newest wins: a provider id is unique in practice, but this is an index and not
                    # a unique constraint (a re-used id must not be able to fail an INSERT on the
                    # send path, which is the path that matters).
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"pmid": provider_message_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        # Not an error, and not worth an ERROR log: a callback for a pruned row, and one for mail
        # sent by another deployment sharing this Resend account, look exactly like this.
        return False

    await session.execute(
        text(
            "INSERT INTO comms.message_events "
            "  (message_id, tenant_id, event_type, provider_event_id, occurred_at, payload) "
            "VALUES (:mid, :t, :et, :peid, :occ, CAST(:p AS jsonb)) "
            "ON CONFLICT ON CONSTRAINT message_events_provider_key DO NOTHING"
        ),
        {
            "mid": row["id"],
            "t": row["tenant_id"],
            "et": event_type[:100],
            "peid": provider_event_id,
            "occ": occurred_at,
            "p": json.dumps(payload, default=str),
        },
    )

    status = EVENT_STATUS.get(event_type)
    if status is None:
        return True
    stamp = STAMP_COLUMN[status]
    new_status = advance(str(row["status"]), status)
    # `stamp` is a value of the closed STAMP_COLUMN map, keyed by a status this module derived from
    # EVENT_STATUS — never a string off the wire. That is what makes this interpolation safe, and
    # it is the only interpolation in the module.
    await session.execute(
        text(
            # Three-way COALESCE, and the `now()` arm matters: a provider that sends no timestamp
            # would otherwise leave a DELIVERED message with a NULL delivered_at, which the
            # timeline reads as "this never happened". When we learned of it is a worse answer than
            # when it happened, and a far better one than nothing. The first arm is what makes a
            # redelivery harmless — the earliest time we were told always wins.
            f"UPDATE comms.messages SET {stamp} = COALESCE({stamp}, :occ, now()), "  # noqa: S608
            "    status = COALESCE(:new_status, status) "
            "WHERE id = :mid"
        ),
        {"occ": occurred_at, "new_status": new_status, "mid": row["id"]},
    )
    return True
