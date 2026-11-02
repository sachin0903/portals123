"""The curated notification-event catalogue — the builder's menu.

Human-named TRIGGERS over the raw ``audit.actions`` vocabulary (the automations-triggers pattern),
each declaring the event type(s) it fires on and the AUDIENCES a rule may target — with, per
audience, how recipients resolve and the built-in default copy + channel defaults. Never expose a
raw ``event_type`` to an author; they pick a trigger and an audience.

The trigger set is the **action-sourced** half of the automations registry (``app/flows/triggers``)
— the events that actually land on ``audit.actions`` (domain/schedule triggers cannot fire through
this tail), plus the worker-emitted task notices, which are on the stream but not on any route.
Those that ship a built-in default (document sent/requested → the client, a task assigned or nearly
due → its assignee) keep their hand-written resolvers, because a document send names *several*
contacts and the generic single-subject resolver would drop all but one. Every other trigger is
OFFERED to the builder but ``builtin=False`` — it fires only once a firm authors a rule for it, so
exposing the whole catalogue changes nothing a tenant receives until they build something.

Recipients for the offered triggers resolve GENERICALLY from the event's subject, reusing the
automations subject resolver (``automation.resolve_subject``): a contact subject → that client and
its book advisers, an account subject → the account's owner and their advisers, a task → its
assignee. ``actor`` (whoever did it) is always available.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.notifications import registry
from app.notifications.registry import (
    GROUP_REQUEST_EVENT,
    Recipient,
    _resolve_document_contacts,
    _resolve_request_contacts,
    _resolve_review_proposer,
    _resolve_review_role,
    _resolve_task_assignee,
)

Resolver = Callable[[AsyncSession, dict[str, Any], int], Awaitable[list[Recipient]]]


# ---- subject resolution (reused from the automations engine) ---------------------------------


async def _subject_of(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> tuple[str | None, int | None]:
    """The (kind, id) the event's trigger is about, via the automations subject resolver.

    Imported lazily to keep the notifications package free of an import-time dependency on the
    flows engine. ``resolve_subject`` never raises and returns ``None`` when the event names no
    subject, so an unmappable event simply yields no recipients (not an error)."""
    from app.flows.automation import resolve_subject
    from app.flows.triggers import TRIGGERS

    event_type = str(action.get("event_type"))
    spec = next((s for s in TRIGGERS.values() if s.event == event_type), None)
    if spec is None:
        return None, None
    sid = await resolve_subject(session, spec, action, tenant_id=tenant_id)
    return spec.subject, sid


async def _subject_contact_id(
    session: AsyncSession, kind: str | None, sid: int | None, tenant_id: int
) -> int | None:
    """Normalise a subject to the client contact it concerns: a contact IS one; an account resolves
    to its owner; anything else (task/application) has no single client contact here."""
    if sid is None:
        return None
    if kind == "contact":
        return sid
    if kind == "account":
        row = await session.execute(
            text("SELECT contact_id FROM platform.accounts WHERE id = :i AND tenant_id = :t"),
            {"i": sid, "t": tenant_id},
        )
        return row.scalar_one_or_none()
    return None


async def _advisers_of_contact(
    session: AsyncSession, contact_id: int, tenant_id: int
) -> list[Recipient]:
    """The contact's enabled adviser — as a user recipient. ``role = 'adviser'`` is unique per
    contact (0245); a paraplanner/administrator/support row on the same contact is not the
    'adviser' audience. Still returns a list (usually 0 or 1 entries) for the caller's shape."""
    rows = (
        await session.execute(
            text(
                "SELECT ca.adviser_id::text FROM crm.client_advisers ca "
                "JOIN auth.users u ON u.id = ca.adviser_id "
                "WHERE ca.tenant_id = :t AND ca.contact_id = :c AND u.enabled "
                "  AND ca.role = 'adviser'"
            ),
            {"t": tenant_id, "c": contact_id},
        )
    ).scalars()
    seen: set[str] = set()
    out: list[Recipient] = []
    for uid in rows:
        if uid and uid not in seen:
            seen.add(uid)
            out.append(Recipient(kind="user", user_id=uid))
    return out


