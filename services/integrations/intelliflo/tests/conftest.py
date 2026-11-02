"""Shared lake fixture: the full `core.platform.*`/`core.raw.*`/`core.ref.*` schema every
`providers/intelliflo/views/*.sql` file references, empty, with the real views created on top via
`providers.intelliflo.views.create_views` — so every lake test runs the actual view SQL, not a
reimplementation of it. `CREATE VIEW` validates its query at creation time (DuckDB), so every
table any view references must exist here even in tests that only care about one of them.

Two catalogues, like production: `core` is what the view files reference (`core.platform.*`, …);
`memory` (the connection's own default) is where `create_views` puts the views themselves —
see `shared/lake.py`'s docstring for why.
"""

from unittest.mock import MagicMock

import duckdb
import pytest

from intelliflo_sync.providers.intelliflo import db as iflo_db
from intelliflo_sync.providers.intelliflo import views as iflo_views


@pytest.fixture
def empty_lake(monkeypatch):
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS core")
    con.execute("CREATE SCHEMA core.platform")
    con.execute("CREATE SCHEMA core.raw")
    con.execute("CREATE SCHEMA core.ref")

    con.execute(
        """
        CREATE TABLE core.platform.clients (
            external_id VARCHAR, client_type_raw VARCHAR, first_name VARCHAR, surname VARCHAR,
            address STRUCT(lines VARCHAR[], city VARCHAR, region VARCHAR, postcode VARCHAR, country VARCHAR)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE core.platform.accounts (
            account_number VARCHAR, account_type VARCHAR, firm_id VARCHAR, status VARCHAR,
            dsr_team_ids VARCHAR[]
        )
        """
    )
    # The raw headers (and their amendments) a fee line is joined to: only the columns the income
    # items query reads. `dt` is the drop date, as in the real lake.
    for table in ("transaction_header", "transaction_header_upd"):
        con.execute(
            f"""
            CREATE TABLE core.raw.{table} (
                "FIRM_ID" VARCHAR, "TRANSACTION_ID" VARCHAR, "ACCOUNT_NUMBER" VARCHAR,
                "TRANSACTION_DATE" DATE, "TRANSACTION_CURRENCY" VARCHAR, dt DATE
            )
            """
        )
    con.execute(
        """
        CREATE TABLE core.platform.transactions (
            firm_id VARCHAR, external_id VARCHAR, account_number VARCHAR, instrument_key VARCHAR,
            asset_name VARCHAR, isin VARCHAR, description VARCHAR, transaction_date DATE,
            unit_price DECIMAL(20,8), gross_amount DECIMAL(20,8), quantity DECIMAL(20,8),
            currency VARCHAR, dsr_team_ids VARCHAR[]
        )
        """
    )
    con.execute(
        """
        CREATE TABLE core.platform.instruments (
            external_id VARCHAR, name VARCHAR, isin VARCHAR, asset_type VARCHAR
        )
        """
    )
    con.execute(
        "CREATE TABLE core.raw.client_account_link "
        '("CLIENT_ID" VARCHAR, "ACCOUNT_NUMBER" VARCHAR, "RELATIONSHIP" VARCHAR, dt DATE)'
    )
    con.execute(
        """
        CREATE TABLE core.raw.transaction_detail (
            "TRANSACTION_DETAIL_ID" VARCHAR, "TRANSACTION_ID" VARCHAR, "FIRM_ID" VARCHAR,
            "FEE_TYPE" VARCHAR, "TRANSACTION_AMT" DECIMAL(18,2), "DEBIT_CREDIT_FLAG_1" VARCHAR,
            dt DATE
        )
        """
    )
    con.execute("CREATE TABLE core.ref.codes (type VARCHAR, code VARCHAR, description VARCHAR, firm_id VARCHAR)")
    con.execute(
        """
        CREATE TABLE core.raw.eod_positions (
            "ACCOUNT_NUMBER" VARCHAR, "INSTRUMENT_ID" VARCHAR, "TAXLOT_PORTFOLIO_ID" VARCHAR,
            "TAXLOT_ID" VARCHAR, "TRD_INSTRUMENT_ID" VARCHAR, "AS_OF_DATE" DATE,
            "PRICE_DATE" DATE, "SETTLED_QTY" DECIMAL(38,10), "ASSET_PRICE" DECIMAL(38,10),
            "ASSET_CURRENCY" VARCHAR, dt DATE, _row BIGINT
        )
        """
    )

    iflo_views.create_views(con)

    proxy = MagicMock(wraps=con)
    proxy.close = lambda: None
    monkeypatch.setattr(iflo_db, "open_lake", lambda: proxy)
    return con
