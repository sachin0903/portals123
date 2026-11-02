"""SEI reference codes for the contribution rail — read from the nightly Reference report.

``docs/contributions.md`` §4 recorded ``descriptionTypeCode`` and ``treatmentTypeCode`` as the
key blocker, on the grounds that "no lookup endpoint exists in the published spec set". That is
true of the **API** and false of the **file drop**: the nightly ``Reference`` report carries
both code sets in full (``TYPE = 'TREATMENT_CODE'`` — 758 rows, and
``TYPE = 'DESCRIPTION_CODE_ID'`` — 6,108 rows), and the UK relief-at-source pair is in there.
The values below are read from the p04 production drop, not inferred.

**The two sides.** They are the same six concepts, split across the write path and the read
path, which is why both are needed and why they are listed together:

| Concept                              | treatment (write) | description (read) |
| ------------------------------------ | ----------------- | ------------------ |
| Member Net Contribution              | 4958              | 21121              |
| Member Basic Rate Tax Relief         | 4959              | 21122              |
| Employee Gross Contribution          | 4960              | 21123              |
| Employer Gross Contribution          | 4961              | 21124              |
| Employee Net Contribution            | 4962              | 21125              |
| Employee Basic Rate Tax Relief       | 4963              | 21126              |

``treatmentTypeCode`` goes **out** on ``createCurrencyReceipt`` and encodes personal-vs-employer
at SEI. ``DESCRIPTION_CODE_ID`` comes **back** on every ``TransactionHeader`` row, and is the
only thing separating a contribution from the relief payment that follows it — both arrive as
transaction type 6 (Cash Receipt) for the same account, often within days. Without it the
reconciliation counts the relief as a second contribution.

**These are not defaults.** ``resolve_codes`` still reads the tenant's own provider config and
refuses when it is unset, because SEI validates the description/treatment pair against the
transaction type *and* the caller's role, and a wrong pair returns error 1006 — a rejection at
the custodian, on a client's pension. What changes here is that the refusal can now say which
code the operator should configure instead of only that one is missing. The TREATMENT_CODE rows
carry a blank FIRM_ID (platform-wide), while DESCRIPTION_CODE_ID is firm-scoped and was present
for every firm in the corpus (3102, 3106, 3108, 3110, 3112, 3114) — so the suggestion is well
founded but still a suggestion.
"""

from __future__ import annotations

import re
from typing import Any

#: ``TYPE = 'TRANSACTION_TYPE'`` in the Reference report. The full set is 124 codes; these are
#: the ones the money flow turns on. The canonical mapping for all of them lives in
#: ``services/sei/models/platform/transactions.sql`` — this is the API-side half, used to read
#: what already landed rather than to classify it.
TRANSACTION_TYPE_CASH_RECEIPT = "6"
TRANSACTION_TYPE_CASH_DISBURSEMENT = "7"

#: ``TYPE = 'TREATMENT_CODE'``, sent as ``treatmentTypeCode``. Platform-wide (blank FIRM_ID).
TREATMENT_MEMBER_NET = "4958"
TREATMENT_MEMBER_BASIC_RATE_RELIEF = "4959"
TREATMENT_EMPLOYEE_GROSS = "4960"
TREATMENT_EMPLOYER_GROSS = "4961"
TREATMENT_EMPLOYEE_NET = "4962"
TREATMENT_EMPLOYEE_BASIC_RATE_RELIEF = "4963"
TREATMENT_OTHER_CONTRIBUTION = "4437"

#: ``TYPE = 'DESCRIPTION_CODE_ID'``, echoed on TransactionHeader as ``DESCRIPTION_CODE_ID``.
DESCRIPTION_CONTRIBUTION = "20009"
DESCRIPTION_TRANSFER_IN = "20012"
DESCRIPTION_WITHDRAWAL = "20317"
DESCRIPTION_CASH_DISBURSEMENT = "20023"

#: ``TREATMENT_CODE``. Platform-wide (blank FIRM_ID). An ISA subscription and a cash
#: withdrawal are not a pension's personal-versus-employer classification.
TREATMENT_ISA_SUBSCRIPTION = "1401"
TREATMENT_ADDITION = "1451"
TREATMENT_ISA_WITHDRAWAL = "2410"
TREATMENT_WITHDRAWAL = "2403"

