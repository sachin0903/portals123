"""Display-path fill-in when a demo lake stored cost/gain as NULL (or never had the columns)."""

from app.lake.demo import apply_missing_economics


def _holding(**over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "account_external_id": "demo-1-retirement",
        "asset_type": "Equity",
        "isin": "US67066G1040",
        "value": 100.0,
        "book_cost": None,
        "growth": None,
    }
    row.update(over)
    return row


def test_apply_missing_economics_leaves_numeric_cost_alone() -> None:
    rows = [_holding(book_cost=80.0, growth=20.0, growth_pct=25.0)]
    apply_missing_economics(rows, [{"account_external_id": "demo-1-retirement", "value": 90}])
    assert rows[0]["book_cost"] == 80.0
    assert rows[0]["growth"] == 20.0


def test_apply_missing_economics_cash_is_cost_equal_value() -> None:
    rows = [_holding(asset_type="Cash", isin=None, value=50.0)]
    apply_missing_economics(rows, [])
    assert rows[0]["book_cost"] == 50.0
    assert rows[0]["growth"] == 0.0
    assert rows[0]["growth_pct"] == 0.0


def test_apply_missing_economics_backs_out_cost_from_a_sane_series() -> None:
    # 100 → 112 is a 12% return; cost of a 112 holding is 100.
    series = [
        {"account_external_id": "demo-1-retirement", "value": 100.0},
        {"account_external_id": "demo-1-retirement", "value": 112.0},
    ]
    rows = [_holding(value=112.0)]
    apply_missing_economics(rows, series)
    assert rows[0]["book_cost"] == 100.0
    assert rows[0]["growth"] == 12.0


def test_apply_missing_economics_ignores_a_four_x_series() -> None:
    """The ISA's incomplete early snapshots are not a return — same guard as the seed."""
    series = [
        {"account_external_id": "demo-1-retirement", "value": 25.0},
        {"account_external_id": "demo-1-retirement", "value": 100.0},
    ]
    rows = [_holding(value=112.0)]
    apply_missing_economics(rows, series)
    # Default 12%, matching demo_seed._cumulative_return.
    assert rows[0]["book_cost"] == 100.0
    assert rows[0]["growth"] == 12.0
