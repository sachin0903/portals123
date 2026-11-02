"""The notification sink — one primitive every producer calls.

``notify`` fans a single logical notification out to its recipients: for each, it reads the
effective per-category preference (the stored override else the registry default) and, when the
in-app channel is on, inserts one row — collapsing at-least-once redelivery / consumer replay on
the ``notifications_source_ref_key`` partial unique index (``ON CONFLICT ... DO NOTHING``).

**Writes require a service_role session** (``notifications.notifications`` grants no INSERT to
``authenticated`` — a signed-in user may only mark their own rows read). The tail consumer and the
worker-side domain producers all hold one; a request-path producer must route through the tail, not
call this directly.

The EMAIL channel is a declared-but-inert seam in v1 (marked below): the preference is computed and
respected, but nothing is enqueued yet. The fast-follow wires ``flows.notifications.enqueue`` of a
``notify.*`` outbox row here.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ids import new_id
from app.notifications.registry import Recipient

PRIORITIES = ("low", "normal", "high")


async def _tenant_config(
    session: AsyncSession, tenant_id: int, category: str, audience: str
) -> dict[str, object] | None:
    """The firm-level config row for a category + audience, if configured (else None).

    ``audience`` is the coarse 'client' vs 'adviser' bucket the admin defaults matrix sets — a
    client-audience notification reads the 'client' row, every staff audience the 'adviser' row."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT enabled, in_app, email, mandatory FROM notifications.tenant_config "
                    "WHERE tenant_id = :t AND category = :cat AND audience = :aud"
                ),
                {"t": tenant_id, "cat": category, "aud": audience},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


