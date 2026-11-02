"""GIA and ISA growth and cost projection.

A monthly loop, run five times, produces every figure on the illustration. Growth is applied at
the real rate, so the balances are already in today's money. The "before charges" column is a
true no-charges accumulation (COBS 13 Annex 3 Note 5). ``seccl_addback`` is recorded when a run
asks for it: that method adds the pounds paid in charges back onto the after-charges balance,
which understates the effect of charges, and it is not the default.

The renderer prints ``display`` and does not recalculate. Full-precision figures stay on the
result for the stored snapshot.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

Wrapper = Literal["GIA", "ISA"]
Frequency = Literal["monthly", "quarterly", "annual"]
InstrumentType = Literal["fund", "etf", "equity", "cash"]
Method = Literal["annex3", "seccl_addback"]

_POUND = Decimal("1")
_PENCE = Decimal("0.01")
_PERCENT = Decimal("0.01")
_ZERO = Decimal(0)
_ONE = Decimal(1)
_TWELVE = Decimal(12)
_HUNDRED = Decimal(100)


class IllustrationBlocked(Exception):
    """A named reason an illustration cannot be produced. Nothing is filed."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class Cashflow:
    """One single payment, or a regular series."""

    kind: Literal["single", "regular"]
    amount: Decimal
    on: date | None = None
    start: date | None = None
    end: date | None = None
    frequency: Frequency = "monthly"
    escalation: Decimal = _ZERO


@dataclass(frozen=True)
class Tier:
    start: Decimal
    end: Decimal | None
    rate: Decimal


@dataclass(frozen=True)
class Holding:
    name: str
    weight: Decimal
    instrument_type: InstrumentType
    isin: str | None = None
    oneoff: Decimal = _ZERO
    ongoing: Decimal = _ZERO
    transaction: Decimal = _ZERO
    incidental: Decimal = _ZERO
    cost_date: date | None = None


@dataclass(frozen=True)
class AssetMix:
    equity: Decimal
    gilts: Decimal
    bonds: Decimal
    other: Decimal
    cash: Decimal

    def as_dict(self) -> dict[str, Decimal]:
        return {
            "equity": self.equity,
            "gilts": self.gilts,
            "bonds": self.bonds,
            "other": self.other,
            "cash": self.cash,
        }


@dataclass(frozen=True)
class GrowthSet:
    equity: Decimal
    gilts: Decimal
    bonds: Decimal
    other: Decimal
    spread: Decimal
    gia_haircut: Decimal
    inflation: Decimal


@dataclass(frozen=True)
class ProjectionRequest:
    wrapper: Wrapper
    start: date
    term_years: int
    contributions: tuple[Cashflow, ...]
    withdrawals: tuple[Cashflow, ...]
    existing_balance: Decimal
    other_platform_assets: Decimal
    adviser_initial_amount: Decimal | None
    adviser_initial_rate: Decimal | None
    adviser_ongoing: Decimal
    dfm_rate: Decimal
    product_rate: Decimal
    tiers: tuple[Tier, ...]
    dealing_rate_exchange_traded: Decimal
    dealing_rate_fund: Decimal
    dealing_minimum: Decimal | None
    growth_low: Decimal
    growth_mid: Decimal
    growth_high: Decimal
    inflation: Decimal
    holdings: tuple[Holding, ...]
    minimum_monthly_fee: Decimal | None = None
    method: Method = "annex3"


@dataclass
class _Acc:
    platform_ongoing: Decimal = _ZERO
    platform_dealing: Decimal = _ZERO
    dfm: Decimal = _ZERO
    product: Decimal = _ZERO
    adviser_initial: Decimal = _ZERO
    adviser_ongoing: Decimal = _ZERO
    asset: Decimal = _ZERO

    def copy(self) -> _Acc:
        return _Acc(**self.__dict__)

    def total(self) -> Decimal:
        return (
            self.platform_ongoing
            + self.platform_dealing
            + self.dfm
            + self.product
            + self.adviser_initial
            + self.adviser_ongoing
            + self.asset
        )

    def adviser(self) -> Decimal:
        return self.adviser_initial + self.adviser_ongoing


