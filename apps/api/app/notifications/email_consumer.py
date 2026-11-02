"""The notification-email subscriber — a peer of the in-app notifications consumer.

Structurally a mirror of ``app/notifications/consumer.py``: the same bus (``audit.actions``), the
same registry rules, the same 3-layer preference resolution (registry → tenant → user), its own
cursor. The difference is the sink — instead of an in-app row it renders a branded notification
email and enqueues it to ``flows.outbox``, so the dispatcher sends it and the non-production send
guard + retry/dead-letter all apply unchanged.

Scope (docs/notifications.md): NOTIFICATION email only. Transactional email (invite / password
reset / verify) stays inline — it must be synchronous and reliable, not a best-effort tail. Nothing
sends until a category's ``email`` preference is turned on (default off), so this is inert out of
the box even though the loop runs.

The transactional guarantee is the consumer's, not a dedup ledger's: reading the batch, enqueuing
its emails and advancing the cursor all commit in ONE transaction, so a crash replays cleanly and a
committed batch never re-enqueues.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import environments
from app.config import settings
from app.email import catalog as email_catalog
from app.email import render as email_render
from app.email import transport as email_mod
from app.flows import notifications as flows_notifications
from app.notifications import consumer as inapp_consumer
from app.notifications import context as context_mod
from app.notifications import registry, render, resolve, service
from app.notifications.catalogue import _subject_of

logger = logging.getLogger("notifications.email")

CONSUMER = "email"
BATCH_LIMIT = 25
#: KNOWN HOLE, ACCEPTED FOR NOW (Aug 2026) — the same one documented at length on
#: ``app/notifications/consumer.py::LAG_SECONDS``, and worth stating separately because this tail
#: holds its OWN cursor (``CONSUMER = "email"``) and is therefore skipped independently.
#:
#: In short: an id is taken at INSERT and the row appears at COMMIT, so a write that stalls longer
#: than this window lets a higher id commit first; the cursor advances past the earlier row and
#: ``id > position`` never returns it. That notification EMAIL is then never enqueued — silently.
#:
#: WHY THIS ONE HAS NO REPAIR PASS, unlike the in-app tail. ``consumer.resweep_once`` can blindly
#: re-read the recent past because an in-app insert is idempotent on ``source_ref``. This subscriber
#: has no such key — see the module docstring: "the transactional guarantee is the consumer's, not a
#: dedup ledger's". A blind re-sweep here would re-enqueue and SEND DUPLICATE EMAILS to clients,
#: which is worse than the miss it repairs. Do not copy the resweep across without first giving the
#: outbox row a deterministic dedupe key (e.g. ``notify:{action_id}:{trigger}:{recipient}``) plus
#: the unique index to enforce it.
#:
#: So the fix for this one is PREVENTION, not repair: the snapshot-watermark described on
#: ``consumer.LAG_SECONDS`` (advance only once the transactions in flight when the batch was read
#: have settled) needs no idempotency and would close this tail, the in-app tail and automations
#: alike.
#:
#: CONSEQUENCE WHILE IT STANDS, so nobody is surprised by it: the in-app tail HAS a repair pass and
#: this one does not, so a skipped row can leave a user with the bell notification and no email —
#: the two channels diverging for the same event. Asymmetric on purpose, not by oversight.
LAG_SECONDS = 5


async def _ensure_cursor(session: AsyncSession) -> None:
    """This subscriber's cursor, seeded at the CURRENT head on first run (never replay history)."""
    await session.execute(
        text(
            "INSERT INTO notifications.cursor (consumer, position) "
            "VALUES (:c, COALESCE((SELECT max(id) FROM audit.actions), 0)) "
            "ON CONFLICT (consumer) DO NOTHING"
        ),
        {"c": CONSUMER},
    )


