"""The notifications tail — a second cursor consumer of ``audit.actions``.

Structurally the twin of ``app/flows/automation.py::consume_once``: claim this consumer's cursor
``FOR UPDATE SKIP LOCKED`` (single-consumer for the estate), read the next batch of action rows
past it (with a small ``LAG_SECONDS`` cutoff for commit-order skew), turn each into notifications
per ``registry.RULES``, and advance the cursor — ALL in one transaction, so a crash replays the
batch and the ``source_ref`` unique key makes the replay a no-op.

The cursor is seeded lazily at the CURRENT head on first run (``_ensure_cursor``), never at 0 — a
fresh deployment notifies on what happens next, not the whole history of the stream. Gated by
``settings.notifications_enabled`` (the estate-wide switch); the cursor keeps advancing while it is
off, matching the automations kill switch, so releasing it never unleashes a backlog.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.logging import report_error
from app.notifications import catalogue, realtime, registry, render, resolve, service
from app.notifications import context as context_mod

logger = logging.getLogger("notifications")

CONSUMER = "notifications"
BATCH_LIMIT = 25
#: Only read rows older than this, so the middleware's post-commit write of a row has settled before
#: we tail past it (audit.actions is written best-effort AFTER the response). Matches automation's.
#:
#: KNOWN HOLE, ACCEPTED FOR NOW (Aug 2026). This is a BET on how long a commit takes, not a
#: guarantee. An id is taken at INSERT and the row appears at COMMIT, so if a write stalls longer
#: than this between the two (a saturated pool, a lock wait, a paused container), a higher id
#: commits first, the cursor advances past the lower one, and `id > position` never returns it
#: again. The notification is then never created — silently and permanently, with no error and
#: nothing watching for it. It is also the whole of the ~5-7s a user waits for one to appear.
#:
#: The fix is to ask whether a row has SETTLED rather than how old it is: take
#: ``pg_current_snapshot()`` and only advance the cursor past a batch once every transaction that
#: was in flight when it was read has FINISHED. Exact instead of probabilistic, and self-adjusting
#: (no wait when commits are quick, exactly as long as needed when one is slow).
#:
#: THE TRAP, for whoever implements it: "process only rows whose ``xmin`` is below the snapshot's
#: ``pg_snapshot_xmin``" looks like the one-line version and is NOT sufficient — it silently
#: reintroduces this exact bug. Transaction-id order and sequence order can disagree, because a
#: transaction that STARTS later can INSERT earlier: C(xid 500) opens, A(xid 505) opens and takes id
#: 100 and stays open, C takes id 101 and commits. xmin is 505, so row 101 (xmin 500) passes the
#: test, the cursor advances to 101, and A's row 100 appears behind it. The condition has to be "the
#: snapshot I read under has fully retired", which means carrying state between passes.
#:
#: Given that, a cursor on ``(commit_ts, id)`` is probably the better trade: commit order IS
#: visibility order, so it cannot skip BY CONSTRUCTION, and the code is far simpler — which matters
#: when the cost of a subtle bug is silent loss. It needs ``track_commit_timestamp`` at server level
#: (a restart) plus a startup assertion, so a deployment without it fails loudly instead of quietly.
#:
#: Separately, the LATENCY half has its own answer, and the two are not the same fix: an AFTER
#: INSERT trigger on ``audit.actions`` emitting ``pg_notify(id)`` (transactional, so a rolled-back
#: write announces nothing) lets this consumer process a row on arrival instead of waiting the
#: window out. That makes delivery sub-second but fixes NOTHING about the hole above — a nudge can
#: be missed too, which is why the lagged cursor stays the backstop. Safe to run both: the insert
#: is idempotent on ``source_ref``, so a re-read writes nothing.
#:
#: WHAT IS ACTUALLY DONE ABOUT IT HERE: ``resweep_once`` below re-processes the recent past on a
#: slow cadence, so a row this window skipped is delivered late rather than never. That repairs the
#: hole without touching this read path, and covers every other cause of loss too. The window stays
#: as it is — with the repair pass in place its only remaining cost is latency, not correctness.
#:
#: ``app/flows/automation.py`` has the identical hole and NO repair pass (a firing is not idempotent
#: the way a notification insert is, so the same trick does not transfer for free). Left open
#: deliberately; noted there.
LAG_SECONDS = 5

#: How far back ``resweep_once`` re-reads. Long enough to cover a stall far beyond ``LAG_SECONDS``,
#: short enough that a pass is a cheap indexed range read (``actions_created_at_idx``) and that a
#: rule created mid-window can only fire late for minutes, not hours.
RESWEEP_WINDOW_SECONDS = 600

#: The AML tipping-off deny (POCA 2002 s.333A) — this consumer is a SECOND, independent tail of
#: audit.actions, so it needs the same carve-out the audit READ path enforces
#: (routers/firm.py::FIRM_EXCLUDED_EVENT_PREFIXES). Today the catalogue offers no AML trigger so no
#: aml.* event resolves a rule anyway, but a well-meaning future firm-transparency rule must
#: NEVER be able to deliver a screening notice to a firm's bell/email; this makes the guarantee
#: structural rather than allowlist-by-omission. Pinned by tests/test_notifications.py.
#: BOTH spellings: `aml.` is the vocabulary now, `action.aml-` is what every row written
#: before the rename still carries. A control that covers only the current spelling stops
#: covering history the moment the names change — and history is exactly what a stream is.
AML_EVENT_PREFIXES: tuple[str, ...] = ("aml.", "action.aml-")


async def _ensure_cursor(session: AsyncSession) -> None:
    """Create this consumer's cursor at the CURRENT head on first run (never replay history).

    Lazily rather than in the migration: ``notifications.cursor`` is service_role-only with FORCE
    RLS, so only this role can insert; and a consumer's first run must define its own start."""
    await session.execute(
        text(
            "INSERT INTO notifications.cursor (consumer, position) "
            "VALUES (:c, COALESCE((SELECT max(id) FROM audit.actions), 0)) "
            "ON CONFLICT (consumer) DO NOTHING"
        ),
        {"c": CONSUMER},
    )


