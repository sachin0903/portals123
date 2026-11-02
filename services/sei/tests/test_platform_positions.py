"""`platform.positions` and `platform.valuations` — executed in DuckDB, over rows.

**These two models published six of the fifteen columns their only reader needs, and the gap was
invisible because nothing read them.** `book_sink.LAKE_ONLY["sei"]` was empty, so SEI's positions
never reached a tenant's lake; `app/lake/holdings.py::_HOLDINGS_SQL` — the query behind every
holdings table, allocation donut, cash figure and withdrawal gate — would have been a Binder Error
the day it was pointed at them, which the merge ledger records as "unavailable" and a reader sees
as "this firm has no data".

Three things here can only be caught by EXECUTING, which is why this file exists:

* **the join grain.** EODPositions is per taxlot; the supplemental is per traded instrument and
  has no taxlot id. A second taxlot on one traded instrument fans the supplement's money onto
  both rows and doubles it. A blank taxlot is the other failure: `concat_ws` skips it, so two
  traded instruments of one instrument id collapse onto one key and the join pairs each with
  both gains. A schema check cannot see either; a row that appears twice can.
* **cash.** It is `ASSET_TYPE = 'MRCXXXXXXX'`, not a missing ISIN — 8,486 money-market fund rows in
  the real drop have no ISIN and are not cash.
* **the series.** `platform.valuations` is keyed `(account, as_of_date)` and was summing a model
  windowed to `max(dt)`, so it produced exactly one row per account. Only a fixture with two drops
  in it shows that.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

MODELS = Path(__file__).resolve().parent.parent / "models" / "platform"

#: Only the columns the models read, typed as `sei_sync.schema` materialises them.
EOD_POSITIONS = """
    dt date,
    "ACCOUNT_NUMBER" varchar, "TAXLOT_PORTFOLIO_ID" varchar, "INSTRUMENT_ID" varchar,
    "TRD_INSTRUMENT_ID" varchar,
    "TAXLOT_ID" varchar, "SEDOL" varchar, "ASSET_CURRENCY" varchar,
    "AS_OF_DATE" date, "QUANTITY_HELD" decimal(38,10), "ASSET_PRICE" decimal(38,10),
    "MARKET_VALUE_IN_ASSET_CURRENCY" decimal(38,10), "DEBIT_CREDIT_FLAG_1" varchar,
    "DEALING_COST" decimal(38,10), "DEBIT_CREDIT_FLAG_5" varchar,
    "VALUATION_CURRENCY" varchar,
    "VALUATION_ACCRUED_INTEREST" decimal(38,10), "DEBIT_CREDIT_FLAG_6" varchar,
    "VALUATION_CLEAN_MARKET_VALUE" decimal(38,10), "DEBIT_CREDIT_FLAG_7" varchar,
    "VALUATION_COST" decimal(38,10), "DEBIT_CREDIT_FLAG_10" varchar,
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
#: resolved once there and selected here, so a fact and the account it is about cannot disagree
#: about which node owns them (see models/platform/accounts.sql). Only the spine columns are
#: needed; an account absent from it publishes NULL, which is what "no org" spells.
ACCOUNTS_SPINE = """
    external_id varchar, key varchar, lineage varchar[]
"""

#: The Accounts report, which valuations reads for the SIPP split. Empty here: these tests are
#: about the position series, and a missing table is a Binder Error rather than "no split".
#: `_source_file` and `_row` are the report's own key — the SIPP pick orders by them.
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

#: SEI's asset type for a currency holding. Verified against the drop: 7 such instruments carry
#: £10.2m across 1,025 position rows, all with a blank ISIN and a currency for a name.
CASH_TYPE = "MRCXXXXXXX"
#: A money-market fund. Also has no ISIN, and is NOT cash.
MMF_TYPE = "MMFXXXXXXX"


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
    connection.execute(
        "INSERT INTO platform.tenancy VALUES ('A1', '3106', NULL, [], []),"
        " ('A2', '3106', NULL, [], [])"
    )
    connection.execute(
        "INSERT INTO platform.accounts VALUES ('A1', '3106', ['3106']), ('A2', '3106', ['3106'])"
    )
    connection.executemany(
        "INSERT INTO platform.instruments VALUES (?, ?, ?, ?)",
        [
            ("I-EQ", "A Global Equity Fund", "GB00B0001", "ESXXXXXXXX"),
            ("I-MMF", "A Money Market Fund", None, MMF_TYPE),
            ("I-GBP", "Pound Sterling", None, CASH_TYPE),
        ],
    )
    return connection


