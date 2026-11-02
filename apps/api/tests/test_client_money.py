"""The client-raised money-movement rules (``app/client_money.py``).

Pure-function pins for the decision the review step shows and the raise re-checks: which
instruction kind a withdrawal becomes, what the safety check flags, what an allowance-capped
deposit refuses, and the working-day arithmetic. The database halves ride the same control as
the adviser platform (``instructions.create_instruction``), which has its own tests.
"""

import datetime as dt

import pytest

from app import client_money as cm


def test_working_days_skip_the_weekend() -> None:
    # Thursday 1 Oct 2026 + 7 working days = Monday 12 Oct 2026.
    assert cm.working_days_from(dt.date(2026, 10, 1), 7) == dt.date(2026, 10, 12)
    assert cm.working_days_from(dt.date(2026, 10, 2), 1) == dt.date(2026, 10, 5)  # Fri → Mon
    assert cm.working_days_from(dt.date(2026, 10, 1), 0) == dt.date(2026, 10, 1)


class TestReviewFlags:
    def test_a_small_withdrawal_is_not_flagged(self) -> None:
        assert cm.review_flags(3_000, account_value=24_318, portfolio_value=100_000) == []

    def test_each_rule_names_itself_in_reading_order(self) -> None:
        flags = cm.review_flags(18_000, account_value=24_318, portfolio_value=32_000)
        assert [f.code for f in flags] == ["absolute", "account_share", "portfolio_share"]
        assert flags[0].label == "Over £10,000 threshold"
        assert flags[1].label == "Over 50% of this account"
        assert flags[2].label == "Over 10% of total portfolio"

    def test_the_absolute_rule_is_inclusive(self) -> None:
        assert [
            f.code for f in cm.review_flags(10_000, account_value=None, portfolio_value=None)
        ] == ["absolute"]

    def test_an_unknown_denominator_cannot_trip_a_share_rule(self) -> None:
        # An unvalued account is not a 100% withdrawal.
        assert cm.review_flags(500, account_value=None, portfolio_value=0) == []

    def test_the_thresholds_come_from_the_policy(self) -> None:
        # £600 is over a £500 absolute and 6% of a £10,000 account: over a 5% account share,
        # under a 90% book share. The platform defaults (£10,000 / 50% / 10%) would flag none
        # of this, so a hit here is the policy being read, not the defaults.
        pol = cm.Policy(
            review_absolute_gbp=500, review_account_share=0.05, review_portfolio_share=0.9
        )
        flags = cm.review_flags(600, account_value=10_000, portfolio_value=10_000, pol=pol)
        assert [f.code for f in flags] == ["absolute", "account_share"]


class TestWithdrawalPlan:
    def test_cash_pays_a_cash_withdrawal_and_sells_nothing(self) -> None:
        assert cm.withdrawal_plan(
            500, cash_available=870, invested_available=20_000, has_model=True
        ) == ("withdrawal", 500, 0.0, None)

    def test_short_of_cash_a_modelled_account_sells_down(self) -> None:
        kind, from_cash, from_inv, refusal = cm.withdrawal_plan(
            3_000, cash_available=870, invested_available=23_000, has_model=True
        )
        assert (kind, from_cash, from_inv, refusal) == ("model_withdrawal", 870, 2_130, None)

    def test_a_modelled_account_cannot_exceed_what_it_holds(self) -> None:
        kind, _c, _i, refusal = cm.withdrawal_plan(
            30_000, cash_available=870, invested_available=23_000, has_model=True
        )
        assert kind is None
        assert refusal == "Only £23,870 can be withdrawn from this account just now."

    def test_an_unmodelled_account_is_refused_beyond_its_cash_with_the_figure(self) -> None:
        kind, _c, _i, refusal = cm.withdrawal_plan(
            3_000, cash_available=870, invested_available=None, has_model=False
        )
        assert kind is None
        assert refusal is not None and refusal.startswith("Only £870 is held as cash")

    def test_unknown_cash_fails_closed(self) -> None:
        kind, _c, _i, refusal = cm.withdrawal_plan(
            10, cash_available=None, invested_available=None, has_model=False
        )
        assert kind is None
        assert refusal is not None and "can't be confirmed" in refusal

    def test_a_modelled_account_with_unknown_holdings_fails_closed_too(self) -> None:
        # The raise's funding gate refuses a sell-down it cannot measure (a lake that is not
        # there), so the review must refuse at the preview rather than at the last click.
        kind, _c, _i, refusal = cm.withdrawal_plan(
            10, cash_available=None, invested_available=None, has_model=True
        )
        assert kind is None
        assert refusal is not None and "can't be confirmed" in refusal


