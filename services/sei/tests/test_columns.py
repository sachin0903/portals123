"""The raw layer's text -> declared type conversions.

These run the generated expressions through a real DuckDB rather than asserting on
SQL strings, because the bug they exist to prevent was a cast that *silently*
produced NULL instead of failing — a string assertion would have passed for the
broken version too.
"""

from __future__ import annotations

from decimal import Decimal

import duckdb
import pytest

from sei_sync import schema
from sei_sync.columns import (
    DECLARED_TYPES,
    cast_expression,
    loss_audit,
    replace_clauses,
    typed_columns,
)


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


def evaluate(conn: duckdb.DuckDBPyConnection, declared: str, value: str | None) -> object:
    """Apply one conversion to one literal value, as VARCHAR to keep it comparable.

    A TIMESTAMPTZ crossing into Python needs `pytz` installed; rendering to text
    inside DuckDB avoids depending on that just to assert a parse worked.
    """
    expression = cast_expression("C", declared)
    literal = "NULL" if value is None else "'" + value.replace("'", "''") + "'"
    sql = f'SELECT ({expression})::VARCHAR FROM (SELECT {literal} AS "C") t'  # noqa: S608
    return conn.execute(sql).fetchone()[0]


# --- dates: the bug this layer exists for -------------------------------------


def test_sei_date_format_cannot_be_cast(conn: duckdb.DuckDBPyConnection) -> None:
    """The regression guard. SEI writes 20260327, and CAST returns NULL for it —
    which is how nine consumption-layer date columns came to be 100% NULL with
    nothing failing."""
    assert conn.execute("SELECT try_cast('20260327' AS DATE)").fetchone()[0] is None


def test_date_is_parsed_with_an_explicit_format(conn: duckdb.DuckDBPyConnection) -> None:
    assert evaluate(conn, "DATE", "20260327") == "2026-03-27"


@pytest.mark.parametrize("value", ["", "garbage", "20261332", None])
def test_unparseable_date_becomes_null(conn: duckdb.DuckDBPyConnection, value: str | None) -> None:
    assert evaluate(conn, "DATE", value) is None


# --- the other declared types -------------------------------------------------


def test_decimal_keeps_scale(conn: duckdb.DuckDBPyConnection) -> None:
    expression = cast_expression("C", "DECIMAL")
    sql = f"SELECT {expression} FROM (SELECT '123.4500' AS \"C\") t"  # noqa: S608
    assert conn.execute(sql).fetchone()[0] == Decimal("123.4500000000")


def as_utc(conn: duckdb.DuckDBPyConnection, declared: str, value: str) -> str | None:
    """One timestamp conversion, rendered as UTC text.

    Pinned to UTC rather than compared as text: a TIMESTAMPTZ renders in the session
    timezone, so an unpinned assertion passes or fails depending on the machine.
    """
    expression = cast_expression("C", declared)
    literal = "'" + value.replace("'", "''") + "'"
    sql = (
        f"SELECT strftime(({expression}) AT TIME ZONE 'UTC', '%Y-%m-%d %H:%M:%S') "  # noqa: S608
        f'FROM (SELECT {literal} AS "C") t'
    )
    return conn.execute(sql).fetchone()[0]


def test_timestamptz_24_hour(conn: duckdb.DuckDBPyConnection) -> None:
    """user_detail.LAST_LOGIN_TIME — 24-hour clock with an offset."""
    assert as_utc(conn, "TIMESTAMPTZ", "2025-07-30 19:11:34 +00:00") == "2025-07-30 19:11:34"


def test_timestamptz_12_hour_keeps_pm_and_the_offset(conn: duckdb.DuckDBPyConnection) -> None:
    """clients.PREF_TAX_FORM_DEL_UPD_DT — 12-hour clock, AM/PM, and an offset. The
    conversion audit caught this one declared as a plain TIMESTAMP, which made 8,303
    of 8,321 values NULL. 02:00:07 PM at +01:00 is 13:00:07 UTC — so this asserts both
    that PM was honoured and that the offset was applied."""
    assert as_utc(conn, "TIMESTAMPTZ_12H", "2024-10-16 02:00:07 PM +01:00") == "2024-10-16 13:00:07"


def test_the_two_timestamp_formats_do_not_parse_each_other(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    assert evaluate(conn, "TIMESTAMPTZ", "2024-10-16 02:00:07 PM +01:00") is None
    assert evaluate(conn, "TIMESTAMPTZ_12H", "2025-07-30 19:11:34 +00:00") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Y", "true"), ("N", "false"), ("", None), (None, None), ("X", None)],
)
def test_boolean_distinguishes_no_from_unanswered(
    conn: duckdb.DuckDBPyConnection, value: str | None, expected: str | None
) -> None:
    """`= 'Y'` would map both 'N' and an absent value to false, claiming SEI answered
    a flag it never sent."""
    assert evaluate(conn, "BOOLEAN", value) == expected


