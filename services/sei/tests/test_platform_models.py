"""`platform.models` — executed in DuckDB, over rows.

Without this relation `platform.accounts.model_key` resolved against nothing: 85% of SEI accounts
name a model and every one of them carried `model_id` NULL in the book.

SEI's own two model reports (`ModelsIntExt` — 16 columns including MODEL_NAME, MODEL_TYPE,
RISK_RATING_ID — and `ModelAlloc` — 22 columns) are declared and **0 rows on all 74 drops of the
p04 corpus**, so the catalogue is DISCOVERED from the accounts that use it, exactly as
`platform.products` is discovered from the same report.

Four things are worth executing for, and an empty stub table can see none of them:

1. **THE GRAIN.** The Accounts report is grained account × portfolio × something further — 14,462
   rows for 1,104 accounts — so a model appears hundreds of times and `unique: external_id`
   fails on an un-grouped projection.
2. **THE NAME.** `IMPLEMENTATION_MODEL_NAME` is populated on 6% of rows (23 of the 49 models), and
   the other 94% carry an EMPTY STRING rather than a NULL. An aggregate that let a blank win would
   name a model `""`.
3. **THE TYPED NULLS.** Registering `models` registers four entities, and every column their
   projections ask of a provider must exist here — a bare `NULL` types as INTEGER in DuckDB, so a
   downstream cast against VARCHAR silently never matches (the `key` trap).
4. **`allocations` AS AN EMPTY ARRAY**, so the sibling that unnests it yields zero lines rather
   than failing to bind.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

MODELS = Path(__file__).resolve().parent.parent / "models" / "platform"

#: Only the columns this model reads, plus `dt`. The Accounts report has 200-odd; naming them all
#: would make the fixture a transcription rather than a contract.
ACCOUNTS = """
    dt date,
    "ACCOUNT_NUMBER" varchar, "FIRM_ID" varchar, "MODEL_ID" varchar,
    "IMPLEMENTATION_MODEL_NAME" varchar, "DATE_LAST_UPDATED" date
"""

TENANCY = """
    account_number varchar, firm_id varchar, subfirm_id varchar,
    dsr_team_ids varchar[], adviser_firm_dsr_ids varchar[]
"""


def _model_sql(name: str) -> str:
    sql = (MODELS / f"{name}.sql").read_text()
    return re.sub(r"/\*.*?\*/", "", sql, count=1, flags=re.S).strip()


@pytest.fixture
def warehouse() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    for schema in ("raw", "platform"):
        connection.execute(f"CREATE SCHEMA {schema}")
    connection.execute(f"CREATE TABLE raw.accounts ({ACCOUNTS})")
    connection.execute(
        "CREATE TABLE raw.api_models ("
        " firm_id VARCHAR, model_id VARCHAR, model_name VARCHAR, model_type VARCHAR)"
    )
    connection.execute(f"CREATE TABLE platform.tenancy ({TENANCY})")
    connection.execute("INSERT INTO platform.tenancy VALUES ('A1', '3102', NULL, [], [])")
    return connection


def _account(
    connection: duckdb.DuckDBPyConnection,
    *,
    account: str = "A1",
    firm: str = "3102",
    model: str | None = "87984",
    model_name: str | None = None,
    day: str = "2026-03-27",
    repeat: int = 1,
) -> None:
    """`repeat` is the point for the grain: the real report repeats an account's row."""
    for _ in range(repeat):
        connection.execute(
            "INSERT INTO raw.accounts VALUES (?, ?, ?, ?, ?, ?)",
            [day, account, firm, model, model_name, day],
        )


def _run(connection: duckdb.DuckDBPyConnection) -> list[dict[str, object]]:
    connection.execute(f"CREATE OR REPLACE VIEW platform.models AS {_model_sql('models')}")
    cursor = connection.execute("SELECT * FROM platform.models ORDER BY external_id")
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


