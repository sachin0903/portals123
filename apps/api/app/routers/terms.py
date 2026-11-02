"""Legal terms catalogue — firm library, Graphene catalogue, outstanding + accept."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage, tenant_storage
from app import terms as svc
from app.auth import Principal, get_current_principal, is_system, require_permission
from app.db import book_session, system_transaction
from app.db import target_tenant_session as tenant_session
from app.permissions import (
    CONTACTS_READ,
    PLATFORM_TERMS_READ,
    PLATFORM_TERMS_WRITE,
    TERMS_READ,
    TERMS_WRITE,
)
from app.routers.documents import CappedBodyRoute, SingleUploadRoute

router = APIRouter(prefix="/terms", tags=["terms"], route_class=CappedBodyRoute)
uploads = APIRouter(prefix="/terms", tags=["terms"], route_class=SingleUploadRoute)
contacts_router = APIRouter(prefix="/contacts", tags=["terms"])

Owner = Literal["graphene", "tenant"]
Audience = Literal["client", "staff", "all"]
BodyKind = Literal["html", "pdf"]
RequiredAt = Literal["onboarding", "login"]


def _default_required_at() -> list[RequiredAt]:
    return ["onboarding"]


class TermOut(BaseModel):
    uid: str
    owner: str
    code: str
    title: str
    audience: str
    required_at: list[str]
    applicability: dict[str, Any]
    status: str
    body_kind: str
    published_at: datetime | None
    current_version_uid: str | None = None
    current_version_number: int | None = None
    current_version_published_at: datetime | None = None
    has_file: bool = False
    created_at: datetime
    updated_at: datetime


class TermDetailOut(TermOut):
    body_md: str | None = None
    body_html: str | None = None
    file_content_type: str | None = None
    file_size: int | None = None


class CreateTerm(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=64)
    audience: Audience = "client"
    required_at: list[RequiredAt] = Field(default_factory=_default_required_at)
    applicability: dict[str, Any] | None = None
    body_kind: BodyKind = "html"
    body_md: str | None = None


class UpdateTerm(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    audience: Audience | None = None
    required_at: list[RequiredAt] | None = None
    applicability: dict[str, Any] | None = None
    body_kind: BodyKind | None = None
    body_md: str | None = None


class AcceptTermBody(BaseModel):
    application_uid: str | None = None
    attested: bool = False
    note: str | None = None


class TermVersionOut(BaseModel):
    uid: str
    version_number: int
    title: str
    body_kind: str
    published_at: datetime
    content_hash: str


class AcceptedTermsOut(BaseModel):
    """The terms a client accepted, exactly as they read them: the accepted version's text, or a
    file (``has_file``) read from the acceptance's file route."""

    uid: str
    title: str
    code: str
    version_number: int
    accepted_at: datetime
    channel: str
    body_kind: str
    body_html: str | None = None
    has_file: bool
    evidence_document_uid: str | None = None


class AcceptanceOut(BaseModel):
    uid: str
    term_uid: str
    title: str
    code: str
    version_number: int
    version_uid: str
    accepted_at: datetime
    channel: str
    owner: str | None = None
    # The uploaded signed copy an in-person acceptance was recorded against; null for a portal
    # acceptance, which is its own evidence.
    evidence_document_uid: str | None = None


class OutstandingOut(BaseModel):
    items: list[TermDetailOut]


def _raise(exc: svc.TermsError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail)


async def _ctx(session: AsyncSession) -> tuple[int | None, str | None]:
    row = (
        (await session.execute(text("SELECT auth.tenant_id() AS t, auth.uid() AS u")))
        .mappings()
        .one()
    )
    tenant = row["t"]
    user = row["u"]
    return (int(tenant) if tenant is not None else None, str(user) if user is not None else None)


async def _contact_id(session: AsyncSession) -> int | None:
    return (
        await session.execute(
            text(
                "SELECT COALESCE("
                "  (SELECT cu.contact_id FROM crm.contact_users cu "
                "    WHERE cu.user_id = auth.uid() "
                "    ORDER BY (cu.capacity = 'self') DESC, cu.contact_id LIMIT 1),"
                "  (auth.contact_scope())[1])"
            )
        )
    ).scalar_one_or_none()