@dataclass
class _Year:
    year: int
    balance: Decimal
    paid_in: Decimal
    withdrawn: Decimal
    acc: _Acc


@dataclass
class _Run:
    growth: Decimal
    final: Decimal
    paid_in: Decimal
    withdrawn: Decimal
    exhausted_month: int | None
    years: list[_Year]
    acc: _Acc


@dataclass
class ProjectionResult:
    growth_low: Decimal
    growth_mid: Decimal
    growth_high: Decimal
    inflation: Decimal
    method: Method
    growth_basis: str
    warnings: list[str]
    low: _Run
    mid: _Run
    high: _Run
    adviser_off: _Run
    no_charges: _Run
    weighted_oneoff: Decimal
    weighted_ongoing: Decimal
    weighted_transaction: Decimal
    weighted_incidental: Decimal
    exchange_traded_share: Decimal
    nominal_capped: bool = False
    derived: dict[str, str] = field(default_factory=dict)


def add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def _years_elapsed(start: date, on: date) -> int:
    years = on.year - start.year
    if (on.month, on.day) < (start.month, start.day):
        years -= 1
    return max(0, years)


def _month_index(start: date, on: date) -> int:
    """1-based illustration month whose calendar month contains ``on``."""
    return (on.year - start.year) * 12 + (on.month - start.month) + 1


def _due(flow: Cashflow, start: date, month: int, term_months: int) -> Decimal:
    """Amount of ``flow`` payable in illustration month ``month``, or zero."""
    if flow.kind == "single":
        if flow.on is None:
            return _ZERO
        return flow.amount if _month_index(start, flow.on) == month else _ZERO
    origin = flow.start or start
    step = {"monthly": 1, "quarterly": 3, "annual": 12}[flow.frequency]
    # Payments whose date falls inside this illustration month and on or before the end date.
    cursor = origin
    # Walk forward from the series start. A 40-year monthly series is 480 steps.
    guard = 0
    while guard < term_months + 24:
        guard += 1
        if flow.end is not None and cursor > flow.end:
            return _ZERO
        if _month_index(start, cursor) == month:
            years = _years_elapsed(origin, cursor)
            return flow.amount * ((_ONE + flow.escalation) ** years)
        if _month_index(start, cursor) > month:
            return _ZERO
        cursor = add_months(cursor, step)
    return _ZERO


def tier_fee(value: Decimal, tiers: tuple[Tier, ...]) -> Decimal:
    fee = _ZERO
    for tier in tiers:
        ceiling = value if tier.end is None else tier.end
        width = min(value, ceiling) - tier.start
        if width > 0:
            fee += width * tier.rate
    return fee


def growth_from_target(
    target_percent: Decimal, wrapper: Wrapper
) -> tuple[Decimal, Decimal, Decimal, list[str]]:
    """Real growth from the model's target return.

    Mid is the target less 2 percentage points, which is the inflation allowance. Low and high
    sit 3 points either side. The nominal equivalent is still held to the FCA cap.
    """
    inflation = Decimal("0.02")
    spread = Decimal("0.03")
    mid = target_percent / Decimal(100) - inflation
    warnings: list[str] = []
    nominal = (_ONE + mid) * (_ONE + inflation) - _ONE
    cap = Decimal("0.05") if wrapper == "ISA" else Decimal("0.045")
    if nominal > cap:
        mid = (_ONE + cap) / (_ONE + inflation) - _ONE
        warnings.append("The nominal growth rate was above the FCA cap and has been reduced to it.")
    return mid - spread, mid, mid + spread, warnings


