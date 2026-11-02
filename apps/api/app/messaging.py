"""Client<->firm chat (schema `messaging`) — a prototype slice (see 0080_messaging.sql).

One conversation per client. `adviser_user_id` is an optional pointer re-synced from
`crm.client_advisers` when an adviser is assigned — it is not a gate on who may open or
send: staff reach a thread with `messages:read`/`messages:write`, not by being on that
client's book. The CLIENT side runs on the RLS `authenticated` role (`tenant_session`) —
`auth.contact_scope()`/`auth.tenant_lineage()` already restrict a client to their own
conversation, so those functions never need a service-role bypass. The STAFF side runs on
`firm_session` (service_role) and scopes every statement to `tenant_id` explicitly, same
discipline as `app.contacts`/`app.provisioning`.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage, tenant_storage
from app.db import system_transaction
from app.ids import new_id

SenderKind = Literal["client", "staff"]
Reader = Literal["client", "staff"]

_READ_RECEIPT_COLUMN: dict[Reader, str] = {
    "client": "client_last_read_at",
    "staff": "staff_last_read_at",
}

_CONVERSATION_COLUMNS = (
    "id, uid, tenant_id, contact_id, adviser_user_id, "
    "client_last_read_at, staff_last_read_at, created_at, updated_at"
)
_ATTACHMENT_STORAGE_COLUMNS = "id, uid, tenant_id, bucket, storage_key, filename, content_type"

#: One file to attach, already read and validated by the router: (filename, bytes,
#: content_type, extension). A send takes a LIST — 0123 lets one message carry several, so a
#: note and the three documents it is about arrive as one thing rather than four.
StagedFile = tuple[str, bytes, str, str]


class MessagingError(Exception):
    """A refusal the router turns into an HTTP error (never a bare 500)."""


class AttachmentStorageUnavailable(MessagingError):
    """The tenant has no active object-storage bucket provisioned yet — a distinct case from a
    plain not-found/validation refusal so the router can surface 503, the same split
    `app.storage.StorageUnavailable` gets for the documents feature."""


async def current_user_uuid(session: AsyncSession) -> str:
    """The caller's own `auth.users.id`, read off the claims already published on this
    session (works under `authenticated` AND `service_role` — the claims are independent of
    which role is bound for RLS)."""
    uid = (await session.execute(text("SELECT auth.uid()"))).scalar()
    if uid is None:
        raise MessagingError("No resolvable user identity for this session")
    return str(uid)


async def _is_client_portal_session(session: AsyncSession) -> bool:
    """`auth.contact_scope()` is NOT by itself proof of "this caller is a client":
    `auth.resolve_claims` folds a staff adviser's OWN ASSIGNED CLIENTS into their contact_scope
    too (crm.client_advisers), so a non-empty scope alone would let an adviser's request be
    mistaken for one of their own clients. `auth.portal() = 'client'` is THE signal (0107:
    app-only classification, membership-gated) — the same one `auth.tenant_lineage()` whitelists
    against. The old `auth.role() = 'client'` belt is gone with the org model."""
    row = (await session.execute(text("SELECT auth.portal() = 'client'"))).scalar()
    return bool(row)


async def _caller_contact_id(session: AsyncSession, contact_id: int | None = None) -> int:
    """The caller's own contact — CLIENT endpoints only.

    ``contact_id``, when given and a member of the caller's real ``auth.contact_scope()``, is
    the active-contact selector for a login linked to more than one client. Otherwise falls
    back to the first scope id — arbitrary, but this is also the pre-existing behaviour for
    every login with exactly one linked contact, which is the vast majority."""
    if not await _is_client_portal_session(session):
        raise MessagingError("This endpoint is for client principals only")
    contact_ids = (await session.execute(text("SELECT auth.contact_scope()"))).scalar()
    if not contact_ids:
        raise MessagingError("No linked contact for this session")
    if contact_id is not None and contact_id in contact_ids:
        return contact_id
    return int(contact_ids[0])


async def _with_client_unread(
    session: AsyncSession, conversation: dict[str, Any]
) -> dict[str, Any]:
    unread = (
        await session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM messaging.messages "
                "WHERE conversation_id = :cid AND sender_kind = 'staff' "
                "AND created_at > COALESCE(:since, '-infinity'::timestamptz))"
            ),
            {"cid": conversation["id"], "since": conversation["client_last_read_at"]},
        )
    ).scalar()
    return {**conversation, "unread": bool(unread)}


# ---- conversations (one row per client; adviser pointer is optional) -------------------------


async def _upsert_conversation(
    session: AsyncSession,
    tenant_id: int,
    contact_id: int,
    adviser_id: str | None,
    *,
    with_contact_name: bool = False,
) -> dict[str, Any]:
    """Open (or lazily create) the channel for a contact.

    The adviser pointer is re-synced only when an adviser IS assigned. Un-assigning someone
    must not wipe a pointer (or hide a thread that already has messages), and creating a
    thread no longer waits on `crm.client_advisers` — `messages:write` is the staff gate.

    The DO UPDATE is guarded so an unchanged (or still-absent) adviser writes NOTHING. A
    get-or-create the client record's Messages tab makes on every page view used to re-set
    `adviser_user_id` to the same value, fire the UPDATE trigger, and leave an `audit.log`
    entry for a conversation nobody had touched.

    The UNION arm is what the guard costs: a suppressed DO UPDATE returns no row, so the
    existing conversation has to be read back explicitly.
    """
    returning = (
        "SELECT one.*, ct.name AS contact_name, ct.uid AS contact_uid "
        "FROM one JOIN crm.contacts ct ON ct.id = one.contact_id"
        if with_contact_name
        else "SELECT * FROM one"
    )
    conversation = (
        (
            await session.execute(
                text(
                    "WITH ins AS ("  # noqa: S608 - fixed _CONVERSATION_COLUMNS list, not user input
                    "  INSERT INTO messaging.conversations "
                    "  (tenant_id, contact_id, adviser_user_id) "
                    "  VALUES (:tenant_id, :contact_id, :adviser_id) "
                    "  ON CONFLICT (tenant_id, contact_id) "
                    "  DO UPDATE SET adviser_user_id = EXCLUDED.adviser_user_id "
                    "  WHERE EXCLUDED.adviser_user_id IS NOT NULL "
                    "    AND messaging.conversations.adviser_user_id "
                    "        IS DISTINCT FROM EXCLUDED.adviser_user_id "
                    f"  RETURNING {_CONVERSATION_COLUMNS}"
                    "), found AS ("
                    f"  SELECT {_CONVERSATION_COLUMNS} FROM messaging.conversations "
                    "  WHERE tenant_id = :tenant_id AND contact_id = :contact_id "
                    "    AND NOT EXISTS (SELECT 1 FROM ins)"
                    "), one AS ("
                    "  SELECT * FROM ins UNION ALL SELECT * FROM found"
                    f") {returning}"
                ),
                {
                    "tenant_id": tenant_id,
                    "contact_id": contact_id,
                    "adviser_id": adviser_id,
                },
            )
        )
        .mappings()
        .one()
    )
    return dict(conversation)


# ---- client side (tenant_session — RLS authenticated role) --------------------------------


async def get_or_create_conversation_for_caller(
    session: AsyncSession, contact_id: int | None = None
) -> dict[str, Any]:
    """The client's one channel, re-syncing its adviser pointer from `crm.client_advisers` when
    one is assigned. A missing assignment is not a refusal — the firm still has a thread with
    this client, and staff with `messages:read`/`messages:write` can use it.

    ``contact_id`` selects WHICH of the caller's linked contacts' channel to resolve — each
    contact has its own conversation row (``messaging.conversations``'s unique key is
    ``(tenant_id, contact_id)``), so a login linked to more than one client already has
    separate threads per client; this just picks which one (see ``_caller_contact_id``)."""
    contact_id = await _caller_contact_id(session, contact_id)

    row = (
        await session.execute(
            text(
                "SELECT c.tenant_id, ca.adviser_id "
                "FROM crm.contacts c "
                "LEFT JOIN crm.client_advisers ca "
                "  ON ca.contact_id = c.id AND ca.tenant_id = c.tenant_id AND ca.role = 'adviser' "
                "WHERE c.id = :contact_id "
                "LIMIT 1"
            ),
            {"contact_id": contact_id},
        )
    ).first()
    if row is None:
        raise MessagingError("No linked contact for this session")

    conversation = await _upsert_conversation(
        session, int(row.tenant_id), contact_id, row.adviser_id
    )
    return await _with_client_unread(session, conversation)


async def _insert_message(
    session: AsyncSession,
    tenant_id: int,
    conversation_id: int,
    contact_id: int,
    sender_kind: SenderKind,
    sender_user_id: str | None,
    body: str | None,
    *,
    request_item_id: int | None = None,
    group_id: int | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Shared by every send path (text-only and attachment). `body` may be None/blank — an
    attachment-only message — now that `messaging.messages.body` is nullable (0052); "at least
    one of body/attachment" is enforced by the caller, never here (this function doesn't know
    yet whether an attachment is coming). `request_item_id` links this message to the
    document-request item it announces (0054) — only `create_request`'s chat echo sets it; a
    plain send never should, so it isn't a public parameter of `send_staff_message`/
    `send_client_message`. `group_id` (0056) tags a message as sent THROUGH a group — only
    `messaging_groups.send_group_message` sets it, via `send_staff_message`'s own `group_id`
    param; nothing a client sends is ever tagged. `batch_id` (0057) ties together the N
    per-member rows ONE group send creates, so the feed can collapse them back into a single
    entry; always set alongside `group_id`, never on its own.

    `sender_name`/`sender_role` are resolved here (not left for the reader to look up) so the
    just-sent message's immediate HTTP response already carries them — the Slack-style header
    (name + role) would otherwise be blank until the next full reload re-reads `list_messages`.
    A client sender has no `auth.users` row at all (`sender_user_id` is NULL), so both come back
    NULL for one — the caller already has the client's own name off the conversation."""
    message = (
        (
            await session.execute(
                text(
                    "WITH ins AS ("
                    "  INSERT INTO messaging.messages "
                    "  (tenant_id, conversation_id, contact_id, sender_kind, sender_user_id, body, "
                    "   request_item_id, group_id, batch_id, sender_is_support, "
                    "   sender_display_name) "
                    "  VALUES (:tenant_id, :conversation_id, :contact_id, :sender_kind, "
                    "   :sender_user_id, :body, :request_item_id, :group_id, :batch_id, "
                    "   auth.portal() = 'operations', "
                    "   (SELECT name FROM auth.users WHERE id = :sender_user_id)) "
                    "  RETURNING id, tenant_id, conversation_id, contact_id, sender_kind, "
                    "    sender_user_id, sender_is_support, sender_display_name, body, created_at"
                    ") "  # noqa: S608 - fixed column list, not user input
                    # `contact_id` is returned so the action event can capture WHOSE channel this
                    # is. The notification for a client's message must name the CLIENT, and the
                    # actor cannot supply it: one login can act for several contacts, and even at
                    # capacity 'self' the login's name (an auth.users row) is not the client record
                    # (a crm.contacts row) — a real book has them differing.
                    "SELECT ins.id, ins.conversation_id, c.uid AS conversation_uid, "
                    "  ins.contact_id, ins.sender_kind, ins.sender_user_id, "
                    "  ins.sender_is_support, ins.body, ins.created_at, "
                    "  COALESCE(ins.sender_display_name, u.name) AS sender_name, "
                    "  r.name AS sender_role, "
                    # The same expression as the read path rather than a hard-coded true: one
                    # rule decides authorship everywhere. NOTE this row is also what gets
                    # BROADCAST, and `live_push.push` strips the flag for that reason.
                    "  COALESCE(ins.sender_user_id = auth.uid(), false) AS sent_by_me "
                    "FROM ins "
                    "JOIN messaging.conversations c ON c.id = ins.conversation_id "
                    "LEFT JOIN auth.users u ON u.id = ins.sender_user_id "
                    "LEFT JOIN auth.organisation_memberships om ON om.user_id = u.id "
                    "  AND om.tenant_id = ins.tenant_id AND om.portal = 'platform' "
                    "LEFT JOIN auth.roles r ON r.id = om.role_id"
                ),
                {
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                    "contact_id": contact_id,
                    "sender_kind": sender_kind,
                    "sender_user_id": sender_user_id,
                    "body": body,
                    "request_item_id": request_item_id,
                    "group_id": group_id,
                    "batch_id": batch_id,
                },
            )
        )
        .mappings()
        .one()
    )
    return {**dict(message), "attachments": [], "request_item": None}


