"""The ``ref.countries`` catalogue, held in memory for the life of the process.

The table is seed data (``0050_ref_data``), the same kind of fact as ``ref.currencies``.
Nothing in the product writes it, so the process reads it once and then never again.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SQL = "SELECT code, name, adjective, in_eea, identifiers FROM ref.countries ORDER BY name, code"


@dataclass(frozen=True, slots=True)
class Country:
    code: str
    name: str
    adjective: str | None
    in_eea: bool
    identifiers: tuple[object, ...]

    def as_ref(self) -> dict[str, object]:
        """The shape ``GET /ref/countries`` already returns. A copy, so a caller cannot
        edit the process cache by mutating the response."""
        return {
            "code": self.code,
            "name": self.name,
            "adjective": self.adjective,
            "in_eea": self.in_eea,
            "identifiers": copy.deepcopy(self.identifiers),
        }


class CountryCatalogue:
    """Every reference country. Lookup is by ISO code; the list is by name, then code."""

    def __init__(self, rows: Sequence[Country]) -> None:
        self._by_code: Mapping[str, Country] = {row.code: row for row in rows}

    def get(self, code: str | None) -> Country | None:
        if code is None:
            return None
        key = code.strip().upper()
        if not key:
            return None
        return self._by_code.get(key)

    def __contains__(self, code: object) -> bool:
        return isinstance(code, str) and self.get(code) is not None

    def __iter__(self) -> Iterator[Country]:
        return iter(self._by_code.values())

    def __len__(self) -> int:
        return len(self._by_code)

    def ordered(self) -> tuple[Country, ...]:
        """Name, then code — the order ``GET /ref/countries`` has always used."""
        return tuple(sorted(self._by_code.values(), key=lambda row: (row.name, row.code)))

    def name_of(self, code: str | None) -> str | None:
        """The display name for one code, or ``None`` when the code is absent or unknown."""
        row = self.get(code)
        return row.name if row else None

    def names_of(self, codes: Sequence[str] | None) -> list[str] | None:
        """Names in the stored order. An unknown code is skipped, matching the old join.

        ``None`` when there is nothing to resolve, which is what ``array_agg`` returned
        for a null or empty list and for a list whose codes are all unknown.
        """
        if not codes:
            return None
        names = [row.name for code in codes if (row := self.get(str(code))) is not None]
        return names or None


_catalogue: CountryCatalogue | None = None
_lock = asyncio.Lock()


def current() -> CountryCatalogue | None:
    """The loaded catalogue, or ``None`` before the first read."""
    return _catalogue


def clear() -> None:
    """Drop the process cache so the next load reads again.

    The test database is not the process's configured DSN. The suite loads the
    seed from that database once; a leftover empty cache would send the next
    read to localhost instead.
    """
    global _catalogue
    _catalogue = None


def _identifiers(value: object) -> tuple[object, ...]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list | tuple):
        return ()
    return tuple(value)


async def load(session: AsyncSession) -> CountryCatalogue:
    """Read ``ref.countries`` if this process has not already done so.

    A second call returns the same object and does not query. The table does not
    change for the life of the process.
    """
    global _catalogue
    if _catalogue is not None:
        return _catalogue
    rows = (await session.execute(text(_SQL))).mappings().all()
    if not rows:
        raise RuntimeError("ref.countries is empty; the reference seed has not been applied")
    loaded = tuple(
        Country(
            code=str(row["code"]),
            name=str(row["name"]),
            adjective=str(row["adjective"]) if row["adjective"] is not None else None,
            in_eea=bool(row["in_eea"]),
            identifiers=_identifiers(row["identifiers"]),
        )
        for row in rows
    )
    _catalogue = CountryCatalogue(loaded)
    return _catalogue


async def catalogue() -> CountryCatalogue:
    """The process's copy of ``ref.countries``. Loads it on the first call."""
    if _catalogue is not None:
        return _catalogue
    async with _lock:
        if _catalogue is not None:
            return _catalogue
        from app.db import system_transaction

        async with system_transaction() as session:
            return await load(session)


@contextmanager
def override(rows: Sequence[Country]) -> Iterator[CountryCatalogue]:
    """Swap the catalogue for a test, then put back whatever was there."""
    global _catalogue
    previous = _catalogue
    _catalogue = CountryCatalogue(rows)
    try:
        yield _catalogue
    finally:
        _catalogue = previous
