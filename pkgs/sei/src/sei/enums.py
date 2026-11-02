"""SWP code sets, transcribed verbatim from SEI's own Swagger descriptions.

Every one of these is a **string on the wire** even though the values are digits — SEI types
the fields ``string`` throughout, and sending a JSON number is rejected. So they are
``StrEnum``s: the member value is exactly what goes in the payload.

Codes that are *firm-configured* rather than fixed by SEI are deliberately absent — chiefly
``descriptionTypeCode`` and ``treatmentTypeCode``, which are per-firm reference data SEI
provisions and validates per transaction type and user role. There is no lookup service for
them in the published spec set; they must be supplied as configuration.
"""

from __future__ import annotations

from enum import StrEnum


class ReceiptMethod(StrEnum):
    """``receiptInstructions.receiptMethod`` — "The code associated with payment method in
    cash receipt (ACH=1, Wire=2, CHECK=3, BACS=5, CHAPS via SWIFT = 6, SWIFT = 7)"."""

    ACH = "1"
    WIRE = "2"
    CHECK = "3"
    BACS = "5"
    CHAPS_VIA_SWIFT = "6"
    SWIFT = "7"


class BacsReceiptType(StrEnum):
    """``receiptInstructions.receiptType`` when the method is BACS — "For BACS[Direct Debit
    =2 , Direct Credit = 1]".

    Kept separate from :class:`AchReceiptType` because the same field carries a different
    meaning per method: ``"2"`` is a BACS Direct Debit but an ACH Direct Payment.
    """

    DIRECT_CREDIT = "1"
    DIRECT_DEBIT = "2"


class AchReceiptType(StrEnum):
    """``receiptInstructions.receiptType`` when the method is ACH (US) — "For ACH[Direct
    Deposit = 1, Direct Payment = 2]". See :class:`BacsReceiptType` on the overload."""

    DIRECT_DEPOSIT = "1"
    DIRECT_PAYMENT = "2"


class ReceiptAmountType(StrEnum):
    """``receiptInstructions.receiptAmountType`` — "Receipt amount type can be
    'Gross Value = 1, Unknown = 4'".

    ``UNKNOWN`` is the one that matters for an anticipated receipt where only the fact of the
    payment is known, not its size. Note SEI ignores any fee override when it is used.
    """

    GROSS_VALUE = "1"
    UNKNOWN = "4"


class TxnSuppression(StrEnum):
    """``receiptInstructions.txnSuppressionFor`` — "1 - No suppression, 2 - Statements,
    3 - Both Statements and Contract Notes/Advices, 4 - Contract Notes/Advices".

    Only honoured for ad-hoc transactions; SEI warns (2196) and ignores it otherwise.
    """

    NONE = "1"
    STATEMENTS = "2"
    STATEMENTS_AND_ADVICES = "3"
    ADVICES = "4"


class DayType(StrEnum):
    """``dayType`` on the daily/monthly recurrence — "Indicator for calendar (1 = Day) or
    business (2 = Business) days"."""

    CALENDAR = "1"
    BUSINESS = "2"


class WeekDay(StrEnum):
    """``weekly.weekDay`` — "1 = Sunday, 2 = Monday, ..." through to Saturday."""

    SUNDAY = "1"
    MONDAY = "2"
    TUESDAY = "3"
    WEDNESDAY = "4"
    THURSDAY = "5"
    FRIDAY = "6"
    SATURDAY = "7"


class ResolveOption(StrEnum):
    """``monthly.resolveOption`` — "either '1' or '2', with 1 being prior and 2 being next".
    Decides which way a non-business due date moves."""

    PRIOR = "1"
    NEXT = "2"


class RelatedSecurityIdentifier(StrEnum):
    """``relatedSecurityDetails.relatedSecurityIdentifier`` — "Identifies security instruments
    (2=ISIN, 4= Ticker, 5=CUSIP, 11=SEDOL, 22=Firm_Asset_Id)"."""

    ISIN = "2"
    TICKER = "4"
    CUSIP = "5"
    SEDOL = "11"
    FIRM_ASSET_ID = "22"


#: SEI error codes worth naming, from the spec's own error table. Used to turn a rejected
#: payload into an actionable message instead of "HTTP 400".
ERROR_CODES: dict[str, str] = {
    "1001": "The firm is invalid or missing.",
    "1002": "The account is invalid.",
    "1006": "Invalid input — commonly the description or treatment code.",
    "1016": "The currency code is invalid.",
    "2022": "Amount type must be valid for the firm.",
    "2024": "The processing/due date is not a valid business date.",
    "2044": "Payer details invalid.",
    "2127": "Amount is outside the configured minimum/maximum range.",
    "380145": "Not entitled — the user lacks the required function point.",
    "100010": "SEI encountered an unexpected error.",
}
