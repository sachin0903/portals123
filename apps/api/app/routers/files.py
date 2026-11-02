"""Files — supporting client uploads (passport, ID, proof of address), distinct from documents.

A file is NOT a financial document: it's a supporting artifact about a client, stored in the same
blob layer (documents.files) but recorded in documents.attachments with its own lifecycle
(upload-only, never generated). Runs under tenant_session (RLS: staff by tenant, the linked client
sees their own via contact_scope); gated by the documents permissions (same people manage both).
"""

import hashlib
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import listing, storage, tenant_storage
from app.auth import Principal, get_current_principal, require_permission
from app.db import target_tenant_session as tenant_session
from app.ids import new_id
from app.permissions import DOCUMENTS_READ, DOCUMENTS_WRITE
from app.routers.documents import CappedBodyRoute, SingleUploadRoute

# ``CappedBodyRoute`` puts a hard ceiling on the request body at the ASGI-receive layer, the
# only layer that sees the wire bytes: Starlette spools FILE parts to disk with NO size limit, and
# does it before this module's handler (or its dependencies) run. See the long note in
# routers/documents.py. The router default is the JSON ceiling; ``/files/upload`` is registered
# imperatively below with ``SingleUploadRoute`` (one file per request: the per-document cap plus
# framing) so the allowance does not leak onto the list/get/delete routes, which carry no body.
router = APIRouter(prefix="/files", tags=["files"], route_class=CappedBodyRoute)

# The kinds of supporting file the firm can classify an upload as (the client never classifies).
ATTACHMENT_TYPES = {"passport", "national_id", "proof_of_address", "other"}

_SELECT = (
    "SELECT a.uid, a.name, a.attachment_type, a.contact_id, a.created_at, "
    "       f.content_type, f.size_bytes, f.storage_key, u.name AS created_by, "
    # Composed, not bare `c.name` -- see documents.py::_SELECT for why (crm.contacts.name is
    # nullable; an individual's name is first_name/surname), which left a supporting file's
    # client blank in the library's Client column.
    "       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), "
    "                c.name) AS contact_name, "
    # The client's UID as well as their name: the library filters by uid (two clients can share a
    # name), and only the name is a display string.
    "       c.uid AS contact_uid "
    "FROM documents.attachments a "
    "JOIN documents.files f ON f.id = a.file_id "
    "LEFT JOIN auth.users u ON u.id = a.uploaded_by "
    "LEFT JOIN crm.contacts c ON c.id = a.contact_id "
)


class FileOut(BaseModel):
    uid: str
    name: str | None
    attachment_type: str
    content_type: str
    size_bytes: int
    contact_id: int | None
    contact_name: str | None
    contact_uid: str | None = None
    created_at: datetime
    created_by: str | None


def _to_out(row: Mapping[Any, Any]) -> FileOut:
    return FileOut(
        uid=row["uid"],
        name=row["name"],
        attachment_type=row["attachment_type"],
        content_type=row["content_type"],
        size_bytes=row["size_bytes"],
        contact_id=row["contact_id"],
        contact_name=row["contact_name"],
        contact_uid=row["contact_uid"],
        created_at=row["created_at"],
        created_by=row["created_by"],
    )


