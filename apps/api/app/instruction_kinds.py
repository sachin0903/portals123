"""How an origin kind is spelled, and which kinds may carry an adviser fee.

The ledger, the review summary and the statement subtype all read ``display_kind``.
A second map — in the dispatch module, or in the portal — is how ``Model top-up``
and ``Model topup`` end up on adjacent rows.

``FEE_WITH_MOVEMENT`` is the same rule as the check
``instructions_adviser_fee_movement_only``: an adviser fee rides a payment in (taken off the
money arriving) or a withdrawal (an additional debit), and nothing else. The Seccl group and the
SEI disbursement consult this set rather than growing their own.
"""

from __future__ import annotations

#: Origin kind → the word a person reads. Snake_case keys are internal.
KIND_LABEL: dict[str, str] = {
    "expected_in": "Payment",
    "withdrawal": "Withdrawal",
    "adhoc_fee": "Adviser fee",
    "model_topup": "Model top-up",
    "model_withdrawal": "Model withdrawal",
    "personal_contribution": "Personal contribution",
    "employer_contribution": "Employer contribution",
    "third_party_contribution": "Third party contribution",
    "contribution": "Contribution",
    "transfer": "Transfer in",
    "transfer_in": "Transfer in",
}

#: Kinds whose money movement may include an adviser fee. The column check names the same set.
#: A payment in takes the fee off the money arriving; a withdrawal adds it as a second debit.
FEE_WITH_MOVEMENT = frozenset({"expected_in", "withdrawal", "model_withdrawal"})


def display_kind(kind: str | None) -> str:
    """The listing word for an origin kind — never a snake_case key."""
    if not kind:
        return ""
    labelled = KIND_LABEL.get(kind)
    if labelled:
        return labelled
    return kind.replace("_", " ").replace("-", " ")
