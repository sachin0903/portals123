"""The conformed transactions model binds, and its canonical type mapping is the real one.

Two failures this exists to catch, both of which were live:

1. **The model did not publish what the materialiser reads.** It published
   ``account_number`` where ``app/materialise.py::_sync_transactions`` looks for
   ``account_external_id``, and no ``type`` column at all — so every SEI transaction was
   rejected ("unknown account", then "unmapped type") and ``platform.transactions`` was
   Seccl-only. A reject reads identically to "this tenant has no data here", which is why it
   went unnoticed.

2. **A guessed type mapping.** SEI's ``TRANSACTION_TYPE`` is a 124-member code set, published
   in the nightly Reference report under ``TYPE = 'TRANSACTION_TYPE'``. The expectations below
   are read from the p04 production drop, not inferred from the code names — in particular
   code 6 (Cash Receipt) is the rail every pension contribution arrives on, so getting it
   wrong silently empties the contributions workbench.

The stub is typed from ``sei_sync.schema``: the raw layer materialises real DATE/DECIMAL/
BOOLEAN columns, so a model that still treats one as text binds here and fails.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

MODELS = Path(__file__).resolve().parent.parent / "models" / "platform"

#: Columns of ``raw.transaction_header`` the conformed model reads, with the types the raw
#: layer actually materialises (``sei_sync.schema``'s declared set; anything absent there is
#: VARCHAR, which is deliberate for identifiers and numeric code enumerations).
TRANSACTION_HEADER = """
    "FIRM_ID" varchar, "TRANSACTION_ID" varchar, "TRANSACTION_TYPE" varchar,
    "ACCOUNT_NUMBER" varchar, "INSTRUMENT_ID" varchar,
    "TRANSACTION_DESCRIPTION" varchar, "DESCRIPTION_CODE_ID" varchar, "NARRATIVE" varchar,
    "TRANSACTION_CURRENCY" varchar,
    "TRANSACTION_DATE" date, "TRADE_DATE" date, "SETTLEMENT_DATE" date,
    "ACTUAL_SETTLEMENT_DATE" date,
    "QUANTITY" decimal(28,8), "UNIT_PRICE" decimal(20,8),
    "NET_TRANSACTION_AMOUNT" decimal(20,4), "GROSS_TRANSACTION_AMOUNT" decimal(20,4),
    "BASE_TRANSACTION_AMOUNT" decimal(20,4), "NET_FEE_AMT" decimal(20,4),
    "NET_ACCRUED_INTEREST_AMT" decimal(20,4), "NET_TAXES_AMT" decimal(20,4),
    "BOOK_VALUE_IN_ASSET_CURRENCY" decimal(20,4),
    "DEBIT_CREDIT_FLAG_1" varchar, "DEBIT_CREDIT_FLAG_2" varchar,
    "DEBIT_CREDIT_FLAG_3" varchar, "DEBIT_CREDIT_FLAG_4" varchar,
    "DEBIT_CREDIT_FLAG_5" varchar, "DEBIT_CREDIT_FLAG_6" varchar,
    "DEBIT_CREDIT_FLAG_7" varchar, "DEBIT_CREDIT_FLAG_12" varchar,
    -- BOOLEAN, not VARCHAR: sei_sync.schema declares it, so the raw layer materialises it
    -- typed. The model still compares it to 'Y', which DuckDB coerces — pinned here so a
    -- future profile run that flips the type cannot break the comparison unnoticed.
    "REVERSAL_FLAG" boolean,
    dt date
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

