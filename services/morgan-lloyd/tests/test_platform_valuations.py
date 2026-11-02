"""`platform.valuations` EXECUTED, not just parsed — over real rows.

`test_book_contract.py` reads every ML model with sqlglot and explains why: `raw.contacts` is 457
columns off the live OData entity, so a stub warehouse for it would be a fixture nobody could keep
true. That reasoning does not carry to THIS model. `raw.scheme_detail` is `cam_getscheme`'s eight
documented fields plus the spine, so it is entirely stubbable — and this model, unlike `clients`,
does arithmetic:

  * `coalesce(crystallised, 0) + coalesce(uncrystallised, 0)`, which is there because NULL + x is
    NULL and a half-populated scheme would otherwise publish no market_value and be rejected by
    the sink as "no value or date";
  * `coalesce(calculated_on, current_date)`, so a scheme the administrator has not costed still
    files under a date rather than under NULL;
  * a WHERE that drops a scheme the action answered about with no figures at all.

None of that is visible to a parse, and the estate has been bitten twice by exactly the gap
(CLAUDE.md: a two-argument lambda does not survive sqlglot; an empty fixture table cannot catch a
value-dependent failure, because binding is not evaluating). So this executes over ROWS.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

MODELS = Path(__file__).resolve().parent.parent / "models" / "platform"

#: `raw.scheme_detail`'s landed shape. Everything arrives as text off the OData transport and is
#: snake-cased by `ml_sync.records.sanitise_key`, which is why the model `try_cast`s rather than
#: trusting a type — and why a stub of VARCHARs is the honest fixture rather than a convenient one.
_RAW = (
    "scheme_reference VARCHAR, ml_tenant_id VARCHAR, scheme_id VARCHAR,"
    " crystallised_fund_amount VARCHAR, uncrystallised_fund_amount VARCHAR,"
    " total_bank_balance VARCHAR, ongoing_adviser_fee_percentage VARCHAR,"
    " investment_costs_percentage VARCHAR, platform_fees_percentage VARCHAR,"
    " funds_last_calculated_date VARCHAR"
)
_TENANT = "02492031-0000-0000-0000-000000000000"


def _model_sql() -> str:
    sql = (MODELS / "valuations.sql").read_text()
    return re.sub(r"/\*.*?\*/", "", sql, count=1, flags=re.S).strip()


@pytest.fixture
def warehouse() -> object:
    connection = duckdb.connect(":memory:")
    for schema in ("raw", "platform"):
        connection.execute(f"CREATE SCHEMA {schema}")
    connection.execute(f"CREATE TABLE raw.scheme_detail ({_RAW})")
    # NO node dimension, deliberately: Morgan Lloyd publishes none and never has. Creating one
    # here is what hid a real defect for as long as it existed -- the model declared
    # `platform.nodes` in `depends_on` and joined it, so it passed against a table only the test
    # made and failed every real `interlace apply` with "schema platform does not exist", taking
    # the whole tenant chain (and therefore the merge) with it.
    return connection


def _scheme(
    connection: object,
    reference: str = "MLSIPP/01417",
    *,
    crystallised: str | None = "12000.00",
    uncrystallised: str | None = "88000.50",
    bank: str | None = "1500.25",
    calculated: str | None = "2026-09-03",
) -> None:
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO raw.scheme_detail (scheme_reference, ml_tenant_id,"
        " crystallised_fund_amount, uncrystallised_fund_amount, total_bank_balance,"
        " funds_last_calculated_date) VALUES (?, ?, ?, ?, ?, ?)",
        [reference, _TENANT, crystallised, uncrystallised, bank, calculated],
    )


def _rows(connection: object) -> list[dict[str, object]]:
    result = connection.execute(_model_sql())  # type: ignore[attr-defined]
    names = [d[0] for d in result.description]
    return [dict(zip(names, row, strict=True)) for row in result.fetchall()]


def _columns(connection: object) -> dict[str, str]:
    connection.execute(f"CREATE OR REPLACE TEMP VIEW v AS {_model_sql()}")  # type: ignore[attr-defined]
    return {r[0]: r[1] for r in connection.execute("DESCRIBE v").fetchall()}  # type: ignore[attr-defined]


class TestItBindsAndPublishesTheContract:
    def test_it_binds_against_the_landed_shape(self, warehouse: object) -> None:
        """The failure this catches is the one that aborts a whole tenant: a column the model
        reads and the extractor does not land is a Binder Error the ledger records as
        'unavailable', indistinguishable from 'this firm has no data with this provider'."""
        assert _columns(warehouse)

    def test_it_publishes_the_conformed_spine(self, warehouse: object) -> None:
        assert {"external_id", "key", "lineage", "as_of_date", "data"} <= set(_columns(warehouse))

    def test_money_is_decimal_not_double(self, warehouse: object) -> None:
        """A float is the wrong type for money and the merge casts to the TARGET's type, so a
        DOUBLE here would round on the way in rather than failing visibly."""
        columns = _columns(warehouse)
        for name in (
            "market_value",
            "cash_value",
            "crystallised_value",
            "uncrystallised_value",
        ):
            assert columns[name].startswith("DECIMAL"), f"{name} is {columns[name]}"

    def test_lineage_is_a_list_of_strings(self, warehouse: object) -> None:
        """A bare NULL types as INTEGER, which is how a join to a node dimension silently never
        matches — the exact bug the Seccl models' own test records."""
        assert _columns(warehouse)["lineage"] == "VARCHAR[]"

    def test_lineage_is_the_microsoft_tenant_and_needs_no_dimension(
        self, warehouse: object
    ) -> None:
        """ML's org axis is the single Microsoft tenant id stamped on every record, so the
        one-element path is stated outright — the same shape `clients`, `transfers` and
        `beneficiaries` publish. It is NOT looked up: this model declared `platform.nodes` in
        `depends_on` and joined it for a `coalesce` whose fallback was this very value, against a
        relation Morgan Lloyd has never published."""
        _scheme(warehouse)
        assert _rows(warehouse)[0]["lineage"] == [_TENANT]

    def test_it_depends_only_on_relations_this_project_publishes(self) -> None:
        """The tripwire for the above, and the one that generalises. A `depends_on` naming a model
        that does not exist is not a no-op: `interlace apply` fails the model outright, and with it
        every staging model below it and the merge ledger that depends on those — so a whole firm's
        book stops arriving and the symptom is an empty book, not an error anyone sees."""
        published = {
            f"{sql.parent.name}.{sql.stem}"
            for sql in (MODELS.parent).rglob("*.sql")
            if sql.parent.name != "models"
        }
        declared = re.search(r"depends_on:\s*\[([^\]]*)\]", (MODELS / "valuations.sql").read_text())
        assert declared is not None
        for name in (n.strip() for n in declared.group(1).split(",") if n.strip()):
            if name.startswith("raw."):
                continue  # raw.* are Python models, not .sql files
            assert name in published, f"{name} is declared but no model publishes it"

    def test_the_account_is_addressed_by_the_scheme_reference(self, warehouse: object) -> None:
        """`account_external_id` is what the sink resolves through
        `platform.links(link_type='primary')`, which holds the `MLSIPP/…` reference minted by
        `sync_client`. Not the policy number — that is the `policy` link, and only a valuation
        PUSH is addressed by it. Sending one where the other belongs is the `cam_schemerefs`
        mistake, refused with a message that reads as 'not found'."""
        _scheme(warehouse)
        row = _rows(warehouse)[0]
        assert row["account_external_id"] == "MLSIPP/01417"
        assert row["external_id"] == "MLSIPP/01417"


