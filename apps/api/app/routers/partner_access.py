"""A firm's partner-API access, managed by Graphene operations: its API keys, its check webhook,
and the deliveries made to that webhook.

Under ``/admin/tenants/{tenant_uid}``, on the admin session (service_role, the actor's claims
published so ``audit.record()`` stamps who did it). Reads need ``tenants:read`` and writes
``tenants:write``, the gates of every other firm-configuration page.

* A key is minted by ``external.keys.mint_key``. Its secret is in the create response and nowhere
  else: ``auth.api_keys`` keeps the SHA-256 digest and the first characters. A revoke sets
  ``revoked_at``; the firm API process refuses the key once its 60-second key cache lets go.
* The webhook is the same ``compliance.webhooks`` row the firm's own ``PUT /webhook`` writes, with
  the same rules: HTTPS, a host that resolves to public addresses only, and a new signing secret
  shown once on every set. Removing it deletes its deliveries with it.
* A delivery that ran out of retries can be sent again: it goes back to ``pending`` with a fresh
  retry schedule (``attempts`` back to 0), to the firm's current URL and secret.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import crypto
from app.addressing import admin_tenant_id
from app.auth import Principal
from app.complycube.webhook_url import UnsafeWebhookUrl, check_syntax, check_url
from app.db import admin_session
from app.permissions import TENANTS_READ, TENANTS_WRITE
from app.routers.admin import requires
from external.keys import SCOPES, mint_key

router = APIRouter(prefix="/admin/tenants/{tenant_uid}", tags=["admin"])

_tenants_read = requires(TENANTS_READ)
_tenants_write = requires(TENANTS_WRITE)

TenantId = Annotated[int, Depends(admin_tenant_id)]

KeyStatus = Literal["active", "revoked", "expired"]


class ApiKeyOut(BaseModel):
    uid: str
    name: str
    key_prefix: str
    scopes: list[str]
    status: KeyStatus
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class ApiKeysOut(BaseModel):
    keys: list[ApiKeyOut]
    #: Every scope the firm API knows, for the create form.
    scopes: list[str]


class CreateApiKey(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1)
    expires_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def trimmed(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("scopes")
    @classmethod
    def known_scopes(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(SCOPES))
        if unknown:
            raise ValueError(f"unknown scopes: {', '.join(unknown)}")
        return [scope for scope in SCOPES if scope in value]

    @field_validator("expires_at")
    @classmethod
    def in_the_future(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if aware <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future")
        return aware


class CreatedApiKey(BaseModel):
    key: ApiKeyOut
    #: The bearer secret. This response is the only place it ever appears.
    secret: str


class RevokedApiKey(BaseModel):
    key: ApiKeyOut


class WebhookOut(BaseModel):
    url: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SetWebhook(BaseModel):
    url: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def https_only(cls, value: str) -> str:
        return str(check_syntax(value.strip()))


class WebhookSet(BaseModel):
    url: str
    #: The signing secret. This response is the only place it ever appears.
    secret: str


class DeliveryOut(BaseModel):
    uid: str
    event: str
    check_uid: str | None
    kind: str | None
    check_status: str | None
    status: Literal["pending", "sent", "failed"]
    attempts: int
    next_attempt_at: datetime
    last_error: str | None
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DeliveriesOut(BaseModel):
    items: list[DeliveryOut]


class ResentDelivery(BaseModel):
    delivery: DeliveryOut


_LIST_KEYS = text(
    """
    SELECT uid, name, key_prefix, scopes, created_at, last_used_at, expires_at, revoked_at,
           CASE WHEN revoked_at IS NOT NULL THEN 'revoked'
                WHEN expires_at IS NOT NULL AND expires_at <= now() THEN 'expired'
                ELSE 'active' END AS status
      FROM auth.api_keys
     WHERE tenant_id = :t
     ORDER BY created_at DESC, id DESC
    """
)

_INSERT_KEY = text(
    """
    INSERT INTO auth.api_keys (
      tenant_id, name, key_prefix, key_hash, scopes, expires_at, created_by
    )
    VALUES (:t, :name, :prefix, :hash, :scopes, :expires_at, auth.uid())
    RETURNING uid, name, key_prefix, scopes, created_at, last_used_at, expires_at, revoked_at,
              CASE WHEN expires_at IS NOT NULL AND expires_at <= now() THEN 'expired'
                   ELSE 'active' END AS status
    """
)

_REVOKE_KEY = text(
    """
    UPDATE auth.api_keys
       SET revoked_at = COALESCE(revoked_at, now()),
           revoked_by = COALESCE(revoked_by, auth.uid())
     WHERE tenant_id = :t AND uid = :uid
    RETURNING uid, name, key_prefix, scopes, created_at, last_used_at, expires_at, revoked_at,
              'revoked' AS status
    """
)

_GET_WEBHOOK = text(
    "SELECT url, created_at, updated_at FROM compliance.webhooks WHERE tenant_id = :t"
)

_UPSERT_WEBHOOK = text(
    """
    INSERT INTO compliance.webhooks (tenant_id, url, secret_ct)
    VALUES (:t, :url, :secret_ct)
    ON CONFLICT (tenant_id)
    DO UPDATE SET url = EXCLUDED.url, secret_ct = EXCLUDED.secret_ct, updated_at = now()
    """
)

_DELETE_WEBHOOK = text("DELETE FROM compliance.webhooks WHERE tenant_id = :t")

_LIST_DELIVERIES = text(
    """
    SELECT d.uid, d.event, d.payload ->> 'uid' AS check_uid,
           COALESCE(d.payload ->> 'kind', ch.kind) AS kind,
           d.payload ->> 'status' AS check_status, d.status, d.attempts, d.next_attempt_at,
           d.last_error, d.sent_at, d.created_at, d.updated_at
      FROM compliance.webhook_deliveries d
      LEFT JOIN compliance.checks ch
        ON ch.uid = d.payload ->> 'uid' AND ch.tenant_id = d.tenant_id
     WHERE d.tenant_id = :t
     ORDER BY d.created_at DESC, d.id DESC
     LIMIT :limit
    """
)

_GET_DELIVERY = text(
    """
    SELECT d.uid, d.event, d.payload ->> 'uid' AS check_uid,
           COALESCE(d.payload ->> 'kind', ch.kind) AS kind,
           d.payload ->> 'status' AS check_status, d.status, d.attempts, d.next_attempt_at,
           d.last_error, d.sent_at, d.created_at, d.updated_at
      FROM compliance.webhook_deliveries d
      LEFT JOIN compliance.checks ch
        ON ch.uid = d.payload ->> 'uid' AND ch.tenant_id = d.tenant_id
     WHERE d.tenant_id = :t AND d.uid = :uid
    """
)

_REQUEUE_DELIVERY = text(
    """
    UPDATE compliance.webhook_deliveries
       SET status = 'pending', attempts = 0, next_attempt_at = now()
     WHERE tenant_id = :t AND uid = :uid AND status = 'failed'
    RETURNING id
    """
)


def _key(row: Any) -> ApiKeyOut:
    return ApiKeyOut.model_validate(dict(row))


def _delivery(row: Any) -> DeliveryOut:
    return DeliveryOut.model_validate(dict(row))


def _secrets_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "SECRETS_MASTER_KEY is not set (or is not a valid Fernet key) on the API — the "
            "webhook signing secret cannot be stored until it is."
        ),
    )


@router.get("/api-keys", name="list-api-keys")
async def list_api_keys(
    tenant_id: TenantId,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> ApiKeysOut:
    """The firm's partner-API keys, newest first. A secret is never returned here."""
    rows = (await session.execute(_LIST_KEYS, {"t": tenant_id})).mappings().all()
    return ApiKeysOut(keys=[_key(row) for row in rows], scopes=list(SCOPES))


