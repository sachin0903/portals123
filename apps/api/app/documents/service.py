"""The documents service — ONE implementation of the three client-facing document acts.

``generate`` (queue a render), ``send`` (release a document to a client) and ``create_request``
(ask a client for something) each used to exist only as the body of a FastAPI endpoint. They are
here now because an automation must perform them by calling the same function a person's request
calls: the alternative — an executor writing its own INSERTs into ``documents.*`` — is a second
implementation of the business rules, i.e. a check that exists on the human path and not on the
automated one. Same reasoning, same shape, as ``app/tasks/service.py``.

Two adaptations were needed to make one function serve both callers, and they are the only
differences from the extracted router code:

**Tenant and actor are arguments, not ambient.** The request path runs on ``tenant_session``, where
``auth.tenant_id()``/``auth.uid()`` answer for the caller; the engine runs on a ``service_role``
session where both are NULL. So every statement takes an explicit ``tenant_id`` and stamps the
actor as ``COALESCE(:actor, auth.uid())`` — the request path passes no actor and keeps stamping the
signed-in staffer, the engine passes none either and honestly records no human. The explicit
``tenant_id`` predicates are *additional* to RLS, never instead of it: on the request path RLS
still narrows a query to the caller's book, which is why the router keeps its own
``_assert_visible_contact`` check on top of the tenant check here.

**Refusals are ``DocumentServiceError``, not ``HTTPException``.** The router maps them to the 4xx
it always returned; the executor maps them to ``ActionRefused``. A service that raised
``HTTPException`` would be a service only a router can call.

Notification delivery is injected (``notify``) for one reason: ``tests/test_documents_api.py``
monkeypatches the router module's ``queue_client_email`` to capture mail, and a service that
reached for its own module global would silently bypass that. The default is the real thing.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import live_push, messaging
from app.db import system_transaction
from app.document_types import register_document_type
from app.email import transport as email_mod
from app.flows import notifications as flows_notifications
from app.flows import orchestrator as flows_orchestrator
from app.ids import new_id
from app.tasks import service as tasks_service

logger = logging.getLogger(__name__)


class DocumentServiceError(ValueError):
    """A considered refusal: the act cannot be performed as asked (no such template, no valid
    recipient, an item with no target). Carries a reason fit to show the person who asked."""


#: The action a request item may ask a client for (mirrors the ``request_items_action_check``).
REQUEST_ACTIONS = ("upload", "agree", "acknowledge", "complete_form")
#: Actions that act ON an existing document, so they are meaningless without a target.
_TARGETED_ACTIONS = ("agree", "acknowledge")

#: A request item's mirrored Task (docs/tasks.md: "tasks link to [a request] via `subject_type=
#: 'document'` or the description") — ``subject_type='document'`` itself means an actual
#: ``documents.documents`` row (`app/tasks/service.py`'s `_SUBJECT_CHECKS`), never a request, so
#: the link here is `source_kind='document'` + the request ITEM's own uid as `source_ref` (stable,
#: unique per item — the request/group fan-out create one independent request per client, so this
#: never collides across members) plus a plain-English mention in the description.
_TASK_CATEGORY_BY_ACTION = {
    "upload": "upload",
    "agree": "acknowledge",
    "acknowledge": "acknowledge",
    "complete_form": "generic",
}
_TASK_VERB_BY_ACTION = {
    "upload": "upload",
    "agree": "agree to",
    "acknowledge": "acknowledge",
    "complete_form": "complete",
}
#: tasks.tasks.priority has no 'urgent' tier — folds into 'high', the closest it has.
_TASK_PRIORITY = {"low": "low", "normal": "normal", "high": "high", "urgent": "high"}


@dataclass(frozen=True)
class RequestItemSpec:
    """One line item of a document request — what the client is being asked to do."""

    action: str
    title: str
    category: str | None = None
    format_hint: str | None = None
    target_document_uid: str | None = None
    form_uid: str | None = None


@dataclass(frozen=True)
class SendOutcome:
    """What ``send`` did: who gained portal visibility, and who was (not) told about it."""

    sent: int
    notified: int = 0
    not_notified: list[str] = field(default_factory=list)
    recipient_ids: list[int] = field(default_factory=list)
    document_name: str | None = None


#: ``(tenant_id, *, event_type, to, subject, html, sender) -> delivered?``
Notifier = Callable[..., Awaitable[bool]]


async def queue_client_email(
    tenant_id: int,
    *,
    event_type: str,
    to: str,
    subject: str,
    html: str,
    sender: email_mod.Sender,
) -> bool:
    """Queue one client email on ``flows.outbox`` rather than sending it inline: the worker's
    dispatcher delivers ``notify.*`` rows with backoff, retry and a dead-letter, and the request
    path does no third-party network IO. Returns False (logged) when queueing itself failed, so the
    caller reports honestly instead of claiming the client was told.

    NOT transactional with the action it reports, despite the outbox's name. ``authenticated`` has
    no INSERT on ``flows.outbox`` (0025), so this opens its OWN service_role transaction, committing
    on exit — i.e. BEFORE the caller's tenant_session transaction does. If that outer transaction
    then fails (deadlock, serialisation failure, constraint), the row it described never exists but
    the queued notice still goes out: the client is emailed about a request or a send they cannot
    see. Genuinely closing that needs an after-commit hook on the request session (see the handoff
    on ``db.py::tenant_session``) so the enqueue happens once the action is durable.

    A separate consequence of opening its own transaction: it connects via the module-level
    ``app.db.SessionLocal`` (``settings.database_url``), NOT the caller's session — so a test must
    monkeypatch ``system_transaction`` (see the ``outbox_on_container`` fixture) or nothing lands
    anywhere the assertions can see.
    """
    try:
        async with system_transaction() as outbox:
            await flows_notifications.enqueue(
                outbox,
                tenant_id=tenant_id,
                event_type=event_type,
                payload={
                    "to": to,
                    "subject": subject,
                    "html": html,
                    "event_type": event_type,
                    "sender": sender.from_addr,
                    "reply_to": sender.reply_to,
                },
            )
    except Exception:  # noqa: BLE001 — a notification must never fail the action it reports on
        logger.exception("could not queue %s email to %s", event_type, to)
        return False
    return True


async def audit_event(
    session: AsyncSession,
    document_uid: str,
    action: str,
    detail: dict[str, object] | None = None,
    *,
    tenant_id: int | None = None,
    actor_id: str | None = None,
) -> None:
    """Append a product-level event to ``documents.audit_log`` for the document (append-only).

    ``tenant_id``/``actor_id`` fall back to the session's ambient identity, so the request path
    reads exactly as it did; the engine passes its tenant and no actor (there is no human, and
    naming one would be a lie in an append-only log)."""
    await session.execute(
        text(
            "INSERT INTO documents.audit_log (tenant_id, document_id, actor_id, action, detail) "
            "SELECT COALESCE(CAST(:tenant AS int), auth.tenant_id()), d.id, "
            "       COALESCE(CAST(:actor AS uuid), auth.uid()), :action, CAST(:detail AS jsonb) "
            "FROM documents.documents d WHERE d.uid = :uid "
            "  AND (CAST(:tenant AS int) IS NULL OR d.tenant_id = CAST(:tenant AS int))"
        ),
        {
            "tenant": tenant_id,
            "actor": actor_id,
            "action": action,
            "detail": json.dumps(detail) if detail is not None else None,
            "uid": document_uid,
        },
    )


# ---------------------------------------------------------------------------------------------
# generate — queue a render of a published template
# ---------------------------------------------------------------------------------------------


async def generate(
    session: AsyncSession,
    *,
    tenant_id: int,
    template_uid: str,
    answers: dict[str, Any] | None = None,
    name: str | None = None,
    client_contact_id: int | None = None,
    renderer: str | None = None,
    allow_incomplete: bool = False,
    idempotency_key: str | None = None,
    generated_by: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Queue a document for generation from a published template + inputs. Returns its uid.

    Inserts a ``queued`` document — recording the ``inputs``, the template version and (optionally)
    the client it's for — and enqueues a render job, in one transaction. The DocumentWorker renders
    it asynchronously.

    A ``client_contact_id`` FILES the document under that client, which now also makes it visible to
    them (a ``'subject'`` link — see ``link_subject``). ``send`` remains a distinct act: it is what
    DELIVERS the document (a ``'recipient'`` link, the covering note, the notification), and it is
    still the only thing that reports as sent.
    """
    # Idempotency: serialise on (tenant, key) for the rest of this transaction, then look for the
    # document that key already produced. The advisory lock is what makes the check-then-insert
    # safe under a genuine double-submit (two requests racing in parallel), not just a retry.
    if idempotency_key:
        await session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "  hashtextextended(CAST(:t AS text) || ':generate:' || :k, 0))"
            ),
            {"t": tenant_id, "k": idempotency_key},
        )
        existing = (
            await session.execute(
                text(
                    "SELECT uid FROM documents.documents "
                    "WHERE tenant_id = :t AND metadata ->> 'idempotency_key' = :k "
                    "ORDER BY created_at LIMIT 1"
                ),
                {"t": tenant_id, "k": idempotency_key},
            )
        ).scalar_one_or_none()
        if existing is not None:
            return str(existing)

    # Resolve the template's current published version + validate it has render content, so we fail
    # fast rather than enqueue a doomed job. (The worker re-reads the version's schema to render.)
    # An ARCHIVED template is not generatable — it has been withdrawn, so its published version
    # must not keep producing client-facing documents.
    row = (
        (
            await session.execute(
                text(
                    "SELECT t.document_type, t.name AS template_name, v.id AS version_id, "
                    "       v.schema "
                    "FROM documents.templates t "
                    "JOIN documents.template_versions v ON v.id = t.current_published_version_id "
                    "WHERE t.uid = :uid AND t.tenant_id = :t AND t.archived_at IS NULL"
                ),
                {"uid": template_uid, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise DocumentServiceError("Template not found, archived, or has no published version.")
    schema = row["schema"] or {}
    if not (isinstance(schema, dict) and schema.get("html")):
        raise DocumentServiceError(
            "This template version has no rendered content. Open it in the editor, save, "
            "then publish a new version."
        )

    # Completeness gate: refuse to produce a client-facing document whose variables have no source
    # (common context + attached form fields + linked datasets). Hand-typed / unwired tokens render
    # blank, so a firm shouldn't be able to generate a document full of empty figures by accident.
    if not allow_incomplete:
        # Imported here, not at module scope: the lint lives with the template editor that grew it,
        # and importing a router package at import time from a service would invert the layering
        # for a function only this branch needs.
        from app.routers.templates import unresolved_variables

        unresolved = await unresolved_variables(session, template_uid, schema)
        if unresolved:
            shown = ", ".join(u.name for u in unresolved[:8])
            more = f" (+{len(unresolved) - 8} more)" if len(unresolved) > 8 else ""
            raise DocumentServiceError(
                f"This template has {len(unresolved)} variable(s) with no data source, so "
                f"the document would render blank: {shown}{more}. Connect them to the input form "
                "or a linked dataset in the template editor, then try again — or generate it as an "
                "incomplete draft (allow_incomplete)."
            )

    doc_uid = new_id()
    # Inherited from the template; register it so the generated doc's type is a reusable tag too.
    dtype = await register_document_type(session, row["document_type"], tenant_id=tenant_id)
    doc_id = (
        await session.execute(
            text(
                "INSERT INTO documents.documents "
                "  (uid, tenant_id, document_type, source, name, status, inputs, metadata, "
                "   template_version_id, generated_by, intended_contact_id) "
                "VALUES (:uid, :t, :dtype, 'generated', :name, 'queued', "
                "   CAST(:inputs AS jsonb), CAST(:metadata AS jsonb), :vid, "
                "   COALESCE(CAST(:by AS uuid), auth.uid()), :cid) "
                "RETURNING id"
            ),
            {
                "uid": doc_uid,
                "t": tenant_id,
                "dtype": dtype,
                "name": name or row["template_name"],
                "inputs": json.dumps(answers or {}),
                "metadata": json.dumps(
                    {
                        **(metadata or {}),
                        **({"renderer": renderer} if renderer else {}),
                        **({"idempotency_key": idempotency_key} if idempotency_key else {}),
                    }
                ),
                "vid": row["version_id"],
                "by": generated_by,
                "cid": client_contact_id,
            },
        )
    ).scalar_one()

    # Enqueue the render job (drained by the DocumentWorker).
    await session.execute(
        text("INSERT INTO documents.render_jobs (tenant_id, document_id) VALUES (:t, :doc)"),
        {"t": tenant_id, "doc": doc_id},
    )
    await audit_event(
        session,
        doc_uid,
        "document.queued",
        {"template_uid": template_uid},
        tenant_id=tenant_id,
        actor_id=generated_by,
    )
    # NOT linked to the client here. Filing under a client is what makes it theirs to see (no send
    # required -- see link_subject), but this document is `queued` and has not rendered yet: the
    # client branch of `documents_read` carries no status predicate, so linking now would show them
    # an unrendered document, or a permanently failed one. `intended_contact_id` above records who
    # it is for, and `worker._persist` files the link once the render actually succeeds.
    return doc_uid


# ---------------------------------------------------------------------------------------------
# link_subject — make a document filed under a client visible to that client
# ---------------------------------------------------------------------------------------------


#: The link role meaning "this document is FILED UNDER this client", as distinct from
#: ``'recipient'`` ("it was delivered to them"). Both grant the client visibility — the client
#: branch of the ``documents_read`` policy (last written by ``0021_documents_scope``) matches on
#: ``contact_id`` alone, with NO role predicate — but only ``'recipient'`` is read anywhere as
#: evidence of a SEND, and that is why the two must not share a value:
#:
#: * ``routers/documents._SELECT`` computes ``sent_to_client``, ``sent_at`` and the ``recipients``
#:   string by filtering ``role = 'recipient'``. Auto-linking as a recipient would report every
#:   filed document as already sent and list the client among its own recipients — destroying the
#:   one signal that answers "has this client actually received this?".
#: * ``worker._client_context`` picks the first ``'recipient'`` as the person to render for, falling
#:   back to ``intended_contact_id``. Unaffected by a 'subject' row, deliberately.
#:
#: So a 'subject' link buys visibility WITHOUT the claim of delivery. The value is not new —
#: ``routers/illustrations._store_pdf`` has always filed its PDFs this way; this is that rule made
#: general and given one implementation. The primary key is (document_id, contact_id, role), so a
#: 'subject' row and a later 'recipient' row for the same client coexist: filing does not consume
#: the send, and a send still records itself as a send.
SUBJECT_ROLE = "subject"


async def link_subject(
    session: AsyncSession,
    *,
    tenant_id: int,
    document_id: uuid.UUID | str,
    contact_id: int,
    added_by: str | None = None,
) -> None:
    """Link ``contact_id`` to a document as its SUBJECT: the client sees everything filed against
    them, without a staffer having to perform a separate "send".

    Idempotent (``ON CONFLICT ... DO NOTHING``), so every filing path may call it unconditionally —
    re-filing under the same client is a no-op, not a duplicate-key error.

    ``added_by`` follows this module's rule: the request path passes nothing and keeps stamping the
    signed-in staffer through ``auth.uid()``; the engine passes nothing either and honestly records
    no human (``auth.uid()`` is NULL on its ``service_role`` session, and the column is nullable).
    """
    await session.execute(
        text(
            "INSERT INTO documents.document_contacts "
            "  (document_id, tenant_id, contact_id, role, added_by) "
            "VALUES (CAST(:doc AS uuid), :t, :cid, :role, "
            "        COALESCE(CAST(:by AS uuid), auth.uid())) "
            "ON CONFLICT (document_id, contact_id, role) DO NOTHING"
        ),
        {
            "doc": str(document_id),
            "t": tenant_id,
            "cid": contact_id,
            "role": SUBJECT_ROLE,
            "by": added_by,
        },
    )


#: The one document type held back from the client when it is filed. An illustration is a draft
#: until an adviser has read it, and the firm — not the renderer — decides when the client does.
ILLUSTRATION_TYPE = "illustration"


async def illustrations_auto_share(session: AsyncSession, tenant_id: int) -> bool:
    """Whether this firm shows a rendered illustration to its client at once
    (``auth.tenants.illustrations_auto_share``, 0248). Off by default: the subject link that is
    the client's read permission is then bound by a person — a share or a send from the document —
    rather than at render (FB-20261006-04)."""
    flag = (
        await session.execute(
            text("SELECT illustrations_auto_share FROM auth.tenants WHERE id = :t"),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()
    return bool(flag)


async def shared_on_render(
    session: AsyncSession, *, tenant_id: int, document_id: uuid.UUID | str
) -> bool:
    """Whether a document that has just rendered may be filed under its client now.

    Every type but an illustration is; an illustration only when the firm has switched
    ``illustrations_auto_share`` on. ``worker._persist`` asks this before ``link_subject``.
    """
    document_type = (
        await session.execute(
            text("SELECT document_type FROM documents.documents WHERE id = CAST(:d AS uuid)"),
            {"d": str(document_id)},
        )
    ).scalar_one_or_none()
    if document_type != ILLUSTRATION_TYPE:
        return True
    return await illustrations_auto_share(session, tenant_id)


# ---------------------------------------------------------------------------------------------
# send — release a document to clients
# ---------------------------------------------------------------------------------------------


async def send(
    session: AsyncSession,
    *,
    tenant_id: int,
    document_uid: str,
    contact_ids: Sequence[int],
    message: str | None = None,
    sent_by: str | None = None,
    sent_by_name: str | None = None,
) -> SendOutcome:
    """Deliver a document to one or more clients: link each as a recipient so it appears in their
    portal and record the covering note against each send. The EMAIL is the notification system's
    job — the ``document.shared`` rule renders the ``document_sent`` template (enriched with this
    covering note) and queues it — so there is no inline send here; ``notified`` reports how many
    recipients CAN be emailed (have an address), not a synchronous send result.

    This is the client-facing RELEASE act, which is why the human route gates it on
    ``documents:send`` and the contract gates the automated one on the same slug.
    """
    if not contact_ids:
        raise DocumentServiceError("No recipients.")
    doc = (
        (
            await session.execute(
                text(
                    "SELECT id, name FROM documents.documents WHERE uid = :uid AND tenant_id = :t"
                ),
                {"uid": document_uid, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if doc is None:
        raise DocumentServiceError("Document not found")
    # Recipients must be crm.contacts of this firm the caller may see. RLS on document_contacts
    # only checks the tenant, so the contacts are validated here — and on the request path RLS on
    # crm.contacts narrows this further to the caller's book / read-all scope.
    valid = (
        (
            await session.execute(
                text(
                    "SELECT id, name, first_name, email FROM crm.contacts "
                    "WHERE id = ANY(:cids) AND tenant_id = :t"
                ),
                {"cids": list(contact_ids), "t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    if not valid:
        raise DocumentServiceError("No valid client recipients.")
    recipients = [int(r["id"]) for r in valid]
    await session.execute(
        text(
            "INSERT INTO documents.document_contacts "
            "  (document_id, tenant_id, contact_id, role, added_by) "
            "SELECT d.id, d.tenant_id, c, 'recipient', COALESCE(CAST(:by AS uuid), auth.uid()) "
            "FROM documents.documents d "
            "CROSS JOIN unnest(CAST(:cids AS int[])) AS c "
            "WHERE d.uid = :uid AND d.tenant_id = :t "
            "ON CONFLICT (document_id, contact_id, role) DO NOTHING"
        ),
        {"cids": recipients, "uid": document_uid, "t": tenant_id, "by": sent_by},
    )
    note = (message or "").strip() or None
    # One audit event per recipient — a send is a per-client act, so each event names exactly one
    # client. This keeps the client's own activity tab leak-free (scope by the event's own
    # recipient, not the document's whole recipient list) and lets the feed/metadata always name
    # who a document went to without a read-time join. The covering note rides on the event: that
    # is where it is persisted, and it is what the client's activity feed shows them.
    for r in valid:
        await audit_event(
            session,
            document_uid,
            "document.sent",
            {"recipient_id": r["id"], "recipient_name": r["name"], "message": note},
            tenant_id=tenant_id,
            actor_id=sent_by,
        )

    # The email is the notification system's job (the document.shared rule → document_sent
    # template, enriched from the document.sent event above). Here we only report who CAN be
    # emailed — a recipient with no address is named so the sender sees the gap.
    notified = sum(1 for r in valid if r["email"])
    not_notified = [str(r["name"] or r["id"]) for r in valid if not r["email"]]
    return SendOutcome(
        sent=len(recipients),
        notified=notified,
        not_notified=not_notified,
        recipient_ids=recipients,
        document_name=doc["name"],
    )


# ---------------------------------------------------------------------------------------------
# create_request — ask a client for something
# ---------------------------------------------------------------------------------------------


async def create_request(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    items: Sequence[RequestItemSpec],
    priority: str = "normal",
    message: str | None = None,
    due_date: str | None = None,
    requested_by: str | None = None,
    group_id: int | None = None,
    batch_ids: Sequence[str | None] | None = None,
) -> str:
    """Ask a client to do one or more things (upload / sign / acknowledge / complete a form).
    Returns the request's uid.

    `group_id`/`batch_ids` (0056/0057) are set only by `messaging_groups.create_group_request`,
    which calls this once PER MEMBER for the same set of items — `batch_ids[i]` (one entry per
    item, shared across every member's call) is what lets the group's feed collapse item `i`'s
    N per-member echoes back into a single "Requested: ..." entry, the same as a text/attachment
    send. `len(batch_ids)` must equal `len(items)` when given; the single-contact caller
    (`routers/documents.py`) never passes either.

    A request lands in the client's portal AND their inbox, so raising one is the same
    client-facing release act as sending a document — hence ``documents:send`` on both callers. The
    EMAIL is the notification system's job (the ``document.request`` rule renders the
    ``request_raised`` template, enriched with the item list + note + due date), not an inline send.
    """
    if not items:
        raise DocumentServiceError("A request needs at least one item.")
    for item in items:
        if item.action not in REQUEST_ACTIONS:
            raise DocumentServiceError(f"'{item.action}' is not a request action.")
    # agree/acknowledge act ON a document, so they must carry a target — otherwise fulfilment
    # silently confirms nothing (no contact link, no audit event) yet marks the item done.
    if any(i.action in _TARGETED_ACTIONS and not i.target_document_uid for i in items):
        raise DocumentServiceError("Agree/acknowledge items need a target document.")
    known = (
        await session.execute(
            text("SELECT 1 FROM crm.contacts WHERE id = :c AND tenant_id = :t"),
            {"c": contact_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if known is None:
        raise DocumentServiceError("Unknown client, or you don't have access to them.")
    # Every agree/acknowledge target must actually resolve to a visible document -- otherwise the
    # insert's `(SELECT id ... WHERE uid = :target)` yields NULL and the item later self-marks
    # fulfilled without linking/auditing anything (the exact failure the non-empty check above
    # only half-guards against).
    targets = [i.target_document_uid for i in items if i.target_document_uid]
    if targets:
        found = set(
            (
                await session.execute(
                    text(
                        "SELECT uid FROM documents.documents "
                        "WHERE uid = ANY(:uids) AND tenant_id = :t"
                    ),
                    {"uids": targets, "t": tenant_id},
                )
            )
            .scalars()
            .all()
        )
        missing = [t for t in targets if t not in found]
        if missing:
            raise DocumentServiceError(
                f"Target document(s) not found: {', '.join(sorted(set(missing)))}"
            )
    req_uid = new_id()
    req_id = (
        await session.execute(
            text(
                "INSERT INTO documents.requests "
                "  (uid, tenant_id, contact_id, requested_by, priority, message, due_date, "
                "   group_id) "
                "VALUES (:uid, :t, :cid, COALESCE(CAST(:by AS uuid), auth.uid()), "
                "  :prio, :msg, CAST(:due AS date), :group_id) RETURNING id"
            ),
            {
                "uid": req_uid,
                "t": tenant_id,
                "cid": contact_id,
                "by": requested_by,
                "prio": priority,
                "msg": message,
                "due": due_date,
                "group_id": group_id,
            },
        )
    ).scalar_one()
    item_ids: list[int] = []
    item_uids: list[str] = []
    for item in items:
        item_uid = new_id()
        item_id = (
            await session.execute(
                text(
                    "INSERT INTO documents.request_items "
                    "  (uid, request_id, tenant_id, action, title, category, format_hint, "
                    "   form_uid, target_document_id) "
                    "VALUES (:uid, :rid, :t, :action, :title, :cat, :fmt, :form, "
                    "  (SELECT id FROM documents.documents "
                    "     WHERE uid = :target AND tenant_id = :t)) "
                    "RETURNING id"
                ),
                {
                    "uid": item_uid,
                    "rid": req_id,
                    "t": tenant_id,
                    "action": item.action,
                    "title": item.title,
                    "cat": item.category,
                    "fmt": item.format_hint,
                    "form": item.form_uid,
                    "target": item.target_document_uid,
                },
            )
        ).scalar_one()
        item_ids.append(item_id)
        item_uids.append(item_uid)
    # Look the client up for the task mirror below (their name). The client is told about the
    # request by the event-triggered notification system — the `document.request` rule renders
    # `request_raised` (enriched with the item list, note and due date) — NOT an inline send here.
    # That retired the old, non-event email path (docs/notifications.md). NOTE: a GROUP request
    # fans out per-member below the notification consumer's view (it sees one action for the group
    # endpoint), so group-request email coverage is an event-side follow-up.
    contact = (
        (
            await session.execute(
                text("SELECT name FROM crm.contacts WHERE id = :cid AND tenant_id = :t"),
                {"cid": contact_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    # Echo into the client's chat thread too — best-effort, never a precondition for raising a
    # request. The request row itself falls back to `auth.uid()` in SQL when `requested_by` is
    # unset (the router never passes it — only the SQL COALESCE does), so the echo resolves the
    # sender the SAME way rather than trusting the Python param: `current_user_uuid` raises
    # MessagingError for an automation call (system_transaction has no human actor to speak
    # as), which is caught below — no echo, not a failed request. One message PER item (not one
    # joined line) — each is linked via `request_item_id` so the client's thread can render its
    # own clickable card, matching request_items' own "independently fulfilled" design.
    try:
        sender_user_id = requested_by or await messaging.current_user_uuid(session)
        conversation = await messaging.get_or_create_conversation_for_contact(
            session, tenant_id, contact_id
        )
        request_rows = zip(items, item_ids, item_uids, strict=True)
        for idx, (item, item_id, item_uid) in enumerate(request_rows):
            batch_id = batch_ids[idx] if batch_ids is not None else None
            _conversation, chat_message = await messaging.send_staff_message_for_request_item(
                session,
                tenant_id,
                conversation["id"],
                sender_user_id,
                f"Requested: {item.title}",
                item_id,
                item_uid,
                item.title,
                item.action,
                group_id=group_id,
                batch_id=batch_id,
            )
            # The client side always gets a push: it didn't locally append this message the
            # way the normal send flow would (it was raised via the request dialog, not the
            # composer), so even the requesting staffer's own already-open chat needs the WS
            # round trip to show it there. The STAFF side only gets one when this ISN'T a group
            # echo — a group-tagged message is excluded from the ordinary Direct-message view
            # entirely (`list_messages`'s `include_group_messages=False`), so pushing it there
            # live would just re-introduce the same "group send looks like a DM" bug the send
            # paths already fixed; `messaging_groups.create_group_request` doesn't push a
            # "group_message" event of its own either — the group's feed picks these up on its
            # next fetch (the caller re-fetches inline), not live across every open tab.
            push_payload = {
                "type": "message",
                "conversation_id": conversation["id"],
                "message": chat_message,
            }
            await live_push.push(live_push.client_key(contact_id), push_payload)
            if group_id is None:
                await live_push.push(live_push.staff_key(tenant_id), push_payload)
    except messaging.MessagingError:
        logger.warning("request %s: could not mirror requested items to chat", req_uid)
    # Mirror each item as a Task too — best-effort, same discipline as the chat echo: a request
    # is fully raised whether or not this side effect lands. `subject_type='document'` means an
    # actual documents.documents ROW (app/tasks/service.py's _SUBJECT_CHECKS), never a request, so
    # the link is `source_kind='document'` + the item's own uid as `source_ref` (idempotent —
    # calling this twice for the same item never double-creates) rather than a subject pair.
    contact_name = contact["name"] if contact is not None else None
    for item, item_uid in zip(items, item_uids, strict=True):
        try:
            await tasks_service.create_task(
                session,
                tenant_id=tenant_id,
                assignee_kind="contact",
                assignee_contact_id=contact_id,
                title=item.title,
                category=_TASK_CATEGORY_BY_ACTION.get(item.action, "generic"),
                description=(
                    f"Waiting on {contact_name or 'the client'} to "
                    f"{_TASK_VERB_BY_ACTION.get(item.action, 'complete')} this — "
                    f"raised via document request {req_uid}."
                ),
                source_kind="document",
                source_ref=item_uid,
                due_date=due_date,
                priority=_TASK_PRIORITY.get(priority, "normal"),
                created_by=requested_by,
            )
        except tasks_service.TaskError:
            logger.warning("request %s: could not mirror item %s as a task", req_uid, item_uid)
    return req_uid


# ---------------------------------------------------------------------------------------------
# fulfil a request item straight from the chat composer (no documents.documents row)
# ---------------------------------------------------------------------------------------------


async def find_own_pending_upload_item(
    session: AsyncSession, item_uid: str
) -> dict[str, Any] | None:
    """A client's own still-pending upload item, by uid — runs on the CALLER's own session
    (never service_role), so RLS's `request_items_read` policy (its `contact_scope()` branch) is
    the entire ownership check: a client can't reach another client's item by guessing its uid.
    `action = 'upload'` on purpose — agree/acknowledge/complete_form aren't fulfilled by
    attaching a file, so the chat card never offers this path for them. `group_id` (the parent
    request's, 0058) tells the caller whether to tag the fulfilment message the same way, so it
    surfaces in that group's own feed too, not just this client's 1:1 thread."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT ri.id, ri.uid, ri.tenant_id, ri.title, r.group_id "
                    "FROM documents.request_items ri "
                    "JOIN documents.requests r ON r.id = ri.request_id "
                    "WHERE ri.uid = :uid AND ri.status = 'pending' AND ri.action = 'upload'"
                ),
                {"uid": item_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def mark_request_item_fulfilled_via_chat(tenant_id: int, item_id: int) -> None:
    """Fulfil a request item with no `documents.documents` row — the chat-attachment path (a
    client uploading straight through the message composer, the file landing in
    `messaging.attachments` instead). Runs its OWN service_role transaction:
    `documents.request_items`/`requests`' write policy requires `tenant_lineage()` non-empty,
    which is ALWAYS empty for a client-portal session (the same shape as the AML tipping-off
    control) — a client can never satisfy that WITH CHECK directly, the exact reason
    `fulfill_request_item` (the Requests-page path) runs elevated via `firm_session` too."""
    async with system_transaction() as session:
        row = (
            await session.execute(
                text(
                    "UPDATE documents.request_items SET status = 'fulfilled', fulfilled_at = now() "
                    "WHERE id = :iid AND tenant_id = :t AND status = 'pending' "
                    "RETURNING request_id"
                ),
                {"iid": item_id, "t": tenant_id},
            )
        ).first()
        if row is None:
            return
        await session.execute(
            text(
                "UPDATE documents.requests SET status = 'fulfilled', completed_at = now() "
                "WHERE id = :rid AND NOT EXISTS (SELECT 1 FROM documents.request_items "
                "  WHERE request_id = :rid AND status = 'pending')"
            ),
            {"rid": row.request_id},
        )
        # Mirrors fulfill_request_item's own call — a no-op unless this item backs a flow-case
        # step, in which case chat-fulfilment must advance the case exactly like the Requests
        # page does.
        await flows_orchestrator.on_item_fulfilled(session, item_id)