class TestTheArithmetic:
    def test_the_fund_total_is_the_two_halves(self, warehouse: object) -> None:
        _scheme(warehouse)
        assert float(_rows(warehouse)[0]["market_value"]) == pytest.approx(100000.50)

    def test_a_half_populated_scheme_still_publishes_a_value(self, warehouse: object) -> None:
        """THE REASON THE COALESCE IS THERE. A scheme with nothing crystallised publishes a NULL
        rather than a zero, and `NULL + 88000` is NULL — which the sink's own Requirement rejects
        as 'no value or date', so the whole scheme would vanish from the book with the ledger
        blaming missing data. Invisible to a parse; this is why the model is executed."""
        _scheme(warehouse, crystallised=None)
        row = _rows(warehouse)[0]
        assert float(row["market_value"]) == pytest.approx(88000.50)
        # The half that IS null stays null — 0 would state the fund is wholly uncrystallised.
        assert row["crystallised_value"] is None

    def test_a_scheme_with_no_figures_at_all_is_dropped(self, warehouse: object) -> None:
        """ "The action answered without figures" is not "the fund is worth zero". A row of zeros
        would read as a real valuation of an empty pension."""
        _scheme(warehouse, crystallised=None, uncrystallised=None)
        assert _rows(warehouse) == []

    def test_the_split_is_published_as_the_administrator_computes_it(
        self, warehouse: object
    ) -> None:
        """0117's whole point: no custodian in the estate models crystallisation, so this is the
        only source for the fact drawdown eligibility turns on."""
        _scheme(warehouse)
        row = _rows(warehouse)[0]
        assert float(row["crystallised_value"]) == pytest.approx(12000.00)
        assert float(row["uncrystallised_value"]) == pytest.approx(88000.50)


