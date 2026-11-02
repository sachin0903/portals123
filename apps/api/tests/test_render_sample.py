"""Unit tests for the preview sample-data helpers (pure functions, no DB)."""

from __future__ import annotations

from app.render.sample import (
    sample_context,
    sample_field_value,
    sample_portfolio,
    synthesize_rows,
)


def test_sample_context_has_john_doe_client() -> None:
    ctx = sample_context([])
    assert ctx["client"]["name"] == "John Doe"
    assert ctx["client_name"] == "John Doe"
    assert ctx["adviser_name"] == "Alex Adviser"
    assert "today" in ctx and "date" in ctx


def test_sample_context_fills_form_fields_by_type() -> None:
    fields = [
        {"field_key": "investor_name", "field_type": "text", "label": "Investor name"},
        {"field_key": "fee_pct", "field_type": "percent", "label": "Fee"},
        {"field_key": "pot", "field_type": "currency", "label": "Pot"},
    ]
    ctx = sample_context(fields)
    assert ctx["investor_name"] == "Sample investor name"
    assert ctx["fee_pct"] == 5.0
    assert ctx["pot"] == 25000
    # Also grouped under `answers` for `{{ answers.x }}` access.
    assert ctx["answers"]["investor_name"] == "Sample investor name"


def test_sample_field_value_options_win() -> None:
    assert sample_field_value({"field_type": "select", "options": ["Cautious", "Balanced"]}) == (
        "Cautious"
    )
    assert (
        sample_field_value({"field_type": "select", "options": [{"value": "c", "label": "C"}]})
        == "c"
    )


def test_sample_field_value_closed_lists() -> None:
    assert sample_field_value({"field_type": "marital"}) == "Married"
    assert sample_field_value({"field_type": "gender"}) == "Female"
    assert sample_field_value({"field_type": "title"}) == "Mrs"
    assert sample_field_value({"field_type": "employment"}) == "Employed"


def test_synthesize_rows_shapes_by_column_name() -> None:
    rows = synthesize_rows(["product", "value"], n=3)
    assert len(rows) == 3
    assert rows[0]["product"] == "Global Equity"  # label-ish column → fund name
    assert rows[0]["value"] == 30000  # numeric-ish column → descending 10000*(n-i)
    assert rows[2]["value"] == 10000


def test_synthesize_rows_empty_columns() -> None:
    assert synthesize_rows([]) == []


# -- the sample client's book ---------------------------------------------------------------------


def test_sample_context_carries_accounts_to_loop_over() -> None:
    """More than one, so an author previewing a valuation table sees a repeated header row and a
    total that adds up to something the eye can check."""
    ctx = sample_context([])
    assert len(ctx["accounts"]) > 1
    assert {"name", "value", "product_type", "status"} <= set(ctx["accounts"][0])


def test_sample_account_values_are_raw_for_the_filters() -> None:
    """Numbers and ISO dates, matching the real render -- `| currency` / `| date` do the formatting,
    and a pre-formatted value would make its own filter a no-op."""
    for account in sample_context([])["accounts"]:
        assert isinstance(account["value"], (int, float))
        assert account["opened"][:4].isdigit(), account["opened"]
        assert account["value_as_of"][:4].isdigit(), account["value_as_of"]


def test_sample_portfolio_totals_the_sample_accounts() -> None:
    ctx = sample_context([])
    assert ctx["portfolio"]["account_count"] == len(ctx["accounts"])
    assert ctx["portfolio"]["total_value"] == sum(a["value"] for a in ctx["accounts"])
    assert ctx["portfolio"]["currency"] == "GBP"


def test_sample_portfolio_states_a_date_the_author_can_lay_out() -> None:
    """The preview must fill an "as at ..." line rather than leave it blank -- otherwise the author
    discovers at render that the line exists."""
    assert sample_context([])["portfolio"]["as_at"]


def test_portfolio_as_at_is_the_oldest_valuation() -> None:
    """A roll-up is only as current as its stalest input. ISO dates make that comparable at all --
    sorting the formatted form would order "9 May" after "31 July"."""
    rolled = sample_portfolio(
        [
            {"value": 1.0, "currency": "GBP", "value_as_of": "2026-07-31"},
            {"value": 2.0, "currency": "GBP", "value_as_of": "2026-06-30"},
        ]
    )
    assert rolled["as_at"] == "2026-06-30"


def test_portfolio_withholds_a_symbol_across_two_currencies() -> None:
    rolled = sample_portfolio(
        [{"value": 1.0, "currency": "GBP"}, {"value": 2.0, "currency": "USD"}]
    )
    assert rolled["currency"] == ""
    assert rolled["total_value"] == 3.0


def test_portfolio_of_nothing_is_zero_with_no_date() -> None:
    """A staff document with no client still renders a total, and claims no valuation date."""
    assert sample_portfolio([]) == {
        "total_value": 0.0,
        "currency": "",
        "account_count": 0,
        "as_at": "",
    }


def test_portfolio_ignores_an_unvalued_row_in_the_total_but_still_counts_it() -> None:
    rolled = sample_portfolio([{"value": None, "currency": "GBP"}, {"value": 5.0}])
    assert rolled["total_value"] == 5.0
    assert rolled["account_count"] == 2