class TestDepositRefusal:
    def test_an_isa_may_not_exceed_the_remaining_allowance(self) -> None:
        assert cm.deposit_refusal(500, product_type="ISA", allowance_remaining=19_500) is None
        assert cm.deposit_refusal(20_000, product_type="isa", allowance_remaining=19_500) == (
            "You can put in £19,500 more before you reach this year's allowance."
        )

    def test_a_gia_has_no_allowance(self) -> None:
        assert cm.deposit_refusal(1_000_000, product_type="GIA", allowance_remaining=None) is None

    def test_an_unknown_allowance_is_not_a_refusal(self) -> None:
        assert cm.deposit_refusal(50_000, product_type="ISA", allowance_remaining=None) is None


class TestDecide:
    def _decide(self, **over: object) -> cm.Decision:
        base: dict[str, object] = {
            "kind": "withdrawal",
            "amount": 3_000.0,
            "account_value": 24_318.0,
            "portfolio_value": 100_000.0,
            "cash_available": 870.0,
            "invested_available": 23_000.0,
            "has_model": True,
            "product_type": "ISA",
            "allowance_remaining": 19_500.0,
        }
        base.update(over)
        return cm.decide(**base)  # type: ignore[arg-type]

    def test_a_deposit_is_an_expected_in_and_never_flagged(self) -> None:
        d = self._decide(kind="deposit", amount=500.0)
        assert (d.instruction_kind, d.refusal, d.flags) == ("expected_in", None, [])
        assert d.review_required is False

    def test_a_flagged_withdrawal_still_has_a_plan(self) -> None:
        d = self._decide(amount=18_000.0, portfolio_value=32_000.0)
        assert d.instruction_kind == "model_withdrawal"
        assert d.review_required is True
        assert [f.code for f in d.flags] == ["absolute", "account_share", "portfolio_share"]
        assert (d.from_cash, d.from_investments) == (870.0, 17_130.0)

    def test_a_refused_withdrawal_carries_no_flags(self) -> None:
        d = self._decide(amount=50_000.0)
        assert d.instruction_kind is None
        assert d.refusal is not None
        assert d.flags == []

    @pytest.mark.parametrize("amount", [0.0, -5.0, float("nan"), float("inf")])
    def test_a_bad_amount_is_refused_before_anything_else(self, amount: float) -> None:
        d = self._decide(amount=amount)
        assert d.refusal == "Enter an amount greater than zero."

    def test_an_unknown_kind_is_refused(self) -> None:
        assert self._decide(kind="transfer").refusal == "Choose a payment or a withdrawal."


def test_the_note_tells_the_firm_who_raised_it_and_why_it_is_held() -> None:
    d = cm.Decision(
        "withdrawal",
        18_000.0,
        "model_withdrawal",
        None,
        [cm.Flag("absolute", "Over £10,000 threshold")],
        870.0,
        17_130.0,
    )
    note = cm._note(d, from_cash=870.0, from_investments=17_130.0)  # noqa: SLF001
    assert note.startswith("Raised by the client in the portal.")
    assert "£870 from cash and £17,130 from investments." in note
    assert "Safety check: Over £10,000 threshold." in note
    assert "Not sent to the custodian" in note


def test_bank_label_shows_the_bank_and_the_last_four_only() -> None:
    assert cm.bank_label({"bank_name": "Barclays", "account_number": "12342241"}) == (
        "Barclays •••• 2241"
    )
    assert cm.bank_label({"name": "Joint account", "iban": "GB29 NWBK 6016 1331 9268 19"}) == (
        "Joint account •••• 6819"
    )
    assert cm.bank_label(None) is None


def test_the_default_policy_has_no_pay_in_details_yet() -> None:
    # Firm-level configuration is not built (module note); the portal must not print an account
    # number nobody set.
    assert cm.policy(1).pay_in is None
