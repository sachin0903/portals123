"""The pooled cost basis and the realised gains that come off it.

Every case here is a SEQUENCE, because that is the only way this can be wrong: a running average is
a recurrence, so a row's answer depends on every row before it, and a fixture of one movement
proves nothing. The expected figures are worked from first principles (section 104 pooling and the
TCGA 1992 s.106A 30-day identification rule) rather than from the v1 pipeline's output — the point
of porting was to get a CGT figure right, not to reproduce one.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from seccl_sync import book_costs as bc

#: Day 1 of every fixture. Movements are placed by an OFFSET from it rather than by a day-of-month,
#: because the 30-day boundary cases need to cross a month end — `date(2026, 1, 41)` is not a date.
EPOCH = date(2026, 1, 1)


def _mv(
    tid: str,
    day: int,
    movement: str,
    quantity: str,
    cost: str,
    *,
    product: str = "GIA",
    sub_type: str | None = None,
    transaction_type: str = "Order",
    equalisation: str = "0",
) -> bc.Movement:
    """One movement, signed the way `movements_from_rows` would sign it.

    `cost` is the money that changed hands, which means two different things by direction and is
    carried in two fields for exactly that reason: on an acquisition it is what was PAID (and joins
    the pool), on a disposal it is what was RECEIVED (and is what the gain is measured against,
    while the cost of the units sold comes from the pool). Conflating them reported every disposal
    as realising nothing, which is the bug `Movement.proceeds` exists to prevent.
    """
    sign = 1 if movement in bc.ACQUISITION_MOVEMENTS else -1
    return bc.Movement(
        transaction_id=tid,
        account_id="ACC1",
        isin="GB00B7FQLN12",
        on=EPOCH + timedelta(days=day - 1),
        movement_type=movement,
        transaction_type=transaction_type,
        sub_type=sub_type,
        product_type=product,
        signed_quantity=sign * abs(Decimal(quantity)),
        consideration=sign * abs(Decimal(cost)),
        proceeds=abs(Decimal(cost)),
        equalisation=Decimal(equalisation),
    )


def _by_id(rows: list[bc.BookCost]) -> dict[str, bc.BookCost]:
    return {row.transaction_id: row for row in rows}


def test_the_pool_averages_across_acquisitions() -> None:
    """Section 104 pooling: two purchases at different prices leave ONE holding at their weighted
    average, and a later disposal comes out at that average — not at the price of the units the
    client happens to think they sold."""
    rows = _by_id(
        bc.compute(
            [
                _mv("b1", 1, "Invest", "100", "1000"),  # £10.00 a unit
                _mv("b2", 2, "Invest", "100", "2000"),  # £20.00 a unit
            ]
        )
    )
    assert rows["b2"].total_quantity == Decimal(200)
    assert rows["b2"].total_book_cost == Decimal(3000)
    assert rows["b2"].book_price == Decimal(15)  # the weighted average, not either purchase price


def test_a_disposal_comes_out_of_the_pool_at_the_pooled_price() -> None:
    """50 of 200 units held at a £15 average cost £750, so proceeds of £1,000 realise £250. The
    pool keeps the remaining 150 units at the SAME average — a disposal does not re-price what
    is left."""
    rows = _by_id(
        bc.compute(
            [
                _mv("b1", 1, "Invest", "100", "1000"),
                _mv("b2", 2, "Invest", "100", "2000"),
                # Far enough after the purchases that nothing follows it to match against.
                _mv("s1", 20, "Sell", "50", "1000"),
            ]
        )
    )
    assert rows["s1"].book_cost == Decimal(-750)
    assert rows["s1"].realised_gain == Decimal("250.000000")
    assert rows["s1"].total_quantity == Decimal(150)
    assert rows["s1"].book_price == Decimal(15)


def test_a_loss_is_a_negative_gain_not_an_absent_one() -> None:
    """Proceeds below cost realise a LOSS, which is a figure a client needs — it offsets a gain
    elsewhere. Reporting it as None (or as zero) would lose it."""
    rows = _by_id(
        bc.compute([_mv("b1", 1, "Invest", "100", "1000"), _mv("s1", 20, "Sell", "50", "300")])
    )
    assert rows["s1"].realised_gain == Decimal("-200.000000")


def test_an_acquisition_realises_nothing_which_is_not_realising_zero() -> None:
    """None, not 0. A purchase has no gain to report, and a statement summing zeros would claim the
    account realised something and it happened to be nil."""
    rows = _by_id(bc.compute([_mv("b1", 1, "Invest", "100", "1000")]))
    assert rows["b1"].realised_gain is None


# ── share matching (TCGA 1992 s.106A) ──────────────────────────────────────────────────────────
#
# The rule exists to stop bed-and-breakfasting: sell to crystallise a loss, buy back the next
# morning, keep the position and the relief. So a disposal is identified FIRST against acquisitions
# of the same share in the following 30 days, at THEIR price, and only the remainder comes out of
# the pool.


def test_a_repurchase_within_30_days_is_matched_at_its_own_price() -> None:
    """The textbook case. 100 units pooled at £10; sell 100 for £600 (a £400 loss on the pool); buy
    100 back three days later for £650. The disposal is identified against the repurchase, so its
    cost is £650 and the "loss" is £50 — not the £400 the pool would have given."""
    rows = _by_id(
        bc.compute(
            [
                _mv("b1", 1, "Invest", "100", "1000"),
                _mv("s1", 10, "Sell", "100", "600"),
                _mv("b2", 13, "Invest", "100", "650"),
            ]
        )
    )
    assert rows["s1"].matched_quantity == Decimal(100)
    assert rows["s1"].book_cost == Decimal(-650)
    assert rows["s1"].realised_gain == Decimal("-50.000000")
    # And the POOL is untouched: the units sold were the repurchase's, so the original 100 are
    # still held at their original £1,000. (This is the half of the rule that doubles a cost when
    # missed — the repurchase must not ALSO join the pool.)
    assert rows["b2"].total_book_cost == Decimal(1000)
    assert rows["b2"].total_quantity == Decimal(100)


def test_a_repurchase_after_30_days_is_not_matched() -> None:
    """31 days later is outside the window, so the disposal comes out of the pool and the loss
    stands. The boundary is the whole control: a rule that matched anything later would erase real
    losses."""
    rows = _by_id(
        bc.compute(
            [
                _mv("b1", 1, "Invest", "100", "1000"),
                _mv("s1", 10, "Sell", "100", "600"),
                _mv("b2", 10 + 31, "Invest", "100", "650"),
            ]
        )
    )
    assert rows["s1"].matched_quantity == Decimal(0)
    assert rows["s1"].book_cost == Decimal(-1000)
    assert rows["s1"].realised_gain == Decimal("-400.000000")


def test_a_partial_repurchase_matches_what_it_covers_and_pools_the_rest() -> None:
    """Sell 100, buy 40 back inside the window. 40 units are identified against the repurchase at
    its price (£4.00 → £160) and the other 60 come out of the pool at £10.00 (£600), so the cost is
    £760 against £600 of proceeds — a £160 loss."""
    rows = _by_id(
        bc.compute(
            [
                _mv("b1", 1, "Invest", "100", "1000"),
                _mv("s1", 10, "Sell", "100", "600"),
                _mv("b2", 12, "Invest", "40", "160"),
            ]
        )
    )
    assert rows["s1"].matched_quantity == Decimal(40)
    assert rows["s1"].book_cost == Decimal(-760)
    assert rows["s1"].realised_gain == Decimal("-160.000000")


def test_one_repurchase_is_not_matched_twice_by_two_disposals() -> None:
    """The carry that makes matching stateful. Two disposals of 50 each, one repurchase of 60
    between
    them: the first disposal takes 50 of the 60, the second can only take the remaining 10.

    Double-matching would use the repurchase's price twice, understating both gains — which is the
    failure the partial-match ledger exists to prevent."""
    rows = _by_id(
        bc.compute(
            [
                _mv("b1", 1, "Invest", "200", "2000"),  # pooled at £10
                _mv("s1", 5, "Sell", "50", "400"),
                _mv("s2", 6, "Sell", "50", "400"),
                _mv("b2", 8, "Invest", "60", "300"),  # £5 a unit, inside both windows
            ]
        )
    )
    assert rows["s1"].matched_quantity == Decimal(50)
    assert rows["s2"].matched_quantity == Decimal(10)
    # s1: 50 units at £5 = £250 against £400 → +£150
    assert rows["s1"].realised_gain == Decimal("150.000000")
    # s2: 10 at £5 (£50) + 40 from the pool at £10 (£400) = £450 against £400 → -£50
    assert rows["s2"].realised_gain == Decimal("-50.000000")


def test_an_acquisitions_surplus_survives_for_a_later_disposal() -> None:
    """DIVERGENCE from the v1 pipeline, and the reason it is one.

    v1 recorded an acquisition's leftover units only when `-matched_quantity > remaining_quantity`,
    which is false exactly when the acquisition is LARGER than the disposal — so the surplus was
    dropped and a later disposal inside the same window fell back to the pool price instead of the
    repurchase's. Here a 100-unit repurchase matched 30 by the first disposal still has 70
    available to the second."""
    rows = _by_id(
        bc.compute(
            [
                _mv("b1", 1, "Invest", "200", "4000"),  # pooled at £20
                _mv("s1", 5, "Sell", "30", "300"),
                _mv("s2", 7, "Sell", "70", "700"),
                _mv("b2", 9, "Invest", "100", "500"),  # £5 a unit
            ]
        )
    )
    assert rows["s1"].matched_quantity == Decimal(30)
    assert rows["s2"].matched_quantity == Decimal(70), "the surplus must survive the first disposal"
    assert rows["s2"].book_cost == Decimal(-350)  # 70 at £5, all matched — none from the pool


def test_matching_does_not_apply_to_a_wrapper_with_no_cgt() -> None:
    """An ISA disposal is not a chargeable event, so identifying it against a repurchase would spend
    the effort to compute a figure nobody reports — and would state a different basis from the pool
    for no reason. Seccl's own book-cost report draws the same line."""
    for product in ("ISA", "JISA", "SIPP", "PENSION"):
        rows = _by_id(
            bc.compute(
                [
                    _mv("b1", 1, "Invest", "100", "1000", product=product),
                    _mv("s1", 10, "Sell", "100", "600", product=product),
                    _mv("b2", 13, "Invest", "100", "650", product=product),
                ]
            )
        )
        assert rows["s1"].matched_quantity == Decimal(0), product
        assert rows["s1"].book_cost == Decimal(-1000), product


