"""Map a ComplyCube check onto our status vocabulary, and our status onto the portal's.

``compliance.checks.status`` is ``pending``, ``clear``, ``refer``, ``rejected`` or ``failed``.

``failed`` is a TECHNICAL failure: the provider could not run the check (its ``status`` is
``failed``, the ``check.failed`` event). It is not a verdict. It says nothing about the client, so
it does not count as one of the client's tries, the portal's kyc row stays ``pending``, the firm
API reads it as ``pending``, and the right response is a new check. ``rejected`` is a VERIFICATION
failure: the check ran and the client did not pass. ``is_technical_failure``, ``next_attempt`` and
``KycOutcome.retryable`` carry that distinction to callers.

A document whose issuing country is outside the declared nationalities is ``refer`` even when the
provider's own outcome was clear — the comparison needs something to compare against, which is why
identity requires a nationality before it starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

STATUSES = ("pending", "clear", "refer", "rejected", "failed")
TECHNICAL_FAILURE = "failed"
#: The verdicts that close a referred check.
DECIDED = frozenset({"clear", "rejected"})

_COMPLETE = {
    "clear": "clear",
    "attention": "refer",
    "rejected": "rejected",
    "match_confirmed": "refer",
}


def provider_outcome(check: dict[str, Any]) -> str | None:
    """The check's outcome. A webhook payload carries it at the top level; the check object
    carries it under ``result``."""
    top = check.get("outcome")
    if isinstance(top, str) and top:
        return top
    result = check.get("result")
    if isinstance(result, dict):
        nested = result.get("outcome")
        if isinstance(nested, str) and nested:
            return nested
    return None


def map_status(provider_status: str | None, outcome: str | None) -> str:
    if provider_status == "failed":
        return TECHNICAL_FAILURE
    if provider_status != "complete":
        return "pending"
    return _COMPLETE.get(outcome or "", "refer")


def issuing_country_conflicts(issuing_country: str | None, nationalities: list[str]) -> bool:
    if not issuing_country or not nationalities:
        return False
    declared = {item.upper() for item in nationalities}
    return issuing_country.upper() not in declared


def public_status(
    provider_status: str | None,
    outcome: str | None,
    *,
    issuing_country: str | None = None,
    nationalities: list[str] | None = None,
) -> str:
    mapped = map_status(provider_status, outcome)
    if mapped == "clear" and issuing_country_conflicts(issuing_country, nationalities or []):
        return "refer"
    return mapped


def is_technical_failure(status: str | None) -> bool:
    """True for a check the provider could not run. It is not one of the client's tries."""
    return status == TECHNICAL_FAILURE


def monitoring_covers(status: str | None, asked: bool) -> bool:
    """Whether ongoing monitoring covers this screen.

    The flag is stored when the screen is started. A technical failure never ran, and the poller
    does not ask about it again, so that flag does not mean ComplyCube is re-screening the client.
    """
    return bool(asked) and not is_technical_failure(status)


def next_attempt(previous_attempt: int, previous_status: str | None) -> int:
    """The ``attempt`` number for the check that replaces one with ``previous_status``.

    A technical failure keeps the number: the client did not get a try. Anything else is a new
    try.
    """
    if is_technical_failure(previous_status):
        return previous_attempt
    return previous_attempt + 1


def partner_status(status: str) -> str:
    """The firm API's vocabulary: ``pending``, ``clear``, ``refer``, ``rejected``.

    The firm API has no technical-failure status. A check the provider could not run is not a
    verdict, so it reads as ``pending`` while operations runs it again.
    """
    return status if status in {"pending", "clear", "refer", "rejected"} else "pending"


@dataclass(frozen=True)
class KycOutcome:
    """A check's result in ``crm.kyc_checks.status`` terms.

    ``retryable`` is true for a technical failure: the kyc row stays ``pending``, a new check is
    started, and this one does not count against the client.
    """

    status: str
    retryable: bool = False

    @property
    def counts_as_attempt(self) -> bool:
        return not self.retryable


_KYC = {
    "pending": KycOutcome("pending"),
    "clear": KycOutcome("passed"),
    "refer": KycOutcome("referred"),
    "rejected": KycOutcome("failed"),
    TECHNICAL_FAILURE: KycOutcome("pending", retryable=True),
}


def to_kyc_status(status: str) -> KycOutcome:
    """``clear`` → passed, ``refer`` → referred, ``rejected`` → failed, ``failed`` → pending and
    retryable, ``pending`` → pending. An unknown status raises ``ValueError``."""
    try:
        return _KYC[status]
    except KeyError:
        raise ValueError(f"unknown screening status {status!r}") from None


#: An identity check's status as a firm reads it. Anything still with Graphene — running, held for
#: review, or one ComplyCube could not run — is ``in_progress``, and never says which. A try closed
#: for newer photos is ``superseded``.
FirmIdentityStatus = Literal["in_progress", "passed", "failed", "superseded"]

#: An AML screen's status as a firm reads it, as ``crm.get_contact_screening`` gives it.
FirmAmlStatus = Literal["pending", "passed", "referred", "failed"]


def firm_status(status: str, decided: bool) -> FirmIdentityStatus:
    """An identity check's status in the firm's vocabulary."""
    if status == "clear":
        return "passed"
    if status == "rejected":
        return "failed"
    if decided and status in {"refer", TECHNICAL_FAILURE}:
        return "superseded"
    return "in_progress"


def allowed_past_match(status: str, decided: bool, origin: str) -> bool:
    """A portal AML screen Graphene let continue past its confirmed matches
    (``aml.allow_journey``): it stays ``refer``, decided, and its kyc row passes."""
    return origin == "portal" and status == "refer" and decided


def firm_aml_status(status: str, decided: bool, *, portal: bool) -> FirmAmlStatus:
    """An AML screen's status in the firm's vocabulary, as ``crm.get_contact_screening`` gives it:
    a portal screen allowed past its confirmed matches passed; any other ``refer`` reads as
    referred only once Graphene has decided it, and a screen ComplyCube could not run reads as
    pending."""
    if status == "clear":
        return "passed"
    if status == "rejected":
        return "failed"
    if status == "refer" and decided:
        return "passed" if portal else "referred"
    return "pending"