async def _resolve_client(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> list[Recipient]:
    """The client the event concerns (its subject contact, or an account's owner)."""
    kind, sid = await _subject_of(session, action, tenant_id)
    cid = await _subject_contact_id(session, kind, sid, tenant_id)
    return [Recipient(kind="contact", contact_id=cid)] if cid is not None else []


async def _resolve_adviser(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> list[Recipient]:
    """The book advisers of the client the event concerns."""
    kind, sid = await _subject_of(session, action, tenant_id)
    cid = await _subject_contact_id(session, kind, sid, tenant_id)
    return await _advisers_of_contact(session, cid, tenant_id) if cid is not None else []


async def _resolve_actor(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> list[Recipient]:
    """The person who performed the action (rarely wanted — usually you don't notify the actor)."""
    actor = action.get("actor_id")
    return [Recipient(kind="user", user_id=str(actor))] if actor else []


# The book advisers of the contact(s) a DOCUMENT event names — multi-contact (from
# metadata.subject), so a multi-recipient send is honoured where the generic resolver would not.
async def _resolve_document_contact_advisers(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> list[Recipient]:
    contacts = await _resolve_document_contacts(session, action, tenant_id)
    out: list[Recipient] = []
    seen: set[str] = set()
    for c in contacts:
        if c.contact_id is None:
            continue
        for r in await _advisers_of_contact(session, c.contact_id, tenant_id):
            if r.user_id and r.user_id not in seen:
                seen.add(r.user_id)
                out.append(r)
    return out


# ---- messaging (client<->adviser chat) -------------------------------------------------------
# A chat message notifies the OTHER party. Direction is read from the event, never captured: the
# message BODY is client-facing free text and must NEVER enter a notification (there is deliberately
# no SUBJECT_FIELDS entry for these events), so the notice carries only the SENDER's name via
# {actor.name} — which context.build_context resolves generically from actor_id for a client sender
# or a staff sender alike. {client.name} does NOT resolve for these events (they name no subject),
# so authored copy should use {actor.name}; the picker's offer of {client.name} is the F4
# over-advertising to be tidied there, not relied on here.


def _conversation_key(path_params: dict[str, Any]) -> str | int | None:
    """Which conversation a message route names, whichever key it names it by.

    THE DEFECT THIS EXISTS FOR. Both resolvers read `path_params["conversation_id"]`, and 0112
    renamed the path param to `conversation_uid` — so that key was never present, and both went
    wrong in opposite directions from the same cause:

      * the direction guard below stopped recognising a STAFF send, fell through to the
        client-sent branch, and notified the client's advisers — INCLUDING the adviser who had
        just sent the message. That is the "why am I being told about my own message" report;
      * `_resolve_message_recipient_contacts` did `int(None)`, caught the TypeError and returned
        nobody, so a staff message notified the CLIENT it was addressed to not at all.

    Neither surfaced as an error: one over-notified, the other silently under-notified, and both
    read as a notification-rules question rather than a renamed dictionary key. The serial is still
    accepted for rows written before the rename.
    """
    uid = path_params.get("conversation_uid")
    if isinstance(uid, str) and uid:
        return uid
    raw = path_params.get("conversation_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _resolve_message_thread_advisers(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> list[Recipient]:
    """A CLIENT sent a 1:1 message → notify the book adviser(s) of THE CLIENT WHOSE THREAD IT IS.

    The thread routes carry no path param (the client's conversation is derived from their own
    scope), so the actor is the only handle on the sender — but the actor is NOT enough on its own:
    one login can be linked to several contacts (`crm.contact_users.capacity` allows
    authorised/power_of_attorney/delegate), while a client's thread belongs to exactly ONE contact
    (`messaging.get_or_create_conversation_for_caller` keys it on a single contact id). Fanning out
    over every linked contact therefore notified the advisers of every client that login can act
    for, not the one the message was actually about.

    So resolve the CONVERSATION instead: the thread this actor most recently sent a client message
    on, within this tenant. That names one contact, whichever of the login's contacts it belongs
    to. (The event is written straight after the send, so the newest message is this one.)

    The join to the actor goes through ``crm.contact_users``, NOT through
    ``messaging.messages.sender_user_id``. That column is the obvious one and it is ALWAYS NULL on a
    client message: ``messaging.send_client_message`` passes ``None`` for it (a client's name comes
    off the conversation, so nothing needed it), and matching on it therefore never returned a row —
    NULL equals nothing. Client-to-adviser notifications silently resolved to nobody for every
    message ever sent, while adviser-to-client worked, because that direction reads a conversation
    id off the route path instead. The test missed it by SEEDING a message with `sender_user_id`
    set — a shape the send path does not produce (verified against the live table: 17 client
    messages, 0 with a sender user id). Do not "simplify" this back onto that column without first
    making the writer populate it.

    DIRECTION IS CHECKED FIRST, and it has to be. Both ends emit ``message.sent``, and the lookup
    below asks "which thread has this login most recently sent a client message on" - a
    question a STAFF send answers just as readily, because a staff login can also be linked to a
    contact (``crm.contact_users``, capacity 'self' for their own client record, or as an authorised
    person for someone else's). An adviser's own message then resolved that contact's advisers and
    notified them - including the sender - alongside the client who correctly received it. The staff
    route names its conversation on the PATH and the client's thread route names nothing, which is
    the same marker ``_resolve_message_recipient_contacts`` reads to answer the mirror question.

    EVERY adviser on the client is notified, and the actor is NOT filtered out. There was a filter
    here - "never tell someone about their own message" - and it was the wrong answer to a question
    the direction check above already settles. Its only effect was on a login that is both an
    adviser of a client AND the login that acts AS that client, which a firm's own staff genuinely
    are when they are also its client: their message then notified every colleague on the book
    except them. Silently, and to the one person most likely to be watching for it."""
    path_params = action.get("path_params")
    if isinstance(path_params, dict) and _conversation_key(path_params) is not None:
        return []  # a staff send: the client is the recipient, not the adviser.
    actor = action.get("actor_id")
    if not actor:
        return []

    # The event NAMES whose channel it is, so prefer that over inferring it. `contact_id` is lifted
    # off the send response (event_schema/registry), which is exact where the query below is a
    # guess: it asks for the newest client message on any conversation this login can reach, and a
    # login linked to several contacts (authorised person, delegate) can have a NEWER message on a
    # different one by the time this runs - the tail is deliberately LAG_SECONDS behind, and
    # `resweep_once` re-reads MINUTES back, where "newest" is almost certainly not the message that
    # raised the event. That would notify the wrong client's advisers, which is a disclosure, not
    # just a miss. The lookup stays as the fallback for rows written before the capture existed.
    subject = action.get("metadata")
    if isinstance(subject, dict):
        named = subject.get("subject")
        if isinstance(named, dict) and named.get("contact_id") is not None:
            try:
                contact = int(named["contact_id"])
            except (TypeError, ValueError):
                contact = 0
            if contact:
                return await _advisers_of_contact(session, contact, tenant_id)
    contact_id = (
        await session.execute(
            text(
                "SELECT c.contact_id FROM messaging.messages m "
                "JOIN messaging.conversations c ON c.id = m.conversation_id "
                "JOIN crm.contact_users cu ON cu.contact_id = c.contact_id "
                "  AND cu.tenant_id = c.tenant_id "
                "WHERE c.tenant_id = :t AND m.sender_kind = 'client' "
                "  AND cu.user_id = CAST(:u AS uuid) "
                "ORDER BY m.created_at DESC, m.id DESC LIMIT 1"
            ),
            {"t": tenant_id, "u": str(actor)},
        )
    ).scalar_one_or_none()
    if contact_id is None:
        return []
    return await _advisers_of_contact(session, int(contact_id), tenant_id)


async def _resolve_message_recipient_contacts(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> list[Recipient]:
    """A STAFF member sent a message → notify the client(s). A GROUP send (group_id on the path)
    fans out to every member (reusing the group resolver); a 1:1 send names the conversation, whose
    contact is the recipient. The actor (staff) is never a recipient, so no exclusion is needed."""
    et = str(action.get("event_type"))
    if "group" in et:
        return await registry._resolve_group_members(session, action, tenant_id)
    path_params = action.get("path_params")
    if not isinstance(path_params, dict):
        return []
    key = _conversation_key(path_params)
    if key is None:
        return []
    # Matched on EITHER key, and the column is chosen by which one the row carries — a uid is not
    # an id and casting one to the other is how this broke.
    column = "uid" if isinstance(key, str) else "id"
    cid = (
        await session.execute(
            text(  # noqa: S608 - `column` is one of two fixed literals chosen above
                f"SELECT contact_id FROM messaging.conversations "  # noqa: S608
                f"WHERE {column} = :c AND tenant_id = :t"
            ),
            {"c": key, "t": tenant_id},
        )
    ).scalar_one_or_none()
    return [Recipient(kind="contact", contact_id=int(cid))] if cid is not None else []


@dataclass(frozen=True)
class Audience:
    key: str  # client | adviser | actor | assignee
    label: str
    resolve: Resolver
    default_title: str
    default_body: str
    # Plain, context-free copy for a recipient's preferences screen. A delivered notification may
    # use merge fields ("New message from {actor.name}"); a settings row has no event or actor to
    # resolve them against and must never print the token literally.
    preference_label: str | None = None
    default_link: str | None = None
    default_in_app: bool = True
    default_email: bool = False
    mandatory: bool = False
    # The email TEMPLATE this audience's notification renders (an ``email.templates`` code). None →
    # the general ``notification`` wrapper. The two document notices point at their own templates so
    # the rich document email is what a firm gets if it turns their email on; every other audience
    # defaults to the wrapper and a firm can pick a template per rule in the builder.
    email_code: str | None = None
    # A BUILT-IN audience fires by default (it is today's shipped behaviour). A non-built-in one is
    # offered by the builder but only fires once a firm authors a rule for it — so adding audiences
    # here never changes what a tenant with no stored rules receives.
    builtin: bool = True


@dataclass(frozen=True)
class Trigger:
    key: str
    label: str
    description: str
    event_types: tuple[str, ...]
    category: str  # groups the trigger + drives the notification row's category
    audiences: tuple[Audience, ...]
    # What the event is ABOUT (a flows SUBJECT_KIND: contact | account | task | application). Drives
    # the merge PARAMS a rule's copy may use — see ``params_for``.
    subject_kind: str = "contact"
    # Params beyond those the subject kind implies — for a trigger that carries an extra resolvable
    # entity (a document send names the document via its route path).
    extra_params: tuple[Param, ...] = ()


@dataclass(frozen=True)
class Param:
    """One merge placeholder a rule's copy may carry — dotted key + a human label + an example the
    builder previews with. Rendered by ``app.notifications.render`` against the built context."""

    key: str  # dotted, e.g. 'client.name'
    label: str
    example: str


_ACTOR_PARAM = Param("actor.name", "Who did it", "Sarah Adviser")
_ACTOR_EMAIL_PARAM = Param("actor.email", "Who did it (email)", "sarah@oakview.example")
_EVENT_WHEN_PARAM = Param("event.when", "When it happened", "15 Jan 2026")
_FIRM_PARAM = Param("firm.name", "Firm name", "Oakview Wealth")
_CLIENT_PARAM = Param("client.name", "Client name", "Jane Doe")
_ADVISER_PARAM = Param("adviser.name", "The client's adviser", "Sarah Adviser")
_ACCOUNT_NAME_PARAM = Param("account.name", "Account name", "ISA")
_ACCOUNT_NUMBER_PARAM = Param("account.number", "Account number", "ISA-000123")
_TASK_PARAM = Param("task.title", "Task title", "Complete your fact-find")
_TASK_DUE_PARAM = Param("task.due_date", "Task due date", "22 Jan 2026")
_TASK_DESC_PARAM = Param("task.description", "Task description", "Check the ATR answers")
_DOCUMENT_PARAM = Param("document.name", "Document name", "Suitability report")
_DOCUMENT_TYPE_PARAM = Param("document.type", "Document type", "Suitability report")
_REVIEW_LABEL_PARAM = Param("review.label", "What needs review", "Update client bank account")
_REVIEW_CASE_PARAM = Param("review.case", "Review case", "RV-12")
_REVIEW_UID_PARAM = Param("review.uid", "Review id", "abc123xyz789")


# Events whose ROUTE PATH names a document — the {uid} path param IS the document (the send is
# POST /documents/{uid}/send). One source of truth: context._document_uid resolves the id from here
# for the merge context, and link_targets_for offers a click destination to it.
#: Both spellings of the same act. The route was renamed send -> notify (a document is not
#: 'sent' anywhere — the client is notified that one is waiting), and `audit.actions` is
#: append-only, so the rows written under the old name are in the stream for good. A trigger
#: that knew only the new name would silently stop matching everything before the rename.
#: Only `document.sent` remains, and only for HISTORY: both routes that emitted these are gone (send
#: was renamed notify, notify was removed in favour of document requests). Kept so a rule authored
#: against a past event still resolves its document, and dropped from nothing else.
DOCUMENT_PATH_EVENTS: dict[str, str] = {"document.sent": "uid", "document.notified": "uid"}

# Events where a document is resolved INDIRECTLY (not a path param). A client fulfilling a request
# item by upload creates a document linked to the item (request_items.fulfilled_document_id), so
# ``context._resolved_document_uid`` looks it up from the item_uid on the event. Offered as a "The
# document" target too, so the adviser's "a client uploaded a document" notice links to the upload.
DOCUMENT_RESOLVED_EVENTS: frozenset[str] = frozenset({"document_request.item_fulfilled"})


def _names_a_document(trig: Trigger) -> bool:
    """Whether a document click-target can be resolved for this trigger's event(s) — by path param
    or by the indirect lookup above."""
    return any(
        et in DOCUMENT_PATH_EVENTS or et in DOCUMENT_RESOLVED_EVENTS for et in trig.event_types
    )


def link_targets_for(trig: Trigger) -> list[dict[str, object]]:
    """Named click destinations that resolve to the SPECIFIC item the event is about — "The
    document", "The account", "The client", "The task" — rendered per-notification from the merge
    context, exactly like the copy (``/documents/{document.uid}`` → ``/documents/doc_…``). Offered
    in the builder as ready-made choices so an author needn't know or hand-type a path.

    ``client`` marks a target valid for a CLIENT-audience notice: a client's portal has
    ``/documents/{uid}`` and ``/accounts/{uid}`` but no ``/clients/{uid}`` record view or task view,
    so those two are staff-only. Only a destination we can ACTUALLY resolve at delivery is listed
    (the id must reach the context — see ``notifications.context``)."""
    targets: list[dict[str, object]] = []
    # A document-naming event → the specific document (both portals have /documents/{id}). Includes
    # events where the document resolves indirectly (a client's upload), not just path-named ones.
    if _names_a_document(trig):
        targets.append(
            {
                "key": "document",
                "label": "The document",
                "href": "/documents/{document.uid}",
                "client": True,
            }
        )
    # The event's own subject, addressed by the route that shows it.
    if trig.subject_kind == "account":
        targets.append(
            {
                "key": "account",
                "label": "The account",
                "href": "/accounts/{account.uid}",
                "client": True,
            }
        )
    elif trig.subject_kind == "contact":
        targets.append(
            {
                "key": "contact",
                "label": "The client",
                "href": "/clients/{contact.uid}",
                "client": False,
            }
        )
    elif trig.subject_kind == "task":
        targets.append(
            {"key": "task", "label": "The task", "href": "/tasks/{task.uid}", "client": False}
        )
    if any(et.startswith("review.") for et in trig.event_types):
        targets.append(
            {
                "key": "review",
                "label": "The review",
                "href": "/reviews?open={review.uid}",
                "client": False,
            }
        )
    return targets


def params_for(trig: Trigger) -> list[Param]:
    """The merge placeholders a trigger's copy may use, derived from its subject kind. Every trigger
    exposes the actor + firm; a contact/account subject adds the client (an account also its name +
    number), a task its title. Only params we can ACTUALLY resolve at delivery are offered.
    ``extra_params`` carries any beyond the subject kind — e.g. the document send, whose
    ``{document.name}`` comes from the document id on the route PATH (not from ``metadata.subject``,
    which the send action fills with contact ids only).

    The MESSAGING triggers are a partial exception. They still have no ``flows.triggers`` entry, so
    the generic subject resolver finds nothing — but ``message.sent``/``message.attachment_sent``
    now capture ``contact_id`` off the response (event_schema/registry), which
    ``context._fallback_contact_id`` reads, so ``{client.name}`` resolves for them like any other
    contact-subject trigger. It resolves for a client RECIPIENT too, by a different route
    (``context_for_recipient`` overrides it per recipient). Both are offered.

    ``{adviser.name}`` is deliberately still withheld: for the adviser-audience trigger it would
    name the recipient back to themselves, and for a GROUP send there is no single contact to
    resolve one from. Only params we can ACTUALLY resolve at delivery are offered — advertising a
    token that renders blank is the failure this rule exists to prevent, and the message body is
    never captured under any of it."""
    params: list[Param] = list(trig.extra_params)
    messaging = trig.category == "messages"
    if trig.subject_kind in ("contact", "account") and not messaging:
        # A client subject implies the client AND their book adviser (both from build_context).
        params += [_CLIENT_PARAM, _ADVISER_PARAM]
    elif messaging:
        params.append(_CLIENT_PARAM)
    if trig.subject_kind == "account":
        params += [_ACCOUNT_NAME_PARAM, _ACCOUNT_NUMBER_PARAM]
    if trig.subject_kind == "task":
        params += [_TASK_PARAM, _TASK_DUE_PARAM, _TASK_DESC_PARAM]
    # Universal — build_context resolves the actor (name + email) and the event's own timestamp for
    # every action; the firm is always available.
    params += [_ACTOR_PARAM, _ACTOR_EMAIL_PARAM, _EVENT_WHEN_PARAM, _FIRM_PARAM]
    return params


# The triggers that ship a built-in default — hand-written resolvers (multi-contact document
# sends, the task assignee) that the generic single-subject resolver cannot reproduce.
_BUILTIN: list[Trigger] = [
    Trigger(
        key="document.shared",
        label="When a document is shared with a client",
        description="A staff member sends a document to a client.",
        event_types=("document.sent",),
        category="documents",
        subject_kind="contact",
        extra_params=(_DOCUMENT_PARAM, _DOCUMENT_TYPE_PARAM),
        audiences=(
            Audience(
                key="client",
                label="The client",
                resolve=_resolve_document_contacts,
                default_title="A document was shared with you",
                default_body="Your adviser has shared a document. Open your portal to view it.",
                # Deep-links to the SPECIFIC document (rendered per-notification); /documents/{id}
                # exists in the client portal too, so the client lands on the exact document.
                default_link="/documents/{document.uid}",
                # Renders the "Document sent" template, and email is ON by default because it HAS a
                # dedicated template (the wrapper-only notices ship email-off).
                email_code="document_sent",
                default_email=True,
            ),
            Audience(
                key="adviser",
                label="The client's adviser",
                resolve=_resolve_document_contact_advisers,
                default_title="Your document was delivered",
                default_body="A document you sent has been delivered to the client's portal.",
                default_link="/documents/{document.uid}",
                default_in_app=False,
                builtin=False,
            ),
        ),
    ),
    # `document.notice` stood here — "when a client is told about a document". It is gone with the
    # notify route: a DOCUMENT REQUEST already asks a client to act on a document and records
    # whether they did, so the notice was the weaker of two features doing one job.
    #
    # Nothing needs migrating. A trigger is matched by `event_types`, and no route emits
    # `document.notified` any more; rules a firm authored against it simply stop matching, which is
    # the same outcome as the act never happening again.
    Trigger(
        key="document.request",
        label="When a document request is raised",
        description="A staff member asks a client to provide a document.",
        # Both entry paths: a single request AND a group request (one ask fanned out to every
        # member). The group action names only its group_id, so the resolver expands the members —
        # without this the group path notifies no one (neither consumer sees the fan-out).
        event_types=(
            "document_request.created",
            GROUP_REQUEST_EVENT,
        ),
        category="documents",
        subject_kind="contact",
        audiences=(
            Audience(
                key="client",
                label="The client",
                resolve=_resolve_request_contacts,
                default_title="Action needed: a document request",
                default_body="Your adviser has requested something. Open your portal to respond.",
                default_link="/documents",
                # Renders the "Document request" template; email ON by default because it has a
                # dedicated template (wrapper-only notices ship email-off).
                email_code="request_raised",
                default_email=True,
            ),
        ),
    ),
    # The two task notices. Their events come from the WORKER's scan (``app/tasks/notifier.py``),
    # not from the create route: every creation path funnels through the scan's stamps, and "nearly
    # due" is the clock's doing. `task.created` is deliberately no longer a trigger event — it fires
    # only when the API route is the door, and its subject is unresolvable there (``tasks.tasks`` is
    # not on ``audit.attach``), so `{task.title}` rendered blank on every notice.
    #
    # TWO triggers rather than one with two events: they render different email templates
    # (``email_code`` is per audience) and a firm must be able to silence the chase without losing
    # the assignment. Both ship email ON, because a task email is what the bespoke sender these
    # replaced already did — turning it off is now the firm's choice to make, not this change's.
    Trigger(
        key="task.assigned",
        label="When a task is assigned",
        description="A task is assigned to a staff member, role or client.",
        event_types=("task.assigned",),
        category="tasks",
        subject_kind="task",
        audiences=(
            Audience(
                key="assignee",
                label="The assignee",
                resolve=_resolve_task_assignee,
                default_title="New task: {task.title}",
                default_body="{task.title} is waiting in your task inbox.",
                # The task itself, not the inbox listing: single braces are the token grammar
                # ``render`` compiles, and ``build_context`` publishes the uid on every task notice.
                default_link="/tasks/{task.uid}",
                email_code="task_assigned",
                default_email=True,
            ),
        ),
    ),
    Trigger(
        key="task.due_soon",
        label="When a task is nearly due",
        description="A task assigned to a staff member, role or client is due soon.",
        event_types=("task.due_soon",),
        category="tasks",
        subject_kind="task",
        audiences=(
            Audience(
                key="assignee",
                label="The assignee",
                resolve=_resolve_task_assignee,
                default_title="Task due soon: {task.title}",
                # The date always resolves here: the scan only emits for a task WITH a due date.
                default_body="{task.title} is due {task.due_date}.",
                default_link="/tasks/{task.uid}",
                email_code="task_reminder",
                default_email=True,
            ),
        ),
    ),
    Trigger(
        key="review.requested",
        label="When a change needs peer review",
        description="A staff member submitted a change that needs a second person's approval.",
        event_types=("review.requested",),
        category="reviews",
        subject_kind="contact",
        extra_params=(_REVIEW_LABEL_PARAM, _REVIEW_CASE_PARAM, _REVIEW_UID_PARAM),
        audiences=(
            Audience(
                key="reviewer",
                label="The reviewer role",
                resolve=_resolve_review_role,
                default_title="Review needed: {review.case} · {review.label}",
                default_body="A colleague submitted a change that needs your approval.",
                default_link="/reviews?open={review.uid}",
                default_email=True,
            ),
        ),
    ),
    Trigger(
        key="review.decided",
        label="When a peer review is decided",
        description="A change you submitted was approved, rejected, or conflicted.",
        event_types=("review.approved", "review.rejected", "review.conflicted"),
        category="reviews",
        subject_kind="contact",
        extra_params=(_REVIEW_LABEL_PARAM, _REVIEW_CASE_PARAM, _REVIEW_UID_PARAM),
        audiences=(
            Audience(
                key="proposer",
                label="The person who submitted it",
                resolve=_resolve_review_proposer,
                default_title="Peer review update: {review.case} · {review.label}",
                default_body="A decision was made on a change you submitted.",
                default_link="/reviews?open={review.uid}",
                default_email=True,
            ),
        ),
    ),
    # Chat messages notify the OTHER party — the whole point of the messaging feature having a bell.
    # TWO separate single-audience triggers (not one with both audiences over all six events), so a
    # client-send never offers the client audience and a staff-send never offers the adviser one —
    # the wrong-direction resolver is never even paired. The body is never in the copy (see the
    # resolver note above): only {actor.name}, the sender.
    Trigger(
        key="message.received_by_adviser",
        label="When a client sends a message",
        description="A client sends a chat message to their adviser.",
        event_types=(
            "message.sent",
            "message.attachment_sent",
        ),
        category="messages",
        subject_kind="contact",
        audiences=(
            Audience(
                key="adviser",
                label="The client's adviser",
                resolve=_resolve_message_thread_advisers,
                # {client.name}, NOT {actor.name}. The actor is the LOGIN that sent it and the
                # sender an adviser cares about is the CLIENT whose channel it is - and the two are
                # not the same value even when they are the same person: the actor resolves an
                # `auth.users` name, the client a `crm.contacts` one, and a real book has them
                # differing. Worse, one login may act for several contacts (authorised person,
                # power of attorney, delegate), so the actor can be a different person entirely.
                # It resolves because the event now captures `contact_id` (event_schema/registry),
                # which `context._fallback_contact_id` reads with no special case.
                default_title="New message from {client.name}",
                default_body="You have a new message. Open your inbox to reply.",
                default_link="/chats",
            ),
        ),
    ),
    Trigger(
        key="message.received_by_client",
        label="When an adviser sends a message",
        description="A staff member sends a chat message to a client (1:1 or a group).",
        event_types=(
            "message.sent",
            "message.attachment_sent",
            "message_group.message_sent",
            "message_group.attachment_sent",
        ),
        category="messages",
        subject_kind="contact",
        audiences=(
            Audience(
                key="client",
                label="The client",
                resolve=_resolve_message_recipient_contacts,
                default_title="New message from {actor.name}",
                default_body="You have a new message from your adviser. Open your portal to reply.",
                preference_label="New message from your adviser",
                # `/messages`, not `/adviser/messages` — the latter names no route at all, so
                # every "new message from your adviser" notice sent the client to a 404. The
                # client portal's thread has always lived at `/messages`.
                default_link="/messages",
            ),
        ),
    ),
]


# The rest of the action-sourced automations triggers, OFFERED but not shipped-on (builtin=False):
# (key, label, event_type, category, subject_kind). Their events are distinct from the built-ins'.
_OFFERED: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "document.generated",
        "When a document is generated",
        "document.generated",
        "documents",
        "contact",
    ),
    (
        "client.document_uploaded",
        "When a client uploads a document",
        "document_request.item_fulfilled",
        "documents",
        "contact",
    ),
    (
        "client.form_submitted",
        "When a client submits a form",
        "document_request.form_submitted",
        "documents",
        "contact",
    ),
    (
        "contact.created",
        "When a client record is created",
        "client.created",
        "clients",
        "contact",
    ),
    (
        "contact.login_linked",
        "When a client login is linked",
        "client.portal_user_linked",
        "clients",
        "contact",
    ),
    (
        "contact.adviser_assigned",
        "When an adviser is assigned",
        "client.adviser_assigned",
        "clients",
        "contact",
    ),
    (
        "contact.bank_details_changed",
        "When a client's bank details change",
        "client.bank_account_updated",
        "clients",
        "contact",
    ),
    (
        "account.model_changed",
        "When an account's model changes",
        "account.model_updated",
        "accounts",
        "account",
    ),
    (
        "instruction.created",
        "When an instruction is raised",
        "instruction.created",
        "clients",
        "contact",
    ),
    (
        "instruction.cancelled",
        "When an instruction is cancelled",
        "instruction.cancelled",
        "clients",
        "contact",
    ),
    ("task.completed", "When a task is completed", "task.completed", "tasks", "task"),
    (
        "application.started",
        "When an application starts",
        "journey.started",
        "clients",
        "application",
    ),
)


# Offered triggers whose CLIENT audience ships ON by default — genuine client-facing notices
# (confirmations/transparency), with client-facing copy. Everything else stays offered-but-off.
_CLIENT_DEFAULTS: dict[str, dict[str, str]] = {
    "contact.bank_details_changed": {
        "title": "Your bank details were updated",
        "body": "We've updated the bank details on your account. If this wasn't you, "
        "please contact your adviser.",
        # `/profile`, not `/account`: the client portal names no `/account` route, so this sent
        # every "your bank details changed" notice to a 404 -- the same mistake, and the same
        # 404, that `/adviser/messages` was already fixed for two entries down. The client
        # portal has no bank-details view at all outside onboarding, so `/profile` (their own
        # details) is the closest thing the notice can honestly point at.
        "link": "/profile",
    },
    "contact.adviser_assigned": {
        "title": "Your adviser has been updated",
        "body": "Your adviser has changed. Open your portal to see who's looking after you.",
        "link": "/",
    },
    "account.model_changed": {
        "title": "Your investment strategy changed",
        "body": "The investment model on one of your accounts has changed. "
        "Open your portal to view the details.",
        "link": "/accounts",
    },
}


# Offered triggers whose ADVISER audience ships ON by default — a firm's staff hear about their
# clients' key events (the staff mirror of _CLIENT_DEFAULTS), with staff-facing copy. No link: the
# consumer deep-links a staff notice to the subject's record (consumer._click_link). Everything else
# stays offered-but-off.
# Staff copy NAMES the client (and account) via the trigger's already-resolving merge fields —
# {client.name}, {account.name} — so an adviser can tell WHICH client/account without opening it.
# ``params_for`` proves these resolve for each trigger's subject; an unresolved token drops out.
_STAFF_DEFAULTS: dict[str, dict[str, str]] = {
    "contact.bank_details_changed": {
        "title": "Bank details changed for {client.name}",
        "body": "{client.name}'s bank details were updated.",
    },
    "contact.adviser_assigned": {
        "title": "You were assigned {client.name}",
        "body": "{client.name} was added to your book.",
    },
    "client.document_uploaded": {
        "title": "{client.name} uploaded a document",
        "body": "{client.name} uploaded a document you requested.",
    },
    "client.form_submitted": {
        "title": "{client.name} submitted a form",
        "body": "{client.name} completed a form you requested.",
    },
    "account.model_changed": {
        "title": "Investment model changed for {client.name}",
        "body": "The investment model on {client.name}'s {account.name} account changed.",
    },
}


def _offered_audiences(key: str, label: str, subject_kind: str) -> tuple[Audience, ...]:
    """The audiences an offered trigger exposes, by its subject kind. Audiences are offered-only
    (``builtin=False`` — the trigger fires only once a firm authors a rule) EXCEPT a client in
    ``_CLIENT_DEFAULTS`` and an adviser in ``_STAFF_DEFAULTS``, which ship on with their own copy.
    A task offers its assignee; a contact/account the client + adviser; an application the actor."""
    what = label[5:] if label.startswith("When ") else label  # "a client uploads a document"
    actor = Audience(
        key="actor",
        label="Whoever did it",
        resolve=_resolve_actor,
        default_title=label,
        default_body=f"You performed an action: {what}.",
        default_in_app=False,
        builtin=False,
    )
    if subject_kind == "task":
        return (
            Audience(
                key="assignee",
                label="The assignee",
                resolve=_resolve_task_assignee,
                default_title=label,
                default_body=f"An update on your task: {what}.",
                default_link="/tasks/{task.uid}",
                default_in_app=False,
                builtin=False,
            ),
            actor,
        )
    if subject_kind == "application":
        return (actor,)
    # contact / account
    cd = _CLIENT_DEFAULTS.get(key)
    client = Audience(
        key="client",
        label="The client",
        resolve=_resolve_client,
        default_title=cd["title"] if cd else label,
        default_body=cd["body"]
        if cd
        else "There's an update on your account. Open your portal for details.",
        default_link=cd.get("link") if cd else None,
        default_in_app=bool(cd),
        builtin=bool(cd),
    )
    sd = _STAFF_DEFAULTS.get(key)
    return (
        client,
        Audience(
            key="adviser",
            label="The client's adviser",
            resolve=_resolve_adviser,
            default_title=sd["title"] if sd else label,
            default_body=sd["body"] if sd else f"An update on your client: {what}.",
            default_in_app=bool(sd),
            builtin=bool(sd),
        ),
        actor,
    )


CATALOGUE: list[Trigger] = [
    *_BUILTIN,
    *(
        Trigger(
            key=key,
            label=label,
            description=label,
            event_types=(event_type,),
            category=category,
            audiences=_offered_audiences(key, label, subject_kind),
            subject_kind=subject_kind,
        )
        for key, label, event_type, category, subject_kind in _OFFERED
    ),
]


def client_builtins() -> list[tuple[Trigger, Audience]]:
    """The client-facing built-in notifications — the (trigger, client Audience) pairs a client can
    set a per-notification preference for. Drives the client preferences surface (grouped by the
    trigger's category). A category with none of these never appears on the client page."""
    return [(t, a) for t in CATALOGUE for a in t.audiences if a.key == "client" and a.builtin]


def staff_builtins() -> list[tuple[Trigger, Audience]]:
    """The staff-facing built-in notifications — the (trigger, adviser/assignee Audience) pairs a
    firm's staff can set a per-notification preference for. The mirror of ``client_builtins`` for
    the platform portal; drives the adviser preferences surface (grouped by category)."""
    return [
        (t, a)
        for t in CATALOGUE
        for a in t.audiences
        if a.key in ("adviser", "assignee", "reviewer", "proposer") and a.builtin
    ]


# Fast lookup: event_type -> [(trigger, audience), …], built once.
_BY_EVENT: dict[str, list[tuple[Trigger, Audience]]] = {}
for _trigger in CATALOGUE:
    for _et in _trigger.event_types:
        for _aud in _trigger.audiences:
            _BY_EVENT.setdefault(_et, []).append((_trigger, _aud))


def defaults_for(event_type: str) -> list[tuple[Trigger, Audience]]:
    """The built-in (trigger, audience) pairs a raw event fires."""
    return _BY_EVENT.get(event_type, [])


def trigger(key: str) -> Trigger | None:
    return next((t for t in CATALOGUE if t.key == key), None)


# Audience → the portal the notification row records (client-facing vs staff).
def audience_portal(audience_key: str) -> str:
    return "client" if audience_key == "client" else "platform"


_ = registry  # keep the import meaningful for readers: resolvers come from the registry