# ── distributions ──────────────────────────────────────────────────────────────────────────────


def test_an_equalisation_payment_comes_out_of_the_pool() -> None:
    """An equalisation payment is a return of the capital embedded in the price paid, not income.
    Left in the pool it would overstate the cost of everything sold afterwards, and therefore
    understate every later gain — on a CASH distribution it is the ONLY thing that touches the
    pool."""
    rows = _by_id(
        bc.compute(
            [
                _mv("b1", 1, "Invest", "100", "1000"),
                _mv(
                    "d1",
                    5,
                    "In",
                    "0",
                    "12",
                    sub_type=bc.CASH_DIVIDEND,
                    transaction_type="Cash",
                    equalisation="4",
                ),
            ]
        )
    )
    assert rows["d1"].book_cost == Decimal(-4)
    assert rows["d1"].total_book_cost == Decimal(996)


def test_an_accumulation_distribution_joins_the_pool_net_of_equalisation() -> None:
    """A reinvested distribution DOES add to the pool — the units are worth more — but only by the
    income half. £0.91 distributed of which £0.52 is equalisation adds £0.39."""
    rows = _by_id(
        bc.compute(
            [
                _mv("b1", 1, "Invest", "100", "1000"),
                _mv(
                    "d1",
                    5,
                    "In",
                    "0",
                    "0.91",
                    sub_type=bc.ACCUMULATION_DIVIDEND,
                    transaction_type="Cash",
                    equalisation="0.52",
                ),
            ]
        )
    )
    assert rows["d1"].book_cost == Decimal("0.39")
    assert rows["d1"].total_book_cost == Decimal("1000.39")


