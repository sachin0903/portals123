"""Typed payloads for the cash-receipt service.

Transcribed from SEI's ``swagger-spec-CreateOrMaintainCashReceipts.yaml`` (vendored in the
monitoring repo under ``apps/api/app/resources/sei/openapi/``). Only the request shapes we
send and the response shape we read are modelled — the spec also covers payer profiles, fee
overrides and recurring schedules that the current flow does not use, and those are left out
rather than modelled speculatively.

Two conventions from the spec are preserved deliberately:

- **Every scalar is a string**, including amounts and the numeric code sets. SEI types the
  whole payload ``string``; a JSON number is rejected. :func:`money` does the conversion.
- **Dates are ``YYYY-MM-DD``.** The field descriptions say ``[YYYY/MM/DD]`` but every example
  in the spec uses hyphens, and hyphens are what the service accepts.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sei.enums import DayType, ReceiptAmountType, ReceiptMethod, ResolveOption, WeekDay


def money(value: Decimal | float | int | str) -> str:
    """Format an amount the way SEI wants it: a plain decimal string, 2dp, no separators.

    ``Decimal`` is normalised through ``quantize`` rather than ``str()`` because
    ``Decimal("60").normalize()`` renders as ``6E+1`` — the same trap the CTC XML builder
    hit (see ``app/integrations/ctc/xml_builder.py``).
    """
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class _Payload(BaseModel):
    """Base: reject unknown fields (a typo in a SEI key is silently ignored by the service,
    which is worse than a validation error) and omit unset ones from the wire payload."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Once(_Payload):
    """``processingScheduleDetails.once`` — a single, scheduled movement.

    For an **anticipated cash receipt** this is the whole schedule: ``dueDate`` is the date
    by which the money is expected. SEI rejects a past date (error 2024).
    """

    due_date: str | None = Field(default=None, alias="dueDate")
    beneficiary_value_date: str | None = Field(default=None, alias="beneficiaryValueDate")

    @classmethod
    def on(cls, due: date) -> Once:
        return cls(due_date=due.isoformat())


class Daily(_Payload):
    """``recurring.daily`` — every ``interval`` calendar or business days."""

    interval: str
    day_type: DayType | str = Field(alias="dayType")


class Weekly(_Payload):
    """``recurring.weekly`` — every ``interval`` weeks on ``weekDay``."""

    interval: str
    week_day: WeekDay | str = Field(alias="weekDay")


class Monthly(_Payload):
    """``recurring.monthly`` — every ``interval`` months.

    Two ways to name the day and they are not interchangeable: ``monthDayNum`` with
    ``dayType`` is "the 6th calendar day" / "the 6th business day", while ``monthDayType``
    with ``monthlyOccurrence`` is "the second Tuesday". ``resolveOption`` only applies to the
    first form and decides which way a non-business date moves.
    """

    interval: str
    day_type: DayType | str | None = Field(default=None, alias="dayType")
    month_day_num: str | None = Field(default=None, alias="monthDayNum")
    resolve_option: ResolveOption | str | None = Field(default=None, alias="resolveOption")
    month_day_type: str | None = Field(default=None, alias="monthDayType")
    monthly_occurrence: str | None = Field(default=None, alias="monthlyOccurrence")


class Recurring(_Payload):
    """``processingScheduleDetails.recurring`` — a standing instruction.

    Exactly one of ``daily``/``weekly``/``monthly`` carries the period; ``RECURRENCE_FREQUENCY``
    in the nightly Reference report confirms SEI offers only those three (there is no
    quarterly or annual — a quarter is ``monthly`` with ``interval`` 3).

    ``endAfterCount`` and ``endByDate`` are the two end conditions and are mutually exclusive;
    supplying neither means "until cancelled", which is a real and common answer. The
    cancellation is :meth:`sei.SeiClient.cancel_activity`, not a field here.
    """

    start_date: str = Field(alias="startDate")
    daily: Daily | None = None
    weekly: Weekly | None = None
    monthly: Monthly | None = None
    end_after_count: str | None = Field(default=None, alias="endAfterCount")
    end_by_date: str | None = Field(default=None, alias="endByDate")

    @classmethod
    def monthly_on(
        cls, start: date, *, day_of_month: int, interval: int = 1, business_days: bool = False
    ) -> Recurring:
        """The shape a monthly standing order actually takes: a calendar day of the month,
        resolved BACKWARDS when it is not a business day.

        ``PRIOR`` rather than ``NEXT`` is the default deliberately — a contribution dated the
        31st that resolves forward lands in the following month and is then counted against
        the wrong tax year at the wrong allowance.
        """
        return cls(
            start_date=start.isoformat(),
            monthly=Monthly(
                interval=str(interval),
                day_type=DayType.BUSINESS if business_days else DayType.CALENDAR,
                month_day_num=str(day_of_month),
                resolve_option=ResolveOption.PRIOR,
            ),
        )


class ProcessingScheduleDetails(_Payload):
    """``processingScheduleDetails`` — exactly one of ``once`` or ``recurring``.

    Sending both is not a richer instruction, it is an ambiguous one, so the two are kept
    separate rather than merged: :meth:`sei.SeiClient.create_currency_receipt` takes whichever
    the caller built.
    """

    once: Once | None = None
    recurring: Recurring | None = None