#: code -> canonical platform.transaction_type, read from the Reference report's
#: TRANSACTION_TYPE set. NULL means "SEI's taxonomy has no unambiguous canonical equivalent",
#: which the materialiser counts as a visible reject rather than filing money under a guess.
EXPECTED_TYPES: dict[str, str | None] = {
    "1": "buy",
    "2": "sell",
    "3": "transfer_out",  # Free Delivery
    "4": "transfer_in",  # Free Receipt
    # Directionless on their own -- SEI publishes the direction as a separate code pair
    # (95/96, 97/98, 106/107), so guessing here inverts half of them.
    "5": None,  # Security Transfer
    "6": "deposit",  # Cash Receipt -- the contribution rail
    "7": "withdrawal",  # Cash Disbursement
    # Cash Transfer. Direction is the signed net, so a row with no amount stays unmapped.
    "8": None,
    "9": "interest",
    "10": "interest",
    "11": "corporate_action",
    "12": "fee",
    "13": "fee",  # One Time fee
    "14": "fee",  # Periodic Fee
    "15": "adjustment",
    "17": "corporate_action",  # Bonus Issue
    "18": "dividend",  # Capital Gains Distribution
    "19": "dividend",  # Cash Dividend
    "20": None,  # "Change" -- not a money movement
    "30": "interest",  # Interest Payment
    "33": "corporate_action",  # Merger
    "47": "corporate_action",  # Spin-Off
    "53": None,  # Annual General Meeting -- a notice, not a settlement
    "60": None,  # Court Meeting
    "62": None,  # Disclosure
    "68": None,  # Extraordinary General Meeting
    "73": None,  # Ordinary General Meeting
    "83": "tax",  # Tax Reclaim
    "84": None,  # Trading Status: Active
    "85": None,  # Trading Status: Delisted
    "86": None,  # Trading Status: Suspended
    "88": None,  # Custody Transfer -- directionless
    "89": None,  # Custodian Account Transfer -- directionless
    "92": "adjustment",  # FX-Spot
    "95": "transfer_out",  # Security Transfer -Transfer From
    "96": "transfer_in",  # Security Transfer -Transfer To
    "97": "transfer_out",  # Currency Transfer From
    "98": "transfer_in",  # Currency Transfer To
    "105": "interest",  # Deferred Interest Pay Up
    "106": "transfer_out",  # Intra Account Cash Transfer From
    "107": "transfer_in",  # Intra Account Cash Transfer To
    "108": None,  # Free Form
    "109": None,  # Junior ISA Conversion Free Form
    "117": "fee",  # Real Time Fee
    "118": "fee",  # Money Market Redemption Fee
    "121": None,  # Loan Payable
    "122": None,  # Loan Receivable
    "126": "adjustment",  # Free Form Adjustment
}

#: The conformed spine + the fields ``_sync_transactions`` maps into OLTP.
CONTRACT = {
    "external_id",
    "key",
    "lineage",
    "as_of_date",
    "data",
    "account_external_id",
    "type",
    "source_type",
    "source_subtype",
    "instrument_key",
    "currency",
    "quantity",
    "unit_price",
    "gross_amount",
    "net_amount",
    "fee_amount",
    "tax_amount",
    "transaction_date",
    "settlement_date",
    "posted_at",
    "is_reversal",
    "order_key",
    "report_group_kind",
    "dealing_charges",
    "other_charges",
    "fee_type",
    "book_cost",
    "realised_gain",
    "equalisation_payment",
    "subscription_amount",
}


def _model_sql(name: str) -> str:
    """The model body, with the interlace front-matter comment stripped."""
    sql = (MODELS / f"{name}.sql").read_text()
    return re.sub(r"/\*.*?\*/", "", sql, count=1, flags=re.S).strip()


@pytest.fixture
def warehouse() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    for schema in ("raw", "ref", "platform"):
        connection.execute(f"CREATE SCHEMA {schema}")
    connection.execute(f"CREATE TABLE raw.transaction_header ({TRANSACTION_HEADER})")
    connection.execute(f"CREATE TABLE raw.transaction_header_upd ({TRANSACTION_HEADER})")
    # The columns the conformed model reads off the API pull. Types match api_rows.SCHEMA.
    connection.execute(
        """
        CREATE TABLE raw.api_transactions (
            transaction_id bigint, firm_id varchar, account_number varchar,
            transaction_type varchar, transaction_description varchar,
            transaction_date date, trade_date date, settlement_date date,
            reversal_flag boolean, local_currency_code varchar,
            transaction_quantity decimal(28, 8),
            local_transaction_gross_amount decimal(28, 4),
            local_net_transaction_amount decimal(28, 4),
            base_net_transaction_amount decimal(28, 4),
            net_charges_amount decimal(28, 4), net_taxes_amount decimal(28, 4),
            local_book_value decimal(28, 4),
            isin varchar, sedol varchar, fetched_at timestamp
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE ref.codes (
            type varchar, code varchar, description varchar, firm_id varchar
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE platform.instruments (
            external_id varchar, isin varchar, sedol varchar, name varchar
        )
        """
    )
    connection.execute(f"CREATE TABLE platform.tenancy ({TENANCY})")
    connection.execute(f"CREATE TABLE platform.accounts ({ACCOUNTS_SPINE})")
    return connection


