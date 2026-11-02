"""Identifiers and rows for ``raw.asset_prices``.

The historical-price call is one as-of date and a list of market identifiers.
The Assets report's own ``PRICE`` is the latest print only, and it is not a
history. Cash and money-market rows have no ISIN, SEDOL, CUSIP or ticker, so
they are not asked for: the API has no scheme for a SEI instrument id.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pyarrow as pa

from sei_sync.api import PRICE_BATCH, as_day, format_asof, parse_asof

# Scheme names are the spec's, in the order a listing should be asked for.
# ISIN is global; SEDOL and CUSIP are the trading identifiers the same row
# also carries; ticker is the last one the call accepts.
_IDENTIFIERS = (
    ("ISIN", "ISIN"),
    ("SEDOL", "SEDOL_NUMBER"),
    ("CUSIP", "CUSIP"),
    ("Ticker", "TICKER"),
)

SCHEMA = pa.schema(
    [
        pa.field("firm_id", pa.string(), nullable=False),
        pa.field("instrument_id", pa.string()),
        pa.field("input_scheme", pa.string(), nullable=False),
        pa.field("external_id", pa.string(), nullable=False),
        pa.field("as_of_date", pa.date32(), nullable=False),
        pa.field("price_date", pa.date32()),
        pa.field("price", pa.decimal128(38, 10)),
        pa.field("clean_price", pa.decimal128(38, 10)),
        pa.field("price_currency", pa.string()),
        pa.field("isin", pa.string()),
        pa.field("sedol", pa.string()),
        pa.field("cusip", pa.string()),
        pa.field("ticker", pa.string()),
        pa.field("sei_instrument_id", pa.string()),
        pa.field("instrument_name", pa.string()),
        pa.field("fetched_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)


@dataclass(frozen=True)
class Instrument:
    """One asset the price call can name."""

    instrument_id: str
    scheme: str
    external_id: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.scheme.upper(), self.external_id.upper())


def batches(items: list[Instrument], size: int = PRICE_BATCH) -> Iterator[list[Instrument]]:
    """Split a universe into lists the price call is asked with."""
    if size < 1:
        raise ValueError("batch size must be at least one")
    for start in range(0, len(items), size):
        yield items[start : start + size]


def criteria(items: list[Instrument], day: date) -> list[dict[str, str]]:
    """One as-of date, repeated on every identifier. The call has no range."""
    asof = format_asof(day)
    return [
        {"inputScheme": item.scheme, "externalID": item.external_id, "asofDate": asof}
        for item in items
    ]


def instruments_from(handle: Any) -> list[Instrument]:
    """Distinct priceable instruments on the latest Assets drop.

    The report repeats an instrument once per listing and once per firm. The
    price does not differ by firm, so the identifier is enough. A row with no
    market identifier is skipped.
    """
    if handle is None:
        return []
    table = handle.table()
    if table is None or table.num_rows == 0:
        return []
    columns = {name.upper(): name for name in table.column_names}
    instrument_col = columns.get("INSTRUMENT_ID")
    if instrument_col is None:
        return []

    def values(name: str) -> list[Any]:
        actual = columns.get(name)
        if actual is None:
            return [None] * table.num_rows
        return list(table.column(actual).to_pylist())

    names = ["INSTRUMENT_ID", "DT", *(column for _, column in _IDENTIFIERS)]
    lists = {name: values(name) for name in names}
    days = [as_day(value) for value in lists["DT"]]
    latest = max((day for day in days if day is not None), default=None)
    chosen: dict[tuple[str, str], Instrument] = {}
    for index, instrument_id in enumerate(lists["INSTRUMENT_ID"]):
        if latest is not None and days[index] != latest:
            continue
        if not instrument_id or not str(instrument_id).strip():
            continue
        identity = _identifier_at(lists, index)
        if identity is None:
            continue
        scheme, external_id = identity
        item = Instrument(str(instrument_id).strip(), scheme, external_id)
        current = chosen.get(item.key)
        if current is None or item.instrument_id < current.instrument_id:
            chosen[item.key] = item
    return [chosen[key] for key in sorted(chosen)]


def _identifier_at(lists: dict[str, list[Any]], index: int) -> tuple[str, str] | None:
    for scheme, column in _IDENTIFIERS:
        value = lists[column][index]
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return scheme, text
    return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.0000000001"))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def flatten(
    rows: list[dict[str, Any]],
    *,
    fetched_at: datetime,
    firm_id: str,
    as_of: date,
    instruments: dict[tuple[str, str], str],
) -> pa.Table:
    """One day's response into the declared schema.

    ``as_of`` is the date we asked for. ``price_date`` is the print SEI returned,
    which can be earlier. The merge key is the request, so a restatement of the
    same day replaces the row.
    """
    built: dict[str, list[Any]] = {field.name: [] for field in SCHEMA}
    for row in rows:
        scheme = _text(row.get("inputScheme"))
        external_id = _text(row.get("externalID"))
        if scheme is None or external_id is None:
            continue
        built["firm_id"].append(firm_id)
        built["instrument_id"].append(instruments.get((scheme.upper(), external_id.upper())))
        built["input_scheme"].append(scheme)
        built["external_id"].append(external_id)
        built["as_of_date"].append(as_of)
        built["price_date"].append(parse_asof(row.get("priceDate")))
        built["price"].append(_decimal(row.get("price")))
        built["clean_price"].append(_decimal(row.get("cleanPrice")))
        built["price_currency"].append(_text(row.get("priceCurrency")))
        built["isin"].append(_text(row.get("isin")))
        built["sedol"].append(_text(row.get("sedol")))
        built["cusip"].append(_text(row.get("cusip")))
        built["ticker"].append(_text(row.get("ticker")))
        built["sei_instrument_id"].append(_text(row.get("instrumentId")))
        built["instrument_name"].append(_text(row.get("instrumentLongName")))
        built["fetched_at"].append(fetched_at)
    if not built["firm_id"]:
        return SCHEMA.empty_table()
    return pa.table(built, schema=SCHEMA)
