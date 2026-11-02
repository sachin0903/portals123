"""Build a CTC Bespoke XML v1.6 illustration request (``message_version`` CTC1).

Vendor spec: ``docs/morgan-lloyd/XML_v1.6_Bespoke.pdf`` (22/02/2021); element map in
``docs/morgan-lloyd/data-mapping.md``. Outline of what we emit::

    <message>
      <header>...</header>
      <content>
        <quotations>
          <effective_date>/<output_required>/<output_reference>/<agent_name>/<client_type>
          <investor>*
          <quotation product_ref=".." product_type="..">
            <quotation_type>
            <plan_stream plan_stream_key="..">+
            <premium_split>?
            <withdrawals>?
            <product_fees>?
            <adviser_fees>?
          </quotation>
        </quotations>
      </content>
    </message>

Two things the spec gets wrong that this module deliberately does NOT copy:

* The header example opens ``<mode>`` and closes ``</state>``. That is not well-formed XML; we
  emit ``<mode>Request</mode>``.
* ``<productfees>`` appears once in an example where the table says ``<product_fees>``. We emit
  the underscored form the table defines.

Everything here is pure -- no I/O, no clock. ``created_date`` is passed in so the output is
reproducible and testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

MESSAGE_VERSION = "CTC1"

# Closed vocabularies from the spec. Enforced at build time because the engine's only feedback
# on a bad value is "Error reading XML message" -- worth catching before the round trip.
SEXES = frozenset({"Male", "Female"})
LIFE_TYPES = frozenset({"Applicant", "Life Assured", "Both", "Dependant", "None"})
MARITAL_STATUSES = frozenset(
    {"Civil Partnership", "Divorced", "Married", "Partnered", "Single", "Widowed", "Unknown"}
)
CLIENT_TYPES = frozenset({"Company", "Trust", "Individual"})
CONTRIBUTION_TYPES = frozenset({"Member", "Employer", "Transfer Value", "Existing Fund Value"})
FREQUENCIES = frozenset({"Weekly", "Monthly", "Quarterly", "Half Yearly", "Annually", "Single"})
WITHDRAWAL_FREQUENCIES = frozenset(
    {"Single", "Monthly", "Quarterly", "Half Yearly", "Annually", "2 Months", "4 Months"}
)
WITHDRAWAL_BASES = frozenset({"Fixed", "Maximum GAD", "Percentage of Maximum GAD"})
DRAWDOWN_TYPES = frozenset({"Flexible", "Capped", "UFPLS"})
ANNUITY_FREQUENCIES = frozenset({"Monthly", "Quarterly", "Half Yearly", "Annually"})
ANNUITY_TIMINGS = frozenset({"Advance", "Arrears"})
ESCALATIONS = frozenset({"Fixed", "Level", "LPI", "RPI"})
FEE_BASES = frozenset({"Single", "Ongoing", "Initial"})
FEE_TIMINGS = frozenset({"In Advance", "In Arrears", "Before Growth", "After Growth"})
FEE_VALUE_RATES = frozenset({"Per Frequency", "Per Annum"})

# Spec limits — the engine truncates or rejects beyond these.
MAX_OUTPUT_REFERENCE = 45
MAX_AGENT_NAME = 130
MAX_FORENAME = 100
MAX_SURNAME = 50
# Above this an exit age is a typo, not a plan. The engine accepts anything.
MAX_EXIT_AGE = 100
# A drawdown quote for someone already past retirement still has a term: it runs on to this
# age. The recorded retirement age is when they retired (the column stops at 75). Sending that
# as exit_age makes the engine print "Chosen Final Annuity Age" and "Term length" in the past
# — the invalid_exit_age_in_past scenario was itself a drawdown. 99 leaves a year under
# MAX_EXIT_AGE for a client who is already older, and it is long enough that the year-5 and
# year-10 figures on a drawdown layout still fall inside the term for someone in their 80s.
DRAWDOWN_HORIZON_AGE = 99
MAX_FEE_NAME = 50

# "Transfer Value" and "Existing Fund Value" streams must always be Single (spec, plan_stream).
_ALWAYS_SINGLE = frozenset({"Transfer Value", "Existing Fund Value"})
# Only those two may be crystallised; every other payment type must be "No".
_MAY_CRYSTALLISE = _ALWAYS_SINGLE


class CtcValidationError(ValueError):
    """A request that the CTC engine would reject. Raised before any network call."""


def _money(value: Decimal | float | int) -> str:
    """Plain decimal, no thousands separators or currency symbol, two places for money."""
    return f"{Decimal(str(value)):.2f}"


def _rate(value: Decimal | float | int) -> str:
    """Percentages/rates keep their own precision (0.35, 0.075) -- no forced 2dp.

    ``format(..., "f")`` is essential: ``Decimal("60").normalize()`` is ``6E+1``, and the engine
    would reject that as a non-numeric fund split.
    """
    return format(Decimal(str(value)).normalize(), "f")


def _one_of(name: str, value: str, allowed: frozenset[str]) -> str:
    if value not in allowed:
        raise CtcValidationError(f"{name} must be one of {sorted(allowed)}, got {value!r}")
    return value


def _capped(name: str, value: str, limit: int) -> str:
    if len(value) > limit:
        raise CtcValidationError(f"{name} exceeds {limit} characters ({len(value)})")
    return value


def _child(
    parent: ET.Element, tag: str, text: str | None = None, **attrib: str | None
) -> ET.Element:
    """Create a child element. Attributes with a ``None`` value are dropped, so an optional
    attribute (``product_type``, ``fund_split/@type``) is simply absent rather than empty."""
    element = ET.SubElement(parent, tag, {k: v for k, v in attrib.items() if v is not None})
    if text is not None:
        element.text = text
    return element


def _opt(parent: ET.Element, tag: str, text: str | None) -> None:
    """Emit an element only when there is a value -- the engine treats empty tags as errors."""
    if text is not None and text != "":
        _child(parent, tag, text)


@dataclass(slots=True)
class Investor:
    """A life included in the illustration. Every field is optional per the spec, but a
    pension projection is meaningless without date_of_birth + exit_age."""

    title: str | None = None
    forename: str | None = None
    surname: str | None = None
    sex: str | None = None
    life_type: str | None = None
    marital_status: str | None = None
    date_of_birth: str | None = None  # yyyy-mm-dd
    exit_age: int | None = None

    def validate(self, effective_date: str | None = None) -> None:
        if self.sex is not None:
            _one_of("investor.sex", self.sex, SEXES)
        if self.life_type is not None:
            _one_of("investor.life_type", self.life_type, LIFE_TYPES)
        if self.marital_status is not None:
            _one_of("investor.marital_status", self.marital_status, MARITAL_STATUSES)
        if self.forename is not None:
            _capped("investor.forename", self.forename, MAX_FORENAME)
        if self.surname is not None:
            _capped("investor.surname", self.surname, MAX_SURNAME)
        self._check_exit_age(effective_date)

    def _check_exit_age(self, effective_date: str | None) -> None:
        """Reject a retirement age the investor has already passed at the effective date.

        The engine will not do this for us. Both fields are optional in the schema, so it has
        no obligation to hold the pair — and given both it still produces a confident,
        well-typeset document quoting a maturity date in the PAST. A shipped illustration
        (`GRAPHENE-TEST-0000.pdf`) reads "Chosen Final Annuity Age 20 February 2024" for exactly
        this reason. Nothing downstream catches it, so it has to be caught here.

        Age is measured at the request's ``effective_date``, not today: that is the date the
        projection actually runs from, and it keeps this module free of a clock (so a fixture
        does not quietly become invalid as it ages).

        A pension already in payment is still illustrated. ``projection_exit_age`` chooses a
        later age for that drawdown quote, so this check keeps refusing a past date and the
        caller stops asking for one.
        """
        if self.exit_age is None or self.date_of_birth is None or effective_date is None:
            return
        try:
            born = date.fromisoformat(self.date_of_birth)
        except ValueError as exc:
            raise CtcValidationError(
                f"investor.date_of_birth {self.date_of_birth!r} is not yyyy-mm-dd"
            ) from exc
        try:
            asof = date.fromisoformat(effective_date)
        except ValueError as exc:
            raise CtcValidationError(
                f"effective_date {effective_date!r} is not yyyy-mm-dd"
            ) from exc
        age = asof.year - born.year - ((asof.month, asof.day) < (born.month, born.day))
        if self.exit_age <= age:
            raise CtcValidationError(
                f"investor.exit_age {self.exit_age} is not in the future: the investor is "
                f"already {age} at {effective_date}. The engine would quote a maturity date "
                f"in the past."
            )
        if self.exit_age > MAX_EXIT_AGE:
            raise CtcValidationError(f"investor.exit_age {self.exit_age} is above {MAX_EXIT_AGE}")


def age_on(born: date, asof: date) -> int:
    """Age in whole years at ``asof``. The birthday itself counts."""
    return asof.year - born.year - ((asof.month, asof.day) < (born.month, born.day))


def projection_exit_age(
    *,
    retirement_age: int,
    born: date,
    effective: date,
    drawdown: bool,
) -> int:
    """The ``exit_age`` to send.

    A retirement age still ahead is the term, on either basis. Someone already past it can
    still be illustrated in drawdown — that is the ordinary case for a pension in payment —
    but the quote has to run to a date that has not happened. ``DRAWDOWN_HORIZON_AGE`` is that
    date. Accumulation to a retirement already reached has no term left.
    """
    age = age_on(born, effective)
    if retirement_age > age:
        return retirement_age
    if not drawdown:
        raise CtcValidationError(
            f"An accumulation illustration runs to retirement age {retirement_age}, "
            f"and this client is already {age}. A pension already in payment is a "
            "drawdown illustration."
        )
    horizon = max(DRAWDOWN_HORIZON_AGE, age + 1)
    if horizon > MAX_EXIT_AGE:
        raise CtcValidationError(
            f"This client is already {age}, and an illustration cannot run past age {MAX_EXIT_AGE}."
        )
    return horizon


@dataclass(slots=True)
class PlanStream:
    """One payment type on the product. ``key`` must be unique within the request."""

    key: str
    contribution_type: str
    amount: Decimal | float | int
    frequency: str
    crystallised: bool = False
    net_gross: str | None = None  # required for contribution_type "Member"
    start_date: str | None = None
    end_date: str | None = None

    def validate(self) -> None:
        _one_of("plan_stream.contribution_type", self.contribution_type, CONTRIBUTION_TYPES)
        _one_of("plan_stream.frequency", self.frequency, FREQUENCIES)
        if self.contribution_type in _ALWAYS_SINGLE and self.frequency != "Single":
            raise CtcValidationError(
                f"{self.contribution_type} streams must have frequency 'Single', "
                f"got {self.frequency!r}"
            )
        if self.crystallised and self.contribution_type not in _MAY_CRYSTALLISE:
            raise CtcValidationError(
                f"only {sorted(_MAY_CRYSTALLISE)} streams may be crystallised, "
                f"got {self.contribution_type!r}"
            )
        if self.contribution_type == "Member":
            if self.net_gross is None:
                raise CtcValidationError("net_gross is required for a 'Member' contribution")
            _one_of("plan_stream.net_gross", self.net_gross, frozenset({"Net", "Gross"}))


@dataclass(slots=True)
class FundSplit:
    """A fund within a premium split. Exactly one of percent/amount defines the split."""

    fund_ref: str
    fund_name: str | None = None
    split_per: Decimal | float | int | None = None
    split_amount: Decimal | float | int | None = None
    rate_of_return: Decimal | float | int | None = None
    management_charge: Decimal | float | int | None = None
    initial_charge: Decimal | float | int | None = None
    type: str | None = None  # 'COMMPROP' for commercial property

    def validate(self) -> None:
        if (self.split_per is None) == (self.split_amount is None):
            raise CtcValidationError(
                f"fund_split {self.fund_ref}: set exactly one of split_per or split_amount"
            )


@dataclass(slots=True)
class PremiumSplit:
    """How one or more streams are invested. Applies to every listed stream key."""

    stream_keys: list[str]
    funds: list[FundSplit]

    def validate(self) -> None:
        if not self.stream_keys:
            raise CtcValidationError("premium_split needs at least one stream key")
        if not self.funds:
            raise CtcValidationError("premium_split needs at least one fund_split")
        for fund in self.funds:
            fund.validate()
        # A percentage split that doesn't total 100 silently mis-projects, so refuse it here.
        percents = [f.split_per for f in self.funds if f.split_per is not None]
        if percents and len(percents) == len(self.funds):
            total = sum(Decimal(str(p)) for p in percents)
            if total != Decimal("100"):
                raise CtcValidationError(f"fund split percentages must total 100, got {total}")


@dataclass(slots=True)
class Withdrawals:
    """Regular income out of the product."""

    drawdown_type: str
    basis: str | None = None
    amount: Decimal | float | int | None = None
    percent: Decimal | float | int | None = None
    frequency: str | None = None
    start_date: str | None = None
    increase_percent: Decimal | float | int | None = None

    def validate(self) -> None:
        _one_of("withdrawals.drawdown_type", self.drawdown_type, DRAWDOWN_TYPES)
        if self.basis is not None:
            _one_of("withdrawals.basis", self.basis, WITHDRAWAL_BASES)
        if self.frequency is not None:
            _one_of("withdrawals.frequency", self.frequency, WITHDRAWAL_FREQUENCIES)
        if self.basis == "Percentage of Maximum GAD" and self.percent is None:
            raise CtcValidationError("percent is required when basis is a percentage of max GAD")


@dataclass(slots=True)
class Fee:
    """A product or adviser fee. Set exactly one of amount/percentage for a non-tiered charge."""

    fee_basis: str
    fee_frequency: str
    fee_timing: str
    fee_value_rate: str
    fee_name: str | None = None
    amount: Decimal | float | int | None = None
    percentage: Decimal | float | int | None = None
    plus_vat: bool | None = None
    start_type: str = "Immediately"
    start_date: str | None = None
    end_type: str = "Maturity"
    end_date: str | None = None
    stream_key: str | None = None  # adviser fees: required when fee_basis is 'Initial'

    def validate(self, *, adviser: bool = False) -> None:
        _one_of("fee.fee_basis", self.fee_basis, FEE_BASES)
        _one_of("fee.fee_timing", self.fee_timing, FEE_TIMINGS)
        _one_of("fee.fee_value_rate", self.fee_value_rate, FEE_VALUE_RATES)
        if self.fee_name is not None:
            _capped("fee.fee_name", self.fee_name, MAX_FEE_NAME)
        if (self.amount is None) == (self.percentage is None):
            raise CtcValidationError(
                f"fee {self.fee_name or ''!r}: set exactly one of amount or percentage"
            )
        if adviser and self.fee_basis == "Initial" and not self.stream_key:
            raise CtcValidationError("an Initial adviser fee must name the stream it attaches to")


@dataclass(slots=True)
class AnnuityBasis:
    """The annuity the projection converts to at retirement.

    **Mandatory for a pension quotation** (spec §i: "M If the quotation is for a Pension product
    otherwise O"), which is why ``Quotation.pension`` exists -- without it a SIPP illustration is
    silently short a required group.
    """

    annuity_frequency: str | None = None
    annuity_timing: str | None = None
    escalation: str | None = None
    percent: Decimal | float | int | None = None  # only when escalation is "Fixed"
    guaranteed_period: int | None = None
    spouse_percentage: Decimal | float | int | None = None

    def validate(self) -> None:
        if self.annuity_frequency is not None:
            _one_of("annuity.annuity_frequency", self.annuity_frequency, ANNUITY_FREQUENCIES)
        if self.annuity_timing is not None:
            _one_of("annuity.annuity_timing", self.annuity_timing, ANNUITY_TIMINGS)
        if self.escalation is not None:
            _one_of("annuity.escalation", self.escalation, ESCALATIONS)
        if self.escalation == "Fixed" and self.percent is None:
            raise CtcValidationError("annuity escalation 'Fixed' needs a percent")
        if self.escalation != "Fixed" and self.percent is not None:
            raise CtcValidationError("annuity percent applies only to 'Fixed' escalation")


@dataclass(slots=True)
class Quotation:
    """One product within the request."""

    product_ref: str
    quotation_type: str
    product_type: str | None = None
    plan_streams: list[PlanStream] = field(default_factory=list)
    premium_splits: list[PremiumSplit] = field(default_factory=list)
    withdrawals: Withdrawals | None = None
    product_fees: list[Fee] = field(default_factory=list)
    adviser_fees: list[Fee] = field(default_factory=list)
    annuity_basis: AnnuityBasis | None = None
    inflation_adjustment: bool | None = None
    pension: bool = False
    """Marks a pension product. Does NOT force ``annuity_basis``, deliberately.

    Spec §i marks the group "M If the quotation is for a Pension product otherwise O", and this
    class used to enforce that. The engine does not: omitting the group is accepted and yields
    an equivalent document with the projected-income column left at zero, which is a legitimate
    thing to ask for (docs/morgan-lloyd/illustrations-report.md §10 has the measurement, and
    CTC's own Drawdown example omits it). Enforcing it here only blocked requests the engine
    was happy to answer. The flag is kept because callers branch on it.
    """

    def validate(self) -> None:
        if not self.plan_streams:
            raise CtcValidationError(f"quotation {self.product_ref} has no plan_stream")
        if self.annuity_basis is not None:
            self.annuity_basis.validate()
        keys = [s.key for s in self.plan_streams]
        if len(set(keys)) != len(keys):
            raise CtcValidationError("plan_stream_key values must be unique within the request")
        for stream in self.plan_streams:
            stream.validate()
        for split in self.premium_splits:
            split.validate()
            unknown = set(split.stream_keys) - set(keys)
            if unknown:
                raise CtcValidationError(
                    f"premium_split references unknown stream keys: {sorted(unknown)}"
                )
        if self.withdrawals is not None:
            self.withdrawals.validate()
        for fee in self.product_fees:
            fee.validate()
        for fee in self.adviser_fees:
            fee.validate(adviser=True)


@dataclass(slots=True)
class IllustrationRequest:
    """A complete CTC1 request."""

    effective_date: str  # yyyy-mm-dd
    created_date: str  # yyyy-MM-dd HH:mm:ss — passed in, never clocked here
    investors: list[Investor]
    quotations: list[Quotation]
    output_required: bool = True
    output_reference: str | None = None
    agent_name: str | None = None
    client_type: str | None = None
    client_name: str | None = None  # only when client_type is Company or Trust

    def validate(self) -> None:
        if not self.investors:
            raise CtcValidationError("at least one investor is required")
        if not self.quotations:
            raise CtcValidationError("at least one quotation is required")
        if self.output_reference is not None:
            _capped("output_reference", self.output_reference, MAX_OUTPUT_REFERENCE)
        if self.agent_name is not None:
            _capped("agent_name", self.agent_name, MAX_AGENT_NAME)
        if self.client_type is not None:
            _one_of("client_type", self.client_type, CLIENT_TYPES)
            if self.client_type in {"Company", "Trust"} and not self.client_name:
                raise CtcValidationError(f"a {self.client_type} client needs a <name>")
        for investor in self.investors:
            investor.validate(self.effective_date)
        for quotation in self.quotations:
            quotation.validate()


def _append_investor(parent: ET.Element, investor: Investor) -> None:
    node = _child(parent, "investor")
    _opt(node, "title", investor.title)
    _opt(node, "forename", investor.forename)
    _opt(node, "surname", investor.surname)
    _opt(node, "sex", investor.sex)
    _opt(node, "life_type", investor.life_type)
    _opt(node, "marital_status", investor.marital_status)
    _opt(node, "date_of_birth", investor.date_of_birth)
    _opt(node, "exit_age", None if investor.exit_age is None else str(investor.exit_age))


def _append_plan_stream(parent: ET.Element, stream: PlanStream) -> None:
    node = _child(parent, "plan_stream", plan_stream_key=stream.key)
    _child(node, "contribution_type", stream.contribution_type)
    _opt(node, "net_gross", stream.net_gross)
    _child(node, "amount", _money(stream.amount))
    _child(node, "frequency", stream.frequency)
    _child(node, "crystallised", "Yes" if stream.crystallised else "No")
    if stream.start_date:
        start = _child(node, "plan_stream_start")
        _child(start, "start_type", "Specified Date")
        _child(start, "start_date", stream.start_date)
    if stream.end_date:
        end = _child(node, "plan_stream_end")
        _child(end, "end_type", "Specified Date")
        _child(end, "end_date", stream.end_date)


def _append_premium_split(parent: ET.Element, split: PremiumSplit) -> None:
    node = _child(parent, "premium_split")
    for key in split.stream_keys:
        _child(node, "stream_applicable", plan_stream_key=key)
    for fund in split.funds:
        fund_node = _child(node, "fund_split", type=fund.type)
        _child(fund_node, "fund_ref", fund.fund_ref)
        _opt(fund_node, "fund_name", fund.fund_name)
        if fund.split_per is not None:
            _child(fund_node, "split_per", _rate(fund.split_per))
        if fund.split_amount is not None:
            _child(fund_node, "split_amount", _money(fund.split_amount))
        _opt(
            fund_node,
            "rate_of_return",
            None if fund.rate_of_return is None else _rate(fund.rate_of_return),
        )
        _opt(
            fund_node,
            "management_charge",
            None if fund.management_charge is None else _rate(fund.management_charge),
        )
        _opt(
            fund_node,
            "initial_charge",
            None if fund.initial_charge is None else _rate(fund.initial_charge),
        )
        # generic_fund is 'Yes' when the split carries its own return/charge assumptions.
        if fund.rate_of_return is not None or fund.management_charge is not None:
            _child(fund_node, "generic_fund", "Yes")


def _append_withdrawals(parent: ET.Element, withdrawals: Withdrawals) -> None:
    node = _child(parent, "withdrawals")
    _opt(node, "basis", withdrawals.basis)
    _opt(node, "amount", None if withdrawals.amount is None else _money(withdrawals.amount))
    _opt(node, "percent", None if withdrawals.percent is None else _rate(withdrawals.percent))
    _opt(node, "frequency", withdrawals.frequency)
    _opt(node, "start_date", withdrawals.start_date)
    if withdrawals.increase_percent is not None:
        increases = _child(node, "increases")
        _child(increases, "percent", _rate(withdrawals.increase_percent))
    _child(node, "drawdown_type", withdrawals.drawdown_type)


def _append_fee(parent: ET.Element, fee: Fee) -> None:
    node = _child(parent, "adviser_fee" if parent.tag == "adviser_fees" else "product_fee")
    if fee.stream_key:
        _child(node, "stream_applicable", plan_stream_key=fee.stream_key)
    _opt(node, "fee_name", fee.fee_name)
    _child(node, "fee_basis", fee.fee_basis)
    _child(node, "fee_frequency", fee.fee_frequency)
    _child(node, "fee_timing", fee.fee_timing)
    _child(node, "fee_value_rate", fee.fee_value_rate)
    start = _child(node, "fee_start")
    _child(start, "start_type", fee.start_type)
    if fee.start_type == "Specified Date" and fee.start_date:
        _child(start, "start_date", fee.start_date)
    end = _child(node, "fee_end")
    _child(end, "end_type", fee.end_type)
    if fee.end_type == "Specified Date" and fee.end_date:
        _child(end, "end_date", fee.end_date)
    _opt(node, "amount", None if fee.amount is None else _money(fee.amount))
    _opt(node, "percentage", None if fee.percentage is None else _rate(fee.percentage))
    if fee.plus_vat is not None:
        _child(node, "plus_vat", "Yes" if fee.plus_vat else "No")


def _append_quotation(parent: ET.Element, quotation: Quotation) -> None:
    node = _child(
        parent,
        "quotation",
        product_ref=quotation.product_ref,
        product_type=quotation.product_type,
    )
    _child(node, "quotation_type", quotation.quotation_type)
    if quotation.inflation_adjustment is not None:
        _child(
            node,
            "inflation_adjustment_indicator",
            "Yes" if quotation.inflation_adjustment else "No",
        )
    for stream in quotation.plan_streams:
        _append_plan_stream(node, stream)
    for split in quotation.premium_splits:
        _append_premium_split(node, split)
    if quotation.withdrawals is not None:
        _append_withdrawals(node, quotation.withdrawals)
    if quotation.product_fees:
        fees = _child(node, "product_fees")
        for fee in quotation.product_fees:
            _append_fee(fees, fee)
    if quotation.adviser_fees:
        fees = _child(node, "adviser_fees")
        for fee in quotation.adviser_fees:
            _append_fee(fees, fee)
    if quotation.annuity_basis is not None:
        _append_annuity(node, quotation.annuity_basis)


def _append_annuity(parent: ET.Element, annuity: AnnuityBasis) -> None:
    """Emit the annuity group.

    NAME DISCREPANCY: the spec's outline and its §i table both call this ``<annuity_basis>``,
    while both of §i's worked examples show ``<annuity>``. Two sources to one, so we emit
    ``<annuity_basis>`` -- but this is the single most likely thing to need flipping after the
    first live call, so it is isolated here.
    """
    node = _child(parent, "annuity_basis")
    _opt(node, "annuity_frequency", annuity.annuity_frequency)
    _opt(node, "annuity_timing", annuity.annuity_timing)
    _opt(node, "escalation", annuity.escalation)
    _opt(node, "percent", None if annuity.percent is None else _rate(annuity.percent))
    _opt(
        node,
        "guaranteed_period",
        None if annuity.guaranteed_period is None else str(annuity.guaranteed_period),
    )
    _opt(
        node,
        "spouse_percentage",
        None if annuity.spouse_percentage is None else _rate(annuity.spouse_percentage),
    )


def build_request(request: IllustrationRequest) -> str:
    """Validate and serialise a CTC1 request. Raises ``CtcValidationError`` on a bad request."""
    request.validate()

    message = ET.Element("message")
    header = _child(message, "header")
    _child(header, "message_version", MESSAGE_VERSION)
    _child(header, "created_date", request.created_date)
    _child(header, "mode", "Request")

    content = _child(message, "content")
    quotations = _child(content, "quotations")
    _child(quotations, "effective_date", request.effective_date)
    _child(quotations, "output_required", "Yes" if request.output_required else "No")
    _opt(quotations, "output_reference", request.output_reference)
    _opt(quotations, "agent_name", request.agent_name)
    _opt(quotations, "client_type", request.client_type)
    _opt(quotations, "name", request.client_name)

    for investor in request.investors:
        _append_investor(quotations, investor)
    for quotation in request.quotations:
        _append_quotation(quotations, quotation)

    return ET.tostring(message, encoding="unicode", xml_declaration=True)


@dataclass(slots=True)
class IllustrationResponse:
    """Parsed CTC response. ``document_location`` names the generated PDF."""

    success: bool
    document_location: str | None
    errors: list[str]


def parse_response(xml: str) -> IllustrationResponse:
    """Read the engine's reply. A malformed body is reported as a failure, not raised, so the
    caller can surface the engine's own text rather than a parser traceback."""
    try:
        root = ET.fromstring(xml)  # noqa: S314 - vendor response, not attacker-controlled
    except ET.ParseError as exc:
        return IllustrationResponse(False, None, [f"Unreadable response from CTC: {exc}"])

    response = root.findtext("./header/response", default="").strip().upper()
    errors = [
        (e.text or "").strip()
        for e in root.findall("./header/errors/error")
        if (e.text or "").strip()
    ]
    location = root.findtext(".//results/document_location")
    location = location.strip() if location else None
    # Trust the explicit status when present; otherwise infer from the absence of errors.
    success = response == "SUCCESS" if response else not errors
    return IllustrationResponse(success and not errors, location, errors)