def _position(
    connection: duckdb.DuckDBPyConnection,
    *,
    day: str,
    account: str = "A1",
    instrument: str = "I-EQ",
    traded: str | None = None,
    taxlot: str | None = "T1",
    portfolio: str = "P1",
    quantity: float = 100.0,
    local_value: float = 1000.0,
    local_currency: str = "GBP",
    local_price: float = 10.0,
    base_currency: str = "GBP",
    base_value: float = 1000.0,
    base_cost: float | None = 800.0,
    base_accrued: float = 0.0,
    firm_gain: float | None = 200.0,
    base_rate: float = 1.0,
    firm_rate: float = 1.0,
) -> None:
    """One taxlot, plus its supplemental row.

    `base_*` is the ACCOUNT's base currency (SEI's `VALUATION_*` columns), `local_*` the
    instrument's own, and `firm_gain` the firm-currency gain SEI states — the two rates are what
    carry it into the account's currency.
    """
    connection.execute(
        "INSERT INTO raw.eod_positions VALUES"
        " (?, ?, ?, ?, ?, ?, 'SED1', ?, ?, ?, ?, ?, 'C', 800.0, 'C',"
        "  ?, ?, 'C', ?, 'C', ?, 'C', ?, ?)",
        [
            day,
            account,
            portfolio,
            instrument,
            traded,
            taxlot,
            local_currency,
            day,
            quantity,
            local_price,
            local_value,
            base_currency,
            base_accrued,
            base_value,
            base_cost,
            base_rate,
            firm_rate,
        ],
    )
    connection.execute(
        "INSERT INTO raw.eod_positions_supplemental VALUES (?, ?, ?, ?, ?, ?)",
        [day, account, portfolio, instrument, traded, firm_gain],
    )