class FeeOverride(_Payload):
    """One ``feesAndCharges`` entry on a currency receipt.

    ``overridenFeeAmount`` is SEI's spelling. ``feeAmountType`` ``1`` is gross value.
    ``feeDescriptionId`` is the firm's fee type (Reference ``FEE_TYPE``), not a
    transaction description code.
    """

    fee_description_id: str = Field(alias="feeDescriptionId")
    fee_amount_type: str = Field(default="1", alias="feeAmountType")
    overriden_fee_amount: str = Field(alias="overridenFeeAmount")
    notes: str | None = None


class ReceiptInstructions(_Payload):
    """``receiptInstructions`` — what the money is and how it arrives.

    ``descriptionTypeCode`` and ``treatmentTypeCode`` are **firm-configured reference data**,
    not fixed codes: the description says what the transaction is for, the treatment says how
    it is taxed, and SEI validates the pair against the transaction type and the caller's
    role. They have to be supplied per firm — there is no lookup endpoint in the spec set.
    """

    currency_code: str = Field(alias="currencyCode")
    receipt_method: ReceiptMethod | str = Field(alias="receiptMethod")
    receipt_type: str | None = Field(default=None, alias="receiptType")
    description_type_code: str = Field(alias="descriptionTypeCode")
    treatment_type_code: str = Field(alias="treatmentTypeCode")
    treatment_code_tax_year: str | None = Field(default=None, alias="treatmentCodeTaxYear")
    receipt_amount: str = Field(alias="receiptAmount")
    receipt_amount_type: ReceiptAmountType | str | None = Field(
        default=None, alias="receiptAmountType"
    )
    # ISA/JISA only, and only for treatment codes configured to take a contribution amount.
    isa_contribution: str | None = Field(default=None, alias="isaContribution")
    external_reference: str | None = Field(default=None, alias="externalReference")
    external_system_txn_id: str | None = Field(default=None, alias="externalSystemTxnId")
    additional_notes: str | None = Field(default=None, alias="additionalNotes")
    invest_cash: str | None = Field(default=None, alias="investCash")
    eligible_for_invest_cash_scheduler: str | None = Field(
        default=None, alias="eligibleForInvestCashScheduler"
    )
    # A memo receipt (Select's working example: cheque method, memoFlag true) never collects,
    # so it needs no payer. officialCheck and checkReceivedFlag are the cheque-method pair;
    # SEI ignores receiptType for that method and warns 2196.
    official_check: str | None = Field(default=None, alias="officialCheck")
    check_received_flag: str | None = Field(default=None, alias="checkReceivedFlag")
    memo_flag: str | None = Field(default=None, alias="memoFlag")
    # true bypasses the initiated-approval step; SEI warns (1145) either way so the caller
    # can see which happened.
    bypass_approval_required: str | None = Field(default=None, alias="byPassApprovalRequired")
    # An adviser fee taken from this receipt. Omitted when the payment has none.
    fees_and_charges: list[FeeOverride] | None = Field(default=None, alias="feesAndCharges")


class Attributes(_Payload):
    """``data[].attributes`` — the receipt itself.

    ``firmId`` is documented as "Required if caller is at the Processing Organization level".
    Graphene's SEI users are PO-level (e.g. ``i31pographeneapi50``), so in practice it is
    required and its absence returns error 1001.
    """

    account_number: str = Field(alias="accountNumber")
    portfolio_id: str = Field(alias="portfolioId")
    firm_id: str | None = Field(default=None, alias="firmId")
    receipt_instructions: ReceiptInstructions = Field(alias="receiptInstructions")
    processing_schedule_details: ProcessingScheduleDetails | None = Field(
        default=None, alias="processingScheduleDetails"
    )


class Data(_Payload):
    # The request type. ``CreateCurrencyReceipt`` is the *response* type (``SuccessData``);
    # sending it as the request type is what the spec's own example does not do.
    type: str = "CurrencyReceiptRequest"
    attributes: Attributes


class CurrencyReceiptRequest(_Payload):
    """The ``POST /currencyreceiptservices/createCurrencyReceipt`` body."""

    data: list[Data]

    @classmethod
    def single(cls, attributes: Attributes) -> CurrencyReceiptRequest:
        return cls(data=[Data(attributes=attributes)])

    def to_payload(self) -> dict[str, Any]:
        """Serialise for the wire: SEI aliases, unset fields dropped."""
        return self.model_dump(by_alias=True, exclude_none=True)


# --- responses ------------------------------------------------------------------


class ReceiptWarning(BaseModel):
    """One entry of ``attributes.warnings``. SEI returns HTTP 200 with warnings for things it
    accepted but changed (approval required, a fee override ignored), so they must be read —
    a 200 is not proof the receipt went in exactly as sent."""

    model_config = ConfigDict(extra="allow")

    code: str | None = None
    message: str | None = None


class ReceiptResult(BaseModel):
    """``data[0].attributes`` of a successful create."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    activity_id: str | None = Field(default=None, alias="activityId")
    event_id: str | None = Field(default=None, alias="eventId")
    receipt_amount: str | None = Field(default=None, alias="receiptAmount")
    beneficiary_value_date: str | None = Field(default=None, alias="beneficiaryValueDate")
    invest_cash_activity_id: str | None = Field(default=None, alias="investCashActivityId")
    warnings: list[ReceiptWarning] = Field(default_factory=list)

    @classmethod
    def from_body(cls, body: Mapping[str, Any]) -> ReceiptResult:
        """Pull the first ``data[]`` entry's attributes out of a success response."""
        rows = body.get("data")
        first = rows[0] if isinstance(rows, list) and rows else {}
        attributes = first.get("attributes") if isinstance(first, dict) else None
        return cls.model_validate(attributes if isinstance(attributes, dict) else {})
