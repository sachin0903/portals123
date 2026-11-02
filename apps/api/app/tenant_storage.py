"""Per-tenant object-storage registry + provisioning (docs/storage.md §6–7).

One bucket per tenant (``{S3_TENANT_BUCKET_PREFIX}{tenant_id}``) — the sovereignty
boundary. ``storage.tenants`` records where each tenant's bucket is; this
module owns that registry and the provisioning flow that mints the bucket at tenant
creation. Provisioning is idempotent (ensure-style, re-run repairs) and best-effort:
it must never fail tenant creation — a failed step leaves the row ``provisioning`` and
``POST /admin/tenants/{id}/storage/provision`` retries.

The tenant service-user secret is Fernet-encrypted into
``storage.tenant_secrets`` (service_role-only), mirroring the WorkOS
environment secrets.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import crypto, datalake, object_store
from app.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TenantStore:
    """A tenant's resolved storage location (non-secret registry row)."""

    tenant_id: int
    kind: str
    endpoint: str
    region: str | None
    bucket: str
    public_base_url: str | None
    status: str


def provisioning_configured() -> bool:
    """Whether this instance can mint buckets at all: the platform root credential is set
    (endpoint may legitimately be blank = plain AWS S3). Unconfigured (fresh dev checkout)
    => no registry rows are written and branding falls back to data: URIs."""
    return bool(settings.s3_access_key_id and settings.s3_secret_access_key)


def _kind() -> str:
    """The store kind recorded in the registry. Explicit ``S3_KIND`` wins; otherwise a
    custom endpoint means RustFS locally (the only custom-endpoint store we deploy) and a
    blank endpoint means plain AWS S3."""
    if settings.s3_kind:
        return settings.s3_kind
    return "rustfs" if settings.s3_endpoint_url else "s3"


def _bucket_name(uid: str) -> str:
    """The tenant's bucket: ``{prefix}{uid-hex}`` (e.g. ``t-ab12cd34ef567890``). Named by
    the immutable uid; its ``ten_`` prefix is dropped because S3 bucket names cannot
    contain underscores."""
    return f"{settings.s3_tenant_bucket_prefix}{uid.removeprefix('ten_')}"


def _public_base_url(bucket: str) -> str:
    """Browser-reachable base for the bucket's public prefix: path-style under a custom
    endpoint, virtual-hosted for plain AWS."""
    if settings.s3_endpoint_url:
        return f"{settings.s3_endpoint_url.rstrip('/')}/{bucket}"
    region = settings.s3_region if settings.s3_region not in ("", "auto") else "us-east-1"
    return f"https://{bucket}.s3.{region}.amazonaws.com"


