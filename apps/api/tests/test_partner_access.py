"""The admin's partner-API access routes, over real HTTP against a real Postgres.

``admin_session`` is replaced by a service-role session on the test database and the principal is
chosen per test, so the permission gates, the SQL and the response shapes are what run. The key
round trip ends at the firm API's own verifier: a key minted here is accepted there, and refused
once revoked.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import crypto
from app import db as db_mod
from app.auth import Principal, get_current_principal
from app.config import settings
from app.db import admin_session
from app.main import app
from app.routers import partner_access
from external.keys import LOOKUP_SQL, SCOPES, CachedKey, KeyCache, KeyVerifier, cached_from_row

Maker = async_sessionmaker[AsyncSession]

READER = Principal(user_id="user_ops_reader", permissions=["tenants:read"])
WRITER = Principal(user_id="user_ops_writer", permissions=["tenants:read", "tenants:write"])


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def firm(maker: Maker) -> AsyncIterator[dict[str, Any]]:
    nonce = uuid.uuid4().hex[:10]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tenant_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) "
                        "RETURNING id"
                    ),
                    {"u": f"ten_{nonce}", "a": f"pa-{nonce}", "n": f"Partner {nonce}"},
                )
            ).scalar_one()
        )
    yield {"id": tenant_id, "uid": f"ten_{nonce}"}
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        for stmt in (
            "DELETE FROM compliance.webhook_deliveries WHERE tenant_id = :t",
            "DELETE FROM compliance.checks WHERE tenant_id = :t",
            "DELETE FROM compliance.clients WHERE tenant_id = :t",
            "DELETE FROM compliance.webhooks WHERE tenant_id = :t",
            "DELETE FROM crm.contacts WHERE tenant_id = :t",
            "DELETE FROM auth.api_keys WHERE tenant_id = :t",
            "DELETE FROM auth.tenants WHERE id = :t",
        ):
            await s.execute(text(stmt), {"t": tenant_id})


@pytest_asyncio.fixture
async def http(
    maker: Maker, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Callable[[Principal], AsyncClient]]:
    monkeypatch.setattr(db_mod, "SessionLocal", maker)
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())

    async def _session() -> AsyncIterator[AsyncSession]:
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            yield session

    app.dependency_overrides[admin_session] = _session
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    def acting(principal: Principal) -> AsyncClient:
        app.dependency_overrides[get_current_principal] = lambda: principal
        return client

    try:
        yield acting
    finally:
        await client.aclose()
        app.dependency_overrides.pop(admin_session, None)
        app.dependency_overrides.pop(get_current_principal, None)


def _public(monkeypatch: pytest.MonkeyPatch, answer: str = "93.184.216.34") -> None:
    async def resolve(host: str, port: int) -> list[str]:
        return [answer]

    monkeypatch.setattr("app.complycube.webhook_url.system_resolver", resolve)


async def _verifier(maker: Maker) -> KeyVerifier:
    """The firm API's verifier, reading this database, with a cold cache."""

    async def lookup(key_hash: str) -> CachedKey:
        async with maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            return cached_from_row((await s.execute(LOOKUP_SQL, {"key_hash": key_hash})).first())

    async def touch(record: CachedKey) -> None:
        return None

    return KeyVerifier(lookup, touch, cache=KeyCache(ttl=0, miss_ttl=0))


# ---- API keys -----------------------------------------------------------------------------------


