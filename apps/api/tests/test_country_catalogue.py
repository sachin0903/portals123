"""ref.countries is read once per process and then left alone."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app import countries
from app.countries import Country


def _session(rows: list[dict[str, object]]) -> MagicMock:
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


_ROWS = [
    {
        "code": "GB",
        "name": "United Kingdom",
        "adjective": "British",
        "in_eea": True,
        "identifiers": [{"name": "National Insurance Number", "priority": 0}],
    },
    {
        "code": "SE",
        "name": "Sweden",
        "adjective": "Swedish",
        "in_eea": True,
        "identifiers": "[]",
    },
]


@pytest.mark.asyncio
async def test_the_catalogue_is_read_once_and_then_does_not_change() -> None:
    session = _session(_ROWS)
    previous = countries.current()
    countries._catalogue = None  # noqa: SLF001
    try:
        loaded = await countries.load(session)
        again = await countries.load(session)
    finally:
        countries._catalogue = previous  # noqa: SLF001
    assert again is loaded
    assert session.execute.await_count == 1
    assert loaded.get("se") is not None
    assert loaded.get("se").name == "Sweden"  # type: ignore[union-attr]
    assert [row.code for row in loaded.ordered()] == ["SE", "GB"]


@pytest.mark.asyncio
async def test_an_empty_table_is_not_cached() -> None:
    previous = countries.current()
    countries._catalogue = None  # noqa: SLF001
    try:
        with pytest.raises(RuntimeError, match="ref.countries is empty"):
            await countries.load(_session([]))
        assert countries.current() is None
    finally:
        countries._catalogue = previous  # noqa: SLF001


def test_names_keep_order_and_skip_an_unknown_code() -> None:
    catalogue = countries.CountryCatalogue(
        (
            Country(code="SE", name="Sweden", adjective="Swedish", in_eea=True, identifiers=()),
            Country(
                code="GB",
                name="United Kingdom",
                adjective="British",
                in_eea=True,
                identifiers=(),
            ),
        )
    )
    assert catalogue.name_of("SE") == "Sweden"
    assert catalogue.name_of("AN") is None
    assert catalogue.name_of(None) is None
    assert catalogue.names_of(["SE", "AN", "GB"]) == ["Sweden", "United Kingdom"]
    assert catalogue.names_of(["AN"]) is None
    assert catalogue.names_of(None) is None
    assert catalogue.names_of([]) is None
