"""The pay-to profile enrolment builds from the bank details already collected."""

import pytest
from sei import SeiApiError

from app.integrations.sei.pay_to import (
    already_a_pay_to,
    build_pay_to,
    matching_profile,
    pay_to_entry,
    profile_id_of,
    remembered_entries,
)

_UK = {
    "name": "Alex Holder",
    "sort_code": "04-00-40",
    "account_number": "37448762",
}


def test_a_uk_account_is_bacs_on_the_enrolled_party():
    body = build_pay_to(
        firm_id="3108",
        account_number="AIO00213",
        bank=_UK,
        external_party_id="TcgoaGY24khB",
    )
    assert list(body) == [
        "usedBy",
        "accountNumber",
        "firmId",
        "newPartyIndicator",
        "externalPartyId",
        "existingPartyAddressPrimaryIndicator",
        "existingPartyAddressProfileType",
        "paymentMethod",
        "bacsInfo",
        "byPassApproval",
    ]
    assert body["newPartyIndicator"] is False
    assert body["existingPartyAddressPrimaryIndicator"] is False
    assert body["existingPartyAddressProfileType"] == "Home"
    assert body["externalPartyId"] == "TcgoaGY24khB"
    assert body["paymentMethod"] == "BACS"
    assert body["byPassApproval"] is True
    assert body["bacsInfo"] == {
        "bacsAccountName": "Alex Holder",
        "bacsBeneficiaryReferenceRollNumber": "Alex Holder",
        "bacsDestinationSortCode": "040040",
        "bacsPayToAccountNumber": "37448762",
    }


def test_an_existing_sei_client_is_named_by_party_id():
    body = build_pay_to(
        firm_id="3108",
        account_number="AIO00213",
        bank=_UK,
        external_party_id="TcgoaGY24khB",
        party_id="59103906",
    )
    assert body["partyId"] == "59103906"
    assert "externalPartyId" not in body
    assert body["existingPartyAddressPrimaryIndicator"] is False
    assert list(body)[-1] == "byPassApproval"
    assert (
        list(body).index("partyId")
        < list(body).index("existingPartyAddressPrimaryIndicator")
        < list(body).index("paymentMethod")
    )


def test_faster_payment_and_iban_use_their_own_blocks():
    faster = build_pay_to(
        firm_id="3108",
        account_number="AIO00213",
        bank={**_UK, "method": "faster_payment"},
        external_party_id="TcgoaGY24khB",
    )
    assert faster["paymentMethod"] == "FASTERPAYMENT"
    assert "bacsInfo" not in faster
    assert faster["fasterInfo"]["fasterPayToAccountNumber"] == "37448762"

    swift = build_pay_to(
        firm_id="3108",
        account_number="AIO00213",
        bank={"name": "Alex Holder", "iban": "GB29 NWBK 6016 1331 9268 19", "bic": "NWBKGB2L"},
        external_party_id="TcgoaGY24khB",
    )
    assert swift["paymentMethod"] == "SWIFT"
    assert swift["swiftInfo"]["swiftBeneficiaryAccountId"] == "GB29NWBK60161331926819"
    assert swift["swiftInfo"]["swiftBeneficiaryIntitutionBIC"] == "NWBKGB2L"


def test_a_bank_sei_cannot_pay_is_refused_before_the_call():
    with pytest.raises(ValueError, match="sort code"):
        build_pay_to(
            firm_id="3108",
            account_number="AIO00213",
            bank={"name": "Alex", "sort_code": "12", "account_number": "1"},
            external_party_id="TcgoaGY24khB",
        )


def test_a_zero_profile_id_is_approval_not_a_destination():
    assert profile_id_of({"status": "SUCCESS", "profileId": 0}) is None
    assert profile_id_of({"status": "SUCCESS", "profileId": 55443}) == "55443"


def test_a_duplicate_profile_is_the_one_already_on_the_account():
    exc = SeiApiError(
        "duplicate pay-to",
        errors=[{"code": "2004", "detail": "Duplicate Payer To relationship exists"}],
    )
    assert already_a_pay_to(exc) is True
    assert already_a_pay_to(SeiApiError("sort code is not eligible")) is False
    details = {
        "listOfpayToRecipients": [
            {
                "profileId": "60790001",
                "bankInfo": {"bankSortCode": "040040", "bankAccountNumber": "37448762"},
            }
        ]
    }
    assert matching_profile(details, _UK) == "60790001"
    assert pay_to_entry(_UK, "60790001")["provider_ref"] == "60790001"
    assert pay_to_entry(_UK, "60790001")["method"] == "bacs"


def test_the_profile_lands_on_the_bank_row_a_withdrawal_reads():
    onboarding = {"name": "Alex Holder", "sort_code": "04-00-40", "account_number": "37448762"}
    profile = pay_to_entry(_UK, "60790001")
    stored = remembered_entries([onboarding], profile)
    assert stored[0]["provider_ref"] == "60790001"
    assert stored[0]["direction"] == "pay_to"
    assert len(stored) == 1

    payer = {**onboarding, "direction": "payer", "provider_ref": "payer-1"}
    beside = remembered_entries([payer], profile)
    assert beside[0]["provider_ref"] == "payer-1"
    assert beside[1]["provider_ref"] == "60790001"
