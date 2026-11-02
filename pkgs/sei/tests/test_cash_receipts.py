"""The cash-receipt payload, checked against SEI's own vendored spec.

This is the gate that matters. The models in ``sei.models`` are hand-transcribed, so the risk
is not that the code is wrong but that the *transcription* is — a renamed key, a field SEI
does not have, a required field we forgot. Both directions are checked here:

- every payload we build validates against the spec's JSON Schema (nothing missing, nothing
  mistyped);
- every key our models can emit exists in the spec (nothing invented).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from sei.enums import ReceiptAmountType, ReceiptMethod
from sei.models import (
    Attributes,
    CurrencyReceiptRequest,
    Once,
    ProcessingScheduleDetails,
    ReceiptInstructions,
    ReceiptResult,
    money,
)

SPEC = (
    Path(__file__).resolve().parent.parent
    / "spec"
    / "swagger-spec-CreateOrMaintainCashReceipts.yaml"
)


@pytest.fixture(scope="session")
def spec() -> dict[str, Any]:
    with SPEC.open() as handle:
        loaded: dict[str, Any] = yaml.safe_load(handle)
    return loaded


@pytest.fixture(scope="session")
def request_validator(spec: dict[str, Any]) -> Draft202012Validator:
    """A validator over ``CurrencyReceiptRequest`` with the spec's components inlined so
    ``$ref``s resolve locally."""
    schema = {
        "$ref": "#/components/schemas/CurrencyReceiptRequest",
        "components": spec["components"],
    }
    return Draft202012Validator(schema)


def anticipated_receipt() -> Attributes:
    """The payload the contributions flow sends: a future-dated receipt with no payer — an
    Anticipated Cash Receipt."""
    return Attributes(
        account_number="USB0042817",
        portfolio_id="1234567",
        firm_id="3102",
        receipt_instructions=ReceiptInstructions(
            currency_code="GBP",
            receipt_method=ReceiptMethod.BACS,
            description_type_code="1091",
            treatment_type_code="4402",
            treatment_code_tax_year="2026/27",
            receipt_amount=money(Decimal("1250.00")),
            receipt_amount_type=ReceiptAmountType.GROSS_VALUE,
            external_reference="con_abc123",
        ),
        processing_schedule_details=ProcessingScheduleDetails(once=Once.on(date(2026, 9, 1))),
    )


# --- the payload validates ------------------------------------------------------------


def test_anticipated_receipt_validates(request_validator: Draft202012Validator) -> None:
    payload = CurrencyReceiptRequest.single(anticipated_receipt()).to_payload()
    request_validator.validate(payload)


def test_minimal_receipt_validates(request_validator: Draft202012Validator) -> None:
    """Only the spec's required fields — no schedule, no firm, no optional instructions."""
    payload = CurrencyReceiptRequest.single(
        Attributes(
            account_number="USB0042817",
            portfolio_id="1234567",
            receipt_instructions=ReceiptInstructions(
                currency_code="GBP",
                receipt_method=ReceiptMethod.BACS,
                description_type_code="1091",
                treatment_type_code="4402",
                receipt_amount="100.00",
            ),
        )
    ).to_payload()
    request_validator.validate(payload)


def test_unset_optionals_are_omitted_not_nulled() -> None:
    """SEI treats an explicit null as a supplied value on some fields; the payload must simply
    not carry them."""
    payload = CurrencyReceiptRequest.single(
        Attributes(
            account_number="A",
            portfolio_id="P",
            receipt_instructions=ReceiptInstructions(
                currency_code="GBP",
                receipt_method=ReceiptMethod.BACS,
                description_type_code="1091",
                treatment_type_code="4402",
                receipt_amount="100.00",
            ),
        )
    ).to_payload()
    instructions = payload["data"][0]["attributes"]["receiptInstructions"]
    assert "receiptType" not in instructions
    assert "isaContribution" not in instructions
    assert "firmId" not in payload["data"][0]["attributes"]


# --- the transcription has not drifted ------------------------------------------------


def _spec_properties(spec: dict[str, Any], name: str) -> set[str]:
    return set((spec["components"]["schemas"][name].get("properties") or {}).keys())


@pytest.mark.parametrize(
    ("model", "schema_name"),
    [
        (Attributes, "Attributes"),
        (ReceiptInstructions, "ReceiptInstructions"),
        (ProcessingScheduleDetails, "ProcessingScheduleDetails"),
        (Once, "Once"),
    ],
)
def test_models_invent_no_fields(spec: dict[str, Any], model: Any, schema_name: str) -> None:
    """Every alias our models emit must exist in SEI's schema. Catches a typo'd key, which
    SEI silently ignores rather than rejecting."""
    ours = {field.alias or name for name, field in model.model_fields.items()}
    assert ours <= _spec_properties(spec, schema_name), (
        f"{schema_name}: not in SEI's spec -> {sorted(ours - _spec_properties(spec, schema_name))}"
    )


@pytest.mark.parametrize(
    ("model", "schema_name"),
    [(Attributes, "Attributes"), (ReceiptInstructions, "ReceiptInstructions")],
)
def test_every_required_spec_field_is_modelled(
    spec: dict[str, Any], model: Any, schema_name: str
) -> None:
    required = set(spec["components"]["schemas"][schema_name].get("required") or [])
    ours = {field.alias or name for name, field in model.model_fields.items()}
    assert required <= ours, f"{schema_name}: unmodelled required -> {sorted(required - ours)}"


