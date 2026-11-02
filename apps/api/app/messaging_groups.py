"""Groups (schema `messaging`, 0084_messaging_groups) — a staff-only saved shortcut to a set of
clients, NOT a new kind of conversation.

Every client still has exactly one real conversation (`messaging.conversations`, owned by
`app/messaging.py`). Sending "to a group" fans out into each member's own conversation via the
existing send path (`messaging.send_staff_message`) — a group has no conversation row of its
own. Each fanned message IS tagged with `group_id` (0056), and a group's own feed
(`get_group_feed`) is scoped to exactly those tagged messages — never a member's whole 1:1
history, which would pull in unrelated DMs that just happen to involve the same client. A
client's own view is completely unaffected and carries no trace that grouping exists; the
adviser's ordinary Direct-message view of that same client, symmetrically, filters group-tagged
messages back OUT (`messaging.list_messages(..., include_group_messages=False)`,
`list_conversations_for_tenant`'s `last_message`) — a group broadcast is either read as a group
message or a direct one, never both.

A client's own message is group-tagged in exactly one case: fulfilling a request that was
itself raised via a group (`documents.requests.group_id`, 0058) — that fulfilment surfaces in
the group's feed as its own entry, tagged with the one member who sent it, the mirror image of
the request that asked for it. Nothing else a client sends is ever tagged, which is why groups
still carry no read-receipt/unread state: a plain client reply is never attributed to a group,
so there's no "has the client seen this" question the group itself needs to answer, and opening
one never touches any member's own conversation's read watermark.

Staff-only throughout: `messaging.groups`/`group_members` have no client-facing RLS branch at
all (unlike `messaging.conversations`, where a client legitimately reads their own slice). Every
function here runs on `firm_session` (service_role) and scopes explicitly to `tenant_id`, the
same discipline `app.messaging`'s staff-side functions already use.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import listing, messaging
from app.documents import service as docs_service
from app.messaging import MessagingError

_GROUP_COLUMNS = "id, uid, tenant_id, name, created_by, created_at, updated_at"


async def create_group(
    session: AsyncSession, tenant_id: int, name: str, contact_ids: list[int], created_by: str
) -> dict[str, Any]:
    """A named shortcut to one or more clients. Refuses an empty name/member list, and any
    contact id that isn't a real `crm.contacts` row in this tenant — the same "no
    caller-supplied id reaches an unrelated tenant" discipline as
    `documents.service.create_request`. Membership is not gated on `crm.client_advisers`:
    staff with `messages:write` can group (and message) any client in the firm they can
    name, the same rule as opening a direct thread."""
    clean_name = name.strip()
    if not clean_name:
        raise MessagingError("A group needs a name.")
    unique_ids = list(dict.fromkeys(contact_ids))
    if not unique_ids:
        raise MessagingError("A group needs at least one client.")
    known = set(
        (
            await session.execute(
                text("SELECT id FROM crm.contacts WHERE id = ANY(:cids) AND tenant_id = :t"),
                {"cids": unique_ids, "t": tenant_id},
            )
        )
        .scalars()
        .all()
    )
    missing = [c for c in unique_ids if c not in known]
    if missing:
        raise MessagingError("Unknown client, or you don't have access to them.")
    group_id = (
        await session.execute(
            text(
                "INSERT INTO messaging.groups (tenant_id, name, created_by) "
                "VALUES (:t, :n, :by) RETURNING id"
            ),
            {"t": tenant_id, "n": clean_name, "by": created_by},
        )
    ).scalar_one()
    for contact_id in unique_ids:
        await session.execute(
            text(
                "INSERT INTO messaging.group_members (tenant_id, group_id, contact_id) "
                "VALUES (:t, :g, :c)"
            ),
            {"t": tenant_id, "g": group_id, "c": contact_id},
        )
    return await get_group_for_tenant(session, tenant_id, group_id)


async def get_group_for_tenant(
    session: AsyncSession, tenant_id: int, group_id: int
) -> dict[str, Any]:
    return await _group_for_tenant(session, tenant_id, "id", group_id)


async def get_group_for_tenant_by_uid(
    session: AsyncSession, tenant_id: int, group_uid: str
) -> dict[str, Any]:
    """The same read, addressed the way a URL addresses it (0091) — see
    `messaging.get_conversation_for_tenant_by_uid` for why the boundary sits here and not deeper."""
    return await _group_for_tenant(session, tenant_id, "uid", group_uid)


async def _group_for_tenant(
    session: AsyncSession, tenant_id: int, column: str, value: object
) -> dict[str, Any]:
    if column not in {"id", "uid"}:  # pragma: no cover - the two callers above are the allowlist
        raise MessagingError("Group not found")
    group = (
        (
            await session.execute(
                # `column` from the two-value allowlist above, never a caller's string.
                text(
                    f"SELECT {_GROUP_COLUMNS} FROM messaging.groups "  # noqa: S608 - fixed columns list, not user input
                    f"WHERE tenant_id = :t AND {column} = :value"
                ),
                {"t": tenant_id, "value": value},
            )
        )
        .mappings()
        .first()
    )
    if group is None:
        raise MessagingError("Group not found")
    members = (
        (
            await session.execute(
                text(
                    "SELECT gm.contact_id, ct.name AS contact_name "
                    "FROM messaging.group_members gm "
                    "JOIN crm.contacts ct ON ct.id = gm.contact_id "
                    "WHERE gm.group_id = :g ORDER BY ct.name"
                ),
                # The resolved row's own id — the caller may have addressed it by either handle.
                {"g": group["id"]},
            )
        )
        .mappings()
        .all()
    )
    return {**dict(group), "members": [dict(m) for m in members]}


async def list_groups_for_tenant(
    session: AsyncSession, tenant_id: int, query: str | None = None
) -> list[dict[str, Any]]:
    """Every group in the tenant, with a `last_message`/`last_message_at` preview scoped to
    messages actually sent THROUGH this group (`m.group_id = g.id`) — never a member's wider 1:1
    history. No `unread`: every group-tagged message is staff-sent (only staff can trigger
    `send_group_message`), so there's nothing analogous to a client's unread reply to flag."""
    params: dict[str, Any] = {"tenant_id": tenant_id}
    where = "g.tenant_id = :tenant_id"
    if query and query.strip():
        # The Spotlight bar's match over `_search` (uid, name).
        await session.execute(text(listing.FUZZY_THRESHOLD_SQL))
        where += " AND " + listing.search_sql("g._search")
        params.update(listing.search_params(query))
    rows = (
        (
            await session.execute(
                # `where` is a fixed literal plus an optional ILIKE clause, both bound via `params`.
                text(
                    "SELECT g.id, g.uid, g.tenant_id, g.name, g.created_at, "  # noqa: S608
                    "last_msg.body AS last_message, last_msg.created_at AS last_message_at "
                    "FROM messaging.groups g "
                    "LEFT JOIN LATERAL ("
                    "  SELECT COALESCE(m.body, 'Attachment: ' || a.filename) AS body, m.created_at "
                    "  FROM messaging.messages m "
                    # One file names the preview, not one row per file — see the same LATERAL in
                    # messaging.py's conversation list.
                    "  LEFT JOIN LATERAL ("
                    "    SELECT filename FROM messaging.attachments "
                    "    WHERE message_id = m.id ORDER BY id LIMIT 1"
                    "  ) a ON true "
                    "  WHERE m.group_id = g.id "
                    "  ORDER BY m.created_at DESC, m.id DESC LIMIT 1"
                    ") last_msg ON true "
                    f"WHERE {where} "
                    "ORDER BY last_message_at DESC NULLS LAST"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def get_group_feed(session: AsyncSession, tenant_id: int, group_id: int) -> dict[str, Any]:
    """The log of messages actually sent THROUGH this group (`m.group_id = :g`) — never a
    member's wider 1:1 history, which would pull in DMs that have nothing to do with the group.
    Collapsed by `batch_id` (0057) into one entry PER SEND, tagged with every recipient, rather
    than one entry per recipient — three members getting the same broadcast is one thing that
    happened, not three. Members are returned alongside the messages (not just implied by who
    has been messaged) so a caller can still name every member of a brand-new, message-less
    group. `conversation_id` on each entry is one representative recipient's own copy (every
    member's row is identical content, so any one of them can serve the attachment download).
    A request raised via `create_group_request` and a client's fulfilment of it (0058) are both
    group-tagged, so both land here too, `request_item` read live off `documents.request_items`
    exactly as `messaging.list_messages` does for a 1:1 thread — the group feed renders the SAME
    rich request card, not a plainer stand-in."""
    group = await get_group_for_tenant(session, tenant_id, group_id)  # 404s an unknown group id
    rows = (
        (
            await session.execute(
                text(
                    "SELECT m.id, m.batch_id, m.conversation_id, c.uid AS conversation_uid, "  # noqa: S608
                    "m.sender_kind, m.sender_user_id, m.sender_is_support, "
                    # Whether the CALLER sent it — see app/messaging.py's note. This feed has
                    # its own projection, so the flag has to be added here as well; without it
                    # a group message read as nobody's.
                    "COALESCE(m.sender_user_id = auth.uid(), false) AS sent_by_me, "
                    "m.body, m.created_at, c.contact_id, ct.name AS contact_name, "
                    "COALESCE(m.sender_display_name, u.name) AS sender_name, "
                    "r.name AS sender_role, "
                    # Aggregated, not joined — a message may carry several files (0123), and a
                    # join would return each member's row once per file.
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
                    "JOIN crm.contacts ct ON ct.id = c.contact_id "
                    "LEFT JOIN auth.users u ON u.id = m.sender_user_id "
                    "LEFT JOIN auth.organisation_memberships om ON om.user_id = u.id "
                    "  AND om.tenant_id = m.tenant_id AND om.portal = 'platform' "
                    "LEFT JOIN auth.roles r ON r.id = om.role_id "
                    "LEFT JOIN documents.request_items ri ON ri.id = m.request_item_id "
                    "WHERE m.group_id = :g "
                    # `created_at` alone ties within one transaction — a group send fans out several
                    # rows under one `now()` (transaction-frozen) — `id` is the reliable secondary
                    # order key, but `batch_id` (not order) is what says "these rows are one send".
                    "ORDER BY m.created_at, m.id"
                ),
                {"g": group_id},
            )
        )
        .mappings()
        .all()
    )
    return {
        "members": group["members"],
        "messages": _shape_batches([dict(r) for r in rows]),
    }


def _shape_batches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the raw per-recipient rows by `batch_id`, preserving first-seen order, into one
    entry per send with a `recipients` list — the inverse of the fan-out `send_group_message`/
    `send_group_attachment`/`create_group_request` performs. Every row here has a `batch_id`
    (nothing else ever sets `group_id`), so there's no ungrouped fallback case to handle."""
    batches: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        key = str(row["batch_id"])
        if key not in batches:
            request_item = None
            if row["request_item_uid"] is not None:
                request_item = {
                    "uid": row["request_item_uid"],
                    "title": row["request_item_title"],
                    "action": row["request_item_action"],
                    "status": row["request_item_status"],
                }
            batches[key] = {
                "id": row["id"],
                "conversation_id": row["conversation_id"],
                "conversation_uid": row.get("conversation_uid"),
                "sender_kind": row["sender_kind"],
                "sender_user_id": row["sender_user_id"],
                "sender_is_support": bool(row.get("sender_is_support", False)),
                "sender_name": row["sender_name"],
                "sender_role": row["sender_role"],
                "body": row["body"],
                "created_at": row["created_at"],
                "attachments": list(row.get("attachments") or []),
                "request_item": request_item,
                "recipients": [],
            }
            order.append(key)
        batches[key]["recipients"].append(
            {"contact_id": row["contact_id"], "contact_name": row["contact_name"]}
        )
    return [batches[key] for key in order]