async def _recipient_email(session: AsyncSession, r: registry.Recipient) -> str | None:
    """The email address for a recipient — a staff user's login, or a client contact's own address
    (falling back to their portal login). Mirrors the task notifier's resolution."""
    if r.kind == "user" and r.user_id:
        return (
            await session.execute(
                text("SELECT email FROM auth.users WHERE id = CAST(:u AS uuid) AND enabled"),
                {"u": r.user_id},
            )
        ).scalar_one_or_none()
    if r.kind == "contact" and r.contact_id is not None:
        direct = (
            await session.execute(
                text("SELECT email FROM crm.contacts WHERE id = :c"), {"c": r.contact_id}
            )
        ).scalar_one_or_none()
        if direct:
            return str(direct)
        return (
            await session.execute(
                text(
                    "SELECT u.email FROM crm.contact_users cu "
                    "JOIN auth.users u ON u.id = cu.user_id "
                    "WHERE cu.contact_id = :c AND u.enabled LIMIT 1"
                ),
                {"c": r.contact_id},
            )
        ).scalar_one_or_none()
    return None


def _first_name(full_name: str | None) -> str:
    """A greeting name from a resolved full name (the flat email templates address ``first_name``).
    Best-effort: the first whitespace token, or the whole string. Blank when unresolved."""
    return (full_name or "").strip().split(" ")[0]


def _surname(full_name: str | None) -> str:
    """The LAST whitespace token of a resolved name, for a formal line ("Dear Ms Whitfield").
    Best-effort like ``_first_name`` and blank rather than wrong: a one-word name has no surname to
    give, and repeating the forename there would put "Dear Sarah" under a formal salutation."""
    parts = (full_name or "").strip().split()
    return parts[-1] if len(parts) > 1 else ""