async def _ref(session: AsyncSession, uid: str) -> tuple[str, str, str | None, str] | None:
    """(storage_key, content_type, name, bucket) for a live file the caller may see; else None.

    The bucket comes from the row rather than from settings because a tenant's own objects live
    in its own bucket while everything filed before that lives in the shared one, and the key
    alone cannot say which: tenant keys carry no tenant segment (the bucket IS the tenant)."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT f.storage_key, f.content_type, a.name, f.bucket "
                    "FROM documents.attachments a JOIN documents.files f ON f.id = a.file_id "
                    "WHERE a.uid = :uid AND a.deleted_at IS NULL"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return (row["storage_key"], row["content_type"], row["name"], row["bucket"])


async def _assert_visible_contact(session: AsyncSession, contact_id: int) -> None:
    """422 unless contact_id is a crm.contacts row the caller may see (RLS-scoped)."""
    ok = (
        await session.execute(
            text("SELECT 1 FROM crm.contacts WHERE id = :cid"), {"cid": contact_id}
        )
    ).scalar_one_or_none()
    if ok is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Unknown client, or you don't have access to them.",
        )


def _oversize() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"File exceeds the {storage.MAX_DOCUMENT_BYTES // (1024 * 1024)} MiB limit.",
    )


async def _read_upload(file: UploadFile) -> bytes:
    """The upload's bytes, capped: the declared size first (cheap), then the real byte count as it
    streams. The declared-size check alone is bypassed by a chunked upload that sends no
    Content-Length (``file.size is None``) — that body used to be buffered whole before
    ``validate_upload`` could reject it."""
    if file.size is not None and file.size > storage.MAX_DOCUMENT_BYTES:
        raise _oversize()
    try:
        return await storage.read_capped(file)
    except storage.PayloadTooLarge as exc:
        raise _oversize() from exc


def _file_size(row: dict[str, Any]) -> float | None:
    """The file's stored size, or None when the column carries something unreadable as a number —
    same reader documents.py::_document_size uses on its own table, kept over here rather than
    imported because the two routers do not share a module for this (each already duplicates
    `_SELECT`/`_client_labels`/`_assert_visible_contact` the same way)."""
    value = row.get("size_bytes")
    return float(value) if isinstance(value, int | float) else None


# The same filters the documents library declares (documents.py::_DOCUMENT_FILTERS), reading this
# table's own columns. Supporting files arrive in that library beside documents, so both halves
# have to answer the same question or a filter would silently drop one kind.
_FILE_FILTERS: listing.Filters = {
    "type": lambda row, value: listing.matches(value, row.get("attachment_type")),
    "author": lambda row, value: listing.matches(value, row.get("created_by")),
    "client": lambda row, value: listing.matches(value, row.get("contact_uid")),
    "size": lambda row, value: listing.within_range(value, _file_size(row)),
    "created": lambda row, value: listing.within_range(value, row.get("created_at")),
}
_FILE_BOUNDS: dict[str, Callable[[dict[str, Any]], float | None]] = {
    "size": _file_size,
}
_FILE_FACETS: listing.Facets = [
    ("type", lambda row: row.get("attachment_type")),
    ("author", lambda row: row.get("created_by")),
    ("client", lambda row: row.get("contact_uid")),
]


def _client_labels(
    rows: list[dict[str, Any]], q: str | None, active: dict[str, str | list[str] | None]
) -> dict[str, str]:
    """Readable names for the client facet, whose values are uids — see documents.py for why they
    are built with the CLIENT filter lifted rather than read off the returned page."""
    needle = listing.clean(q)
    return {
        str(row["contact_uid"]): str(row["contact_name"] or row["contact_uid"])
        for row in rows
        if row.get("contact_uid")
        and listing.search(needle, row.get("name"))
        and all(
            predicate(row, active.get(key))
            for key, predicate in _FILE_FILTERS.items()
            if key != "client"
        )
    }


@router.get("", name="list-files")
async def list_files(
    contact_id: int | None = None,
    q: str | None = None,
    type: str | None = None,  # noqa: A002 - the wire name; `attachment_type` is the column
    author: str | None = None,
    client: str | None = None,
    size: Annotated[list[str] | None, Query()] = None,
    created: Annotated[list[str] | None, Query()] = None,
    limit: int | None = None,
    offset: int = 0,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[FileOut] | dict[str, object]:
    """The firm's files (RLS-scoped), newest first. Pass ``contact_id`` to scope to one client.

    ``q``/``type``/``author``/``client``/``size``/``created`` mirror ``GET /documents`` exactly,
    because the platform's documents library is ONE list built from both endpoints — a filter
    either narrows both halves or it quietly hides every supporting file. With ``limit`` the
    response is the listing envelope (``items``/``total``/``facets``/``facet_counts``/``bounds``);
    without it, the bare list every existing caller parses.
    """
    require_permission(principal, DOCUMENTS_READ)
    where = "a.deleted_at IS NULL"
    params: dict[str, object] = {}
    if contact_id is not None:
        where += " AND a.contact_id = :cid"
        params["cid"] = contact_id
    rows = (
        (await session.execute(text(_SELECT + f"WHERE {where} ORDER BY a.created_at DESC"), params))
        .mappings()
        .all()
    )
    shaped = [dict(r) for r in rows]
    active: dict[str, str | list[str] | None] = {
        "type": listing.clean(type),
        "author": listing.clean(author),
        "client": listing.clean(client),
        "size": listing.clean_range(size),
        "created": listing.clean_range(created),
    }
    rows_out, envelope = listing.apply(
        shaped,
        q=q,
        search_fields=("name",),
        filters=_FILE_FILTERS,
        facets=_FILE_FACETS,
        active=active,
        limit=limit,
        offset=offset,
        bounds=_FILE_BOUNDS,
    )
    if envelope is None:
        return [_to_out(r) for r in rows_out]
    return {
        **envelope,
        "items": [_to_out(r) for r in rows_out],
        "facet_labels": {"client": _client_labels(shaped, q, active)},
    }


async def upload_file(
    file: UploadFile = File(...),
    attachment_type: str = Form("other"),
    contact_id: int | None = Form(None),
    name: str | None = Form(None),
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> FileOut:
    """Upload a supporting file (passport, ID, proof of address): magic-byte sniff + size check,
    store in the bucket, record it in documents.attachments. The firm classifies it and may bind it
    to a client contact — never the client."""
    require_permission(principal, DOCUMENTS_WRITE)
    if attachment_type not in ATTACHMENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"attachment_type must be one of {sorted(ATTACHMENT_TYPES)}.",
        )
    if contact_id is not None:
        await _assert_visible_contact(session, contact_id)
    data = await _read_upload(file)
    try:
        content_type, ext = storage.validate_upload(data, file.content_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    tenant_id = (await session.execute(text("SELECT auth.tenant_id()"))).scalar_one_or_none()
    if tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tenant context")

    att_uid = new_id()
    bucket = await tenant_storage.document_bucket(session, tenant_id)
    key = storage.tenant_upload_key(att_uid, ext)
    try:
        await storage.put_document(key, data, content_type, bucket=bucket)
    except storage.StorageUnavailable as exc:
        # str(exc): the exception distinguishes an unconfigured store from an unreachable one, and
        # sending an operator to check S3_DOCUMENTS_BUCKET when it is set is a wasted afternoon.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    file_id = (
        await session.execute(
            text(
                "INSERT INTO documents.files "
                "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                "VALUES (auth.tenant_id(), :bucket, :key, :ct, :size, :sha) RETURNING id"
            ),
            {
                "bucket": bucket,
                "key": key,
                "ct": content_type,
                "size": len(data),
                "sha": hashlib.sha256(data).hexdigest(),
            },
        )
    ).scalar_one()

    await session.execute(
        text(
            "INSERT INTO documents.attachments "
            "  (uid, tenant_id, file_id, contact_id, attachment_type, name, uploaded_by) "
            "VALUES (:uid, auth.tenant_id(), :fid, :cid, :atype, :name, auth.uid())"
        ),
        {
            "uid": att_uid,
            "fid": file_id,
            "cid": contact_id,
            "atype": attachment_type,
            "name": name or file.filename,
        },
    )
    row = (
        (await session.execute(text(_SELECT + "WHERE a.uid = :uid"), {"uid": att_uid}))
        .mappings()
        .one_or_none()
    )
    if row is None:  # pragma: no cover - just inserted under this tenant
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Insert failed"
        )
    return _to_out(row)


# The one route on this router that carries a body worth measuring, so the only one that opts into
# the upload ceiling (registered imperatively because the decorator form cannot pass a route class).
router.add_api_route(
    "/upload",
    upload_file,
    methods=["POST"],
    name="upload-file",
    status_code=status.HTTP_201_CREATED,
    route_class_override=SingleUploadRoute,
)


@router.get("/{uid}", name="get-file")
async def get_file(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> FileOut:
    require_permission(principal, DOCUMENTS_READ)
    row = (
        (
            await session.execute(
                text(_SELECT + "WHERE a.uid = :uid AND a.deleted_at IS NULL"), {"uid": uid}
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return _to_out(row)


@router.get("/{uid}/url", name="file-url")
async def file_url(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, str]:
    """A short-lived (15-min) presigned download URL for the file's blob."""
    require_permission(principal, DOCUMENTS_READ)
    ref = await _ref(session, uid)
    if ref is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    key, _ct, _name, bucket = ref
    try:
        url = await storage.presigned_get_document(key, filename=_name or uid, bucket=bucket)
    except storage.StorageUnavailable as exc:
        # str(exc): the exception distinguishes an unconfigured store from an unreachable one, and
        # sending an operator to check S3_DOCUMENTS_BUCKET when it is set is a wasted afternoon.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return {"url": url}


