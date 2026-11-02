"""`platform.portfolios` — executed in DuckDB, over rows.

`portfolioId` is REQUIRED by `createCurrencyReceipt`, `createcashdisbursement`,
`currencytransfercreate` and every `PSM/model/assignment/*`, and the estate could never supply one:
`docs/contributions.md` records the SEI contribution leg as blocked on "the account's SEI
account-number/portfolio-id links", of which only the account number was ever solved.

The failure this file catches is the GRAIN. The Accounts report is not one row per portfolio — the
2026-03-27 drop has 14,462 rows for 1,163 distinct (account, portfolio) pairs, and 362 of those
pairs appear exactly SEVEN times. A model that took the report at face value would put seven rows
in the book for one portfolio, and `unique: external_id` on an un-deduplicated projection is a
build failure rather than a wrong answer — but only if something executes it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

MODELS = Path(__file__).resolve().parent.parent / "models" / "platform"

ACCOUNTS = """
    dt date,
    "ACCOUNT_NUMBER" varchar, "FIRM_ID" varchar, "PORTFOLIO_ID" varchar,
    "PORTFOLIO_NAME" varchar, "PORTFOLIO_TYPE" varchar, "PORTFOLIO_PURPOSE" varchar,
    "LINKED_PORTFOLIO_ID" varchar, "DATE_LAST_UPDATED" date,
    "DATE_PORTFOLIO_OPENED" date, "DATE_PORTFOLIO_CLOSED" date
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

#: SEI's two codes. An investment-PURPOSE split, not a tax wrapper.
CAPITAL = "189"
INCOME = "190"


def _rewrite_line_comments_as_blocks(sql: str) -> str:
    """interlace rewrites `--` comments as one `/* … */` per line. DuckDB nests block
    comments, so a `--` line that contains `/*` (e.g. `PSM/model/assignment/*`) is
    `unterminated /* comment` at apply-time and a green unit test — `_build` used to
    leave `--` in place, which DuckDB treats as to-end-of-line and never sees the inner
    opener. Rewrite the same way the warehouse does so this file catches it."""
    rewritten: list[str] = []
    for line in sql.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("--"):
            indent = line[: len(line) - len(stripped)]
            rewritten.append(f"{indent}/* {stripped[2:].strip()} */")
        else:
            rewritten.append(line)
    return "\n".join(rewritten)


def _model_sql(name: str) -> str:
    sql = (MODELS / f"{name}.sql").read_text()
    sql = re.sub(r"/\*.*?\*/", "", sql, count=1, flags=re.S).strip()
    return _rewrite_line_comments_as_blocks(sql)


@pytest.fixture
def warehouse() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    for schema in ("raw", "platform"):
        connection.execute(f"CREATE SCHEMA {schema}")
    connection.execute(f"CREATE TABLE raw.accounts ({ACCOUNTS})")
    connection.execute(f"CREATE TABLE platform.tenancy ({TENANCY})")
    connection.execute(f"CREATE TABLE platform.accounts ({ACCOUNTS_SPINE})")
    connection.execute("INSERT INTO platform.tenancy VALUES ('A1', '3106', NULL, [], [])")
    connection.execute("INSERT INTO platform.accounts VALUES ('A1', '3106', ['3106'])")
    return connection


def _row(
    connection: duckdb.DuckDBPyConnection,
    *,
    account: str = "A1",
    portfolio: str = "P1",
    name: str = "Portfolio-01 (Capital)",
    kind: str = CAPITAL,
    purpose: str | None = None,
    linked: str | None = None,
    day: str = "2026-03-27",
    repeat: int = 1,
) -> None:
    """`repeat` is the point: the real report repeats each (account, portfolio) up to seven
    times."""
    for _ in range(repeat):
        connection.execute(
            "INSERT INTO raw.accounts VALUES (?, ?, '3106', ?, ?, ?, ?, ?, ?, ?, NULL)",
            [day, account, portfolio, name, kind, purpose, linked, day, day],
        )


