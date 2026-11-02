"""Global reference reads used by onboarding controls."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import Principal, get_current_principal
from app.currencies import Currency, override
from app.db import target_tenant_session
from app.main import app

_REFERENCE = (
    Currency(code="EUR", name="Euro", country="Europe", symbol="€", hex_codes=("20ac",)),
    Currency(code="USD", name="US Dollar", country="United States", symbol="$", hex_codes=("24",)),
    Currency(
        code="GBP",
        name="Pound sterling",
        country="United Kingdom",
        symbol="£",
        hex_codes=("a3",),
    ),
)

_PRINCIPAL = Principal(
    user_id="user_ref_currencies_test",
    organisation_id="org_ref_currencies_test",
    role="adviser",
    permissions=[],
    client_id="client_ref_currencies_test",
)


@pytest.fixture
def as_user() -> Iterator[None]:
    app.dependency_overrides[get_current_principal] = lambda: _PRINCIPAL
    yield
    app.dependency_overrides.pop(get_current_principal, None)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _policy_session(base: str, allowed: list[str]) -> MagicMock:
    """The allow-list query. Symbols are not in this result — they come from the cache."""
    result = MagicMock()
    result.mappings.return_value.one_or_none.return_value = {
        "base_currency": base,
        "allowed": allowed,
    }
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_lists_only_the_firms_currencies_with_its_base_first(
    client: AsyncClient, as_user: None
) -> None:
    async def _session() -> AsyncIterator[Any]:
        yield _policy_session("EUR", ["USD", "EUR"])

    app.dependency_overrides[target_tenant_session] = _session
    try:
        with override(_REFERENCE):
            r = await client.get("/ref/currencies")
    finally:
        app.dependency_overrides.pop(target_tenant_session, None)
    assert r.status_code == 200
    body = r.json()
    assert body[0] == {
        "code": "EUR",
        "symbol": "€",
        "currency": "Euro",
        "is_base": True,
    }
    assert {row["code"] for row in body} == {"EUR", "USD"}


@pytest.mark.asyncio
async def test_product_currency_filter_is_bound_to_the_query(
    client: AsyncClient, as_user: None
) -> None:
    session = _policy_session("GBP", ["GBP"])

    async def _session() -> AsyncIterator[Any]:
        yield session

    app.dependency_overrides[target_tenant_session] = _session
    try:
        with override(_REFERENCE):
            response = await client.get("/ref/currencies?product_id=42")
    finally:
        app.dependency_overrides.pop(target_tenant_session, None)
    assert response.status_code == 200
    assert session.execute.await_args.args[1] == {"product": 42}


@pytest.mark.asyncio
async def test_auth_is_required(client: AsyncClient) -> None:
    r = await client.get("/ref/currencies")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_lists_countries_with_identifier_rules(client: AsyncClient, as_user: None) -> None:
    from app.countries import Country
    from app.countries import override as override_countries

    identifiers = [
        {
            "name": "National Insurance Number",
            "regex": ["^[A-Z]{2}[0-9]{6}[A-D]$"],
            "priority": 0,
        }
    ]
    rows = [
        Country(
            code="SE",
            name="Sweden",
            adjective="Swedish",
            in_eea=True,
            identifiers=(),
        ),
        Country(
            code="GB",
            name="United Kingdom",
            adjective="British",
            in_eea=True,
            identifiers=tuple(identifiers),
        ),
    ]
    with override_countries(rows):
        r = await client.get("/ref/countries")
    assert r.status_code == 200
    assert r.json() == [
        {
            "code": "SE",
            "name": "Sweden",
            "adjective": "Swedish",
            "in_eea": True,
            "identifiers": [],
        },
        {
            "code": "GB",
            "name": "United Kingdom",
            "adjective": "British",
            "in_eea": True,
            "identifiers": identifiers,
        },
    ]