@router.post("/api-keys", name="create-api-key", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    tenant_id: TenantId,
    body: CreateApiKey,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> CreatedApiKey:
    """Mint a live key for the firm. The secret is in this response only."""
    minted = mint_key()
    row = (
        (
            await session.execute(
                _INSERT_KEY,
                {
                    "t": tenant_id,
                    "name": body.name,
                    "prefix": minted.key_prefix,
                    "hash": minted.key_hash,
                    "scopes": body.scopes,
                    "expires_at": body.expires_at,
                },
            )
        )
        .mappings()
        .one()
    )
    return CreatedApiKey(key=_key(row), secret=minted.secret)


@router.post("/api-keys/{key_uid}/revoke", name="revoke-api-key")
async def revoke_api_key(
    tenant_id: TenantId,
    key_uid: str,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> RevokedApiKey:
    """Revoke a key. Revoking a revoked key keeps its first revocation."""
    row = (
        (await session.execute(_REVOKE_KEY, {"t": tenant_id, "uid": key_uid}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    return RevokedApiKey(key=_key(row))


@router.get("/webhook", name="get-tenant-webhook")
async def get_tenant_webhook(
    tenant_id: TenantId,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> WebhookOut:
    """The firm's check webhook URL, or null. The signing secret is not read."""
    row = (await session.execute(_GET_WEBHOOK, {"t": tenant_id})).mappings().one_or_none()
    if row is None:
        return WebhookOut(url=None)
    return WebhookOut.model_validate(dict(row))


@router.put("/webhook", name="set-tenant-webhook")
async def set_tenant_webhook(
    tenant_id: TenantId,
    body: SetWebhook,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> WebhookSet:
    """Set or replace the firm's webhook. A new signing secret every time, in this response
    only."""
    try:
        url = await check_url(body.url)
    except UnsafeWebhookUrl as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    secret = secrets.token_urlsafe(24)
    try:
        sealed = crypto.encrypt(secret)
    except crypto.SecretsNotConfigured as exc:
        raise _secrets_unavailable() from exc
    await session.execute(_UPSERT_WEBHOOK, {"t": tenant_id, "url": url, "secret_ct": sealed})
    return WebhookSet(url=url, secret=secret)


@router.delete("/webhook", name="delete-tenant-webhook")
async def delete_tenant_webhook(
    tenant_id: TenantId,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> WebhookOut:
    """Remove the firm's webhook, and with it every queued and past delivery."""
    await session.execute(_DELETE_WEBHOOK, {"t": tenant_id})
    return WebhookOut(url=None)


@router.get("/webhook-deliveries", name="list-webhook-deliveries")
async def list_webhook_deliveries(
    tenant_id: TenantId,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_read),
) -> DeliveriesOut:
    """The firm's most recent webhook deliveries, newest first."""
    rows = (
        (await session.execute(_LIST_DELIVERIES, {"t": tenant_id, "limit": limit})).mappings().all()
    )
    return DeliveriesOut(items=[_delivery(row) for row in rows])


@router.post("/webhook-deliveries/{delivery_uid}/resend", name="resend-webhook-delivery")
async def resend_webhook_delivery(
    tenant_id: TenantId,
    delivery_uid: str,
    session: AsyncSession = Depends(admin_session),
    _p: Principal = Depends(_tenants_write),
) -> ResentDelivery:
    """Queue a failed delivery again, with a fresh retry schedule. 409 for one that is not
    failed."""
    params = {"t": tenant_id, "uid": delivery_uid}
    requeued = (await session.execute(_REQUEUE_DELIVERY, params)).first()
    row = (await session.execute(_GET_DELIVERY, params)).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found")
    if requeued is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a failed delivery can be sent again.",
        )
    return ResentDelivery(delivery=_delivery(row))