#: Wrappers whose money in is a subscription, not a pension contribution.
_SUBSCRIPTION = frozenset({"ISA", "JISA"})
_PENSION = frozenset({"SIPP", "PENSION", "SSAS", "TPPENSION"})

#: ``createCurrencyReceipt`` ``portfolioId`` is at most nine digits (example ``12085274``).
#: Enrolment reports a global id, ``{firm}-SWP-{env}-{entity}``.
_PORTFOLIO_ENTITY = re.compile(r"^[0-9]{1,9}$")
_PORTFOLIO_GLOBAL = re.compile(r"^[0-9]+-SWP-[A-Za-z0-9]+-([0-9]{1,9})$")
DESCRIPTION_MEMBER_NET = "21121"
DESCRIPTION_MEMBER_BASIC_RATE_RELIEF = "21122"
DESCRIPTION_EMPLOYEE_GROSS = "21123"
DESCRIPTION_EMPLOYER_GROSS = "21124"
DESCRIPTION_EMPLOYEE_NET = "21125"
DESCRIPTION_EMPLOYEE_BASIC_RATE_RELIEF = "21126"

#: Graphene category -> the treatment code the Reference report publishes for it. Used to tell
#: an operator what to configure, NOT to fill the gap silently.
SUGGESTED_TREATMENT_CODE: dict[str, str] = {
    "personal_contribution": TREATMENT_MEMBER_NET,
    "employer_contribution": TREATMENT_EMPLOYER_GROSS,
    "third_party_contribution": TREATMENT_OTHER_CONTRIBUTION,
}

#: The description code SEI stamps on a contribution credit, by category. The generic 20009
#: ("Contribution") is the fallback a firm using the unsplit code set will see.
SUGGESTED_DESCRIPTION_CODE: dict[str, str] = {
    "personal_contribution": DESCRIPTION_MEMBER_NET,
    "employer_contribution": DESCRIPTION_EMPLOYER_GROSS,
    "third_party_contribution": DESCRIPTION_CONTRIBUTION,
}

#: Description codes that mean "this credit is basic-rate relief the scheme reclaimed", i.e.
#: the ``sei (relief payment)`` step. Both spellings exist because SEI splits member-paid from
#: employee-paid, and a firm may use either depending on how the scheme is set up.
RELIEF_DESCRIPTION_CODES: frozenset[str] = frozenset(
    {DESCRIPTION_MEMBER_BASIC_RATE_RELIEF, DESCRIPTION_EMPLOYEE_BASIC_RATE_RELIEF}
)

#: Description codes that mean "this credit is a contribution", i.e. the thing an adviser
#: classifies. Deliberately narrow: an unrecognised code still reaches the workbench queue as a
#: plain credit, it just carries no suggestion.
CONTRIBUTION_DESCRIPTION_CODES: frozenset[str] = frozenset(
    {
        DESCRIPTION_CONTRIBUTION,
        DESCRIPTION_MEMBER_NET,
        DESCRIPTION_EMPLOYEE_GROSS,
        DESCRIPTION_EMPLOYER_GROSS,
        DESCRIPTION_EMPLOYEE_NET,
    }
)

#: description code -> the Graphene category it implies. Drives the workbench's pre-selection
#: only; the adviser's choice is what is stored, because SEI's code says how the money was
#: labelled at the custodian and the HMRC decision is ours.
CATEGORY_FOR_DESCRIPTION: dict[str, str] = {
    DESCRIPTION_MEMBER_NET: "personal_contribution",
    DESCRIPTION_EMPLOYEE_NET: "personal_contribution",
    DESCRIPTION_EMPLOYEE_GROSS: "personal_contribution",
    DESCRIPTION_EMPLOYER_GROSS: "employer_contribution",
    DESCRIPTION_TRANSFER_IN: "transfer_in",
}

#: Human labels, verbatim from the Reference report's DESCRIPTION column.
DESCRIPTION_LABELS: dict[str, str] = {
    DESCRIPTION_CONTRIBUTION: "Contribution",
    DESCRIPTION_TRANSFER_IN: "Transfer In",
    DESCRIPTION_MEMBER_NET: "Funding Transaction Member Net Contribution",
    DESCRIPTION_MEMBER_BASIC_RATE_RELIEF: "Funding Member Basic Rate Tax Relief on Contribution",
    DESCRIPTION_EMPLOYEE_GROSS: "Funding Transaction Employee Gross Contribution",
    DESCRIPTION_EMPLOYER_GROSS: "Funding Transaction Employer Gross Contribution",
    DESCRIPTION_EMPLOYEE_NET: "Funding Transaction Employee Net Contribution",
    DESCRIPTION_EMPLOYEE_BASIC_RATE_RELIEF: (
        "Funding Employee Basic Rate Tax Relief on Contribution"
    ),
}


