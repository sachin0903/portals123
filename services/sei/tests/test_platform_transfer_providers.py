"""`platform.transfer_providers` — one row per reference code, kinds kept beside it.

The same code is a transfer agent and a custodian (44 names in the current drop, the
name agreeing every time). Keying a row per kind would publish two counterparties for
one id, and the id is what a transfer-in sends.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

MODELS = Path(__file__).resolve().parent.parent / "models" / "platform"


def _model_sql() -> str:
    sql = (MODELS / "transfer_providers.sql").read_text()
    sql = re.sub(r"/\*.*?\*/", "", sql, count=1, flags=re.S).strip()
    return sql


@pytest.fixture
def warehouse() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA ref")
    connection.execute("CREATE SCHEMA platform")
    connection.execute(
        """
        CREATE TABLE ref.transfer_providers (
            provider_id varchar,
            provider_name varchar,
            kind varchar,
            country_code varchar,
            firm_id varchar,
            as_of_date date
        )
        """
    )
    return connection


def _build(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(f"CREATE OR REPLACE TABLE platform.transfer_providers AS {_model_sql()}")


def test_one_code_in_two_kinds_is_one_counterparty(warehouse: duckdb.DuckDBPyConnection) -> None:
    warehouse.execute(
        """
        INSERT INTO ref.transfer_providers VALUES
            ('58096900', 'Truist Financial', 'cash_agent', 'US', NULL, DATE '2026-10-02'),
            ('58096900', 'Truist Financial', 'custodian', 'US', NULL, DATE '2026-10-02'),
            ('58096900', 'Truist Financial', 'transfer_agent', 'US', NULL, DATE '2026-10-02'),
            ('100', 'Aviva Investors', 'transfer_agent', 'GB', NULL, DATE '2026-10-02')
        """
    )
    _build(warehouse)
    rows = warehouse.execute(
        "SELECT external_id, name, origo_id, json(data) FROM platform.transfer_providers ORDER BY 1"
    ).fetchall()
    assert [row[0] for row in rows] == ["100", "58096900"]
    truist = rows[1]
    assert truist[1] == "Truist Financial"
    assert truist[2] is None
    payload = json.loads(truist[3])
    assert payload["kinds"] == ["cash_agent", "custodian", "transfer_agent"]
    assert payload["country_code"] == "US"
    assert warehouse.execute("SELECT count(*) FROM platform.transfer_providers").fetchone() == (2,)
