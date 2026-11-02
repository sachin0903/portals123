"""GET /internal/admin-branding — the admin portal's shell-brand read.

The admin portal has no host row in ``auth.tenant_environments`` (only client/platform hosts
exist), so it cannot ride ``/internal/env-config``; this endpoint is the minimal read instead:
the Graphene (operations) tenant's CLIENT-portal logo set + name, and nothing else — the admin
surface keeps the product look, so none of the theming tokens may leak through here.

Real HTTP against the testcontainer Postgres (the ``test_admin_hardening.py`` conventions):
``internal_session`` builds its own sessions, so seeds commit through a session of the same
maker. The operations tenant resolves by matching ``settings.workos_client_id`` against the
registered core environment (``environments.operations_tenant_id``), monkeypatched unique per
test so the shared database never bleeds between runs.
"""

import json
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
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


async def _seed_ops_tenant(
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    *,
    theme: dict[str, object] | None,
) -> None:
    """An operations tenant registered as the core WorkOS environment (so
    ``operations_tenant_id`` resolves it), carrying ``theme`` — committed, because the endpoint
    reads through its own session."""
    s = uuid.uuid4().hex[:8]
    core = f"client_core_iab_{s}"
    monkeypatch.setattr(settings, "workos_client_id", core)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name, theme) "
                    "VALUES (:u, :u, :n, CAST(:th AS jsonb)) RETURNING id"
                ),
                {
                    "u": f"iabops_{s}",
                    "n": f"Graphene Platforms {s}",
                    "th": json.dumps(theme) if theme is not None else None,
                },
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id) "
                "VALUES (:t, 'production', :c)"
            ),
            {"t": tid, "c": core},
        )


async def _get(headers: dict[str, str]) -> tuple[int, dict[str, object]]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/internal/admin-branding", headers=headers)
        return res.status_code, (res.json() if res.status_code == 200 else {})


@pytest.mark.asyncio
async def test_serves_the_operations_tenants_client_logo_set_and_nothing_else(
    internal_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the logo assets + name ride out — no colours/fonts/material: the admin surface keeps
    the product look, and the payload shape IS that rule."""
    await _seed_ops_tenant(
        internal_sessions,
        monkeypatch,
        theme={
            "v": 2,
            "client": {
                "logo_url": "https://cdn.test/graphene.svg",
                "logo_dark_url": "https://cdn.test/graphene-dark.svg",
                "icon_url": "https://cdn.test/graphene-mark.svg",
                "logo_variant": "wordmark",
                "colors": {"light": {"primary": "#123456"}},
            },
        },
    )
    status, body = await _get({"x-internal-token": TOKEN})
    assert status == 200
    # The CLOSED payload shape: colours (and every other theming token) must never appear.
    assert set(body.keys()) == {"app_name", "logo_url", "logo_dark_url", "icon_url", "logo_variant"}
    assert body["logo_url"] == "https://cdn.test/graphene.svg"
    assert body["logo_dark_url"] == "https://cdn.test/graphene-dark.svg"
    assert body["icon_url"] == "https://cdn.test/graphene-mark.svg"
    assert body["logo_variant"] == "wordmark"
    assert str(body["app_name"]).startswith("Graphene Platforms")


@pytest.mark.asyncio
async def test_unset_branding_serves_nulls_so_the_portal_keeps_its_static_brand(
    internal_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_ops_tenant(internal_sessions, monkeypatch, theme=None)
    status, body = await _get({"x-internal-token": TOKEN})
    assert status == 200
    assert (body["logo_url"], body["logo_dark_url"], body["icon_url"]) == (None, None, None)


@pytest.mark.asyncio
async def test_proxy_asset_paths_are_served_not_rejected(
    internal_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    src = "/_assets/logo-a02d18bf9b52c496.png"
    await _seed_ops_tenant(
        internal_sessions,
        monkeypatch,
        theme={"v": 2, "client": {"logo_url": src, "icon_url": src}},
    )
    status, body = await _get({"x-internal-token": TOKEN})
    assert status == 200
    assert body["logo_url"] == src
    assert body["icon_url"] == src


@pytest.mark.asyncio
async def test_a_large_inline_logo_is_withheld_so_the_admin_shell_keeps_its_mark(
    internal_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A data-URI bitmap decoded on the main thread becomes the shell's Largest Contentful Paint,
    so the payload carries None for it; a small inline SVG icon still rides out."""
    import base64
    import os
    from io import BytesIO

    from PIL import Image

    out = BytesIO()
    Image.frombytes("RGBA", (600, 200), os.urandom(600 * 200 * 4)).save(out, "PNG")
    big = f"data:image/png;base64,{base64.b64encode(out.getvalue()).decode('ascii')}"
    small = "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode("ascii")
    await _seed_ops_tenant(
        internal_sessions,
        monkeypatch,
        theme={"v": 2, "client": {"logo_url": big, "logo_dark_url": big, "icon_url": small}},
    )
    status, body = await _get({"x-internal-token": TOKEN})
    assert status == 200
    assert (body["logo_url"], body["logo_dark_url"]) == (None, None)
    assert body["icon_url"] == small


@pytest.mark.asyncio
async def test_unparseable_theme_degrades_to_nulls_not_a_500(
    internal_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_ops_tenant(
        internal_sessions,
        monkeypatch,
        theme={"v": 2, "client": {"logo_url": "javascript:alert(1)"}},
    )
    status, body = await _get({"x-internal-token": TOKEN})
    assert status == 200
    assert (body["logo_url"], body["logo_dark_url"], body["icon_url"]) == (None, None, None)


@pytest.mark.asyncio
async def test_missing_core_environment_is_404_like_an_unknown_host(
    internal_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local dev before the core environment is registered: the portal must get the same 404 an
    unknown env-config host gets, and fall back to the static Graphene brand."""
    monkeypatch.setattr(settings, "workos_client_id", f"client_core_never_{uuid.uuid4().hex[:8]}")
    status, _ = await _get({"x-internal-token": TOKEN})
    assert status == 404


@pytest.mark.asyncio
async def test_token_gate_is_a_404_never_an_advert(
    internal_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_ops_tenant(
        internal_sessions,
        monkeypatch,
        theme={"v": 2, "client": {"logo_url": "https://cdn.test/graphene.svg"}},
    )
    for headers in ({}, {"x-internal-token": "wrong"}):
        status, _ = await _get(headers)
        assert status == 404
