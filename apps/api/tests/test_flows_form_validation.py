"""Unit tests for "does this submission answer the form?" — the rule the step transition enforces.

The pure half is here (no DB): what counts as a blank answer, which fields a submission must carry,
and the shapes that used to slip through — an absent payload, a whitespace-only string, an empty
list from an unticked multi-select. The DB half (that the rule is applied at the transition, so
every route inherits it) is in ``test_applications_api.py``.
"""

from __future__ import annotations

from typing import Any

from app.flows.form_validation import is_blank, missing_required, national_insurance_problems


def _field(
    key: str,
    label: str,
    *,
    field_type: str = "text",
    required: bool = True,
    conditions: Any = None,
    validation: Any = None,
    options: Any = None,
) -> dict[str, Any]:
    return {
        "field_key": key,
        "field_type": field_type,
        "label": label,
        "required": required,
        "conditions": conditions,
        "validation": validation,
        "options": options,
    }


def test_zero_and_false_are_answers_but_empty_containers_are_not() -> None:
    assert not is_blank(0)
    assert not is_blank(False)
    assert not is_blank("no")
    assert is_blank(None)
    assert is_blank("")
    assert is_blank("   ")
    assert is_blank([])
    assert is_blank({})
    assert not is_blank(["GBP"])


def test_missing_required_names_the_unanswered_questions() -> None:
    fields = [_field("first_name", "First name"), _field("surname", "Surname")]
    assert missing_required(fields, {"first_name": "Ada"}) == ["Surname"]
    assert missing_required(fields, {"first_name": "Ada", "surname": "Lovelace"}) == []


def test_an_absent_payload_answers_nothing() -> None:
    """``None`` (no submission recorded at all) must not read as "nothing was required" — that was
    the shape behind completing a fact-find with no form response in existence."""
    fields = [_field("first_name", "First name")]
    assert missing_required(fields, None) == ["First name"]
    assert missing_required(fields, {}) == ["First name"]


def test_optional_and_conditional_fields_are_never_demanded() -> None:
    fields = [
        _field("middle_name", "Middle name", required=False),
        # Its condition may legitimately be false for this client, and conditions are evaluated in
        # the browser — demanding it server-side would wedge the step.
        _field("trust_name", "Trust name", conditions={"field": "type", "eq": "trust"}),
    ]
    assert missing_required(fields, {}) == []


def test_a_field_with_no_label_falls_back_to_its_key() -> None:
    assert missing_required([_field("nino", "")], {}) == ["nino"]


def test_an_address_shell_does_not_answer_a_required_address() -> None:
    field = _field("address", "Residential address", field_type="address")
    assert missing_required(
        [field],
        {"address": {"line1": "", "city": "", "postcode": "", "country": "GB"}},
    ) == ["Residential address"]
    assert (
        missing_required(
            [field],
            {
                "address": {
                    "line1": "123 Rue Brizeux",
                    "city": "Plouvien",
                    "postcode": "29860",
                    "country": "FR",
                }
            },
        )
        == []
    )


def test_the_list_is_ordered_as_the_form_is() -> None:
    fields = [_field("a", "Alpha"), _field("b", "Beta"), _field("c", "Gamma")]
    assert missing_required(fields, {"b": "x"}) == ["Alpha", "Gamma"]


def _pensions() -> dict[str, Any]:
    return _field(
        "pensions",
        "Pensions",
        field_type="repeating",
        options=[
            {"key": "provider", "label": "Provider", "type": "text"},
            {"key": "value", "label": "Value", "type": "currency"},
        ],
    )


def test_a_required_repeating_group_with_no_row_is_missing() -> None:
    field = _pensions()
    assert missing_required([field], {}) == ["Pensions"]
    assert missing_required([field], {"pensions": []}) == ["Pensions"]
    assert missing_required([field], {"pensions": None}) == ["Pensions"]


def test_a_required_repeating_column_left_blank_is_missing() -> None:
    field = _pensions()
    assert missing_required([field], {"pensions": [{"provider": "", "value": "1000"}]}) == [
        "Pensions (Provider)"
    ]
    assert missing_required([field], {"pensions": [{"provider": "  ", "value": "1000"}]}) == [
        "Pensions (Provider)"
    ]
    assert missing_required([field], {"pensions": [{"provider": "Aviva", "value": "1000"}]}) == []


def test_zero_in_a_repeating_column_is_an_answer() -> None:
    field = _field(
        "liabilities",
        "Liabilities",
        field_type="repeating",
        options=[{"key": "balance", "label": "Balance", "type": "number"}],
    )
    assert missing_required([field], {"liabilities": [{"balance": 0}]}) == []


def test_an_optional_repeating_group_may_be_empty_but_not_half_filled() -> None:
    """Starting a row and leaving a required column blank is not an answer. A column is
    required unless the author set ``required`` false."""
    field = _field(
        "dependants",
        "Dependants",
        field_type="repeating",
        required=False,
        options=[
            {"key": "name", "label": "Name", "type": "text"},
            {"key": "note", "label": "Note", "type": "text", "required": False},
        ],
    )
    assert missing_required([field], {}) == []
    assert missing_required([field], {"dependants": []}) == []
    assert missing_required([field], {"dependants": [{"name": "", "note": ""}]}) == [
        "Dependants (Name)"
    ]
    assert missing_required([field], {"dependants": [{"name": "Ada", "note": ""}]}) == []


def test_a_national_insurance_number_must_be_one_hmrc_issues() -> None:
    fields = [
        _field("national_identifier", "National Insurance number"),
        _field("national_identifier_type", "Identifier type", required=False),
    ]
    answers = {
        "national_identifier_type": "National Insurance Number",
        "national_identifier": "BG123456A",
    }
    problems = national_insurance_problems(fields, answers)
    assert len(problems) == 1
    assert "National Insurance number" in problems[0]
    assert "BG123456A" not in problems[0]
    assert (
        national_insurance_problems(
            fields,
            {
                "national_identifier_type": "National Insurance Number",
                "national_identifier": "QQ 12 34 56 C",
            },
        )
        == []
    )
    # A passport on the same field is not an NI number.
    assert (
        national_insurance_problems(
            fields, {"national_identifier_type": "Passport", "national_identifier": "BG123456A"}
        )
        == []
    )
    # Blank is the required-field rule's job.
    assert (
        national_insurance_problems(
            fields,
            {"national_identifier_type": "nino", "national_identifier": ""},
        )
        == []
    )


def test_required_text_honours_its_minimum_length() -> None:
    field = _field(
        "wealth",
        "Source of wealth statement",
        field_type="textarea",
        validation={"minLength": 30},
    )
    assert missing_required([field], {"wealth": "Sale of shares"}) == ["Source of wealth statement"]
    assert (
        missing_required(
            [field],
            {"wealth": "Proceeds from the sale of shares held for ten years"},
        )
        == []
    )
