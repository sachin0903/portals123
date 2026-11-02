"""Service-role sessions for the external process.

The key hash is looked up before any tenant is known, so the session assumes
``service_role`` and publishes no portal claim. After the key resolves, writes
publish that key's ``tenant_id`` and nothing else. This module is imported only
when the process is not serving the stub.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.complycube.client import ScreeningClient, screening_from_env
from app.complycube.sync import accept_event
from external.keys import LOOKUP_SQL, TOUCH_SQL, CachedKey, KeyVerifier, cached_from_row
from external.live import LiveStore
from external.models import (
    CheckCreate,
    CheckOut,
    ChecksOut,
    ClientCreate,
    ClientOut,
    ClientPatch,
    CustodianPut,
    DocumentOut,
    HeldDocument,
    WebhookCreated,
    WebhookOut,
)

DEFAULT_URL = "postgresql+psycopg://app:app@localhost:5432/portals"

_factory: async_sessionmaker[AsyncSession] | None = None

T = TypeVar("T")


def _sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _factory
    if _factory is None:
        engine = create_async_engine(
            os.environ.get("DATABASE_URL", DEFAULT_URL), pool_pre_ping=True
        )
        from app.tracing import instrument_engine

        instrument_engine(engine)
        _factory = async_sessionmaker(engine, expire_on_commit=False)
    return _factory


@asynccontextmanager
async def service_session() -> AsyncIterator[AsyncSession]:
    """``service_role`` with no claims. The tenant is not known yet."""
    factory = _sessionmaker()
    async with factory() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        yield session


async def publish_tenant(session: AsyncSession, tenant_id: int) -> None:
    """Record the key's tenant for audit. The claim has no portal."""
    await session.execute(
        text("SELECT set_config('request.jwt.claims', :claims, true)"),
        {"claims": json.dumps({"tenant_id": tenant_id, "role": "service_role"})},
    )


async def lookup_key(key_hash: str) -> CachedKey:
    async with service_session() as session:
        result = await session.execute(LOOKUP_SQL, {"key_hash": key_hash})
        return cached_from_row(result.first())


async def touch_last_used(record: CachedKey) -> None:
    async with service_session() as session:
        await session.execute(TOUCH_SQL, {"uid": record.uid, "tenant_id": record.tenant_id})


async def tenant_enabled(tenant_id: int) -> bool:
    """False when the firm is disabled or the tenant row is missing."""
    async with service_session() as session:
        enabled = await session.scalar(
            text("SELECT enabled FROM auth.tenants WHERE id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
    return enabled is True


def production_keys() -> KeyVerifier:
    return KeyVerifier(lookup_key, touch_last_used, tenant_enabled=tenant_enabled)


class RequestBoundStore:
    """One service-role transaction per call, scoped to the key's tenant."""

    def __init__(self, screening: ScreeningClient | None = None) -> None:
        self._screening = screening if screening is not None else screening_from_env()

    async def _using(self, tenant_id: int, call: Callable[[LiveStore], Awaitable[T]]) -> T:
        async with service_session() as session:
            await publish_tenant(session, tenant_id)
            return await call(LiveStore(session, screening=self._screening))

    async def create_client(self, body: ClientCreate, *, tenant_id: int) -> ClientOut:
        return await self._using(
            tenant_id, lambda store: store.create_client(body, tenant_id=tenant_id)
        )

    async def get_client(self, uid: str, *, tenant_id: int) -> ClientOut | None:
        return await self._using(
            tenant_id, lambda store: store.get_client(uid, tenant_id=tenant_id)
        )

    async def list_clients(self, *, tenant_id: int, limit: int, offset: int) -> list[ClientOut]:
        return await self._using(
            tenant_id,
            lambda store: store.list_clients(tenant_id=tenant_id, limit=limit, offset=offset),
        )

    async def patch_client(
        self, uid: str, body: ClientPatch, *, tenant_id: int
    ) -> ClientOut | None:
        return await self._using(
            tenant_id, lambda store: store.patch_client(uid, body, tenant_id=tenant_id)
        )

    async def create_document(
        self, client_uid: str, body: HeldDocument, *, tenant_id: int
    ) -> DocumentOut:
        return await self._using(
            tenant_id, lambda store: store.create_document(client_uid, body, tenant_id=tenant_id)
        )

    async def create_check(self, client_uid: str, body: CheckCreate, *, tenant_id: int) -> CheckOut:
        """Persist the provider client id, then upload the address and start the check.

        The id is committed before the address, the provider check, and the local
        check insert. A later failure cannot roll it back, and a retry finds the
        same provider client by the contact uid instead of creating another.
        The address is uploaded on that retry when the provider client still has none.
        """
        async with service_session() as session:
            await publish_tenant(session, tenant_id)
            bound_now = await LiveStore(session, screening=self._screening).bind_provider_client(
                client_uid, body, tenant_id=tenant_id
            )
        return await self._using(
            tenant_id,
            lambda store: store.create_check(
                client_uid,
                body,
                tenant_id=tenant_id,
                upload_address=bound_now,
                already_bound=True,
            ),
        )

    async def get_check(self, uid: str, *, tenant_id: int) -> CheckOut | None:
        return await self._using(tenant_id, lambda store: store.get_check(uid, tenant_id=tenant_id))

    async def checks_for(self, client_uid: str, *, tenant_id: int) -> ChecksOut:
        return await self._using(
            tenant_id, lambda store: store.checks_for(client_uid, tenant_id=tenant_id)
        )

    async def put_custodian(
        self, uid: str, body: CustodianPut, *, tenant_id: int
    ) -> ClientOut | None:
        return await self._using(
            tenant_id, lambda store: store.put_custodian(uid, body, tenant_id=tenant_id)
        )

    async def get_webhook(self, *, tenant_id: int) -> WebhookOut:
        return await self._using(tenant_id, lambda store: store.get_webhook(tenant_id=tenant_id))

    async def put_webhook(self, url: str, *, tenant_id: int) -> WebhookCreated:
        return await self._using(
            tenant_id, lambda store: store.put_webhook(url, tenant_id=tenant_id)
        )

    async def delete_webhook(self, *, tenant_id: int) -> WebhookOut:
        return await self._using(tenant_id, lambda store: store.delete_webhook(tenant_id=tenant_id))


def production_store() -> RequestBoundStore:
    return RequestBoundStore()


async def accept_screening(event: dict[str, Any]) -> None:
    """Store the inbox row and return. The worker's ComplyCube consumer applies it.

    ``accept_event`` raises ``MissingEventId`` for an event without an id; the receiver refuses
    those before calling here.
    """
    async with service_session() as session:
        await accept_event(session, event)
