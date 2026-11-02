"""The dev-extract fallback: the shape it publishes, and the two rules that bound it.

The rules matter more than the rows. A fallback that quietly reached the withdrawal gate would put
back exactly the hazard `app/lake/holdings.py` was written to remove, and one that stayed on in
production would show a real client somebody else's anonymised numbers.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.config import settings
from app.lake import fixture as lake_fixture
from app.lake import holdings as lake_holdings

# Real Seccl ids from the committed extract — the same ones `platform.links` carries for the
# Graphene tenant's accounts, which is the only reason the fallback resolves anything at all.
ACCOUNT = "AXB1D16C3"


@pytest.fixture(autouse=True)
def _dev_stage(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "app_stage", "dev", raising=False)
    monkeypatch.delenv("LAKE_FIXTURE_DISABLED", raising=False)


def _has_extract() -> bool:
    return lake_fixture.path() is not None


requires_extract = pytest.mark.skipif(not _has_extract(), reason="docs/seccl.duckdb absent")


def test_production_never_reads_the_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_stage", "production", raising=False)
    assert lake_fixture.path() is None
    assert not lake_fixture.available()


def test_stage_is_matched_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A misspelt stage lands on the guarded side of nothing — it is 'production' or it is not."""
    monkeypatch.setattr(settings, "app_stage", "Production", raising=False)
    assert lake_fixture.available() == _has_extract()


def test_an_explicit_opt_out_disables_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAKE_FIXTURE_DISABLED", "1")
    assert lake_fixture.path() is None


@pytest.mark.asyncio
async def test_no_ids_reads_nothing() -> None:
    assert await lake_fixture.holdings([]) == []
    assert await lake_fixture.transactions([]) == []
    assert await lake_fixture.value_series([]) == []


@requires_extract
@pytest.mark.asyncio
async def test_holdings_publish_the_conformed_columns() -> None:
    rows = await lake_fixture.holdings([ACCOUNT])
    assert rows, "the extract holds this account"
    # The column set `crm.py` and the portal's holdings view consume, named as `_HOLDINGS_SQL` in
    # holdings.py names them. A rename on either side must fail here rather than on a blank column.
    expected = {
        "account_external_id",
        "asset_id",
        "asset_name",
        "isin",
        "asset_type",
        "value",
        "quantity",
        "price",
        "book_cost",
        "growth",
        "growth_pct",
        "allocation",
        "as_of",
    }
    assert set(rows[0]) == expected
    assert {row["account_external_id"] for row in rows} == {ACCOUNT}


@requires_extract
@pytest.mark.asyncio
async def test_cash_is_a_position_and_allocations_total_a_hundred() -> None:
    """Cash comes from its own table. Without it the donut sums the invested assets alone and the
    account's Cash figure reads zero beside a real balance — wrong, and silently so."""
    rows = await lake_fixture.holdings([ACCOUNT])
    assert any(row["asset_type"] == "Cash" for row in rows)
    assert sum(row["allocation"] for row in rows) == pytest.approx(100.0, abs=0.01)


@requires_extract
@pytest.mark.asyncio
async def test_transactions_are_newest_first_and_capped() -> None:
    rows = await lake_fixture.transactions([ACCOUNT], limit=5)
    assert 0 < len(rows) <= 5
    dates = [row["date"] for row in rows if row["date"]]
    assert dates == sorted(dates, reverse=True)


@requires_extract
@pytest.mark.asyncio
async def test_the_value_series_is_a_dated_ascending_run() -> None:
    rows = await lake_fixture.value_series([ACCOUNT])
    assert len(rows) > 1
    dates = [row["date"] for row in rows]
    assert dates == sorted(dates)


@requires_extract
@pytest.mark.asyncio
async def test_an_unknown_account_reads_nothing_rather_than_everything() -> None:
    """The extract carries no tenancy, so the id list IS the whole of the narrowing."""
    assert await lake_fixture.holdings(["NOT-AN-ACCOUNT"]) == []


def test_the_extract_covers_only_its_own_custodian() -> None:
    """A SEI outage must not be papered over with Seccl numbers. `_firm_read` asks this rather
    than naming a custodian itself — that would fail the display-path tripwire."""
    assert lake_fixture.covers("seccl")
    assert not lake_fixture.covers("sei")
    assert not lake_fixture.covers("morgan-lloyd")
    assert not lake_fixture.covers("")
    for conformed in (
        lake_holdings.firm_holdings,
        lake_holdings.firm_holdings_summary,
        lake_holdings.firm_transactions,
        lake_holdings.firm_value_series,
        lake_holdings.search_firm_holdings,
    ):
        assert lake_fixture.reader_for(conformed) is not None
    assert lake_fixture.reader_for(lake_holdings.by_firm) is None


def test_extract_search_matches_name_or_isin_and_drops_a_negative_leg() -> None:
    """Spotlight's fallback uses the same two fields and the same quantity rule as the warehouse."""
    rows = [
        {
            "asset_id": "a",
            "asset_name": "Global Equity",
            "isin": "GB00AAAA0001",
            "value": 10,
            "quantity": 1,
        },
        {
            "asset_id": "b",
            "asset_name": "UK Gilts",
            "isin": "GB00BBBB0002",
            "value": 40,
            "quantity": 2,
        },
        {
            "asset_id": "c",
            "asset_name": "Global Equity",
            "isin": "GB00CCCC0003",
            "value": 5,
            "quantity": -1,
        },
        {"asset_id": "d", "asset_name": "Cash", "isin": None, "value": 3, "quantity": None},
    ]
    assert [row["asset_id"] for row in lake_fixture.matching_holdings(rows, "global", 6)] == ["a"]
    assert [row["asset_id"] for row in lake_fixture.matching_holdings(rows, "bbbb", 6)] == ["b"]
    assert [row["asset_id"] for row in lake_fixture.matching_holdings(rows, "cash", 6)] == ["d"]
    # Value order, then the cap. The negative twin of Global Equity stays out.
    capped = lake_fixture.matching_holdings(rows, "g", 1)
    assert [row["asset_id"] for row in capped] == ["b"]


def test_the_withdrawal_gate_never_reaches_the_extract() -> None:
    """THE RULE. `instructions.py` calls the conformed reader directly, so an unreadable warehouse
    still REFUSES a withdrawal. Asserted on the source, because the failure this prevents — a gate
    permitting money out against somebody else's anonymised positions — leaves no trace at run time.
    """
    source = Path(inspect.getfile(lake_holdings)).parent.parent / "instructions.py"
    tree = ast.parse(source.read_text())
    # The module names the old fixture repeatedly in PROSE — it exists to explain why it stopped
    # reading one — so this asks the syntax, not the text.
    imported = {
        alias.name.split(".")[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom | ast.Import)
        for alias in node.names
    }
    assert "fixture" not in imported
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)} | {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
    assert not {"lake_fixture", "reader_for"} & attributes
