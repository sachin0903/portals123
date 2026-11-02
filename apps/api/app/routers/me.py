import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import environments, session_mirror
from app.audit_actions import writes_no_row
from app.auth import Principal, get_current_principal
from app.db import system_transaction, tenant_session
from app.page_views import PORTALS, normalise_page_path
from app.rum import identified, parse_rum, rum_body
from app.session_refresh import refresh_member_sessions
from app.tracing import emit_browser_view

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


@router.get("/me", name="me")
async def me(principal: Principal = Depends(get_current_principal)) -> Principal:
    """Return the verified caller — permissions, and the portal this session resolves to.

    Portals used to follow this with ``GET /me/portal`` so the shell could bounce a
    wrong-app cookie. ``portal`` is the same answer ``get_current_principal`` already
    computed (``auth.resolve_claims``); keep ``/me/portal`` for login-time
    ``validatePortalSession``, which still needs a confirmed answer before a cookie is
    written."""
    return principal


@router.get("/me/portal", name="me-portal")
async def my_portal(principal: Principal = Depends(get_current_principal)) -> dict[str, object]:
    """The portal THIS session resolves to.

    ``get_current_principal`` already ran ``auth.resolve_claims``, and that run is the one that
    sees the portal the request named. Classifying again here would drop the name: a staff
    login that holds both platform and operations, through one shared application, would come
    back as platform, and the admin portal would refuse a session that does have access.
    """
    return {"portal": principal.portal}


@router.get("/me/tenant", name="me-tenant")
async def my_tenant(session: AsyncSession = Depends(tenant_session)) -> dict[str, object] | None:
    """The caller's active firm, resolved through the RLS context (auth.tenant_id())."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, alias, name, enabled "
                    "FROM auth.tenants WHERE id = auth.tenant_id()"
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


@router.get("/me/memberships", name="me-memberships")
async def my_memberships(
    session: AsyncSession = Depends(tenant_session),
) -> list[dict[str, object]]:
    """The caller's own memberships across firms -- the org switcher. RLS on
    organisation_memberships already restricts a user to their own rows."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT m.tenant_id, t.name AS tenant_name, t.alias AS tenant_alias, "
                    "       m.portal, r.slug AS role_slug, m.status "
                    "FROM auth.organisation_memberships m "
                    "JOIN auth.tenants t ON t.id = m.tenant_id "
                    "LEFT JOIN auth.roles r ON r.id = m.role_id "
                    "WHERE m.user_id = auth.uid() ORDER BY t.name, m.portal"
                )
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


class PageViewIn(BaseModel):
    path: str


@router.post("/me/page-view", name="record-page-view", status_code=status.HTTP_204_NO_CONTENT)
@writes_no_row
async def record_page_view(
    body: PageViewIn,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> Response:
    """Record the signed-in caller's navigation. Pathname only; not an action event.

    The portal comes from the verified session, not the body. A missing tenant or an
    unrecognised portal is ignored: the page has already been served.
    """
    path = normalise_page_path(body.path)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="A page path is required."
        )
    portal = principal.portal if principal.portal in PORTALS else None
    if portal is not None:
        await session.execute(
            text(
                "INSERT INTO audit.page_views (tenant_id, actor_id, portal, path) "
                "SELECT auth.tenant_id(), auth.uid(), :portal, :path "
                "WHERE auth.tenant_id() IS NOT NULL AND auth.uid() IS NOT NULL"
            ),
            {"portal": portal, "path": path},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- The caller's own notes -----------------------------------------------------------------
#
# `platform.staff_notes`: a member of staff's checklist, shown on their dashboard. RLS admits the
# owner alone, so every statement here is scoped by the policy and the handlers add nothing to
# it — `auth.uid()` names the owner on insert, and a uid that is not theirs is simply not found.
# Not audit actions: a note is private, and a trail of someone's "call Joan" is nobody's business.


class NoteIn(BaseModel):
    body: str


class NotePatch(BaseModel):
    body: str | None = None
    done: bool | None = None


def _note_body(raw: str) -> str:
    body = raw.strip()
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A note needs words.")
    if len(body) > 500:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="A note is at most 500 characters."
        )
    return body