def test_required_fields_are_actually_required_in_the_model() -> None:
    """A spec-required field modelled as optional would pass the drift check above while
    still letting a caller omit it."""
    with pytest.raises(ValueError):
        ReceiptInstructions(currency_code="GBP", receipt_method=ReceiptMethod.BACS)  # type: ignore[call-arg]


def test_a_typo_in_the_payload_is_rejected(request_validator: Draft202012Validator) -> None:
    """The models forbid extras, and the spec validator is the second line of defence."""
    with pytest.raises(ValueError):
        ReceiptInstructions(
            currency_code="GBP",
            receipt_method=ReceiptMethod.BACS,
            description_type_code="1091",
            treatment_type_code="4402",
            receipt_amount="1.00",
            recieptTypo="oops",  # type: ignore[call-arg]
        )


# --- money formatting -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("60"), "60.00"),  # normalize() would render this 6E+1
        (Decimal("1250"), "1250.00"),
        (Decimal("1250.005"), "1250.01"),  # half-up, not banker's
        (Decimal("0.1"), "0.10"),
        (1250.5, "1250.50"),
        (100, "100.00"),
        ("42.129", "42.13"),
    ],
)
def test_money_renders_a_plain_decimal_string(value: Any, expected: str) -> None:
    assert money(value) == expected


def test_amounts_are_strings_on_the_wire() -> None:
    payload = CurrencyReceiptRequest.single(anticipated_receipt()).to_payload()
    amount = payload["data"][0]["attributes"]["receiptInstructions"]["receiptAmount"]
    assert isinstance(amount, str)


def test_request_type_is_the_spec_request_not_the_response_type() -> None:
    """``CreateCurrencyReceipt`` is ``SuccessData``. The request example, and the call Select
    accepts, is ``CurrencyReceiptRequest``."""
    payload = CurrencyReceiptRequest.single(anticipated_receipt()).to_payload()
    assert payload["data"][0]["type"] == "CurrencyReceiptRequest"


def test_memo_receipt_flags_serialise() -> None:
    """Select's working receipt is a memo cheque: these four flags are in the spec and were
    previously rejected by the model, so the call could not be built."""
    payload = CurrencyReceiptRequest.single(
        Attributes(
            account_number="MSP001062",
            portfolio_id="59018837",
            firm_id="3106",
            receipt_instructions=ReceiptInstructions(
                currency_code="GBP",
                receipt_method=ReceiptMethod.CHECK,
                receipt_type="2",
                official_check="true",
                check_received_flag="false",
                description_type_code="20190",
                treatment_type_code="1303",
                invest_cash="false",
                eligible_for_invest_cash_scheduler="false",
                receipt_amount_type=ReceiptAmountType.GROSS_VALUE,
                receipt_amount="10.00",
                bypass_approval_required="true",
                memo_flag="true",
            ),
            processing_schedule_details=ProcessingScheduleDetails(
                once=Once(due_date="2026-09-28", beneficiary_value_date="2026-09-28")
            ),
        )
    ).to_payload()
    instructions = payload["data"][0]["attributes"]["receiptInstructions"]
    assert instructions["memoFlag"] == "true"
    assert instructions["officialCheck"] == "true"
    assert instructions["receiptMethod"] == "3"
    assert "payerInfo" not in instructions


def test_enum_members_serialise_to_their_codes() -> None:
    payload = CurrencyReceiptRequest.single(anticipated_receipt()).to_payload()
    instructions = payload["data"][0]["attributes"]["receiptInstructions"]
    assert instructions["receiptMethod"] == "5"  # BACS
    assert instructions["receiptAmountType"] == "1"  # Gross Value


def test_due_date_is_hyphenated_iso() -> None:
    """The field description says [YYYY/MM/DD] but every example in the spec — and the
    service — uses hyphens."""
    assert Once.on(date(2026, 9, 1)).due_date == "2026-09-01"


# --- responses -------------------------------------------------------------------------


def test_result_reads_the_activity_id_out_of_the_envelope() -> None:
    body = {
        "data": [
            {
                "type": "CreateCurrencyReceipt",
                "attributes": {
                    "activityId": "857458",
                    "eventId": "963258",
                    "receiptAmount": "1250.00",
                    "warnings": [{"code": "1145", "message": "Approval Exists"}],
                },
            }
        ]
    }
    result = ReceiptResult.from_body(body)
    assert result.activity_id == "857458"
    assert result.warnings[0].code == "1145"


def test_result_tolerates_an_empty_envelope() -> None:
    assert ReceiptResult.from_body({}).activity_id is None
    assert ReceiptResult.from_body({"data": []}).activity_id is None


def test_result_keeps_unknown_response_fields() -> None:
    """SEI adds fields between releases; a response model that dropped them would lose data
    we might need to explain a transaction later."""
    result = ReceiptResult.from_body(
        {"data": [{"attributes": {"activityId": "1", "somethingNew": "x"}}]}
    )
    assert result.model_extra == {"somethingNew": "x"}


def _validation_message(exc: ValidationError) -> str:
    return exc.message


def test_spec_validator_catches_a_missing_required_field(
    request_validator: Draft202012Validator,
) -> None:
    """Proves the validator is actually enforcing, not vacuously passing."""
    bad = {"data": [{"type": "CreateCurrencyReceipt", "attributes": {"portfolioId": "1"}}]}
    with pytest.raises(ValidationError):
        request_validator.validate(bad)
