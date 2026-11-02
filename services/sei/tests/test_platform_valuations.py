"""`platform.valuations` executed over rows in DuckDB.

It had no test at all, which is how the org half of it came to be wrong: the model recomputed
`key`/`lineage` from `platform.tenancy` instead of reading the account's, and spelt "this row's
owning node is unknown" as `[]` — which the fan-out reads as GLOBAL (`book_sink.predicate`), the
opposite of what every other relation on this axis says. That is invisible to a parse and to an
empty fixture: binding is not evaluating, and a list function over zero rows is never asked to
produce one.

The arithmetic is worth pinning for its own sake — the model derives the account's value, its cash
and its opening value from taxlot rows, and none of that is checked anywhere else.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

MODELS = Path(__file__).resolve().parent.parent / "models" / "platform"

#: Only the columns the model reads. `raw.eod_positions` has 83; the rest stay in the raw layer.
EOD_POSITIONS = """
    dt date,
    "ACCOUNT_NUMBER" varchar, "TAXLOT_PORTFOLIO_ID" varchar, "INSTRUMENT_ID" varchar,
    "TRD_INSTRUMENT_ID" varchar,
    "AS_OF_DATE" date, "VALUATION_CURRENCY" varchar,
    "VALUATION_CLEAN_MARKET_VALUE" decimal(38,10), "DEBIT_CREDIT_FLAG_7" varchar,
    "VALUATION_COST" decimal(38,10), "DEBIT_CREDIT_FLAG_10" varchar,
    "VALUATION_ACCRUED_INTEREST" decimal(38,10), "DEBIT_CREDIT_FLAG_6" varchar,
    "LOCAL_TO_BASE_EXCH_RATE" decimal(38,10), "LOCAL_TO_FIRM_EXCH_RATE" decimal(38,10)
"""

EOD_SUPPLEMENTAL = """
    dt date,
    "ACCOUNT_NUMBER" varchar, "PORTFOLIO_ID" varchar, "INSTRUMENT_ID" varchar,
    "TRD_INSTRUMENT_ID" varchar,
    "UNREALIZED_GAIN_LOSS_IN_FBC" decimal(38,10)
"""

INSTRUMENTS = """
    external_id varchar, name varchar, isin varchar, asset_type varchar
"""

TENANCY = """
    account_number varchar, firm_id varchar, subfirm_id varchar,
    dsr_team_ids varchar[], adviser_firm_dsr_ids varchar[]
"""

#: `platform.accounts` is the ORG OF RECORD for every account-grained relation — key/lineage are
#: resolved once there and selected here (see models/platform/accounts.sql). Only the spine
#: columns are needed.
ACCOUNTS_SPINE = """
    external_id varchar, key varchar, lineage varchar[]
"""

#: The Accounts report's SIPP split. Empty unless a test states one: a missing table is a
#: Binder Error, and a blank column must stay NULL rather than become zero.
ACCOUNTS_REPORT = """
    dt date,
    "ACCOUNT_NUMBER" varchar,
    "DATE_LAST_UPDATED" date,
    "PORTFOLIO_ID" varchar,
    "SIPP_CRYSTALLISED_VALUE" decimal(38, 10),
    "SIPP_UNCRYSTALLISED_VALUE" decimal(38, 10),
    _source_file varchar,
    _row bigint
