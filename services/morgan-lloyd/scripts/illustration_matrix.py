"""Exercise the CTC illustration engine across a matrix of scenarios and audit the PDFs.

Two jobs, because a request that returns HTTP 200 with a PDF attached tells you almost
nothing:

1. **Breadth** — drive the engine from a one-line accumulation quote up to a full drawdown
   with tiered adviser fees, and record what it accepts and rejects.
2. **Correctness** — pull the figures back out of each PDF and check them against each other
   and against the input. An illustration built from bad input does not fail; it produces a
   confident, wrong document. The fixture that shipped before this script says
   "Chosen Final Annuity Age 20 February 2024" — a date two years in the PAST, because
   `exit_age` was below the investor's actual age. Nothing in the response flags that.

    uv run python scripts/illustration_matrix.py --dry-run     # emit the XML, call nothing
    uv run python scripts/illustration_matrix.py               # run the matrix live
    uv run python scripts/illustration_matrix.py --only minimal_accumulation

Artefacts land in `.data/illustrations/matrix/<scenario>.{req.xml,res.xml,pdf,txt}` (gitignored)
and the write-up goes to `docs/morgan-lloyd/illustrations-report.md`.

Requires `pdftotext` (poppler-utils) for the text extraction, and network access to CTC. The
content type must be exactly `text/xml` — a `charset` parameter makes the engine answer an HTML
error page with HTTP 200 (see `apps/api/app/integrations/ctc/client.py`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

SERVICE_ROOT = Path(__file__).resolve().parent.parent
OUT = SERVICE_ROOT / ".data" / "illustrations" / "matrix"
REPORT = SERVICE_ROOT.parent.parent / "docs" / "morgan-lloyd" / "illustrations-report.md"
URL = "https://camtest.ctc.uk.com/Pages/Remote.aspx?wci=RunQuote&nodb=true"
AGENT = "Graphene Platforms (test)"
TODAY = date(2026, 7, 31)

# Periods per year, for checking a quoted annual total against its per-payment amount.
PER_YEAR = {
    "Monthly": 12,
    "Quarterly": 4,
    "Half Yearly": 2,
    "Yearly": 1,
    "Annually": 1,
    "Single": 0,
}


# --------------------------------------------------------------------------- scenarios


@dataclass
class Stream:
    """One `plan_stream`. `contribution_type` must be one of the four the spec allows —
    `Member`, `Employer`, `Transfer Value`, `Existing Fund Value`. An undocumented value
    (this file used to send "Gross Contribution") is accepted by the engine and then, as far
    as we can tell, ignored, which quietly removes the contribution from the projection."""

    key: str
    contribution_type: str
    amount: str
    frequency: str = "Single"
    crystallised: str = "No"
    # Mandatory when contribution_type is `Member`; meaningless otherwise.
    net_gross: str | None = None


@dataclass
class Scenario:
    name: str
    purpose: str
    dob: str
    exit_age: int
    streams: list[Stream]
    sex: str = "Female"
    title: str = "Mrs"
    forename: str = "Sophie"
    surname: str = "Halloway"
    marital_status: str = "Married"
    quotation_type: str = "Accumulation"
    product_ref: str = "SIPPEN"
    product_type: str = "Self Invested Personal Pension"
    inflation: str = "No"
    growth: str = "0"
    fund_name: str = "Cash"
    product_fees: list[dict[str, Any]] = field(default_factory=list)
    adviser_tiers: list[tuple[str, str]] = field(default_factory=list)
    withdrawals: list[dict[str, Any]] = field(default_factory=list)
    annuity: bool = False
    expect_reject: bool = False

    @property
    def total_in(self) -> float:
        return sum(float(s.amount) for s in self.streams if s.frequency == "Single")

    @property
    def crystallised_in(self) -> float:
        return sum(
            float(s.amount)
            for s in self.streams
            if s.crystallised == "Yes" and s.frequency == "Single"
        )


def _fee(name: str, amount: str, basis: str = "Ongoing", freq: str = "Yearly") -> dict[str, Any]:
    return {"name": name, "amount": amount, "basis": basis, "frequency": freq}


def _withdrawal(amount: str, frequency: str = "Monthly", kind: str = "Flexible") -> dict[str, Any]:
    return {"amount": amount, "frequency": frequency, "type": kind}


def scenarios() -> list[Scenario]:
    """Minimal first, then one new concept at a time, then the combinations.

    Ordering matters when reading the report: if `with_product_fees` breaks and
    `minimal_accumulation` passes, the fee block is implicated and nothing else is.
    """
    uncrys = "Existing Fund Value"
    return [
        Scenario(
            "minimal_accumulation",
            "The smallest document the engine will produce: one transfer in, no fees, "
            "no withdrawals. Everything else is measured against this.",
            dob="1975-06-15",
            exit_age=65,
            streams=[Stream("S1", "Transfer Value", "100000.00")],
        ),
        Scenario(
            "regular_contributions",
            "Monthly contributions rather than a lump sum — exercises the projection of a "
            "stream that keeps paying in.",
            dob="1985-03-01",
            exit_age=65,
            streams=[
                Stream("S1", "Transfer Value", "25000.00"),
                Stream("S2", "Member", "500.00", frequency="Monthly", net_gross="Gross"),
            ],
        ),
        Scenario(
            "existing_fund_plus_transfer",
            "An existing pot plus an incoming transfer, the commonest real opening position.",
            dob="1970-01-20",
            exit_age=65,
            streams=[
                Stream("S1", uncrys, "185000.00"),
                Stream("S2", "Transfer Value", "60000.00"),
            ],
        ),
        Scenario(
            "with_product_fees",
            "Adds Morgan Lloyd's own charges. The first-year charge figure and the "
            "reduction in yield should both move off zero.",
            dob="1970-01-20",
            exit_age=65,
            streams=[Stream("S1", uncrys, "185000.00")],
            product_fees=[
                _fee("Monthly Administration Fee", "115", freq="Monthly"),
                _fee("Pension Scheme Return Fee", "230"),
                _fee("Establish PAYE Scheme", "210", basis="Single", freq="Single"),
            ],
        ),
        Scenario(
            "with_tiered_adviser_fees",
            "A tiered percentage adviser charge — the fee structure most likely to be "
            "mis-modelled, because the tiers apply per threshold rather than to the whole.",
            dob="1970-01-20",
            exit_age=65,
            streams=[Stream("S1", uncrys, "750000.00")],
            adviser_tiers=[
                ("500000.0000", "1"),
                ("1000000.0000", "0.75"),
                ("2000000.0000", "0.5"),
                ("10000000000.0000", "0.25"),
            ],
        ),
        Scenario(
            "drawdown_flexible",
            "Crystallised funds with a regular flexi-access income. Exercises the PCLS "
            "arithmetic and the fund-exhaustion projection.",
            dob="1960-02-20",
            exit_age=85,
            quotation_type="Drawdown",
            streams=[
                Stream("S1", uncrys, "150000.00", crystallised="Yes"),
            ],
            withdrawals=[_withdrawal("750.00")],
        ),
        Scenario(
            "drawdown_with_growth",
            "The same drawdown with a positive real growth rate, so the projection is not "
            "a straight drawdown of a static pot.",
            dob="1960-02-20",
            exit_age=85,
            quotation_type="Drawdown",
            growth="5",
            streams=[Stream("S1", uncrys, "150000.00", crystallised="Yes")],
            withdrawals=[_withdrawal("750.00")],
        ),
        Scenario(
            "crystallised_and_uncrystallised",
            "Both pots at once — the shape Morgan Lloyd's own working example used, and "
            "the one where the tax-free cash base is easiest to get wrong.",
            dob="1968-01-30",
            exit_age=80,
            quotation_type="Drawdown",
            streams=[
                Stream("S1", uncrys, "130000.00"),
                Stream("S2", uncrys, "45000.00", crystallised="Yes"),
                Stream("S3", "Transfer Value", "500000.00"),
            ],
            withdrawals=[_withdrawal("750.00"), _withdrawal("300.00")],
            product_fees=[_fee("Monthly Administration Fee", "115", freq="Monthly")],
        ),
        Scenario(
            "young_long_horizon",
            "Age 35 to 65 — a 30-year projection, where compounding and charge drag are "
            "largest and most likely to expose a units/precision fault.",
            dob="1991-05-10",
            exit_age=65,
            growth="5",
            streams=[
                Stream("S1", "Transfer Value", "40000.00"),
                Stream("S2", "Member", "750.00", frequency="Monthly", net_gross="Gross"),
            ],
            product_fees=[_fee("Monthly Administration Fee", "115", freq="Monthly")],
        ),
        Scenario(
            "near_retirement",
            "Age 64 with an exit age of 75: a short horizon. Guards against the engine "
            "assuming a long term.",
            dob="1962-04-02",
            exit_age=75,
            streams=[Stream("S1", uncrys, "300000.00")],
        ),
        Scenario(
            "inflation_adjusted",
            "Real terms rather than nominal. The projected values should differ from the "
            "otherwise-identical nominal run.",
            dob="1970-01-20",
            exit_age=65,
            inflation="Yes",
            growth="5",
            streams=[Stream("S1", "Existing Fund Value", "185000.00")],
        ),
        Scenario(
            "comprehensive",
            "Everything at once: both pots, a transfer, regular contributions, product "
            "fees, tiered adviser fees, two withdrawal streams and an annuity basis.",
            dob="1968-01-30",
            exit_age=80,
            quotation_type="Drawdown",
            growth="5",
            streams=[
                Stream("S1", "Existing Fund Value", "130000.00"),
                Stream("S2", "Existing Fund Value", "45000.00", crystallised="Yes"),
                Stream("S3", "Transfer Value", "500000.00"),
                Stream("S4", "Member", "1000.00", frequency="Monthly", net_gross="Gross"),
            ],
            product_fees=[
                _fee("Monthly Administration Fee", "115", freq="Monthly"),
                _fee("Pension Scheme Return Fee", "230"),
                _fee("Annual Income Drawdown", "355"),
            ],
            adviser_tiers=[("500000.0000", "1"), ("10000000000.0000", "0.5")],
            withdrawals=[_withdrawal("750.00"), _withdrawal("300.00")],
            annuity=True,
        ),
        Scenario(
            "accumulation_with_annuity",
            "Identical to `minimal_accumulation` but WITH an annuity_basis. The accumulation "
            "layout is the only one carrying a 'yearly income worth' column, so this is the "
            "only place the annuity block can be observed at all — the omission test was run "
            "against a Drawdown scenario, where that column does not exist.",
            dob="1975-06-15",
            exit_age=65,
            streams=[Stream("S1", "Transfer Value", "100000.00")],
            annuity=True,
        ),
        # --- deliberately bad input: the engine must either reject it or we must catch it ---
        Scenario(
            "invalid_exit_age_in_past",
            "REGRESSION. exit_age below the investor's real age. The shipped fixture did "
            "this and the engine produced a confident PDF quoting a maturity date two "
            "years in the past. This scenario exists to prove the audit catches it.",
            dob="1959-02-20",
            exit_age=65,
            streams=[Stream("S1", "Existing Fund Value", "150000.00", crystallised="Yes")],
            withdrawals=[_withdrawal("750.00")],
            quotation_type="Drawdown",
        ),
        Scenario(
            "zero_amount",
            "A nil pot. Either a clean rejection or a document of zeroes — both are "
            "acceptable, silently projecting a non-zero fund is not.",
            dob="1975-06-15",
            exit_age=65,
            streams=[Stream("S1", "Transfer Value", "0.00")],
        ),
    ]


# --------------------------------------------------------------------------- XML


def _esc(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_xml(scenario: Scenario) -> str:
    parts: list[str] = ['<?xml version="1.0" encoding="utf-8"?>', "<message>", "<header>"]
    parts += [
        "<message_version>CTC1</message_version>",
        f"<created_date>{TODAY.isoformat()} 09:00:00</created_date>",
        "<mode>Request</mode>",
        "</header><content><quotations>",
        f"<effective_date>{TODAY.isoformat()}</effective_date>",
        "<output_required>Yes</output_required>",
        f"<output_reference>GRMATRIX-{scenario.name}</output_reference>",
        f"<agent_name>{_esc(AGENT)}</agent_name>",
        "<client_type>Individual</client_type>",
        "<investor>",
        f"<title>{scenario.title}</title>",
        f"<forename>{_esc(scenario.forename)}</forename>",
        f"<surname>{_esc(scenario.surname)}</surname>",
        f"<sex>{scenario.sex}</sex>",
        f"<marital_status>{scenario.marital_status}</marital_status>",
        f"<date_of_birth>{scenario.dob}</date_of_birth>",
        f"<exit_age>{scenario.exit_age}</exit_age>",
        "</investor>",
        f'<quotation product_ref="{scenario.product_ref}" '
        f'product_type="{_esc(scenario.product_type)}">',
        f"<inflation_adjustment_indicator>{scenario.inflation}</inflation_adjustment_indicator>",
        f"<quotation_type>{scenario.quotation_type}</quotation_type>",
        "<inc_already_taken>0</inc_already_taken>",
    ]
    for stream in scenario.streams:
        parts += [
            f'<plan_stream plan_stream_key="{stream.key}">',
            f"<contribution_type>{stream.contribution_type}</contribution_type>",
        ]
        if stream.contribution_type == "Member":
            parts.append(f"<net_gross>{stream.net_gross or 'Gross'}</net_gross>")
        parts += [
            f"<amount>{stream.amount}</amount>",
            f"<frequency>{stream.frequency}</frequency>",
            f"<crystallised>{stream.crystallised}</crystallised>",
            "</plan_stream>",
        ]
    # One fund split covering every stream, so the money is fully allocated. An
    # unallocated premium is a common cause of a silently empty projection.
    parts.append("<premium_split>")
    for stream in scenario.streams:
        parts.append(f'<stream_applicable plan_stream_key="{stream.key}" />')
    total = scenario.total_in
    parts += [
        "<fund_split>",
        f"<fund_name>{scenario.fund_name}</fund_name>",
        f"<fund_ref>{scenario.fund_name}</fund_ref>",
        f"<split_amount>{total:.2f}</split_amount>",
        f"<rate_of_return>{scenario.growth}</rate_of_return>",
        "<generic_fund>Yes</generic_fund>",
        "</fund_split></premium_split>",
    ]
    if scenario.product_fees:
        parts.append("<product_fees>")
        for fee in scenario.product_fees:
            parts += [
                "<product_fee>",
                f"<fee_name>{_esc(fee['name'])}</fee_name>",
                f"<fee_basis>{fee['basis']}</fee_basis>",
                f"<fee_frequency>{fee['frequency']}</fee_frequency>",
                "<fee_timing>Before Growth</fee_timing>",
                "<fee_value_rate>Per Annum</fee_value_rate>",
                "<fee_start><start_type>Immediately</start_type></fee_start>",
                "<fee_end><end_type>Maturity</end_type></fee_end>",
                f"<amount>{fee['amount']}</amount>",
                "<plus_vat>Yes</plus_vat>",
                "</product_fee>",
            ]
        parts.append("</product_fees>")
    for withdrawal in scenario.withdrawals:
        parts += [
            "<withdrawals>",
            "<basis>Fixed</basis>",
            f"<amount>{withdrawal['amount']}</amount>",
            f"<frequency>{withdrawal['frequency']}</frequency>",
            f"<start_date>{TODAY.isoformat()}</start_date>",
            f"<drawdown_type>{withdrawal['type']}</drawdown_type>",
            "</withdrawals>",
        ]
    if scenario.adviser_tiers:
        parts += [
            "<adviser_fees><adviser_fee>",
            "<fee_basis>Ongoing</fee_basis>",
            "<fee_frequency>Annually</fee_frequency>",
            "<fee_timing>In Advance</fee_timing>",
            "<fee_value_rate>Per Annum</fee_value_rate>",
            "<fee_name>Graphene Advice Charge</fee_name>",
            "<fee_tier_selection>Each Threshold</fee_tier_selection>",
            '<fee_tiers type="Percentage">',
        ]
        for upper, rate in scenario.adviser_tiers:
            parts.append(f'<tier upperval="{upper}">{rate}</tier>')
        parts += [
            "</fee_tiers>",
            "<fee_start><start_type>Immediately</start_type></fee_start>",
            "<fee_end><end_type>Maturity</end_type></fee_end>",
            "<plus_vat>No</plus_vat>",
            "</adviser_fee></adviser_fees>",
        ]
    if scenario.annuity:
        parts += [
            "<annuity_basis>",
            "<annuity_frequency>Monthly</annuity_frequency>",
            "<annuity_timing>Advance</annuity_timing>",
            "<escalation>RPI</escalation>",
            "</annuity_basis>",
        ]
    parts += ["</quotation></quotations></content></message>"]
    return "".join(parts)


# --------------------------------------------------------------------------- PDF figures

_MONEY = r"£\s?([\d,]+(?:\.\d{2})?)"
_FIGURES: dict[str, re.Pattern[str]] = {
    "tax_free_lump_sum": re.compile(rf"Tax-free lump sum:\s*{_MONEY}"),
    "drawdown_investment": re.compile(rf"remaining for drawdown investment:\s*{_MONEY}"),
    "pre_retirement_funds": re.compile(rf"Pre-retirement funds:\s*{_MONEY}"),
    "annual_withdrawn": re.compile(rf"withdrawn annually as regular withdrawals:\s*{_MONEY}"),
    "first_year_charge": re.compile(rf"first year charge figure is:\s*{_MONEY}"),
    "year5_value": re.compile(rf"end of year 5 your plan might be worth:\s*{_MONEY}"),
    "year10_value": re.compile(rf"end of year 10 your plan might be worth:\s*{_MONEY}"),
    "exhaustion_age": re.compile(r"drawdown fund will run out when you are:\s*(\d+)"),
    "growth_rate": re.compile(r"investments grow at ([\d.]+)% a year above inflation"),
    "charge_reduction": re.compile(r"after inflation, by:\s*([\d.]+)%"),
    "net_yield": re.compile(r"reduce to ([\d.]+)% after charges"),
    "term_length": re.compile(r"Term length:\s*(.+?)\s*$", re.M),
    "annuity_age_line": re.compile(r"Chosen Final Annuity Age\s*(.+?)\s*$", re.M),
}
# The accumulation layout is a different document from the drawdown one: instead of single
# figures it prints a low/mid/high triplet against the regulator's 2%/5%/8% gross rates.
# Matching only the drawdown labels made nine of fourteen scenarios extract nothing and
# still report "clean" — a pass that checked nothing.
_TRIPLE = r"£\s?([\d,]+)\s+£\s?([\d,]+)\s+£\s?([\d,]+)"
_TRIPLETS: dict[str, re.Pattern[str]] = {
    "projected_fund": re.compile(rf"{_TRIPLE}\s*\n\s*fund could then be worth in real terms:"),
    "projected_tax_free_cash": re.compile(
        rf"{_TRIPLE}\s*\n\s*sum\. In real terms this could be worth:"
    ),
    "projected_yearly_income": re.compile(rf"{_TRIPLE}\s*\n\s*you could buy a yearly income"),
}
_SINGLES_ACC: dict[str, re.Pattern[str]] = {
    "total_investment": re.compile(rf"Total investment\s+{_MONEY}"),
    "retirement_age": re.compile(r"When you reach age (\d+) your retirement"),
}

_DATE_IN_TEXT = re.compile(
    r"(\d{1,2}) (January|February|March|April|May|June|July|August|"
    r"September|October|November|December) (\d{4})"
)
_MONTHS = (
    "January February March April May June July August September October November December"
).split()


def _num(raw: str) -> float:
    return float(raw.replace(",", ""))


def parse_pdf(text: str) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, pattern in _TRIPLETS.items():
        match = pattern.search(text)
        if match:
            found[key] = [_num(g) for g in match.groups()]
    for key, pattern in _SINGLES_ACC.items():
        match = pattern.search(text)
        if match:
            found[key] = _num(match.group(1))
    for key, pattern in {**_FIGURES}.items():
        match = pattern.search(text)
        if not match:
            continue
        raw = match.group(1).strip()
        if key in {"term_length", "annuity_age_line"}:
            found[key] = raw
        elif key == "exhaustion_age":
            found[key] = int(raw)
        else:
            found[key] = _num(raw)
    return found


def _as_date(raw: str) -> date | None:
    match = _DATE_IN_TEXT.search(raw)
    if not match:
        return None
    return date(int(match.group(3)), _MONTHS.index(match.group(2)) + 1, int(match.group(1)))


def audit(scenario: Scenario, figures: dict[str, Any], text: str) -> list[str]:
    """Checks that a wrong-but-confident illustration would fail. Returns problem strings."""
    problems: list[str] = []

    # 1. Anything the engine prints as a maturity/annuity date must be in the future.
    #    This is the fault the shipped fixture had and nothing else detects it.
    for key in ("term_length", "annuity_age_line"):
        raw = figures.get(key)
        if not isinstance(raw, str):
            continue
        when = _as_date(raw)
        if when and when <= TODAY:
            problems.append(
                f"{key} is {raw!r} — that date is in the PAST, so exit_age "
                f"({scenario.exit_age}) is at or below the investor's current age"
            )

    # 2. Tax-free cash is capped at 25% of the crystallised pot.
    tfc = figures.get("tax_free_lump_sum")
    crystallised = scenario.crystallised_in
    if tfc is not None and crystallised:
        cap = crystallised * 0.25
        if tfc > cap + 1:
            problems.append(f"tax-free lump sum £{tfc:,.2f} exceeds 25% of £{crystallised:,.2f}")

    # 3. TFC + remaining drawdown should reconstruct the crystallised pot.
    remaining = figures.get("drawdown_investment")
    if tfc is not None and remaining is not None and crystallised:
        if abs((tfc + remaining) - crystallised) > max(1.0, crystallised * 0.01):
            problems.append(
                f"tax-free cash £{tfc:,.2f} + drawdown £{remaining:,.2f} = "
                f"£{tfc + remaining:,.2f}, but £{crystallised:,.2f} was crystallised"
            )

    # 4. The annual withdrawal total must equal amount x periods per year.
    annual = figures.get("annual_withdrawn")
    if annual is not None and scenario.withdrawals:
        expected = sum(
            float(w["amount"]) * PER_YEAR.get(w["frequency"], 0) for w in scenario.withdrawals
        )
        if abs(annual - expected) > 1:
            problems.append(
                f"annual withdrawals £{annual:,.2f} but the inputs imply £{expected:,.2f}"
            )

    # 5. Charges must actually bite when fees were sent, and must not when none were.
    charge = figures.get("first_year_charge")
    has_fees = bool(scenario.product_fees or scenario.adviser_tiers)
    if charge is not None and has_fees and charge <= 0:
        problems.append("fees were sent but the first-year charge figure is zero")
    if charge is not None and not has_fees and charge > 0:
        problems.append(f"no fees were sent but the first-year charge figure is £{charge:,.2f}")

    # 6. Reduction in yield must be self-consistent: gross - reduction = net.
    gross, reduction, net = (
        figures.get("growth_rate"),
        figures.get("charge_reduction"),
        figures.get("net_yield"),
    )
    if gross is not None and reduction is not None and net is not None:
        if abs((gross - reduction) - net) > 0.15:
            problems.append(
                f"reduction in yield does not reconcile: {gross}% - {reduction}% != {net}%"
            )

    # 7. A drawdown pot cannot run out before the investor reaches it.
    exhaustion = figures.get("exhaustion_age")
    if exhaustion is not None:
        born = date.fromisoformat(scenario.dob)
        age_now = (TODAY - born).days // 365
        if exhaustion < age_now:
            problems.append(f"fund runs out at age {exhaustion}, below current age {age_now}")

    # 8. A funded plan must not project to nothing at year 5.
    year5 = figures.get("year5_value")
    if year5 is not None and scenario.total_in > 0 and year5 == 0 and not scenario.withdrawals:
        problems.append("plan is funded with no withdrawals but year-5 value is zero")

    # 9. The document must be about the right person.
    if scenario.surname not in text:
        problems.append(f"the PDF does not mention the investor's surname {scenario.surname!r}")

    # 10. The accumulation triplet must ascend with the growth rate, and its tax-free cash
    #     column must be a quarter of the matching fund column. This is the only arithmetic
    #     the accumulation document exposes, so it is the whole check for those scenarios.
    fund = figures.get("projected_fund")
    cash = figures.get("projected_tax_free_cash")
    if isinstance(fund, list) and fund != sorted(fund):
        problems.append(f"projected fund does not rise with the growth rate: {fund}")
    if isinstance(fund, list) and isinstance(cash, list):
        for gross, quarter in zip(fund, cash, strict=False):
            if gross and abs(quarter - gross * 0.25) > max(100.0, gross * 0.02):
                problems.append(
                    f"tax-free cash £{quarter:,.0f} is not 25% of the projected £{gross:,.0f}"
                )

    # 11. A pot that grows between year 5 and year 10 cannot also be exhausted soon after.
    #     The comprehensive run reports both, which is internally contradictory.
    y5, y10 = figures.get("year5_value"), figures.get("year10_value")
    if None not in (y5, y10, exhaustion) and isinstance(y5, float) and isinstance(y10, float):
        born = date.fromisoformat(scenario.dob)
        age_now = (TODAY - born).days // 365
        assert exhaustion is not None  # noqa: S101
        if y10 > y5 and exhaustion - age_now <= 10:
            problems.append(
                f"fund grows £{y5:,.0f} -> £{y10:,.0f} over years 5-10 yet is said to run out "
                f"at age {exhaustion}, only {exhaustion - age_now} years away"
            )

    # 12. An audit that extracted nothing has verified nothing, and must not read as a pass.
    if not figures:
        problems.append("no figures could be extracted from the PDF — the audit is vacuous")

    # 13. Placeholders and obvious nulls.
    for token in ("NaN", "#REF", "Infinity", "£0.00 £0.00 £0.00"):
        if token in text:
            problems.append(f"the PDF contains {token!r}")
    return problems


# --------------------------------------------------------------------------- run


def _pdf_text(path: Path) -> str:
    """`pdftotext -layout` keeps the label/value columns aligned, which is what makes the
    figures greppable; the default reflow interleaves them."""
    return subprocess.run(  # noqa: S603
        ["pdftotext", "-layout", str(path), "-"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout


async def run_one(client: Any, scenario: Scenario) -> dict[str, Any]:
    xml = build_xml(scenario)
    (OUT / f"{scenario.name}.req.xml").write_text(xml)
    result: dict[str, Any] = {"name": scenario.name, "purpose": scenario.purpose}

    reply = await client.post(
        URL,
        content=xml.encode("utf-8"),
        # Exactly text/xml: a charset parameter makes the engine 200 with an HTML error.
        headers={"content-type": "text/xml"},
    )
    body = reply.text
    (OUT / f"{scenario.name}.res.xml").write_text(body)
    result["http"] = reply.status_code

    alert = re.search(r"alert\(\s*['\"](.*?)['\"]\s*\)", body, re.S)
    location = re.search(r"<document_location>([^<]+)</document_location>", body)
    outcome = re.search(r"<response>([^<]+)</response>", body)
    result["engine_response"] = outcome.group(1) if outcome else None
    result["rejected"] = alert.group(1) if alert else None
    if not location:
        result["problems"] = ["no document_location in the response"]
        return result

    pdf = await client.get(location.group(1))
    if pdf.status_code != 200 or not pdf.content.startswith(b"%PDF"):
        result["problems"] = [f"document fetch returned {pdf.status_code}, not a PDF"]
        return result
    path = OUT / f"{scenario.name}.pdf"
    path.write_bytes(pdf.content)
    result["pdf_bytes"] = len(pdf.content)

    text = await asyncio.to_thread(_pdf_text, path)
    (OUT / f"{scenario.name}.txt").write_text(text)
    result["pdf_pages"] = text.count("\f") + 1
    result["figures"] = parse_pdf(text)
    result["problems"] = audit(scenario, result["figures"], text)
    return result


async def run_matrix(chosen: list[Scenario]) -> list[dict[str, Any]]:
    import httpx

    OUT.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        for scenario in chosen:
            print(f"  {scenario.name} ... ", end="", flush=True)
            try:
                result = await run_one(client, scenario)
            except Exception as exc:  # noqa: BLE001 - one scenario must not stop the matrix
                result = {
                    "name": scenario.name,
                    "purpose": scenario.purpose,
                    "problems": [f"{type(exc).__name__}: {exc}"],
                }
            problems = result.get("problems") or []
            if result.get("rejected"):
                print(f"rejected by the engine ({result['rejected'][:50]})")
            elif problems:
                print(f"{len(problems)} problem(s)")
            else:
                print(
                    f"clean ({result.get('pdf_bytes', 0) // 1024}KB, "
                    f"{len(result.get('figures') or {})} figures)"
                )
            results.append(result)
    return results


# --------------------------------------------------------------------------- omissions

# Each optional section, and the scenario field that switches it off. The question these
# answer is not "does it still work" but "does the output silently lose a section, or does
# the engine substitute its own standing data" — §10 of ctc-xml-schema.md says product_fees
# falls back to provider standing data rather than to nothing, which is a materially
# different failure and would be invisible without comparing documents.
OMISSIONS: dict[str, dict[str, Any]] = {
    "baseline": {},
    "no_product_fees": {"product_fees": []},
    "no_adviser_fees": {"adviser_tiers": []},
    "no_withdrawals": {"withdrawals": []},
    "no_annuity_basis": {"annuity": False},
    "no_fees_at_all": {"product_fees": [], "adviser_tiers": []},
}


def _omission_scenarios(base_name: str) -> list[tuple[str, Scenario]]:
    base = next(s for s in scenarios() if s.name == base_name)
    built: list[tuple[str, Scenario]] = []
    for label, overrides in OMISSIONS.items():
        variant = replace(base, name=f"omit-{label}", **overrides)
        built.append((label, variant))
    return built


async def run_omissions(base_name: str) -> None:
    import httpx

    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, dict[str, Any]]] = []
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        for label, variant in _omission_scenarios(base_name):
            print(f"  {label} ... ", end="", flush=True)
            try:
                result = await run_one(client, variant)
            except Exception as exc:  # noqa: BLE001
                print(f"error: {exc}")
                continue
            print(f"{result.get('pdf_pages', '?')}pp, {result.get('pdf_bytes', 0) // 1024}KB")
            rows.append((label, result))

    if not rows:
        return
    _, base_result = rows[0]
    base_figures = base_result.get("figures") or {}
    print(f"\n{'variant':20s} {'pages':>5} {'charge':>10}  differences vs baseline")
    for label, result in rows:
        figures = result.get("figures") or {}
        gone = sorted(set(base_figures) - set(figures))
        added = sorted(set(figures) - set(base_figures))
        changed = sorted(
            key for key in set(base_figures) & set(figures) if base_figures[key] != figures[key]
        )
        notes = []
        if gone:
            notes.append(f"lost {gone}")
        if added:
            notes.append(f"gained {added}")
        if changed:
            notes.append(f"changed {changed}")
        charge = figures.get("first_year_charge")
        shown = f"£{charge:,.2f}" if charge is not None else "-"
        print(
            f"{label:20s} {result.get('pdf_pages', '?'):>5} {shown:>10}  "
            f"{'; '.join(notes) or 'identical figure set'}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the CTC illustration matrix.")
    parser.add_argument("--dry-run", action="store_true", help="write the request XML only")
    parser.add_argument("--only", action="append", help="run just these scenarios")
    parser.add_argument("--json", type=Path, help="also write the raw results as JSON")
    parser.add_argument(
        "--omit",
        metavar="SCENARIO",
        help="drop each optional section from SCENARIO in turn and diff the resulting PDFs",
    )
    args = parser.parse_args(argv)

    if args.omit:
        print(f"omission matrix on {args.omit}")
        asyncio.run(run_omissions(args.omit))
        return 0

    chosen = scenarios()
    if args.only:
        wanted = set(args.only)
        chosen = [s for s in chosen if s.name in wanted]
        if not chosen:
            print(f"no scenario matches {sorted(wanted)}")
            return 1

    if args.dry_run:
        OUT.mkdir(parents=True, exist_ok=True)
        for scenario in chosen:
            path = OUT / f"{scenario.name}.req.xml"
            path.write_text(build_xml(scenario))
            print(f"  {path.relative_to(SERVICE_ROOT)}  {len(build_xml(scenario))} bytes")
        return 0

    print(f"running {len(chosen)} scenario(s) against {URL}")
    results = asyncio.run(run_matrix(chosen))
    if args.json:
        args.json.write_text(json.dumps(results, indent=2, default=str) + "\n")
    clean = sum(1 for r in results if not r.get("problems") and not r.get("rejected"))
    print(f"\n{clean}/{len(results)} clean")
    for result in results:
        for problem in result.get("problems") or []:
            print(f"  {result['name']}: {problem}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