def recipient_portal(rule_portal: str, recipients: Sequence[registry.Recipient]) -> str:
    """The portal a notification is stamped for: the RECIPIENT'S, not the audience's.

    `catalogue.audience_portal` reads the audience KEY, so anything not keyed 'client' becomes
    'platform'. That is right for every audience whose resolver can only find staff, and wrong for
    the one that can find either: `task.assigned`'s sole audience is keyed 'assignee', and
    `_resolve_task_assignee` returns a contact whenever the task was assigned to a client -- which
    `docs/tasks.md` describes as a first-class thing to do.

    The client then got a row stamped for a portal they cannot open, linking to `/tasks`, which does
    not exist in the client portal at all, plus an email about a staff task inbox.

    A contact IS a client -- `crm.contacts` is the client record, and a client holds only the client
    portal -- so the recipient's kind settles this and the audience key cannot be wrong about it.
    ALL recipients, not any: a mixed list would be a rule fanning one notice to staff and clients
    together, which no resolver does, and guessing one portal for both would be worse than keeping
    the author's.
    """
    if recipients and all(r.kind == "contact" for r in recipients):
        return "client"
    return rule_portal


#: Where a client-portal notice lands when the rule that produced it was authored for staff. The
#: client portal is composed (docs/portal-customisation.md), so there is no record view to deep-link
#: -- the root is where their action items are surfaced.
CLIENT_PORTAL_HOME = "/"


async def _subject_uid(
    session: AsyncSession, subject_type: str | None, subject_id: int | None, tenant_id: int
) -> str | None:
    """The public uid for a resolved subject serial, or None if it no longer resolves.

    ``_subject_of`` answers in internal ids because that is what the flows subject resolver deals
    in; a portal path needs the public one. Read through the caller's own session, so a subject
    outside the reader's scope simply yields no link rather than leaking that the row exists.

    The stored notification ``subject_id`` stays the serial — ``isOnScreen`` parses it as the
    contact's id. Only the click destination is uid-addressed.
    """
    if subject_type is None or subject_id is None:
        return None
    table = {"contact": "crm.contacts", "account": "platform.accounts"}.get(subject_type)
    if table is None:
        return None
    row = await session.execute(
        text(f"SELECT uid FROM {table} WHERE id = :i AND tenant_id = :t"),  # noqa: S608
        {"i": subject_id, "t": tenant_id},
    )
    uid = row.scalar_one_or_none()
    return str(uid) if uid else None


def _click_link(
    link: str | None, portal: str, subject_type: str | None, subject_id: str | None
) -> str | None:
    """Where clicking this notification goes. The rule's own link wins; otherwise a STAFF (platform)
    notice deep-links to the subject's record, so a click lands somewhere useful instead of only
    marking it read. Client-portal notices keep their explicit link (the built-ins ship one) — a
    client's portal is composed and has no ``/clients/{uid}`` record view to send them to.

    ``subject_id`` is the public UID (the caller resolves it): both targets are uid-addressed
    routes, and a serial here lands on a record that does not exist."""
    if link:
        return link
    if portal == "platform" and subject_id:
        if subject_type == "contact":
            return f"/clients/{subject_id}"
        if subject_type == "account":
            return f"/accounts/{subject_id}"
    return None