"""

#: SEI's asset type for a currency holding — the one thing that distinguishes cash from a security.
CASH_TYPE = "MRCXXXXXXX"
EQUITY_TYPE = "ESXXXXXXXX"


def _model_sql(name: str) -> str:
    sql = (MODELS / f"{name}.sql").read_text()
    return re.sub(r"/\*.*?\*/", "", sql, count=1, flags=re.S).strip()


@pytest.fixture
def warehouse() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    for schema in ("raw", "platform"):
        connection.execute(f"CREATE SCHEMA {schema}")
    connection.execute(f"CREATE TABLE raw.eod_positions ({EOD_POSITIONS})")
    connection.execute(f"CREATE TABLE raw.eod_positions_supplemental ({EOD_SUPPLEMENTAL})")
    connection.execute(f"CREATE TABLE platform.instruments ({INSTRUMENTS})")
    connection.execute(f"CREATE TABLE platform.tenancy ({TENANCY})")
    connection.execute(f"CREATE TABLE platform.accounts ({ACCOUNTS_SPINE})")
    connection.execute(f"CREATE TABLE raw.accounts ({ACCOUNTS_REPORT})")
    connection.executemany(
        "INSERT INTO platform.instruments VALUES (?, ?, ?, ?)",
        [
            ("I-EQ", "A Global Equity Fund", "GB00B0001", EQUITY_TYPE),
            ("I-GBP", "Pound Sterling", None, CASH_TYPE),
        ],
    )
    connection.execute("INSERT INTO platform.tenancy VALUES ('A1', '3106', NULL, [], [])")
    connection.execute("INSERT INTO platform.accounts VALUES ('A1', '3106', ['3106'])")
    return connection


def _position(
    connection: duckdb.DuckDBPyConnection,
    *,
    account: str = "A1",
    instrument: str = "I-EQ",
    portfolio: str = "P1",
    day: str = "2026-03-27",
    market_value: str | None = "1000.00",
    cost: str = "900.00",
    accrued: str = "0.00",
    market_flag: str = "C",
    currency: str | None = "GBP",
) -> None:
    connection.execute(
        """
        INSERT INTO raw.eod_positions VALUES (
            ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, 'C', ?, 'C', 1.0, 1.0
        )
        """,
        [
            day,
            account,
            portfolio,
            instrument,
            day,
            currency,
            market_value,
            market_flag,
            cost,
            accrued,
        ],
    )


def _build(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(f"CREATE OR REPLACE TABLE platform.valuations AS {_model_sql('valuations')}")


def test_the_model_binds_against_the_typed_raw_layer(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """A Binder Error here aborts the whole provider's promote, which reads as missing data."""
    _build(warehouse)


