"""`platform.bank_accounts` — PaidTo profiles, executed in DuckDB.

The codes are Reference `PAY_TO_METHOD` and `ROUTING_NUMBER_TYPE`. A sort code is
type 5 and is stored as `11-22-33`. A 7-digit UK account number is padded to 8.
SEPA (code 8) has no book method. An earlier file is not the latest profile.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

MODELS = Path(__file__).resolve().parent.parent / "models" / "platform"

PAID_TO = """
    dt DATE,
    "ACCOUNT_NUMBER" VARCHAR,
    "PROFILE_ID" VARCHAR,
    "PAY_TO_RECIPIENT_NAME" VARCHAR,
    "PAY_TO_METHOD" VARCHAR,
    "ROUTING_NUMBER_TYPE" VARCHAR,
    "ROUTING_NUMBER" VARCHAR,
    "DESTINATION_ACCOUNT" VARCHAR,
    "IBAN" VARCHAR,
    "AD_HOC_PAY_TO_FLAG" BOOLEAN,
    "RECEIVING_BANK_NAME" VARCHAR,
    "FIRM_ID" VARCHAR,
    _row INTEGER
"""


def _model_sql() -> str:
    sql = (MODELS / "bank_accounts.sql").read_text()
    return re.sub(r"/\*.*?\*/", "", sql, count=1, flags=re.S).strip()


def _warehouse() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA raw")
    connection.execute("CREATE SCHEMA platform")
    connection.execute(f"CREATE TABLE raw.paid_to ({PAID_TO})")
    connection.execute(
        "CREATE TABLE platform.tenancy ("
        " account_number VARCHAR, firm_id VARCHAR, subfirm_id VARCHAR, dsr_team_ids VARCHAR[])"
    )
    return connection


def _insert(connection: duckdb.DuckDBPyConnection, **row: object) -> None:
    connection.execute(
        "INSERT INTO raw.paid_to VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            row.get("dt"),
            row.get("account_number"),
            row.get("profile_id"),
            row.get("name"),
            row.get("method"),
            row.get("routing_type"),
            row.get("routing_number"),
            row.get("destination_account"),
            row.get("iban"),
            row.get("ad_hoc", False),
            row.get("bank"),
            row.get("firm_id", "3102"),
            row.get("_row", 1),
        ],
    )


def test_a_uk_profile_is_a_pay_to_entry_and_an_older_file_is_ignored() -> None:
    connection = _warehouse()
    connection.execute("INSERT INTO platform.tenancy VALUES ('A1', '3102', '3110', ['T1'])")
    _insert(
        connection,
        dt="2026-09-01",
        account_number="A1",
        profile_id="P-OLD",
        name="Old Payee",
        method="5",
        routing_type="5",
        routing_number="000000",
        destination_account="11111111",
        bank="Old Bank",
    )
    _insert(
        connection,
        dt="2026-10-01",
        account_number="A1",
        profile_id="P1",
        name="Ada Lovelace",
        method="5",
        routing_type="5",
        routing_number="11-22-33",
        destination_account="1234567",
        bank="Test Bank",
        _row=2,
    )
    _insert(
        connection,
        dt="2026-10-01",
        account_number="A1",
        profile_id="P2",
        name="Grace Hopper",
        method="10",
        routing_type="5",
        routing_number="445566",
        destination_account="87654321",
        bank="Other Bank",
        _row=1,
    )
    connection.execute(f"CREATE TABLE platform.bank_accounts AS {_model_sql()}")
    rows = {
        row[0]: row
        for row in connection.execute(
            "SELECT profile_id, method, sort_code, account_number, is_ad_hoc, "
            "entry::VARCHAR, key FROM platform.bank_accounts ORDER BY profile_id"
        ).fetchall()
    }
    assert list(rows) == ["P1", "P2"]
    profile_id, method, sort_code, account_number, is_ad_hoc, entry, key = rows["P1"]
    assert profile_id == "P1"
    assert method == "bacs"
    assert sort_code == "11-22-33"
    assert account_number == "01234567"
    assert is_ad_hoc is False
    assert key == "3110"
    parsed = json.loads(entry)
    assert parsed["direction"] == "pay_to"
    assert parsed["provider"] == "sei"
    assert parsed["provider_ref"] == "P1"
    assert parsed["name"] == "Ada Lovelace"
    assert parsed["bank"] == "Test Bank"
    assert parsed["sort_code"] == "11-22-33"
    assert parsed["account_number"] == "01234567"
    assert parsed["method"] == "bacs"
    assert "iban" not in parsed
    assert json.loads(rows["P2"][5])["method"] == "faster_payment"


def test_swift_sepa_and_ad_hoc_keep_only_the_fields_the_code_supports() -> None:
    connection = _warehouse()
    _insert(
        connection,
        dt="2026-10-01",
        account_number="A1",
        profile_id="SW",
        name="International",
        method="7",
        routing_type="2",
        routing_number="testgb2l",
        iban="gb00 test 0000",
        bank="Foreign Bank",
    )
    _insert(
        connection,
        dt="2026-10-01",
        account_number="A1",
        profile_id="SEPA",
        name="Sepa Payee",
        method="8",
        routing_type="2",
        routing_number="TESTGB2L",
        iban="GB00TEST0000",
        _row=2,
    )
    _insert(
        connection,
        dt="2026-10-01",
        account_number="A1",
        profile_id="ONCE",
        name="Once",
        method="3",
        routing_type="5",
        routing_number="112233",
        destination_account="12345678",
        ad_hoc=True,
        _row=3,
    )
    connection.execute(f"CREATE TABLE platform.bank_accounts AS {_model_sql()}")
    by_id = {
        row[0]: row
        for row in connection.execute(
            "SELECT profile_id, method, bic, iban, is_ad_hoc, entry::VARCHAR "
            "FROM platform.bank_accounts"
        ).fetchall()
    }
    swift = json.loads(by_id["SW"][5])
    assert by_id["SW"][1] == "swift"
    assert by_id["SW"][2] == "TESTGB2L"
    assert by_id["SW"][3] == "GB00TEST0000"
    assert swift["bic"] == "TESTGB2L"
    assert swift["iban"] == "GB00TEST0000"
    assert "sort_code" not in swift
    sepa = json.loads(by_id["SEPA"][5])
    assert by_id["SEPA"][1] is None
    assert "method" not in sepa
    assert sepa["iban"] == "GB00TEST0000"
    assert by_id["ONCE"][4] is True
    assert json.loads(by_id["ONCE"][5])["method"] == "cheque"
