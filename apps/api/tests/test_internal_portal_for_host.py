"""GET /internal/portal-for-host — the ingress router's host→portal lookup.

Tenants bring their own domains, so the origin proxy cannot infer ``client`` vs ``platform``
from the hostname; it asks here and routes on the ``X-Portal`` header. The read must carry
the portal word and nothing else — no WorkOS material — because it runs once per request.

Real HTTP against the testcontainer Postgres (``test_internal_admin_branding.py`` conventions).
"""

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import db as db_mod
from app.config import settings
from app.main import app
from app.routers import internal

TOKEN = "test-internal-token"  # noqa: S105 - the test's own shared-secret fixture, not a credential


@pytest_asyncio.fixture
async def internal_sessions(
    db: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "SessionLocal", maker)
    monkeypatch.setattr(internal, "SessionLocal", maker)
    monkeypatch.setattr(settings, "internal_api_token", TOKEN)
    yield maker
    await engine.dispose()


async def _seed_tenant_hosts(
    maker: async_sessionmaker[AsyncSession],
) -> tuple[str, str, str]:
    """One tenant environment with a client, platform and operations host on its own domains —
    nothing about the names says which portal they are."""
    s = uuid.uuid4().hex[:8]
    client_host = f"app.firm-{s}.co.uk"
    platform_host = f"advisers.firm-{s}.co.uk"
    ops_host = f"ops-{s}.internal.example"
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :u, :n) RETURNING id"
                ),
                {"u": f"pfh_{s}", "n": f"Firm {s}"},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO auth.tenant_environments "
                "  (tenant_id, stage, workos_client_id, client_portal_host, platform_host, "
                "   operations_host) "
                "VALUES (:t, 'production', :c, :ch, :ph, :oh)"
            ),
            {
                "t": tid,
                "c": f"client_pfh_{s}",
                "ch": client_host,
                "ph": platform_host,
                "oh": ops_host,
            },
        )
    return client_host, platform_host, ops_host


async def _get(host: str, headers: dict[str, str]) -> Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/internal/portal-for-host", params={"host": host}, headers=headers)


@pytest.mark.asyncio
async def test_names_the_portal_for_each_registered_host(
    internal_sessions: async_sessionmaker[AsyncSession],
) -> None:
    client_host, platform_host, ops_host = await _seed_tenant_hosts(internal_sessions)
    for host, expected in (
        (client_host, "client"),
        (platform_host, "platform"),
        (ops_host, "operations"),
    ):
        res = await _get(host, {"x-internal-token": TOKEN})
        assert res.status_code == 200, host
        # The header is what a forward-auth hop copies; the body is for humans and curl.
        assert res.headers["x-portal"] == expected
        assert res.json() == {"portal": expected}


@pytest.mark.asyncio
async def test_carries_no_workos_material(
    internal_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A per-request routing read: the payload is the portal word, full stop."""
    client_host, _, _ = await _seed_tenant_hosts(internal_sessions)
    res = await _get(client_host, {"x-internal-token": TOKEN})
    assert set(res.json()) == {"portal"}
    assert "workos" not in res.text.lower()


@pytest.mark.asyncio
async def test_unknown_host_is_404(
    internal_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The router turns this into a 404 to the browser: an unregistered domain reaches nothing."""
    res = await _get("nobody.example", {"x-internal-token": TOKEN})
    assert res.status_code == 404
    assert "x-portal" not in res.headers


@pytest.mark.asyncio
async def test_token_gated_like_the_rest_of_internal(
    internal_sessions: async_sessionmaker[AsyncSession],
) -> None:
    client_host, _, _ = await _seed_tenant_hosts(internal_sessions)
    assert (await _get(client_host, {})).status_code == 404
    assert (await _get(client_host, {"x-internal-token": "wrong"})).status_code == 404
