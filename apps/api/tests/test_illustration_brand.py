"""The illustration cover takes the firm's colour and logo, and refuses an unsafe colour."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.render.illustration_brand import DEFAULT_INK, brand_for_illustration, safe_brand_color

_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_a_colour_that_can_break_out_of_css_is_replaced() -> None:
    assert safe_brand_color("#0b3d2e") == "#0b3d2e"
    assert safe_brand_color("oklch(0.45 0.08 160)") == "oklch(0.45 0.08 160)"
    assert safe_brand_color(None) == DEFAULT_INK
    assert safe_brand_color('red; } body { background: url("http://x")') == DEFAULT_INK


async def _scalar(sess: AsyncSession, sql: str, **params: Any) -> Any:
    return (await sess.execute(text(sql), params)).scalar_one_or_none()


@asynccontextmanager
async def _svc(maker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        yield session


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_client_colour_and_logo_are_copied_onto_the_illustration(maker) -> None:
    async with _svc(maker) as sess:
        s = uuid.uuid4().hex[:8]
        tenant = int(
            await _scalar(
                sess,
                "INSERT INTO auth.tenants (uid, alias, name, theme) "
                "VALUES (:u, :a, 'Firm', CAST(:theme AS jsonb)) RETURNING id",
                u=f"br_{s}",
                a=f"br-{s}",
                theme=json.dumps(
                    {
                        "client": {
                            "colors": {"light": {"primary": "#0b3d2e"}},
                            "logo_url": _PNG,
                        }
                    }
                ),
            )
        )
        brand = await brand_for_illustration(sess, tenant)
    assert brand["ink_color"] == "#0b3d2e"
    assert brand["brand_color"] == "#0b3d2e"
    assert brand["logo"] == _PNG


@pytest.mark.asyncio
async def test_a_platform_colour_is_used_when_the_client_portal_has_none(maker) -> None:
    async with _svc(maker) as sess:
        s = uuid.uuid4().hex[:8]
        tenant = int(
            await _scalar(
                sess,
                "INSERT INTO auth.tenants (uid, alias, name, theme) "
                "VALUES (:u, :a, 'Firm', CAST(:theme AS jsonb)) RETURNING id",
                u=f"br_{s}",
                a=f"br-{s}",
                theme='{"platform": {"colors": {"light": {"primary": "#112233"}}}}',
            )
        )
        brand = await brand_for_illustration(sess, tenant)
    assert brand["ink_color"] == "#112233"
    assert brand["brand_color"] == "#112233"
    assert brand["logo"] is None


@pytest.mark.asyncio
async def test_the_accent_is_the_brand_token_and_the_ink_is_primary(maker) -> None:
    async with _svc(maker) as sess:
        s = uuid.uuid4().hex[:8]
        tenant = int(
            await _scalar(
                sess,
                "INSERT INTO auth.tenants (uid, alias, name, theme) "
                "VALUES (:u, :a, 'Firm', CAST(:theme AS jsonb)) RETURNING id",
                u=f"br_{s}",
                a=f"br-{s}",
                theme=json.dumps(
                    {"client": {"colors": {"light": {"primary": "#102030", "brand": "#0b3d2e"}}}}
                ),
            )
        )
        brand = await brand_for_illustration(sess, tenant)
    assert brand["ink_color"] == "#102030"
    assert brand["brand_color"] == "#0b3d2e"