def shape_batch(per_member: list[dict[str, Any]]) -> dict[str, Any]:
    """The one-entry-per-send shape a live send returns immediately, built from the N messages
    just inserted (or, for a single client's own fulfilment, the ONE message it sent) — every
    field but `recipients` is identical across them, so the first stands in for the batch. Kept
    in sync with `_shape_batches` (the equivalent view read back from storage) so a live push and
    a page reload render identically. Public: `routers/messaging.py`'s client-attachment handler
    reuses this for a group-tagged fulfilment, wrapping its own single message the same way a
    fan-out wraps N."""
    first = per_member[0]
    return {
        "id": first["id"],
        "conversation_id": first["conversation_id"],
        "sender_kind": first["sender_kind"],
        "sender_user_id": first["sender_user_id"],
        "sender_is_support": bool(first.get("sender_is_support", False)),
        "sender_name": first.get("sender_name"),
        "sender_role": first.get("sender_role"),
        "body": first["body"],
        "created_at": first["created_at"],
        "attachments": list(first.get("attachments") or []),
        "request_item": first.get("request_item"),
        "recipients": [
            {"contact_id": m["contact_id"], "contact_name": m["contact_name"]} for m in per_member
        ],
    }


async def send_group_message(
    session: AsyncSession, tenant_id: int, group_id: int, sender_user_id: str, body: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fan out ONE message into each member's own conversation — literally `N` calls to the
    same `send_staff_message` a normal reply uses, all sharing one `batch_id` (0057) alongside
    `group_id` (0056) — a client can never tell the difference either way. Returns
    `(per_member, batch)`: `per_member` (one entry per recipient, tagged with their contact) is
    what the router pushes to each client's own `client_key`; `batch` (one entry, tagged with
    every recipient) is what a human looking at the group's feed should see — the same shape
    `get_group_feed` returns, so a live push and a page reload render identically."""
    group = await get_group_for_tenant(session, tenant_id, group_id)
    batch_id = str(uuid.uuid4())
    per_member: list[dict[str, Any]] = []
    for member in group["members"]:
        conversation = await messaging.get_or_create_conversation_for_contact(
            session, tenant_id, member["contact_id"]
        )
        message = await messaging.send_staff_message(
            session,
            tenant_id,
            conversation["id"],
            sender_user_id,
            body,
            group_id=group_id,
            batch_id=batch_id,
        )
        per_member.append(
            {
                **message,
                "contact_id": member["contact_id"],
                "contact_name": member["contact_name"],
            }
        )
    return per_member, shape_batch(per_member)


async def send_group_attachment(
    session: AsyncSession,
    tenant_id: int,
    group_id: int,
    sender_user_id: str,
    body: str | None,
    files: list[messaging.StagedFile],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The attachment counterpart to `send_group_message` — the SAME files, uploaded once per
    member into their own conversation (same discipline `send_staff_attachment` uses for a
    single conversation), tagged with one shared `batch_id`. Returns `(per_member, batch)` — see
    `send_group_message`."""
    group = await get_group_for_tenant(session, tenant_id, group_id)
    batch_id = str(uuid.uuid4())
    per_member: list[dict[str, Any]] = []
    for member in group["members"]:
        conversation = await messaging.get_or_create_conversation_for_contact(
            session, tenant_id, member["contact_id"]
        )
        message = await messaging.send_staff_attachment(
            session,
            tenant_id,
            conversation["id"],
            sender_user_id,
            body,
            files,
            group_id=group_id,
            batch_id=batch_id,
        )
        per_member.append(
            {
                **message,
                "contact_id": member["contact_id"],
                "contact_name": member["contact_name"],
            }
        )
    return per_member, shape_batch(per_member)


