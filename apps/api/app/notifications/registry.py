"""The notification rule registry — the single source of truth for what notifies whom.

Two declarations, the automations/permissions idiom (one place, pinned by a test):

* ``CATEGORIES`` — the human-named groups a user sets preferences against. The raw
  ``audit.actions`` event vocabulary is deliberately NOT exposed to users; they pick categories.
* ``RULES`` — how a tailed ``audit.actions`` row becomes notifications: which event types it
  matches, the recipients it resolves (from the row's allowlisted ``metadata.subject`` — see
  ``app/audit_actions.py::SUBJECT_FIELDS``), and the static copy/link.

Worker-side transitions the tail cannot see (a journey blocking, an AML hit) do NOT live here —
they call ``service.notify`` directly with their own copy. This module is the request-path fan-out
only.

Recipient resolvers take the audit row and return concrete ``Recipient``s. They resolve against the
BOOK tenant (``target_tenant_id or tenant_id``) — a proxied ops action names the firm it acted on,
and the notification belongs to that firm's people, not Graphene's.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class Recipient:
    """One concrete person a notification is addressed to (read-state is per person)."""

    kind: str  # 'user' | 'contact'
    user_id: str | None = None
    contact_id: int | None = None

    def key(self) -> str:
        return f"u:{self.user_id}" if self.kind == "user" else f"c:{self.contact_id}"


@dataclass(frozen=True)
class Category:
    """A preference group. ``portals`` are the audiences that can receive this category — the
    preferences surface shows a user only the categories their portal can receive."""

    key: str
    label: str
    description: str
    portals: tuple[str, ...]
    default_in_app: bool = True
    default_email: bool = False  # the email channel is inert in v1 (service seam)


# The categories that ACTUALLY notify — one per live rule below, nothing aspirational. A category
# earns its place by having a rule that fires off a real event on the bus (docs/notifications.md);
# add one here only alongside the rule that produces it. (Journeys/AML/contributions are not here:
# their "needs attention" moments already reach the bus as tasks, so they need no category.)
CATEGORIES: dict[str, Category] = {
    "tasks": Category(
        "tasks", "Tasks", "A task is assigned to you or is due soon.", ("platform", "client")
    ),
    "documents": Category(
        "documents",
        "Documents",
        "A document is shared with you or a request needs action.",
        ("client",),
    ),
    # Staff-facing groups for the expanded trigger catalogue. Client-facing categories keep 'client'
    # in portals so clients can toggle them; these are book-activity notices for the firm's people.
    "clients": Category(
        "clients",
        "Clients",
        "A change to a client record — created, linked, adviser assigned, bank, instructions.",
        ("platform",),
    ),
    "accounts": Category(
        "accounts",
        "Accounts",
        "A change to an account, such as its investment model.",
        ("platform",),
    ),
    # Client<->adviser chat. Both audiences: the adviser is notified of a client's message, the
    # client of their adviser's — so portals carries both, and config.py surfaces it to each side.
    "messages": Category(
        "messages",
        "Messages",
        "A new message in a client and adviser chat.",
        ("platform", "client"),
    ),
    "reviews": Category(
        "reviews",
        "Peer review",
        "A change is waiting for your review, or a decision was made on one you submitted.",
        ("platform",),
    ),
}


def default_channels(category: str) -> dict[str, bool]:
    cat = CATEGORIES.get(category)
    if cat is None:
        return {"in_app": True, "email": False}
    return {"in_app": cat.default_in_app, "email": cat.default_email}


# ---- recipient resolvers ---------------------------------------------------------------------


def _subject(action: dict[str, Any]) -> dict[str, Any]:
    meta = action.get("metadata") or {}
    subj = meta.get("subject") if isinstance(meta, dict) else None
    return subj if isinstance(subj, dict) else {}


def book_tenant_id(action: dict[str, Any]) -> int:
    """The firm the action is ABOUT — the proxied ops target if present, else the actor's tenant."""
    return int(action.get("target_tenant_id") or action["tenant_id"])