class TestTheOrgComesFromTheAccount:
    def test_it_publishes_the_accounts_own_key_and_lineage(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _position(warehouse)
        _build(warehouse)
        key, lineage = warehouse.execute("SELECT key, lineage FROM platform.valuations").fetchone()
        assert key == "3106"
        assert lineage == ["3106"]

    def test_an_account_this_provider_does_not_model_publishes_no_lineage_and_not_an_empty_one(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        # THE POINT OF THIS FILE. An EMPTY lineage is the fan-out's GLOBAL sentinel
        # (`book_sink.predicate.conformed`: "an empty lineage means global reference data …
        # which projects whole"), so a valuation whose account is unknown must publish NULL —
        # `[]` would hand one firm's account value to every tenant on the platform. The model
        # computed it from `platform.tenancy` and produced exactly that `[]`.
        _position(warehouse, account="A-UNKNOWN")
        _build(warehouse)
        key, lineage = warehouse.execute(
            "SELECT key, lineage FROM platform.valuations WHERE account_external_id = 'A-UNKNOWN'"
        ).fetchone()
        assert key is None
        assert lineage is None, "an empty lineage is GLOBAL, not unknown"

    def test_the_tenancy_triple_is_still_published_beside_it(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        # The live fan-out narrows SEI on the triple, not on containment (§6 step 5 is the switch),
        # so the spine moving to the account must not have taken the triple with it.
        _position(warehouse)
        _build(warehouse)
        firm, subfirm, teams = warehouse.execute(
            "SELECT firm_id, subfirm_id, dsr_team_ids FROM platform.valuations"
        ).fetchone()
        assert firm == "3106"
        assert subfirm is None
        assert teams == []


class TestTheValuationItself:
    def test_one_row_per_account_and_day_summing_every_taxlot(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _position(warehouse, portfolio="P1", market_value="1000.00")
        _position(warehouse, portfolio="P2", market_value="250.50")
        _build(warehouse)
        rows = warehouse.execute(
            "SELECT account_external_id, as_of_date, market_value, position_count"
            " FROM platform.valuations"
        ).fetchall()
        assert rows == [("A1", date(2026, 3, 27), Decimal("1250.5000000000"), 2)]

    def test_a_debit_flag_signs_the_market_value_negative(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _position(warehouse, market_value="1000.00")
        _position(warehouse, portfolio="P2", market_value="100.00", market_flag="D")
        _build(warehouse)
        (value,) = warehouse.execute("SELECT market_value FROM platform.valuations").fetchone()
        assert value == Decimal("900.0000000000")

    def test_cash_is_the_currency_holdings_value_and_nothing_else(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        # `market_value` is the WHOLE account including the cash; `cash_value` is a breakdown of
        # it, not an addition to it.
        _position(warehouse, instrument="I-EQ", market_value="1000.00")
        _position(warehouse, instrument="I-GBP", portfolio="P2", market_value="40.00")
        _build(warehouse)
        value, cash = warehouse.execute(
            "SELECT market_value, cash_value FROM platform.valuations"
        ).fetchone()
        assert value == Decimal("1040.0000000000")
        assert cash == Decimal("40.0000000000")

    def test_an_account_holding_no_currency_publishes_null_cash_rather_than_zero(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        # "SEI states no cash here" is a different fact from "the balance is nil", and the account
        # detail's provider block adds a negative balance rather than flooring it.
        _position(warehouse, instrument="I-EQ")
        _build(warehouse)
        (cash,) = warehouse.execute("SELECT cash_value FROM platform.valuations").fetchone()
        assert cash is None

    def test_opening_value_is_the_previous_drop_and_null_on_the_first(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _position(warehouse, day="2026-03-26", market_value="1000.00")
        _position(warehouse, day="2026-03-27", market_value="1100.00")
        _build(warehouse)
        rows = warehouse.execute(
            "SELECT as_of_date, market_value, opening_value FROM platform.valuations"
            " ORDER BY as_of_date"
        ).fetchall()
        assert [(r[1], r[2]) for r in rows] == [
            (Decimal("1000.0000000000"), None),
            (Decimal("1100.0000000000"), Decimal("1000.0000000000")),
        ]

    def test_an_unvalued_account_day_is_omitted_not_coalesced_to_zero(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """SEI published quantity and cost with no VALUATION_CLEAN_MARKET_VALUE (G086002,
        15-16 Sep 2026). `sum` of those NULLs is NULL, which failed `not_null: market_value`
        and blocked promotion of every other account. A day SEI did not value is not a £0
        close — the account still holds the units."""
        _position(warehouse, day="2026-09-15", market_value=None)
        _position(warehouse, day="2026-09-16", market_value="1000.00")
        _build(warehouse)
        rows = warehouse.execute(
            "SELECT as_of_date, market_value FROM platform.valuations ORDER BY as_of_date"
        ).fetchall()
        assert rows == [(date(2026, 9, 16), Decimal("1000.0000000000"))]

    def test_an_unvalued_lot_does_not_drop_a_day_that_has_a_valued_one(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """22 of 125,392 lots in the corpus have a blank clean market value; 20 of those sit
        on a day that also has a valued lot. `sum` already ignores NULLs, so the day still
        publishes."""
        _position(warehouse, portfolio="P1", market_value="1000.00")
        _position(warehouse, portfolio="P2", market_value=None)
        _build(warehouse)
        value, n = warehouse.execute(
            "SELECT market_value, position_count FROM platform.valuations"
        ).fetchone()
        assert value == Decimal("1000.0000000000")
        assert n == 2

    def test_the_crystallisation_split_is_a_typed_null_not_an_absent_column(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        # The sink's projection reads both for EVERY provider (0117). A bare NULL types as INTEGER
        # in DuckDB and an absent column is a Binder Error that lands as "unavailable".
        _position(warehouse)
        _build(warehouse)
        types = dict(
            warehouse.execute(
                "SELECT column_name, data_type FROM information_schema.columns"
                " WHERE table_schema = 'platform' AND table_name = 'valuations'"
                " AND column_name IN ('crystallised_value', 'uncrystallised_value')"
            ).fetchall()
        )
        assert types == {
            "crystallised_value": "DECIMAL(38,10)",
            "uncrystallised_value": "DECIMAL(38,10)",
        }
        crystallised, uncrystallised = warehouse.execute(
            "SELECT crystallised_value, uncrystallised_value FROM platform.valuations"
        ).fetchone()
        assert crystallised is None
        assert uncrystallised is None

    def test_a_stated_split_lands_on_the_latest_day_only(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """The Accounts report is today's split. An earlier valuation day must not wear it."""
        _position(warehouse, day="2026-03-26", market_value="900.00")
        _position(warehouse, day="2026-03-27", market_value="1000.00")
        warehouse.execute(
            """
            INSERT INTO raw.accounts (
                dt, "ACCOUNT_NUMBER", "DATE_LAST_UPDATED", "PORTFOLIO_ID",
                "SIPP_CRYSTALLISED_VALUE", "SIPP_UNCRYSTALLISED_VALUE"
            ) VALUES
                (DATE '2026-03-20', 'A1', DATE '2026-03-01', 'P-OLD', 1, 2),
                (DATE '2026-03-27', 'A1', DATE '2026-03-02', 'P-NEW', 40000, 60000),
                (DATE '2026-03-27', 'A1', DATE '2026-03-27', 'P-LATEST', 11111, 22222)
            """
        )
        _build(warehouse)
        rows = warehouse.execute(
            "SELECT as_of_date, crystallised_value, uncrystallised_value "
            "FROM platform.valuations ORDER BY 1"
        ).fetchall()
        assert rows[0][0] == date(2026, 3, 26)
        assert rows[0][1] is None and rows[0][2] is None
        assert rows[1] == (
            date(2026, 3, 27),
            Decimal("11111.0000000000"),
            Decimal("22222.0000000000"),
        )

    def test_a_tied_sipp_row_and_a_blank_currency_do_not_move_between_reads(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """`full_merge` reads the model twice. A tie, or `any_value` over a blank currency,
        lets the two reads differ and the second row lands beside the first."""
        _position(warehouse, portfolio="P1", currency="GBP", market_value="1000.00")
        _position(warehouse, portfolio="P2", currency=None, market_value="10.00")
        warehouse.execute(
            """
            INSERT INTO raw.accounts (
                dt, "ACCOUNT_NUMBER", "DATE_LAST_UPDATED", "PORTFOLIO_ID",
                "SIPP_CRYSTALLISED_VALUE", "SIPP_UNCRYSTALLISED_VALUE",
                _source_file, _row
            ) VALUES
                (DATE '2026-03-27', 'A1', DATE '2026-03-27', 'P1', 1, 9, 'b.out', 2),
                (DATE '2026-03-27', 'A1', DATE '2026-03-27', 'P1', 5, 7, 'a.out', 1)
            """
        )
        _build(warehouse)
        first = warehouse.execute(
            "SELECT account_external_id, as_of_date, currency, crystallised_value, "
            "uncrystallised_value FROM platform.valuations"
        ).fetchall()
        _build(warehouse)
        second = warehouse.execute(
            "SELECT account_external_id, as_of_date, currency, crystallised_value, "
            "uncrystallised_value FROM platform.valuations"
        ).fetchall()
        assert first == second
        assert first == [
            ("A1", date(2026, 3, 27), "GBP", Decimal("5.0000000000"), Decimal("7.0000000000"))
        ]

    def test_the_gain_is_summed_as_decimal_so_both_reads_agree(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """DuckDB's `/` on decimals is a double. Summing those doubles is what wrote the
        second copy of 158 account-days: same account, same day, gain off by one ulp."""
        _position(warehouse, portfolio="P1", market_value="1000.00")
        _position(warehouse, portfolio="P2", market_value="500.00")
        warehouse.execute(
            """
            UPDATE raw.eod_positions
            SET "LOCAL_TO_BASE_EXCH_RATE" = CAST('1.25' AS decimal(38, 10)),
                "LOCAL_TO_FIRM_EXCH_RATE" = CAST('1' AS decimal(38, 10))
            WHERE "TAXLOT_PORTFOLIO_ID" = 'P1'
            """
        )
        warehouse.execute(
            """
            UPDATE raw.eod_positions
            SET "LOCAL_TO_BASE_EXCH_RATE" = CAST('2' AS decimal(38, 10)),
                "LOCAL_TO_FIRM_EXCH_RATE" = CAST('4' AS decimal(38, 10))
            WHERE "TAXLOT_PORTFOLIO_ID" = 'P2'
            """
        )
        warehouse.execute(
            """
            INSERT INTO raw.eod_positions_supplemental VALUES
                (DATE '2026-03-27', 'A1', 'P1', 'I-EQ', NULL, CAST('100.50' AS decimal(38, 10))),
                (DATE '2026-03-27', 'A1', 'P2', 'I-EQ', NULL, CAST('80' AS decimal(38, 10)))
            """
        )
        _build(warehouse)
        kind, gain = warehouse.execute(
            "SELECT typeof(unrealised_gain), unrealised_gain FROM platform.valuations"
        ).fetchone()
        # 100.50 * 1.25 / 1 = 125.625; 80 * 2 / 4 = 40.
        assert kind.startswith("DECIMAL")
        assert gain == Decimal("165.6250000000")
        _build(warehouse)
        again = warehouse.execute("SELECT unrealised_gain FROM platform.valuations").fetchone()[0]
        assert again == gain


def test_it_publishes_every_column_the_sink_reads(warehouse: duckdb.DuckDBPyConnection) -> None:
    """`book_sink.entities._VALUATIONS`. A column the sink reads and the model does not publish is
    a Binder Error that aborts the whole tenant and files as "unavailable"."""
    _position(warehouse)
    _build(warehouse)
    published = {
        row[0]
        for row in warehouse.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'platform' AND table_name = 'valuations'"
        ).fetchall()
    }
    assert {
        "account_external_id",
        "as_of_date",
        "market_value",
        "cash_value",
        "book_cost",
        "unrealised_gain",
        "opening_value",
        "crystallised_value",
        "uncrystallised_value",
        "currency",
        "data",
    } <= published