async def _deliver(
    session: AsyncSession,
    action: dict[str, Any],
    action_id: int,
    tenant_id: int,
    rule: resolve.EffectiveRule,
    recipients: list[registry.Recipient],
    *,
    title: str,
    body: str | None,
    link: str | None,
    subject_type: str | None,
    subject_id: str | None,
    delivered: list[registry.Recipient],
) -> None:
    """Insert notifications for ``rule`` to ``recipients`` with rendered title/body/link. The
    subject (what the event is about) rides along so the portal can deep-link the click.
    ``delivered`` collects the recipients that got a new row, for the realtime announce."""
    portal = recipient_portal(rule.portal, recipients)
    if portal != rule.portal:
        # The link came from a staff rule, so it is a staff route -- either the rule's own or the
        # `/clients/{uid}` deep link `_click_link` adds for a platform notice. Neither resolves in
        # the client portal. Their own portal root does, and it is where their action items live.
        link = CLIENT_PORTAL_HOME
    await service.notify(
        session,
        tenant_id=tenant_id,
        recipients=recipients,
        category=rule.category,
        portal=portal,
        title=title,
        body=body,
        link=link,
        priority=rule.priority,
        event_type=str(action["event_type"]),
        source_kind="action",
        source_ref=f"action:{action_id}:{rule.trigger_key}:{rule.audience_key}",
        source_action_id=action_id,
        subject_type=subject_type,
        subject_id=subject_id,
        default_in_app=rule.default_in_app,
        default_email=rule.default_email,
        mandatory=rule.mandatory,
        audience=rule.audience_key,
        trigger_key=rule.trigger_key,
        delivered=delivered,
    )


def _policy_channels(action: dict[str, Any]) -> dict[str, bool] | None:
    """The approval notice's channel preference, stamped on a ``review.requested`` event by the
    action's policy. ``None`` for any other event (no per-policy mask). A firm that set an action
    to in-app only does not send an email; one set to email only does not store an in-app row —
    this is an authoritative mask over the usual channel resolution, applied by both consumers."""
    meta = action.get("metadata")
    subject = meta.get("subject") if isinstance(meta, dict) else None
    if not isinstance(subject, dict) or "notify_in_app" not in subject:
        return None
    return {
        "in_app": bool(subject.get("notify_in_app", True)),
        "email": bool(subject.get("notify_email", False)),
    }


