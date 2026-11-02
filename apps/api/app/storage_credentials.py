"""Per-user object-storage credentials (docs/storage.md §4).

Platform-portal staff hold S3-style credentials for their firm's bucket: literal per-user
IAM users on the store, scoped by the holder's ``storage:*`` permissions AT ISSUE TIME
(rotate after RBAC changes). The secret is generated here, shown ONCE in the response, and
kept only as Fernet ciphertext (``storage.user_credential_secrets``) so a beta store's IAM
state can be re-pushed. One active credential per (tenant, user); re-issue rotates.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets as _secrets
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import crypto, object_store, tenant_storage
from app.permissions import (
    STORAGE_CREDENTIALS,
    STORAGE_CREDENTIALS_OWN,
    STORAGE_READ,
    STORAGE_READ_ALL,
    STORAGE_SHARED,
    satisfies,
)

logger = logging.getLogger(__name__)


class StorageInactive(LookupError):
    """The tenant has no active storage — nothing to issue credentials against."""


@dataclass(frozen=True)
class Scope:
    """What an issued credential may touch — derived from permissions, rendered into the
    store-side policy, and recorded on the registry row for display/audit."""

    read_prefixes: list[str]
    shared: bool


def scope_for(permission_slugs: set[str]) -> Scope | None:
    """The credential scope a set of permission slugs earns (the one place the
    permission→prefix mapping lives). None = no storage entitlements at all."""
    if satisfies(permission_slugs, str(STORAGE_READ_ALL)):
        read = ["files/", "exports/"]
    elif satisfies(permission_slugs, str(STORAGE_READ)):
        read = ["files/reports/", "exports/"]
    else:
        read = []
    shared = satisfies(permission_slugs, str(STORAGE_SHARED))
    if not read and not shared:
        return None
    return Scope(read_prefixes=read, shared=shared)


def can_issue_own(permission_slugs: set[str]) -> bool:
    return satisfies(permission_slugs, str(STORAGE_CREDENTIALS_OWN)) or satisfies(
        permission_slugs, str(STORAGE_CREDENTIALS)
    )


def can_manage(permission_slugs: set[str]) -> bool:
    return satisfies(permission_slugs, str(STORAGE_CREDENTIALS))


async def member_permission_slugs(session: AsyncSession, tenant_id: int, user_id: str) -> set[str]:
    """A member's permission slugs from the mirror (their platform-portal role's grants) —
    the scope source when issuing FOR another user."""
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT p.slug FROM auth.organisation_memberships m "
                "JOIN auth.membership_roles mr ON mr.membership_id = m.id "
                "JOIN auth.role_permissions rp ON rp.role_id = mr.role_id "
                "JOIN auth.permissions p ON p.id = rp.permission_id "
                "WHERE m.user_id = :u AND m.tenant_id = :t "
                "  AND m.portal = 'platform' AND m.status = 'active'"
            ),
            {"u": user_id, "t": tenant_id},
        )
    ).scalars()
    return set(rows)


def _admin_for_store(store: tenant_storage.TenantStore) -> object_store.ObjectStoreAdmin:
    return object_store.admin_for(store.kind, store.endpoint, store.region)


async def issue(
    session: AsyncSession,
    tenant_id: int,
    user_id: str,
    scope: Scope,
    *,
    admin: object_store.ObjectStoreAdmin | None = None,
) -> dict[str, Any]:
    """Issue (or rotate) a user's credential: revoke any active one, create the store-side
    IAM user with the scoped policy, record the registry row + encrypted secret. Returns
    the payload INCLUDING the secret — the only time it is ever shown."""
    store = await tenant_storage.get_tenant_store(session, tenant_id)
    if store is None:
        raise StorageInactive("tenant storage is not provisioned/active")
    store_admin = admin or _admin_for_store(store)

    existing = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, access_key_id FROM storage.user_credentials "
                    "WHERE tenant_id = :t AND user_id = :u AND status = 'active'"
                ),
                {"t": tenant_id, "u": user_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    access_key = f"svc-u-{tenant_id}-{_secrets.token_hex(6)}"
    secret_key = _secrets.token_urlsafe(30)
    policy = object_store.user_files_policy(
        store.bucket, read_prefixes=scope.read_prefixes, shared=scope.shared
    )
    # Create the replacement BEFORE revoking the old credential: the store-side delete is
    # not transactional, so revoking first + failing here would roll the registry back to
    # an 'active' row whose store-side user no longer exists (a silently dead key).
    await asyncio.to_thread(store_admin.create_user_credential, access_key, secret_key, policy)

    try:
        if existing:
            await _revoke_row(
                session, store_admin, int(existing["id"]), str(existing["access_key_id"])
            )
    except BaseException:
        # The caller's transaction rolls back (old row stays active) — drop the store-side
        # user we just minted so the store doesn't accumulate orphans.
        with contextlib.suppress(Exception):
            await asyncio.to_thread(store_admin.delete_user_credential, access_key)
        raise

    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO storage.user_credentials "
                    "  (tenant_id, user_id, access_key_id, read_prefixes, shared) "
                    "VALUES (:t, :u, :ak, :rp, :sh) RETURNING id, created_at"
                ),
                {
                    "t": tenant_id,
                    "u": user_id,
                    "ak": access_key,
                    "rp": scope.read_prefixes,
                    "sh": scope.shared,
                },
            )
        )
        .mappings()
        .one()
    )
    await session.execute(
        text(
            "INSERT INTO storage.user_credential_secrets (credential_id, secret_access_key_ct) "
            "VALUES (:id, :ct)"
        ),
        {"id": row["id"], "ct": crypto.encrypt(secret_key)},
    )
    return {
        "id": row["id"],
        "access_key_id": access_key,
        "secret_access_key": secret_key,  # shown once — never retrievable again
        "endpoint": store.endpoint or None,
        "bucket": store.bucket,
        "read_prefixes": scope.read_prefixes,
        "shared": scope.shared,
        "created_at": row["created_at"],
    }


async def _revoke_row(
    session: AsyncSession,
    store_admin: object_store.ObjectStoreAdmin,
    credential_id: int,
    access_key: str,
) -> None:
    await asyncio.to_thread(store_admin.delete_user_credential, access_key)
    await session.execute(
        text(
            "UPDATE storage.user_credentials SET status = 'revoked', revoked_at = now() "
            "WHERE id = :id"
        ),
        {"id": credential_id},
    )


async def revoke(
    session: AsyncSession,
    tenant_id: int,
    credential_id: int,
    *,
    admin: object_store.ObjectStoreAdmin | None = None,
) -> dict[str, Any] | None:
    """Revoke a credential (store-side user deleted — access dies immediately; the row is
    kept as history). None when the id isn't an active credential of this tenant."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, user_id, access_key_id FROM storage.user_credentials "
                    "WHERE id = :id AND tenant_id = :t AND status = 'active'"
                ),
                {"id": credential_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    store = await tenant_storage.get_tenant_store(session, tenant_id)
    if store is None:
        raise StorageInactive("tenant storage is not provisioned/active")
    store_admin = admin or _admin_for_store(store)
    await _revoke_row(session, store_admin, int(row["id"]), str(row["access_key_id"]))
    return {"id": row["id"], "user_id": row["user_id"], "status": "revoked"}


async def list_for_tenant(session: AsyncSession, tenant_id: int) -> list[dict[str, Any]]:
    """The tenant's credentials (active + revoked history; never secrets)."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT c.id, c.user_id, u.email, u.name, c.access_key_id, "
                    "  c.read_prefixes, c.shared, c.status, c.created_at, c.revoked_at "
                    "FROM storage.user_credentials c "
                    "JOIN auth.users u ON u.id = c.user_id "
                    "WHERE c.tenant_id = :t ORDER BY c.created_at DESC"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def get_own(session: AsyncSession, tenant_id: int, user_id: str) -> dict[str, Any] | None:
    """The caller's active credential (no secret), or None."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, access_key_id, read_prefixes, shared, status, created_at "
                    "FROM storage.user_credentials "
                    "WHERE tenant_id = :t AND user_id = :u AND status = 'active'"
                ),
                {"t": tenant_id, "u": user_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


def connection_info(store: tenant_storage.TenantStore, scope: Scope | None) -> dict[str, Any]:
    """What the portal shows on the storage page: where to point an S3 client and what
    the caller's permissions would earn."""
    return {
        "endpoint": store.endpoint or None,
        "bucket": store.bucket,
        "region": store.region,
        "read_prefixes": scope.read_prefixes if scope else [],
        "shared": scope.shared if scope else False,
    }


def is_supported(store: tenant_storage.TenantStore) -> bool:
    """Whether the tenant's backend can mint user credentials at all (RustFS/MinIO — not
    plain S3, where IAM lives out of band)."""
    return store.kind in ("rustfs", "minio")