async def get_tenant_store(session: AsyncSession, tenant_id: int) -> TenantStore | None:
    """The tenant's ACTIVE storage row, or None (not provisioned / suspended). The request
    path resolves buckets through this — suspended tenants stop resolving immediately."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT tenant_id, kind, endpoint, region, bucket, public_base_url, status "
                    "FROM storage.tenants WHERE tenant_id = :t AND status = 'active'"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return TenantStore(**row) if row else None


async def document_bucket(session: AsyncSession, tenant_id: int | None = None) -> str:
    """Where a NEW document object goes: the tenant's own bucket when its storage is active,
    else the shared legacy `S3_DOCUMENTS_BUCKET`.

    Only new writes consult this. Existing objects are found through the `bucket` recorded on
    their `documents.files` row, so a tenant provisioned today starts writing into its own
    bucket while everything already filed keeps resolving where it is — no migration, no
    cutover, no window in which a document is unreachable.

    Works on a tenant-bound session (RLS lets a firm read its own registry row) and on a
    service_role one (the worker, which passes `tenant_id` explicitly).
    """
    bucket = (
        await session.execute(
            text(
                "SELECT bucket FROM storage.tenants "
                "WHERE tenant_id = COALESCE(:t, auth.tenant_id()) AND status = 'active'"
            ),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()
    return str(bucket) if bucket else settings.s3_documents_bucket


async def get_storage_row(session: AsyncSession, tenant_id: int) -> dict[str, Any] | None:
    """The raw registry row regardless of status (admin UI / retry flows)."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT tenant_id, kind, endpoint, region, bucket, public_base_url, "
                    "  access_key_id, status, created_at, updated_at "
                    "FROM storage.tenants WHERE tenant_id = :t"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def _ensure_service_users(
    session: AsyncSession,
    tenant_id: int,
    bucket: str,
    store_admin: object_store.ObjectStoreAdmin,
) -> dict[str, tuple[str, str]]:
    """Ensure all four scoped service users exist for this bucket (0061).

    Returns ``{role: (access_key_id, secret_ciphertext)}`` for the roles that are live.

    "Ensure" means ensure, not assume: a stored credential is PROBED against the store
    (``credential_works``) and re-minted when the store no longer authenticates it. The
    previous implementation reported ``service_user: ok`` whenever the registry held a key,
    which made a lost principal permanently unrepairable — the store had forgotten
    ``svc-t-graphene`` and every lake read 403'd with ``InvalidAccessKeyId`` while
    provisioning insisted all four steps were green.

    A role that cannot be minted (no IAM API — plain AWS S3) is simply absent from the
    result. That is not a failure: `lake_config` falls back to the platform root credential,
    which is how hosted deployments with out-of-band IAM already work.
    """
    stored = {
        str(r["role"]): (str(r["access_key_id"]), str(r["secret_access_key_ct"]))
        for r in (
            await session.execute(
                text(
                    "SELECT u.role, u.access_key_id, s.secret_access_key_ct "
                    "  FROM storage.tenant_service_users u "
                    "  JOIN storage.tenant_service_user_secrets s "
                    "    ON s.tenant_id = u.tenant_id AND s.role = u.role "
                    " WHERE u.tenant_id = :t"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    }

    live: dict[str, tuple[str, str]] = {}
    for role in object_store.SERVICE_USER_ROLES:
        existing = stored.get(role)
        if existing is not None:
            access_key, secret_ct = existing
            works = await asyncio.to_thread(
                store_admin.credential_works, bucket, access_key, crypto.decrypt(secret_ct)
            )
            if works:
                live[role] = existing
                continue
            logger.warning(
                "tenant %s service user %r no longer authenticates against %s — re-minting",
                tenant_id,
                access_key,
                bucket,
            )

        name = object_store.service_user_role_name(bucket, role)
        principal = await asyncio.to_thread(
            store_admin.ensure_tenant_principal,
            bucket,
            name,
            object_store.service_user_policy(bucket, role),
        )
        if principal is None:
            continue
        access_key, secret_access_key = principal
        secret_ct = crypto.encrypt(secret_access_key)
        # Minting is idempotent by name: the store's add-user overwrites the secret, so a
        # re-mint rotates in place rather than orphaning a principal.
        await session.execute(
            text(
                "INSERT INTO storage.tenant_service_users (tenant_id, role, access_key_id) "
                "VALUES (:t, :role, :ak) "
                "ON CONFLICT (tenant_id, role) DO UPDATE SET "
                "  access_key_id = EXCLUDED.access_key_id, updated_at = now()"
            ),
            {"t": tenant_id, "role": role, "ak": access_key},
        )
        await session.execute(
            text(
                "INSERT INTO storage.tenant_service_user_secrets "
                "  (tenant_id, role, secret_access_key_ct) VALUES (:t, :role, :ct) "
                "ON CONFLICT (tenant_id, role) DO UPDATE SET "
                "  secret_access_key_ct = EXCLUDED.secret_access_key_ct, updated_at = now()"
            ),
            {"t": tenant_id, "role": role, "ct": secret_ct},
        )
        live[role] = (access_key, secret_ct)
    return live


async def provision_tenant_storage(
    session: AsyncSession,
    tenant_id: int,
    *,
    admin: object_store.ObjectStoreAdmin | None = None,
) -> dict[str, Any]:
    """Mint (or repair) the tenant's bucket and registry row. Idempotent end-to-end:
    every step is ensure-style, so re-running a partial provision completes it.

    Returns the non-secret summary. Never raises for a *provisioning* failure — the row
    is left ``provisioning`` for a retry; only unexpected DB errors propagate."""
    # Per-step summary for the admin UI ("cooking up storage…"): ok | pending | skipped.
    steps = {
        "bucket": "pending",
        "catalogue": "pending" if datalake.configured() else "skipped",
        "public_prefix": "pending",
        "service_user": "pending",
    }
    if not provisioning_configured():
        return {"status": "unconfigured", "tenant_id": tenant_id, "steps": steps}

    uid = str(
        (
            await session.execute(
                text("SELECT uid FROM auth.tenants WHERE id = :t"), {"t": tenant_id}
            )
        ).scalar_one()
    )
    bucket = _bucket_name(uid)
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO storage.tenants "
                    "  (tenant_id, kind, endpoint, region, bucket, status) "
                    "VALUES (:t, :kind, :endpoint, :region, :bucket, 'provisioning') "
                    "ON CONFLICT (tenant_id) DO UPDATE SET "
                    # A suspended tenant being re-provisioned goes back through
                    # 'provisioning'; endpoint/kind refresh in case the instance moved
                    # stores. The bucket name is immutable (it exists remotely). An
                    # ACTIVE row keeps its status: a re-run is a repair, and it must
                    # not take working storage offline when a remote step then fails.
                    "  kind = EXCLUDED.kind, endpoint = EXCLUDED.endpoint, "
                    "  region = EXCLUDED.region, "
                    "  status = CASE WHEN storage.tenants.status = 'active' "
                    "           THEN 'active' ELSE 'provisioning' END "
                    "RETURNING tenant_id, bucket, access_key_id, status"
                ),
                {
                    "t": tenant_id,
                    "kind": _kind(),
                    "endpoint": settings.s3_endpoint_url,
                    "region": settings.s3_region or None,
                    "bucket": bucket,
                },
            )
        )
        .mappings()
        .one()
    )
    bucket = str(row["bucket"])  # pre-existing rows keep their original bucket name
    store_admin = admin or object_store.admin_for(
        _kind(), settings.s3_endpoint_url, settings.s3_region or None
    )

    try:
        await asyncio.to_thread(store_admin.ensure_bucket, bucket)
        steps["bucket"] = "ok"
    except Exception:
        logger.warning(
            "bucket create failed for tenant %s (%s); status left %s",
            tenant_id,
            bucket,
            row["status"],
        )
        return {
            "status": str(row["status"]),
            "tenant_id": tenant_id,
            "bucket": bucket,
            "steps": steps,
        }

    # DuckLake catalogue schema (docs/datalake.md): named by the tenant's immutable uid.
    # Skipped when no datalakes DB is configured; a failure leaves the row 'provisioning'
    # (same retry path as the bucket).
    if datalake.configured():
        try:
            await datalake.ensure_tenant_catalog(uid)
            steps["catalogue"] = "ok"
        except Exception:
            logger.warning(
                "lake catalogue create failed for tenant %s (%s); status left %s",
                tenant_id,
                uid,
                row["status"],
            )
            return {
                "status": str(row["status"]),
                "tenant_id": tenant_id,
                "bucket": bucket,
                "steps": steps,
            }

    public_base_url: str | None = None
    if await asyncio.to_thread(store_admin.ensure_public_prefix, bucket, "files/tenant"):
        public_base_url = _public_base_url(bucket)
        steps["public_prefix"] = "ok"

    roles = await _ensure_service_users(session, tenant_id, bucket, store_admin)
    base = roles.get("base")
    access_key_id = base[0] if base else None
    steps["service_user"] = "ok" if base else "pending"
    # Per-role detail alongside the flat status the admin UI already renders (additive).
    steps["service_users"] = {  # type: ignore[assignment]
        role: ("ok" if role in roles else "pending") for role in object_store.SERVICE_USER_ROLES
    }
    if base:
        # `base` stays mirrored onto storage.tenants/tenant_secrets so every existing reader
        # of those columns keeps working — 0061 is an expand-phase migration.
        await session.execute(
            text(
                "INSERT INTO storage.tenant_secrets "
                "  (tenant_id, secret_access_key_ct) VALUES (:t, :ct) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "  secret_access_key_ct = EXCLUDED.secret_access_key_ct, updated_at = now()"
            ),
            {"t": tenant_id, "ct": base[1]},
        )

    await session.execute(
        text(
            "UPDATE storage.tenants SET "
            "  public_base_url = :url, access_key_id = :ak, status = 'active' "
            "WHERE tenant_id = :t"
        ),
        {"t": tenant_id, "url": public_base_url, "ak": access_key_id},
    )
    return {
        "status": "active",
        "tenant_id": tenant_id,
        "bucket": bucket,
        "public_base_url": public_base_url,
        "access_key_id": access_key_id,
        "steps": steps,
    }