class TestTheGrain:
    """One row per model, out of a report that repeats each account."""

    def test_a_model_used_by_many_accounts_appears_once(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        for n in range(6):
            _account(warehouse, account=f"A{n}", model="87984", repeat=7)
        rows = _run(warehouse)
        assert [r["external_id"] for r in rows] == ["87984"]

    def test_the_declared_unique_check_would_hold(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        for model in ("87984", "83011", "87991"):
            _account(warehouse, account="A1", model=model, repeat=3)
        rows = _run(warehouse)
        assert len({r["external_id"] for r in rows}) == len(rows) == 3

    def test_an_account_naming_no_model_contributes_none(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """15% of the drop's account rows have a blank `MODEL_ID`, and a blank is not a model."""
        _account(warehouse, model=None)
        _account(warehouse, account="A2", model="  ")
        assert _run(warehouse) == []

    def test_a_row_with_no_firm_is_dropped(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """`not_null: firm_id` is declared, and the tenant predicate narrows on it — an
        unattributable model would belong to no firm and project to nobody."""
        _account(warehouse, firm=None)
        assert _run(warehouse) == []


class TestTheName:
    """`IMPLEMENTATION_MODEL_NAME` is 6% populated and the other 94% is an EMPTY STRING."""

    def test_the_name_is_taken_where_the_drop_has_one(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _account(warehouse, model="87955", model_name="AIM Withdrawal")
        assert _run(warehouse)[0]["name"] == "AIM Withdrawal"

    def test_a_blank_never_beats_a_real_name(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """THE CASE THE REAL DATA IS ALMOST ENTIRELY MADE OF. One model, hundreds of rows, a name
        on a handful of them — and `max()` over the raw column would return `''` because an empty
        string sorts above nothing. `nullif` is what makes the name win."""
        _account(warehouse, account="A1", model="87955", model_name="", repeat=40)
        _account(warehouse, account="A2", model="87955", model_name="AIM Withdrawal")
        _account(warehouse, account="A3", model="87955", model_name="   ", repeat=10)
        rows = _run(warehouse)
        assert len(rows) == 1
        assert rows[0]["name"] == "AIM Withdrawal"

    def test_a_model_the_drop_never_names_is_still_published(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """26 of the 49. The sink's own projection coalesces `name` to `external_id` — "a model the
        provider named nothing is still a model" — so publishing NULL here is what lets the book
        show the id and lets a firm rename it, which the nightly run then never overwrites."""
        _account(warehouse, model="87984", model_name="", repeat=5)
        rows = _run(warehouse)
        assert len(rows) == 1
        assert rows[0]["name"] is None

    def test_the_assignment_read_names_a_model_the_file_left_blank(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """The file's name is an empty string on most rows. The assignment read is what
        turns the id the book would otherwise show into the model's name, and it is also
        the only place the type is stated."""
        _account(warehouse, model="87741", model_name="", repeat=4)
        warehouse.execute(
            "INSERT INTO raw.api_models VALUES "
            "('3102', '87741', 'Marlborough Fund (M) Risk profile 6', 'Implementation Model')"
        )
        row = _run(warehouse)[0]
        assert row["name"] == "Marlborough Fund (M) Risk profile 6"
        assert row["model_type"] == "Implementation Model"

    def test_the_assignment_name_wins_over_the_file_name(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _account(warehouse, model="87741", model_name="UK Gilt Ladder")
        warehouse.execute(
            "INSERT INTO raw.api_models VALUES "
            "('3102', '87741', 'Optimal Risk 5 Implementation', 'Implementation Model')"
        )
        assert _run(warehouse)[0]["name"] == "Optimal Risk 5 Implementation"

    def test_a_blank_assignment_name_does_not_erase_the_file_name(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _account(warehouse, model="87955", model_name="AIM Withdrawal")
        warehouse.execute("INSERT INTO raw.api_models VALUES ('3102', '87955', '', NULL)")
        row = _run(warehouse)[0]
        assert row["name"] == "AIM Withdrawal"
        assert row["model_type"] is None


class TestWhatSeiDoesNotSay:
    """Every column the four `models`-sourced entities ask of a provider, typed."""

    def test_the_unstated_columns_are_present_and_typed(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """A bare `NULL` types as INTEGER in DuckDB, so a cast against VARCHAR silently never
        matches. An ABSENT column is worse: a Binder Error that aborts the whole tenant's
        projection and lands as "this firm has no data"."""
        _account(warehouse)
        warehouse.execute(f"CREATE OR REPLACE VIEW platform.models AS {_model_sql('models')}")
        types = dict(
            warehouse.execute(
                "SELECT column_name, data_type FROM information_schema.columns"
                " WHERE table_name = 'models'"
            ).fetchall()
        )
        for column in (
            "aim",
            "model_type",
            "decision_maker",
            "risk_rating",
            "status_raw",
            "asset_mix",
            "commentary",
            "available_to_key",
            "available_to_org_name",
        ):
            assert types.get(column) == "VARCHAR", f"{column}: {types.get(column)}"
        assert types["model_charge"].startswith("DECIMAL")
        assert types["cash_allocation"].startswith("DECIMAL")
        assert types["vatable"] == "BOOLEAN"
        assert types["suspend_rebalances"] == "BOOLEAN"
        assert types["product_key"] == "VARCHAR[]"

    def test_allocations_is_an_empty_array_not_null(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """`_MODEL_LINES_FROM` unnests `json_extract(allocations, '$[*]')`. An empty array yields
        zero allocation lines — so no model VERSION is minted, which is right: a version is a
        decision about a set of holdings and SEI has published no holdings to decide about."""
        _account(warehouse)
        row = _run(warehouse)[0]
        assert row["allocations"] == "[]"
        lines = warehouse.execute(
            "SELECT count(*) FROM ("
            "  SELECT unnest(json_extract(allocations, '$[*]')) AS entry FROM platform.models"
            ")"
        ).fetchone()
        assert lines is not None and lines[0] == 0

    def test_the_wrappers_are_an_empty_list_rather_than_a_guess(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """SEI states which accounts USE a model, never which wrappers a model is FOR. Inferring it
        from today's users would turn a coincidence into a rule — and `platform.models.products`
        drives the catalogue page's product filter."""
        _account(warehouse)
        assert _run(warehouse)[0]["product_key"] == []

    def test_a_discovered_model_is_active(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """Derived, not defaulted: this relation is built from the accounts naming a model TODAY,
        so appearing here IS current use — and `strategy: full_merge` means a retired model stops
        being published, the same statement `ref.enrollment_templates` makes for a template."""
        _account(warehouse)
        assert _run(warehouse)[0]["status"] == "active"


class TestTheTenancy:
    def test_the_firm_axis_is_published(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        _account(warehouse, firm="3102")
        row = _run(warehouse)[0]
        assert row["firm_id"] == "3102"
        assert row["lineage"] == ["3102"]

    def test_the_subfirm_comes_from_the_accounts_that_use_it(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """A model has no tenancy of its own — it is reached through an account."""
        warehouse.execute("DELETE FROM platform.tenancy")
        warehouse.execute(
            "INSERT INTO platform.tenancy VALUES ('A1', '3102', '9001', ['200119'], [])"
        )
        _account(warehouse)
        row = _run(warehouse)[0]
        assert row["key"] == "9001"
        assert set(row["lineage"] or []) == {"3102", "9001", "200119"}

    def test_two_firms_naming_one_id_are_two_rows(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """The honest shape. `unique: external_id` would then FAIL and say so, rather than one
        firm's model quietly carrying the other's name — which is the outcome of grouping by the
        id alone."""
        _account(warehouse, account="A1", firm="3102", model="87984", model_name="Ours")
        _account(warehouse, account="A2", firm="3106", model="87984", model_name="Theirs")
        rows = _run(warehouse)
        assert len(rows) == 2
        assert {r["firm_id"] for r in rows} == {"3102", "3106"}
        assert {r["name"] for r in rows} == {"Ours", "Theirs"}