def test_unknown_declared_type_is_refused() -> None:
    """A hand-edit or profiler change introducing a type nothing can build must fail
    loudly rather than silently falling through to a pass-through."""
    with pytest.raises(ValueError, match="no conversion"):
        cast_expression("C", "GEOGRAPHY")


# --- REPLACE clauses ----------------------------------------------------------


def test_replace_clauses_cover_exactly_the_declared_columns() -> None:
    clauses = replace_clauses("accounts")
    assert len(clauses) == len(typed_columns("accounts"))
    for column in typed_columns("accounts"):
        assert any(clause.endswith(f'AS "{column}"') for clause in clauses)


def test_replace_clauses_are_empty_for_an_untyped_report() -> None:
    """`* REPLACE ()` is a syntax error, so the generator needs this to be empty
    rather than a clause list with nothing in it."""
    assert replace_clauses("no_such_report") == []


def test_identifiers_are_quoted() -> None:
    """SEI's headers are SHOUTY_SNAKE and would fold without quoting."""
    assert all(clause.endswith('"') for clause in replace_clauses("accounts"))


def test_declared_columns_are_ordered_stably() -> None:
    """The clauses go into a generated file; an unstable order would churn the diff."""
    assert list(typed_columns("accounts")) == sorted(typed_columns("accounts"))


# --- the declared schema itself -----------------------------------------------


def test_every_declared_type_can_be_built() -> None:
    seen = {t for columns in schema.COLUMN_TYPES.values() for t in columns.values()}
    assert seen <= DECLARED_TYPES, f"unbuildable type(s): {seen - DECLARED_TYPES}"
    for declared in seen:
        assert cast_expression("C", declared)


def test_the_reports_that_matter_have_their_dates_declared() -> None:
    """These feed the consumption layer's date columns; if a profile run stops
    declaring them, the all-NULL bug comes straight back."""
    for table, column in [
        ("transaction_header", "TRANSACTION_DATE"),
        ("transaction_header", "TRADE_DATE"),
        ("transaction_header", "SETTLEMENT_DATE"),
        ("accounts", "ACCOUNT_OPEN_DATE"),
        ("accounts", "DATE_LAST_UPDATED"),
        ("eod_positions", "AS_OF_DATE"),
        ("clients", "DATE_OF_BIRTH"),
    ]:
        assert schema.COLUMN_TYPES.get(table, {}).get(column) == "DATE", f"{table}.{column}"


def test_identifiers_are_not_typed() -> None:
    """Leading zeros are real in this feed and these are join keys, never arithmetic —
    typing them numeric would silently destroy data."""
    for table, column in [
        ("accounts", "ACCOUNT_NUMBER"),
        ("clients", "CLIENT_ID"),
        ("eod_positions", "SEDOL"),
        ("transaction_header", "RELATED_ACTIVITY_ID"),
    ]:
        assert column not in schema.COLUMN_TYPES.get(table, {}), f"{table}.{column}"


# --- conversion-loss audit ----------------------------------------------------


def test_loss_audit_is_none_without_typed_columns() -> None:
    assert loss_audit("no_such_report") is None


def stub_source(table: str, column: str, values: list[str | None]) -> str:
    """A relation with every typed column of ``table``, only ``column`` populated.

    The audit names all of a report's typed columns, so a stub missing any of them
    would fail to bind rather than test anything.
    """
    typed = list(typed_columns(table))
    rows = []
    for value in values:
        literal = "NULL" if value is None else "'" + value.replace("'", "''") + "'"
        rows.append(
            "SELECT "
            + ", ".join(f'{literal if name == column else "NULL"} AS "{name}"' for name in typed)
        )
    return "(" + " UNION ALL ".join(rows) + ")"


def test_loss_audit_counts_only_genuine_failures(conn: duckdb.DuckDBPyConnection) -> None:
    audit = loss_audit("accounts")
    assert audit is not None
    # one good, one absent, one malformed -> exactly one loss. An absent value is not
    # a loss: SEI leaves fields empty constantly and DuckDB's CSV reader has already
    # turned those into NULL by the time the conversion sees them.
    source = stub_source("accounts", "ACCOUNT_OPEN_DATE", ["20260327", None, "nonsense"])
    row = conn.execute(audit.format(source=source)).fetchone()
    assert row is not None
    losses = dict(zip(typed_columns("accounts"), row, strict=True))
    assert losses["ACCOUNT_OPEN_DATE"] == 1
    assert sum(losses.values()) == 1  # nothing else reported a loss


def test_loss_audit_is_silent_when_everything_converts(conn: duckdb.DuckDBPyConnection) -> None:
    audit = loss_audit("accounts")
    assert audit is not None
    source = stub_source("accounts", "ACCOUNT_OPEN_DATE", ["20260327", "20240101"])
    assert sum(conn.execute(audit.format(source=source)).fetchone()) == 0