async def suspend_tenant_storage(session: AsyncSession, tenant_id: int) -> None:
    """Stop the tenant's storage resolving (the bucket itself is untouched — buckets are
    never deleted automatically; offboarding is a deliberate export). Only an 'active' row
    suspends; a half-provisioned row stays 'provisioning' (it never resolved anyway)."""
    await session.execute(
        text(
            "UPDATE storage.tenants SET status = 'suspended' "
            "WHERE tenant_id = :t AND status = 'active'"
        ),
        {"t": tenant_id},
    )
    # The lake layer caches the attach config (TTL) and keeps attached connections pooled;
    # both would keep serving the suspended tenant's data until they aged out. Evict now.
    uid = (
        await session.execute(text("SELECT uid FROM auth.tenants WHERE id = :t"), {"t": tenant_id})
    ).scalar_one_or_none()
    if uid is not None:
        from app import lake  # local import: app.lake.attach imports this module

        await lake.evict_tenant(str(uid))


async def get_lake_config(session: AsyncSession, uid: str) -> dict[str, Any] | None:
    """Everything ``app/lake`` needs to attach one tenant's catalogue read-only
    (docs/datalake.md): bucket + endpoint, the DuckLake DATA_PATH and METADATA_SCHEMA,
    and an S3 credential — the tenant's own scoped service user when one was minted,
    else the platform root credential (``scoped: false``). Secrets are decrypted here;
    the result stays in-process (never serve it to a client). None when the tenant has
    no active storage.

    Must run as service_role (the secrets table has no authenticated grant)."""
    row = (
        (
            await session.execute(
                text(
                    # The `datalake` role (0061) is the least-privileged credential that can
                    # read the lake; `base` is the whole-bucket fallback for a tenant
                    # provisioned before 0061 and not yet re-provisioned. COALESCE rather
                    # than two queries so a missing datalake row degrades instead of failing.
                    "SELECT ts.tenant_id, t.uid, ts.kind, ts.endpoint, ts.region, ts.bucket, "
                    "  COALESCE(du.access_key_id, ts.access_key_id) AS access_key_id, "
                    "  COALESCE(ds.secret_access_key_ct, s.secret_access_key_ct) "
                    "    AS secret_access_key_ct "
                    "FROM storage.tenants ts "
                    "JOIN auth.tenants t ON t.id = ts.tenant_id "
                    "LEFT JOIN storage.tenant_secrets s ON s.tenant_id = ts.tenant_id "
                    "LEFT JOIN storage.tenant_service_users du "
                    "  ON du.tenant_id = ts.tenant_id AND du.role = 'datalake' "
                    "LEFT JOIN storage.tenant_service_user_secrets ds "
                    "  ON ds.tenant_id = ts.tenant_id AND ds.role = 'datalake' "
                    "WHERE t.uid = :uid AND ts.status = 'active'"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    if row["access_key_id"] and row["secret_access_key_ct"]:
        access_key_id = row["access_key_id"]
        secret_access_key = crypto.decrypt(row["secret_access_key_ct"])
        scoped = True
    else:
        access_key_id = settings.s3_access_key_id
        secret_access_key = settings.s3_secret_access_key
        scoped = False
    return {
        "tenant_id": row["tenant_id"],
        "uid": row["uid"],
        "kind": row["kind"],
        "endpoint": row["endpoint"],
        "region": row["region"],
        "bucket": row["bucket"],
        "data_path": f"s3://{row['bucket']}/datalake/",
        "lake_schema": row["uid"],
        "access_key_id": access_key_id,
        "secret_access_key": secret_access_key,
        "scoped": scoped,
    }


async def resume_tenant_storage(session: AsyncSession, tenant_id: int) -> None:
    """Undo a suspension. Guarded to 'suspended' rows only — a 'provisioning' row must go
    through provision_tenant_storage (the bucket may not exist yet)."""
    await session.execute(
        text(
            "UPDATE storage.tenants SET status = 'active' "
            "WHERE tenant_id = :t AND status = 'suspended'"
        ),
        {"t": tenant_id},
    )
