"""records.py: normalisation, Arrow conversion, event keys."""

from __future__ import annotations

import json

import pyarrow as pa

from seccl_sync.records import event_key, normalise_record, records_to_table, snake_case


def test_snake_case_boundaries() -> None:
    assert snake_case("transactionDateFrom") == "transaction_date_from"
    assert snake_case("ISACode") == "isa_code"
    assert snake_case("already_snake") == "already_snake"


def test_normalise_serialises_nested_values() -> None:
    row = normalise_record(
        {"clientId": "C1", "bankDetails": {"sortCode": "01-02-03"}, "tags": [1, 2]}
    )
    assert row["client_id"] == "C1"
    assert json.loads(row["bank_details"]) == {"sortCode": "01-02-03"}
    assert json.loads(row["tags"]) == [1, 2]


def test_records_to_table_unions_keys() -> None:
    table = records_to_table([{"id": "a", "x": 1}, {"id": "b", "y": 2}])
    assert set(table.column_names) == {"id", "x", "y"}
    assert table.num_rows == 2
    assert table.column("y").to_pylist() == [None, 2]


def test_records_to_table_widens_mixed_types_to_string() -> None:
    table = records_to_table([{"id": "a", "v": 1}, {"id": "b", "v": "two"}])
    assert table.column("v").type == pa.string()
    assert table.column("v").to_pylist() == ["1", "two"]


def test_records_to_table_empty_uses_spine() -> None:
    table = records_to_table([], spine={"id": pa.string(), "created": pa.string()})
    assert table.num_rows == 0
    assert table.column_names == ["id", "created"]


def test_spine_columns_always_present_and_typed() -> None:
    """Downstream SQL references spine columns (e.g. transactions.charges), so
    they must exist — typed, not Arrow null — even when no record carries them."""
    table = records_to_table(
        [{"id": "a"}, {"id": "b"}],
        spine={"id": pa.string(), "charges": pa.string()},
    )
    assert table.column_names[:2] == ["id", "charges"]
    assert table.column("charges").type == pa.string()
    assert table.column("charges").to_pylist() == [None, None]


def test_event_key_is_stable_and_kind_scoped() -> None:
    event = {"id": "T1", "updateDate": "2026-07-13T10:00:00Z", "status": "Pending"}
    assert event_key("transaction", event) == event_key("transaction", dict(event))
    assert event_key("transaction", event) != event_key("position", event)
    # a status-only change with the same updateDate dedupes; a new updateDate does not
    assert event_key("transaction", event) != event_key(
        "transaction", {**event, "updateDate": "2026-07-13T11:00:00Z"}
    )