class TestTheBankBalance:
    def test_it_is_published_as_cash_at_this_source_system(self, warehouse: object) -> None:
        """THE NEW FACT. Every cash figure in the portals is derived from custodian POSITIONS, so
        the scheme's own bank account at the administrator is money the client holds and cannot
        see anywhere today."""
        _scheme(warehouse)
        assert float(_rows(warehouse)[0]["cash_value"]) == pytest.approx(1500.25)

    def test_a_negative_balance_is_published_as_negative(self, warehouse: object) -> None:
        """The vendor states it can be negative. Never clamped: an overdrawn scheme is something
        an adviser has to see, and a floor at zero would hide it."""
        _scheme(warehouse, bank="-250.00")
        assert float(_rows(warehouse)[0]["cash_value"]) == pytest.approx(-250.00)

    def test_no_balance_is_null_rather_than_zero(self, warehouse: object) -> None:
        _scheme(warehouse, bank=None)
        assert _rows(warehouse)[0]["cash_value"] is None


class TestTheDate:
    def test_it_files_under_the_administrators_own_calculation_date(
        self, warehouse: object
    ) -> None:
        """The series is what makes this a valuation rather than a snapshot, and the date has to
        be the one the figure was calculated on — not the day we happened to read it."""
        _scheme(warehouse, calculated="2026-09-03")
        assert str(_rows(warehouse)[0]["as_of_date"]) == "2026-09-03"

    def test_an_uncosted_scheme_falls_back_to_today(self, warehouse: object) -> None:
        """`as_of_date` is half the merge key, so a NULL would fail the sink's Requirement and
        drop the scheme. Today is the honest fallback: it is when the figure was asserted."""
        import datetime as dt

        _scheme(warehouse, calculated=None)
        assert _rows(warehouse)[0]["as_of_date"] == dt.date.today()

    def test_an_unparseable_date_does_not_fail_the_whole_model(self, warehouse: object) -> None:
        """`try_cast`, not `cast`: the transport is untyped text, and one malformed date must not
        take every other scheme in the firm down with it."""
        _scheme(warehouse, calculated="not-a-date")
        assert len(_rows(warehouse)) == 1

    def test_two_dates_for_one_scheme_are_two_rows(self, warehouse: object) -> None:
        """`merge` on (external_id, as_of_date) — `cam_getscheme` returns the CURRENT position
        only, so the series accumulates one row per run and a `replace` would destroy it."""
        _scheme(warehouse, calculated="2026-09-02")
        _scheme(warehouse, calculated="2026-09-03")
        assert {str(r["as_of_date"]) for r in _rows(warehouse)} == {"2026-09-02", "2026-09-03"}


class TestTheDoubleCountTrap:
    """0117's standing warning, asserted where it can be seen rather than only written down."""

    def test_the_fund_total_is_our_own_pushed_figure(self, warehouse: object) -> None:
        """For a SIPP, Morgan Lloyd is the ADMINISTRATOR and not the custodian: we push the
        valuation (`cam_createpolicyvaluation`) from the custodian's holdings, so
        `crystallised + uncrystallised` is the number we last sent them.

        The consequence is a rule for every reader: take a VALUE from one source system, take
        CASH from all of them. Summing `market_value` across source systems double-counts the
        investments; summing `cash_value` is exactly right and is the point of this model.

        Asserted here as the identity it is — the model must not add the bank balance INTO
        market_value, which would make the two figures overlap and the cash unsummable."""
        _scheme(warehouse, crystallised="12000.00", uncrystallised="88000.50", bank="1500.25")
        row = _rows(warehouse)[0]
        total = float(row["crystallised_value"]) + float(row["uncrystallised_value"])
        assert float(row["market_value"]) == pytest.approx(total)
        assert float(row["market_value"]) != pytest.approx(total + 1500.25)

    def test_the_source_system_is_stamped_by_the_sink_not_the_model(
        self, warehouse: object
    ) -> None:
        """`source_system` is `'{provider}'` in the sink's projection, which is what keeps one
        account's ML row and its custodian row apart under the (account, date, source) key. A
        model publishing its own would be a second place for it to be wrong."""
        assert "source_system" not in _columns(warehouse)
