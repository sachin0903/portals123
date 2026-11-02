"""Merge context for notification copy — the values a rule's ``{placeholders}`` render against.

The BASE is built once per ``audit.actions`` row (:func:`build_context`): it describes the event's
SUBJECT and ACTOR, so staff copy like ``"{client.name} uploaded a document"`` names the client the
notice is *about*, whoever receives it. The CLIENT slice is then overridden PER RECIPIENT
(:func:`context_for_recipient`) — a trigger can fan out to several contacts (a document sent to many
clients), and each must see its OWN ``{client.name}``, never the first contact's. Keys are dotted
(``{client.name}``, ``{account.number}``); :mod:`app.notifications.render` does the substitution.

Every lookup is DEFENSIVE — an unresolved value is simply absent (``render`` then drops the token),
never an error: a name we cannot read must never stall the estate's tail. The subject is resolved
through the automations resolver (``catalogue._subject_of``), with a direct fall-back to the
action's allowlisted ``metadata.subject`` for the events that resolver does not name — the document
events, and the worker-emitted task notices, which sit on no route at all.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit_actions as actions
from app.notifications.catalogue import (
    DOCUMENT_PATH_EVENTS,
    DOCUMENT_RESOLVED_EVENTS,
    _subject_contact_id,
    _subject_of,
)
from app.notifications.registry import Recipient, _subject
from app.render.jinja_engine import _fmt_currency

logger = logging.getLogger("notifications")

# The document-naming events live in ``catalogue.DOCUMENT_PATH_EVENTS`` (one source of truth, shared
# with ``link_targets_for``). The id is a PATH param, so it rides ``audit.actions.path_params`` —
# NOT ``metadata.subject``, which ``extract_subject`` builds from the body/query only.


#: EVERY FIELD `build_context` CAN PRODUCE, by dotted path, grouped by the SUBJECT KIND that has to
#: be in play for it to resolve. `None` means "always available" — it does not depend on the
#: subject.
#:
#: This is the authoring vocabulary: a notification rule's own title, message and link may name any
#: of these as a `{dotted.key}` placeholder, and the email editor's context panel lists exactly this
#: set for whatever the email says it is about (mirrored in
#: `pkgs/ui/src/lib/components/email-editor/scopes.ts`, pinned by `scope-parity.test.ts`). It is
#: written out rather than derived because the queries above are hand-written SQL — deriving it
#: would mean parsing them.
SCOPE_FIELDS: dict[str | None, tuple[str, ...]] = {
    None: (
        "firm.name",
        "firm.phone",
        "actor.name",
        "actor.email",
        "event.when",
    ),
    "contact": (
        "client.name",
        "client.first_name",
        "adviser.name",
        "adviser.email",
    ),
    "account": (
        "account.name",
        "account.number",
        "account.product",
        "account.status",
        "account.value",
        "account.currency",
        "client.name",
        "client.first_name",
        "adviser.name",
        "adviser.email",
    ),
    "task": (
        "task.title",
        "task.description",
        "task.due_date",
    ),
    "document": (
        "document.name",
        "document.type",
        "client.name",
        "client.first_name",
        "adviser.name",
        "adviser.email",
    ),
}

#: Flattened, for the tests that hold the whole vocabulary to the SHAPE `render._lookup` walks
#: (dotted, lowercase, two segments). The per-subject mapping above is what every reader consults.
KNOWN_SCOPE_FIELDS: frozenset[str] = frozenset(f for fs in SCOPE_FIELDS.values() for f in fs)


#: Currency symbols for the codes a UK firm's book actually holds. An unknown code falls back to the
#: code itself ("CHF 1,234.56") rather than a guessed symbol — wrong money beats verbose money.
_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$"}


def _symbol(currency: object) -> str:
    code = str(currency or "GBP").strip().upper()
    return _SYMBOLS.get(code, f"{code} " if code else "£")


def _document_uid(action: dict[str, Any]) -> str | None:
    key = DOCUMENT_PATH_EVENTS.get(str(action.get("event_type")))
    if not key:
        return None
    path_params = action.get("path_params")
    if not isinstance(path_params, dict):
        return None
    value = path_params.get(key)
    return str(value) if value else None


async def _resolved_document_uid(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> str | None:
    """A document uid resolved INDIRECTLY, for events that don't name it on the path — today the
    client request-item upload (``DOCUMENT_RESOLVED_EVENTS``), whose created document is linked to
    the item via ``request_items.fulfilled_document_id``. Looks it up from the ``item_uid`` on the
    event so the adviser's "a client uploaded a document" notice can deep-link to the upload."""
    if str(action.get("event_type")) not in DOCUMENT_RESOLVED_EVENTS:
        return None
    path_params = action.get("path_params")
    if not isinstance(path_params, dict):
        return None
    item_uid = path_params.get("item_uid")
    if not item_uid:
        return None
    return await _scalar(
        session,
        "SELECT d.uid FROM documents.request_items i "
        "JOIN documents.documents d ON d.id = i.fulfilled_document_id "
        "WHERE i.uid = :iu AND i.tenant_id = :t",
        {"iu": str(item_uid), "t": tenant_id},
    )