async def handle_action(
    session: AsyncSession, action: dict[str, Any], delivered: list[registry.Recipient]
) -> None:
    """Fan one audit.actions row out to in-app notifications per the effective rules for the event
    (catalogue built-ins overlaid with the tenant's stored rules). ``delivered`` accumulates the
    recipients that got a new row, so the caller can announce them on the realtime channel."""
    # AML tipping-off deny FIRST (before any rule resolution): a screening action must never fan out
    # to a firm-facing notification. Defense-in-depth mirroring the audit read path — see
    # AML_EVENT_PREFIXES. A client must not learn from a bell that they were screened.
    if str(action.get("event_type") or "").startswith(AML_EVENT_PREFIXES):
        return
    # A per-policy channel preference (an approval hold) is authoritative: an action set to
    # email-only stores no in-app notice, whatever the rule/firm/recipient channels would resolve.
    channels = _policy_channels(action)
    if channels is not None and not channels["in_app"]:
        return
    rules = await resolve.effective_rules(session, action)
    if not rules:
        return
    tenant_id = registry.book_tenant_id(action)
    action_id = int(action["id"])
    # What the event is ABOUT, so a click can deep-link to it. Only the kinds the table's CHECK
    # allows AND that map to a portal route are stored; others leave it null (the rule's own link,
    # if any, still applies). Resolved once — the subject is the same for every rule.
    subj_kind, subj_raw = await catalogue._subject_of(session, action, tenant_id)
    subject_type = subj_kind if subj_kind in ("contact", "account") else None
    subject_serial = subj_raw if subject_type and subj_raw is not None else None
    if subject_type is None:
        # `_subject_of` answers from the FLOWS trigger registry, and not every event that notifies
        # has one there — chat messages are the standing example: `message.sent` has no flows
        # trigger, so every message notification was stored with a NULL subject even though its own
        # catalogue Trigger declares `subject_kind="contact"` and the event carries the contact on
        # its allowlisted subject (`{"contact_id": 8}`).
        #
        # A null subject is not cosmetic. It is what the row says the notification is ABOUT, so
        # nothing downstream can tell one client's message notice from another's — and the reader
        # that needs it most is the portal's own "am I already looking at this?" suppression, which
        # silently matched nothing at all. Same precedent, and the same reader, as
        # `context._fallback_contact_id`: where the generic resolver names nothing, believe the id
        # the PRODUCER put on the event.
        #
        # The link is untouched: `_click_link` only invents one when the rule ships none, and every
        # trigger in this family ships its own.
        fallback_contact = context_mod._fallback_contact_id(action)
        if fallback_contact is not None:
            subject_type, subject_serial = "contact", fallback_contact
    # Stored as the serial: `isOnScreen` parses `subject_id` as the contact's id. The click
    # destination is a PORTAL PATH, and both routes `_click_link` targets are uid-addressed, so a
    # serial there lands on a record that does not exist. It resolves to None rather than falling
    # back to the serial: a notice with no link still marks read, where one pointing at a 404
    # reads as the record having been deleted.
    subject_id = str(subject_serial) if subject_serial is not None else None
    click_uid = await _subject_uid(session, subject_type, subject_serial, tenant_id)
    # Merge context for the {placeholders} in any rule's copy OR link — the base is resolved ONCE
    # per action (the subject/actor is the same for every rule), and only when a rule carries a
    # token. A link target like /documents/{document.uid} needs the same context as the copy.
    needs_context = any(
        "{" in r.title or (r.body and "{" in r.body) or (r.link and "{" in r.link) for r in rules
    )
    base_context = (
        await context_mod.build_context(session, action, tenant_id) if needs_context else {}
    )
    for rule in rules:
        try:
            recipients = await rule.resolve(session, action, tenant_id)
        except Exception as exc:  # noqa: BLE001 — one bad row must not stall the estate's tail
            report_error(
                f"notifications rule resolve failed for action {action_id} ({rule.trigger_key})",
                log=logger,
                event="notifications_resolve_failed",
                action_id=action_id,
                trigger=rule.trigger_key,
                error=f"{type(exc).__name__}: {exc}",
            )
            continue
        if not recipients:
            continue
        # The click destination: the rule's own link (which may be a token template like
        # /documents/{document.uid}) wins; else a staff notice deep-links to the subject record.
        raw_link = _click_link(rule.link, rule.portal, subject_type, click_uid)
        if not needs_context:
            await _deliver(
                session,
                action,
                action_id,
                tenant_id,
                rule,
                recipients,
                title=rule.title,
                body=rule.body,
                link=raw_link,
                subject_type=subject_type,
                subject_id=subject_id,
                delivered=delivered,
            )
            continue
        # Templated copy renders PER RECIPIENT: a contact recipient sees its own {client.name}, so a
        # document fanned out to several clients never prints the first client's name to the rest.
        # The link renders against the same context (its entity ids don't vary per recipient, but
        # one render path keeps title/body/link consistent).
        for r in recipients:
            rctx = await context_mod.context_for_recipient(session, base_context, r, tenant_id)
            await _deliver(
                session,
                action,
                action_id,
                tenant_id,
                rule,
                [r],
                title=render.render(rule.title, rctx),
                body=render.render(rule.body, rctx) if rule.body else rule.body,
                link=render.render(raw_link, rctx) if raw_link and "{" in raw_link else raw_link,
                subject_type=subject_type,
                subject_id=subject_id,
                delivered=delivered,
            )


