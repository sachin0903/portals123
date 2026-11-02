"""``raw.asset_prices`` — which instruments can be asked for, and how a day is shaped.

The call is one as-of date and a list of market identifiers. These tests pin that
shape without calling SEI.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pytest

from sei_sync.api import PRICE_BATCH, ProxyUnavailable, format_asof, parse_asof
from sei_sync.asset_prices import (
    SCHEMA,
    Instrument,
    batches,
    criteria,
    flatten,
    instruments_from,
)
from sei_sync.pull import fetch_asset_prices
from sei_sync.settings import SeiSettings

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "models"))
import api_asset_prices  # noqa: E402

NOW = datetime(2026, 10, 14, tzinfo=UTC)


class _Handle:
    def __init__(self, rows: list[dict[object]]) -> None:
        self._table = pa.Table.from_pylist(rows)

    def table(self) -> pa.Table:
        return self._table


def test_the_as_of_date_is_the_spec_shape_not_iso() -> None:
    assert format_asof(date(2020, 8, 28)) == "28-Aug-2020"
    assert parse_asof("28-Aug-2020") == date(2020, 8, 28)
    assert parse_asof("28-AUG-2020") == date(2020, 8, 28)


def test_cash_and_an_older_drop_are_not_asked_for() -> None:
    """Money-market and cash rows have no market identifier. An older drop is not the universe."""
    handle = _Handle(
        [
            {
                "dt": date(2026, 10, 12),
                "INSTRUMENT_ID": "OLD",
                "ISIN": "GB00OLD00001",
            },
            {
                "dt": date(2026, 10, 13),
                "INSTRUMENT_ID": "FUND",
                "ISIN": "GB00B1CKQD97",
                "SEDOL_NUMBER": "B1CKQD9",
            },
            {
                "dt": date(2026, 10, 13),
                "INSTRUMENT_ID": "FUND",
                "ISIN": "GB00B1CKQD97",
                "SEDOL_NUMBER": "B1CKQD9",
            },
            {
                "dt": date(2026, 10, 13),
                "INSTRUMENT_ID": "CASH",
                "ISIN": None,
                "SEDOL_NUMBER": "",
                "CUSIP": None,
                "TICKER": None,
            },
        ]
    )
    assert instruments_from(handle) == [Instrument("FUND", "ISIN", "GB00B1CKQD97")]


def test_a_day_is_one_criteria_list_and_a_hundred_is_the_batch() -> None:
    instruments = [
        Instrument(str(index), "ISIN", f"GB{index:010d}") for index in range(PRICE_BATCH + 1)
    ]
    assert [len(batch) for batch in batches(instruments)] == [PRICE_BATCH, 1]
    assert criteria(instruments[:1], date(2024, 6, 28)) == [
        {"inputScheme": "ISIN", "externalID": "GB0000000000", "asofDate": "28-Jun-2024"}
    ]


def test_a_price_row_keeps_the_day_we_asked_for_and_the_print_date() -> None:
    table = flatten(
        [
            {
                "inputScheme": "ISIN",
                "externalID": "GB00B1CKQD97",
                "price": "123.45",
                "cleanPrice": "123.40",
                "priceDate": "27-Jun-2024",
                "priceCurrency": "GBP",
                "isin": "GB00B1CKQD97",
                "instrumentId": "550938427",
                "instrumentLongName": "A fund",
            }
        ],
        fetched_at=NOW,
        firm_id="3102",
        as_of=date(2024, 6, 28),
        instruments={("ISIN", "GB00B1CKQD97"): "FUND"},
    )
    assert table.schema == SCHEMA
    row = table.to_pylist()[0]
    assert row["as_of_date"] == date(2024, 6, 28)
    assert row["price_date"] == date(2024, 6, 27)
    assert row["price"] == Decimal("123.4500000000")
    assert row["instrument_id"] == "FUND"
    assert row["sei_instrument_id"] == "550938427"


def test_a_row_without_an_identifier_is_dropped() -> None:
    table = flatten(
        [{"price": "1"}],
        fetched_at=NOW,
        firm_id="3102",
        as_of=date(2024, 6, 28),
        instruments={},
    )
    assert table.num_rows == 0
    assert table.schema == SCHEMA


def test_the_first_price_scan_starts_at_the_open_date() -> None:
    settings = SeiSettings(api_base_url="https://example.invalid", api_lookback_days=30)
    start, end = api_asset_prices._window(
        None, None, date(2024, 6, 28), settings, today=date(2026, 10, 14)
    )
    assert (start, end) == (date(2024, 6, 28), date(2026, 10, 13))


def test_the_next_price_scan_overlaps_the_latest_as_of() -> None:
    """``as_of_date`` is the day we asked for, so its maximum is how far the scan reached."""
    settings = SeiSettings(api_base_url="https://example.invalid", api_lookback_days=30)
    stored = api_asset_prices.StoredSync(latest=date(2026, 10, 1))
    start, end = api_asset_prices._window(
        None, stored, date(2024, 6, 28), settings, today=date(2026, 10, 14)
    )
    assert (start, end) == (date(2026, 9, 1), date(2026, 10, 13))


def test_a_rebuild_keeps_the_stored_day_and_prefers_the_new_print(monkeypatch) -> None:  # noqa: ANN001
    existing = pa.table(
        {
            "input_scheme": ["ISIN", "ISIN"],
            "external_id": ["GB1", "GB2"],
            "as_of_date": [date(2024, 6, 28), date(2024, 6, 29)],
            "price": ["1", "2"],
        }
    )
    fresh = pa.table(
        {
            "input_scheme": ["ISIN"],
            "external_id": ["GB2"],
            "as_of_date": [date(2024, 6, 29)],
            "price": ["9"],
        }
    )
    monkeypatch.setattr(api_asset_prices, "_existing_rows", lambda: existing)
    stored = api_asset_prices.StoredSync(latest=date(2024, 6, 29))
    out = api_asset_prices._publish(fresh, cursor=None, stored=stored)
    got = {(row["external_id"], row["as_of_date"]): row["price"] for row in out.to_pylist()}
    assert got == {("GB1", date(2024, 6, 28)): "1", ("GB2", date(2024, 6, 29)): "9"}


def test_unknown_resource_is_a_missing_proxy() -> None:
    from sei_sync.api import _proxy_missing

    body = (
        '{ "code": "404", "message": "Raising fault. Fault name : unknown-resource",'
        ' "more_info": "Not Found" }'
    )
    assert _proxy_missing(body)


async def test_a_missing_price_proxy_stops_the_scan(monkeypatch) -> None:
    calls = {"n": 0}

    class _Api:
        def __init__(self, settings: SeiSettings) -> None:
            del settings

        async def __aenter__(self) -> _Api:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def asset_prices(self, firm_id: str, criteria: list[dict[str, str]]) -> list[dict]:
            del firm_id, criteria
            calls["n"] += 1
            raise ProxyUnavailable(
                'HTTP 404: { "message": "Raising fault. Fault name : unknown-resource" }'
            )

    monkeypatch.setattr("sei_sync.pull.SeiApi", _Api)
    instruments = [Instrument(str(i), "ISIN", f"GB{i:010d}") for i in range(3)]
    settings = SeiSettings(api_base_url="https://example.invalid", api_concurrency=1)
    with pytest.raises(ProxyUnavailable):
        await fetch_asset_prices(
            settings, "3102", instruments, date(2024, 12, 13), date(2024, 12, 15)
        )
    assert calls["n"] == 1


async def test_the_price_model_does_not_call_sei(monkeypatch) -> None:
    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("raw.asset_prices is disabled")

    monkeypatch.setattr("sei_sync.api.SeiApi.__init__", _refuse)
    out = await api_asset_prices.raw_asset_prices(None, None)
    assert out.num_rows == 0
    assert out.schema == SCHEMA
