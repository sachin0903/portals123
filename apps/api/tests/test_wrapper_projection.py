"""The GIA/ISA monthly engine, checked against the build template's worked example."""

from datetime import date
from decimal import Decimal

import pytest

from app.projections import (
    AssetMix,
    Cashflow,
    GrowthSet,
    Holding,
    ProjectionRequest,
    Tier,
    derive_growth,
    growth_from_target,
    present,
    project,
)

D = Decimal
START = date(2026, 9, 23)

# The £100,000 GIA sample's charge weights, carried by one exchange-traded line plus cash,
# so the loop is tested independently of a particular model's asset list.
_ONGOING = D("0.002402") / D("0.895")
_TRANSACTION = D("0.000447") / D("0.895")
TIERS = (
    Tier(D(0), D(50000), D("0.0030")),
    Tier(D(50000), D(150000), D("0.0025")),
    Tier(D(150000), D(500000), D("0.0020")),
    Tier(D(500000), D(1000000), D("0.0010")),
    Tier(D(1000000), None, D(0)),
)
HOLDINGS = (
    Holding(
        "Exchange traded",
        D("0.895"),
        "etf",
        "XS0000000001",
        ongoing=_ONGOING,
        transaction=_TRANSACTION,
    ),
    Holding("Cash", D("0.105"), "cash"),
)


def _request(**overrides: object) -> ProjectionRequest:
    base: dict[str, object] = {
        "wrapper": "GIA",
        "start": START,
        "term_years": 15,
        "contributions": (Cashflow("single", D(100000), on=START),),
        "withdrawals": (),
        "existing_balance": D(0),
        "other_platform_assets": D(0),
        "adviser_initial_amount": None,
        "adviser_initial_rate": None,
        "adviser_ongoing": D("0.005"),
        "dfm_rate": D("0.002"),
        "product_rate": D(0),
        "tiers": TIERS,
        "dealing_rate_exchange_traded": D("0.0004"),
        "dealing_rate_fund": D(0),
        "dealing_minimum": None,
        "growth_low": D("-0.0122"),
        "growth_mid": D("0.0178"),
        "growth_high": D("0.0478"),
        "inflation": D("0.02"),
        "holdings": HOLDINGS,
    }
    base.update(overrides)
    return ProjectionRequest(**base)  # type: ignore[arg-type]


def test_gia_100k_matches_the_worked_example() -> None:
    result = project(_request())
    shown = present(result, _request())
    # The template's own model, and within 0.05% of Seccl's printed £107,869.
    assert shown["projected_value_mid"] == str(result.mid.final)
    final = result.mid.final
    assert abs(final - D(107827)) / D(107827) < D("0.001")
    assert abs(final - D(107869)) / D(107869) < D("0.0005")
    year1 = next(year for year in result.mid.years if year.year == 1)
    assert abs(year1.balance - D("100469")) < D(2)
    adviser = year1.acc.adviser_ongoing.quantize(D("0.01"))
    assert adviser == D("501.71")
    display = shown["display"]
    assert display["riy_all_text"]
    assert "1.78%" in display["riy_all_text"]
    assert "1.28%" in display["riy_all_text"]
    # Annex 3 before-charges is a real no-charges run, about £130,298 at year 15.
    before = result.no_charges.years[-1].balance
    assert abs(before - D(130298)) < D(20)
    assert shown["method"] == "annex3"


def test_regular_contribution_counts_every_month_up_to_the_end_date() -> None:
    request = _request(
        contributions=(
            Cashflow(
                "regular",
                D(1000),
                start=START,
                end=date(2035, 9, 6),
                frequency="monthly",
            ),
        ),
        adviser_ongoing=D(0),
        dfm_rate=D(0),
    )
    result = project(request)
    assert result.mid.paid_in == D(108000)


def test_escalation_steps_on_the_anniversary() -> None:
    request = _request(
        term_years=2,
        contributions=(
            Cashflow("regular", D(1000), start=START, frequency="monthly", escalation=D("0.03")),
        ),
        adviser_ongoing=D(0),
        dfm_rate=D(0),
        holdings=(Holding("Cash", D(1), "cash"),),
        dealing_rate_exchange_traded=D(0),
    )
    result = project(request)
    # 12 months at 1,000 and 12 at 1,030.
    assert result.mid.paid_in == D(1000) * 12 + D(1030) * 12


def test_pot_exhaustion_suppresses_the_reduction_in_yield() -> None:
    request = _request(
        term_years=10,
        contributions=(Cashflow("single", D(10000), on=START),),
        withdrawals=(Cashflow("regular", D(2600), start=START, frequency="monthly"),),
        growth_low=D("-0.05"),
        growth_mid=D("0.01"),
        growth_high=D("0.04"),
    )
    result = project(request)
    shown = present(result, request)
    assert result.mid.exhausted_month is not None
    assert shown["display"]["riy_all_text"] is None
    assert shown["display"]["exhausted_mid"]


def test_growth_from_target_is_the_return_less_inflation() -> None:
    low, mid, high, warnings = growth_from_target(D(4), "GIA")
    assert mid == D("0.02")
    assert low == D("-0.01")
    assert high == D("0.05")
    assert warnings == []


def test_derive_growth_applies_the_gia_haircut_and_the_cap() -> None:
    mix = AssetMix(D("0.5"), D(0), D("0.5"), D(0), D(0))
    rates = GrowthSet(D("0.03"), D("0.01"), D("0.01"), D("0.01"), D("0.03"), D("0.005"), D("0.02"))
    low, mid, high, capped, warnings = derive_growth(mix, rates, "GIA")
    assert mid == D("0.015")
    assert low == mid - D("0.03")
    assert high == mid + D("0.03")
    assert capped is False
    assert warnings == []
    # A rate whose nominal exceeds 4.5% for a GIA is capped, and the warning is kept.
    hot = GrowthSet(D("0.20"), D(0), D(0), D(0), D("0.03"), D(0), D("0.02"))
    equity = AssetMix(D(1), D(0), D(0), D(0), D(0))
    _, capped_mid, _, was_capped, notes = derive_growth(equity, hot, "GIA")
    nominal = (1 + capped_mid) * D("1.02") - 1
    assert was_capped is True
    assert nominal <= D("0.045") + D("0.0000001")
    assert notes


def test_missing_asset_mix_is_a_named_error() -> None:
    mix = AssetMix(D("0.5"), D(0), D(0), D(0), D(0))
    rates = GrowthSet(D("0.04"), D("0.01"), D("0.02"), D("0.01"), D("0.03"), D("0.005"), D("0.02"))
    with pytest.raises(Exception, match="sum to 1"):
        derive_growth(mix, rates, "ISA")