async def consume_once(
    maker: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> bool:
    """One pass of the action-stream tail. Returns True when the batch was full (more to do)."""
    at = now or datetime.now(UTC)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        await _ensure_cursor(session)
        position = (
            await session.execute(
                text(
                    "SELECT position FROM notifications.cursor WHERE consumer = :c "
                    "FOR UPDATE SKIP LOCKED"
                ),
                {"c": CONSUMER},
            )
        ).scalar_one_or_none()
        if position is None:
            return False  # another worker holds the cursor
        events = (
            (
                await session.execute(
                    text(
                        "SELECT id, event_type, tenant_id, target_tenant_id, actor_id, "
                        "       path_params, metadata, created_at "
                        "FROM audit.actions WHERE id > :pos AND created_at < :cutoff "
                        "ORDER BY id LIMIT :lim"
                    ),
                    {
                        "pos": int(position),
                        "cutoff": at - timedelta(seconds=LAG_SECONDS),
                        "lim": BATCH_LIMIT,
                    },
                )
            )
            .mappings()
            .all()
        )
        if not events:
            return False
        delivered: list[registry.Recipient] = []
        if settings.notifications_enabled:
            for event in events:
                await handle_action(session, dict(event), delivered)
        # Announce the new in-app rows on the realtime channel from THIS transaction, so the NOTIFY
        # fires on commit alongside the cursor advance: a rolled-back batch announces nothing and
        # stays unconsumed (retried next pass), never a phantom nudge for a row that didn't land.
        await realtime.announce(session, delivered)
        await session.execute(
            text(
                "UPDATE notifications.cursor SET position = :p, updated_at = now() "
                "WHERE consumer = :c"
            ),
            {"p": int(events[-1]["id"]), "c": CONSUMER},
        )
    return len(events) == BATCH_LIMIT


async def resweep_once(
    maker: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    window_seconds: int = RESWEEP_WINDOW_SECONDS,
) -> int:
    """Re-process the recent past REGARDLESS of the cursor, and return how many rows were re-read.

    This is the repair pass that closes the ``LAG_SECONDS`` hole documented above, and it does it
    without needing to DETECT anything — which matters, because a skip is not detectable after the
    fact. "Produced no notification" is not evidence of a miss (most actions match no rule at all),
    and gaps in the id sequence are not evidence either (a rolled-back transaction burns its
    sequence value permanently, so gaps are normal). Anything that tried to spot a skip would be
    guessing.

    So it does not try. It blindly re-reads every action in the last ``window_seconds`` and runs the
    same ``handle_action`` over each. Everything already delivered writes NOTHING, because
    ``service.notify`` inserts under the ``notifications_source_ref_key`` unique index with a
    deterministic ``source_ref`` per (action, trigger, audience) — so the normal case is a read and
    no writes, and a genuinely missed row is created on its next sweep.

    Being blind is also what makes it broad: it recovers a row skipped by out-of-order commits, but
    equally one lost to a crash mid-batch, a rolled-back consumer transaction, or a bug in the tail.
    The snapshot-watermark alternative (see ``LAG_SECONDS``) only prevents the first of those, and
    changes the hot read path to do it.

    Deliberately NOT advancing the cursor: this pass is not the tail and must never move it, or a
    sweep would skip the very rows the tail has yet to reach. It takes no cursor lock either, so it
    can run beside ``consume_once`` — the unique index, not a lock, is what keeps them from
    duplicating each other's work.

    One accepted consequence, stated rather than discovered: re-processing evaluates the rules as
    they stand NOW, so a rule created within the window can fire for an action that predates it by
    up to ``window_seconds``. That is bounded and, for a rule a firm has just switched on, arguably
    the kinder reading.
    """
    at = now or datetime.now(UTC)
    if not settings.notifications_enabled:
        return 0
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        events = (
            (
                await session.execute(
                    text(
                        "SELECT id, event_type, tenant_id, target_tenant_id, actor_id, "
                        "       path_params, metadata, created_at "
                        "FROM audit.actions "
                        "WHERE created_at >= :floor AND created_at < :cutoff "
                        "ORDER BY id"
                    ),
                    {
                        "floor": at - timedelta(seconds=window_seconds),
                        # The same cutoff the tail uses: a row younger than the lag is the TAIL's
                        # to deliver first. Sweeping it early would notify ahead of the cursor and
                        # make this pass the primary path, which it is not.
                        "cutoff": at - timedelta(seconds=LAG_SECONDS),
                    },
                )
            )
            .mappings()
            .all()
        )
        if not events:
            return 0
        delivered: list[registry.Recipient] = []
        for event in events:
            # A SAVEPOINT per row, so one unprocessable action cannot take the sweep down with it.
            # This matters more here than in the tail: the tail's batch is 25 rows and retried,
            # a sweep spans the whole window — so without isolation one poison row would abort
            # every pass for as long as it stayed in range, and the repair would be dead
            # exactly when something needed repairing. Postgres poisons the whole transaction after
            # a failed statement, so catching without a savepoint would not be enough.
            try:
                async with session.begin_nested():
                    await handle_action(session, dict(event), delivered)
            except Exception as exc:  # noqa: BLE001 — one bad row must not stop the repair
                report_error(
                    "notifications resweep could not process an action; skipping it",
                    exc=exc,
                    log=logger,
                    event="notifications_resweep_action_failed",
                    action_id=int(event["id"]),
                )
        # Announce only what this pass actually created — normally nothing at all, which is the
        # healthy case. A recovered row gets the same realtime nudge a timely one would have.
        await realtime.announce(session, delivered)
        if delivered:
            logger.warning(
                "notifications resweep recovered %d missed notification(s) over %ds",
                len(delivered),
                window_seconds,
                extra={"event": "notifications_resweep_recovered", "count": len(delivered)},
            )
    return len(events)