def _applicability(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("applicability")
    if isinstance(raw, dict):
        return raw
    return {"always": True}


def _term_out(row: dict[str, Any], *, detail: bool) -> TermOut | TermDetailOut:
    kind = row.get("published_body_kind") or row["body_kind"]
    html = row.get("published_body_html")
    file_key = row.get("published_file_key") or row.get("file_key")
    base = {
        "uid": row["uid"],
        "owner": row["owner"],
        "code": row["code"],
        "title": row["title"],
        "audience": row["audience"],
        "required_at": list(row.get("required_at") or []),
        "applicability": _applicability(row),
        "status": row["status"],
        "body_kind": kind,
        "published_at": row.get("published_at"),
        "current_version_uid": row.get("current_version_uid"),
        "current_version_number": row.get("current_version_number"),
        "current_version_published_at": row.get("current_version_published_at"),
        "has_file": bool(file_key),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if not detail:
        return TermOut(**base)
    return TermDetailOut(
        **base,
        body_md=row.get("body_md"),
        body_html=html,
        file_content_type=row.get("published_file_content_type") or row.get("file_content_type"),
        file_size=row.get("file_size"),
    )


@router.get("", name="list-terms")
async def list_terms(
    owner: Owner | None = None,
    term_status: str | None = Query(None, alias="status"),
    audience: Audience | None = None,
    q: str | None = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[TermOut]:
    if owner == "graphene":
        require_permission(principal, PLATFORM_TERMS_READ)
    else:
        require_permission(principal, TERMS_READ)
    rows = await svc.list_terms(
        session, owner=owner or "tenant", status=term_status, audience=audience, q=q
    )
    return [_term_out(r, detail=False) for r in rows]


@router.post("", name="create-term", status_code=status.HTTP_201_CREATED)
async def create_term(
    body: CreateTerm,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> TermDetailOut:
    require_permission(principal, TERMS_WRITE)
    tenant_id, actor = await _ctx(session)
    if tenant_id is None:
        raise HTTPException(status_code=403, detail="A firm session is required.")
    try:
        created = await svc.create_term(
            session,
            owner="tenant",
            tenant_id=tenant_id,
            actor_id=actor,
            title=body.title,
            code=body.code,
            audience=body.audience,
            required_at=list(body.required_at),
            applicability=body.applicability,
            body_kind=body.body_kind,
            body_md=body.body_md,
        )
    except svc.TermsError as exc:
        _raise(exc)
    return _term_out(created, detail=True)  # type: ignore[return-value]


@router.get("/outstanding", name="list-outstanding-terms")
async def list_outstanding(
    required_at: RequiredAt = "onboarding",
    product: str | None = None,
    has_transfer: bool | None = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> OutstandingOut:
    _, user_id = await _ctx(session)
    subject: svc.SubjectKind = "user" if principal.portal != "client" else "contact"
    contact_id = await _contact_id(session) if subject == "contact" else None
    rows = await svc.outstanding_terms(
        session,
        svc.OutstandingContext(
            required_at=required_at,
            subject=subject,
            contact_id=int(contact_id) if contact_id is not None else None,
            user_id=user_id,
            product=product,
            has_transfer=has_transfer,
        ),
    )
    return OutstandingOut(items=[_term_out(r, detail=True) for r in rows])


@router.get("/graphene", name="list-graphene-terms")
async def list_graphene_terms(
    term_status: str | None = Query(None, alias="status"),
    q: str | None = None,
    principal: Principal = Depends(get_current_principal),
) -> list[TermOut]:
    require_permission(principal, PLATFORM_TERMS_READ)
    async with system_transaction() as session:
        rows = await svc.list_terms(session, owner="graphene", status=term_status, q=q)
    return [_term_out(r, detail=False) for r in rows]


@router.post("/graphene", name="create-graphene-term", status_code=status.HTTP_201_CREATED)
async def create_graphene_term(
    body: CreateTerm,
    principal: Principal = Depends(get_current_principal),
) -> TermDetailOut:
    require_permission(principal, PLATFORM_TERMS_WRITE)
    async with system_transaction() as session:
        try:
            created = await svc.create_term(
                session,
                owner="graphene",
                tenant_id=None,
                actor_id=None,
                title=body.title,
                code=body.code,
                audience=body.audience,
                required_at=list(body.required_at),
                applicability=body.applicability,
                body_kind=body.body_kind,
                body_md=body.body_md,
            )
        except svc.TermsError as exc:
            _raise(exc)
    return _term_out(created, detail=True)  # type: ignore[return-value]


@router.get("/graphene/{uid}", name="get-graphene-term")
async def get_graphene_term(
    uid: str,
    principal: Principal = Depends(get_current_principal),
) -> TermDetailOut:
    require_permission(principal, PLATFORM_TERMS_READ)
    async with system_transaction() as session:
        row = await svc.get_term(session, uid)
    if row is None or row["owner"] != "graphene":
        raise HTTPException(status_code=404, detail="Not found.")
    return _term_out(row, detail=True)  # type: ignore[return-value]


@router.get("/graphene/{uid}/versions", name="list-graphene-term-versions")
async def list_graphene_term_versions(
    uid: str,
    principal: Principal = Depends(get_current_principal),
) -> list[TermVersionOut]:
    require_permission(principal, PLATFORM_TERMS_READ)
    async with system_transaction() as session:
        row = await svc.get_term(session, uid)
        if row is None or row["owner"] != "graphene":
            raise HTTPException(status_code=404, detail="Not found.")
        versions = await svc.list_versions(session, uid)
    return [TermVersionOut(**v) for v in versions]


@router.get("/{uid}", name="get-term")
async def get_term(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> TermDetailOut:
    row = await svc.get_term(session, uid)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if row["owner"] == "graphene":
        if row["status"] != "published" and not (
            is_system(principal) or PLATFORM_TERMS_READ.slug in principal.permissions
        ):
            raise HTTPException(status_code=404, detail="Not found.")
    elif row["status"] != "published":
        require_permission(principal, TERMS_READ)
    return _term_out(row, detail=True)  # type: ignore[return-value]


@router.patch("/{uid}", name="update-term")
async def update_term(
    uid: str,
    body: UpdateTerm,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> TermDetailOut:
    row = await svc.get_term(session, uid)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if row["owner"] == "graphene":
        raise HTTPException(status_code=403, detail="Edit Graphene terms from operations.")
    require_permission(principal, TERMS_WRITE)
    try:
        updated = await svc.update_term(
            session,
            uid,
            title=body.title,
            audience=body.audience,
            required_at=list(body.required_at) if body.required_at is not None else None,
            applicability=body.applicability,
            body_kind=body.body_kind,
            body_md=body.body_md,
        )
    except svc.TermsError as exc:
        _raise(exc)
    return _term_out(updated, detail=True)  # type: ignore[return-value]


@router.patch("/graphene/{uid}", name="update-graphene-term")
async def update_graphene_term(
    uid: str,
    body: UpdateTerm,
    principal: Principal = Depends(get_current_principal),
) -> TermDetailOut:
    require_permission(principal, PLATFORM_TERMS_WRITE)
    async with system_transaction() as session:
        try:
            updated = await svc.update_term(
                session,
                uid,
                title=body.title,
                audience=body.audience,
                required_at=list(body.required_at) if body.required_at is not None else None,
                applicability=body.applicability,
                body_kind=body.body_kind,
                body_md=body.body_md,
            )
        except svc.TermsError as exc:
            _raise(exc)
    return _term_out(updated, detail=True)  # type: ignore[return-value]


@router.post("/{uid}/publish", name="publish-term")
async def publish_term(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> TermDetailOut:
    row = await svc.get_term(session, uid)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if row["owner"] == "graphene":
        raise HTTPException(status_code=403, detail="Publish Graphene terms from operations.")
    require_permission(principal, TERMS_WRITE)
    _, actor = await _ctx(session)
    try:
        published = await svc.publish_term(session, uid, actor)
    except svc.TermsError as exc:
        _raise(exc)
    return _term_out(published, detail=True)  # type: ignore[return-value]


@router.post("/graphene/{uid}/publish", name="publish-graphene-term")
async def publish_graphene_term(
    uid: str,
    principal: Principal = Depends(get_current_principal),
) -> TermDetailOut:
    require_permission(principal, PLATFORM_TERMS_WRITE)
    async with system_transaction() as session:
        try:
            published = await svc.publish_term(session, uid, None)
        except svc.TermsError as exc:
            _raise(exc)
    return _term_out(published, detail=True)  # type: ignore[return-value]


@router.post("/{uid}/archive", name="archive-term")
async def archive_term(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> TermDetailOut:
    row = await svc.get_term(session, uid)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if row["owner"] == "graphene":
        raise HTTPException(status_code=403, detail="Archive Graphene terms from operations.")
    require_permission(principal, TERMS_WRITE)
    try:
        archived = await svc.archive_term(session, uid)
    except svc.TermsError as exc:
        _raise(exc)
    return _term_out(archived, detail=True)  # type: ignore[return-value]


@router.post("/graphene/{uid}/archive", name="archive-graphene-term")
async def archive_graphene_term(
    uid: str,
    principal: Principal = Depends(get_current_principal),
) -> TermDetailOut:
    require_permission(principal, PLATFORM_TERMS_WRITE)
    async with system_transaction() as session:
        try:
            archived = await svc.archive_term(session, uid)
        except svc.TermsError as exc:
            _raise(exc)
    return _term_out(archived, detail=True)  # type: ignore[return-value]


@router.get("/{uid}/versions", name="list-term-versions")
async def list_term_versions(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[TermVersionOut]:
    row = await svc.get_term(session, uid)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if row["owner"] == "graphene":
        require_permission(principal, PLATFORM_TERMS_READ)
    else:
        require_permission(principal, TERMS_READ)
    return [TermVersionOut(**v) for v in await svc.list_versions(session, uid)]


@router.get("/{uid}/file", name="get-term-file")
async def get_term_file(
    uid: str,
    draft: bool = Query(default=False),
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> Response:
    row = await svc.get_term(session, uid)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if draft:
        # Authoring previews show the file currently attached to the mutable header. The ordinary
        # client route must stay pinned to the published snapshot, especially when a replacement
        # PDF has been uploaded but not published yet.
        require_permission(
            principal, PLATFORM_TERMS_READ if row["owner"] == "graphene" else TERMS_READ
        )
        file_key = row.get("file_key")
        file_content_type = row.get("file_content_type")
    else:
        if row["status"] != "published":
            raise HTTPException(status_code=404, detail="Not found.")
        file_key = row.get("published_file_key")
        file_content_type = row.get("published_file_content_type")
    if not file_key:
        raise HTTPException(status_code=404, detail="Not found.")
    return await _term_file(session, row, str(file_key), file_content_type)


async def _term_file(
    session: AsyncSession, row: dict[str, Any], file_key: str, file_content_type: str | None
) -> Response:
    """A terms file from the store it was uploaded to: the firm's own for its terms, the caller's
    firm's for Graphene's."""
    tenant_id, _ = await _ctx(session)
    if row["owner"] == "tenant":
        if tenant_id is None:
            raise HTTPException(status_code=404, detail="Not found.")
        store = await tenant_storage.get_tenant_store(session, tenant_id)
    else:
        store = await tenant_storage.get_tenant_store(session, tenant_id) if tenant_id else None
    if store is None:
        raise HTTPException(status_code=503, detail="Object storage is not configured.")
    data, content_type = await storage.get_tenant_object(store, file_key)
    return Response(
        content=data,
        media_type=file_content_type or content_type,
        headers={
            "Content-Disposition": f'inline; filename="{row["code"]}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )


@uploads.post("/{uid}/file", name="upload-term-file")
async def upload_term_file(
    uid: str,
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> TermDetailOut:
    row = await svc.get_term(session, uid)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if row["owner"] == "graphene":
        raise HTTPException(status_code=403, detail="Upload Graphene files from operations.")
    require_permission(principal, TERMS_WRITE)
    tenant_id, _ = await _ctx(session)
    if tenant_id is None:
        raise HTTPException(status_code=403, detail="A firm session is required.")
    data = await storage.read_capped(file)
    try:
        content_type, ext = storage.validate_upload(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if content_type != "application/pdf":
        raise HTTPException(status_code=422, detail="Terms files must be a PDF.")
    store = await tenant_storage.get_tenant_store(session, tenant_id)
    if store is None:
        raise HTTPException(
            status_code=503, detail="Object storage is not configured for this firm."
        )
    key = f"terms/{uid}/body.{ext}"
    await storage.put_tenant_object(store, key, data, content_type)
    try:
        updated = await svc.set_pdf_file(
            session, uid, file_key=key, content_type=content_type, size=len(data)
        )
    except svc.TermsError as exc:
        _raise(exc)
    return _term_out(updated, detail=True)  # type: ignore[return-value]


@router.post("/{uid}/accept", name="accept-term")
async def accept_term(
    uid: str,
    body: AcceptTermBody | None = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> AcceptanceOut:
    tenant_id, user_id = await _ctx(session)
    subject: svc.SubjectKind = "user" if principal.portal != "client" else "contact"
    contact_id = await _contact_id(session) if subject == "contact" else None
    application_id = None
    payload = body or AcceptTermBody()
    if payload.application_uid and tenant_id is not None:
        application_id = (
            await session.execute(
                text("SELECT id FROM crm.applications WHERE uid = :u AND tenant_id = :t"),
                {"u": payload.application_uid, "t": tenant_id},
            )
        ).scalar_one_or_none()
    try:
        accepted = await svc.accept_term(
            session,
            uid,
            subject=subject,
            tenant_id=tenant_id,
            contact_id=int(contact_id) if contact_id is not None else None,
            user_id=user_id,
            accepted_by=user_id,
            channel="in_person" if payload.attested else "portal",
            application_id=int(application_id) if application_id is not None else None,
        )
    except svc.TermsError as exc:
        _raise(exc)
    return AcceptanceOut(
        uid=accepted["uid"],
        term_uid=accepted["term_uid"],
        title=accepted["title"],
        code=accepted["code"],
        version_number=accepted["version_number"],
        version_uid=accepted["version_uid"],
        accepted_at=accepted["accepted_at"],
        channel=accepted["channel"],
        owner=accepted.get("owner"),
    )


@contacts_router.get("/{uid}/term-acceptances", name="list-contact-term-acceptances")
async def list_contact_term_acceptances(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[AcceptanceOut]:
    require_permission(principal, CONTACTS_READ)
    contact_id = (
        await session.execute(
            text("SELECT id FROM crm.contacts WHERE uid = :u AND deleted_at IS NULL"),
            {"u": uid},
        )
    ).scalar_one_or_none()
    if contact_id is None:
        raise HTTPException(status_code=404, detail="Not found.")
    rows = await svc.list_contact_acceptances(session, int(contact_id))
    return [AcceptanceOut(**{k: r[k] for k in AcceptanceOut.model_fields if k in r}) for r in rows]


async def _acceptance(session: AsyncSession, uid: str, acceptance_uid: str) -> dict[str, Any]:
    """One acceptance of the contact ``uid`` names, or 404."""
    contact_id = (
        await session.execute(
            text("SELECT id FROM crm.contacts WHERE uid = :u AND deleted_at IS NULL"),
            {"u": uid},
        )
    ).scalar_one_or_none()
    found = (
        await svc.get_contact_acceptance(session, int(contact_id), acceptance_uid)
        if contact_id is not None
        else None
    )
    if found is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return found


@contacts_router.get("/{uid}/term-acceptances/{acceptance_uid}", name="get-contact-term-acceptance")
async def get_contact_term_acceptance(
    uid: str,
    acceptance_uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> AcceptedTermsOut:
    """The terms a client accepted, exactly as they read them: the version the acceptance names,
    not today's. Gated as the list is: who may see that a client accepted terms may read what."""
    require_permission(principal, CONTACTS_READ)
    row = await _acceptance(session, uid, acceptance_uid)
    return AcceptedTermsOut(
        uid=row["uid"],
        title=row["version_title"] or row["title"],
        code=row["code"],
        version_number=row["version_number"],
        accepted_at=row["accepted_at"],
        channel=row["channel"],
        body_kind=row["body_kind"],
        body_html=row["body_html"],
        has_file=bool(row["file_key"]),
        evidence_document_uid=row["evidence_document_uid"],
    )


@contacts_router.get(
    "/{uid}/term-acceptances/{acceptance_uid}/file",
    name="get-contact-term-acceptance-file",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def get_contact_term_acceptance_file(
    uid: str,
    acceptance_uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> Response:
    """The file of the terms version a client accepted. 404 when that version is text."""
    require_permission(principal, CONTACTS_READ)
    row = await _acceptance(session, uid, acceptance_uid)
    if not row["file_key"]:
        raise HTTPException(status_code=404, detail="Not found.")
    return await _term_file(session, row, str(row["file_key"]), row["file_content_type"])