async def create_group_request(
    session: AsyncSession,
    tenant_id: int,
    group_id: int,
    requested_by: str,
    items: Sequence[docs_service.RequestItemSpec],
    *,
    priority: str = "normal",
    message: str | None = None,
    due_date: str | None = None,
) -> list[str]:
    """Raise the SAME ask against every member — one independent `documents.requests` row per
    member (each fulfilled on its own, exactly as a single-client request would be), not one
    request shared across the group. One `batch_id` PER ITEM (not one for the whole call) is
    generated up front and handed to every member's `create_request` call, so item `i`'s N
    per-member chat echoes collapse back into a single group-feed entry for that item —
    `create_request` sends one chat message per item, so batching at the whole-request level
    would wrongly merge DIFFERENT items' echoes into one entry instead."""
    group = await get_group_for_tenant(session, tenant_id, group_id)
    batch_ids = [str(uuid.uuid4()) for _ in items]
    req_uids: list[str] = []
    for member in group["members"]:
        req_uid = await docs_service.create_request(
            session,
            tenant_id=tenant_id,
            contact_id=member["contact_id"],
            items=items,
            priority=priority,
            message=message,
            due_date=due_date,
            requested_by=requested_by,
            group_id=group_id,
            batch_ids=batch_ids,
        )
        req_uids.append(req_uid)
    return req_uids