async def _scalar(session: AsyncSession, sql: str, params: dict[str, Any]) -> str | None:
    """One name lookup, tidied to a non-empty string or None. Swallows any error (a missing name is
    not a reason to fail the tail)."""
    try:
        value = (await session.execute(text(sql), params)).scalar_one_or_none()
    except Exception:  # noqa: BLE001 — a merge-field lookup must never stall the consumer
        return None
    s = str(value).strip() if value is not None else ""
    return s or None


def _event_when(created_at: Any) -> str | None:
    """A readable date for ``{event.when}`` from the action's ``created_at`` (a datetime from the
    consumer's SELECT, or an ISO string in a test). No leading zero on the day, cross-platform."""
    if created_at is None:
        return None
    try:
        dt = (
            created_at
            if isinstance(created_at, datetime)
            else datetime.fromisoformat(str(created_at))
        )
    except (TypeError, ValueError):
        return None
    return f"{dt.day} {dt:%b %Y}"


async def contact_name(session: AsyncSession, contact_id: int, tenant_id: int) -> str | None:
    """A client contact's display name (full name, else first + surname), or None."""
    return await _scalar(
        session,
        "SELECT COALESCE("
        "  NULLIF(name, ''), "
        "  NULLIF(trim(coalesce(first_name, '') || ' ' || coalesce(surname, '')), '')) "
        "FROM crm.contacts WHERE id = :c AND tenant_id = :t",
        {"c": contact_id, "t": tenant_id},
    )


async def client_block(session: AsyncSession, contact_id: int, tenant_id: int) -> dict[str, str]:
    """The whole ``client`` scope for one contact.

    ONE FUNCTION BECAUSE THE PER-RECIPIENT OVERRIDE READS IT TOO, and that is the fix for a real
    disclosure. ``context_for_recipient`` used to restate a single key — ``name`` — so
    ``first_name``,
    added later beside it, kept the SUBJECT contact's value for every other recipient of a fan-out:
    a
    document sent to Priya and Tom mailed Tom "Dear Tom Blake … Hi Priya". Any field added here is
    per-recipient by construction; a field added to a literal dict at the call site is not.

    Empty when the contact reads as nothing, which both callers treat as "leave the context alone".
    """
    block: dict[str, str] = {}
    name = await contact_name(session, contact_id, tenant_id)
    if name:
        block["name"] = name
    # FIRST NAME as its own field. `{client.name}` is the full name, so "Hi Priya" could not be
    # written at all — a rule had to say "Hi Priya Shah," or nothing.
    first = await _scalar(
        session,
        "SELECT NULLIF(trim(first_name), '') FROM crm.contacts WHERE id = :c AND tenant_id = :t",
        {"c": contact_id, "t": tenant_id},
    )
    if first:
        block["first_name"] = first
    return block


