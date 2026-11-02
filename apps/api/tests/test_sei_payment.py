"""Which booked payment an SEI action sends, and which category that row carries."""

from __future__ import annotations

import pytest

from app.actions.attempt import OperationError
from app.actions.money import PAYMENT_MISSING, PAYMENT_SEVERAL, UNSENT_PAYMENTS
from app.integrations.sei.actions import payment_category, select_booked_payment


def _row(uid: str, category: str | None = None) -> dict[str, object]:
    return {"uid": uid, "amount": 1.0, "category": category}


def test_a_named_instruction_is_the_one_sent() -> None:
    chosen = select_booked_payment(
        [_row("older"), _row("named", "personal_contribution")],
        instruction_uid="named",
    )
    assert chosen["uid"] == "named"


def test_the_only_pending_payment_is_sent_when_the_run_names_none() -> None:
    chosen = select_booked_payment([_row("only", "employer_contribution")], instruction_uid=None)
    assert chosen["uid"] == "only"


def test_several_pending_payments_are_not_resolved_by_age() -> None:
    with pytest.raises(OperationError, match=PAYMENT_SEVERAL) as caught:
        select_booked_payment([_row("older"), _row("newer")], instruction_uid=None)
    assert caught.value.permanent is True


def test_a_missing_payment_is_a_permanent_refusal() -> None:
    with pytest.raises(OperationError, match=PAYMENT_MISSING):
        select_booked_payment([], instruction_uid=None)
    with pytest.raises(OperationError, match=PAYMENT_MISSING):
        select_booked_payment([_row("other")], instruction_uid="missing")


def test_a_payment_already_lodged_is_left_out_of_the_next_run() -> None:
    assert "tx.source_transaction_id = i.uid" in UNSENT_PAYMENTS
    assert "data ->> 'origin' = 'instruction'" in UNSENT_PAYMENTS
    assert "'submitted'" in UNSENT_PAYMENTS
    assert "'posted'" in UNSENT_PAYMENTS


def test_only_a_real_contribution_category_is_passed_to_sei() -> None:
    assert payment_category("personal_contribution") == "personal_contribution"
    assert payment_category("employer_contribution") == "employer_contribution"
    assert payment_category("third_party_contribution") == "third_party_contribution"
    assert payment_category(None) == ""
    assert payment_category("payment") == ""
    assert payment_category("") == ""
