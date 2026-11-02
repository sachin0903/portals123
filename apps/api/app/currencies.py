"""The ``ref.currencies`` catalogue, held in memory for the life of the process.

The table is seed data (``0050_ref_data``). Nothing in the product writes it, so the
process reads it once and then never again. A firm's allow-list is a different fact
and stays on ``auth.tenants`` — this cache is only the reference rows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SQL = "SELECT code, currency, country, symbol, hex_codes FROM ref.currencies ORDER BY code"


@dataclass(frozen=True, slots=True)
class Currency:
    code: str
    name: str
    country: str | None
    symbol: str
    hex_codes: tuple[str, ...]

    def as_ref(self) -> dict[str, str]:
        """The shape ``/ref/currencies`` and the firm currency editor already return."""
        return {"code": self.code, "symbol": self.symbol, "currency": self.name}


class CurrencyCatalogue:
    """Every reference currency. Lookup is by ISO code; order is the code, base first."""

    def __init__(self, rows: Sequence[Currency]) -> None:
        self._by_code: Mapping[str, Currency] = {row.code: row for row in rows}

    def get(self, code: str | None) -> Currency | None:
        if not code:
            return None
        return self._by_code.get(code.strip().upper())

    def __contains__(self, code: object) -> bool:
        return isinstance(code, str) and self.get(code) is not None

    def __iter__(self) -> Iterator[Currency]:
        return iter(self._by_code.values())

    def __len__(self) -> int:
        return len(self._by_code)

    def ordered(self, *, base: str | None = None) -> tuple[Currency, ...]:
        """Base currency first, then ISO code. The same order the settings query used."""
        head = (base or "").strip().upper()
        return tuple(sorted(self._by_code.values(), key=lambda row: (row.code != head, row.code)))


_catalogue: CurrencyCatalogue | None = None
_lock = asyncio.Lock()


def current() -> CurrencyCatalogue | None:
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


async def load(session: AsyncSession) -> CurrencyCatalogue:
    """Read ``ref.currencies`` if this process has not already done so.

    A second call returns the same object and does not query. The table does not
    change for the life of the process, so a refresh would only be a way to observe
    a write that is not supposed to happen.
    """
    global _catalogue
    if _catalogue is not None:
        return _catalogue
    rows = (await session.execute(text(_SQL))).mappings().all()
    if not rows:
        raise RuntimeError("ref.currencies is empty; the reference seed has not been applied")
    loaded = tuple(
        Currency(
            code=str(row["code"]),
            name=str(row["currency"] or ""),
            country=str(row["country"]) if row["country"] is not None else None,
            symbol=str(row["symbol"] or ""),
            hex_codes=tuple(row["hex_codes"] or ()),
        )
        for row in rows
    )
    _catalogue = CurrencyCatalogue(loaded)
    return _catalogue


async def catalogue() -> CurrencyCatalogue:
    """The process's copy of ``ref.currencies``. Loads it on the first call."""
    if _catalogue is not None:
        return _catalogue
    async with _lock:
        if _catalogue is not None:
            return _catalogue
        # Imported here: ``app.db`` pulls in the request stack, and this module is
        # imported while the application is still being constructed.
        from app.db import system_transaction

        async with system_transaction() as session:
            return await load(session)


@contextmanager
def override(rows: Sequence[Currency]) -> Iterator[CurrencyCatalogue]:
    """Swap the catalogue for a test, then put back whatever was there."""
    global _catalogue
    previous = _catalogue
    _catalogue = CurrencyCatalogue(rows)
    try:
        yield _catalogue
    finally:
        _catalogue = previous