def derive_growth(
    mix: AssetMix, rates: GrowthSet, wrapper: Wrapper
) -> tuple[Decimal, Decimal, Decimal, bool, list[str]]:
    """Blended real rates, then the nominal FCA cap. Returns low, mid, high, capped, warnings."""
    weights = mix.as_dict()
    total = sum(weights.values(), _ZERO)
    if abs(total - _ONE) > Decimal("0.0001"):
        raise IllustrationBlocked(
            "Model asset mix must sum to 1 "
            f"(equity, gilts, bonds, other and cash currently sum to {total})."
        )
    mid = (
        weights["equity"] * rates.equity
        + weights["gilts"] * rates.gilts
        + weights["bonds"] * rates.bonds
        + weights["other"] * rates.other
        + weights["cash"] * rates.other
    )
    if wrapper == "GIA":
        mid -= rates.gia_haircut
    warnings: list[str] = []
    capped = False
    nominal = (_ONE + mid) * (_ONE + rates.inflation) - _ONE
    cap = Decimal("0.05") if wrapper == "ISA" else Decimal("0.045")
    if nominal > cap:
        mid = (_ONE + cap) / (_ONE + rates.inflation) - _ONE
        capped = True
        warnings.append(
            "The blended nominal growth rate was above the FCA cap and has been reduced to it."
        )
    return mid - rates.spread, mid, mid + rates.spread, capped, warnings


