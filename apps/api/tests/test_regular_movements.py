"""Regular payments / withdrawals jsonb — the reader the account card uses, and the PUT that
writes it."""

from app.regular_movements import (
    initial_cash_deposit_of,
    normalise_movements,
    regular_movements_of,
    standing_movements_of,
)


def test_jsonb_read_back_as_text_is_still_read() -> None:
    assert regular_movements_of('[{"amount": 5, "frequency": "monthly"}]')[0]["amount"] == 5.0
    assert standing_movements_of('{"amount": 5}')[0]["amount"] == 5.0
    assert regular_movements_of("not json") == []


def test_initial_cash_deposit_is_read_from_data_then_the_older_payments_object() -> None:
    deposit = {"investment_amount": 10_000}
    assert initial_cash_deposit_of({"initial_cash_deposit": deposit}, []) == deposit
    assert initial_cash_deposit_of({}, {"initial_cash_deposit": deposit}) == deposit
    assert initial_cash_deposit_of('{"initial_cash_deposit": {"investment_amount": 1}}', None) == {
        "investment_amount": 1
    }
    assert initial_cash_deposit_of(None, [{"amount": 5}]) is None


def test_regular_movements_of_reads_the_register_array() -> None:
    assert regular_movements_of(
        [
            {
                "amount": 500,
                "frequency": "monthly",
                "method": "direct_debit",
                "next_date": "2026-08-01",
            }
        ]
    ) == [
        {
            "amount": 500.0,
            "frequency": "monthly",
            "next_date": "2026-08-01",
            "method": "direct_debit",
            "reference": None,
        }
    ]


def test_regular_movements_of_reads_onboarding_objects() -> None:
    assert regular_movements_of(
        {
            "initial_cash_deposit": {"investment_amount": 10_000},
            "regular_payments": {"amount": 250, "frequency": "monthly", "date": "2026-09-01"},
        }
    ) == [
        {
            "amount": 250.0,
            "frequency": "monthly",
            "next_date": "2026-09-01",
            "method": None,
            "reference": None,
        }
    ]
    assert regular_movements_of(
        {"amount": 100, "frequency": "quarterly", "date": "2026-10-06"}
    ) == [
        {
            "amount": 100.0,
            "frequency": "quarterly",
            "next_date": "2026-10-06",
            "method": None,
            "reference": None,
        }
    ]


def test_standing_movements_of_keeps_provider_rows_whole() -> None:
    row = {"amount": 100, "payment_day": 14, "method": "BACS Credit", "auto_disinvest": "Largest"}
    assert standing_movements_of([row, "junk"]) == [row]


def test_standing_movements_of_turns_onboarding_objects_into_a_list() -> None:
    assert standing_movements_of(
        {"amount": 100, "frequency": "quarterly", "date": "2026-10-06"}
    ) == [
        {
            "amount": 100.0,
            "frequency": "quarterly",
            "next_date": "2026-10-06",
            "method": None,
            "reference": None,
        }
    ]
    assert standing_movements_of({"initial_cash_deposit": {"investment_amount": 10_000}}) == []
    assert standing_movements_of(None) == []


def test_regular_movements_of_does_not_invent_a_row_from_an_initial_deposit() -> None:
    assert regular_movements_of({"initial_cash_deposit": {"investment_amount": 10_000}}) == []
    assert regular_movements_of(None) == []
    assert regular_movements_of("monthly") == []


def test_normalise_movements_refuses_a_zero_amount_and_an_unknown_frequency() -> None:
    try:
        normalise_movements([{"amount": 0, "frequency": "monthly"}], kind="payment")
    except ValueError as exc:
        assert "amount" in str(exc)
    else:
        raise AssertionError("expected a refusal")
    try:
        normalise_movements([{"amount": 50, "frequency": "daily"}], kind="withdrawal")
    except ValueError as exc:
        assert "frequency" in str(exc)
    else:
        raise AssertionError("expected a refusal")


def test_normalise_movements_clears_on_an_empty_list() -> None:
    assert normalise_movements([], kind="payment") is None
    assert normalise_movements(None, kind="payment") is None
