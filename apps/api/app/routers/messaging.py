"""Client<->adviser chat (schema `messaging`) — a prototype slice, docs/messaging.md.

Client endpoints run on `tenant_session` (RLS `authenticated`): a client can only ever reach
their own conversation, so there's no permission gate — the same "RLS is the gate" split
`routers/crm.py` uses. Staff endpoints run on `firm_session` (service_role) with an explicit
`require_permission` gate and `ctx.tenant_id` scoping on every call, the `routers/firm.py`
discipline.

The live half is a single-process mock — see `app/live_push.py`. It is served over
Server-Sent Events (`/messaging/events`), redeeming a single-use ticket minted by
`POST /messaging/ticket`.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy import text as sql_text  # `send_thread_attachment` has its own `text` param
from sqlalchemy.ext.asyncio import AsyncSession

from app import live_push, messaging_groups, storage, tenant_storage
from app import messaging as messaging_mod
from app.audit_actions import writes_no_row
from app.auth import require_permission
from app.db import (
    FirmContext,
    target_tenant_read_session,
)
from app.db import (
    target_firm_session as firm_session,
)
from app.db import (
    target_tenant_session as tenant_session,
)
from app.documents import service as docs_service
from app.permissions import DOCUMENTS_SEND, MESSAGES_READ, MESSAGES_WRITE
from app.routers.documents import CappedBodyRoute, SingleUploadRoute

# ``CappedBodyRoute`` caps every route's body at the ASGI-receive layer (router default: the JSON
# ceiling); the two attachment-upload routes below opt into the larger ``SingleUploadRoute``
# ceiling explicitly (registered imperatively — the decorator form can't pass a route class). See
# the long note in routers/documents.py for why this can't just be an in-handler size check.
router = APIRouter(tags=["messaging"], route_class=CappedBodyRoute)


class SendMessageBody(BaseModel):
    text: str


class OpenConversationBody(BaseModel):
    contact_id: int


class CreateGroupBody(BaseModel):
    name: str
    contact_ids: list[int]


class GroupRequestItemBody(BaseModel):
    action: Literal["upload", "agree", "acknowledge", "complete_form"]
    title: str
    category: str | None = None
    format_hint: str | None = None
    target_document_uid: str | None = None
    form_uid: str | None = None


class CreateGroupRequestBody(BaseModel):
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    message: str | None = None
    due_date: str | None = None
    items: list[GroupRequestItemBody]


def _no_adviser(exc: messaging_mod.MessagingError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _require_body(body: SendMessageBody) -> str:
    text_value = body.text.strip()
    if not text_value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Empty message"
        )
    return text_value


def _oversize_attachment() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        detail=f"Attachment exceeds the {storage.MAX_DOCUMENT_BYTES // (1024 * 1024)} MiB limit.",
    )


async def _read_attachments(files: list[UploadFile]) -> list[messaging_mod.StagedFile]:
    """Read and validate every part, in the order they were picked.

    A message may carry SEVERAL files (0123). Validation stays per file and still refuses the whole
    request on the first bad one: a partial send would leave the sender's note attached to some of
    what they chose, with no way to tell which.
    """
    staged: list[messaging_mod.StagedFile] = []
    for file in files:
        data, content_type, ext = await _read_attachment(file)
        staged.append((_attachment_filename(file), data, content_type, ext))
    return staged


async def _read_attachment(file: UploadFile) -> tuple[bytes, str, str]:
    """The upload's bytes (capped) + sniffed (content_type, ext) — magic-byte checked, never
    the client's declared Content-Type, same rule `storage.validate_upload` uses for documents.
    Raises a typed HTTPException so both send endpoints below get identical error shapes."""
    if file.size is not None and file.size > storage.MAX_DOCUMENT_BYTES:
        raise _oversize_attachment()
    try:
        data = await storage.read_capped(file)
    except storage.PayloadTooLarge as exc:
        raise _oversize_attachment() from exc
    try:
        content_type, ext = storage.validate_attachment_upload(data)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return data, content_type, ext


def _attachment_filename(file: UploadFile) -> str:
    """The uploader's original name for display + download disposition — basename only (never
    used to build the storage key itself, so this is hygiene, not a security boundary)."""
    return os.path.basename(file.filename or "attachment")[:255] or "attachment"


def _storage_unavailable(exc: messaging_mod.AttachmentStorageUnavailable) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))


def _attachment_response(data: bytes, content_type: str, filename: str) -> Response:
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": storage.content_disposition("attachment", filename)},
    )


# ---- client (the client portal's own chat) -------------------------------------------------


@router.get("/messaging/thread", name="messaging-thread")
async def get_thread(
    contact_id: int | None = None, session: AsyncSession = Depends(tenant_session)
) -> dict[str, Any]:
    try:
        conversation = await messaging_mod.get_or_create_conversation_for_caller(
            session, contact_id
        )
    except messaging_mod.MessagingError as exc:
        raise _no_adviser(exc) from exc
    messages = await messaging_mod.list_messages(session, conversation["id"])
    return {"conversation": conversation, "messages": messages}


@router.post("/messaging/thread/messages", name="messaging-thread-send")
async def send_thread_message(
    body: SendMessageBody,
    contact_id: int | None = None,
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    text_value = _require_body(body)
    try:
        conversation, message = await messaging_mod.send_client_message(
            session, text_value, contact_id
        )
    except messaging_mod.MessagingError as exc:
        raise _no_adviser(exc) from exc
    await live_push.push(
        live_push.staff_key(int(conversation["tenant_id"])),
        {"type": "message", "conversation_id": conversation["id"], "message": message},
    )
    return message


@router.post("/messaging/thread/read", name="messaging-thread-read")
async def read_thread(
    contact_id: int | None = None, session: AsyncSession = Depends(tenant_session)
) -> dict[str, Any]:
    try:
        conversation = await messaging_mod.get_or_create_conversation_for_caller(
            session, contact_id
        )
    except messaging_mod.MessagingError as exc:
        raise _no_adviser(exc) from exc
    await messaging_mod.mark_read(session, int(conversation["id"]), "client")
    return {"ok": True}


async def send_thread_attachment(
    file: list[UploadFile] = File(...),
    text: str = Form(""),
    request_item_uid: str | None = Form(None),
    contact_id: int | None = None,
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """One file per message, staged client-side — the attachment counterpart to
    `send_thread_message`. `text` may be blank (attachment-only).

    `request_item_uid`, when present, means this upload is fulfilling a document request raised
    in this same thread (the rich "upload" card a client sees for a pending item) — resolved and
    ownership-checked BEFORE the attachment is even validated, so a stale/foreign/already-
    fulfilled item 404s instead of silently sending a plain attachment that never marks anything
    fulfilled. `group_id` on the item's PARENT REQUEST (0058) means the ask was raised via a
    group — this fulfilment gets the same tag (plus its own fresh `batch_id`: this is one
    client's own reply, not a fan-out) so it surfaces in that group's feed too, as its own entry
    naming this client, mirroring the request that asked for it."""
    request_item = None
    if request_item_uid:
        request_item = await docs_service.find_own_pending_upload_item(session, request_item_uid)
        if request_item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That request is no longer waiting on you.",
            )
    group_id = request_item["group_id"] if request_item else None
    batch_id = str(uuid.uuid4()) if group_id is not None else None
    staged = await _read_attachments(file)
    try:
        conversation, message = await messaging_mod.send_client_attachment(
            session,
            text.strip() or None,
            staged,
            request_item=request_item,
            group_id=group_id,
            batch_id=batch_id,
            contact_id=contact_id,
        )
    except messaging_mod.AttachmentStorageUnavailable as exc:
        raise _storage_unavailable(exc) from exc
    except messaging_mod.MessagingError as exc:
        raise _no_adviser(exc) from exc
    if request_item is not None:
        await docs_service.mark_request_item_fulfilled_via_chat(
            int(request_item["tenant_id"]), int(request_item["id"])
        )
    if group_id is None:
        await live_push.push(
            live_push.staff_key(int(conversation["tenant_id"])),
            {"type": "message", "conversation_id": conversation["id"], "message": message},
        )
    else:
        contact_name = (
            await session.execute(
                sql_text("SELECT name FROM crm.contacts WHERE id = :cid"),
                {"cid": conversation["contact_id"]},
            )
        ).scalar_one_or_none()
        batch = messaging_groups.shape_batch(
            [{**message, "contact_id": conversation["contact_id"], "contact_name": contact_name}]
        )
        await live_push.push(
            live_push.staff_key(int(conversation["tenant_id"])),
            {"type": "group_message", "group_id": group_id, "message": batch},
        )
    return message


router.add_api_route(
    "/messaging/thread/attachments",
    send_thread_attachment,
    methods=["POST"],
    name="messaging-thread-send-attachment",
    route_class_override=SingleUploadRoute,
)


@router.get("/messaging/thread/attachments/{attachment_uid}", name="messaging-thread-attachment")
async def get_thread_attachment(
    attachment_uid: str, session: AsyncSession = Depends(tenant_session)
) -> Response:
    """Proxied stream, fetched only when the download button is clicked — RLS already
    restricts this to the caller's own contact."""
    try:
        attachment = await messaging_mod.get_attachment_for_caller(session, attachment_uid)
    except messaging_mod.MessagingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    store = await tenant_storage.get_tenant_store(session, attachment["tenant_id"])
    if store is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    data, content_type = await storage.get_tenant_object(store, attachment["storage_key"])
    return _attachment_response(
        data, content_type or attachment["content_type"], attachment["filename"]
    )


# ---- staff (the platform portal's inbox) ----------------------------------------------------


@router.get("/messaging/conversations", name="messaging-conversations")
async def list_conversations(
    q: str | None = None,
    contact_id: int | None = None,
    limit: int | None = None,
    ctx: FirmContext = Depends(firm_session),
) -> list[dict[str, Any]]:
    require_permission(ctx.principal, MESSAGES_READ)
    return await messaging_mod.list_conversations_for_tenant(
        ctx.session, ctx.tenant_id, q, contact_id=contact_id, limit=limit
    )


@router.post("/messaging/conversations", name="messaging-conversations-open")
async def open_conversation(
    body: OpenConversationBody, ctx: FirmContext = Depends(firm_session)
) -> dict[str, Any]:
    """Staff-initiated: open (or lazily create) the channel with a named client in the firm's
    own book — the client doesn't need to have ever logged in first, and the caller does not
    need to be that client's assigned adviser; `messages:write` is the gate."""
    require_permission(ctx.principal, MESSAGES_WRITE)
    try:
        return await messaging_mod.get_or_create_conversation_for_contact(
            ctx.session, ctx.tenant_id, body.contact_id
        )
    except messaging_mod.MessagingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/messaging/unread", name="messaging-unread")
async def conversations_unread(
    contact_id: int | None = None, ctx: FirmContext = Depends(firm_session)
) -> dict[str, bool]:
    """The Chats nav dot — a plain top-level path (never nested under `/messaging/conversations/
    {uid}`, which would risk this literally matching `{conversation_uid}` first). `contact_id`
    narrows it to one client, for that client record's own Chats section; omitted, it's the
    tenant-wide signal the global sidebar's Chats item uses."""
    require_permission(ctx.principal, MESSAGES_READ)
    return {
        "unread": await messaging_mod.has_unread_conversations(
            ctx.session, ctx.tenant_id, contact_id=contact_id
        )
    }


@router.get(
    "/messaging/conversations/{conversation_uid}/messages", name="messaging-conversation-messages"
)
async def get_conversation_messages(
    conversation_uid: str, ctx: FirmContext = Depends(firm_session)
) -> list[dict[str, Any]]:
    require_permission(ctx.principal, MESSAGES_READ)
    try:
        conversation = await messaging_mod.get_conversation_for_tenant_by_uid(
            ctx.session, ctx.tenant_id, conversation_uid
        )
    except messaging_mod.MessagingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return await messaging_mod.list_messages(
        ctx.session, int(conversation["id"]), include_group_messages=False
    )


@router.post(
    "/messaging/conversations/{conversation_uid}/messages", name="messaging-conversation-send"
)
async def send_conversation_message(
    conversation_uid: str, body: SendMessageBody, ctx: FirmContext = Depends(firm_session)
) -> dict[str, Any]:
    require_permission(ctx.principal, MESSAGES_WRITE)
    text_value = _require_body(body)
    try:
        conversation = await messaging_mod.get_conversation_for_tenant_by_uid(
            ctx.session, ctx.tenant_id, conversation_uid
        )
    except messaging_mod.MessagingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    conversation_id = int(conversation["id"])
    sender_user_id = await messaging_mod.current_user_uuid(ctx.session)
    message = await messaging_mod.send_staff_message(
        ctx.session, ctx.tenant_id, conversation_id, sender_user_id, text_value
    )
    # The push keeps `conversation_id`: it is an event field the socket's consumers compare against
    # a conversation they already hold, never a path. 0091's uid is for URLs.
    await live_push.push(
        live_push.client_key(int(conversation["contact_id"])),
        {"type": "message", "conversation_id": conversation_id, "message": message},
    )
    return message


async def send_conversation_attachment(
    conversation_uid: str,
    file: list[UploadFile] = File(...),
    text: str = Form(""),
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, Any]:
    """The attachment counterpart to `send_conversation_message`."""
    require_permission(ctx.principal, MESSAGES_WRITE)
    staged = await _read_attachments(file)
    try:
        conversation = await messaging_mod.get_conversation_for_tenant_by_uid(
            ctx.session, ctx.tenant_id, conversation_uid
        )
    except messaging_mod.MessagingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    conversation_id = int(conversation["id"])
    sender_user_id = await messaging_mod.current_user_uuid(ctx.session)
    try:
        message = await messaging_mod.send_staff_attachment(
            ctx.session,
            ctx.tenant_id,
            conversation_id,
            sender_user_id,
            text.strip() or None,
            staged,
        )
    except messaging_mod.AttachmentStorageUnavailable as exc:
        raise _storage_unavailable(exc) from exc
    await live_push.push(
        live_push.client_key(int(conversation["contact_id"])),
        {"type": "message", "conversation_id": conversation_id, "message": message},
    )
    return message


router.add_api_route(
    "/messaging/conversations/{conversation_uid}/attachments",
    send_conversation_attachment,
    methods=["POST"],
    name="messaging-conversation-send-attachment",
    route_class_override=SingleUploadRoute,
)


@router.get(
    "/messaging/conversations/{conversation_uid}/attachments/{attachment_uid}",
    name="messaging-conversation-attachment",
)
async def get_conversation_attachment(
    conversation_uid: str, attachment_uid: str, ctx: FirmContext = Depends(firm_session)
) -> Response:
    """Proxied stream, fetched only when the download button is clicked."""
    require_permission(ctx.principal, MESSAGES_READ)
    try:
        conversation = await messaging_mod.get_conversation_for_tenant_by_uid(
            ctx.session, ctx.tenant_id, conversation_uid
        )
        attachment = await messaging_mod.get_attachment_for_tenant(
            ctx.session, ctx.tenant_id, int(conversation["id"]), attachment_uid
        )
    except messaging_mod.MessagingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    store = await tenant_storage.get_tenant_store(ctx.session, ctx.tenant_id)
    if store is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    data, content_type = await storage.get_tenant_object(store, attachment["storage_key"])
    return _attachment_response(
        data, content_type or attachment["content_type"], attachment["filename"]
    )


@router.post("/messaging/conversations/{conversation_uid}/read", name="messaging-conversation-read")
async def read_conversation(
    conversation_uid: str, ctx: FirmContext = Depends(firm_session)
) -> dict[str, Any]:
    require_permission(ctx.principal, MESSAGES_READ)
    try:
        conversation = await messaging_mod.get_conversation_for_tenant_by_uid(
            ctx.session, ctx.tenant_id, conversation_uid
        )
    except messaging_mod.MessagingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await messaging_mod.mark_read(ctx.session, int(conversation["id"]), "staff")
    return {"ok": True}


# ---- groups (a staff-only saved shortcut to a set of clients — app/messaging_groups.py) -------


@router.get("/messaging/groups", name="messaging-groups")
async def list_groups(
    q: str | None = None, ctx: FirmContext = Depends(firm_session)
) -> list[dict[str, Any]]:
    require_permission(ctx.principal, MESSAGES_READ)
    return await messaging_groups.list_groups_for_tenant(ctx.session, ctx.tenant_id, q)


@router.post("/messaging/groups", name="messaging-groups-create")
async def create_group(
    body: CreateGroupBody, ctx: FirmContext = Depends(firm_session)
) -> dict[str, Any]:
    require_permission(ctx.principal, MESSAGES_WRITE)
    sender_user_id = await messaging_mod.current_user_uuid(ctx.session)
    try:
        return await messaging_groups.create_group(
            ctx.session, ctx.tenant_id, body.name, body.contact_ids, sender_user_id
        )
    except messaging_mod.MessagingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/messaging/groups/{group_uid}/messages", name="messaging-group-messages")
async def get_group_messages(
    group_uid: str, ctx: FirmContext = Depends(firm_session)
) -> dict[str, Any]:
    require_permission(ctx.principal, MESSAGES_READ)
    try:
        group = await messaging_groups.get_group_for_tenant_by_uid(
            ctx.session, ctx.tenant_id, group_uid
        )
        return await messaging_groups.get_group_feed(ctx.session, ctx.tenant_id, int(group["id"]))
    except messaging_mod.MessagingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/messaging/groups/{group_uid}/messages", name="messaging-group-send")
async def send_group_message(
    group_uid: str, body: SendMessageBody, ctx: FirmContext = Depends(firm_session)
) -> dict[str, Any]:
    """Fans one message out into each member's own real conversation
    (`messaging_groups.send_group_message`), tagged with `group_id`/`batch_id` (0056/0057).
    Pushes the PER-MEMBER breakdown to the CLIENT side only, one normal per-conversation
    "message" event each (a client's own live thread still needs it) — never to the staff side,
    whose ordinary Direct-message view now excludes group-tagged messages entirely
    (`messaging.list_messages`'s `include_group_messages=False`, `list_conversations_for_tenant`'s
    `last_message`); pushing a "message" event there too would just re-introduce a group send
    looking like a live DM. The single "group_message" event carries the aggregated BATCH (one
    entry, every recipient) — the shape an open group feed renders — not the per-member list."""
    require_permission(ctx.principal, MESSAGES_WRITE)
    text_value = _require_body(body)
    sender_user_id = await messaging_mod.current_user_uuid(ctx.session)
    try:
        group_id = int(
            (
                await messaging_groups.get_group_for_tenant_by_uid(
                    ctx.session, ctx.tenant_id, group_uid
                )
            )["id"]
        )
        per_member, batch = await messaging_groups.send_group_message(
            ctx.session, ctx.tenant_id, group_id, sender_user_id, text_value
        )
    except messaging_mod.MessagingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    for message in per_member:
        await live_push.push(
            live_push.client_key(int(message["contact_id"])),
            {"type": "message", "conversation_id": message["conversation_id"], "message": message},
        )
    await live_push.push(
        live_push.staff_key(ctx.tenant_id),
        {"type": "group_message", "group_id": group_id, "message": batch},
    )
    return batch


async def send_group_attachment(
    group_uid: str,
    file: list[UploadFile] = File(...),
    text: str = Form(""),
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, Any]:
    """The attachment counterpart to `send_group_message` — the same file, fanned into every
    member's own conversation (`messaging_groups.send_group_attachment`). Pushed exactly like a
    text group send: per-member "message" events to the CLIENT side, one aggregated
    "group_message" batch event to the staff side."""
    require_permission(ctx.principal, MESSAGES_WRITE)
    staged = await _read_attachments(file)
    sender_user_id = await messaging_mod.current_user_uuid(ctx.session)
    try:
        group_id = int(
            (
                await messaging_groups.get_group_for_tenant_by_uid(
                    ctx.session, ctx.tenant_id, group_uid
                )
            )["id"]
        )
        per_member, batch = await messaging_groups.send_group_attachment(
            ctx.session,
            ctx.tenant_id,
            group_id,
            sender_user_id,
            text.strip() or None,
            staged,
        )
    except messaging_mod.AttachmentStorageUnavailable as exc:
        raise _storage_unavailable(exc) from exc
    except messaging_mod.MessagingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    for message in per_member:
        await live_push.push(
            live_push.client_key(int(message["contact_id"])),
            {"type": "message", "conversation_id": message["conversation_id"], "message": message},
        )
    await live_push.push(
        live_push.staff_key(ctx.tenant_id),
        {"type": "group_message", "group_id": group_id, "message": batch},
    )
    return batch


router.add_api_route(
    "/messaging/groups/{group_uid}/attachments",
    send_group_attachment,
    methods=["POST"],
    name="messaging-group-send-attachment",
    route_class_override=SingleUploadRoute,
)


@router.post("/messaging/groups/{group_uid}/requests", name="messaging-group-create-request")
async def create_group_request(
    group_uid: str, body: CreateGroupRequestBody, ctx: FirmContext = Depends(firm_session)
) -> dict[str, Any]:
    """Raise the same ask against every member (`messaging_groups.create_group_request`) — one
    independent request per member, each fulfilled on its own. `documents:send`, not
    `messages:write`: a request lands in the client's portal and email, the same client-facing
    release act `POST /documents/requests` gates the same way. No WS push here (unlike a text/
    attachment send) — see the long comment on `documents.service.create_request`'s echo loop for
    why; the caller re-fetches the group's feed itself after this returns."""
    require_permission(ctx.principal, DOCUMENTS_SEND)
    sender_user_id = await messaging_mod.current_user_uuid(ctx.session)
    items = [
        docs_service.RequestItemSpec(
            action=i.action,
            title=i.title,
            category=i.category,
            format_hint=i.format_hint,
            target_document_uid=i.target_document_uid,
            form_uid=i.form_uid,
        )
        for i in body.items
    ]
    try:
        group_id = int(
            (
                await messaging_groups.get_group_for_tenant_by_uid(
                    ctx.session, ctx.tenant_id, group_uid
                )
            )["id"]
        )
        req_uids = await messaging_groups.create_group_request(
            ctx.session,
            ctx.tenant_id,
            group_id,
            sender_user_id,
            items,
            priority=body.priority,
            message=body.message,
            due_date=body.due_date,
        )
    except messaging_mod.MessagingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except docs_service.DocumentServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return {"request_uids": req_uids}


# ---- live push: ticket + SSE -------------------------------------------------------------------


@router.post("/messaging/ticket", name="ticket")
@writes_no_row
async def mint_ticket(
    contact_id: int | None = None, session: AsyncSession = Depends(target_tenant_read_session)
) -> dict[str, str]:
    """Mints a short-lived, single-use ticket for the SSE stream that follows — the caller's own
    bearer token never reaches browser JS (see app/live_push.py).

    ``contact_id`` selects which of the caller's linked contacts to subscribe as (the
    active-contact selector) — must match whatever ``GET /messaging/thread`` was called with,
    or the stream subscribes to a different contact's channel than the thread it's pushing
    into."""
    # "is contact_scope() non-empty" is NOT the signal — a staff adviser's OWN ASSIGNED CLIENTS
    # are folded into their contact_scope too (see app.messaging._is_client_portal_session).
    # `auth.portal() = 'client'` is, with `auth.role() = 'client'` kept as the same legacy
    # fallback `auth.tenant_lineage()` keeps — a real client's WorkOS role slug is never
    # literally 'client' (e.g. 'org-advised-client'), so role() alone refuses every real login.
    is_client = (
        await session.execute(text("SELECT auth.portal() = 'client' OR auth.role() = 'client'"))
    ).scalar()
    if is_client:
        contact_ids = (await session.execute(text("SELECT auth.contact_scope()"))).scalar()
        if not contact_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="No linked contact for this session"
            )
        active = contact_id if contact_id is not None and contact_id in contact_ids else None
        key = live_push.client_key(active if active is not None else int(contact_ids[0]))
    else:
        tenant_id = (await session.execute(text("SELECT auth.tenant_id()"))).scalar()
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="No tenant context for this session"
            )
        key = live_push.staff_key(int(tenant_id))
    return {"ticket": live_push.mint_ticket(key)}


@router.get("/messaging/events", name="messaging-events", include_in_schema=False)
async def messaging_events(request: Request) -> StreamingResponse:
    """The live channel's wire form — fed by `live_push.push`.

    The ticket arrives in a private header (`x-realtime-ticket`), not the URL, and is redeemed
    here exactly as the upgrade redeems it: single-use, 30 seconds, minted only by an
    authenticated `POST /messaging/ticket` that decided the KEY. So authorisation is settled
    before the first byte, and a missing or spent ticket is a plain 401 rather than a stream that
    opens and then says nothing.

    **No DB dependency, deliberately.** A session acquired here would be held for the life of the
    connection — hours, per reader — and the pool would be exhausted by people doing nothing. The
    ticket store is in-memory and the key is already decided, so this endpoint needs no database
    at all; the request that minted the ticket did the reading.
    """
    key = live_push.redeem_ticket(request.headers.get(live_push.TICKET_HEADER, ""))
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired realtime ticket"
        )
    subscriber = live_push.subscribe(key)
    return StreamingResponse(
        live_push.sse_stream(subscriber),
        media_type="text/event-stream",
        headers=live_push.SSE_HEADERS,
    )