def _weights(
    holdings: tuple[Holding, ...],
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
    oneoff = ongoing = transaction = incidental = traded = _ZERO
    for holding in holdings:
        oneoff += holding.weight * holding.oneoff
        ongoing += holding.weight * holding.ongoing
        transaction += holding.weight * holding.transaction
        incidental += holding.weight * holding.incidental
        if holding.instrument_type in ("etf", "equity"):
            traded += holding.weight
    fund = sum((h.weight for h in holdings if h.instrument_type == "fund"), _ZERO)
    return oneoff, ongoing, transaction, incidental, traded, fund


def _dealing(
    amount: Decimal,
    traded: Decimal,
    fund: Decimal,
    request: ProjectionRequest,
) -> Decimal:
    if amount <= 0:
        return _ZERO
    charge = amount * traded * request.dealing_rate_exchange_traded
    charge += amount * fund * request.dealing_rate_fund
    if request.dealing_minimum is not None and charge > 0 and charge < request.dealing_minimum:
        charge = request.dealing_minimum
    return charge


def _run(
    request: ProjectionRequest,
    growth: Decimal,
    *,
    charges: bool,
    adviser: bool,
    traded: Decimal,
    fund_share: Decimal,
    asset_rate: Decimal,
) -> _Run:
    months = request.term_years * 12
    factor = (_ONE + growth) ** (_ONE / _TWELVE)
    balance = _ZERO
    paid_in = _ZERO
    withdrawn = _ZERO
    acc = _Acc()
    exhausted: int | None = None
    years: list[_Year] = []
    initial_taken = False

    for month in range(1, months + 1):
        if exhausted is not None:
            if month % 12 == 0:
                years.append(_Year(month // 12, balance, paid_in, withdrawn, acc.copy()))
            continue

        new_money = sum(
            (_due(flow, request.start, month, months) for flow in request.contributions), _ZERO
        )
        existing = request.existing_balance if month == 1 else _ZERO
        balance += new_money + existing
        paid_in += new_money + existing

        if adviser and not initial_taken and (new_money > 0 or existing > 0):
            if request.adviser_initial_amount is not None:
                initial = request.adviser_initial_amount
            elif request.adviser_initial_rate is not None:
                base = new_money if new_money > 0 else existing
                initial = request.adviser_initial_rate * base
            else:
                initial = _ZERO
            initial = min(initial, balance)
            balance -= initial
            acc.adviser_initial += initial
            initial_taken = True

        if charges:
            purchase = _dealing(new_money, traded, fund_share, request)
            purchase = min(purchase, balance)
            balance -= purchase
            acc.platform_dealing += purchase

        draw = sum(
            (_due(flow, request.start, month, months) for flow in request.withdrawals), _ZERO
        )
        take = min(draw, balance)
        balance -= take
        withdrawn += take
        if charges and take > 0:
            sale = min(_dealing(take, traded, fund_share, request), balance)
            balance -= sale
            acc.platform_dealing += sale

        if balance <= 0:
            balance = _ZERO
            exhausted = month
            if month % 12 == 0:
                years.append(_Year(month // 12, balance, paid_in, withdrawn, acc.copy()))
            continue

        balance *= factor

        if charges:
            base = balance + request.other_platform_assets
            platform = (
                tier_fee(base, request.tiers) * balance / base / _TWELVE if base > 0 else _ZERO
            )
            if request.minimum_monthly_fee is not None and platform < request.minimum_monthly_fee:
                platform = request.minimum_monthly_fee
            dfm = balance * request.dfm_rate / _TWELVE
            product = balance * request.product_rate / _TWELVE
            ongoing = balance * request.adviser_ongoing / _TWELVE if adviser else _ZERO
            asset = balance * asset_rate / _TWELVE
            balance -= platform + dfm + product + ongoing + asset
            acc.platform_ongoing += platform
            acc.dfm += dfm
            acc.product += product
            acc.adviser_ongoing += ongoing
            acc.asset += asset
            if balance < 0:
                balance = _ZERO

        if month % 12 == 0:
            years.append(_Year(month // 12, balance, paid_in, withdrawn, acc.copy()))

    final = years[-1].balance if years else balance
    return _Run(growth, final, paid_in, withdrawn, exhausted, years, acc)


def project(request: ProjectionRequest) -> ProjectionResult:
    """Run the five projections. Raises nothing for a computable request."""
    oneoff, ongoing, transaction, incidental, traded, fund_share = _weights(request.holdings)
    asset_rate = ongoing + transaction + incidental
    common = {
        "traded": traded,
        "fund_share": fund_share,
        "asset_rate": asset_rate,
    }
    low = _run(request, request.growth_low, charges=True, adviser=True, **common)
    mid = _run(request, request.growth_mid, charges=True, adviser=True, **common)
    high = _run(request, request.growth_high, charges=True, adviser=True, **common)
    adviser_off = _run(request, request.growth_mid, charges=True, adviser=False, **common)
    no_charges = _run(request, request.growth_mid, charges=False, adviser=False, **common)
    warnings: list[str] = []
    if any(flow.end is not None and flow.kind == "regular" for flow in request.contributions):
        horizon = add_months(request.start, request.term_years * 12)
        for flow in request.contributions:
            if flow.kind == "regular" and flow.end is not None and flow.end > horizon:
                warnings.append(
                    "A regular contribution runs past the illustration term and has been truncated."
                )
                break
    return ProjectionResult(
        growth_low=request.growth_low,
        growth_mid=request.growth_mid,
        growth_high=request.growth_high,
        inflation=request.inflation,
        method=request.method,
        growth_basis="real",
        warnings=warnings,
        low=low,
        mid=mid,
        high=high,
        adviser_off=adviser_off,
        no_charges=no_charges,
        weighted_oneoff=oneoff,
        weighted_ongoing=ongoing,
        weighted_transaction=transaction,
        weighted_incidental=incidental,
        exchange_traded_share=traded,
    )


def _column(result: ProjectionResult, year: _Year, which: str) -> Decimal:
    """Effect-of-charges balance for one year, under the recorded method."""
    if which == "after_all":
        return year.balance
    if result.method == "seccl_addback":
        if which == "before":
            return year.balance + year.acc.total()
        return year.balance + year.acc.adviser()
    if which == "before":
        match = next(row for row in result.no_charges.years if row.year == year.year)
        return match.balance
    match = next(row for row in result.adviser_off.years if row.year == year.year)
    return match.balance


def _pounds(value: Decimal) -> str:
    return str(value.quantize(_POUND, rounding=ROUND_HALF_UP))


def _pence(value: Decimal) -> str:
    return str(value.quantize(_PENCE, rounding=ROUND_HALF_UP))


def _rate(value: Decimal) -> str:
    return str((value * _HUNDRED).quantize(_PERCENT, rounding=ROUND_HALF_UP))


def _money(value: Decimal) -> str:
    pounds = value.quantize(_POUND, rounding=ROUND_HALF_UP)
    sign = "-" if pounds < 0 else ""
    return f"{sign}£{abs(pounds):,.0f}"


def _irr(
    final: Decimal, paid_in: Decimal, term: int, request: ProjectionRequest, run: _Run
) -> Decimal | None:
    if run.exhausted_month is not None or paid_in <= 0:
        return None
    simple = (
        len(request.contributions) <= 1
        and not request.withdrawals
        and request.existing_balance == 0
        and all(flow.kind == "single" for flow in request.contributions)
    )
    existing_only = (
        not request.contributions and request.existing_balance > 0 and not request.withdrawals
    )
    if simple or existing_only:
        return (final / paid_in) ** (_ONE / Decimal(term)) - _ONE
    # Monthly IRR, annualised. Contributions negative at the start of their month (t = month-1),
    # withdrawals positive, final value at t = term months.
    flows: list[tuple[int, Decimal]] = []
    months = term * 12
    for month in range(1, months + 1):
        inn = sum(
            (_due(flow, request.start, month, months) for flow in request.contributions), _ZERO
        )
        if month == 1:
            inn += request.existing_balance
        out = sum((_due(flow, request.start, month, months) for flow in request.withdrawals), _ZERO)
        net = out - inn
        if net != 0:
            flows.append((month - 1, net))
    flows.append((months, final))
    rate = Decimal("0.001")
    for _ in range(60):
        npv = _ZERO
        deriv = _ZERO
        one_plus = _ONE + rate
        for at, amount in flows:
            if at == 0:
                npv += amount
                continue
            discount = one_plus**at
            npv += amount / discount
            deriv -= Decimal(at) * amount / (discount * one_plus)
        if deriv == 0:
            break
        step = npv / deriv
        rate -= step
        if abs(step) < Decimal("1e-14"):
            break
    return (_ONE + rate) ** _TWELVE - _ONE


def _riy_sentence(
    final: Decimal, rate: Decimal | None, reduction: Decimal | None, growth_mid: Decimal, kind: str
) -> str | None:
    if rate is None or reduction is None:
        return None
    return (
        f"{_money(final)} means that {kind} could have the same effect as reducing growth "
        f"from {_rate(growth_mid)}% to {_rate(rate)}%, "
        f"a reduction in growth of {_rate(reduction)}% per year."
    )


def table_rows(result: ProjectionResult) -> list[int]:
    term = len(result.mid.years)
    if term <= 10:
        return list(range(1, term + 1))
    return list(range(1, 11)) + [term]


def present(result: ProjectionResult, request: ProjectionRequest) -> dict[str, object]:
    """Display strings plus the full-precision figures the snapshot keeps."""
    mid_final_year = result.mid.years[-1]
    product_final = _column(result, mid_final_year, "after_product")
    before_final = _column(result, mid_final_year, "before")
    after_all = mid_final_year.balance
    paid = mid_final_year.paid_in
    riy_all_rate = _irr(after_all, paid, request.term_years, request, result.mid)
    riy_product_rate = _irr(product_final, paid, request.term_years, request, result.adviser_off)
    riy_all = None if riy_all_rate is None else result.growth_mid - riy_all_rate
    riy_product = None if riy_product_rate is None else result.growth_mid - riy_product_rate
    y1 = next(year for year in result.mid.years if year.year == 1)
    y1_paid = y1.paid_in or _ONE

    def line(amount: Decimal) -> dict[str, str]:
        return {"pounds": _pence(amount), "percent": _rate(amount / y1_paid)}

    platform = {
        "one_off": line(_ZERO),
        "ongoing": line(y1.acc.platform_ongoing),
        "transaction": line(y1.acc.platform_dealing),
    }
    dfm = {"one_off": line(_ZERO), "ongoing": line(y1.acc.dfm), "transaction": line(_ZERO)}
    product = {"one_off": line(_ZERO), "ongoing": line(y1.acc.product), "transaction": line(_ZERO)}
    adviser = {
        "one_off": line(y1.acc.adviser_initial),
        "ongoing": line(y1.acc.adviser_ongoing),
        "transaction": line(_ZERO),
    }
    service = (
        y1.acc.platform_ongoing
        + y1.acc.platform_dealing
        + y1.acc.dfm
        + y1.acc.product
        + y1.acc.adviser_initial
        + y1.acc.adviser_ongoing
    )
    rows = []
    show = table_rows(result)
    for year_no in show:
        year = next(item for item in result.mid.years if item.year == year_no)
        rows.append(
            {
                "year": year_no,
                "final": year_no == request.term_years,
                "paid_in": _money(year.paid_in),
                "withdrawn": _money(year.withdrawn),
                "before_charges": _money(_column(result, year, "before")),
                "after_product_charges": _money(_column(result, year, "after_product")),
                "after_all_charges": _money(year.balance),
            }
        )

    def exhausted(run: _Run) -> str | None:
        if run.exhausted_month is None:
            return None
        year = (run.exhausted_month - 1) // 12 + 1
        month = (run.exhausted_month - 1) % 12 + 1
        return f"year {year}, month {month}"

    suppress = result.mid.exhausted_month is not None
    return {
        "method": result.method,
        "growth_basis": result.growth_basis,
        "warnings": list(result.warnings),
        "growth_low": str(result.growth_low),
        "growth_mid": str(result.growth_mid),
        "growth_high": str(result.growth_high),
        "projected_value_low": str(result.low.final),
        "projected_value_mid": str(result.mid.final),
        "projected_value_high": str(result.high.final),
        "exhausted_month_low": result.low.exhausted_month,
        "exhausted_month_mid": result.mid.exhausted_month,
        "exhausted_month_high": result.high.exhausted_month,
        "y1_paid_in": str(y1.paid_in),
        "y1_service_total": str(service),
        "y1_asset_total": str(y1.acc.asset),
        "y1_total": str(service + y1.acc.asset),
        "before_final": str(before_final),
        "after_product_final": str(product_final),
        "after_all_final": str(after_all),
        "riy_product_rate": None if riy_product_rate is None else str(riy_product_rate),
        "riy_all_rate": None if riy_all_rate is None else str(riy_all_rate),
        "riy_product": None if riy_product is None else str(riy_product),
        "riy_all": None if riy_all is None else str(riy_all),
        "weighted_oneoff": str(result.weighted_oneoff),
        "weighted_ongoing": str(result.weighted_ongoing),
        "weighted_transaction": str(result.weighted_transaction),
        "weighted_incidental": str(result.weighted_incidental),
        "display": {
            "growth_low": f"{_rate(result.growth_low)}%",
            "growth_mid": f"{_rate(result.growth_mid)}%",
            "growth_high": f"{_rate(result.growth_high)}%",
            "inflation": f"{_rate(result.inflation)}%",
            "projected_low": None if result.low.exhausted_month else _money(result.low.final),
            "projected_mid": None if result.mid.exhausted_month else _money(result.mid.final),
            "projected_high": None if result.high.exhausted_month else _money(result.high.final),
            "exhausted_low": exhausted(result.low),
            "exhausted_mid": exhausted(result.mid),
            "exhausted_high": exhausted(result.high),
            "y1_service": line(service),
            "y1_asset": line(y1.acc.asset),
            "y1_total": line(service + y1.acc.asset),
            "charges": {
                "platform": platform,
                "discretionary_manager": dfm,
                "product": product,
                "adviser": adviser,
            },
            "weighted_oneoff": f"{_rate(result.weighted_oneoff)}%",
            "weighted_ongoing": f"{_rate(result.weighted_ongoing)}%",
            "weighted_transaction": f"{_rate(result.weighted_transaction)}%",
            "weighted_incidental": f"{_rate(result.weighted_incidental)}%",
            "table": rows,
            "before_final": _money(before_final),
            "after_product_final": _money(product_final),
            "after_all_final": _money(after_all),
            "riy_product_text": None
            if suppress
            else _riy_sentence(
                product_final,
                riy_product_rate,
                riy_product,
                result.growth_mid,
                "just product and investment charges",
            ),
            "riy_all_text": None
            if suppress
            else _riy_sentence(
                after_all, riy_all_rate, riy_all, result.growth_mid, "all the charges"
            ),
        },
    }
