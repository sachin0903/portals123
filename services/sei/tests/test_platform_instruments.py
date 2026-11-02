"""`platform.instruments` — one row per SEI instrument, even when the report repeats it.

The Assets report repeats the instrument block once per listing. `full_merge` then reads the
model twice (the changed-key diff, then the insert). A window that ties on those two reads
keeps a different listing each time, both rows land, and `unique: external_id` blocks
promotion of the whole run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

MODELS = Path(__file__).resolve().parent.parent / "models" / "platform"

ASSETS = """
    dt date,
    "INSTRUMENT_ID" varchar, "ASSET_NAME" varchar, "ASSET_TYPE" varchar,
    "ISIN" varchar, "SEDOL_NUMBER" varchar, "CUSIP" varchar,
    "ISO_CURRENCY" varchar, "STOCK_EXCHANGE_CODE" varchar,
    "MARKET_COUNTRY" varchar, "ISSUER_COUNTRY" varchar, "CREDIT_RATING" varchar,
    "INTEREST_RATE" decimal(38, 10), "ISSUE_DATE" date, "MATURITY_DATE" date,
    _source_file varchar, _row bigint
"""


def _rewrite_line_comments_as_blocks(sql: str) -> str:
    """interlace rewrites `--` comments as one `/* … */` per line. DuckDB nests block
    comments, so a `--` line that contains `/*` is `unterminated /* comment` at apply
    time. Rewrite the same way the warehouse does."""
    rewritten: list[str] = []
    for line in sql.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("--"):
            indent = line[: len(line) - len(stripped)]
            rewritten.append(f"{indent}/* {stripped[2:].strip()} */")
        else:
            rewritten.append(line)
    return "\n".join(rewritten)


def _model_sql() -> str:
    sql = (MODELS / "instruments.sql").read_text()
    sql = re.sub(r"/\*.*?\*/", "", sql, count=1, flags=re.S).strip()
    return _rewrite_line_comments_as_blocks(sql)


@pytest.fixture
def warehouse() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA raw")
    connection.execute("CREATE SCHEMA platform")
    connection.execute(f"CREATE TABLE raw.assets ({ASSETS})")
    connection.execute(f"CREATE TABLE raw.costs_charges ({CHARGES})")
    connection.execute("CREATE TABLE platform.emt_charges (isin varchar, ocf decimal(18, 10))")
    return connection


def _asset(
    connection: duckdb.DuckDBPyConnection,
    *,
    instrument: str,
    name: str = "Fund",
    isin: str = "GB00TEST0001",
    sedol: str = "SED001",
    exchange: str = "LSE",
    source: str = "f1",
    row: int = 1,
    day: str = "2026-09-28",
) -> None:
    connection.execute(
        """
        INSERT INTO raw.assets VALUES (
            ?, ?, ?, 'EQ', ?, ?, 'CUSIP', 'GBP', ?, 'GB', 'GB', 'AA',
            1.0, NULL, NULL, ?, ?
        )
        """,
        [day, instrument, name, isin, sedol, exchange, source, row],
    )


CHARGES = """
    dt date,
    "INSTRUMENT_ID" varchar,
    "ONGOING_CHARGE_ACTUAL" decimal(38, 10),
    "ONGOING_CHARGE_ESTIMATE" decimal(38, 10)
"""


def _charge(
    connection: duckdb.DuckDBPyConnection,
    *,
    instrument: str,
    actual: str | None = None,
    estimate: str | None = None,
    day: str = "2026-09-28",
) -> None:
    connection.execute(
        """
        INSERT INTO raw.costs_charges VALUES (?, ?, ?, ?)
        """,
        [day, instrument, actual, estimate],
    )


def _build(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(f"CREATE OR REPLACE TABLE platform.instruments AS {_model_sql()}")


def _duplicate_groups(connection: duckdb.DuckDBPyConnection) -> list[tuple[object, ...]]:
    """The check interlace names `unique_external_id`: groups with more than one row."""
    return connection.execute(
        """
        SELECT count(*) FROM (
            SELECT external_id FROM platform.instruments
            GROUP BY external_id
            HAVING count(*) > 1
        )
        """
    ).fetchall()


def test_the_model_binds(warehouse: duckdb.DuckDBPyConnection) -> None:
    _asset(warehouse, instrument="A")
    _build(warehouse)


def test_a_repeated_instrument_is_one_row(warehouse: duckdb.DuckDBPyConnection) -> None:
    """The p04 drop has 20,521 asset rows and 6,384 instruments. The extras are the same
    instrument repeated per listing, and `unique: external_id` is what makes a second
    row a failed build."""
    _asset(warehouse, instrument="A", exchange="NYSE", source="f2", row=1)
    _asset(warehouse, instrument="A", exchange="LSE", source="f1", row=9)
    _asset(warehouse, instrument="B", exchange="XETRA", source="f1", row=2)
    _asset(warehouse, instrument="B", exchange="LSE", source="f1", row=1)
    _build(warehouse)
    assert _duplicate_groups(warehouse) == [(0,)]
    kept = dict(
        warehouse.execute(
            "SELECT external_id, exchange_code FROM platform.instruments ORDER BY 1"
        ).fetchall()
    )
    # Lower `_source_file`, then `_row` — not whichever listing happened to be inserted
    # first. NYSE was inserted first and would win a tied, insertion-ordered window.
    assert kept == {"A": "LSE", "B": "LSE"}


def test_a_blank_listing_loses_to_a_named_one(warehouse: duckdb.DuckDBPyConnection) -> None:
    """A populated name beats a blank one even when the blank row has the lower file key.
    Written as `IS NULL` because `NULLS LAST` does not survive the warehouse's SQL parse."""
    _asset(warehouse, instrument="A", name=None, exchange="LSE", source="f1", row=1)
    _asset(warehouse, instrument="A", name="Named Fund", exchange="NYSE", source="f9", row=9)
    _build(warehouse)
    assert warehouse.execute(
        "SELECT name, exchange_code FROM platform.instruments WHERE external_id = 'A'"
    ).fetchone() == ("Named Fund", "NYSE")