def _build(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(f"CREATE OR REPLACE TABLE platform.positions AS {_model_sql('positions')}")
    connection.execute(f"CREATE OR REPLACE TABLE platform.valuations AS {_model_sql('valuations')}")


#: Every column `app/lake/holdings.py::_HOLDINGS_SQL` selects from the positions relation. The
#: contract, in the form the failure takes: a missing one is a Binder Error that aborts the tenant.
HOLDINGS_CONTRACT = frozenset(
    {
        "account_external_id",
        "instrument_key",
        "asset_name",
        "isin",
        "currency",
        "position_type",
        "market_value",
        "quantity",
        "price",
        "book_cost",
        "unrealised_gain",
        "unrealised_gain_percent",
        "allocation",
        "as_of_date",
    }
)


class TestTheHoldingsContract:
    def test_every_column_the_lake_reader_selects_is_published(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """SEI published six of these fourteen. The other eight were not "missing data" — they
        were a query that could not bind, and `LAKE_ONLY["sei"] = ("positions",)` on its own would
        have turned an empty portal into an unavailable one."""
        _position(warehouse, day="2026-03-27")
        _build(warehouse)
        columns = {row[0] for row in warehouse.execute("DESCRIBE platform.positions").fetchall()}
        assert HOLDINGS_CONTRACT <= columns, (
            f"positions does not publish {sorted(HOLDINGS_CONTRACT - columns)}"
        )

    def test_the_account_key_is_the_conformed_name(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """`account_external_id`. Publishing only `account_number` is what made every SEI
        transaction a reject before, in the sibling model."""
        _position(warehouse, day="2026-03-27", account="A1")
        _build(warehouse)
        assert warehouse.execute(
            "SELECT account_external_id FROM platform.positions"
        ).fetchone() == ("A1",)


class TestTheMoneyIsInTheAccountsBaseCurrency:
    """SEI publishes each figure at three currency levels; only one belongs on these columns.

    Every reader SUMS them, so they must be in one currency — and it has to be the ACCOUNT's,
    because `platform.accounts.base_currency` is what the account is denominated in and a total
    shown against an account must be in the account's own money. The model used to publish the
    INSTRUMENT's currency, so 703 of 25,932 positions in the real drop were being added to
    sterling as though dollars were pounds.
    """

    def test_a_foreign_holding_publishes_the_accounts_value(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _position(
            warehouse,
            day="2026-03-27",
            local_currency="USD",
            local_value=1300.0,
            base_currency="GBP",
            base_value=1000.0,
        )
        _build(warehouse)
        row = warehouse.execute(
            "SELECT currency, market_value, local_currency, local_market_value"
            "  FROM platform.positions"
        ).fetchone()
        assert row[0] == "GBP", "the account's currency, so a sum is coherent"
        assert float(row[1]) == 1000.0
        assert (row[2], float(row[3])) == ("USD", 1300.0), "SEI's own figure stays reachable"

    def test_a_usd_based_account_is_not_stated_in_sterling(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """The firm's base currency is NOT the account's, and SEI says so by publishing two rates.

        Two accounts in the real drop (MSP000073, MSP000074) are USD-based while the firm is GBP —
        26 position rows where `LOCAL_TO_BASE_EXCH_RATE` differs from `LOCAL_TO_FIRM_EXCH_RATE`.
        Publishing firm currency would state a USD account's holdings in sterling.
        """
        _position(
            warehouse,
            day="2026-03-27",
            local_currency="GBP",
            local_value=179_400.0,
            base_currency="USD",
            base_value=238_411.27,
            base_cost=200_000.0,
            firm_gain=13_500.0,  # SEI states the gain in GBP
            base_rate=1.32905,  # GBP -> USD, the account's base
            firm_rate=1.0,  # GBP -> GBP, the firm's base
        )
        _build(warehouse)
        row = warehouse.execute(
            "SELECT currency, market_value, unrealised_gain FROM platform.positions"
        ).fetchone()
        assert row[0] == "USD"
        assert float(row[1]) == 238_411.27, "the account's own money, not the firm's"
        # 13,500 GBP carried into USD by SEI's own two rates: * 1.32905 / 1.0
        assert float(row[2]) == 13_500.0 * 1.32905

    def test_the_rates_are_a_no_op_when_the_currencies_coincide(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """25,906 of 25,932 rows, so the conversion must not perturb them."""
        _position(warehouse, day="2026-03-27", firm_gain=200.0, base_rate=1.0, firm_rate=1.0)
        _build(warehouse)
        gain = warehouse.execute("SELECT unrealised_gain FROM platform.positions").fetchone()[0]
        assert float(gain) == 200.0

    def test_the_gain_is_seis_not_derived(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """`market_value - book_cost` was the obvious derivation and no cost column reproduces
        SEI's figure — the closest pairing holds on 24,260 of 24,884 rows, and one failure derives
        +2,770.40 where SEI says -13,767.56. Publish theirs."""
        _position(warehouse, day="2026-03-27", base_value=1000.0, base_cost=800.0, firm_gain=-50.0)
        _build(warehouse)
        gain = warehouse.execute("SELECT unrealised_gain FROM platform.positions").fetchone()[0]
        assert float(gain) == -50.0, "not 200.0, which is what deriving it would have said"

    def test_a_price_is_quoted_in_the_instruments_own_currency(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """Which is how every custodian statement reads — so the currency is published beside it
        rather than left to be assumed to be the account's."""
        _position(
            warehouse,
            day="2026-03-27",
            local_currency="USD",
            local_price=13.0,
            base_currency="GBP",
        )
        _build(warehouse)
        row = warehouse.execute(
            "SELECT price, price_currency, currency FROM platform.positions"
        ).fetchone()
        assert (float(row[0]), row[1]) == (13.0, "USD")
        assert row[2] == "GBP", "the value is the account's currency, the price the instrument's"


class TestCashIsAnAssetType:
    """Not a missing ISIN. Both cash and money-market funds lack one; only one is cash."""

    def test_a_currency_holding_is_typed_as_such(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _position(warehouse, day="2026-03-27", instrument="I-GBP", base_value=250.0)
        _build(warehouse)
        row = warehouse.execute(
            "SELECT position_type, asset_name, isin, cash_balance FROM platform.positions"
        ).fetchone()
        assert row[0] == CASH_TYPE
        assert row[1] == "Pound Sterling"
        assert row[2] is None
        assert float(row[3]) == 250.0, "the position's own value IS the cash"

    def test_a_money_market_fund_is_not_cash(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """8,486 of the drop's 8,755 ISIN-less asset rows are these. A "no ISIN means cash" test
        would count every one of them as cash."""
        _position(warehouse, day="2026-03-27", instrument="I-MMF")
        _build(warehouse)
        row = warehouse.execute(
            "SELECT position_type, isin, cash_balance FROM platform.positions"
        ).fetchone()
        assert row[0] == MMF_TYPE
        assert row[1] is None, "it has no ISIN either — which is why the ISIN test is wrong"
        assert row[2] is None, "and it is not cash"

    def test_the_valuation_splits_cash_out_of_the_total(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """`cash_value` was a typed NULL for every SEI account while the book column existed and
        every portal read it."""
        _position(warehouse, day="2026-03-27", instrument="I-EQ", base_value=1000.0)
        _position(
            warehouse,
            day="2026-03-27",
            instrument="I-GBP",
            taxlot="T2",
            base_value=250.0,
            base_cost=250.0,
            firm_gain=0.0,
        )
        _build(warehouse)
        row = warehouse.execute(
            "SELECT market_value, cash_value FROM platform.valuations"
        ).fetchone()
        assert float(row[0]) == 1250.0, "the whole account, cash included"
        assert float(row[1]) == 250.0, "of which this much is cash"

    def test_an_account_holding_no_currency_position_states_no_cash(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """NULL, not 0 — "SEI states no cash here" is not "this account has none"."""
        _position(warehouse, day="2026-03-27", instrument="I-EQ")
        _build(warehouse)
        assert warehouse.execute("SELECT cash_value FROM platform.valuations").fetchone()[0] is None


class TestTheValuationSeries:
    """`platform.valuations` is keyed (account, as_of_date) and produced ONE row per account.

    It summed `platform.positions`, which windows to `max(dt)` because the holdings reader has no
    date filter. So the table the performance chart, `opening_value` and every AUM tile need was
    structurally a single point. The warehouse held 74 drops the whole time.
    """

    def test_every_drop_becomes_a_point(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        for day, value in (("2026-03-25", 900.0), ("2026-03-26", 950.0), ("2026-03-27", 1000.0)):
            _position(warehouse, day=day, base_value=value)
        _build(warehouse)
        series = warehouse.execute(
            "SELECT as_of_date, market_value FROM platform.valuations"
            " WHERE account_external_id = 'A1' ORDER BY as_of_date"
        ).fetchall()
        assert [(d, float(v)) for d, v in series] == [
            (dt.date(2026, 3, 25), 900.0),
            (dt.date(2026, 3, 26), 950.0),
            (dt.date(2026, 3, 27), 1000.0),
        ]

    def test_positions_stays_current_only(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """The other half of the same decision. `_HOLDINGS_SQL` has no date filter, so a positions
        relation carrying history would render each holding once per drop — 74 copies of every
        line in the holdings table."""
        for day in ("2026-03-25", "2026-03-26", "2026-03-27"):
            _position(warehouse, day=day)
        _build(warehouse)
        rows = warehouse.execute(
            "SELECT as_of_date FROM platform.positions WHERE account_external_id = 'A1'"
        ).fetchall()
        assert rows == [(dt.date(2026, 3, 27),)], "the latest drop, once"

    def test_opening_value_is_the_previous_drop(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        for day, value in (("2026-03-26", 950.0), ("2026-03-27", 1000.0)):
            _position(warehouse, day=day, base_value=value)
        _build(warehouse)
        rows = warehouse.execute(
            "SELECT as_of_date, opening_value FROM platform.valuations"
            " WHERE account_external_id = 'A1' ORDER BY as_of_date"
        ).fetchall()
        assert rows[0][1] is None, "an account's first drop opens from nothing"
        assert float(rows[1][1]) == 950.0

    def test_one_accounts_series_does_not_borrow_anothers(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _position(warehouse, day="2026-03-26", account="A1", base_value=100.0)
        _position(warehouse, day="2026-03-27", account="A1", base_value=110.0)
        _position(warehouse, day="2026-03-27", account="A2", base_value=500.0)
        _build(warehouse)
        opening = dict(
            warehouse.execute(
                "SELECT account_external_id, opening_value FROM platform.valuations"
                " WHERE as_of_date = DATE '2026-03-27'"
            ).fetchall()
        )
        assert float(opening["A1"]) == 100.0
        assert opening["A2"] is None, "A2's first drop, not A1's opening value"


class TestTheSupplementalJoinDoesNotFanOut:
    """EODPositions is per TAXLOT and the supplemental is per traded instrument.

    Verified 1:1 on the 2026-03-27 drop — 25,932 rows and 25,932 distinct
    (account, portfolio, instrument) on both sides, and that day no instrument had two traded
    ids. Production 2026-10-06 did: two listings, a blank taxlot, two supplemental rows.
    `unique: position_key` still turns a split taxlot of one traded instrument into a failed
    build rather than a doubled gain.
    """

    def test_the_position_key_is_unique_per_taxlot(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _position(warehouse, day="2026-03-27", taxlot="T1")
        _build(warehouse)
        assert warehouse.execute("SELECT count(*) FROM platform.positions").fetchone() == (1,)

    def test_a_split_taxlot_breaks_the_declared_key_rather_than_doubling_a_gain(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """The failure the `unique: position_key` check exists for, demonstrated.

        `unique: external_id` CANNOT catch it — external_id carries the taxlot, so two lots on one
        traded instrument are two legitimately distinct keys and the check passes while each lot
        takes a full copy of the supplement. `position_key` is the supplement's own grain and does
        not carry the taxlot, so it collides and the model fails instead.

        A firm's SEI data being unavailable for a night is recoverable. A client's gain silently
        doubling is what somebody acts on.
        """
        _position(warehouse, day="2026-03-27", taxlot="T1", firm_gain=200.0)
        # A second taxlot on the same (account, portfolio, instrument), with no supplement of its
        # own — exactly what SEI splitting a lot would produce.
        warehouse.execute(
            "INSERT INTO raw.eod_positions VALUES"
            " (DATE '2026-03-27', 'A1', 'P1', 'I-EQ', NULL, 'T2',"
            " 'SED1', 'GBP', DATE '2026-03-27', 100.0, 10.0, 1000.0, 'C', 800.0, 'C',"
            " 'GBP', 0.0, 'C', 1000.0, 'C', 800.0, 'C', 1.0, 1.0)"
        )
        _build(warehouse)

        distinct_external, distinct_position, rows = warehouse.execute(
            "SELECT count(DISTINCT external_id), count(DISTINCT position_key), count(*)"
            "  FROM platform.positions"
        ).fetchone()
        assert (distinct_external, rows) == (2, 2), (
            "external_id carries the taxlot, so it stays unique — which is exactly why it is not "
            "the check that catches this"
        )
        assert distinct_position == 1, "position_key collides, and `unique: position_key` fails"

        doubled = warehouse.execute(
            "SELECT sum(unrealised_gain) FROM platform.positions"
        ).fetchone()[0]
        assert float(doubled) == 400.0, (
            "and this is what the check is preventing: 200.0 of gain, counted twice"
        )

    def test_a_blank_taxlot_does_not_collapse_two_traded_instruments(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """Production 2026-10-06. Two listings, one instrument id, TAXLOT_ID blank on both.

        `concat_ws` skips a null, so the taxlot used to drop out of `external_id` and both
        lines shared it. The supplemental join ignored `TRD_INSTRUMENT_ID`, so each line was
        paired with both gains and `platform.valuations` summed the account twice.
        """
        _position(
            warehouse,
            day="2026-03-27",
            taxlot=None,
            traded="TRD-A",
            quantity=373.0,
            base_value=100.0,
            firm_gain=10.0,
        )
        _position(
            warehouse,
            day="2026-03-27",
            taxlot=None,
            traded="TRD-B",
            quantity=227.0,
            base_value=40.0,
            firm_gain=-3.0,
        )
        _build(warehouse)

        rows = warehouse.execute(
            "SELECT external_id, position_key, quantity, unrealised_gain"
            "  FROM platform.positions ORDER BY external_id"
        ).fetchall()
        assert [(row[0], row[1], float(row[2]), float(row[3])) for row in rows] == [
            ("A1:P1:I-EQ::TRD-A:20260327", "A1:P1:I-EQ:TRD-A", 373.0, 10.0),
            ("A1:P1:I-EQ::TRD-B:20260327", "A1:P1:I-EQ:TRD-B", 227.0, -3.0),
        ]

        market_value, gain, position_count = warehouse.execute(
            "SELECT market_value, unrealised_gain, position_count FROM platform.valuations"
        ).fetchone()
        assert float(market_value) == 140.0
        assert float(gain) == 7.0
        assert position_count == 2


class TestAllocation:
    def test_a_holding_share_is_of_its_account(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        _position(warehouse, day="2026-03-27", instrument="I-EQ", base_value=750.0)
        _position(warehouse, day="2026-03-27", instrument="I-GBP", taxlot="T2", base_value=250.0)
        _build(warehouse)
        shares = dict(
            warehouse.execute(
                "SELECT instrument_key, allocation FROM platform.positions"
            ).fetchall()
        )
        assert float(shares["I-EQ"]) == 0.75
        assert float(shares["I-GBP"]) == 0.25

    def test_an_empty_account_does_not_divide_by_zero(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _position(warehouse, day="2026-03-27", base_value=0.0, base_cost=0.0, firm_gain=0.0)
        _build(warehouse)
        assert warehouse.execute("SELECT allocation FROM platform.positions").fetchone()[0] is None