def _build(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(f"CREATE OR REPLACE TABLE platform.portfolios AS {_model_sql('portfolios')}")


def test_a_glob_in_a_line_comment_is_an_unterminated_block_comment() -> None:
    """The warehouse failure this file used to miss: `-- PSM/model/assignment/*` is
    fine as a line comment and `unterminated /* comment` once interlace wraps it."""
    sql = "SELECT 1 AS x\n-- PSM/model/assignment/*\n"
    rewritten = _rewrite_line_comments_as_blocks(sql)
    with pytest.raises(duckdb.ParserException, match="unterminated"):
        duckdb.connect(":memory:").execute(rewritten)


def test_the_model_binds(warehouse: duckdb.DuckDBPyConnection) -> None:
    _row(warehouse)
    _build(warehouse)


class TestTheGrain:
    def test_a_repeated_report_row_becomes_one_portfolio(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """362 of the drop's 1,163 pairs appear seven times. Seven rows in the book for one
        portfolio would break `unique: external_id` — the point of deduplicating HERE is that the
        build fails rather than the money instruction naming an ambiguous portfolio."""
        _row(warehouse, repeat=7)
        _build(warehouse)
        assert warehouse.execute("SELECT count(*) FROM platform.portfolios").fetchone() == (1,)

    def test_two_portfolios_on_one_account_both_survive(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """59 of 1,104 accounts carry a Capital AND an Income portfolio — the reason a
        `platform.links` row (which permits exactly one) could never hold this."""
        _row(warehouse, portfolio="P1", kind=CAPITAL, repeat=7)
        _row(
            warehouse,
            portfolio="P2",
            name="Portfolio-01 (Income)",
            kind=INCOME,
            linked="P1",
            repeat=7,
        )
        _build(warehouse)
        rows = warehouse.execute(
            "SELECT external_id, portfolio_type, is_principal, linked_external_id"
            "  FROM platform.portfolios ORDER BY external_id"
        ).fetchall()
        assert rows == [
            ("P1", "capital", True, None),
            ("P2", "income", False, "P1"),
        ]

    def test_a_portfolio_with_no_id_is_dropped(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """`external_id` is the key and NOT NULL in the book; a blank one is not a portfolio."""
        _row(warehouse, portfolio="")
        _build(warehouse)
        assert warehouse.execute("SELECT count(*) FROM platform.portfolios").fetchone() == (0,)


class TestTheDefault:
    def test_the_capital_portfolio_is_the_principal_one(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _row(warehouse, kind=CAPITAL)
        _build(warehouse)
        assert warehouse.execute("SELECT is_principal FROM platform.portfolios").fetchone() == (
            True,
        )

    def test_an_unknown_code_is_neither_principal_nor_guessed(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """Stated as "is the Capital one" rather than "is not Income", so a third code arriving
        later is not silently promoted to the portfolio money defaults to. `portfolio_type` is NULL
        rather than a guess, and `portfolio_type_raw` keeps the code so it is diagnosable."""
        _row(warehouse, kind="991")
        _build(warehouse)
        row = warehouse.execute(
            "SELECT portfolio_type, portfolio_type_raw, is_principal FROM platform.portfolios"
        ).fetchone()
        assert row == (None, "991", False)


class TestWhatSeiActuallyPublishes:
    def test_the_purpose_code_zero_is_not_an_answer(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """`PORTFOLIO_PURPOSE` is blank on 12,588 of the drop's rows and '0' on 1,874. Zero is not
        a member of the 10-code Reference set, so storing it would look like an answer."""
        _row(warehouse, portfolio="P1", purpose="0")
        _row(warehouse, portfolio="P2", purpose="")
        _row(warehouse, portfolio="P3", purpose="7")
        _build(warehouse)
        purposes = dict(
            warehouse.execute("SELECT external_id, purpose FROM platform.portfolios").fetchall()
        )
        assert purposes == {"P1": None, "P2": None, "P3": "7"}

    def test_the_tenancy_comes_from_the_account(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """A portfolio has no DSR grant of its own — it is reached through the account that owns
        it, so it inherits that account's axes or it fans out to nobody."""
        _row(warehouse)
        _build(warehouse)
        row = warehouse.execute("SELECT key, lineage, firm_id FROM platform.portfolios").fetchone()
        assert row[0] == "3106"
        assert row[1] == ["3106"]
        assert row[2] == "3106"