@router.get("/me/notes", name="my-notes")
async def my_notes(session: AsyncSession = Depends(tenant_session)) -> list[dict[str, object]]:
    """The caller's notes, in their order. RLS does the narrowing."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT uid, body, done, position, created_at, updated_at "
                    "FROM platform.staff_notes ORDER BY position, id"
                )
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


@router.post("/me/notes", name="add-my-note", status_code=status.HTTP_201_CREATED)
@writes_no_row
async def add_my_note(
    body: NoteIn, session: AsyncSession = Depends(tenant_session)
) -> dict[str, object]:
    """Append a note. The owner is the caller, by claim, not by body."""
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO platform.staff_notes (tenant_id, user_id, body, position) "
                    "SELECT auth.tenant_id(), auth.uid(), :body, "
                    "       COALESCE(MAX(position), 0) + 1 FROM platform.staff_notes "
                    "RETURNING uid, body, done, position, created_at, updated_at"
                ),
                {"body": _note_body(body.body)},
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


@router.patch("/me/notes/{uid}", name="edit-my-note")
@writes_no_row
async def edit_my_note(
    uid: str, body: NotePatch, session: AsyncSession = Depends(tenant_session)
) -> dict[str, object]:
    """Re-word a note or tick it. Either field alone is a complete edit."""
    if body.body is None and body.done is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to change.")
    row = (
        (
            await session.execute(
                text(
                    "UPDATE platform.staff_notes SET "
                    "  body = COALESCE(:body, body), done = COALESCE(:done, done), "
                    "  updated_at = now() "
                    "WHERE uid = :uid RETURNING uid, body, done, position, created_at, updated_at"
                ),
                {
                    "uid": uid,
                    "body": _note_body(body.body) if body.body is not None else None,
                    "done": body.done,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such note.")
    return dict(row)


@router.delete("/me/notes/{uid}", name="remove-my-note", status_code=status.HTTP_204_NO_CONTENT)
@writes_no_row
async def remove_my_note(uid: str, session: AsyncSession = Depends(tenant_session)) -> Response:
    """Remove a note. Removing one that is not there (or not yours) is the same silence."""
    await session.execute(text("DELETE FROM platform.staff_notes WHERE uid = :uid"), {"uid": uid})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/me/rum", name="record-rum", status_code=status.HTTP_204_NO_CONTENT)
@writes_no_row
async def record_rum(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> Response:
    """Join a browser page view onto the trace the portal minted for it.

    Authenticated the same way as ``POST /me/page-view``. The body is vitals and a route
    id, never a raw URL. A bad trace id emits nothing. The body is not logged. The caller
    stamped on the span is the verified session (internal user id, tenant id, session id),
    never a field from the body.
    """
    view = parse_rum(rum_body(await request.body()))
    if view is not None:
        row = (await session.execute(text("SELECT auth.tenant_id(), auth.uid()::text"))).one()
        emit_browser_view(
            identified(
                view,
                user_id=row[1],
                tenant_id=int(row[0]) if row[0] is not None else None,
                session_id=principal.session_id,
            )
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me/sessions", name="my-sessions")
async def my_sessions(
    background: BackgroundTasks,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, object]:
    """The caller's own live sessions, from the local mirror.

    The response does not call WorkOS. A refresh of this caller's sessions is started
    after the response, and the browser making this request is marked from the access
    token. When the mirror does not have that session yet, the row is blank and the
    portal names it from the request. It is not written.
    """
    row = (await session.execute(text("SELECT auth.tenant_id(), auth.uid()::text"))).one()
    tenant_id, user_id = row[0], row[1]
    if tenant_id is None or not user_id:
        return {"sessions": []}
    background.add_task(refresh_member_sessions, int(tenant_id), str(user_id))
    return {
        "sessions": await session_mirror.sessions_for_user(
            session,
            tenant_id=int(tenant_id),
            user_id=str(user_id),
            current_session_id=principal.session_id,
        )
    }


@router.post(
    "/me/sessions/refresh",
    name="refresh-my-sessions",
    status_code=status.HTTP_204_NO_CONTENT,
)
@writes_no_row
async def refresh_my_sessions(principal: Principal = Depends(get_current_principal)) -> Response:
    """Copy the caller's live sessions from WorkOS into the local mirror.

    The security page calls this after it has revoked sessions at WorkOS, so the next
    paint is not waiting on the poller. It is a cache fill, not an act: the revoke
    itself is the thing the trail records.
    """
    async with system_transaction() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id::text, tenant_id, workos_user_id, sessions_synced_at "
                    "FROM auth.users "
                    "WHERE workos_user_id = :wu AND deleted_at IS NULL"
                ),
                {"wu": principal.user_id},
            )
        ).first()
        if row is None or row[1] is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        user_id, tenant_id, workos_user_id, seen = (
            str(row[0]),
            int(row[1]),
            str(row[2]),
            row[3],
        )
        try:
            workos = await environments.client_for_env(
                session, await environments.primary_environment_id(session, tenant_id)
            )
        except LookupError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This firm has no identity provider",
            ) from None
        listed_at = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
    try:
        page = await workos.user_management.list_sessions(workos_user_id)
        items = [item async for item in page.auto_paging_iter()]
    except Exception:
        logger.exception("could not refresh sessions for %s", user_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Sessions could not be refreshed",
        ) from None
    async with system_transaction() as session:
        await session_mirror.store_listed_sessions(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            items=items,
            not_before=listed_at,
            seen=seen,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
