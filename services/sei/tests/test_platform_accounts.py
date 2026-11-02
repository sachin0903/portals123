"""`platform.accounts` — executed in DuckDB, with the pension shapes the real drop carries.

**The mapping this pins got 830 SIPPs wrong, and every reader downstream inherited it.**

`product_type` is what every "is this a pension?" reader gates on — drawdown refuses a non-pension
outright, contributions and the MPAA allowance are pension rules, and the client portal groups by
wrapper. SEI files a SIPP as `ACCOUNT_TYPE` **157 Investment Account** and carries the pension
nature in a different code set entirely, so a mapping that reads the wrapper alone labels every one
of them GIA.

Parsed models are not enough here, which is why this executes: the rule is a CASE with an ordering
that matters (classification before wrapper), and the only way to be sure 157-plus-SIPP yields
`SIPP` rather than `GIA` is to run it over rows that look like the drop's.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

MODELS = Path(__file__).resolve().parent.parent / "models" / "platform"

#: Only the columns the model reads. The real table has 152; naming them all here would make this
#: a transcription exercise that goes stale, and a Binder Error names anything missing.
ACCOUNTS = """
    dt VARCHAR,
    "ACCOUNT_NUMBER" VARCHAR, "ACCOUNT_DISPLAY_NAME" VARCHAR, "ACCOUNT_TYPE" VARCHAR,
    "ACCOUNT_STATUS_CODE" VARCHAR, "ACCOUNT_OPEN_DATE" VARCHAR, "ACCOUNT_BASE_CURRENCY" VARCHAR,
    "PORTFOLIO_ID" VARCHAR, "PORTFOLIO_TYPE" VARCHAR, "FIRM_ID" VARCHAR,
    "DATE_LAST_UPDATED" VARCHAR, "COUNTRY_CODE" VARCHAR, "MIFID_CLASSIFICATION" VARCHAR,
    "DORMANT_STATUS_FLAG" VARCHAR, "FROZEN_STATUS_FLAG" VARCHAR, "LAST_TRADE_DATE" VARCHAR,
    "FEE_PACKAGE_ID" VARCHAR, "STRATEGY_ID" VARCHAR, "STRATEGY_NAME" VARCHAR,
    "ACCOUNT_CLASSIFICATION_NAME" VARCHAR, "ACCOUNT_SUB_CLASSIFICATION_NAME" VARCHAR,
    "ACCOUNT_QUALIFIER_NAME" VARCHAR,
    "MODEL_ID" VARCHAR, "IMPLEMENTATION_MODEL_NAME" VARCHAR
"""

#: Only the allowance columns the accounts model reads back off the conformed clients model.
CLIENTS = """
    client_id VARCHAR,
    isa_subscription_used DECIMAL(38,10), isa_headroom_remaining DECIMAL(38,10)
"""

#: Column order for `_account`, so the tuple and the DDL cannot drift apart silently.
_COLUMNS = 24

#: Just the columns the model joins on. `platform.tenancy` is what gives every SEI entity the
#: same tenancy triple, so the model reads it for the org axes and this only has to satisfy those.
TENANCY = """
    dt VARCHAR, account_number VARCHAR, firm_id VARCHAR, subfirm_id VARCHAR,
    dsr_team_ids VARCHAR[], adviser_firm_dsr_ids VARCHAR[], adviser_firm_names VARCHAR[],
    client_ids VARCHAR[]