def _code(value: Any) -> str:
    """SEI codes are text with meaningful leading zeros elsewhere in the feed, and arrive here
    as whatever the lake typed them. Normalise to the string form the constants use."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def is_relief_payment(transaction: dict[str, Any]) -> bool:
    """True when this credit is the basic-rate relief SEI paid in, not a contribution.

    Reads ``source_subtype`` — the conformed name for ``DESCRIPTION_CODE_ID``. Falls back to the
    narrative only when the code is absent, because a firm on the unsplit code set (20009 for
    everything) has nothing else to go on, and treating relief as a fresh contribution
    double-counts the member's annual allowance.
    """
    code = _code(transaction.get("source_subtype"))
    if code:
        return code in RELIEF_DESCRIPTION_CODES
    text = " ".join(
        str(transaction.get(field) or "") for field in ("source_type", "description", "narrative")
    ).lower()
    return "tax relief" in text or "basic rate" in text


def suggested_category(transaction: dict[str, Any]) -> str | None:
    """The category SEI's own description code implies, or None when it says nothing useful."""
    return CATEGORY_FOR_DESCRIPTION.get(_code(transaction.get("source_subtype")))


def describe(transaction: dict[str, Any]) -> str | None:
    """SEI's label for this credit's description code, for the workbench's provenance line."""
    return DESCRIPTION_LABELS.get(_code(transaction.get("source_subtype")))


def portfolio_entity_id(value: object) -> str | None:
    """The numeric portfolio id a cash call takes.

    Enrolment's ``globalIdOfPortfolio`` is ``3108-SWP-I31-59103919``. Sending that
    string is error 1006, "Portfolio ID format is not valid": the receipt and the
    disbursement want the trailing entity id, at most nine digits. A value that is
    already that id is unchanged. Anything else is left for the caller.
    """
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    if _PORTFOLIO_ENTITY.fullmatch(text):
        return text
    match = _PORTFOLIO_GLOBAL.fullmatch(text)
    return match.group(1) if match else None


def contribution_classifies(product_type: str | None, *, is_pension: bool | None = None) -> bool:
    """True when SEI needs personal, employer, or third party.

    That split is a pension fact. An ISA, JISA or GIA subscription is not one, so the
    payment goes out on the wrapper's own description and treatment.
    """
    product = str(product_type or "").upper()
    if product in _SUBSCRIPTION or product == "GIA":
        return False
    if product in _PENSION:
        return True
    if is_pension is None:
        return True
    return bool(is_pension)


def receipt_pair(product_type: str | None) -> tuple[str, str]:
    """Description and treatment for a cash receipt that is not a pension contribution."""
    product = str(product_type or "").upper()
    if product in _SUBSCRIPTION:
        return DESCRIPTION_CONTRIBUTION, TREATMENT_ISA_SUBSCRIPTION
    return DESCRIPTION_CONTRIBUTION, TREATMENT_ADDITION


def disbursement_pair(product_type: str | None) -> tuple[str, str]:
    """Description and treatment for a cash withdrawal."""
    product = str(product_type or "").upper()
    if product in _SUBSCRIPTION:
        return DESCRIPTION_WITHDRAWAL, TREATMENT_ISA_WITHDRAWAL
    return DESCRIPTION_CASH_DISBURSEMENT, TREATMENT_WITHDRAWAL


def suggestion_for(category: str) -> str:
    """One clause naming the codes the Reference report publishes for ``category``.

    Appended to the 422 so an operator configuring a firm is told what to put in, rather than
    only that something is missing — which is what made this a standing blocker.
    """
    treatment = SUGGESTED_TREATMENT_CODE.get(category)
    description = SUGGESTED_DESCRIPTION_CODE.get(category)
    if not treatment:
        return ""
    return (
        f" SEI's Reference report publishes treatment code {treatment} and description code "
        f"{description} for {category.replace('_', ' ')} — confirm them with SEI for this firm "
        "before setting them, because SEI validates the pair against the caller's role."
    )