async def _effective_channels(
    session: AsyncSession,
    tenant_id: int,
    recipient: Recipient,
    category: str,
    tconf: dict[str, object] | None,
    *,
    base_in_app: bool,
    base_email: bool,
    mandatory: bool,
    pref_trigger: str | None,
) -> dict[str, bool]:
    """Resolve a recipient's channels, most-specific wins: the RULE default (``base_*``) -> the
    tenant category default (``tconf``) -> the recipient's own override. When the rule is
    ``mandatory`` the recipient cannot change it, so the rule default is forced. The firm off-switch
    (``tconf.enabled = false``) is handled by the caller before this runs.

    ``pref_trigger`` is the grain of the recipient's own override: a client's preference is
    per-NOTIFICATION (the trigger key), staff's is per-category (``None``). NULLS NOT DISTINCT on
    the query means a category-level row (NULL) matches when ``pref_trigger`` is None."""
    if mandatory:
        return {"in_app": base_in_app, "email": base_email}
    base = {"in_app": base_in_app, "email": base_email}
    if tconf is not None:
        if tconf["in_app"] is not None:
            base["in_app"] = bool(tconf["in_app"])
        if tconf["email"] is not None:
            base["email"] = bool(tconf["email"])
    row = (
        (
            await session.execute(
                text(
                    "SELECT in_app, email FROM notifications.preferences "
                    "WHERE tenant_id = :t AND category = :cat "
                    "  AND trigger_key IS NOT DISTINCT FROM :tk "
                    "  AND recipient_kind = :rk AND recipient_user_id IS NOT DISTINCT FROM :ru "
                    "  AND recipient_contact_id IS NOT DISTINCT FROM :rc"
                ),
                {
                    "t": tenant_id,
                    "cat": category,
                    "tk": pref_trigger,
                    "rk": recipient.kind,
                    "ru": recipient.user_id,
                    "rc": recipient.contact_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return base
    return {
        "in_app": base["in_app"] if row["in_app"] is None else bool(row["in_app"]),
        "email": base["email"] if row["email"] is None else bool(row["email"]),
    }


async def channels_for(
    session: AsyncSession,
    tenant_id: int,
    recipient: Recipient,
    category: str,
    *,
    audience: str = "client",
    trigger_key: str | None = None,
    default_in_app: bool = True,
    default_email: bool = False,
    mandatory: bool = False,
) -> dict[str, bool]:
    """The effective channels for ONE recipient — the single-recipient door onto the same 3-layer
    resolution ``notify`` fans out with (rule default -> firm category config -> the person's own
    override), including the firm-wide off-switch and the mandatory force.

    It exists for producers that send OUTSIDE the notifications tail and must still obey what the
    preferences UI shows. ``app/tasks/notifier.py`` is the first: it scans ``tasks.tasks`` and
    emails assignees directly, and for as long as it consulted nothing a firm could switch task
    email off and keep receiving it — the setting and the behaviour were two different systems that
    never spoke. Anything else that grows its own send path belongs here too, rather than growing a
    second interpretation of the same three layers.

    ``notify`` deliberately does NOT call this: it reads the firm config ONCE per fan-out and reuses
    it across every recipient, where this reads per call. Both compose the same two functions in the
    same order, and ``test_task_notifier_preferences.py`` pins that they agree.
    """
    config_audience = "client" if audience == "client" else "adviser"
    tconf = await _tenant_config(session, tenant_id, category, config_audience)
    # The firm-wide off-switch: nothing is produced for anyone, whatever their own setting.
    if tconf is not None and not tconf["enabled"]:
        return {"in_app": False, "email": False}
    forced = mandatory or bool(tconf and tconf.get("mandatory"))
    return await _effective_channels(
        session,
        tenant_id,
        recipient,
        category,
        tconf,
        base_in_app=default_in_app,
        base_email=default_email,
        mandatory=forced,
        pref_trigger=trigger_key,
    )


async def notify(
    session: AsyncSession,
    *,
    tenant_id: int,
    recipients: list[Recipient],
    category: str,
    portal: str,
    title: str,
    event_type: str,
    source_kind: str,
    source_ref: str | None = None,
    body: str | None = None,
    link: str | None = None,
    priority: str = "normal",
    subject_type: str | None = None,
    subject_id: str | None = None,
    source_action_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    default_in_app: bool = True,
    default_email: bool = False,
    mandatory: bool = False,
    audience: str = "client",
    trigger_key: str | None = None,
    delivered: list[Recipient] | None = None,
) -> int:
    """Fan one notification out to ``recipients``; returns the number of in-app rows inserted.

    ``default_in_app``/``default_email``/``mandatory`` are the RULE's channel defaults (the audience
    default or its stored override); they feed ``_effective_channels``. Idempotent per (recipient,
    source_kind, source_ref) — pass a deterministic ``source_ref`` so a redelivered event does not
    duplicate. Runs on the caller's (service_role) transaction.

    ``delivered``, when given, collects every recipient that got a genuinely NEW in-app row (not a
    dedup no-op) — the caller announces those on the realtime channel after the transaction commits.
    """
    if priority not in PRIORITIES:
        priority = "normal"
    # The subject is a PAIR or nothing: `notifications_subject_pair` CHECKs
    # `(subject_type IS NULL) = (subject_id IS NULL)`, so a type with no id is not a partial answer:
    # it is a failed INSERT that takes the caller's whole transaction with it.
    #
    # And that combination is reachable: `catalogue._subject_of` names the KIND from the event's
    # vocabulary but resolves the ID by lookup, so anything that removed the referenced row between
    # the action and its processing yields ('contact', None). The tail rarely met it — it reads
    # rows seconds old; `consumer.resweep_once` re-reads minutes, which is how it surfaced. One
    # deleted contact would otherwise crash every repair pass for as long as its action stayed in
    # the window — blocking the very mechanism meant to recover missed notifications.
    #
    # Dropping the type is the right repair, not inventing an id: the subject only ever drives an
    # optional deep-link (`consumer._click_link`), so losing it costs a click and keeps the notice.
    if subject_id is None:
        subject_type = None
    # The firm-level layer, read ONCE per fan-out (same for every recipient). A client-audience
    # notification reads the 'client' config row; every staff audience the 'adviser' row. A disabled
    # category is the firm-wide off-switch: nothing is produced for anyone, whatever their setting.
    config_audience = "client" if audience == "client" else "adviser"
    tconf = await _tenant_config(session, tenant_id, category, config_audience)
    if tconf is not None and not tconf["enabled"]:
        return 0
    # Mandatory is forced by EITHER the rule or the firm's default for this audience — a firm can
    # make a category unmissable (over a prior opt-out) without authoring a rule.
    forced = mandatory or bool(tconf and tconf.get("mandatory"))
    # Personal preferences are per NOTIFICATION (the trigger) for everyone — clients and staff alike
    # (the adviser prefs surface mirrors the client one). The firm-wide default (tconf) stays per
    # category; this is only the recipient's own override.
    pref_trigger = trigger_key
    inserted = 0
    seen: set[str] = set()
    for r in recipients:
        if r.key() in seen:  # de-dupe within one fan-out (a person in two roles, say)
            continue
        seen.add(r.key())
        channels = await _effective_channels(
            session,
            tenant_id,
            r,
            category,
            tconf,
            base_in_app=default_in_app,
            base_email=default_email,
            mandatory=forced,
            pref_trigger=pref_trigger,
        )
        if channels["in_app"]:
            result = await session.execute(
                text(
                    "INSERT INTO notifications.notifications (uid, tenant_id, recipient_kind, "
                    "  recipient_user_id, recipient_contact_id, portal, event_type, category, "
                    "  title, body, link, priority, subject_type, subject_id, source_kind, "
                    "  source_ref, source_action_id, metadata) "
                    "VALUES (:uid, :t, :rk, :ru, :rc, :portal, :et, :cat, :title, :body, :link, "
                    "  :prio, :sty, :sid, :sk, :sr, :sa, CAST(:meta AS jsonb)) "
                    "ON CONFLICT (recipient_kind, recipient_user_id, recipient_contact_id, "
                    "  source_kind, source_ref) WHERE source_ref IS NOT NULL DO NOTHING "
                    "RETURNING id"
                ),
                {
                    "uid": new_id(),
                    "t": tenant_id,
                    "rk": r.kind,
                    "ru": r.user_id,
                    "rc": r.contact_id,
                    "portal": portal,
                    "et": event_type,
                    "cat": category,
                    "title": title,
                    "body": body,
                    "link": link,
                    "prio": priority,
                    "sty": subject_type,
                    "sid": subject_id,
                    "sk": source_kind,
                    "sr": source_ref,
                    "sa": source_action_id,
                    "meta": json.dumps(metadata or {}),
                },
            )
            if result.first() is not None:
                inserted += 1
                if delivered is not None:
                    delivered.append(r)
        # EMAIL SEAM (v1 no-op). When the email channel lands:
        #   if channels["email"]:
        #       await flows_notifications.enqueue(session, tenant_id=tenant_id,
        #           event_type=f"notify.{category}", payload={... rendered html, sender, reply_to})
    return inserted