"""


def _model_sql(name: str) -> str:
    """The model body, with the interlace front-matter comment stripped."""
    sql = (MODELS / f"{name}.sql").read_text()
    return re.sub(r"/\*.*?\*/", "", sql, count=1, flags=re.S).strip()


@pytest.fixture
def warehouse() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    for schema in ("raw", "platform"):
        connection.execute(f"CREATE SCHEMA {schema}")
    connection.execute(f"CREATE TABLE raw.accounts ({ACCOUNTS})")
    connection.execute(
        "CREATE TABLE raw.api_allowances ("
        " firm_id VARCHAR, account_number VARCHAR, tax_year VARCHAR,"
        " isa_year_to_date DECIMAL(20, 4), isa_remaining DECIMAL(20, 4),"
        " jisa_year_to_date DECIMAL(20, 4), jisa_remaining DECIMAL(20, 4))"
    )
    connection.execute(f"CREATE TABLE platform.tenancy ({TENANCY})")
    connection.execute(f"CREATE TABLE platform.clients ({CLIENTS})")
    connection.execute(
        "CREATE TABLE platform.bank_accounts ("
        " account_external_id VARCHAR, profile_id VARCHAR,"
        " is_ad_hoc BOOLEAN, entry JSON)"
    )
    return connection


def _account(
    number: str,
    account_type: str,
    *,
    classification: str | None = None,
    sub_classification: str | None = None,
    qualifier: str | None = None,
    strategy_id: str | None = None,
    model_id: str | None = None,
    model_name: str | None = None,
) -> tuple[object, ...]:
    return (
        "2026-09-01",  # dt
        number,
        f"Account {number}",
        account_type,
        "3",  # ACCOUNT_STATUS_CODE: 3 Open
        "2020-01-01",
        "GBP",
        "58251749",
        "189",
        "3102",
        "2026-09-01",
        "GB",
        "1",
        "N",
        "N",
        "2026-08-29",
        None,  # FEE_PACKAGE_ID
        strategy_id,
        None,  # STRATEGY_NAME
        classification,
        sub_classification,
        qualifier,
        model_id,
        model_name,
    )


def _load(connection: duckdb.DuckDBPyConnection, rows: list[tuple[object, ...]]) -> None:
    # noqa justified: the f-string builds the PLACEHOLDER list (`?, ?, …`) and nothing else —
    # every value is bound. Written this way so the column count comes from one constant rather
    # than a literal that drifts from the DDL above it.
    placeholders = ", ".join(["?"] * _COLUMNS)
    connection.executemany(f"INSERT INTO raw.accounts VALUES ({placeholders})", rows)  # noqa: S608
    connection.executemany(
        "INSERT INTO platform.tenancy VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [("2026-09-01", row[1], "3102", None, [], [], [], ["C1"]) for row in rows],
    )
    connection.execute(f"CREATE OR REPLACE TABLE platform.accounts AS {_model_sql('accounts')}")


def _allowance(
    connection: duckdb.DuckDBPyConnection, *, used: float | None, remaining: float | None
) -> None:
    """What SEI states about the lead client's ISA allowance. Per CLIENT, which is the point."""
    connection.execute("INSERT INTO platform.clients VALUES ('C1', ?, ?)", [used, remaining])


def _products(connection: duckdb.DuckDBPyConnection) -> dict[str, str | None]:
    return {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT account_number, product_key FROM platform.accounts"
        ).fetchall()
    }


def test_the_model_binds(warehouse: duckdb.DuckDBPyConnection) -> None:
    """A Binder Error here is a deploy failure — it aborts the whole provider's promote."""
    _load(warehouse, [_account("A1", "157")])