#: The worker-emitted task notices (``app/tasks/notifier.py``). They are on the stream but on no
#: route, so the flows trigger registry — which knows only what a ROUTE emits — names no subject for
#: them.
_TASK_SUBJECT_EVENTS: frozenset[str] = frozenset(
    {actions.TASK_ASSIGNED_EVENT, actions.TASK_DUE_SOON_EVENT}
)

_REVIEW_EVENTS: frozenset[str] = frozenset(
    {
        "review.requested",
        "review.approved",
        "review.rejected",
        "review.cancelled",
        "review.conflicted",
    }
)


def _fallback_task_subject(action: dict[str, Any]) -> tuple[str | None, int | None]:
    """The task a worker-emitted notice is about, from its own ``metadata.subject``.

    Same precedent as :func:`_fallback_contact_id`: where ``_subject_of`` names nothing, read the id
    the PRODUCER put on the event. Without it ``{task.title}`` renders blank on every task notice.
    """
    if str(action.get("event_type")) not in _TASK_SUBJECT_EVENTS:
        return None, None
    raw = _subject(action).get("task_id")
    try:
        return ("task", int(raw)) if raw is not None else (None, None)
    except (TypeError, ValueError):
        return None, None


def _review_uid(action: dict[str, Any]) -> str | None:
    """The held request a review-lifecycle event is about."""
    if str(action.get("event_type")) not in _REVIEW_EVENTS:
        return None
    raw = _subject(action).get("review_uid")
    if raw:
        return str(raw)
    path = action.get("path_params")
    if isinstance(path, dict) and path.get("uid"):
        return str(path["uid"])
    return None


def _fallback_contact_id(action: dict[str, Any]) -> int | None:
    """A contact id straight from the action's allowlisted subject — the document send/request
    events name the contact directly (``contact_id`` / ``contact_ids``) where the generic subject
    resolver does not."""
    subj = _subject(action)
    raw = subj.get("contact_id")
    if raw is None:
        ids = subj.get("contact_ids")
        if isinstance(ids, list) and ids:
            raw = ids[0]
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