# Content-types the browser previews inline; everything else downloads as an attachment.
_INLINE_TYPES = ("application/pdf", "image/png", "image/jpeg")


@router.get("/{uid}/content", name="file-content")
async def file_content(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> Response:
    """Proxy a supporting file's bytes through a SAME-ORIGIN stream (mirrors document_content): the
    object store is compose-internal, so a presigned URL wouldn't resolve in the browser. PDFs and
    images render inline; everything else downloads. RFC-6266 disposition so a non-latin-1 name
    doesn't crash the header encoder."""
    require_permission(principal, DOCUMENTS_READ)
    ref = await _ref(session, uid)
    if ref is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    key, ct, name, bucket = ref
    try:
        data, got_ct = await storage.get_document(key, bucket=bucket)
    except storage.ObjectMissing as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="File content not found"
        ) from exc
    except storage.StorageUnavailable as exc:
        # str(exc): the exception distinguishes an unconfigured store from an unreachable one, and
        # sending an operator to check S3_DOCUMENTS_BUCKET when it is set is a wasted afternoon.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    disposition = "inline" if ct in _INLINE_TYPES else "attachment"
    return Response(
        content=data,
        media_type=got_ct or ct,
        headers={"Content-Disposition": storage.content_disposition(disposition, name or uid)},
    )


@router.delete("/{uid}", name="delete-file")
async def delete_file(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> Response:
    """Soft-delete a file (hidden from lists; the blob is retained)."""
    require_permission(principal, DOCUMENTS_WRITE)
    row = (
        await session.execute(
            text(
                "UPDATE documents.attachments SET deleted_at = now() "
                "WHERE uid = :uid AND deleted_at IS NULL RETURNING id"
            ),
            {"uid": uid},
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