def _build(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f"CREATE OR REPLACE TABLE platform.transactions AS {_model_sql('transactions')}"
    )


def test_the_model_binds_against_the_typed_raw_layer(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """A Binder Error here is a deploy failure — it aborts the whole provider's promote."""
    _build(warehouse)


def test_the_model_publishes_what_the_materialiser_reads(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """``account_external_id`` and ``type`` are the two that were missing, and their absence
    rejected every SEI transaction before it reached OLTP."""
    _build(warehouse)
    columns = {row[0] for row in warehouse.execute("DESCRIBE platform.transactions").fetchall()}
    missing = CONTRACT - columns
    assert not missing, f"platform.transactions is missing {sorted(missing)}"


def test_account_external_id_is_the_key_platform_accounts_publishes(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """platform/accounts.sql publishes ``external_id = ACCOUNT_NUMBER``. If these two ever
    disagree the join in ``_sync_transactions`` silently resolves nothing."""
    warehouse.execute(
        """
        INSERT INTO raw.transaction_header ("TRANSACTION_ID", "TRANSACTION_TYPE",
            "ACCOUNT_NUMBER", "DEBIT_CREDIT_FLAG_1", dt)
        VALUES ('t1', '6', 'G074SEI001', 'C', DATE '2025-10-24')
        """
    )
    _build(warehouse)
    row = warehouse.execute(
        "SELECT account_external_id, account_number FROM platform.transactions"
    ).fetchone()
    assert row == ("G074SEI001", "G074SEI001")


@pytest.mark.parametrize(("code", "expected"), sorted(EXPECTED_TYPES.items()))
def test_canonical_type_matches_the_reference_report(
    warehouse: duckdb.DuckDBPyConnection, code: str, expected: str | None
) -> None:
    """One row per SEI transaction-type code, checked against the published enumeration."""
    warehouse.execute(
        """
        INSERT INTO raw.transaction_header ("TRANSACTION_ID", "TRANSACTION_TYPE",
            "ACCOUNT_NUMBER", "DEBIT_CREDIT_FLAG_1", dt)
        VALUES (?, ?, 'A1', 'C', DATE '2025-10-24')
        """,
        [f"t{code}", code],
    )
    _build(warehouse)
    assert warehouse.execute("SELECT type FROM platform.transactions").fetchone()[0] == expected


def test_cash_receipt_is_a_deposit(warehouse: duckdb.DuckDBPyConnection) -> None:
    """The whole contributions flow starts here: a client's money arrives as SEI transaction
    type 6, and the workbench's unclassified-credit queue reads `type = 'deposit'`."""
    assert EXPECTED_TYPES["6"] == "deposit"


def test_the_description_code_is_resolved_to_its_reference_name(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """DESCRIPTION_CODE_ID separates a contribution (21121) from the relief payment that
    follows it (21122) — both are type 6 Cash Receipt. The listing prints source_subtype, so
    the code itself would show as "Deposit: 21122". The Reference description is the subtype;
    the code stays on description_code_id for the reconciliation."""
    warehouse.execute(
        """
        INSERT INTO ref.codes (type, code, description, firm_id)
        VALUES ('DESCRIPTION_CODE_ID', '21122', 'Relief at source', '3116'),
               ('DESCRIPTION_CODE_ID', '20043', 'Interest Income', '3116')
        """
    )
    warehouse.execute(
        """
        INSERT INTO raw.transaction_header ("TRANSACTION_ID", "TRANSACTION_TYPE",
            "ACCOUNT_NUMBER", "DESCRIPTION_CODE_ID", "TRANSACTION_DESCRIPTION",
            "DEBIT_CREDIT_FLAG_1", dt)
        VALUES ('relief', '6', 'A1', '21122', 'Cash Receipt', 'C', DATE '2025-10-24'),
               ('interest', '9', 'A1', '20043', 'Interest Income', 'C', DATE '2025-10-24'),
               ('unnamed', '6', 'A1', '99999', 'Cash Receipt', 'C', DATE '2025-10-24')
        """
    )
    _build(warehouse)
    rows = {
        row[0]: row[1:]
        for row in warehouse.execute(
            "SELECT external_id, source_type, source_subtype, description_code_id, type"
            " FROM platform.transactions"
        ).fetchall()
    }
    assert rows["relief"] == ("Cash Receipt", "Relief at source", "21122", "deposit")
    assert rows["interest"] == ("Interest Income", "Interest Income", "20043", "interest")
    # No Reference row: the listing falls through to the file's own words, not the number.
    assert rows["unnamed"] == ("Cash Receipt", None, "99999", "deposit")


def test_a_cashless_transfer_carries_its_book_value(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """A free receipt has net and gross of 0. The cost is BOOK_VALUE_IN_ASSET_CURRENCY,
    unsigned, with the direction on the flag that follows it. A delivery is a debit."""
    warehouse.execute(
        """
        INSERT INTO raw.transaction_header (
            "TRANSACTION_ID", "TRANSACTION_TYPE", "ACCOUNT_NUMBER",
            "NET_TRANSACTION_AMOUNT", "GROSS_TRANSACTION_AMOUNT",
            "BOOK_VALUE_IN_ASSET_CURRENCY", "DEBIT_CREDIT_FLAG_12",
            "DEBIT_CREDIT_FLAG_1", dt
        )
        VALUES ('in', '4', 'A1', 0, 0, 147960.00, 'C', 'C', DATE '2025-10-24'),
               ('out', '3', 'A1', 0, 0, 147960.00, 'D', 'D', DATE '2025-10-24')
        """
    )
    warehouse.execute(
        """
        INSERT INTO raw.api_transactions (
            transaction_id, firm_id, account_number, transaction_type,
            transaction_date, local_currency_code,
            local_net_transaction_amount, local_transaction_gross_amount,
            local_book_value, fetched_at
        )
        VALUES (4, '3116', 'A1', 'Free Receipt', DATE '2025-10-24', 'GBP',
                0, 0, 3763.20, TIMESTAMP '2025-10-24 07:00:00')
        """
    )
    _build(warehouse)
    rows = {
        row[0]: row[1:]
        for row in warehouse.execute(
            "SELECT external_id, net_amount, gross_amount, book_value FROM platform.transactions"
        ).fetchall()
    }
    assert rows["in"] == (Decimal("0.0000"), Decimal("0.0000"), Decimal("147960.0000"))
    assert rows["out"] == (Decimal("0.0000"), Decimal("0.0000"), Decimal("-147960.0000"))
    # The API figure is already signed; the file's flag is not applied again.
    assert rows["4"] == (Decimal("0.0000"), Decimal("0.0000"), Decimal("3763.2000"))


def test_debit_credit_flags_still_sign_the_amount(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """SEI ships unsigned money with the direction in a companion flag. The unclassified-credit
    queue filters on `amount > 0`, so an unsigned disbursement would appear as money in."""
    warehouse.execute(
        """
        INSERT INTO raw.transaction_header ("TRANSACTION_ID", "TRANSACTION_TYPE",
            "ACCOUNT_NUMBER", "NET_TRANSACTION_AMOUNT", "DEBIT_CREDIT_FLAG_1", dt)
        VALUES ('in', '6', 'A1', 500.00, 'C', DATE '2025-10-24'),
               ('out', '7', 'A1', 500.00, 'D', DATE '2025-10-24')
        """
    )
    _build(warehouse)
    rows = dict(
        warehouse.execute("SELECT external_id, net_amount FROM platform.transactions").fetchall()
    )
    assert rows["in"] > 0
    assert rows["out"] < 0


def test_a_cash_transfer_follows_the_signed_net(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """Code 8 is 'Cash Transfer' with no from/to. The signed net is the direction.
    A zero net stays unmapped: guessing transfer_in would invert the blank ones."""
    warehouse.execute(
        """
        INSERT INTO ref.codes (type, code, description, firm_id)
        VALUES ('TRANSACTION_TYPE', '8', 'Cash Transfer', NULL)
        """
    )
    warehouse.execute(
        """
        INSERT INTO raw.transaction_header (
            "TRANSACTION_ID", "TRANSACTION_TYPE", "ACCOUNT_NUMBER",
            "NET_TRANSACTION_AMOUNT", "DEBIT_CREDIT_FLAG_1", dt
        )
        VALUES ('in', '8', 'A1', 500.00, 'C', DATE '2025-10-24'),
               ('out', '8', 'A1', 500.00, 'D', DATE '2025-10-24'),
               ('nil', '8', 'A1', 0, 'C', DATE '2025-10-24')
        """
    )
    warehouse.execute(
        """
        INSERT INTO raw.api_transactions (
            transaction_id, firm_id, account_number, transaction_type,
            transaction_date, local_currency_code,
            local_net_transaction_amount, fetched_at
        )
        VALUES (80, '3116', 'A1', 'Cash Transfer', DATE '2025-10-24', 'GBP',
                25.00, TIMESTAMP '2025-10-24 07:00:00'),
               (81, '3116', 'A1', 'Cash Transfer', DATE '2025-10-24', 'GBP',
                -25.00, TIMESTAMP '2025-10-24 07:00:00')
        """
    )
    _build(warehouse)
    rows = {
        row[0]: row[1:]
        for row in warehouse.execute(
            "SELECT external_id, type, status FROM platform.transactions"
        ).fetchall()
    }
    assert rows["in"] == ("transfer_in", "settled")
    assert rows["out"] == ("transfer_out", "settled")
    assert rows["nil"] == (None, "settled")
    assert rows["80"] == ("transfer_in", "settled")
    assert rows["81"] == ("transfer_out", "settled")


def test_an_api_only_id_is_published_and_a_shared_id_stays_with_the_file(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """The pull already has the rows. They reach the book only by landing in this model.

    A shared transaction_id stays with the file: the file has the signed flags and the
    description code, and publishing both would double the movement. An id the drop does
    not carry is the hole the API exists to fill, and it has to resolve to the same
    canonical type the file's code would have.
    """
    warehouse.execute(
        """
        INSERT INTO ref.codes (type, code, description, firm_id)
        VALUES ('TRANSACTION_TYPE', '6', 'Cash Receipt', NULL),
               ('TRANSACTION_TYPE', '14', 'Periodic Fee', NULL)
        """
    )
    warehouse.execute(
        """
        INSERT INTO platform.instruments (external_id, isin, sedol, name)
        VALUES ('inst-linde', 'IE00BZ12WP82', 'BNZHB81', 'Linde PLC')
        """
    )
    warehouse.execute(
        """
        INSERT INTO raw.transaction_header (
            "TRANSACTION_ID", "TRANSACTION_TYPE", "ACCOUNT_NUMBER",
            "NET_TRANSACTION_AMOUNT", "DEBIT_CREDIT_FLAG_1", dt
        )
        VALUES ('100', '6', 'A1', 10.00, 'C', DATE '2026-03-27')
        """
    )
    warehouse.execute(
        """
        INSERT INTO raw.api_transactions (
            transaction_id, firm_id, account_number, transaction_type,
            transaction_description, transaction_date, local_currency_code,
            local_net_transaction_amount, local_transaction_gross_amount,
            isin, fetched_at
        )
        VALUES
            (100, '3106', 'A1', 'Cash Receipt', 'should not win', DATE '2026-03-27',
             'GBP', 999.00, 999.00, NULL, TIMESTAMP '2026-03-28 07:00:00'),
            (200, '3106', 'A1', 'Cash Receipt', 'Contribution', DATE '2026-04-02',
             'GBP', 500.00, 500.00, 'IE00BZ12WP82', TIMESTAMP '2026-04-03 07:00:00'),
            (201, '3106', 'A1', 'Periodic Fee', 'Quarterly', DATE '2026-04-02',
             'GBP', -12.50, -12.50, NULL, TIMESTAMP '2026-04-03 07:00:00')
        """
    )
    _build(warehouse)
    rows = {
        row[0]: row[1:]
        for row in warehouse.execute(
            """
            SELECT external_id, type, net_amount, instrument_key, isin, asset_name, source_type
            FROM platform.transactions
            ORDER BY external_id
            """
        ).fetchall()
    }
    assert set(rows) == {"100", "200", "201"}
    assert rows["100"][0] == "deposit"
    assert rows["100"][1] == 10
    assert rows["200"] == (
        "deposit",
        500,
        "inst-linde",
        "IE00BZ12WP82",
        "Linde PLC",
        "Cash Receipt",
    )
    assert rows["201"][0] == "fee"
    assert rows["201"][1] == -12.5
