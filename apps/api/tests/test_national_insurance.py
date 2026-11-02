"""HMRC's National Insurance number rules, and the contact body that stores a number."""

from __future__ import annotations

import re

import pytest

from app import national_insurance
from app.contacts import UpdateContact


@pytest.mark.parametrize("value", ["QQ 12 34 56 C", "QQ123456C", "qq123456c", "AB-12-34-56-A"])
def test_valid_numbers_normalise_to_compact_upper_case(value: str) -> None:
    assert national_insurance.normalise(value) == re.sub(r"[\s-]", "", value).upper()
    assert national_insurance.is_valid(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "QQ12345C",  # five digits
        "QQ1234567C",  # seven digits
        "QQ123456",  # no suffix
        "QQ123456E",  # suffix outside A-D
        "DA123456A",  # D never leads
        "QA123456A",  # Q never leads, QQ aside
        "AO123456A",  # O never second
        "BG123456A",  # unallocated prefix
        "GB123456A",
        "ZZ123456A",
        "GBR",  # a country code, not a number
    ],
)
def test_invalid_numbers_are_rejected(value: str) -> None:
    assert national_insurance.normalise(value) is None
    assert not national_insurance.is_valid(value)


def test_non_strings_are_not_numbers() -> None:
    assert national_insurance.normalise(None) is None
    assert national_insurance.normalise(123456) is None


def test_field_pattern_matches_the_same_rules_in_a_browser_regex() -> None:
    pattern = re.compile(national_insurance.FIELD_PATTERN)
    assert pattern.match("QQ 12 34 56 C")
    assert pattern.match("qq123456c")
    assert not pattern.match("QQ123456E")
    assert not pattern.match("BG 12 34 56 A")
    assert not pattern.match("DA123456A")


def test_the_regulator_name_counts_as_a_nino() -> None:
    assert national_insurance.is_nino_type("National Insurance Number")
    assert national_insurance.is_nino_type("nino")
    assert not national_insurance.is_nino_type("Passport")
    assert not national_insurance.is_nino_type("CONCAT")


def test_update_contact_stores_a_nino_compact() -> None:
    body = UpdateContact(
        national_identifier="qq 12 34 56 c",
        national_identifier_type="National Insurance Number",
    )
    assert body.national_identifier == "QQ123456C"


def test_update_contact_rejects_a_bad_nino() -> None:
    with pytest.raises(ValueError, match="National Insurance"):
        UpdateContact(national_identifier="BG123456A", national_identifier_type="nino")


def test_update_contact_leaves_other_identifier_types_alone() -> None:
    body = UpdateContact(national_identifier="123456789", national_identifier_type="passport")
    assert body.national_identifier == "123456789"
    untyped = UpdateContact(national_identifier="anything")
    assert untyped.national_identifier == "anything"