async def test_a_created_key_shows_its_secret_once_and_stores_only_its_digest(
    http: Callable[[Principal], AsyncClient], firm: dict[str, Any], maker: Maker
) -> None:
    client = http(WRITER)
    created = await client.post(
        f"/admin/tenants/{firm['uid']}/api-keys",
        json={"name": "Production", "scopes": ["checks:read", "clients:read"]},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    secret = body["secret"]
    assert secret.startswith("live_") and len(secret) > 40
    key = body["key"]
    assert key["name"] == "Production"
    assert key["scopes"] == ["clients:read", "checks:read"]
    assert key["status"] == "active"
    assert key["key_prefix"] == secret[:12]
    assert key["revoked_at"] is None

    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        row = (
            (await s.execute(text("SELECT * FROM auth.api_keys WHERE uid = :u"), {"u": key["uid"]}))
            .mappings()
            .one()
        )
    assert row["key_hash"] == hashlib.sha256(secret.encode()).hexdigest()
    assert row["tenant_id"] == firm["id"]
    assert secret not in {str(value) for value in row.values()}

    listed = await http(READER).get(f"/admin/tenants/{firm['uid']}/api-keys")
    assert listed.status_code == 200
    assert secret not in listed.text
    assert listed.json()["scopes"] == list(SCOPES)
    assert [item["uid"] for item in listed.json()["keys"]] == [key["uid"]]


async def test_a_revoked_key_is_refused_by_the_firm_api(
    http: Callable[[Principal], AsyncClient], firm: dict[str, Any], maker: Maker
) -> None:
    client = http(WRITER)
    created = await client.post(
        f"/admin/tenants/{firm['uid']}/api-keys", json={"name": "Ops", "scopes": list(SCOPES)}
    )
    secret = created.json()["secret"]
    uid = created.json()["key"]["uid"]

    accepted = await (await _verifier(maker)).authenticate(secret)
    assert accepted is not None
    assert accepted.tenant_id == firm["id"]
    assert set(accepted.scopes) == set(SCOPES)

    revoked = await client.post(f"/admin/tenants/{firm['uid']}/api-keys/{uid}/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["key"]["status"] == "revoked"
    first = revoked.json()["key"]["revoked_at"]
    assert first is not None

    assert await (await _verifier(maker)).authenticate(secret) is None

    again = await client.post(f"/admin/tenants/{firm['uid']}/api-keys/{uid}/revoke")
    assert again.status_code == 200
    assert again.json()["key"]["revoked_at"] == first

    missing = await client.post(f"/admin/tenants/{firm['uid']}/api-keys/nosuchkey000/revoke")
    assert missing.status_code == 404


async def test_an_expired_key_reads_expired_and_bad_bodies_are_refused(
    http: Callable[[Principal], AsyncClient], firm: dict[str, Any]
) -> None:
    client = http(WRITER)
    base = f"/admin/tenants/{firm['uid']}/api-keys"
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    created = await client.post(
        base, json={"name": "Expiring", "scopes": ["checks:read"], "expires_at": future}
    )
    assert created.status_code == 201
    assert created.json()["key"]["expires_at"] is not None

    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    for body in (
        {"name": "x", "scopes": ["checks:read"], "expires_at": past},
        {"name": "x", "scopes": []},
        {"name": "x", "scopes": ["accounts:write"]},
        {"name": "   ", "scopes": ["checks:read"]},
    ):
        refused = await client.post(base, json=body)
        assert refused.status_code == 422, body


async def test_reading_needs_tenants_read_and_writing_needs_tenants_write(
    http: Callable[[Principal], AsyncClient], firm: dict[str, Any]
) -> None:
    base = f"/admin/tenants/{firm['uid']}"
    reader = http(READER)
    for method, path, body in (
        ("POST", "/api-keys", {"name": "x", "scopes": ["checks:read"]}),
        ("POST", "/api-keys/anything0001/revoke", None),
        ("PUT", "/webhook", {"url": "https://firm.example/hooks"}),
        ("DELETE", "/webhook", None),
        ("POST", "/webhook-deliveries/anything0001/resend", None),
    ):
        refused = await reader.request(method, base + path, json=body)
        assert refused.status_code == 403, (method, path)
        assert "tenants:write" in refused.json()["detail"]
    for path in ("/api-keys", "/webhook", "/webhook-deliveries"):
        assert (await reader.get(base + path)).status_code == 200

    nobody = http(Principal(user_id="user_nobody", permissions=["book:read"]))
    assert (await nobody.get(base + "/api-keys")).status_code == 403


# ---- the webhook --------------------------------------------------------------------------------


async def test_the_webhook_is_set_with_a_secret_shown_once_and_removed(
    http: Callable[[Principal], AsyncClient],
    firm: dict[str, Any],
    maker: Maker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _public(monkeypatch)
    client = http(WRITER)
    base = f"/admin/tenants/{firm['uid']}/webhook"
    assert (await client.get(base)).json()["url"] is None

    saved = await client.put(base, json={"url": "https://firm.example/hooks"})
    assert saved.status_code == 200, saved.text
    secret = saved.json()["secret"]
    assert saved.json()["url"] == "https://firm.example/hooks"

    got = await client.get(base)
    assert got.json()["url"] == "https://firm.example/hooks"
    assert secret not in got.text
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        sealed = (
            await s.execute(
                text("SELECT secret_ct FROM compliance.webhooks WHERE tenant_id = :t"),
                {"t": firm["id"]},
            )
        ).scalar_one()
    assert sealed != secret
    assert crypto.decrypt(sealed) == secret

    replaced = await client.put(base, json={"url": "https://firm.example/v2"})
    assert replaced.json()["secret"] != secret

    removed = await client.delete(base)
    assert removed.status_code == 200
    assert removed.json() == {"url": None, "created_at": None, "updated_at": None}
    assert (await client.get(base)).json()["url"] is None


@pytest.mark.parametrize("answer", ["127.0.0.1", "10.0.0.8"])
async def test_the_webhook_refuses_a_host_resolving_to_a_private_address(
    http: Callable[[Principal], AsyncClient],
    firm: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
) -> None:
    _public(monkeypatch, answer)
    client = http(WRITER)
    base = f"/admin/tenants/{firm['uid']}/webhook"
    refused = await client.put(base, json={"url": "https://firm.example/hooks"})
    assert refused.status_code == 422
    assert "public address" in refused.json()["detail"]
    assert (await client.get(base)).json()["url"] is None


async def test_the_webhook_refuses_a_url_that_is_not_https(
    http: Callable[[Principal], AsyncClient], firm: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _public(monkeypatch)
    refused = await http(WRITER).put(
        f"/admin/tenants/{firm['uid']}/webhook", json={"url": "http://firm.example/hooks"}
    )
    assert refused.status_code == 422
    assert "HTTPS" in refused.text


# ---- deliveries ---------------------------------------------------------------------------------


async def _delivery(maker: Maker, firm: dict[str, Any], *, status: str) -> str:
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        webhook_id = (
            await s.execute(
                text(
                    "INSERT INTO compliance.webhooks (tenant_id, url, secret_ct) "
                    "VALUES (:t, 'https://firm.example/hooks', :ct) "
                    "ON CONFLICT (tenant_id) DO UPDATE SET url = EXCLUDED.url RETURNING id"
                ),
                {"t": firm["id"], "ct": crypto.encrypt("s")},
            )
        ).scalar_one()
        return str(
            (
                await s.execute(
                    text(
                        "INSERT INTO compliance.webhook_deliveries "
                        "  (tenant_id, webhook_id, event, payload, status, attempts, last_error) "
                        "VALUES (:t, :w, 'check.updated', CAST(:p AS jsonb), :st, 7, "
                        "  'The endpoint answered 500.') RETURNING uid"
                    ),
                    {
                        "t": firm["id"],
                        "w": webhook_id,
                        "p": '{"event": "check.updated", "uid": "chk000000001", '
                        '"kind": "identity", "status": "refer"}',
                        "st": status,
                    },
                )
            ).scalar_one()
        )


async def test_a_failed_delivery_is_listed_and_can_be_sent_again(
    http: Callable[[Principal], AsyncClient], firm: dict[str, Any], maker: Maker
) -> None:
    failed = await _delivery(maker, firm, status="failed")
    sent = await _delivery(maker, firm, status="sent")
    client = http(WRITER)
    base = f"/admin/tenants/{firm['uid']}/webhook-deliveries"

    listed = (await client.get(base)).json()["items"]
    assert [item["uid"] for item in listed] == [sent, failed]
    row = listed[1]
    assert row["status"] == "failed"
    assert row["check_uid"] == "chk000000001"
    assert row["kind"] == "identity"
    assert row["check_status"] == "refer"
    assert row["attempts"] == 7
    assert row["last_error"] == "The endpoint answered 500."

    resent = await client.post(f"{base}/{failed}/resend")
    assert resent.status_code == 200, resent.text
    delivery = resent.json()["delivery"]
    assert delivery["status"] == "pending"
    assert delivery["attempts"] == 0

    not_failed = await client.post(f"{base}/{sent}/resend")
    assert not_failed.status_code == 409
    assert (await client.post(f"{base}/nosuchrow0001/resend")).status_code == 404

    limited = await client.get(base, params={"limit": 1})
    assert len(limited.json()["items"]) == 1


def test_the_routes_are_on_the_admin_router_under_the_tenant() -> None:
    paths = {route.path for route in partner_access.router.routes}  # type: ignore[attr-defined]
    assert paths == {
        "/admin/tenants/{tenant_uid}/api-keys",
        "/admin/tenants/{tenant_uid}/api-keys/{key_uid}/revoke",
        "/admin/tenants/{tenant_uid}/webhook",
        "/admin/tenants/{tenant_uid}/webhook-deliveries",
        "/admin/tenants/{tenant_uid}/webhook-deliveries/{delivery_uid}/resend",
    }