async def role_member_ids(
    session: AsyncSession, tenant_id: int, slug: str, portal: str
) -> list[str]:
    """Active members holding ``slug`` in ``portal`` for ``tenant_id`` — as user ids. Mirrors the
    task notifier's role fan-out (``app/tasks/notifier.py``) but returns ids, not emails."""
    rows = (
        await session.execute(
            text(
                "SELECT u.id::text FROM auth.users u "
                "JOIN auth.organisation_memberships m ON m.user_id = u.id "
                "JOIN auth.membership_roles mr ON mr.membership_id = m.id "
                "JOIN auth.roles r ON r.id = mr.role_id "
                "WHERE m.tenant_id = :t AND m.portal = :p AND m.status = 'active' "
                "  AND r.slug = :s AND u.enabled"
            ),
            {"t": tenant_id, "p": portal, "s": slug},
        )
    ).scalars()
    return [r for r in rows if r]


async def _resolve_document_contacts(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> list[Recipient]:
    """Send carries ``contact_ids`` (a list); a document request carries a single ``contact_id``."""
    subj = _subject(action)
    ids: list[Any] = []
    raw = subj.get("contact_ids")
    if isinstance(raw, list):
        ids.extend(raw)
    one = subj.get("contact_id")
    if one is not None:
        ids.append(one)
    out: list[Recipient] = []
    for cid in ids:
        try:
            out.append(Recipient(kind="contact", contact_id=int(cid)))
        except (TypeError, ValueError):
            continue
    return out


#: The group document-request event. Unlike the single request (which names its contact on the
#: allowlisted subject), this ONE audit action fans out per member below the tail's view — so a
#: resolver has to expand it from the group_id on the route path, or neither consumer notifies a
#: single member. This is the event that closes the "group requests send no email" gap.
GROUP_REQUEST_EVENT = "message_group.document_requested"


async def _resolve_group_members(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> list[Recipient]:
    """Every member of the group a ``messaging-group-create-request`` fanned out to — read from the
    ``group_id`` on the route path (the action carries no contact ids). Each member got their own
    request row, so each is a recipient exactly as a single request's contact is."""
    path_params = action.get("path_params")
    if not isinstance(path_params, dict):
        return []
    try:
        group_id = int(path_params.get("group_id"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return []
    rows = (
        await session.execute(
            text(
                "SELECT contact_id FROM messaging.group_members "
                "WHERE group_id = :g AND tenant_id = :t"
            ),
            {"g": group_id, "t": tenant_id},
        )
    ).scalars()
    return [Recipient(kind="contact", contact_id=int(c)) for c in rows if c is not None]


async def _resolve_request_contacts(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> list[Recipient]:
    """The client(s) a document request concerns — one resolver so the ``document.request`` trigger
    fires for BOTH entry paths: a single request names its contact directly, a group request fans
    out to every member. Keeps the request notice identical however it was raised."""
    if str(action.get("event_type")) == GROUP_REQUEST_EVENT:
        return await _resolve_group_members(session, action, tenant_id)
    return await _resolve_document_contacts(session, action, tenant_id)


async def _resolve_task_assignee(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> list[Recipient]:
    """``tasks-create-task`` carries the assignee (kind + user id / role SLUG / contact id)."""
    subj = _subject(action)
    kind = subj.get("assignee_kind")
    if kind == "user" and subj.get("assignee_user_id"):
        return [Recipient(kind="user", user_id=str(subj["assignee_user_id"]))]
    if kind == "contact" and subj.get("assignee_contact_id") is not None:
        try:
            return [Recipient(kind="contact", contact_id=int(subj["assignee_contact_id"]))]
        except (TypeError, ValueError):
            return []
    if kind == "role" and subj.get("assignee_role_slug"):
        ids = await role_member_ids(session, tenant_id, str(subj["assignee_role_slug"]), "platform")
        return [Recipient(kind="user", user_id=uid) for uid in ids]
    return []


async def _resolve_review_role(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> list[Recipient]:
    """Members of the approver roles snapshotted onto a ``review.requested`` event — every role
    the action names (``reviewer_role_slugs``), or the one older events carried. Each person once,
    however many of those roles they hold; the person who raised it is not an approver of it."""
    subj = _subject(action)
    slugs: list[str] = []
    listed = subj.get("reviewer_role_slugs")
    if isinstance(listed, list):
        slugs.extend(str(slug) for slug in listed if slug)
    single = subj.get("reviewer_role_slug")
    if single and str(single) not in slugs:
        slugs.append(str(single))
    if not slugs:
        return []
    proposer = str(subj.get("proposed_by") or "")
    seen: set[str] = set()
    out: list[Recipient] = []
    for slug in slugs:
        for uid in await role_member_ids(session, tenant_id, slug, "platform"):
            if uid in seen or uid == proposer:
                continue
            seen.add(uid)
            out.append(Recipient(kind="user", user_id=uid))
    return out


async def _resolve_review_proposer(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> list[Recipient]:
    """The person who submitted the held change — from the subject or the review row."""
    subj = _subject(action)
    proposed = subj.get("proposed_by")
    if proposed:
        return [Recipient(kind="user", user_id=str(proposed))]
    path = action.get("path_params") if isinstance(action.get("path_params"), dict) else {}
    uid = subj.get("review_uid") or (path or {}).get("uid")
    if not uid:
        return []
    row = (
        await session.execute(
            text("SELECT requested_by::text FROM baton.runs WHERE uid = :u AND tenant_id = :t"),
            {"u": str(uid), "t": tenant_id},
        )
    ).scalar_one_or_none()
    if row is None:
        return []
    return [Recipient(kind="user", user_id=str(row))]


# ---- rules -----------------------------------------------------------------------------------

Resolver = Callable[[AsyncSession, dict[str, Any], int], Awaitable[list[Recipient]]]


#: How loudly a notification presents. Enforced by a CHECK on both `notifications.notifications`
#: and `notifications.rules`, so a value outside this set is a Postgres constraint violation — a
#: 500 naming a column — where the admin API takes it from a request BODY and should answer 422
#: naming the field. Declared here because it is notification vocabulary; pinned to the migrations'
#: CHECK by `tests/test_notifications.py`.
Priority = Literal["low", "normal", "high"]


@dataclass(frozen=True)
class NotificationRule:
    match: tuple[str, ...]  # audit.actions event_type(s), exact
    category: str
    portal: str  # the recipients' portal (client|platform|operations)
    title: str
    resolve: Resolver
    body: str | None = None
    link: str | None = None
    priority: Priority = "normal"
    subject_type: str | None = None  # optional, for the notification's subject pair
    subject_field: str | None = None  # which metadata.subject key holds subject_id


RULES: list[NotificationRule] = [
    # Documents — client-facing. The audit row names the contact(s); the item links to the client
    # portal's documents section (the audit row does not carry the document's own id).
    NotificationRule(
        match=("document.sent",),
        category="documents",
        portal="client",
        title="A document was shared with you",
        body="Your adviser has shared a document. Open your portal to view it.",
        link="/documents",
        resolve=_resolve_document_contacts,
    ),
    NotificationRule(
        match=("document_request.created",),
        category="documents",
        portal="client",
        title="Action needed: a document request",
        body="Your adviser has requested something from you. Open your portal to respond.",
        link="/documents",
        priority="high",
        resolve=_resolve_document_contacts,
    ),
    # Tasks — a manual assignment, already on the bus as an action event. We do NOT manufacture an
    # event for flow-raised tasks: notifications only play off events that genuinely land on the
    # stream (docs/notifications.md). Flow/AML/journey "needs attention" reaches the bus as its own
    # task-created event when the flow raises a task.
    NotificationRule(
        match=("task.created",),
        category="tasks",
        portal="platform",
        title="A task was assigned to you",
        body="A new task is waiting in your task inbox.",
        link="/tasks",
        resolve=_resolve_task_assignee,
    ),
]

# Fast index for the consumer: event_type -> rules, built once.
_BY_EVENT: dict[str, list[NotificationRule]] = {}
for _rule in RULES:
    for _et in _rule.match:
        _BY_EVENT.setdefault(_et, []).append(_rule)


def rules_for(event_type: str) -> list[NotificationRule]:
    return _BY_EVENT.get(event_type, [])