def test_two_reads_keep_the_same_listing(warehouse: duckdb.DuckDBPyConnection) -> None:
    """`full_merge` evaluates the query twice. Both evaluations have to name the same row,
    or the second inserts a listing the first did not delete."""
    _asset(warehouse, instrument="A", exchange="NYSE", source="f2", row=1)
    _asset(warehouse, instrument="A", exchange="LSE", source="f1", row=9)
    _build(warehouse)
    first = warehouse.execute(
        "SELECT exchange_code FROM platform.instruments WHERE external_id = 'A'"
    ).fetchone()
    _build(warehouse)
    second = warehouse.execute(
        "SELECT exchange_code FROM platform.instruments WHERE external_id = 'A'"
    ).fetchone()
    assert first == second == ("LSE",)


def test_an_agreed_ongoing_charge_lands(warehouse: duckdb.DuckDBPyConnection) -> None:
    """Two firms publishing 0.77 for one instrument is one figure, stored as published."""
    _asset(warehouse, instrument="A")
    _charge(warehouse, instrument="A", actual="0.77")
    _charge(warehouse, instrument="A", actual="0.77")
    _build(warehouse)
    got = warehouse.execute(
        "SELECT ocf FROM platform.instruments WHERE external_id = 'A'"
    ).fetchone()[0]
    assert float(got) == 0.77


def test_a_disagreed_ongoing_charge_stays_blank(warehouse: duckdb.DuckDBPyConnection) -> None:
    _asset(warehouse, instrument="A")
    _charge(warehouse, instrument="A", actual="0.77")
    _charge(warehouse, instrument="A", actual="0.50")
    _build(warehouse)
    assert (
        warehouse.execute(
            "SELECT ocf FROM platform.instruments WHERE external_id = 'A'"
        ).fetchone()[0]
        is None
    )


def test_an_estimate_is_used_when_no_actual_was_published(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    _asset(warehouse, instrument="A")
    _charge(warehouse, instrument="A", estimate="0.40")
    _charge(warehouse, instrument="A", estimate="0.40")
    _build(warehouse)
    got = warehouse.execute(
        "SELECT ocf FROM platform.instruments WHERE external_id = 'A'"
    ).fetchone()[0]
    assert float(got) == 0.40


def _emt(connection: duckdb.DuckDBPyConnection, isin: str, ocf: str) -> None:
    connection.execute("INSERT INTO platform.emt_charges VALUES (?, ?)", [isin, ocf])


def test_a_blank_costs_charge_uses_the_emt_figure(warehouse: duckdb.DuckDBPyConnection) -> None:
    """The nightly file published nothing. The EMT extract is the charge."""
    _asset(warehouse, instrument="A", isin="GB00EMT00001")
    _emt(warehouse, "GB00EMT00001", "0.42")
    _build(warehouse)
    got = warehouse.execute(
        "SELECT ocf FROM platform.instruments WHERE external_id = 'A'"
    ).fetchone()[0]
    assert float(got) == 0.42


def test_an_agreed_costs_charge_wins_over_the_emt_figure(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    _asset(warehouse, instrument="A", isin="GB00EMT00001")
    _charge(warehouse, instrument="A", actual="0.77")
    _charge(warehouse, instrument="A", actual="0.77")
    _emt(warehouse, "GB00EMT00001", "0.10")
    _build(warehouse)
    got = warehouse.execute(
        "SELECT ocf FROM platform.instruments WHERE external_id = 'A'"
    ).fetchone()[0]
    assert float(got) == 0.77


def test_a_disagreed_costs_charge_uses_the_emt_figure(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """Two firms publishing different actuals is not a number. The extract is."""
    _asset(warehouse, instrument="A", isin="GB00EMT00001")
    _charge(warehouse, instrument="A", actual="0.77")
    _charge(warehouse, instrument="A", actual="0.50")
    _emt(warehouse, "GB00EMT00001", "0.42")
    _build(warehouse)
    got = warehouse.execute(
        "SELECT ocf FROM platform.instruments WHERE external_id = 'A'"
    ).fetchone()[0]
    assert float(got) == 0.42
