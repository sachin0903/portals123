"""The delivery FALLBACK: ask Resend what became of a message, when it cannot tell us itself.

The webhook (``app/routers/resend_webhook.py``) is the primary path and stays so — it is pushed,
immediate, and carries the provider's own event id for idempotency. This runs ALONGSIDE it as a
net, not instead of it, because the webhook is the half that fails quietly:

* **it cannot reach ``localhost``**, so on a dev box every message sits at ``sent`` for ever and
  the whole Activity timeline reads as broken;
* **an endpoint deleted in the provider's dashboard, a rotated signing secret, or a deploy that
  lost ``RESEND_WEBHOOK_SECRET``** all look identical from here — callbacks simply stop, and
  nothing announces that they have.

A message stuck at ``sent`` is indistinguishable from one nobody has looked at yet, so there is no
alert to build; the net is what turns "we stopped hearing" into "we asked". With a working webhook
it finds nothing and makes no provider call, so running both is close to free.

It is a poll of ``GET /emails/{id}``, which answers with a ``last_event`` — the provider's current
opinion, not a stream of them. That is enough: the ledger's rank only ever moves forwards, so
learning "delivered" late is the same outcome as being told at the time.

**It writes through the SAME path as the webhook** (``ledger.apply_provider_event``), so the rank,
the stamps, the read-receipt refusal and the idempotency are enforced in one place and cannot drift
between the two. The synthetic ``provider_event_id`` is ``poll:{last_event}``, which makes a repeat
poll a no-op on the unique index exactly as a redelivered callback is.

WHAT A POLLED STAMP MEANS, and it is not what this header used to claim. It said the provider's
``created_at`` was passed through as the event time; it never was, and it must not be — that field
is when the EMAIL RECORD was created, not when ``last_event`` happened, so stamping a delivery with
it put ``delivered_at`` BEFORE ``sent_at`` and every polled message rendered "Delivered 11:10:55 →
Sent 11:10:56". Resend publishes no per-event timestamp anywhere else either: ``GET
/emails/{id}/events`` does not exist (405, checked 07 Sep 2026). So a polled stamp records when we
LEARNED, and the only honest way to narrow the gap is to ask more often — which is what the list
call below makes affordable.

Where the webhook IS registered its timestamp wins, because THIS LOOP WAITS FOR IT — see
:data:`WEBHOOK_GRACE_SECONDS`. That used to be true by luck rather than by design: at a minute's
cadence, scanning oldest-first, a message delivered seconds after it was sent had long since
settled before the poll reached it. Asking every twenty seconds and scanning newest-first made the
poll the likely first speaker for a brand-new message, and `apply_provider_event`'s
``COALESCE(stamp, :occ, now())`` makes the FIRST writer's stamp permanent — so the poll would have
frozen a learned-at time and the webhook's real event time, arriving a second later, would have
been discarded with `advance` refusing to move an already-`delivered` row. The grace period is what
makes the sentence true on purpose.

ONE REQUEST PER PASS, NOT ONE PER MESSAGE. This used to ask ``GET /emails/{id}`` for every pending
row, so a pass cost as many round trips as it had rows. ``GET /emails`` answers for a whole page at
once (``transport.fetch_recent_events``), and the per-id call survives only as the fallback for a
row too old to be on that page — see :func:`refresh_pending`.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.comms import ledger
from app.config import settings
from app.email import transport

logger = logging.getLogger(__name__)

#: Statuses worth asking about. A terminal one has nowhere left to go, and the rank would refuse
#: the answer anyway — so asking would spend a request to be told what we already hold.
PENDING_STATUSES: tuple[str, ...] = ("sent", "delayed")

#: How far back to look. A message Resend has not resolved in three days is not going to be, and
#: an unbounded scan would re-ask about the whole ledger every pass for ever.
HORIZON_HOURS = 72

#: Messages per pass. It bounds the DATABASE scan and the fallback fan-out, not the provider call
#: — one list request now covers the whole page whatever this is — so it can stay modest without
#: costing latency. Raising it only helps an account with more than this many rows pending at once.
BATCH = 25

#: HOW LONG A MESSAGE IS LEFT TO THE WEBHOOK BEFORE THIS LOOP WILL SPEAK FOR IT.
#:
#: The webhook carries the provider's REAL event time; a poll can only record when it learned. So
#: where both are available the webhook must write first — and `apply_provider_event` decides that
#: by ``COALESCE(stamp, :occ, now())``, first writer wins, which makes "first" a correctness
#: property rather than a race to shrug at. A minute is three passes at the default cadence — far
#: longer than a Resend callback takes and far shorter than the 72-hour horizon, so nothing is
#: stranded by waiting, and a webhook that is genuinely down costs a message one extra minute
#: before the net picks it up.
#:
#: ZERO WHEN NO WEBHOOK SECRET IS SET, because then there is nothing to wait for: the route answers
#: `not_configured` and refuses every callback (`routers/resend_webhook.py`), so the poll is not a
#: net under a live webhook but the only source of a delivery state at all. Reading the same
#: setting the route reads is deliberate — one fact, one place, and it is the same "absence of
#: configuration means do nothing" doctrine the secret already carries.
WEBHOOK_GRACE_SECONDS = 60


def _grace_seconds() -> int:
    """Seconds to leave a new message to the webhook. See :data:`WEBHOOK_GRACE_SECONDS`."""
    return WEBHOOK_GRACE_SECONDS if settings.resend_webhook_secret.strip() else 0


#: WHERE THE NEXT PASS RESUMES — the id BELOW the last one looked at, or ``None`` for "from the
#: newest again".
#:
#: DESCENDING, and that direction is load-bearing rather than arbitrary. The provider's list is
#: newest-first (`transport.fetch_recent_events`), so an ascending scan handed the pass the OLDEST
#: pending rows — precisely the ones that cannot be on that page. On the deployment the poll exists
#: for (webhook broken, a backlog inside the horizon) every scanned row missed the page, the whole
#: batch fell through to the per-id fallback, and a pass cost one MORE request than before the list
#: call was added. Scanning newest-first puts the rows most likely to be on the page in front of
#: the call that can answer for them for free.
#:
#: It still wraps, because the tail must not starve: a message the provider never resolves
#: (`last_event: "failed"`, an unknown state, a 404) stays pending for the full horizon, so without
#: a cursor the same rows are re-read every pass, cost a request each to learn nothing, and nothing
#: else is ever polled. Each pass takes the next 25 DOWNWARD and starts from the newest again when
#: it runs out, so coverage is complete however many rows are stuck — the old fairness, the other
#: way up.
#:
#: In memory, not a column: it is a fairness hint, not a fact about a message. Losing it on restart
#: costs one re-read of the newest rows, which are the cheapest ones to re-read.
_cursor: int | None = None


def reset_cursor() -> None:
    """Start the sweep again from the beginning. For tests, which must not inherit a position."""
    global _cursor
    _cursor = None


async def refresh_pending(maker: async_sessionmaker[AsyncSession], *, limit: int = BATCH) -> int:
    """Ask the provider about unresolved messages and advance the ones it has news about.

    Returns how many rows the answer actually moved — not how many were asked about, because a poll
    that changes nothing is the normal case and counting it would make the log read as work.
    """
    global _cursor
    if not transport.configured():
        return 0

    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, provider_message_id, status FROM comms.messages "
                        "WHERE provider_message_id IS NOT NULL "
                        "  AND status = ANY(:pending) "
                        "  AND created_at > now() - make_interval(hours => :hours) "
                        # LEFT TO THE WEBHOOK FIRST — see `WEBHOOK_GRACE_SECONDS`. Zero when no
                        # secret is configured, and then this term is `created_at < now()`, true of
                        # every row.
                        "  AND created_at < now() - make_interval(secs => :grace) "
                        # DESC to match the provider page's order — see `_cursor`.
                        "  AND (CAST(:cursor AS bigint) IS NULL OR id < :cursor) "
                        "ORDER BY id DESC LIMIT :limit"
                    ),
                    {
                        "pending": list(PENDING_STATUSES),
                        "hours": HORIZON_HOURS,
                        "grace": _grace_seconds(),
                        "limit": limit,
                        "cursor": _cursor,
                    },
                )
            )
            .mappings()
            .all()
        )
    # A short pass means the tail is reached: wrap, so the next one starts from the newest again.
    _cursor = int(rows[-1]["id"]) if len(rows) == limit else None
    if not rows:
        return 0

    # ONE row per provider id, keeping the HIGHEST — because that is the row
    # `ledger.apply_provider_event` will write to (it resolves `ORDER BY id DESC LIMIT 1`, since
    # 0111 deliberately left the provider-id index non-unique). Asking about one row and writing to
    # another would leave the asked-about row unchanged and re-polled for ever, while `moved`
    # counted nothing.
    #
    # AN EXPLICIT MAX, not "whichever the loop happens to see last". Assignment keeps the last row
    # scanned and `setdefault` the first, so BOTH are statements about the `ORDER BY` rather than
    # about the rule — and the rule is "the highest id", because that is the row the ledger writes
    # to. Assignment was right while the scan ran ascending and silently became wrong when it was
    # turned around (a duplicate pair split across `sent`/`delayed` then compared the answer
    # against the wrong row's status, writing an event and counting `moved` for a row already
    # there); `setdefault` is right now and would silently become wrong if the direction were ever
    # turned back. Comparing the ids says what is meant under any ordering.
    targets: dict[str, tuple[int, str]] = {}
    for row in rows:
        provider_id, row_id = str(row["provider_message_id"]), int(row["id"])
        held = targets.get(provider_id)
        if held is None or row_id > held[0]:
            targets[provider_id] = (row_id, str(row["status"]))

    # LIST FIRST. One request answers for Resend's most recent page, which on any normal day
    # contains every message this pass cares about — the rows are pending precisely because they
    # are recent.
    listed = await transport.fetch_recent_events()
    answers: dict[str, transport.RetrievedEmailEvent] = {}
    missing: list[str] = []
    if listed is None:
        # The list call failed or there is no credential. Fall back to asking per id, which is what
        # this did before the list call existed — a provider blip should cost accuracy, not the
        # whole pass.
        missing = list(targets)
    else:
        page, _has_more = listed
        for provider_id in targets:
            answer = page.get(provider_id)
            if answer is not None:
                answers[provider_id] = answer
            else:
                # ABSENT MEANS "not on this page", NEVER "no news": the list is newest-first,
                # unfiltered and account-wide, so a row older than the page window is simply not in
                # it. Treating absence as resolved-nothing would strand exactly the oldest rows,
                # which are the ones most likely to need the answer.
                missing.append(provider_id)

    if missing:
        # The tail, asked the old way. Bounded by `BATCH`, so the worst pass is one list call plus
        # at most that many — never more than the old behaviour, and on a quiet account exactly one.
        answers.update(await transport.fetch_last_events(missing))

    moved = 0
    for provider_id, (_row_id, current) in targets.items():
        answer = answers.get(provider_id)
        if answer is None:
            continue
        last_event = answer.event
        event_type = f"email.{last_event}"
        # The read-receipt rule is the webhook's, and it is this path's too — checked HERE as well
        # as at the route, because a second door into the ledger is exactly how a rule ends up
        # enforced in one place only.
        if event_type in ledger.NOT_TRACKED:
            continue
        status = ledger.EVENT_STATUS.get(event_type)
        # Decided BEFORE writing, with the same pure rule the webhook path uses: an answer that
        # cannot move this row (a redelivered `sent`, an unmapped state, a provider `failed` we
        # already hold a terminal status for) is not worth a transaction or an event row. The
        # webhook still records those, because there the callback itself is the evidence; a poll
        # that learned nothing has nothing to evidence.
        if status is None or ledger.advance(current, status) is None:
            continue
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            wrote = await ledger.apply_provider_event(
                session,
                provider_message_id=provider_id,
                event_type=event_type,
                # Synthetic and STABLE, so re-polling the same answer is a no-op on the unique
                # index rather than a second event row every pass.
                provider_event_id=f"poll:{last_event}",
                # NO provider timestamp: the retrieval shape has none for the event (only the
                # email's own `created_at`), so the ledger's `now()` arm records when we learned.
                occurred_at=None,
                payload={"source": "poll", "last_event": last_event},
            )
        # THE LEDGER'S ANSWER, not our own prediction of it. `advance` above is checked against the
        # highest row IN THIS BATCH, while `apply_provider_event` re-resolves the provider id over
        # the whole table — including rows outside `PENDING_STATUSES` and outside the horizon. A
        # duplicated id whose newest row is already `bounced` therefore looks like progress here and
        # is refused there, and counting it logged "1 message(s) advanced" for a pass that advanced
        # nothing. The count is what a reader uses to tell a working net from a stuck one.
        if wrote:
            moved += 1

    if moved:
        logger.info(
            "comms poll: %d message(s) advanced from the provider's own record",
            moved,
            extra={"event": "comms_poll_advanced", "moved": moved, "asked": len(targets)},
        )
    return moved