def test_the_account_terms_are_null_because_sei_does_not_publish_them(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """The shared book-sink accounts projection reads `fees`/`payments`/`withdrawals` from every
    provider. SEI's drop carries none of those schedules, and an omitted column is a Binder Error
    that aborts the tenant. Typed NULLs, not empty lists: empty would read as "charged nothing"."""
    _load(warehouse, [_account("A1", "157")])
    fees, payments, withdrawals = warehouse.execute(
        "SELECT fees, payments, withdrawals FROM platform.accounts"
    ).fetchone()
    assert fees is None
    assert payments is None
    assert withdrawals is None


class TestTheWrapperMapping:
    def test_the_four_wrapper_codes_map_as_before(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """157 / 172 / 173, and 176 House Account deliberately unmapped — it is the firm's own
        account, not a client product, and `platform.product_type` has no member for one."""
        _load(
            warehouse,
            [
                _account("GIA1", "157"),
                _account("ISA1", "172"),
                _account("JISA1", "173"),
                _account("HOUSE1", "176"),
            ],
        )
        assert _products(warehouse) == {
            "GIA1": "GIA",
            "ISA1": "ISA",
            "JISA1": "JISA",
            "HOUSE1": None,
        }


class TestASippIsNotAWrapperCode:
    """The regression this file exists for.

    On the i31 drop: 934 accounts classified `Pension`, 830 sub-classified
    `Pension - HMRC Approved - SIPP`, 14 carrying the `SIPP` qualifier — and **every one of them
    is `ACCOUNT_TYPE` 157**. Mapping on the wrapper alone made all 934 GIAs.
    """

    def test_a_sipp_on_a_157_wrapper_is_a_SIPP_not_a_GIA(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _load(
            warehouse,
            [
                _account(
                    "SIPP1",
                    "157",
                    classification="Pension",
                    sub_classification="Pension - HMRC Approved - SIPP",
                )
            ],
        )
        assert _products(warehouse)["SIPP1"] == "SIPP", (
            "a SIPP filed as a GIA cannot be drawn down, cannot take a pension contribution and "
            "is tested against the wrong annual allowance"
        )

    def test_the_qualifier_alone_is_enough(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """14 accounts carry `ACCOUNT_QUALIFIER_NAME = 'SIPP'`. Read as a second signal rather
        than requiring both, because SEI populates them independently."""
        _load(warehouse, [_account("SIPP2", "157", classification="Pension", qualifier="SIPP")])
        assert _products(warehouse)["SIPP2"] == "SIPP"

    def test_an_SSAS_is_unmapped_rather_than_called_a_GIA(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """`platform.product_type` has no SSAS member, and a small-self-administered scheme is
        not a SIPP. NULL for the reason House Account is NULL: it shows up as `unmodelled
        wrapper` in the merge ledger, where filing it as a GIA would be a quiet lie."""
        _load(
            warehouse,
            [
                _account(
                    "SSAS1",
                    "157",
                    classification="Pension",
                    sub_classification="Pension - HMRC Approved - SSAS",
                )
            ],
        )
        assert _products(warehouse)["SSAS1"] is None

    def test_a_bond_inside_a_pension_keeps_the_wrapper_map(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """The 100 pension-classified accounts that are NOT SIPPs (Offshore Bond, Other) are
        deliberately left alone.

        A bond held inside a pension is not a SIPP, and the drop gives nothing to choose between
        `TPOFFBOND` and `SIPP` on a description — so the rule reads only an explicit SIPP and
        guesses at nothing. Pinned so a later "tidy-up" that maps all Pension → SIPP has to argue
        with a test rather than with a comment.
        """
        _load(
            warehouse,
            [
                _account(
                    "BOND1",
                    "157",
                    classification="Pension",
                    sub_classification="Offshore Bond",
                )
            ],
        )
        assert _products(warehouse)["BOND1"] == "GIA"

    def test_an_ordinary_gia_is_untouched(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """The 6,000 accounts that really are GIAs. The classification columns are NULL on
        15,978 of the drop's rows, so the CASE has to fall through cleanly."""
        _load(warehouse, [_account("GIA2", "157")])
        assert _products(warehouse)["GIA2"] == "GIA"


class TestTheModelKey:
    """`model_key` is `MODEL_ID`, and reading `STRATEGY_ID` made it NULL for the whole SEI book.

    `pkgs/book-sink` resolves `platform.accounts.model_id` through a `link:model` parent on
    `model_key`. This mapped to `STRATEGY_ID`, which is **0% populated across all 74 drops of the
    p04 corpus** while `MODEL_ID` is 85% with 49 distinct values — so the parent never resolved, no
    SEI account carried a model, and `GET /crm/accounts`' `discretionary` flag (which is "carries a
    model_id") was false for every one of them.

    Executed over rows rather than parsed, because the failure is a value, not a shape: a projection
    reading an always-empty column binds perfectly.
    """

    def _keys(
        self, connection: duckdb.DuckDBPyConnection
    ) -> dict[str, tuple[str | None, str | None, str | None]]:
        return {
            row[0]: (row[1], row[2], row[3])
            for row in connection.execute(
                "SELECT account_number, model_key, model_name, strategy_id  FROM platform.accounts"
            ).fetchall()
        }

    def test_the_model_id_is_the_model_key(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        _load(
            warehouse,
            [_account("A1", "157", model_id="83011", model_name="Bespoke Risk 5")],
        )
        assert self._keys(warehouse)["A1"][:2] == ("83011", "Bespoke Risk 5")

    def test_an_empty_strategy_id_does_not_become_the_model(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """The shape the whole corpus actually has: a populated MODEL_ID beside a blank STRATEGY_ID.

        Reading STRATEGY_ID here returns NULL and the account silently has no model.
        """
        _load(warehouse, [_account("A1", "172", model_id="83012", strategy_id=None)])
        model_key, _, strategy_id = self._keys(warehouse)["A1"]
        assert model_key == "83012"
        assert strategy_id is None

    def test_strategy_id_is_still_published(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """It stays in the conformed contract: lake-side readers select it, and the day SEI starts
        populating it we want to see that rather than have it hidden."""
        _load(warehouse, [_account("A1", "157", strategy_id="S9", model_id="83013")])
        model_key, _, strategy_id = self._keys(warehouse)["A1"]
        assert (model_key, strategy_id) == ("83013", "S9")


class TestTheWrapperAllowance:
    """SEI states the ISA allowance per CLIENT, and it is carried onto each account of the wrapper.

    `Wrapper.allowanceUsed` / `allowanceLimit` are rendered by `wrapper-card.svelte` and
    `account-stack.svelte` and were fed only by `apps/client/src/lib/demo.ts`, while SEI had stated
    the figures all along — on 1,614 of 2,774 client rows.
    """

    def _allowances(
        self, connection: duckdb.DuckDBPyConnection
    ) -> dict[str, tuple[float | None, float | None, float | None]]:
        return {
            row[0]: (row[1], row[2], row[3])
            for row in connection.execute(
                "SELECT account_number, allowance_used, allowance_limit, "
                "allowance_remaining FROM platform.accounts"
            ).fetchall()
        }

    def test_the_limit_is_used_plus_remaining(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """20,000 for 1,124 of the drop's clients — the ISA annual allowance, arrived at from SEI's
        own two figures rather than hard-coded."""
        _allowance(warehouse, used=5000.0, remaining=15000.0)
        _load(warehouse, [_account("ISA1", "172")])
        used, limit, remaining = self._allowances(warehouse)["ISA1"]
        assert (float(used), float(limit), float(remaining)) == (5000.0, 20000.0, 15000.0)

    def test_a_flexible_isa_exceeds_the_statutory_limit_and_must_not_be_clamped(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """THE REASON 20,000 is not hard-coded. used + remaining is 26,000 for 28 clients in the
        drop and 22,000 for 20 more, because a flexible ISA lets a withdrawal be replaced on top of
        the annual limit. Clamping to 20,000 would tell those clients they had over-subscribed."""
        _allowance(warehouse, used=6000.0, remaining=20000.0)
        _load(warehouse, [_account("ISA1", "172")])
        _used, limit, remaining = self._allowances(warehouse)["ISA1"]
        assert float(limit) == 26000.0
        assert float(remaining) == 20000.0

    def test_a_jisa_gets_its_own_smaller_allowance(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """9,000 for 82 clients. Same arithmetic, no wrapper-specific constant."""
        _allowance(warehouse, used=1000.0, remaining=8000.0)
        _load(warehouse, [_account("JISA1", "173")])
        _used, limit, remaining = self._allowances(warehouse)["JISA1"]
        assert float(limit) == 9000.0
        assert float(remaining) == 8000.0

    def test_a_gia_has_no_allowance(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """A bar on a General Investment Account would invent a constraint that does not exist."""
        _allowance(warehouse, used=5000.0, remaining=15000.0)
        _load(warehouse, [_account("GIA1", "157")])
        assert self._allowances(warehouse)["GIA1"] == (None, None, None)

    def test_a_sipp_is_not_given_an_isas_headroom(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """Gated on the RESOLVED product, not the wrapper code. SEI files a SIPP as ACCOUNT_TYPE 157
        with the pension nature in the classification, so gating on the code alone would be right
        here by accident and wrong the moment the mapping changed."""
        _allowance(warehouse, used=5000.0, remaining=15000.0)
        _load(
            warehouse,
            [
                _account(
                    "SIPP1",
                    "157",
                    classification="Pension",
                    sub_classification="Pension - HMRC Approved - SIPP",
                )
            ],
        )
        assert self._allowances(warehouse)["SIPP1"] == (None, None, None)

    def test_a_house_account_has_no_allowance(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """176 resolves to no product at all — it is the firm's own account."""
        _allowance(warehouse, used=5000.0, remaining=15000.0)
        _load(warehouse, [_account("HOUSE1", "176")])
        assert self._allowances(warehouse)["HOUSE1"] == (None, None, None)

    def test_a_client_stating_neither_figure_gets_no_limit(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """1,160 of 2,774 client rows state neither. NULL, not zero — "SEI has not told us" is not
        "this client has no allowance left", and a zero-limit bar reads as fully subscribed."""
        _allowance(warehouse, used=None, remaining=None)
        _load(warehouse, [_account("ISA1", "172")])
        assert self._allowances(warehouse)["ISA1"] == (None, None, None)

    def test_half_a_figure_yields_no_limit(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """A used figure with no remaining one cannot make a limit, and `used + NULL` is NULL
        anyway — asserted so a later coalesce cannot quietly turn it into "you have used all of
        it"."""
        _allowance(warehouse, used=5000.0, remaining=None)
        _load(warehouse, [_account("ISA1", "172")])
        used, limit, remaining = self._allowances(warehouse)["ISA1"]
        assert float(used) == 5000.0
        assert limit is None
        assert remaining is None

    def test_two_isas_on_one_client_both_carry_the_same_figure(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """The reason every reader must take max() and never sum(). 60 of 921 client x wrapper
        pairs in the drop hold more than one account; summing would report double the subscription
        and double the limit."""
        _allowance(warehouse, used=5000.0, remaining=15000.0)
        _load(warehouse, [_account("ISA1", "172"), _account("ISA2", "172")])
        got = self._allowances(warehouse)
        assert (float(got["ISA1"][0]), float(got["ISA1"][1])) == (5000.0, 20000.0)
        assert got["ISA1"] == got["ISA2"], "the same client's allowance, on both accounts"

    def test_the_account_details_read_replaces_the_file_and_states_the_year(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """A zero year-to-date is a figure. Falling through to the file's £5,000 would
        report last year's subscription against this year's headroom."""
        _allowance(warehouse, used=5000.0, remaining=15000.0)
        warehouse.execute(
            "INSERT INTO raw.api_allowances VALUES ('3102', 'ISA1', '2026-2027', 0, 20000, 0, 9000)"
        )
        _load(warehouse, [_account("ISA1", "172")])
        used, limit, remaining = self._allowances(warehouse)["ISA1"]
        year = warehouse.execute(
            "SELECT allowance_tax_year FROM platform.accounts WHERE account_number = 'ISA1'"
        ).fetchone()
        assert (float(used), float(limit), float(remaining)) == (0.0, 20000.0, 20000.0)
        assert year is not None and year[0] == "2026-2027"

    def test_a_flexible_isa_limit_is_used_plus_remaining_not_the_statutory_field(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """The API also states a 20,000 contribution limit. That is the statutory cap.
        The replacement room on a flexible ISA sits in the remaining figure, and the
        limit the book stores is the sum — the same rule as the file."""
        warehouse.execute(
            "INSERT INTO raw.api_allowances VALUES "
            "('3102', 'ISA1', '2026-2027', 6000, 20000, NULL, NULL)"
        )
        _load(warehouse, [_account("ISA1", "172")])
        used, limit, remaining = self._allowances(warehouse)["ISA1"]
        assert (float(used), float(limit), float(remaining)) == (6000.0, 26000.0, 20000.0)

    def test_a_jisa_reads_the_jisa_side_of_a_block_that_also_states_the_isa(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """Account details returns both wrappers' client-level figures on every account.
        A JISA must not be given the ISA's 20,000."""
        warehouse.execute(
            "INSERT INTO raw.api_allowances VALUES "
            "('3102', 'JISA1', '2026-2027', 0, 20000, 1000, 8000)"
        )
        _load(warehouse, [_account("JISA1", "173")])
        used, limit, remaining = self._allowances(warehouse)["JISA1"]
        assert (float(used), float(limit), float(remaining)) == (1000.0, 9000.0, 8000.0)

    def test_a_sipp_is_not_given_an_isa_block_that_happens_to_be_joined(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        warehouse.execute(
            "INSERT INTO raw.api_allowances VALUES "
            "('3102', 'SIPP1', '2026-2027', 0, 20000, 0, 9000)"
        )
        _load(
            warehouse,
            [
                _account(
                    "SIPP1",
                    "157",
                    classification="Pension",
                    sub_classification="Pension - HMRC Approved - SIPP",
                )
            ],
        )
        assert self._allowances(warehouse)["SIPP1"] == (None, None, None)

    def test_a_partial_api_block_does_not_borrow_the_file(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """Used from the API and remaining from the file would be two years added together."""
        _allowance(warehouse, used=5000.0, remaining=15000.0)
        warehouse.execute(
            "INSERT INTO raw.api_allowances VALUES "
            "('3102', 'ISA1', '2026-2027', 0, NULL, NULL, NULL)"
        )
        _load(warehouse, [_account("ISA1", "172")])
        used, limit, remaining = self._allowances(warehouse)["ISA1"]
        assert float(used) == 0.0
        assert limit is None
        assert remaining is None
        year = warehouse.execute(
            "SELECT allowance_tax_year FROM platform.accounts WHERE account_number = 'ISA1'"
        ).fetchone()
        assert year is not None and year[0] is None


def test_pay_to_profiles_land_on_the_account_without_multiplying_it(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """Two profiles on one account are one array. A second account is a second row.
    An ad hoc profile is not a standing destination."""
    warehouse.execute(
        "INSERT INTO platform.bank_accounts VALUES "
        '(\'A1\', \'P2\', false, \'{"direction":"pay_to","provider":"sei",'
        '"provider_ref":"P2","method":"chaps"}\'::JSON), '
        '(\'A1\', \'P1\', false, \'{"direction":"pay_to","provider":"sei",'
        '"provider_ref":"P1","method":"bacs","sort_code":"11-22-33"}\'::JSON), '
        '(\'A1\', \'ADHOC\', true, \'{"direction":"pay_to","provider":"sei",'
        '"provider_ref":"ADHOC"}\'::JSON)'
    )
    _load(warehouse, [_account("A1", "157"), _account("A2", "157")])
    rows = warehouse.execute(
        "SELECT account_number, bank_accounts::VARCHAR FROM platform.accounts "
        "ORDER BY account_number"
    ).fetchall()
    assert [row[0] for row in rows] == ["A1", "A2"]
    assert rows[1][1] is None
    entries = json.loads(rows[0][1])
    assert [entry["provider_ref"] for entry in entries] == ["P1", "P2"]
    assert entries[0]["sort_code"] == "11-22-33"
    assert "ADHOC" not in rows[0][1]
