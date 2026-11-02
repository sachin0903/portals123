"""The advice fact find, kept in code as a reference and applied by hand.

A published flow expressed entirely as configuration the firm can then edit. Nothing in the
engine is special-cased for it, and it is NOT part of provisioning: it was written for one firm,
so a new tenant does not get it. ``scripts/seed_advice_journey.py`` is the only thing that writes
it, and only for the tenant it is pointed at. Onboarding stays the account-opening flow; this one
opens no account.

The fact find (including the attitude to risk questions that sit inside it, and the adviser's
vulnerability and identity notes) is the journey. The recommendation and the suitability letter
are NOT a seeded journey: a pension transfer is a transfer, and a firm that wants a letter
journey authors one (or imports one — ``flows/portability.py``) against the fact find this seed
leaves on file. An earlier seed did ship a ``pension_transfer`` recommendation-and-letter flow;
``retire_pension_transfer`` archives it where a tenant still has it live.

What a run lays down for the tenant it is pointed at (idempotent by code):

* **Forms** (``documents.forms`` + ``form_fields``), ported from Lifetime's fact-find pack:
  - ``advice_fact_find``        the fact find — identity, health, dependants, occupation and
    income, expenditure, the schedules (mortgages, bank accounts, investments, liabilities,
    protection policies, pensions) as ``repeating`` groups, estate planning, objectives
  - ``advice_risk_profile``     the twelve attitude-to-risk items, scored into five bands, plus
    the capacity-for-loss supplementaries, which are asked on the same form and not scored
  - ``advice_adviser_assessment`` what the adviser records and the client never fills:
    vulnerability, documents sighted, the meeting
* **The flow** ``fact_find`` — fact find and risk questionnaire in parallel, asked of each
  person (``assignee: client``, ``mode: gather`` so the adviser may type in the meeting), the
  adviser notes (vulnerability, documents sighted) once both are in, then each person's
  declaration.

The Word pack gives the questions and the five band definitions; it does not give points per
answer. The seed scores the twelve five-point Likert items with an even ordinal (1–5 per item,
reversed on the risk-averse statements) and even band cuts over the 12–60 range; the pack's one
three-way choice question is asked and not weighted. The firm edits all of that in the scoring
panel.

Nothing here needs a login: forms and flows carry no required author, so the tenant gets the
whole seed even before it has a user. Apply it with ``scripts/seed_advice_journey.py``.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.flows.definitions import validate_definition
from app.flows.onboarding_seed import _cond, _opts, _republish, _write_form_fields
from app.ids import new_id

FACT_FIND_FLOW = "fact_find"
#: The recommendation-and-letter journey an earlier seed shipped beside the fact find. A pension
#: transfer is a transfer, not an advice journey; the seed now archives it where it is still live.
RETIRED_PENSION_FLOW = "pension_transfer"

_YES_NO = _opts(["Yes", "No"])


# ==================================================================================================
# Field helpers — the same tuple shape as ``onboarding_seed`` so ``_write_form_fields`` is shared:
# (key, type, label, section, required, conditions|None, validation|None, options|None
#  [, description])
# ==================================================================================================
def _col(
    key: str, label: str, col_type: str = "text", *, required: bool = True, options: Any = None
) -> dict[str, Any]:
    """One column of a ``repeating`` group (``form_validation.repeating_columns``)."""
    column: dict[str, Any] = {"key": key, "label": label, "type": col_type, "required": required}
    if options is not None:
        column["options"] = options
    return column


def _group(
    key: str,
    label: str,
    section: str,
    columns: list[dict[str, Any]],
    *,
    required: bool = False,
    description: str | None = None,
) -> tuple[Any, ...]:
    """A ``repeating`` field. Optional by default: a client with no mortgage has no row to add."""
    row: tuple[Any, ...] = (
        key,
        "repeating",
        label,
        section,
        required,
        None,
        {"width": 12},
        columns,
    )
    return (*row, description) if description else row


def _yes_no(
    key: str, label: str, section: str, *, required: bool = True, conditions: Any = None
) -> tuple[Any, ...]:
    return (key, "segmented", label, section, required, conditions, {"width": 6}, _YES_NO)


def _money(key: str, label: str, section: str, *, required: bool = False) -> tuple[Any, ...]:
    return (key, "currency", label, section, required, None, {"width": 4}, None)


def _notes(
    key: str, label: str, section: str, *, conditions: Any = None, required: bool = False
) -> tuple[Any, ...]:
    return (key, "textarea", label, section, required, conditions, {"rows": 3}, None)


# ==================================================================================================
# advice_fact_find — the fact find
# ==================================================================================================
_PERSONAL = "Personal details"
_HEALTH = "Health"
_DEPENDANTS = "Dependants"
_OCCUPATION = "Occupation"
_INCOME = "Income"
_BENEFITS = "Employee benefits"
_EXPENDITURE = "Monthly expenditure"
_MORTGAGES = "Mortgages"
_BANK = "Bank accounts and cash deposits"
_INVESTMENTS = "Investments"
_CGT = "Capital gains tax"
_LIABILITIES = "Liabilities"
_PROTECTION = "Protection"
_PENSIONS = "Pensions"
_STATE_PENSION = "State pension"
_ESTATE = "Estate planning"
_OBJECTIVES = "Objectives"

_FACT_FIND_FIELDS: list[tuple[Any, ...]] = [
    # -- identity: the keys match the client record's columns, written back by FACT_FIND_MAPPING.
    # Title, gender, marital status and employment are the closed person lists (field types
    # `title` / `gender` / `marital` / `employment`), the same vocabulary onboarding asks.
    ("title", "title", "Title", _PERSONAL, True, None, {"width": 4}, None),
    ("first_name", "text", "First name", _PERSONAL, True, None, {"minLength": 2, "width": 4}, None),
    ("middle_name", "text", "Middle name(s)", _PERSONAL, False, None, {"width": 4}, None),
    ("surname", "text", "Surname", _PERSONAL, True, None, {"minLength": 2, "width": 6}, None),
    (
        "previous_name",
        "text",
        "Maiden or previous name",
        _PERSONAL,
        False,
        None,
        {"width": 6},
        None,
    ),
    ("dob", "dateOfBirth", "Date of birth", _PERSONAL, True, None, None, None),
    ("gender", "gender", "Gender", _PERSONAL, False, None, None, None),
    ("marital_status", "marital", "Marital status", _PERSONAL, True, None, None, None),
    (
        "nationality",
        "multiSelect",
        "Nationality",
        _PERSONAL,
        False,
        None,
        None,
        {"source": "nationalities"},
    ),
    (
        "national_identifier",
        "nationalIdentifier",
        "National Insurance number",
        _PERSONAL,
        True,
        None,
        None,
        None,
    ),
    (
        "national_identifier_type",
        "text",
        "Identifier type",
        _PERSONAL,
        False,
        None,
        # Derived by the nationalIdentifier control; submitted beside the identifier, never typed.
        {"hidden": True},
        None,
    ),
    _yes_no("uk_domicile", "UK domiciled?", _PERSONAL),
    _yes_no("uk_resident", "UK resident?", _PERSONAL),
    ("address", "address", "Home address", _PERSONAL, True, None, None, None),
    # -- health
    _yes_no("smoker", "Do you smoke?", _HEALTH),
    _yes_no("smoked_last_12_months", "Have you smoked in the last 12 months?", _HEALTH),
    _yes_no("good_health", "Are you currently in good health?", _HEALTH),
    _notes(
        "health_notes",
        "Health notes",
        _HEALTH,
        conditions=_cond("good_health", "equals", "No"),
        required=True,
    ),
    _notes("medical_conditions", "Medical conditions, including the date diagnosed", _HEALTH),
    # -- dependants
    _group(
        "dependants",
        "Dependants",
        _DEPENDANTS,
        [
            _col("name", "Name"),
            _col("dob", "Date of birth", "date"),
            _col("relationship", "Relationship"),
            _col("dependent_until", "Dependent until", "date", required=False),
        ],
    ),
    # -- occupation
    ("employment_status", "employment", "Employment status", _OCCUPATION, True, None, None, None),
    ("employer", "text", "Employer or business", _OCCUPATION, False, None, None, None),
    ("job_title", "text", "Job title", _OCCUPATION, False, None, None, None),
    (
        "employer_address",
        "address",
        "Employer or business address",
        _OCCUPATION,
        False,
        None,
        None,
        None,
    ),
    (
        "length_of_service_months",
        "number",
        "Length of service (months)",
        _OCCUPATION,
        False,
        None,
        {"min": 0},
        None,
    ),
    ("future_prospects", "text", "Future prospects", _OCCUPATION, False, None, None, None),
    (
        "intended_retirement_age",
        "number",
        "Intended retirement age",
        _OCCUPATION,
        True,
        None,
        {"min": 50, "max": 75, "placeholder": "e.g. 65"},
        None,
    ),
    _money("business_value", "Value of business", _OCCUPATION),
    (
        "business_established",
        "text",
        "How long established?",
        _OCCUPATION,
        False,
        None,
        {"width": 4},
        None,
    ),
    (
        "business_ownership_pct",
        "percentage",
        "Percentage of ownership",
        _OCCUPATION,
        False,
        None,
        {"width": 4},
        None,
    ),
    _notes("employment_notes", "Employment notes", _OCCUPATION),
    # -- income
    _money("income_basic", "Basic salary", _INCOME),
    _money("income_bonus", "Bonus", _INCOME),
    _money("income_overtime", "Overtime", _INCOME),
    _money("income_self_employed", "Self-employed net profit", _INCOME),
    _money("income_other", "Other income (e.g. dividends)", _INCOME),
    _money("income_benefits", "Other income or benefits", _INCOME),
    _money("income_total", "Total gross income", _INCOME),
    # -- employee benefits
    (
        "sick_pay_full_weeks",
        "number",
        "On full pay for (weeks)",
        _BENEFITS,
        False,
        None,
        {"width": 4, "min": 0},
        None,
    ),
    (
        "sick_pay_reduced_weeks",
        "number",
        "Reduced pay for (weeks)",
        _BENEFITS,
        False,
        None,
        {"width": 4, "min": 0},
        None,
    ),
    (
        "sick_pay_reduced_pct",
        "percentage",
        "% of full pay",
        _BENEFITS,
        False,
        None,
        {"width": 4},
        None,
    ),
    _yes_no(
        "company_pension_member", "Member of a company pension scheme?", _BENEFITS, required=False
    ),
    (
        "death_in_service_multiple",
        "select",
        "Death-in-service salary multiple",
        _BENEFITS,
        False,
        None,
        None,
        _opts(["1x", "2x", "3x", "4x"]),
    ),
    _money("death_in_service_lump_sum", "Death-in-service lump sum", _BENEFITS),
    _notes("other_benefits", "Other benefits", _BENEFITS),
    # -- monthly expenditure
    _money("spend_housekeeping", "Housekeeping", _EXPENDITURE),
    _money("spend_children", "Children", _EXPENDITURE),
    _money("spend_personal", "Personal", _EXPENDITURE),
    _money("spend_motoring", "Motoring", _EXPENDITURE),
    _money("spend_debts", "Cost of servicing debts", _EXPENDITURE),
    _money("spend_investment_life", "Investment and life assurance", _EXPENDITURE),
    _money("spend_professional", "Professional", _EXPENDITURE),
    _money("spend_other", "Other", _EXPENDITURE),
    _money("spend_total", "Overall monthly total", _EXPENDITURE),
    _notes("expenditure_notes", "Expenditure notes", _EXPENDITURE),
    # -- the schedules
    _group(
        "mortgages",
        "Mortgages",
        _MORTGAGES,
        [
            _col("lender", "Current lender"),
            _col("market_value", "Market value", "currency", required=False),
            _col("outstanding", "Mortgage outstanding", "currency"),
            _col("interest_only_amount", "Interest-only amount", "currency", required=False),
            _col(
                "repayment_method",
                "Repayment method",
                "select",
                required=False,
                options=_opts(["Repayment", "Interest only", "Part and part"])["options"],
            ),
            _col("rate_type", "Rate type (e.g. fixed)", required=False),
            _col("fixed_deal_expiry", "Fixed deal expiry", "date", required=False),
            _col("interest_rate", "Interest rate", "percentage", required=False),
            _col("monthly_repayment", "Monthly repayment", "currency", required=False),
            _col("end_date", "End date", "date", required=False),
        ],
    ),
    _notes("mortgage_notes", "Mortgage notes", _MORTGAGES),
    _group(
        "bank_accounts",
        "Bank accounts and cash deposits (excluding ISAs)",
        _BANK,
        [
            _col("contract_type", "Contract type"),
            _col("provider", "Provider"),
            _col("balance", "Current balance", "currency"),
            _col("interest_rate", "Interest rate (if known)", "percentage", required=False),
            _col("regular_savings", "Regular savings", "currency", required=False),
        ],
    ),
    _group(
        "investments",
        "Investments",
        _INVESTMENTS,
        [
            _col("provider", "Provider"),
            _col("policy_type", "Policy type"),
            _col("policy_number", "Policy number", required=False),
            _col(
                "regular_contributions", "Total regular contributions", "currency", required=False
            ),
            _col("value", "Value", "currency"),
            _col("valuation_date", "Valuation date", "date", required=False),
        ],
    ),
    _yes_no(
        "cgt_allowance_used",
        "Have you used any part of this year's capital gains tax allowance?",
        _CGT,
        required=False,
    ),
    _yes_no(
        "cgt_excess_agreed",
        "Do you agree that capital gains may be taken in excess of your annual allowance?",
        _CGT,
        required=False,
    ),
    _notes("asset_notes", "Asset notes", _CGT),
    _group(
        "liabilities",
        "Liabilities",
        _LIABILITIES,
        [
            _col("provider", "Provider"),
            _col("type", "Type"),
            _col("interest_rate", "Interest rate", "percentage", required=False),
            _col("amount_outstanding", "Amount outstanding", "currency"),
            _col("monthly_cost", "Regular monthly cost", "currency", required=False),
        ],
    ),
    _notes("liabilities_notes", "Liabilities notes", _LIABILITIES),
    _group(
        "protection_policies",
        "Protection policies",
        _PROTECTION,
        [
            _col("contract_type", "Contract type"),
            _col("provider", "Provider"),
            _col("policy_number", "Policy number", required=False),
            _col("premium", "Premium", "currency", required=False),
            _col("maturity", "Maturity", "date", required=False),
            _col("lives_assured", "Lives assured", required=False),
            _col("sum_assured", "Sum assured", "currency", required=False),
            _col("cic_sum_assured", "Critical illness sum assured", "currency", required=False),
            _col("benefit", "Benefit", "currency", required=False),
            _col("benefit_frequency", "Benefit frequency", required=False),
            _col("deferred_period", "Waiver or deferred period", required=False),
            _col(
                "in_trust",
                "In trust?",
                "select",
                required=False,
                options=_YES_NO["options"],
            ),
        ],
    ),
    _notes("protection_notes", "Protection notes", _PROTECTION),
    # Every arrangement the client holds; a later suitability letter reads these rows
    # (``participants[].fact_find.pensions`` in ``document_context.letter_inputs``).
    _group(
        "pensions",
        "Pensions",
        _PENSIONS,
        [
            _col("provider", "Administrator or provider"),
            _col(
                "scheme_type",
                "Type",
                "select",
                options=_opts(
                    [
                        "Final salary",
                        "Money purchase",
                        "Personal pension",
                        "Retirement annuity",
                        "FSAVC",
                        "Other",
                    ]
                )["options"],
            ),
            _col("scheme_name", "Scheme or policy name", required=False),
            _col("policy_number", "Policy number", required=False),
            _col("start_date", "Policy start date", "date", required=False),
            _col("retirement_age", "Scheme retirement age", "number", required=False),
            _col("regular_contribution", "Regular contribution", "currency", required=False),
            _col("fund_value", "Current fund value", "currency"),
            _col("transfer_value", "Current transfer value", "currency", required=False),
            _col("annual_charge", "Annual management charge", "percentage", required=False),
            _col("valuation_date", "Valuation date", "date", required=False),
        ],
        description="Every existing arrangement, current or deferred.",
    ),
    (
        "state_pension_age",
        "number",
        "State pension age",
        _STATE_PENSION,
        False,
        None,
        {"width": 4, "min": 60, "max": 75},
        None,
    ),
    _money("state_pension_forecast", "State pension forecast (per year)", _STATE_PENSION),
    _notes("retirement_notes", "Retirement notes", _STATE_PENSION),
    # -- estate planning
    _yes_no("has_will", "Do you have a valid will?", _ESTATE),
    _yes_no(
        "will_up_to_date",
        "Is it up to date?",
        _ESTATE,
        conditions=_cond("has_will", "equals", "Yes"),
    ),
    _yes_no("power_of_attorney", "Power of attorney granted?", _ESTATE),
    (
        "attorney_name",
        "text",
        "Attorney name",
        _ESTATE,
        False,
        _cond("power_of_attorney", "equals", "Yes"),
        None,
        None,
    ),
    _yes_no("long_term_care_discussed", "Long-term care to be discussed?", _ESTATE, required=False),
    _notes("estate_notes", "Estate planning notes", _ESTATE),
    # -- objectives
    _notes("objectives", "Your concerns and goals", _OBJECTIVES, required=True),
]

# The fact find writes its identity answers back to crm.contacts through the existing fill_form
# mapping. Only facts that already have a column: nothing on the schedules is mapped. A client
# submission fills a blank column and does not replace one the record already holds; a staff
# submission still writes these through.
FACT_FIND_MAPPING: list[dict[str, Any]] = [
    {"source": "title", "target": "title", "transforms": ["trim"]},
    {"source": "first_name", "target": "first_name", "transforms": ["trim"]},
    {"source": "middle_name", "target": "middle_name", "transforms": ["trim"]},
    {"source": "surname", "target": "surname", "transforms": ["trim"]},
    {"source": "dob", "target": "dob"},
    {"source": "gender", "target": "gender"},
    {"source": "nationality", "target": "nationality"},
    {
        "source": "national_identifier",
        "target": "national_identifier",
        "transforms": ["trim", "upper"],
    },
    {"source": "national_identifier_type", "target": "national_identifier_type"},
    {"source": "address.line1", "target": "address.line1", "transforms": ["trim"]},
    {"source": "address.line2", "target": "address.line2", "transforms": ["trim"]},
    {"source": "address.city", "target": "address.city", "transforms": ["trim"]},
    {"source": "address.region", "target": "address.region", "transforms": ["trim"]},
    {"source": "address.postcode", "target": "address.postcode", "transforms": ["postcode"]},
    {"source": "address.country", "target": "address.country", "transforms": ["trim"]},
    # `snake` canonicalises the picker's display label into the value the client record expects
    # (the same reason `onboarding_seed.PENSION_MAPPING` does).
    {"source": "marital_status", "target": "marital_status", "transforms": ["snake"]},
    {"source": "employment_status", "target": "employment_status", "transforms": ["snake"]},
    {"source": "intended_retirement_age", "target": "retirement_age", "transforms": ["digits"]},
]


# ==================================================================================================
# advice_risk_profile — the twelve items, scored; the supplementaries, not scored
# ==================================================================================================
_ATTITUDE = "Attitude to risk"
_CAPACITY = "Capacity for loss"

# An item is (key, question, options in display order, options ranked from the MOST risk-seeking
# answer to the least). The ranking is what scores: every ranked item scores 5 → 1 down that list
# regardless of how the question is phrased, so a risk-averse statement ("I want my money to be
# safe") is reversed. The order the client SEES is the pack's own. A ranking of ``None`` keeps the
# question on the form and out of the score.
_AGREE = [
    ("strongly_agree", "I strongly agree with this statement"),
    ("agree", "I tend to agree with this statement"),
    ("neutral", "In between"),
    ("disagree", "I tend to disagree with this statement"),
    ("strongly_disagree", "I strongly disagree with this statement"),
]
_AGREE_REVERSED = list(reversed(_AGREE))

RiskItem = tuple[str, str, list[tuple[str, str]], list[tuple[str, str]] | None]
#: The pack's thirteen attitude-to-risk questions: the twelve five-point Likert items, which are
#: scored, and one three-way product choice, which is asked and left for the firm to weight.
RISK_ITEMS: list[RiskItem] = [
    (
        "risk_explore",
        "I would enjoy exploring investment opportunities for my money",
        _AGREE,
        _AGREE,
    ),
    (
        "risk_best_return",
        "I would go for the best possible return even if there were risk involved",
        [
            ("always", "Always"),
            ("usually", "Usually"),
            ("sometimes", "Sometimes"),
            ("rarely", "Rarely"),
            ("never", "Never"),
        ],
        [
            ("always", "Always"),
            ("usually", "Usually"),
            ("sometimes", "Sometimes"),
            ("rarely", "Rarely"),
            ("never", "Never"),
        ],
    ),
    (
        "risk_attitude",
        "How would you describe your typical attitude when making important financial decisions?",
        [
            ("very_adventurous", "Very adventurous"),
            ("fairly_adventurous", "Fairly adventurous"),
            ("average", "Average"),
            ("fairly_cautious", "Fairly cautious"),
            ("very_cautious", "Very cautious"),
        ],
        [
            ("very_adventurous", "Very adventurous"),
            ("fairly_adventurous", "Fairly adventurous"),
            ("average", "Average"),
            ("fairly_cautious", "Fairly cautious"),
            ("very_cautious", "Very cautious"),
        ],
    ),
    (
        "risk_past",
        "What amount of risk do you feel you have taken with your past financial decisions?",
        [
            ("very_large", "Very large"),
            ("large", "Large"),
            ("medium", "Medium"),
            ("small", "Small"),
            ("very_small", "Very small"),
        ],
        [
            ("very_large", "Very large"),
            ("large", "Large"),
            ("medium", "Medium"),
            ("small", "Small"),
            ("very_small", "Very small"),
        ],
    ),
    (
        "risk_safe_steady",
        "To reach my financial goal I prefer an investment which is safe and grows slowly but "
        "steadily, even if it means lower growth overall",
        _AGREE,
        _AGREE_REVERSED,
    ),
    (
        "risk_high_growth",
        "I am looking for high investment growth. I am willing to accept the possibility of "
        "greater losses to achieve this",
        _AGREE,
        _AGREE,
    ),
    (
        "risk_stake",
        "If you had money to invest, how much would you be willing to place in an investment "
        "with possible high returns but a similar chance of losing some of your money?",
        [
            ("all", "All of it"),
            ("more_than_half", "More than half"),
            ("half", "Half"),
            ("less_than_half", "Less than half"),
            ("very_little", "Very little, if any"),
        ],
        [
            ("all", "All of it"),
            ("more_than_half", "More than half"),
            ("half", "Half"),
            ("less_than_half", "Less than half"),
            ("very_little", "Very little, if any"),
        ],
    ),
    (
        "risk_friend",
        "How do you think that a friend who knows you well would describe your attitude to "
        "taking financial risks?",
        [
            ("daring", "Daring"),
            ("sometimes_daring", "Sometimes daring"),
            ("thoughtful", "A thoughtful risk taker"),
            ("careful", "Careful"),
            ("risk_averse", "Very cautious and risk averse"),
        ],
        [
            ("daring", "Daring"),
            ("sometimes_daring", "Sometimes daring"),
            ("thoughtful", "A thoughtful risk taker"),
            ("careful", "Careful"),
            ("risk_averse", "Very cautious and risk averse"),
        ],
    ),
    (
        "risk_feel",
        "If you had picked an investment with potential for large gains but also the risk of "
        "large losses, how would you feel?",
        [
            ("panicked", "Panicked and very uncomfortable"),
            ("uneasy", "Quite uneasy"),
            ("concerned", "A little concerned"),
            ("accepting", "Accepting of the possible highs and lows"),
            ("excited", "Excited by the potential for gain"),
        ],
        [
            ("excited", "Excited by the potential for gain"),
            ("accepting", "Accepting of the possible highs and lows"),
            ("concerned", "A little concerned"),
            ("uneasy", "Quite uneasy"),
            ("panicked", "Panicked and very uncomfortable"),
        ],
    ),
    (
        "risk_product",
        "Imagine that you have some money to invest and a choice of two investment products. "
        "Which option would you choose?",
        [
            (
                "low_return",
                "A product with a low average annual return but almost no risk of loss of the "
                "initial investment",
            ),
            (
                "higher_return",
                "A product with a higher average annual return but some risk of losing part of "
                "the initial investment",
            ),
            ("mixture", "A mixture of the two products"),
        ],
        # Not a Likert item: three answers, not five, so it does not share the 1–5 ordinal. The
        # firm weights it in the scoring panel if it wants it counted.
        None,
    ),
    (
        "risk_certain_gains",
        "I would prefer small certain gains to large uncertain ones",
        _AGREE,
        _AGREE_REVERSED,
    ),
    (
        "risk_losses_gains",
        "When considering a major financial decision, which statement best describes the way "
        "you think about the possible losses or the possible gains?",
        [
            ("excited_gains", "I'm excited about the possible gains"),
            ("optimistic_gains", "I'm optimistic about possible gains"),
            ("both", "I think about the possible gains and losses"),
            ("conscious_losses", "I am conscious of the possible losses"),
            ("worry_losses", "I worry about the possible losses"),
        ],
        [
            ("excited_gains", "I'm excited about the possible gains"),
            ("optimistic_gains", "I'm optimistic about possible gains"),
            ("both", "I think about the possible gains and losses"),
            ("conscious_losses", "I am conscious of the possible losses"),
            ("worry_losses", "I worry about the possible losses"),
        ],
    ),
    (
        "risk_safe_money",
        "I want my investment money to be safe even if it means lower returns",
        _AGREE,
        _AGREE_REVERSED,
    ),
]


def _item_weights(ranked: list[tuple[str, str]]) -> dict[str, int]:
    """Even ordinal points: 5 for the most risk-seeking answer down to 1 for the least."""
    return {value: 5 - position for position, (value, _label) in enumerate(ranked)}


#: The scored items, in the pack's order.
SCORED_RISK_ITEMS: list[RiskItem] = [item for item in RISK_ITEMS if item[3] is not None]

# The five bands, verbatim from Lifetime's "Risk Definitions". Twelve items at 1–5 each total
# 12–60; the cuts are even tenths of that range (the top band takes the remainder).
RISK_BANDS: list[dict[str, Any]] = [
    {
        "key": "cautious",
        "label": "Cautious",
        "min": 12,
        "max": 21,
        "definition": (
            "You are prepared to take only a small amount of investment risk and it is important "
            "to you that your capital is protected. This means that your portfolio will "
            "concentrate on investments that provide low returns in the long term but present "
            "lower risk to your capital. Only a small amount of riskier assets will usually be "
            "included in your portfolio in order to increase the chance of obtaining better long "
            "term returns. A typical Cautious investor will be invested mostly in fixed interest "
            "gilts and bonds as well as in cash, with a small element in equities and property "
            "that can boost longer term returns but are associated with more risk. Using a broad "
            "range of assets gives you a varied portfolio and that diversification helps to "
            "reduce the overall levels of risk."
        ),
    },
    {
        "key": "cautious_to_moderate",
        "label": "Cautious to Moderate",
        "min": 22,
        "max": 31,
        "definition": (
            "You are prepared to take limited investment risk in order to increase the chances "
            "of achieving a positive return but you only want to risk a small part of your "
            "capital to achieve this. A typical Cautious to Moderate portfolio will usually have "
            "the larger part of the portfolio invested in fixed interest gilts, bonds or cash "
            "that are low risk but offer only low returns. The remainder of the portfolio will "
            "usually be invested in equities and property which can boost longer term returns "
            "but are associated with more risk. Using a broad range of assets gives you a varied "
            "portfolio and that diversification helps to reduce the overall levels of risk."
        ),
    },
    {
        "key": "moderate",
        "label": "Moderate",
        "min": 32,
        "max": 41,
        "definition": (
            "You are prepared to take a moderate amount of investment risk in order to increase "
            "the chance of achieving a positive return. Capital protection is less important to "
            "you than achieving a better return on the investment. A typical Moderate investor "
            "will usually invest in a variety of assets to obtain diversification and therefore "
            "reduce risk. Equities and property, which can boost longer term returns but are "
            "associated with more risk, would often account for a higher proportion of assets "
            "than fixed interest gilts and bonds or cash. At shorter investment terms the "
            "proportion of higher risk assets is usually reduced. The range of asset types helps "
            "reduce the overall risks while increasing the chance of better returns."
        ),
    },
    {
        "key": "moderate_to_adventurous",
        "label": "Moderate to Adventurous",
        "min": 42,
        "max": 51,
        "definition": (
            "You are prepared to take a medium degree of risk with your investments in return "
            "for the prospect of improving longer term performance. Short term capital "
            "protection is not important to you and you are willing to sacrifice some long term "
            "protection for the likelihood of greater returns. A typical Moderate to Adventurous "
            "investor will be invested in equities but with other assets included to provide "
            "some diversification. There may be a small amount of specialised equities within "
            "the portfolio, which focus on a particular sector of the economy or relate to a "
            "particular market or industry. Specialised equities can boost longer term returns "
            "but are associated with more risk than standard type equities."
        ),
    },
    {
        "key": "adventurous",
        "label": "Adventurous",
        "min": 52,
        "max": 60,
        "definition": (
            "You are prepared to take a substantial degree of risk with your investments in "
            "return for the prospect of the highest possible longer term performance. You "
            "appreciate that over some periods of time there can be significant falls, as well "
            "as rises, in the value of your investments and you may get back less than you "
            "invest. This strategy holds significant risk in the shorter term. A typical "
            "Adventurous investor will usually be invested entirely in higher risk assets such "
            "as equities. There may also be a proportion of the investment in specialised "
            "equities, which focus on a particular sector of the economy or relate to a "
            "particular market or industry. Specialised equities can boost longer term returns "
            "but are associated with more risk than standard type equities."
        ),
    },
]

RISK_SCORING: dict[str, Any] = {
    "fields": [
        {"field_key": key, "weights": _item_weights(ranked)}
        for key, _q, _shown, ranked in SCORED_RISK_ITEMS
        if ranked is not None
    ],
    "bands": RISK_BANDS,
}


def _risk_item_field(item: RiskItem) -> tuple[Any, ...]:
    key, question, shown, _ranked = item
    return (
        key,
        "select",
        question,
        _ATTITUDE,
        True,
        None,
        {"width": 12},
        {"options": [{"value": value, "label": label} for value, label in shown]},
    )


def _capacity_pair(key: str, question: str) -> list[tuple[Any, ...]]:
    """One supplementary: a yes/no, and a note asked when the answer is yes. Neither is scored."""
    return [
        _yes_no(key, question, _CAPACITY),
        _notes(
            f"{key}_notes",
            "Notes",
            _CAPACITY,
            conditions=_cond(key, "equals", "Yes"),
            required=True,
        ),
    ]


_RISK_FIELDS: list[tuple[Any, ...]] = [
    *(_risk_item_field(item) for item in RISK_ITEMS),
    (
        "investment_term",
        "segmented",
        "Investment term",
        _CAPACITY,
        True,
        None,
        {"width": 12},
        _opts(["7 years or less", "8 to 15 years", "More than 15 years"]),
    ),
    *_capacity_pair(
        "emergency_cover",
        "Will this investment need to cover your expenses in an emergency? If yes, how will you "
        "cope with this situation?",
    ),
    *_capacity_pair(
        "living_expenses",
        "Will this investment provide for your current or future daily living expenses? If yes, "
        "what other sources of income do you have to meet your expenses?",
    ),
    *_capacity_pair(
        "significant_proportion",
        "Is this investment a significant proportion of your total wealth?",
    ),
    *_capacity_pair(
        "early_access",
        "Do you have any major financial commitments that could mean you need to access this "
        "money earlier than you currently think?",
    ),
    *_capacity_pair(
        "financial_dependants", "Do you have any dependants who rely on you financially?"
    ),
    *_capacity_pair(
        "relevant_experience",
        "Are there any other factors, such as a profession or former profession, that could be "
        "relevant to your knowledge or experience?",
    ),
    *_capacity_pair(
        "past_investments",
        "Have you made any investments in the past? If yes, which types of investments?",
    ),
]


# ==================================================================================================
# advice_adviser_assessment — what the adviser records; never sent to the client
# ==================================================================================================
_VULNERABILITY = "Vulnerability"
_IDENTITY = "Documents sighted"
_MEETING = "Meeting"

_ADVISER_FIELDS: list[tuple[Any, ...]] = [
    (
        "vulnerable",
        "segmented",
        "In your opinion, could the client be vulnerable?",
        _VULNERABILITY,
        True,
        None,
        {"width": 12},
        _opts(["Yes", "No", "Potential"]),
    ),
    (
        "vulnerability_categories",
        "multiSelect",
        "Vulnerability category (or categories)",
        _VULNERABILITY,
        True,
        _cond("vulnerable", "notEquals", "No"),
        None,
        _opts(["Health", "Life event", "Resilience", "Capability"]),
    ),
    _notes(
        "vulnerability_notes",
        "Vulnerability notes",
        _VULNERABILITY,
        conditions=_cond("vulnerable", "notEquals", "No"),
    ),
    _notes(
        "vulnerability_actions",
        "Actions taken",
        _VULNERABILITY,
        conditions=_cond("vulnerable", "notEquals", "No"),
    ),
    (
        "documents_sighted",
        "multiSelect",
        "Documents sighted",
        _IDENTITY,
        False,
        None,
        None,
        _opts(
            [
                "Original driving licence",
                "Original passport",
                "Electricity bill",
                "Mortgage statement",
                "Council tax bill",
                "Utilities bill",
                "HMRC tax notification",
                "Bank statement",
            ]
        ),
        "Passports and driving licences must be in date; bills and statements must be originals.",
    ),
    (
        "driving_licence_ref",
        "text",
        "Driving licence reference",
        _IDENTITY,
        False,
        _cond("documents_sighted", "contains", "Original driving licence"),
        None,
        None,
    ),
    (
        "driving_licence_expiry",
        "date",
        "Driving licence expiry",
        _IDENTITY,
        False,
        _cond("documents_sighted", "contains", "Original driving licence"),
        None,
        None,
    ),
    (
        "passport_ref",
        "text",
        "Passport reference",
        _IDENTITY,
        False,
        _cond("documents_sighted", "contains", "Original passport"),
        None,
        None,
    ),
    (
        "passport_expiry",
        "date",
        "Passport expiry",
        _IDENTITY,
        False,
        _cond("documents_sighted", "contains", "Original passport"),
        None,
        None,
    ),
    (
        "passport_country",
        "combobox",
        "Country of issue",
        _IDENTITY,
        False,
        _cond("documents_sighted", "contains", "Original passport"),
        None,
        {"source": "countries", "valueKey": "code", "labelKey": "name"},
    ),
    _yes_no("home_visit", "Home visit?", _IDENTITY, required=False),
    ("meeting_date", "date", "Date of the meeting", _MEETING, True, None, None, None),
    ("meeting_location", "text", "Where the meeting took place", _MEETING, False, None, None, None),
    _notes("meeting_notes", "Meeting notes", _MEETING, required=True),
]

# form code -> (name, entity_type, fields, scoring|None)
FORMS: dict[str, tuple[str, str, list[tuple[Any, ...]], dict[str, Any] | None]] = {
    "advice_fact_find": ("Fact find", "applicant", _FACT_FIND_FIELDS, None),
    "advice_risk_profile": ("Attitude to risk", "applicant", _RISK_FIELDS, RISK_SCORING),
    "advice_adviser_assessment": (
        "Adviser assessment",
        "applicant",
        _ADVISER_FIELDS,
        None,
    ),
}


# ==================================================================================================
# The client declaration — the acknowledgement at the end of the pack
# ==================================================================================================
ACKNOWLEDGEMENT = "\n\n".join(
    [
        "I confirm that I have received a copy of the Client Agreement.",
        (
            "I confirm that the information provided in this review is correct and is given on "
            "the understanding that it does not place me under any obligation to buy or take up "
            "any recommendation which may be made, and that a copy of this form is available on "
            "request. I confirm my financial planning objectives are those identified and "
            "prioritised in this document."
        ),
        (
            "I authorise the firm to obtain quotations and details of existing life assurance, "
            "pension policies and investments and to make recommendations for my consideration."
        ),
    ]
)


# ==================================================================================================
# The flow — a v3 graph, the same shape the onboarding seed ships
# ==================================================================================================
def _step(step: dict[str, Any], nxt: str | list[str] | None) -> dict[str, Any]:
    return {**step, "kind": "step", "next": nxt}


def _intake() -> dict[str, Any]:
    return {
        "subject": "person",
        "collect_platform": False,
        "collect_custody": False,
        "collect_product": False,
        "allow_new": False,
        # Kickoff is choosing the clients. There is nothing further to restate.
        "review": False,
    }


def _build_fact_find_definition(forms: dict[str, str]) -> dict[str, Any]:
    """The fact-find journey. No letter and no account.

    Fact find and risk questionnaire fan out together and are each asked of every person.
    Both are the client's to answer and ``mode: gather``, so the adviser may complete either
    in the meeting. The adviser notes (vulnerability, documents sighted) are staff-only. The
    client's declaration waits on their own two forms, not on the adviser's notes.
    """
    return {
        "intake": _intake(),
        "milestones": [
            {
                "status": "fact_find",
                "label": "Your details",
                "summary": "Tell us about yourself, your finances and how you feel about risk.",
                "reached_when": ["fact_find", "risk"],
            },
            {
                "status": "recorded",
                "label": "With your adviser",
                "summary": "Your adviser adds their notes. Nothing is needed from you for this.",
                "reached_when": ["adviser"],
            },
            {
                "status": "declared",
                "label": "Your declaration",
                "summary": "Read the declaration and confirm it.",
                "reached_when": ["acknowledge"],
            },
        ],
        "nodes": [
            {
                "id": "start",
                "kind": "start",
                "title": "Start",
                "config": {},
                "position": None,
                "depends_on": [],
                "next": ["fact_find", "risk"],
            },
            _step(
                {
                    "id": "fact_find",
                    "type": "fill_form",
                    "title": "Fact find",
                    "scope": "participant",
                    "assignee": "client",
                    "config": {
                        "form_uid": forms["advice_fact_find"],
                        "mode": "gather",
                        "mapping": FACT_FIND_MAPPING,
                        "title": "Fact find",
                    },
                },
                ["adviser", "acknowledge"],
            ),
            _step(
                {
                    "id": "risk",
                    "type": "fill_form",
                    "title": "Attitude to risk",
                    "scope": "participant",
                    "assignee": "client",
                    "config": {
                        "form_uid": forms["advice_risk_profile"],
                        "mode": "gather",
                        "title": "Attitude to risk",
                        # The computed band, not the questionnaire: one authored fact a later
                        # letter can read (``memory.promotions`` reads outputs first).
                        "remember": {"risk_band": "band"},
                    },
                },
                ["adviser", "acknowledge"],
            ),
            _step(
                {
                    "id": "adviser",
                    "type": "fill_form",
                    "title": "Adviser assessment",
                    "scope": "participant",
                    "assignee": "staff",
                    "config": {
                        "form_uid": forms["advice_adviser_assessment"],
                        "mode": "gather",
                        "title": "Adviser assessment",
                    },
                },
                None,
            ),
            _step(
                {
                    "id": "acknowledge",
                    "type": "accept_terms",
                    "title": "Fact-find declaration",
                    "scope": "participant",
                    "assignee": "client",
                    "config": {
                        "content": ACKNOWLEDGEMENT,
                        "title": "Fact-find declaration",
                        "category": "terms",
                    },
                },
                None,
            ),
        ],
    }


# ==================================================================================================
# Persistence
# ==================================================================================================
async def _ensure_form(session: AsyncSession, tenant_id: int, code: str) -> str:
    """Upsert one form by code and replace its fields (``onboarding_seed._write_form_fields``);
    the scoring document is written on every run so a corrected weight reaches the tenant."""
    name, entity, fields, scoring = FORMS[code]
    existing = (
        (
            await session.execute(
                text("SELECT id, uid FROM documents.forms WHERE tenant_id = :t AND code = :c"),
                {"t": tenant_id, "c": code},
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing:
        form_id, form_uid = int(existing["id"]), str(existing["uid"])
    else:
        form_uid = new_id()
        form_id = int(
            (
                await session.execute(
                    text(
                        "INSERT INTO documents.forms "
                        "  (uid, tenant_id, code, name, status, entity_type) "
                        "VALUES (:u, :t, :c, :n, 'published', :e) RETURNING id"
                    ),
                    {"u": form_uid, "t": tenant_id, "c": code, "n": name, "e": entity},
                )
            ).scalar_one()
        )
    await _write_form_fields(session, tenant_id, form_id, fields)
    await session.execute(
        text("UPDATE documents.forms SET scoring = CAST(:s AS jsonb) WHERE id = :f"),
        {"s": json.dumps(scoring) if scoring is not None else None, "f": form_id},
    )
    return form_uid


async def _ensure_flow(
    session: AsyncSession,
    *,
    tenant_id: int,
    code: str,
    name: str,
    description: str,
    definition: dict[str, Any],
) -> dict[str, object]:
    """Insert or republish one flow. Journeys already running stay on the version they started."""
    existing = (
        (
            await session.execute(
                text("SELECT id, uid FROM flows.flows WHERE tenant_id = :t AND code = :c"),
                {"t": tenant_id, "c": code},
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing:
        updated = await _republish(
            session, tenant_id=tenant_id, flow_id=int(existing["id"]), definition=definition
        )
        return {"flow_uid": str(existing["uid"]), "created": False, "updated": updated}
    flow_uid = new_id()
    flow_id = (
        await session.execute(
            text(
                "INSERT INTO flows.flows (uid, tenant_id, code, name, description, entity, status) "
                "VALUES (:u, :t, :c, :n, :d, 'advice', 'published') RETURNING id"
            ),
            {"u": flow_uid, "t": tenant_id, "c": code, "n": name, "d": description},
        )
    ).scalar_one()
    vid = (
        await session.execute(
            text(
                "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status, "
                "  definition, published_at) "
                "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id"
            ),
            {"u": new_id(), "t": tenant_id, "f": flow_id, "d": json.dumps(definition)},
        )
    ).scalar_one()
    await session.execute(
        text("UPDATE flows.flows SET current_published_version_id = :v WHERE id = :f"),
        {"v": vid, "f": flow_id},
    )
    return {"flow_uid": flow_uid, "created": True, "updated": False}


async def seed_advice_journey(session: AsyncSession, *, tenant_id: int) -> dict[str, object]:
    """Write the advice journey onto one tenant (idempotent by code).

    Called only from ``scripts/seed_advice_journey.py`` — provisioning does not. The three forms,
    then the ``fact_find`` flow, published. Nothing here needs a login, so the tenant gets the
    whole seed before it has a user. ``flow_uid`` is the fact-find journey.

    Re-running republishes the flow as its next version when the built definition has moved on
    (``onboarding_seed._republish``): journeys in flight stay pinned to the version they started on.
    It also retires the ``pension_transfer`` flow an earlier seed published (``retire_pension_
    transfer``) — that recommendation-and-letter journey is no longer shipped.
    """
    forms = {code: await _ensure_form(session, tenant_id, code) for code in FORMS}
    fact_find = await _ensure_flow(
        session,
        tenant_id=tenant_id,
        code=FACT_FIND_FLOW,
        name="Fact find",
        description=(
            "Fact find, attitude to risk and the adviser's notes. Separate from any "
            "recommendation or suitability report."
        ),
        definition=validate_definition(_build_fact_find_definition(forms)),
    )
    retired = await retire_pension_transfer(session, tenant_id=tenant_id)
    return {
        "flow_uid": fact_find["flow_uid"],
        "fact_find_uid": fact_find["flow_uid"],
        "created": fact_find["created"],
        "updated": fact_find["updated"],
        "forms": forms,
        "retired": retired,
    }


async def retire_pension_transfer(session: AsyncSession, *, tenant_id: int) -> bool:
    """Archive the ``pension_transfer`` flow an earlier seed published, if the tenant still has
    it live. Archiving is what the "New journey" picker respects; a journey already running on it
    finishes on its pinned version, and the firm can restore the flow from the Flows page if it
    wants it. Its forms and template are left where they are. Returns whether anything changed."""
    row = (
        await session.execute(
            text(
                "UPDATE flows.flows SET status = 'archived' "
                "WHERE tenant_id = :t AND code = :c AND status = 'published' RETURNING id"
            ),
            {"t": tenant_id, "c": RETIRED_PENSION_FLOW},
        )
    ).first()
    return row is not None
