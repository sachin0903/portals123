"""Object-storage access for tenant users (docs/storage.md §4).

Standalone, tenant-target-aware router: every endpoint takes an optional ``?tenant=<uid>``.
Omitted, the target is the CALLER's own resolved tenant — firm staff get their firm; an
operations principal resolves to the core Graphene tenant. A specified tenant different
from the caller's own requires operations scope (``tenants:read`` to look, ``tenants:write``
to touch), so ops can service any tenant through the same surface.

Issued credentials are scoped by ``storage:*`` permissions at issue time; the secret is
returned once and never retrievable again.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import object_store, storage_credentials, tenant_storage
from app.auth import (
    Principal,
    build_jwt_claims,
    get_current_principal,
    require_any_permission,
    require_permission,
)
from app.db import SessionLocal
from app.permissions import (
    STORAGE_CREDENTIALS,
    STORAGE_CREDENTIALS_OWN,
    STORAGE_READ,
    STORAGE_READ_ALL,
    STORAGE_SHARED,
    TENANTS_READ,
    TENANTS_WRITE,
)

router = APIRouter(prefix="/storage", tags=["storage"])


@dataclass
class StorageContext:
    """A storage request context: elevated session pinned to the TARGET tenant, plus the
    caller's identity. ``foreign`` marks a target that is not the caller's own tenant —
    endpoints escalate their gate (tenants:read/write) when set."""

    session: AsyncSession
    tenant_id: int
    user_id: str  # the caller's internal auth.users id
    principal: Principal
    foreign: bool


async def storage_session(
    tenant: str | None = None,
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[StorageContext]:
    """Mirror of ``firm_session`` with an optional target tenant (by uid). Runs as
    service_role (issuance writes need it) with claims published for ``audit.record()``;
    every query in the endpoint is forced to the resolved ``tenant_id``."""
    if principal.organisation_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organisation context for this principal",
        )
    async with SessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        resolved = (
            await session.execute(
                text("SELECT auth.resolve_claims(:wos_user, :wos_org, :client_id)"),
                {
                    "wos_user": principal.user_id,
                    "wos_org": principal.organisation_id,
                    "client_id": principal.client_id,
                },
            )
        ).scalar_one()
        if not resolved or resolved.get("tenant_id") is None or not resolved.get("user_uuid"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No resolvable tenant/identity context for this principal",
            )
        own_tenant_id = int(resolved["tenant_id"])
        target_tenant_id = own_tenant_id
        foreign = False
        if tenant is not None:
            row = (
                await session.execute(
                    text("SELECT id FROM auth.tenants WHERE uid = :uid"), {"uid": tenant}
                )
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown tenant")
            target_tenant_id = int(row)
            foreign = target_tenant_id != own_tenant_id
            if foreign:
                # Reaching into another tenant's storage is an operations act.
                require_permission(principal, TENANTS_READ)
        claims = {**build_jwt_claims(principal), **resolved}
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps(claims)},
        )
        yield StorageContext(
            session=session,
            tenant_id=target_tenant_id,
            user_id=str(resolved["user_uuid"]),
            principal=principal,
            foreign=foreign,
        )


def _held(principal: Principal) -> set[str]:
    return set(principal.permissions or [])


def _require_foreign_write(ctx: StorageContext) -> None:
    if ctx.foreign:
        require_permission(ctx.principal, TENANTS_WRITE)


async def _active_store(ctx: StorageContext) -> tenant_storage.TenantStore:
    store = await tenant_storage.get_tenant_store(ctx.session, ctx.tenant_id)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="File storage is unavailable for this firm. Contact support.",
        )
    return store


def _require_any_storage(principal: Principal) -> None:
    require_any_permission(
        principal,
        STORAGE_READ,
        STORAGE_READ_ALL,
        STORAGE_SHARED,
        STORAGE_CREDENTIALS_OWN,
        STORAGE_CREDENTIALS,
    )


@router.get("", name="get-storage")
async def get_storage(ctx: StorageContext = Depends(storage_session)) -> dict[str, object]:
    """Connection info for the target tenant's bucket, the scope the caller's permissions
    would earn, and the caller's active credential (never its secret)."""
    if not ctx.foreign:
        _require_any_storage(ctx.principal)
    store = await _active_store(ctx)
    scope = storage_credentials.scope_for(_held(ctx.principal))
    return {
        **storage_credentials.connection_info(store, scope),
        "credentials_supported": storage_credentials.is_supported(store),
        "credential": await storage_credentials.get_own(ctx.session, ctx.tenant_id, ctx.user_id),
    }


