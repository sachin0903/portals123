"""SEI pay-to profiles replace themselves. An entry the firm typed stays."""

from __future__ import annotations

import pytest

from book_sink.banks import UnreadableBankAccounts, merged_bank_accounts

FIRM = {"name": "Firm Payee", "sort_code": "11-22-33", "account_number": "12345678"}
OLD = {
    "name": "Old",
    "direction": "pay_to",
    "provider": "sei",
    "provider_ref": "P1",
    "method": "bacs",
}
NEW = {
    "name": "New",
    "direction": "pay_to",
    "provider": "sei",
    "provider_ref": "P1",
    "method": "chaps",
    "sort_code": "44-55-66",
}
OTHER = {
    "name": "Other",
    "direction": "pay_to",
    "provider": "sei",
    "provider_ref": "P2",
    "method": "bacs",
}


def test_a_firm_entry_survives_and_a_profile_is_refreshed() -> None:
    merged = merged_bank_accounts([FIRM, OLD], [OTHER, NEW])
    assert merged is not None
    assert merged[0] == FIRM
    assert [entry["provider_ref"] for entry in merged[1:]] == ["P2", "P1"]
    assert merged[2]["name"] == "New"
    assert merged[2]["method"] == "chaps"


def test_a_profile_that_has_left_the_file_leaves_the_account() -> None:
    assert merged_bank_accounts([FIRM, OLD], []) == [FIRM]
    assert merged_bank_accounts([OLD], None) is None
    assert merged_bank_accounts([OLD], "null") is None


def test_a_typed_sei_entry_without_a_profile_id_is_the_firms() -> None:
    typed = {"name": "Typed", "provider": "sei", "direction": "pay_to", "method": "bacs"}
    assert merged_bank_accounts([typed], [NEW]) == [typed, NEW]


def test_blank_keys_are_omitted_and_a_json_string_is_read() -> None:
    incoming = (
        '[{"name":"Ada","bank":null,"direction":"pay_to","provider":"sei",'
        '"provider_ref":"P9","method":""}]'
    )
    merged = merged_bank_accounts(None, incoming)
    assert merged == [
        {"name": "Ada", "direction": "pay_to", "provider": "sei", "provider_ref": "P9"}
    ]


def test_a_value_that_is_not_a_list_is_not_treated_as_empty() -> None:
    with pytest.raises(UnreadableBankAccounts):
        merged_bank_accounts([FIRM], "not-json")
    with pytest.raises(UnreadableBankAccounts):
        merged_bank_accounts([FIRM], 3)