# ── ordering, and the pool's own edges ─────────────────────────────────────────────────────────


def test_a_same_day_purchase_is_pooled_before_a_same_day_sale() -> None:
    """Otherwise the sale is priced off a pool the purchase has not joined, which on the FIRST day
    of a holding means pricing a disposal against nothing at all."""
    rows = _by_id(
        bc.compute([_mv("s1", 1, "Sell", "50", "600"), _mv("b1", 1, "Invest", "100", "1000")])
    )
    assert rows["b1"].total_book_cost == Decimal(1000)
    assert rows["s1"].book_cost == Decimal(-500)  # 50 units at the £10 the purchase established


def test_selling_out_entirely_resets_the_pool() -> None:
    """A repurchase after a full exit starts a NEW pool at its own price — carrying the old average
    forward would price it off a holding that no longer exists.

    The repurchase is deliberately 31 days later: inside the window it would be share-matched
    against the disposal instead, which is a different rule and tested above."""
    rows = _by_id(
        bc.compute(
            [
                _mv("b1", 1, "Invest", "100", "1000"),
                _mv("s1", 20, "Sell", "100", "1500"),
                _mv("b2", 20 + 31, "Invest", "50", "2500"),  # £50 a unit, outside the window
            ]
        )
    )
    assert rows["s1"].total_quantity == Decimal(0)
    assert rows["s1"].book_price == Decimal(0)
    assert rows["s1"].realised_gain == Decimal("500.000000")  # £1,500 for units that cost £1,000
    assert rows["b2"].book_price == Decimal(50)
    assert rows["b2"].total_book_cost == Decimal(2500)


