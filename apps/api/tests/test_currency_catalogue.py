"""ref.currencies is read once per process and then left alone."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app import currencies
from app.currencies import Currency


def _session(rows: list[dict[str, object]]) -> MagicMock:
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


_ROWS = [
    {
        "code": "GBP",
        "currency": "Pound sterling",
        "country": "United Kingdom",
        "symbol": "£",
        "hex_codes": ["a3"],
    },
    {
        "code": "USD",
        "currency": "US Dollar",
        "country": "United States",
        "symbol": "$",
        "hex_codes": ["24"],
    },
]


@pytest.mark.asyncio
async def test_the_catalogue_is_read_once_and_then_does_not_change() -> None:
    previous = currencies.current()
    currencies._catalogue = None  # noqa: SLF001 — the cache is the thing under test
    session = _session(_ROWS)
    try:
        first = await currencies.load(session)
        second = await currencies.load(session)
        assert first is second
        assert session.execute.await_count == 1
        usd = first.get("usd")
        assert usd is not None and usd.symbol == "$"
        assert [row.code for row in first.ordered(base="USD")] == ["USD", "GBP"]
    finally:
        currencies._catalogue = previous  # noqa: SLF001


@pytest.mark.asyncio
async def test_an_empty_reference_table_is_not_cached() -> None:
    """Caching nothing would freeze a database that has not been seeded."""
    previous = currencies.current()
    currencies._catalogue = None  # noqa: SLF001
    session = _session([])
    try:
        with pytest.raises(RuntimeError, match="empty"):
            await currencies.load(session)
        assert currencies.current() is None
    finally:
        currencies._catalogue = previous  # noqa: SLF001


def test_a_code_outside_the_catalogue_is_unknown() -> None:
    catalogue = currencies.CurrencyCatalogue(
        (Currency(code="GBP", name="Pound sterling", country=None, symbol="£", hex_codes=()),)
    )
    assert "GBP" in catalogue
    assert "AAA" not in catalogue