@router.post("/credentials", name="issue-own-storage-credentials")
async def issue_own_credentials(
    ctx: StorageContext = Depends(storage_session),
) -> dict[str, object]:
    """Issue (or rotate) the CALLER's credential for the target tenant's bucket, scoped by
    the caller's own permissions. The secret in the response is shown once."""
    _require_foreign_write(ctx)
    if not storage_credentials.can_issue_own(_held(ctx.principal)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted")
    scope = storage_credentials.scope_for(_held(ctx.principal))
    if scope is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to create file access details.",
        )
    return await _issue(ctx, ctx.user_id, scope)


@router.post("/credentials/{user_id}", name="issue-member-storage-credentials")
async def issue_member_credentials(
    user_id: str,
    ctx: StorageContext = Depends(storage_session),
) -> dict[str, object]:
    """Issue (or rotate) a credential for another member of the target tenant, scoped by
    THAT member's role permissions (mirror)."""
    _require_foreign_write(ctx)
    require_permission(ctx.principal, STORAGE_CREDENTIALS)
    member_slugs = await storage_credentials.member_permission_slugs(
        ctx.session, ctx.tenant_id, user_id
    )
    if not member_slugs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active platform membership for that user in this tenant.",
        )
    scope = storage_credentials.scope_for(member_slugs)
    if scope is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That member does not have permission to access files.",
        )
    return await _issue(ctx, user_id, scope)


async def _issue(
    ctx: StorageContext, user_id: str, scope: storage_credentials.Scope
) -> dict[str, object]:
    try:
        return await storage_credentials.issue(ctx.session, ctx.tenant_id, user_id, scope)
    except storage_credentials.StorageInactive as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except object_store.CredentialsUnsupported as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/credentials", name="list-storage-credentials")
async def list_credentials(
    ctx: StorageContext = Depends(storage_session),
) -> list[dict[str, object]]:
    """The target tenant's issued credentials (active + revoked history; no secrets)."""
    if not ctx.foreign:
        require_permission(ctx.principal, STORAGE_CREDENTIALS)
    return await storage_credentials.list_for_tenant(ctx.session, ctx.tenant_id)


async def credential_id_for_uid(
    credential_uid: str, ctx: StorageContext = Depends(storage_session)
) -> int:
    """`/storage/credentials/{credential_uid}` -> the serial the revoke keys on.

    Lives here rather than in `app/addressing.py` because it rides `storage_session`, which is
    defined in this module — importing a router from the addressing module would close a cycle.
    The session is already pinned to the TARGET tenant, so a uid from another firm is a 404 and
    not a credential someone else holds.
    """
    row = (
        await ctx.session.execute(
            text("SELECT id FROM storage.user_credentials WHERE uid = :uid"),
            {"uid": credential_uid},
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
    return int(row)


@router.delete("/credentials/{credential_uid}", name="revoke-storage-credentials")
async def revoke_credentials(
    credential_id: Annotated[int, Depends(credential_id_for_uid)],
    ctx: StorageContext = Depends(storage_session),
) -> dict[str, object]:
    """Revoke a credential. Own: needs `storage:credentials_own`; anyone else's: needs
    `storage:credentials` (or operations scope on a foreign tenant)."""
    _require_foreign_write(ctx)
    row = (
        (
            await ctx.session.execute(
                text(
                    "SELECT user_id FROM storage.user_credentials "
                    "WHERE id = :id AND tenant_id = :t AND status = 'active'"
                ),
                {"id": credential_id, "t": ctx.tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown credential")
    held = _held(ctx.principal)
    is_own = str(row["user_id"]) == ctx.user_id
    if not (
        (is_own and storage_credentials.can_issue_own(held))
        or storage_credentials.can_manage(held)
        or ctx.foreign  # already gated tenants:write above
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted")
    try:
        result = await storage_credentials.revoke(ctx.session, ctx.tenant_id, credential_id)
    except storage_credentials.StorageInactive as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown credential")
    return result