def test_the_last_movement_of_a_holding_is_flagged_current() -> None:
    """`current` is what makes "the position as it stands" a filter rather than a window function
    downstream."""
    rows = bc.compute([_mv("b1", 1, "Invest", "100", "1000"), _mv("b2", 2, "Invest", "10", "100")])
    assert [row.current for row in rows] == [False, True]


def test_holdings_do_not_contaminate_each_other() -> None:
    """The pool is per account per instrument. One shared pool across two ISINs would price a
    disposal of one fund against the average of both."""
    other = _mv("x1", 1, "Invest", "100", "5000")
    other.isin = "GB00B90TBZ18"
    rows = _by_id(bc.compute_all([_mv("b1", 1, "Invest", "100", "1000"), other]))
    assert rows["b1"].book_price == Decimal(10)
    assert rows["x1"].book_price == Decimal(50)


# ── reading the raw layer ──────────────────────────────────────────────────────────────────────


def _raw(**over: object) -> dict[str, object]:
    row = {
        "id": "t1",
        "account_id": "ACC1",
        "isin": "GB00B7FQLN12",
        "status": "Completed",
        "transaction_type": "Order",
        "transaction_sub_type": "At Best",
        "movement_type": "Invest",
        "quantity": 100,
        "amount": 1000,
        "transaction_date": "2026-01-01",
        "information": None,
    }
    row.update(over)
    return row