async def build_context(
    session: AsyncSession, action: dict[str, Any], tenant_id: int
) -> dict[str, dict[str, str]]:
    """The nested merge context for one event: ``{firm, actor, client, account, task}`` — each key
    present only when resolved. Cheap (a few keyed lookups); the caller builds it once per action
    and only when a rule's copy actually carries a placeholder."""
    ctx: dict[str, dict[str, str]] = {}

    firm_row = (
        (
            await session.execute(
                text(
                    "SELECT name, NULLIF(trim(coalesce(phone, '')), '') AS phone "
                    "FROM auth.tenants WHERE id = :t"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if firm_row is not None:
        firm = {k: str(v) for k, v in firm_row.items() if v}
        if firm:
            ctx["firm"] = firm

    actor_id = action.get("actor_id")
    if actor_id:
        # Name AND email in ONE round trip (the name's COALESCE already reads email) — this runs per
        # notification event in the worker's tail, so two separate SELECTs on the same id were pure
        # waste.
        actor_row = (
            (
                await session.execute(
                    text(
                        "SELECT COALESCE("
                        "  NULLIF(name, ''), "
                        "  NULLIF(trim(coalesce(first_name, '') || ' ' "
                        "                || coalesce(last_name, '')), ''), "
                        "  email) AS name, "
                        "  NULLIF(email, '') AS email "
                        "FROM auth.users WHERE id = :u"
                    ),
                    {"u": str(actor_id)},
                )
            )
            .mappings()
            .one_or_none()
        )
        actor: dict[str, str] = {}
        if actor_row:
            if actor_row["name"]:
                actor["name"] = actor_row["name"]
            if actor_row["email"]:
                actor["email"] = actor_row["email"]
        if actor:
            ctx["actor"] = actor

    # When it happened — universal, from the action row itself (the consumer selects created_at).
    when = _event_when(action.get("created_at"))
    if when:
        ctx["event"] = {"when": when}

    kind, sid = await _subject_of(session, action, tenant_id)
    if kind is None:
        kind, sid = _fallback_task_subject(action)

    # The client the event concerns: a contact subject IS one, an account subject resolves to its
    # owner; failing both, a contact named directly in the subject metadata (document events).
    contact_id = await _subject_contact_id(session, kind, sid, tenant_id)
    if contact_id is None:
        contact_id = _fallback_contact_id(action)
    if contact_id is not None:
        # uid drives a click DESTINATION (/clients/{contact.uid}), kept separate from `client`
        # (whose name is overridden PER RECIPIENT); the subject contact id must stay the
        # event's, not the recipient's, so it lives on its own stable key. `id` is still
        # published for rules authored against the old token.
        contact_uid = (
            await session.execute(
                text("SELECT uid FROM crm.contacts WHERE id = :c AND tenant_id = :t"),
                {"c": contact_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
        ctx["contact"] = {"id": str(contact_id)}
        if contact_uid:
            ctx["contact"]["uid"] = str(contact_uid)
        client = await client_block(session, contact_id, tenant_id)
        if client:
            ctx["client"] = client
        # The client's book adviser — for client-facing copy that names who looks after them
        # ("your adviser {adviser.name}"). `role = 'adviser'` is unique per contact (0245), so no
        # tie-break ordering is needed.
        # Name AND how to reach them: "call Tom on 0131…" is the most-wanted line in a client email,
        # and the name alone could not write it.
        adviser_row = (
            (
                await session.execute(
                    text(
                        "SELECT COALESCE("
                        "  NULLIF(u.name, ''), "
                        "  NULLIF(trim(coalesce(u.first_name, '') || ' ' || "
                        "    coalesce(u.last_name, '')), ''), "
                        "  u.email) AS name, "
                        # No phone on `auth.users`: staff numbers are not held, so "call Tom on…"
                        # is written with `{firm.phone}` instead.
                        "  NULLIF(u.email, '') AS email "
                        "FROM crm.client_advisers ca JOIN auth.users u ON u.id = ca.adviser_id "
                        "WHERE ca.tenant_id = :t AND ca.contact_id = :c AND u.enabled "
                        "  AND ca.role = 'adviser'"
                    ),
                    {"t": tenant_id, "c": contact_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if adviser_row is not None:
            adviser = {k: str(v) for k, v in adviser_row.items() if v}
            if adviser:
                ctx["adviser"] = adviser

    if kind == "account" and sid is not None:
        row = (
            (
                await session.execute(
                    # AN ALLOW LIST, NOT `SELECT *`. A column added to `platform.accounts` next
                    # month must not reach a client's email by default, so the fields a notice may
                    # mention are named here — the one place that judgement is recorded.
                    text(
                        "SELECT a.name, a.uid, "
                        "  COALESCE(NULLIF(a.platform_identifier, ''), a.uid) AS number, "
                        "  a.value, a.currency, a.status::text AS status, "
                        "  NULLIF(p.name, '') AS product "
                        "FROM platform.accounts a "
                        "LEFT JOIN platform.products p ON p.id = a.product_id "
                        "WHERE a.id = :i AND a.tenant_id = :t"
                    ),
                    {"i": sid, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            # uid drives a click DESTINATION (/accounts/{account.uid}) -- the portal route is
            # uid-addressed, so a serial there lands on nothing. `id` is still published
            # because rules authored before the switch stored `{account.id}`, and a token
            # that resolves to nothing renders the literal brace text in a client's email.
            account: dict[str, str] = {"id": str(sid), "uid": str(row["uid"])}
            for key in ("name", "number", "product", "status"):
                if row[key]:
                    account[key] = str(row[key])
            # FORMATTED HERE, not by the template: stage-A substitution is deliberately not a
            # template engine (no filters), so a raw numeric would read "184230.55" in a client's
            # email. `_fmt_currency` is the formatter the Jinja `| currency` filter uses.
            if row["value"] is not None:
                account["value"] = _fmt_currency(row["value"], _symbol(row["currency"]))
            if row["currency"]:
                account["currency"] = str(row["currency"])
            ctx["account"] = account

    if kind == "task" and sid is not None:
        # One row for the task's copy fields: title + description, its due date (pretty-formatted,
        # like the email), and uid (drives the /tasks/{task.uid} click destination).
        row = (
            (
                await session.execute(
                    text(
                        "SELECT title, uid, description, "
                        "       to_char(due_date, 'FMDD Mon YYYY') AS due_date "
                        "FROM tasks.tasks WHERE id = :i"
                    ),
                    {"i": sid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            task: dict[str, str] = {}
            for key in ("title", "uid", "description", "due_date"):
                val = row[key]
                if val:
                    task[key] = str(val)
            if task:
                ctx["task"] = task

    doc_uid = _document_uid(action) or await _resolved_document_uid(session, action, tenant_id)
    if doc_uid:
        # uid drives a click DESTINATION (/documents/{document.uid}); name drives copy. uid is set
        # even when the name can't be read, so a "link to the document" target still resolves.
        document: dict[str, str] = {"uid": doc_uid}
        row = (
            (
                await session.execute(
                    text(
                        "SELECT name, document_type FROM documents.documents "
                        "WHERE uid = :u AND tenant_id = :t"
                    ),
                    {"u": doc_uid, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            if row["name"]:
                document["name"] = str(row["name"])
            if row["document_type"]:
                document["type"] = str(row["document_type"])
        ctx["document"] = document

    review_uid = _review_uid(action)
    if review_uid:
        from app.reviews.catalogue import get as review_action

        action_key = _subject(action).get("action_key")
        stored = (
            (
                await session.execute(
                    text(
                        "SELECT action_key, case_no FROM baton.runs "
                        "WHERE uid = :u AND tenant_id = :t"
                    ),
                    {"u": review_uid, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if not action_key and stored is not None:
            action_key = stored["action_key"]
        spec = review_action(str(action_key)) if action_key else None
        review: dict[str, str] = {"uid": review_uid}
        if spec is not None:
            review["label"] = spec.label
        elif action_key:
            # A held PROVIDER action (a payment, a transfer) is named from the actions catalogue,
            # so the notice says "Payment", not "transaction.payment".
            from app.actions.queries import action_name

            review["label"] = action_name(str(action_key))
        case_no = stored["case_no"] if stored is not None else None
        if case_no is not None:
            review["case"] = f"RV-{int(case_no)}"
        ctx["review"] = review

    return ctx


async def context_for_recipient(
    session: AsyncSession,
    base: dict[str, dict[str, str]],
    recipient: Recipient,
    tenant_id: int,
) -> dict[str, dict[str, str]]:
    """One recipient's view of the merge context.

    A CONTACT recipient IS its own ``{client.name}``: a trigger can fan out to several contacts (a
    document sent to many clients emits one recipient per contact), so a shared context — whose
    ``client`` is only the FIRST fanned-out contact — would print client A's name in client B's own
    notification, a cross-client disclosure. Staff (user) recipients keep the base context, where
    ``client`` is the client the event is *about*. Returns ``base`` unchanged when there is nothing
    to override (a user recipient, or a name we cannot read)."""
    if recipient.contact_id is None:
        return base
    # REPLACED, not merged. Merging kept whatever the subject contact's block held for a field this
    # recipient does not have — which is the disclosure itself, one field at a time. A recipient
    # with
    # no first name on record renders `{client.first_name}` empty (and a template's fallback fires),
    # which is the right answer: better nothing than somebody else's name.
    client = await client_block(session, recipient.contact_id, tenant_id)
    if not client:
        return base
    return {**base, "client": client}
