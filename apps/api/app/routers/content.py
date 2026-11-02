"""Staff CMS for news/articles/guides, plus the client-portal read surface under /crm/content."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, overload

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import content as svc
from app import storage, tenant_storage
from app.auth import Principal, get_current_principal, require_permission
from app.db import book_session
from app.db import target_tenant_session as tenant_session
from app.ids import new_id
from app.permissions import CONTENT_READ, CONTENT_WRITE
from app.routers.documents import CappedBodyRoute, SingleUploadRoute

router = APIRouter(prefix="/content", tags=["content"], route_class=CappedBodyRoute)
uploads = APIRouter(prefix="/content", tags=["content"], route_class=SingleUploadRoute)
crm_router = APIRouter(prefix="/crm/content", tags=["crm"])

Kind = Literal["news", "article", "guide"]
Audience = Literal["all", "individual", "organisation"]


class CategoryOut(BaseModel):
    uid: str
    name: str
    slug: str
    sort_order: int
    created_at: datetime


class CreateCategory(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ContentItemOut(BaseModel):
    uid: str
    kind: str
    title: str
    slug: str
    summary: str | None
    status: str
    audience: str
    service_types: list[str]
    sub_types: list[str]
    published_at: datetime | None
    expires_at: datetime | None
    hero_url: str | None = None
    has_file: bool = False
    category_uid: str | None = None
    category_name: str | None = None
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None


class ContentItemDetailOut(ContentItemOut):
    body_html: str | None = None
    schema_content: dict[str, Any] | None = Field(default=None, alias="schema")
    file_content_type: str | None = None
    file_size: int | None = None
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class CreateContentItem(BaseModel):
    kind: Kind
    title: str = Field(min_length=1, max_length=200)


class UpdateContentItem(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=2000)
    audience: Audience | None = None
    service_types: list[str] | None = None
    sub_types: list[str] | None = None
    category_uid: str | None = None
    expires_at: datetime | None = None
    clear_expires: bool = False
    schema_content: dict[str, Any] | None = Field(default=None, alias="schema")
    hero_url: str | None = None
    model_config = ConfigDict(populate_by_name=True)


class ClientContentCardOut(BaseModel):
    uid: str
    kind: str
    title: str
    slug: str
    summary: str | None
    published_at: datetime | None
    hero_url: str | None
    category_uid: str | None
    category_name: str | None
    has_file: bool = False


class ClientContentListOut(BaseModel):
    items: list[ClientContentCardOut]
    total: int


class ClientContentDetailOut(ClientContentCardOut):
    body_html: str | None = None
    file_content_type: str | None = None


def _raise(exc: svc.ContentError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail)


async def _ctx(session: AsyncSession) -> tuple[int, str]:
    row = (
        (await session.execute(text("SELECT auth.tenant_id() AS t, auth.uid() AS u")))
        .mappings()
        .one()
    )
    return int(row["t"]), str(row["u"])


@overload
def _item_out(row: dict[str, Any], *, detail: Literal[True]) -> ContentItemDetailOut: ...
@overload
def _item_out(row: dict[str, Any], *, detail: Literal[False]) -> ContentItemOut: ...
def _item_out(row: dict[str, Any], *, detail: bool) -> ContentItemOut | ContentItemDetailOut:
    hero = row.get("hero_url")
    base = {
        "uid": row["uid"],
        "kind": row["kind"],
        "title": row["title"],
        "slug": row["slug"],
        "summary": row.get("summary"),
        "status": row["status"],
        "audience": row["audience"],
        "service_types": list(row.get("service_types") or []),
        "sub_types": list(row.get("sub_types") or []),
        "published_at": row.get("published_at"),
        "expires_at": row.get("expires_at"),
        "hero_url": storage.public_asset_src(hero) if isinstance(hero, str) else None,
        "has_file": bool(row.get("has_file") or row.get("file_key")),
        "category_uid": row.get("category_uid"),
        "category_name": row.get("category_name"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "created_by": row.get("created_by"),
    }
    if not detail:
        return ContentItemOut(**base)
    raw_schema = row.get("schema")
    schema: dict[str, Any] = dict(raw_schema) if isinstance(raw_schema, dict) else {}
    if isinstance(schema.get("html"), str):
        schema = {**schema, "html": storage.proxy_asset_urls(schema["html"])}
    body_html = row.get("body_html")
    return ContentItemDetailOut(
        **base,
        body_html=storage.proxy_asset_urls(body_html) if isinstance(body_html, str) else None,
        schema_content=schema,
        file_content_type=row.get("file_content_type"),
        file_size=row.get("file_size"),
    )


@router.get("/categories", name="list-content-categories")
async def list_categories(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[CategoryOut]:
    require_permission(principal, CONTENT_READ)
    return [CategoryOut(**r) for r in await svc.list_categories(session)]


@router.post("/categories", name="create-content-category", status_code=status.HTTP_201_CREATED)
async def create_category(
    body: CreateCategory,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> CategoryOut:
    require_permission(principal, CONTENT_WRITE)
    tenant_id, _ = await _ctx(session)
    try:
        created = await svc.create_category(session, tenant_id, body.name)
    except svc.ContentError as exc:
        _raise(exc)
    return CategoryOut(**created)


@router.get("/items", name="list-content-items")
async def list_items(
    kind: Annotated[list[Kind] | None, Query()] = None,
    item_status: Annotated[list[str] | None, Query(alias="status")] = None,
    category: str | None = None,
    q: str | None = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[ContentItemOut]:
    require_permission(principal, CONTENT_READ)
    rows = await svc.list_items(session, kind=kind, status=item_status, category_uid=category, q=q)
    return [_item_out(r, detail=False) for r in rows]


@router.post("/items", name="create-content-item", status_code=status.HTTP_201_CREATED)
async def create_item(
    body: CreateContentItem,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> ContentItemDetailOut:
    require_permission(principal, CONTENT_WRITE)
    tenant_id, actor = await _ctx(session)
    try:
        created = await svc.create_item(session, tenant_id, actor, kind=body.kind, title=body.title)
    except svc.ContentError as exc:
        _raise(exc)
    return _item_out(created, detail=True)


@router.get("/items/{uid}", name="get-content-item")
async def get_item(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> ContentItemDetailOut:
    require_permission(principal, CONTENT_READ)
    row = await svc.get_item(session, uid, include_schema=True)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return _item_out(row, detail=True)


@router.patch("/items/{uid}", name="update-content-item")
async def update_item(
    uid: str,
    body: UpdateContentItem,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> ContentItemDetailOut:
    require_permission(principal, CONTENT_WRITE)
    tenant_id, _ = await _ctx(session)
    try:
        updated = await svc.update_item(
            session,
            uid,
            tenant_id,
            title=body.title,
            summary=body.summary,
            audience=body.audience,
            service_types=body.service_types,
            sub_types=body.sub_types,
            category_uid=body.category_uid,
            clear_category=body.category_uid == "",
            expires_at=body.expires_at,
            clear_expires=body.clear_expires,
            schema=body.schema_content,
            hero_url=body.hero_url,
        )
    except svc.ContentError as exc:
        _raise(exc)
    return _item_out(updated, detail=True)


@router.post("/items/{uid}/publish", name="publish-content-item")
async def publish_item(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> ContentItemDetailOut:
    require_permission(principal, CONTENT_WRITE)
    _, actor = await _ctx(session)
    try:
        published = await svc.publish_item(session, uid, actor)
    except svc.ContentError as exc:
        _raise(exc)
    return _item_out(published, detail=True)


@router.post("/items/{uid}/archive", name="archive-content-item")
async def archive_item(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> ContentItemDetailOut:
    require_permission(principal, CONTENT_WRITE)
    try:
        archived = await svc.archive_item(session, uid)
    except svc.ContentError as exc:
        _raise(exc)
    return _item_out(archived, detail=True)


@router.get("/items/{uid}/file", name="get-content-item-file")
async def get_item_file(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> Response:
    require_permission(principal, CONTENT_READ)
    row = await svc.get_item(session, uid, include_schema=False)
    if row is None or not row.get("file_key"):
        raise HTTPException(status_code=404, detail="Not found.")
    tenant_id, _ = await _ctx(session)
    store = await tenant_storage.get_tenant_store(session, tenant_id)
    if store is None:
        raise HTTPException(status_code=503, detail="Object storage is not configured.")
    data, content_type = await storage.get_tenant_object(store, str(row["file_key"]))
    return Response(
        content=data,
        media_type=row.get("file_content_type") or content_type,
        headers={
            "Content-Disposition": f'inline; filename="{row["slug"]}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )


@uploads.post("/items/{uid}/guide", name="upload-content-guide")
async def upload_guide(
    uid: str,
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> ContentItemDetailOut:
    require_permission(principal, CONTENT_WRITE)
    tenant_id, _ = await _ctx(session)
    data = await storage.read_capped(file)
    try:
        content_type, ext = storage.validate_upload(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if content_type != "application/pdf":
        raise HTTPException(status_code=422, detail="Guides must be a PDF.")
    store = await tenant_storage.get_tenant_store(session, tenant_id)
    if store is None:
        raise HTTPException(
            status_code=503, detail="Object storage is not configured for this firm."
        )
    key = f"content/{uid}/guide.{ext}"
    await storage.put_tenant_object(store, key, data, content_type)
    try:
        updated = await svc.set_guide_file(
            session, uid, file_key=key, content_type=content_type, size=len(data)
        )
    except svc.ContentError as exc:
        _raise(exc)
    return _item_out(updated, detail=True)


@uploads.post("/items/{uid}/hero", name="upload-content-hero")
async def upload_hero(
    uid: str,
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> ContentItemDetailOut:
    require_permission(principal, CONTENT_WRITE)
    tenant_id, _ = await _ctx(session)
    data = await storage.read_capped(file)
    try:
        content_type, _ext = storage.validate_upload(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if content_type not in ("image/png", "image/jpeg"):
        raise HTTPException(status_code=422, detail="Hero image must be a PNG or JPEG.")
    kind = f"content-{uid}-hero"
    try:
        url = await storage.put_branding_asset(
            session, tenant_id, kind, data, content_type, proxy=True
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        updated = await svc.update_item(session, uid, tenant_id, hero_url=url)
    except svc.ContentError as exc:
        _raise(exc)
    return _item_out(updated, detail=True)


@uploads.post("/items/{uid}/images", name="upload-content-image")
async def upload_image(
    uid: str,
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, str]:
    require_permission(principal, CONTENT_WRITE)
    tenant_id, _ = await _ctx(session)
    existing = await svc.get_item(session, uid, include_schema=False)
    if existing is None:
        raise HTTPException(status_code=404, detail="Not found.")
    data = await storage.read_capped(file)
    try:
        content_type, _ext = storage.validate_upload(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if content_type not in ("image/png", "image/jpeg"):
        raise HTTPException(status_code=422, detail="Images must be a PNG or JPEG.")
    kind = f"content-{uid}-{new_id()}"
    try:
        url = await storage.put_branding_asset(
            session, tenant_id, kind, data, content_type, proxy=True
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"src": url}


@crm_router.get("", name="list-crm-content")
async def list_crm_content(
    kind: Kind | None = None,
    category: str | None = None,
    q: str | None = None,
    limit: int = 25,
    offset: int = 0,
    contact_id: int | None = None,
    session: AsyncSession = Depends(book_session),
) -> ClientContentListOut:
    contact = await svc.contact_audience(session, contact_id)
    capped = max(1, min(limit, 50))
    off = max(0, offset)
    rows, total = await svc.published_for_contact(
        session, contact, kind=kind, category_uid=category, q=q, limit=capped, offset=off
    )
    return ClientContentListOut(
        items=[
            ClientContentCardOut(
                uid=r["uid"],
                kind=r["kind"],
                title=r["title"],
                slug=r["slug"],
                summary=r.get("summary"),
                published_at=r.get("published_at"),
                hero_url=(
                    storage.public_asset_src(r["hero_url"])
                    if isinstance(r.get("hero_url"), str)
                    else None
                ),
                category_uid=r.get("category_uid"),
                category_name=r.get("category_name"),
                has_file=bool(r.get("has_file")),
            )
            for r in rows
        ],
        total=total,
    )


@crm_router.get("/{uid}", name="get-crm-content")
async def get_crm_content(
    uid: str,
    contact_id: int | None = None,
    session: AsyncSession = Depends(book_session),
) -> ClientContentDetailOut:
    contact = await svc.contact_audience(session, contact_id)
    row = await svc.published_detail(session, uid, contact)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return ClientContentDetailOut(
        uid=row["uid"],
        kind=row["kind"],
        title=row["title"],
        slug=row["slug"],
        summary=row.get("summary"),
        published_at=row.get("published_at"),
        hero_url=(
            storage.public_asset_src(row["hero_url"])
            if isinstance(row.get("hero_url"), str)
            else None
        ),
        category_uid=row.get("category_uid"),
        category_name=row.get("category_name"),
        has_file=bool(row.get("file_key")),
        body_html=(
            storage.proxy_asset_urls(row["body_html"])
            if isinstance(row.get("body_html"), str)
            else None
        ),
        file_content_type=row.get("file_content_type"),
    )


@crm_router.get("/{uid}/file", name="get-crm-content-file")
async def get_crm_content_file(
    uid: str,
    contact_id: int | None = None,
    session: AsyncSession = Depends(book_session),
) -> Response:
    contact = await svc.contact_audience(session, contact_id)
    row = await svc.published_detail(session, uid, contact)
    if row is None or not row.get("file_key"):
        raise HTTPException(status_code=404, detail="Not found.")
    tenant_id = (await session.execute(text("SELECT auth.tenant_id()"))).scalar_one()
    store = await tenant_storage.get_tenant_store(session, int(tenant_id))
    if store is None:
        raise HTTPException(status_code=503, detail="Object storage is not configured.")
    data, content_type = await storage.get_tenant_object(store, str(row["file_key"]))
    return Response(
        content=data,
        media_type=row.get("file_content_type") or content_type,
        headers={
            "Content-Disposition": f'inline; filename="{row["slug"]}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )
