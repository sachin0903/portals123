"""Seccl onboarding builds a client and an account without guessing a pension."""

from datetime import date

import pytest
from seccl import SecclApiError

from app.actions.attempt import OperationError
from app.integrations.seccl.enrol import account_body, client_body
from app.integrations.seccl.instruct import (
    _refused,
    client_patch,
    fee_patch,
    illustration_body,
    merge_standing,
    standing_from_book,
)


def test_refused_parks_a_4xx_and_retries_a_5xx_or_lost_answer() -> None:
    # A 4xx is a refusal the request itself caused; another try with the same body is refused again.
    bad_request = _refused(SecclApiError("POST /client failed: 400 — surname blank", status=400))
    assert bad_request.permanent is True
    assert bad_request.status_code == 400
    # 429 is a throttle, not a bad request; a 5xx and a lost answer (no status) are transient.
    assert _refused(SecclApiError("too many", status=429)).permanent is False
    assert _refused(SecclApiError("boom", status=503)).permanent is False
    assert _refused(SecclApiError("timeout", status=None)).permanent is False


def _client() -> dict[str, object]:
    return {
        "first_name": "Ada",
        "surname": "Lovelace",
        "email": "ada@example.test",
        "dob": date(1990, 12, 1),
        "address_line1": "1 High Street",
        "address_city": "London",
        "address_postcode": "SW1A 1AA",
        "address_country": "GB",
        "nationality": "GB",
        "gender": "female",
        "national_identifier": "AB123456C",
        "title": "Mrs",
    }


def test_a_complete_client_becomes_a_seccl_body() -> None:
    body = client_body(_client(), node_id="21")
    assert body["nodeId"] == ["21"]
    assert body["clientType"] == "Individual"
    assert body["taxDomicile"] == "GB"
    assert body["gender"] == "Female"
    assert body["addressDetail"]["postCode"] == "SW1A 1AA"
    assert body["addressDetail"]["address2"] == "London"
    assert body["dateOfBirth"] == "1990-12-01"
    assert body["title"] == "Mrs"


def test_a_title_is_sent_as_seccls_enum_word() -> None:
    prof = _client()
    prof["title"] = "Prof"
    assert client_body(prof, node_id="21")["title"] == "Professor"
    dotted = _client()
    dotted["title"] = "Mr."
    assert client_body(dotted, node_id="21")["title"] == "Mr"
    master = _client()
    master["title"] = "Master"
    assert "title" not in client_body(master, node_id="21")


def test_a_client_missing_a_node_or_an_address_is_refused() -> None:
    with pytest.raises(OperationError, match="Seccl node"):
        client_body(_client(), node_id="")
    incomplete = _client()
    incomplete["address_country"] = ""
    incomplete["nationality"] = ""
    with pytest.raises(OperationError, match="country"):
        client_body(incomplete, node_id="21")


def test_a_gia_account_names_the_wrapper_and_a_pension_is_refused() -> None:
    body = account_body({"name": "Ada GIA", "product_type": "GIA"}, client_id="C1", node_id="21")
    assert body["nodeId"] == "21"
    assert body["clientId"] == "C1"
    assert body["wrapperDetail"] == {"wrapperType": "GIA"}
    with pytest.raises(OperationError, match="scheme product"):
        account_body({"product_type": "SIPP"}, client_id="C1", node_id="21")
    with pytest.raises(OperationError, match="client"):
        account_body({"product_type": "ISA"}, client_id="", node_id="21")


def test_a_named_scheme_product_and_model_go_on_the_create() -> None:
    body = account_body(
        {
            "name": "Ada SIPP",
            "product_type": "SIPP",
            "currency": "GBP",
            "scheme_product_id": "SCH-1",
            "model_external_id": "MOD-9",
        },
        client_id="C1",
        node_id="21",
    )
    assert body["wrapperDetail"] == {
        "wrapperType": "PENSION",
        "schemeProductId": "SCH-1",
        "assetAllocationId": "MOD-9",
    }


def test_opening_cash_is_the_amount_on_the_application() -> None:
    from app.integrations.seccl.instruct import initial_cash_amount

    assert initial_cash_amount({"initial_cash_deposit": {"investment_amount": "10000"}}) == 10000
    assert initial_cash_amount({"initial_cash_deposit": {}}) is None
    assert initial_cash_amount(None) is None
    # An older account kept it inside the payments object.
    old = {"initial_cash_deposit": {"investment_amount": 500}, "regular_payments": {}}
    assert initial_cash_amount({}, old) == 500
    assert initial_cash_amount({}, []) is None


def test_a_client_update_sends_one_part_at_a_time() -> None:
    client = {
        "email": "ada@example.test",
        "is_vulnerable": True,
        "vulnerability_reasons": ["health"],
        "vulnerability_is_temporary": False,
        "bank_account": {"sort_code": "30-94-93", "account_number": "01273801"},
        "first_name": "Ada",
    }
    assert client_patch(client, part="details") == {"email": "ada@example.test"}
    assert client_patch(client, part="bank")["bankDetails"]["sortCode"] == "309493"
    assert client_patch(client, part="vulnerability")["vulnerability"]["reason"] == ["health"]
    with pytest.raises(OperationError, match="email"):
        client_patch({}, part="details")
    with pytest.raises(OperationError, match="employment status"):
        client_patch(client, part="circumstances")


def test_a_client_product_is_the_wrapper_around_the_account() -> None:
    from app.integrations.seccl.enrol import client_product_link_type, client_product_of

    assert client_product_of(
        {"wrapperDetail": {"wrapperType": "PENSION", "clientProductId": "02G2H"}}
    ) == ("PENSION", "02G2H")
    assert client_product_of({"wrapperDetail": {}}) is None
    assert client_product_link_type("PENSION") == "client_product:PENSION"


def test_flexi_access_and_primary_protection_go_on_the_client_product() -> None:
    from app.integrations.seccl.instruct import ambiguous_protection, product_detail_fields

    detail = product_detail_fields(
        {
            "flexi_access_taken": True,
            "flexi_access_date": date(2024, 4, 6),
            "pension_protection": ["primary", "fixed"],
        }
    )
    assert detail["flexibleBenefitsAccessed"] is True
    assert detail["flexiAccessDate"] == "2024-04-06T00:00:00.000Z"
    assert detail["protection"] == [{"protectionType": "Primary"}]
    assert ambiguous_protection({"pension_protection": ["fixed"]})


def test_circumstances_are_the_pension_individual_fields() -> None:
    body = client_patch(
        {
            "employment_status": "self_employed",
            "marital_status": "married",
            "retirement_age": 67,
            "employer_name": "Company Ltd",
            "employer_registration_number": "1234567",
            "employer_address": {
                "line1": "1 High Street",
                "city": "London",
                "postcode": "SW1A 1AA",
                "country": "GB",
            },
        },
        part="circumstances",
    )
    assert body["employmentStatus"] == "Self Employed"
    assert body["maritalStatus"] == "Married"
    assert body["retirementAge"] == 67
    assert body["employers"][0]["companyName"] == "Company Ltd"
    assert body["employers"][0]["addressDetail"]["postCode"] == "SW1A 1AA"
    with pytest.raises(OperationError, match="marital status"):
        client_patch({"marital_status": "civil_partner"}, part="circumstances")


def test_a_standing_withdrawal_keeps_the_payment_in() -> None:
    entry = standing_from_book(
        {"amount": 150, "frequency": "monthly", "date": "2026-10-28"},
        movement="Out",
    )
    merged = merge_standing(
        [{"movementType": "In", "amount": 70, "paymentDay": 14}],
        entry,
    )
    assert [row["movementType"] for row in merged] == ["In", "Out"]
    assert entry["method"] == "BACS Credit"
    with pytest.raises(OperationError, match="single payment"):
        standing_from_book(
            {"amount": 10, "frequency": "single", "date": "2026-10-01"}, movement="In"
        )


def test_a_standing_order_reads_the_list_and_the_older_object() -> None:
    listed = standing_from_book(
        [
            {
                "amount": 70,
                "frequency": "monthly",
                "next_date": "2026-10-14",
                "method": "direct_debit",
            }
        ],
        movement="In",
    )
    assert listed["paymentDay"] == 14
    assert listed["amount"] == 70
    # The register's own method code is not Seccl's word for it.
    assert listed["method"] == "Bank Transfer"
    older = standing_from_book(
        {
            "initial_cash_deposit": {"investment_amount": 1_000},
            "regular_payments": {"amount": 70, "frequency": "monthly", "date": "2026-10-14"},
        },
        movement="In",
    )
    assert older == listed
    with pytest.raises(OperationError, match="more than one"):
        standing_from_book(
            [
                {"amount": 70, "frequency": "monthly", "next_date": "2026-10-14"},
                {"amount": 30, "frequency": "monthly", "next_date": "2026-10-20"},
            ],
            movement="In",
        )
    with pytest.raises(OperationError, match="no standing amount"):
        standing_from_book([], movement="Out")


def test_an_illustration_names_the_existing_client() -> None:
    body = illustration_body(client_id="C1", wrapper="ISA", node_id="21")
    assert body["clientInfo"] == {"id": "C1"}
    assert body["calculating"] == "ASYNCHRONOUS"
    assert body["wrapperType"] == "ISA"
    with pytest.raises(OperationError, match="fee"):
        fee_patch("")