async def _store_attachment(
    session: AsyncSession,
    tenant_id: int,
    contact_id: int,
    message_id: int,
    filename: str,
    data: bytes,
    content_type: str,
    ext: str,
) -> dict[str, Any]:
    """Upload to the TENANT'S OWN bucket (never the shared documents bucket — see
    app/storage.py's "Chat attachments" section) and record it. Raises
    `AttachmentStorageUnavailable` if the tenant has no active bucket yet."""
    store = await tenant_storage.get_tenant_store(session, tenant_id)
    if store is None:
        raise AttachmentStorageUnavailable("Attachments aren't available for this firm yet")
    contact_uid = (
        await session.execute(
            text("SELECT uid FROM crm.contacts WHERE tenant_id = :tenant_id AND id = :contact_id"),
            {"tenant_id": tenant_id, "contact_id": contact_id},
        )
    ).scalar_one()
    # The uid is minted HERE rather than left to the column default, because the storage key is
    # built from it and the object is written before the row exists. Keying on the message id
    # instead — which is what this did — made a second file on the same message overwrite the first.
    attachment_uid = new_id()
    key = storage.attachment_key(contact_uid, attachment_uid, ext)
    await storage.put_tenant_object(store, key, data, content_type)
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO messaging.attachments "  # noqa: S608 - fixed columns list, not user input
                    "(uid, tenant_id, contact_id, message_id, bucket, storage_key, filename, "
                    " content_type, size_bytes, sha256) "
                    "VALUES (:uid, :tenant_id, :contact_id, :message_id, :bucket, :key, "
                    " :filename, :content_type, :size, :sha) "
                    # `uid` (0091) is what the download path names, so the freshly-stored
                    # attachment has to carry it back — a sender renders their own upload from
                    # this row, not from a re-read.
                    "RETURNING id, uid, filename, content_type, size_bytes"
                ),
                {
                    "uid": attachment_uid,
                    "tenant_id": tenant_id,
                    "contact_id": contact_id,
                    "message_id": message_id,
                    "bucket": store.bucket,
                    "key": key,
                    "filename": filename,
                    "content_type": content_type,
                    "size": len(data),
                    "sha": hashlib.sha256(data).hexdigest(),
                },
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def send_client_message(
    session: AsyncSession, body: str, contact_id: int | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Send as the caller's own contact. Returns `(conversation, message)` — the conversation is
    re-resolved (and re-synced) fresh rather than trusting a conversation id from a previous
    call, so an adviser reassignment between page load and send still lands in the right place.

    ``contact_id`` selects which linked contact is sending, same as
    ``get_or_create_conversation_for_caller``."""
    conversation = await get_or_create_conversation_for_caller(session, contact_id)
    message = await _insert_message(
        session,
        conversation["tenant_id"],
        conversation["id"],
        conversation["contact_id"],
        "client",
        None,
        body,
    )
    return conversation, message


async def send_client_attachment(
    session: AsyncSession,
    body: str | None,
    files: list[StagedFile],
    *,
    request_item: dict[str, Any] | None = None,
    group_id: int | None = None,
    batch_id: str | None = None,
    contact_id: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The attachment counterpart to `send_client_message` — `body` may be None/blank (the
    router already validated `data` via `storage.validate_attachment_upload`).

    ``contact_id`` selects which linked contact is sending, same as
    ``get_or_create_conversation_for_caller``.

    `request_item` (`{id, uid, title}`, already resolved and ownership-checked by the router)
    links this message to the request it fulfils — the same link `create_request`'s echo puts on
    the original "Requested: ..." message, so the client's thread can render this as the rich
    "here's the document you asked for" card (title, not the raw filename) instead of a plain
    attachment chip. `status` is hardcoded `'fulfilled'` rather than re-read: the router marks
    the item fulfilled right after this call returns, so by the time anyone sees this response
    that will already be true. `group_id`/`batch_id` (0056/0057/0058) are set only when the item
    being fulfilled was itself raised via a group (`documents.requests.group_id`) — the router
    resolves that and mints a fresh, single-use `batch_id` (this is one client's own reply, not a
    fan-out), so the fulfilment surfaces in that group's feed as its own entry, tagged with this
    client, same as the request that asked for it."""
    conversation = await get_or_create_conversation_for_caller(session, contact_id)
    message = await _insert_message(
        session,
        conversation["tenant_id"],
        conversation["id"],
        conversation["contact_id"],
        "client",
        None,
        body,
        request_item_id=request_item["id"] if request_item else None,
        group_id=group_id,
        batch_id=batch_id,
    )
    if request_item is not None:
        message["request_item"] = {
            "uid": request_item["uid"],
            "title": request_item["title"],
            "action": "upload",
            "status": "fulfilled",
        }
    message["attachments"] = [
        await _store_attachment(
            session,
            conversation["tenant_id"],
            conversation["contact_id"],
            message["id"],
            name,
            blob,
            ctype,
            extension,
        )
        for name, blob, ctype, extension in files
    ]
    return conversation, message


async def list_messages(
    session: AsyncSession, conversation_id: int, *, include_group_messages: bool = True
) -> list[dict[str, Any]]:
    """`include_group_messages=False` is the STAFF direct-conversation view (`GET
    /messaging/conversations/{id}/messages`): a group broadcast landed in this same conversation
    row, but it isn't part of this 1:1 thread as far as the adviser is concerned — it belongs to
    the group's own feed instead (`messaging_groups.get_group_feed`). The CLIENT'S OWN thread
    (`GET /messaging/thread`) always passes the default `True` — to a client there is no
    distinction at all, by design."""
    where = "m.conversation_id = :cid"
    if not include_group_messages:
        where += " AND m.group_id IS NULL"
    rows = (
        (
            await session.execute(
                # `where` is a fixed literal plus an optional fixed clause, no user input.
                text(
                    "SELECT m.id, m.conversation_id, c.uid AS conversation_uid, "  # noqa: S608
                    # Not part of the response shape — `_shape_message` drops it. It is what
                    # `_fill_sender_identities` scopes its lookup to.
                    "m.tenant_id, "
                    "m.sender_kind, m.sender_user_id, m.sender_is_support, m.body, "
                    # Whether the CALLER sent it — a thread puts your own messages on one side
                    # and everybody else's on the other, and 'everybody else' includes the
                    # firm's other advisers. `sender_kind` cannot answer that: it says staff or
                    # client, so one adviser's reply was shown as another's own words.
                    #
                    # Only the server can answer it. `sender_user_id` is an `auth.users` uuid
                    # and the portal holds a WorkOS id, so the browser has nothing to compare
                    # against without being handed a mapping it has no other use for.
                    #
                    # COALESCE because a client's message has a NULL sender (they have no
                    # `auth.users` row on the send path), and `NULL = auth.uid()` is NULL, not
                    # false — an unknown would reach the portal as a missing key and read as
                    # "not mine" by luck rather than by decision.
                    "COALESCE(m.sender_user_id = auth.uid(), false) AS sent_by_me, "
                    # Resolves on the STAFF side only. `auth.users` is scoped to self + members
                    # of `auth.tenant_lineage()`, which is EMPTY for a client session, so on the
                    # client's own thread every staff sender comes back NULL here and
                    # `_fill_sender_identities` names them afterwards. The stored display name
                    # remains authoritative for a Graphene support sender, whose operations user
                    # is outside the target firm's lineage.
                    "m.created_at, COALESCE(m.sender_display_name, u.name) AS sender_name, "
                    "r.name AS sender_role, "
                    # AGGREGATED, not joined: a message may carry several attachments (0123 drops
                    # the UNIQUE on message_id), and a plain LEFT JOIN would return the message once
                    # per file — three documents becoming three copies of the same message.
                    "COALESCE(("
                    "  SELECT json_agg(json_build_object("
                    "    'id', a.id, 'uid', a.uid, 'filename', a.filename, "
                    "    'content_type', a.content_type, 'size_bytes', a.size_bytes"
                    "  ) ORDER BY a.id) "
                    "  FROM messaging.attachments a WHERE a.message_id = m.id"
                    "), '[]'::json) AS attachments, "
                    "ri.uid AS request_item_uid, ri.title AS request_item_title, "
                    "ri.action AS request_item_action, ri.status AS request_item_status "
                    "FROM messaging.messages m "
                    "JOIN messaging.conversations c ON c.id = m.conversation_id "
                    "LEFT JOIN auth.users u ON u.id = m.sender_user_id "
                    "LEFT JOIN auth.organisation_memberships om ON om.user_id = u.id "
                    "  AND om.tenant_id = m.tenant_id AND om.portal = 'platform' "
                    "LEFT JOIN auth.roles r ON r.id = om.role_id "
                    "LEFT JOIN documents.request_items ri ON ri.id = m.request_item_id "
                    f"WHERE {where} ORDER BY m.created_at"
                ),
                {"cid": conversation_id},
            )
        )
        .mappings()
        .all()
    )
    shaped = [dict(r) for r in rows]
    await _fill_sender_identities(shaped)
    return [_shape_message(r) for r in shaped]


async def _fill_sender_identities(rows: list[dict[str, Any]]) -> None:
    """Name the staff senders a CLIENT session is not allowed to look up for itself.

    The read above LEFT JOINs `auth.users` for each sender's name and role. That resolves on the
    staff side (service_role) and silently yields NULL on the client side: 0107 gives a
    client-portal session an EMPTY `auth.tenant_lineage()`, and `auth_users_scoped_read` (0001)
    lets a caller read their own row plus users who are members of a tenant in that lineage —
    nobody else. So a client got NULL for every staff sender, and with nothing to render the
    portal fell back to "the adviser assigned to me". A firm where two people answer the same
    client showed BOTH of their replies under one name: words attributed to someone who did not
    write them, in the one place a client goes to read what their firm told them.

    Resolved exactly as the adviser card resolves its own name (`routers/crm.py`
    `_adviser_for_caller`): a SHORT service_role lookup keyed strictly on ids the RLS-bound read
    already returned. The caller has, by construction, just been allowed to read these messages,
    so naming their authors discloses nothing the thread was not already showing — and the
    elevated session never sees an id the request did not first earn under RLS. Narrowed to the
    conversation's own tenant besides, so it can only ever name that firm's own staff.

    A NO-OP whenever the join already answered, which is every staff-side call — the set is
    empty and the second query never runs.
    """
    unresolved = {
        str(r["sender_user_id"])
        for r in rows
        if r.get("sender_user_id") is not None and r.get("sender_name") is None
    }
    if not unresolved:
        return
    # One tenant per conversation, and `list_messages` reads one conversation.
    tenant_id = next((r["tenant_id"] for r in rows if r.get("tenant_id") is not None), None)
    if tenant_id is None:
        return
    async with system_transaction() as sysx:
        found = (
            (
                await sysx.execute(
                    text(
                        "SELECT u.id::text AS id, u.name, r.name AS role "
                        "FROM auth.users u "
                        # The membership decides BOTH the role label and whether this person is
                        # one of the firm's staff at all; an INNER join would drop a sender whose
                        # membership has since been removed, and a thread must still be able to
                        # say who wrote a message after its author leaves.
                        "LEFT JOIN auth.organisation_memberships om ON om.user_id = u.id "
                        "  AND om.tenant_id = :t AND om.portal = 'platform' "
                        "LEFT JOIN auth.roles r ON r.id = om.role_id "
                        "WHERE u.id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"t": tenant_id, "ids": sorted(unresolved)},
                )
            )
            .mappings()
            .all()
        )
    by_id = {row["id"]: row for row in found}
    for row in rows:
        if row.get("sender_name") is not None or row.get("sender_user_id") is None:
            continue
        who = by_id.get(str(row["sender_user_id"]))
        if who is None:
            continue
        row["sender_name"] = who["name"]
        # Only when the join found nothing — a role the RLS read DID resolve stays as it is.
        if row.get("sender_role") is None:
            row["sender_role"] = who["role"]


def _shape_message(row: dict[str, Any]) -> dict[str, Any]:
    """Nest the LEFT JOINed attachment/request-item columns (all NULL when the message carries
    none) under their own keys — the shape both frontends render against. The request item's
    status/title are read live off `documents.request_items` every call, never denormalised
    onto the message, so a card can never show a stale "pending" after the item is fulfilled
    some other way (the Requests page, or a different message's chat upload)."""
    # A LIST, always — empty when the message carries none. `uid` is what a download path names
    # (0091); `id` stays for the schema's own key.
    attachments = list(row.get("attachments") or [])
    request_item = None
    if row["request_item_uid"] is not None:
        request_item = {
            "uid": row["request_item_uid"],
            "title": row["request_item_title"],
            "action": row["request_item_action"],
            "status": row["request_item_status"],
        }
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        # What a link to this message's conversation is built from (0091).
        "conversation_uid": row.get("conversation_uid"),
        "sender_kind": row["sender_kind"],
        "sender_user_id": row["sender_user_id"],
        "sender_is_support": bool(row.get("sender_is_support", False)),
        # Absent on a row that did not come from a caller-bound query (the group feed's own
        # projection); false is the safe reading — a message is not yours until something says
        # it is.
        "sent_by_me": bool(row.get("sent_by_me", False)),
        "sender_name": row["sender_name"],
        "sender_role": row["sender_role"],
        "body": row["body"],
        "created_at": row["created_at"],
        "attachments": attachments,
        "request_item": request_item,
    }


async def get_attachment_for_caller(session: AsyncSession, attachment_uid: str) -> dict[str, Any]:
    """Client-side download — RLS already restricts this to the caller's own contact, same as
    every other client-side messaging read (no extra scoping needed here).

    Addressed by `uid` (0091): the client portal's own download path names it, and a serial there
    both leaks the row count and invites walking the table by arithmetic — RLS would refuse each
    attempt, but the refusals are the tell that the guess is the right shape."""
    row = (
        (
            await session.execute(
                text(
                    f"SELECT {_ATTACHMENT_STORAGE_COLUMNS} FROM messaging.attachments "  # noqa: S608
                    "WHERE uid = :uid"
                ),
                {"uid": attachment_uid},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise MessagingError("Attachment not found")
    return dict(row)


async def get_attachment_for_tenant(
    session: AsyncSession, tenant_id: int, conversation_id: int, attachment_uid: str
) -> dict[str, Any]:
    """Staff-side download — `firm_session` runs as `service_role` (RLS bypassed), so the
    tenant scope has to be an explicit column check, same discipline as
    `get_conversation_for_tenant`. Joined through the parent message so the URL's
    conversation is load-bearing, not decorative — an attachment from a DIFFERENT
    conversation in the same tenant 404s here instead of downloading.

    Addressed by `uid` (0091), like the conversation that contains it; `conversation_id` is the
    already-resolved serial id, because it comes from the authorising read rather than the path."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.uid, a.tenant_id, a.bucket, a.storage_key, a.filename, "  # noqa: S608
                    "a.content_type "
                    "FROM messaging.attachments a "
                    "JOIN messaging.messages m ON m.id = a.message_id "
                    "WHERE a.tenant_id = :tenant_id AND m.conversation_id = :conversation_id "
                    "AND a.uid = :uid"
                ),
                {"tenant_id": tenant_id, "conversation_id": conversation_id, "uid": attachment_uid},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise MessagingError("Attachment not found")
    return dict(row)


async def mark_read(session: AsyncSession, conversation_id: int, reader: Reader) -> None:
    column = _READ_RECEIPT_COLUMN[reader]
    await session.execute(
        # column from the fixed _READ_RECEIPT_COLUMN allowlist, not user input
        text(f"UPDATE messaging.conversations SET {column} = now() WHERE id = :cid"),  # noqa: S608
        {"cid": conversation_id},
    )


# ---- staff side (firm_session — service_role, explicit tenant_id scoping) ------------------


async def list_conversations_for_tenant(
    session: AsyncSession,
    tenant_id: int,
    query: str | None,
    *,
    contact_id: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Every client in the tenant who has an established channel (a client who has never
    resolved their thread has no row yet — nothing to list). `unread` is computed from the
    staff read-receipt watermark, never a stored counter. `last_message`/`last_message_at`
    deliberately exclude a group-tagged message (0056) — a group broadcast isn't part of this
    1:1 preview any more than it's part of the 1:1 thread itself (`list_messages`'
    `include_group_messages=False`); it belongs to the group's own row in the Groups section."""
    params: dict[str, Any] = {"tenant_id": tenant_id}
    where = "c.tenant_id = :tenant_id"
    if contact_id is not None:
        where += " AND c.contact_id = :contact_id"
        params["contact_id"] = contact_id
    if query and query.strip():
        # THE CLIENT'S NAME OR SOMETHING THEY SAID. The box reads "Search direct messages…" and
        # matched only `ct.name` — the ORGANISATION column, null for every individual — so a word
        # from a message found nothing and most clients' own names found nothing either. Now a
        # person's composed name, an organisation's name, or the text of any 1:1 message on the
        # thread (group broadcasts excluded, the same way the preview excludes them).
        where += (
            " AND (COALESCE(ct.name, '') ILIKE :q"
            "      OR trim(COALESCE(ct.first_name, '') || ' ' || COALESCE(ct.surname, '')) ILIKE :q"
            "      OR EXISTS (SELECT 1 FROM messaging.messages mq"
            "                 WHERE mq.conversation_id = c.id AND mq.group_id IS NULL"
            "                   AND mq.body ILIKE :q))"
        )
        params["q"] = f"%{query.strip()}%"
    rows = (
        (
            await session.execute(
                # `where` is a fixed literal plus an optional ILIKE clause, both bound via
                # `params` — the interpolated part is never the query's own string, always a param.
                text(
                    "SELECT c.id, c.uid, c.tenant_id, c.contact_id, ct.name AS contact_name, "  # noqa: S608
                    "ct.uid AS contact_uid, "
                    "c.adviser_user_id, c.client_last_read_at, c.staff_last_read_at, c.created_at, "
                    # LATERAL, not a join: with several attachments on one message a plain join
                    # would return that message once per file, and `ORDER BY … LIMIT 1` would then
                    # pick one of the copies — the preview naming whichever file sorted first
                    # rather than the newest message.
                    "(SELECT COALESCE(m.body, 'Attachment: ' || a.filename) "
                    " FROM messaging.messages m "
                    " LEFT JOIN LATERAL ("
                    "   SELECT filename FROM messaging.attachments"
                    "   WHERE message_id = m.id ORDER BY id LIMIT 1"
                    " ) a ON true "
                    " WHERE m.conversation_id = c.id AND m.group_id IS NULL "
                    " ORDER BY m.created_at DESC LIMIT 1) AS last_message, "
                    "(SELECT max(created_at) FROM messaging.messages m "
                    " WHERE m.conversation_id = c.id AND m.group_id IS NULL) AS last_message_at, "
                    "EXISTS (SELECT 1 FROM messaging.messages m WHERE m.conversation_id = c.id "
                    " AND m.sender_kind = 'client' "
                    " AND m.created_at > COALESCE(c.staff_last_read_at, '-infinity'::timestamptz)"
                    ") AS unread "
                    "FROM messaging.conversations c "
                    "JOIN crm.contacts ct ON ct.id = c.contact_id "
                    f"WHERE {where} "
                    "ORDER BY last_message_at DESC NULLS LAST"
                    + (" LIMIT :lim" if limit is not None else "")
                ),
                {**params, **({"lim": max(1, min(limit, 200))} if limit is not None else {})},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def has_unread_conversations(
    session: AsyncSession, tenant_id: int, *, contact_id: int | None = None
) -> bool:
    """Tenant-wide "does this adviser have anything unread" — the Chats nav dot. The EXACT same
    staff-read-receipt predicate `list_conversations_for_tenant`'s per-row `unread` uses (a
    client message newer than `staff_last_read_at`), never a second definition that could
    disagree with the DM inbox's own dots. Deliberately not narrowed to `group_id IS NULL` —
    the per-row `unread` isn't either, so a client's reply inside a group thread still counts.

    `contact_id`, when given, narrows this to ONE client — the client record's own Chats section
    asks "is THIS client's conversation unread", not the whole tenant's. Purely read-only either
    way: a contact with no conversation row yet (never messaged) has nothing to EXISTS-join
    against and reads as not-unread, never creates one."""
    where = "c.tenant_id = :tenant_id"
    params: dict[str, Any] = {"tenant_id": tenant_id}
    if contact_id is not None:
        where += " AND c.contact_id = :contact_id"
        params["contact_id"] = contact_id
    row = (
        await session.execute(
            text(
                "SELECT EXISTS ("  # noqa: S608 - fixed literal, no user input
                "  SELECT 1 FROM messaging.conversations c "
                "  JOIN messaging.messages m ON m.conversation_id = c.id "
                f"  WHERE {where} AND m.sender_kind = 'client' "
                "    AND m.created_at > COALESCE(c.staff_last_read_at, '-infinity'::timestamptz)"
                ")"
            ),
            params,
        )
    ).scalar()
    return bool(row)


async def get_or_create_conversation_for_contact(
    session: AsyncSession, tenant_id: int, contact_id: int
) -> dict[str, Any]:
    """Staff-initiated: open (or lazily create) the channel for a named client in the caller's
    own tenant — the counterpart to `get_or_create_conversation_for_caller` for the case a
    client has never logged in and messaged first. Refuses a contact outside the tenant.
    An assigned adviser is recorded on the row when there is one; it is not required."""
    known = (
        await session.execute(
            text("SELECT 1 FROM crm.contacts WHERE tenant_id = :tenant_id AND id = :contact_id"),
            {"tenant_id": tenant_id, "contact_id": contact_id},
        )
    ).first()
    if known is None:
        raise MessagingError("Contact not found")

    adviser_id = (
        await session.execute(
            text(
                "SELECT adviser_id FROM crm.client_advisers "
                "WHERE tenant_id = :tenant_id AND contact_id = :contact_id AND role = 'adviser'"
            ),
            {"tenant_id": tenant_id, "contact_id": contact_id},
        )
    ).scalar()
    return await _upsert_conversation(
        session,
        tenant_id,
        contact_id,
        str(adviser_id) if adviser_id is not None else None,
        with_contact_name=True,
    )


async def get_conversation_for_tenant(
    session: AsyncSession, tenant_id: int, conversation_id: int
) -> dict[str, Any]:
    return await _conversation_for_tenant(session, tenant_id, "id", conversation_id)


async def get_conversation_for_tenant_by_uid(
    session: AsyncSession, tenant_id: int, conversation_uid: str
) -> dict[str, Any]:
    """The same read, addressed the way a URL addresses it (0091).

    This is the boundary crossing docs/api.md -> "Addressing" describes: the path names the `uid`,
    everything inside keeps the serial `id` the schema and every FK use. The endpoints already had
    to resolve-and-authorise before doing anything, so uid-addressing costs no extra query — the
    lookup that proved the conversation belongs to this tenant is the one that yields the id.
    """
    return await _conversation_for_tenant(session, tenant_id, "uid", conversation_uid)


async def _conversation_for_tenant(
    session: AsyncSession, tenant_id: int, column: str, value: object
) -> dict[str, Any]:
    if column not in {"id", "uid"}:  # pragma: no cover - the two callers above are the allowlist
        raise MessagingError("Conversation not found")
    row = (
        (
            await session.execute(
                # `column` from the two-value allowlist above, never a caller's string.
                text(
                    f"SELECT {_CONVERSATION_COLUMNS} FROM messaging.conversations "  # noqa: S608 - fixed columns list, not user input
                    f"WHERE tenant_id = :tenant_id AND {column} = :value"
                ),
                {"tenant_id": tenant_id, "value": value},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise MessagingError("Conversation not found")
    return dict(row)


async def send_staff_message(
    session: AsyncSession,
    tenant_id: int,
    conversation_id: int,
    sender_user_id: str,
    body: str,
    *,
    group_id: int | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """`group_id`/`batch_id` (0056/0057) are set only by `messaging_groups.send_group_message` —
    an ordinary reply (the composer, "New") never passes either."""
    conversation = await get_conversation_for_tenant(session, tenant_id, conversation_id)
    return await _insert_message(
        session,
        tenant_id,
        conversation_id,
        conversation["contact_id"],
        "staff",
        sender_user_id,
        body,
        group_id=group_id,
        batch_id=batch_id,
    )


async def send_staff_message_for_request_item(
    session: AsyncSession,
    tenant_id: int,
    conversation_id: int,
    sender_user_id: str,
    body: str,
    request_item_id: int,
    request_item_uid: str,
    request_item_title: str,
    request_item_action: str,
    *,
    group_id: int | None = None,
    batch_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """`create_request`'s chat echo — same as `send_staff_message`, but links the message to
    the request item it announces, so the client's thread can render a clickable "upload" card
    instead of plain text. Returns `(conversation, message)`; the caller (app.documents.service)
    needs the conversation to push to both sides. `group_id`/`batch_id` (0056/0057) are set only
    when the request was raised against a whole group (`messaging_groups.create_group_request`).

    `_insert_message` always comes back with `request_item: None` (it only stamps the FK
    column; a plain send never has a request item to describe) — `send_client_attachment`
    fills it in after the fact for the same reason, and this is that same fix for the OTHER
    message type that carries one. Without it, the request's OWN immediate response and the
    live WS push both rendered as plain "Requested: {title}" text (no card, no upload
    affordance) until whoever was looking at it reloaded and `list_messages`' join backfilled
    it — a freshly raised request is always `pending`, so that status needs no query."""
    conversation = await get_conversation_for_tenant(session, tenant_id, conversation_id)
    message = await _insert_message(
        session,
        tenant_id,
        conversation_id,
        conversation["contact_id"],
        "staff",
        sender_user_id,
        body,
        request_item_id=request_item_id,
        group_id=group_id,
        batch_id=batch_id,
    )
    message["request_item"] = {
        "uid": request_item_uid,
        "title": request_item_title,
        "action": request_item_action,
        "status": "pending",
    }
    return conversation, message


async def send_staff_attachment(
    session: AsyncSession,
    tenant_id: int,
    conversation_id: int,
    sender_user_id: str,
    body: str | None,
    files: list[StagedFile],
    *,
    group_id: int | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """The attachment counterpart to `send_staff_message`. `group_id`/`batch_id` (0056/0057) are
    set only by `messaging_groups.send_group_attachment`."""
    conversation = await get_conversation_for_tenant(session, tenant_id, conversation_id)
    message = await _insert_message(
        session,
        tenant_id,
        conversation_id,
        conversation["contact_id"],
        "staff",
        sender_user_id,
        body,
        group_id=group_id,
        batch_id=batch_id,
    )
    message["attachments"] = [
        await _store_attachment(
            session,
            tenant_id,
            conversation["contact_id"],
            message["id"],
            name,
            blob,
            ctype,
            extension,
        )
        for name, blob, ctype, extension in files
    ]
    return message