def test_only_settled_movements_reach_the_pool() -> None:
    """A pending or deleted order has not changed the holding. Including one would price every later
    disposal off a purchase that never happened."""
    for status in ("Pending", "Deleted", "Placed", "Checking"):
        assert bc.movements_from_rows([_raw(status=status)], {"ACC1": "GIA"}) == []
    assert len(bc.movements_from_rows([_raw()], {"ACC1": "GIA"})) == 1


def test_a_movement_with_no_instrument_has_no_pool() -> None:
    """A cash payment, a fee, an interest credit: real movements with no holding to cost."""
    assert bc.movements_from_rows([_raw(isin=None)], {"ACC1": "GIA"}) == []


def test_a_cash_redemption_is_the_fund_paying_out_not_the_client_disposing() -> None:
    """Seccl books it against the instrument, but the client has not sold anything — treating it
    as a disposal would realise a gain nobody made."""
    assert (
        bc.movements_from_rows(
            [_raw(transaction_type="Cash", transaction_sub_type="Redemption")], {"ACC1": "GIA"}
        )
        == []
    )


def test_the_isin_falls_back_to_the_corporate_action_original() -> None:
    """A notional distribution names its instrument only inside `information.originalIsin`; without
    the fallback every accumulation dividend would be dropped and the pool would never see its
    equalisation."""
    rows = bc.movements_from_rows(
        [_raw(isin=None, information=json.dumps({"originalIsin": "GB00BYWQWW93"}))],
        {"ACC1": "GIA"},
    )
    assert [movement.isin for movement in rows] == ["GB00BYWQWW93"]


def test_the_stated_book_cost_wins_over_the_value_and_the_consideration() -> None:
    """`information.bookCostAmount` is the only one of the three that is explicitly a COST; the
    others may include charges. Preferring it is what keeps the pool a cost pool."""
    rows = bc.movements_from_rows(
        [
            _raw(
                information=json.dumps({"bookCostAmount": 990}),
                book_value=995,
                consideration=1000,
            )
        ],
        {"ACC1": "GIA"},
    )
    assert rows[0].consideration == Decimal(990)


def test_the_wrapper_decides_matching_and_comes_from_the_account() -> None:
    """An account not in the map has no wrapper, so matching is off — which is the safe direction:
    it prices a disposal off the pool rather than applying a rule that may not apply."""
    rows = bc.movements_from_rows([_raw(movement_type="Sell")], {})
    assert rows[0].product_type is None
    assert not rows[0].is_matchable


def test_an_undated_movement_does_not_crash_the_run() -> None:
    """Every date column absent is survivable — it sorts to the front and can only affect the pool
    it opens. Raising here would fail the whole firm's book over one malformed row."""
    rows = bc.movements_from_rows(
        [_raw(transaction_date=None, execution_date=None, created_date=None)], {"ACC1": "GIA"}
    )
    assert rows[0].on is None
    assert bc.compute(rows)[0].total_book_cost == Decimal(1000)


def test_records_round_trip_to_the_arrow_boundary() -> None:
    """Money leaves as float only at the very end, and a None gain stays None — Arrow would
    otherwise read a 0.0 as a realised nil."""
    rows = bc.compute([_mv("b1", 1, "Invest", "100", "1000"), _mv("s1", 20, "Sell", "50", "600")])
    records = {row["transaction_id"]: row for row in bc.as_records(rows)}
    assert records["b1"]["realised_gain"] is None
    assert records["s1"]["realised_gain"] == pytest.approx(100.0)
    assert records["b1"]["book_price"] == pytest.approx(10.0)


def test_the_summary_names_what_a_run_did() -> None:
    """A run that computed nothing reads downstream as "this firm realised no gains", so the log has
    to be able to tell the two apart."""
    movements = [_mv("b1", 1, "Invest", "100", "1000"), _mv("s1", 5, "Sell", "50", "600")]
    summary = bc.summarise(movements, bc.compute(movements))
    assert summary.movements == 2
    assert summary.holdings == 1
    assert summary.disposals == 1
    assert "1 disposals" in summary.describe()