async def _document_extras(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> dict[str, Any]:
    """The document notifications' content that ISN'T on the action itself — resolved so the
    notification email is as rich as the (now-retired) inline documents email:

    - ``document.shared`` → the adviser's covering NOTE, from the ``document.sent`` domain event.
    - ``document.request`` → the request's item list + note + due date, from the request the action
      just created (found by the subject contact, newest-first — a request nobody chases twice).

    Empty for every other event. Resolved ONCE per action (same for all recipients)."""
    et = str(action.get("event_type"))
    if et == "document.sent":
        doc_uid = context_mod._document_uid(action)
        if not doc_uid:
            return {}
        note = (
            await session.execute(
                text(
                    "SELECT al.detail->>'message' FROM documents.audit_log al "
                    "JOIN documents.documents d ON d.id = al.document_id "
                    "WHERE d.uid = :u AND al.tenant_id = :t AND al.action = 'document.sent' "
                    "ORDER BY al.created_at DESC LIMIT 1"
                ),
                {"u": doc_uid, "t": tenant_id},
            )
        ).scalar_one_or_none()
        return {"message": note or ""}
    if et == "document_request.created":
        cid = context_mod._fallback_contact_id(action)
        if cid is None:
            return {}
        return await _request_extras(session, tenant_id, "contact_id", cid)
    # A GROUP request fans out to every member below the tail's view; the members all got the SAME
    # ask, so any of their (identically-itemised) request rows carries the content — found by the
    # group_id on the path. This is what makes the group email as rich as the single one.
    if et == registry.GROUP_REQUEST_EVENT:
        gid = _group_id_of(action)
        if gid is None:
            return {}
        return await _request_extras(session, tenant_id, "group_id", gid)
    return {}


def _group_id_of(action: dict[str, Any]) -> int | None:
    """The ``group_id`` on a group-request event's route path, as an int (else None)."""
    path_params = action.get("path_params")
    if not isinstance(path_params, dict):
        return None
    try:
        return int(path_params.get("group_id"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def _request_extras(
    session: AsyncSession, tenant_id: int, column: str, value: int
) -> dict[str, Any]:
    """The newest matching request's item list + note + due date. ``column`` is a trusted LITERAL
    (``contact_id`` for a single request, ``group_id`` for a fan-out), never caller input."""
    req = (
        (
            await session.execute(
                text(
                    "SELECT id, message, to_char(due_date, 'FMDD Mon YYYY') AS due "  # noqa: S608
                    f"FROM documents.requests WHERE tenant_id = :t AND {column} = :v "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"t": tenant_id, "v": value},
            )
        )
        .mappings()
        .one_or_none()
    )
    if req is None:
        return {}
    titles = list(
        (
            await session.execute(
                text("SELECT title FROM documents.request_items WHERE request_id = :r ORDER BY id"),
                {"r": req["id"]},
            )
        ).scalars()
    )
    return {
        "message": req["message"] or "",
        "due_date": req["due"] or "",
        "item_titles": titles,
    }


def _template_context(
    *,
    brand_tokens: dict[str, Any],
    title: str,
    body: str | None,
    cta: tuple[str, str] | None,
    portal_base: str | None,
    rctx: dict[str, dict[str, str]],
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The merge context for the rule's chosen email template.

    The general ``notification`` wrapper reads ``title``/``body``/``button_*`` only; a custom
    template (e.g. ``document_sent``) reads FLAT keys (``first_name``/``document_name``/``sent_by``)
    derived here from the notification's nested merge context. ``extras`` carries the document
    content that isn't on the event — the covering note, a request's item list + due date
    (:func:`_document_extras`) — so the retired inline documents email loses nothing here."""
    client = rctx.get("client", {})
    actor = rctx.get("actor", {})
    account = rctx.get("account", {})
    document = rctx.get("document", {})
    task = rctx.get("task", {})
    button_url = cta[1] if cta else ""
    ctx: dict[str, Any] = {
        "brand": brand_tokens,
        "title": title,
        "body": body or "",
        "button_label": cta[0] if cta else "",
        "button_url": button_url,
        # Flat aliases for the custom notification templates (blank where unresolved).
        "first_name": _first_name(client.get("name")),
        "full_name": (client.get("name") or "").strip(),
        "surname": _surname(client.get("name")),
        "document_name": document.get("name", ""),
        "sent_by": actor.get("name", ""),
        "shared_by": actor.get("name", ""),
        "task_title": task.get("title", ""),
        # The other two lines a task template writes. Both are guarded clauses in the shipped
        # defaults, so without these the email rendered correctly and silently two lines shorter.
        "task_description": task.get("description", ""),
        "account_name": account.get("name", ""),
        "account_number": account.get("number", ""),
        # The template's own "Open your portal" button: the notice's deep-link when it has one, else
        # a FALLBACK to the recipient's own portal home (platform for a staff user, client for a
        # contact). The fallback is what makes the button always resolve: a link that cannot be
        # absolutised — a staff-audience rule, or a tenant with no registered platform host — leaves
        # the CTA empty, and without a base behind it `{% if portal_url %}` hid the button entirely.
        # The deep-link still wins, so a client's document notice points at that document.
        "portal_url": button_url or (portal_base.rstrip("/") if portal_base else ""),
        # Document content resolved off the event (covering note, request items + due date).
        "message": (extras or {}).get("message", ""),
        "item_titles": (extras or {}).get("item_titles", []),
        # A request's own due date wins; a task notice falls back to the task's.
        "due_date": (extras or {}).get("due_date") or task.get("due_date", ""),
    }
    return ctx


def _dev_scheme(url: str | None) -> str | None:
    """`portal_base_url` hard-codes https, but a localhost host is served over http in dev, so a
    https://localhost link won't load. Downgrade localhost only; real domains keep https."""
    if url and ("localhost" in url or "127.0.0.1" in url):
        return url.replace("https://", "http://", 1)
    return url


async def _portal_base(session: AsyncSession, tenant_id: int, portal: str) -> str | None:
    """The tenant's registered base URL for `portal` (from its WorkOS/environment hosts), or None.
    Tolerant: a tenant with no registered environment simply yields no base (⇒ no CTA), never an
    error that would stall the email loop."""
    try:
        return _dev_scheme(await environments.portal_base_url(session, tenant_id, portal))
    except LookupError:
        return None


def _cta(
    link: str | None, r: registry.Recipient, *, client_base: str | None, platform_base: str | None
) -> tuple[str, str] | None:
    """A CTA button from the notification's (already RENDERED) link. An absolute URL is used as-is;
    a relative in-portal path is absolutised against the recipient's OWN portal base — the client
    portal for a contact, the PLATFORM portal for a staff user (both resolved per tenant from the
    firm's WorkOS/environment hosts). No base ⇒ no CTA rather than a broken relative href."""
    if not link:
        return None
    # An ABSOLUTE link keeps the bare wording: it may point anywhere — another system, a vendor
    # page — so naming it "your portal" would be a claim about a destination we do not control.
    if link.startswith("http://") or link.startswith("https://"):
        return ("Open", link)
    # Both in-portal branches say the same thing. Staff said a bare "Open" until Aug 2026, which
    # named nothing on a button whose whole job is to say where it goes; the platform portal is a
    # staff member's portal exactly as the client portal is a client's.
    if r.kind == "contact" and client_base:
        return ("Open your portal", client_base.rstrip("/") + link)
    if r.kind == "user" and platform_base:
        return ("Open your portal", platform_base.rstrip("/") + link)
    return None


async def _email_for_event(session: AsyncSession, action: dict[str, Any]) -> None:
    """Enqueue notification emails for one audit.actions row, per every matching rule + recipient
    whose EMAIL channel resolves on."""
    rules = await resolve.effective_rules(session, action)
    if not rules:
        return
    # A per-policy channel preference (an approval hold) is authoritative: an action set to in-app
    # only sends no email, whatever the rule/firm/recipient channels would otherwise resolve to.
    channels = inapp_consumer._policy_channels(action)
    if channels is not None and not channels["email"]:
        return
    tenant_id = registry.book_tenant_id(action)
    # Merge context for {placeholders} — the same substitution the in-app consumer does, so an
    # authored "{client.name} …" template does not send raw tokens once email is on. Covers the
    # email's OWN copy and the link target too. Base resolved once per action; the CLIENT slice is
    # overridden per recipient (see context_for_recipient).
    # ALWAYS BUILT: the skip that used to sit here tested the rule's own copy for {tokens} and
    # excluded the general wrapper, so the shipped default — static copy, no email_code, hence
    # `notification` — skipped the build and every rule-supplied "{client.name}" rendered empty.
    # Silently, because a blank is not an error. One build per action, which the in-app consumer
    # already pays.
    needs_context = True
    base_context = (
        await context_mod.build_context(session, action, tenant_id) if needs_context else {}
    )
    # The subject the event is ABOUT — so a staff notice with no explicit link still gets a
    # deep-link to the record (the same fallback the in-app consumer uses). Resolved once.
    subj_kind, subj_raw = await _subject_of(session, action, tenant_id)
    subject_type = subj_kind if subj_kind in ("contact", "account") else None
    subject_serial = subj_raw if subject_type and subj_raw is not None else None
    click_uid = await inapp_consumer._subject_uid(session, subject_type, subject_serial, tenant_id)
    # Portal bases, resolved once per tenant from the firm's registered WorkOS hosts. Client keeps
    # the global setting first (its scheme is correct for dev); platform comes from the env host.
    client_base = settings.client_portal_base_url or await _portal_base(
        session, tenant_id, "client"
    )
    platform_base = await _portal_base(session, tenant_id, "platform")
    # The document content that isn't on the action (covering note, request items) — resolved once.
    doc_extras = await _document_extras(session, action, tenant_id)
    for rule in rules:
        recipients = await rule.resolve(session, action, tenant_id)
        if not recipients:
            continue
        config_audience = "client" if rule.audience_key == "client" else "adviser"
        tconf = await service._tenant_config(session, tenant_id, rule.category, config_audience)
        if tconf is not None and not tconf["enabled"]:
            continue  # firm-wide off-switch
        forced = rule.mandatory or bool(tconf and tconf.get("mandatory"))
        # Personal preferences are per notification (the trigger) for clients and staff alike.
        pref_trigger = rule.trigger_key
        portal: Any = "client" if rule.portal == "client" else "platform"
        seen: set[str] = set()
        for r in recipients:
            if r.key() in seen:
                continue
            seen.add(r.key())
            channels = await service._effective_channels(
                session,
                tenant_id,
                r,
                rule.category,
                tconf,
                base_in_app=rule.default_in_app,
                base_email=rule.default_email,
                mandatory=forced,
                pref_trigger=pref_trigger,
            )
            if not channels["email"]:
                continue
            to = await _recipient_email(session, r)
            if not to:
                continue
            rctx = (
                await context_mod.context_for_recipient(session, base_context, r, tenant_id)
                if needs_context
                else base_context
            )
            sender = await email_mod.resolve_sender(session, tenant_id, portal=portal)
            brand = await email_mod.brand_for(session, tenant_id, portal=portal)
            # The notification's OWN title/body/link, rendered for THIS recipient (a contact sees
            # its own {client.name}), fill the ONE shared 'notification' wrapper — the firm's
            # published design for it, or the shipped default. render_or_default is fail-safe, so a
            # firm that never touched the wrapper still gets a branded email.
            title = render.render(rule.title, rctx) if needs_context else rule.title
            body = (
                (render.render(rule.body, rctx) if rule.body else rule.body)
                if needs_context
                else rule.body
            )
            link = render.render(rule.link, rctx) if rule.link and "{" in rule.link else rule.link
            # A staff notice with no explicit link deep-links to the subject record — the SAME
            # fallback the in-app notice uses, so the email is never a dead end.
            link = inapp_consumer._click_link(link, rule.portal, subject_type, click_uid)
            cta = _cta(link, r, client_base=client_base, platform_base=platform_base)
            # The template this rule renders — its chosen `email_code`, else the general wrapper.
            code = rule.email_code or email_catalog.WRAPPER_CODE
            subject, html = await email_render.render_or_default(
                session,
                tenant_id=tenant_id,
                code=code,
                context=_template_context(
                    brand_tokens=email_mod.brand_tokens(brand),
                    title=title,
                    body=body,
                    cta=cta,
                    # The recipient's own portal home — the same client/platform choice _cta makes —
                    # so the template's "Open your portal" button renders regardless of the CTA.
                    portal_base=platform_base if r.kind == "user" else client_base,
                    rctx=rctx if needs_context else {},
                    extras=doc_extras,
                ),
            )
            await flows_notifications.enqueue(
                session,
                tenant_id=tenant_id,
                event_type=f"notify.{rule.category}",
                payload={
                    "to": to,
                    "subject": subject,
                    "html": html,
                    "event_type": f"notify.{rule.category}",
                    "sender": sender.from_addr,
                    "reply_to": sender.reply_to,
                    # WHY this email exists, carried to the dispatcher for the outbound ledger.
                    # It rides in the payload for the same reason `sender`/`reply_to` do: `_send`
                    # runs in the worker, which has no request session and cannot re-derive any of
                    # it. A row queued before this key existed simply has no `comms` block, and
                    # `_send` falls back rather than raising.
                    "comms": {
                        "template_code": code,
                        "event_type": action["event_type"],
                        "rule_uid": rule.uid,
                        "category": rule.category,
                        "recipient_contact_id": r.contact_id,
                        "recipient_user_id": r.user_id,
                    },
                },
            )


async def consume_once(
    maker: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> bool:
    """One pass of the email tail — the in-app consumer's twin; one transaction per batch."""
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
            return False
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
        if settings.email_notifications_enabled:
            for event in events:
                # ONE SAVEPOINT PER EVENT, and the failure is SWALLOWED. Without it the batch and
                # the cursor share a transaction with every send: one template that cannot render
                # (Jinja raises — see `email_templates_service._render_failure`) rolled the cursor
                # back, the next pass re-read the same event, and NO notification email sent for ANY
                # tenant until a human found it. The cursor is a single global row, so one firm's
                # typo stopped everyone's mail.
                #
                # A savepoint rather than a bare try: a failed statement leaves the transaction
                # aborted, so catching without rolling back would poison every remaining event in
                # the batch. Logged at ERROR with the event id and tenant — a swallowed send is
                # invisible otherwise — and it costs one email, not all of them.
                try:
                    async with session.begin_nested():
                        await _email_for_event(session, dict(event))
                except Exception:
                    logger.exception(
                        "notification email failed for action %s (tenant %s)",
                        event["id"],
                        event["tenant_id"],
                    )
        await session.execute(
            text(
                "UPDATE notifications.cursor SET position = :p, updated_at = now() "
                "WHERE consumer = :c"
            ),
            {"p": int(events[-1]["id"]), "c": CONSUMER},
        )
    return len(events) == BATCH_LIMIT
