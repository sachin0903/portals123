"""SEI enrolment sends one client and one account, and names what the firm has not configured."""

from datetime import date

import pytest

from app.actions.attempt import OperationError
from app.integrations.sei.enrol import (
    _aml_reviewed,
    account_open_for_pay_to,
    allowable_currencies,
    catalogue_defaults,
    currency_unavailable,
    enrolment_attributes,
    enrolment_attributes_for,
    enrolment_outcome,
    enrolment_refusal,
    enrolment_settled,
    opened_identifiers,
    pair_by_name,
    pay_to_should_wait,
    profile_update,
    resume_business_key,
    sei_date,
    sei_email,
    still_inside_enrolment,
)
from app.integrations.sei.grants import configured_team_grants, representative_grant_day


def _config() -> dict[str, object]:
    return {
        "firm_id": "3106",
        "client_template_id": "2981271",
        "individual_classification_id": "48527",
        "account_org_id": "704254",
        "client_team_ids": ["59282"],
        "account_team_ids": ["59261"],
    }


def _client() -> dict[str, object]:
    return {
        "uid": "abc123def456",
        "title": "Mrs",
        "first_name": "Ada",
        "surname": "Lovelace",
        "email": "ada@example.test",
        "dob": "1990-12-01",
        "address_line1": "1 High Street",
        "address_city": "London",
        "address_postcode": "SW1A 1AA",
        "fatca_status": "w8",
        "country_of_birth": "GB",
        "nationality": ["GB"],
        "national_identifier": "AB123456C",
        "national_identifier_type": "National Insurance Number",
        "tax_residency": ["GB"],
    }


def test_a_configured_gia_becomes_one_enrolment() -> None:
    body = enrolment_attributes(
        _client(),
        {
            "uid": "acc123",
            "name": "Ada GIA",
            "product_type": "GIA",
            "scheme_product_id": "2981459",
            "investment_capacity": "advisory",
        },
        _config(),
    )
    assert body["usedBy"] == "Graphene"
    assert body["bypassApproval"] is True
    assert body["clientList"][0]["newClient"]["individualDetails"]["titleId"] == 2
    assert body["clientList"][0]["newClient"]["externalPartyId"] == "abc123def456"
    assert body["clientList"][0]["newClient"]["individualDetails"]["mailingLabelName"] == (
        "Mrs Ada Lovelace"
    )
    assert body["accountList"][0]["accountTypeId"] == "157"
    assert body["accountList"][0]["firmInvestmentCapacityId"] == 4
    assert "genderId" not in body["clientList"][0]["newClient"]["individualDetails"]
    assert body["accountList"][0]["portfolios"][0]["principalPortfolio"]["portfolioName"] == (
        "Ada GIA Capital"
    )


def test_a_declared_non_us_person_is_certified_as_w8() -> None:
    # "Not a US person" is the FATCA answer SEI needs: with no explicit W-9/W-8 selection on the
    # record, the us_person answer derives a W-8 (code 2) rather than failing the readiness check
    # for a missing fatca_status (0134: "2 = W-8, everyone else").
    client = {k: v for k, v in _client().items() if k != "fatca_status"}
    client["us_person"] = False
    body = enrolment_attributes(
        client,
        {
            "uid": "acc123",
            "name": "Ada GIA",
            "product_type": "GIA",
            "scheme_product_id": "2981459",
            "investment_capacity": "advisory",
        },
        _config(),
    )
    details = body["clientList"][0]["newClient"]["individualDetails"]
    assert details["certifiedFATCAComplianceId"] == "2"


def test_a_missing_fatca_status_and_no_us_answer_is_still_refused() -> None:
    # Without a FATCA status AND without a us_person answer, SEI still cannot certify — the
    # readiness check must refuse, not silently enrol.
    client = {k: v for k, v in _client().items() if k != "fatca_status"}
    with pytest.raises(OperationError, match="FATCA"):
        enrolment_attributes(
            client,
            {
                "uid": "acc123",
                "name": "Ada GIA",
                "product_type": "GIA",
                "scheme_product_id": "2981459",
                "investment_capacity": "advisory",
            },
            _config(),
        )


def test_the_adviser_is_the_investment_decision_maker() -> None:
    client = {**_client(), "sei_employee_id": "58988820"}
    bare = enrolment_attributes(
        client,
        {
            "uid": "acc123",
            "name": "Ada GIA",
            "product_type": "GIA",
            "scheme_product_id": "2981459",
            "investment_capacity": "advisory",
            "owners": [client],
        },
        _config(),
    )
    assert "teamServicingRelationshipList" not in bare["clientList"][0]["newClient"]
    assert "teamServicingRelationshipList" not in bare["accountList"][0]

    typed = {
        **_config(),
        "client_team_ids": [{"servicingRelationshipId": 1, "teamId": 59282}],
        "account_team_ids": [{"servicingRelationshipId": 2, "teamId": 59261}],
    }
    body = enrolment_attributes(
        client,
        {
            "uid": "acc123",
            "name": "Ada GIA",
            "product_type": "GIA",
            "scheme_product_id": "2981459",
            "investment_capacity": "advisory",
            "owners": [client],
        },
        typed,
    )
    assert body["clientList"][0]["newClient"]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 1, "teamId": 59282}
    ]
    assert body["accountList"][0]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 2, "teamId": 59261}
    ]
    decided = {
        **typed,
        "investment_decision_type_id": "59306",
    }
    with_adviser = enrolment_attributes(
        client,
        {
            "uid": "acc123",
            "name": "Ada GIA",
            "product_type": "GIA",
            "scheme_product_id": "2981459",
            "investment_capacity": "advisory",
            "owners": [client],
        },
        decided,
    )
    assert with_adviser["accountList"][0]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 2, "teamId": 59261}
    ]
    assert with_adviser["accountList"][0]["employeeServicingRelationshipList"] == [
        {"servicingRelationshipId": 59306, "employeeEntityId": 58988820}
    ]


def test_a_bare_firm_team_is_not_sent() -> None:
    body = enrolment_attributes(
        _client(),
        {
            "uid": "acc123",
            "name": "Ada GIA",
            "product_type": "GIA",
            "scheme_product_id": "2981459",
            "investment_capacity": "advisory",
        },
        _config(),
    )
    assert "teamServicingRelationshipList" not in body["clientList"][0]["newClient"]
    assert "teamServicingRelationshipList" not in body["accountList"][0]


def test_a_sipp_opens_as_an_investment_account() -> None:
    body = enrolment_attributes(
        _client(),
        {
            "name": "Ada SIPP",
            "product_type": "SIPP",
            "scheme_product_id": "2981580",
            "investment_capacity": "advisory",
        },
        _config(),
    )
    account = body["accountList"][0]
    assert account["accountTypeId"] == "157"
    assert account["accountTemplateId"] == "2981580"
    assert account["accountClassificationId"] == "48527"


def test_a_sipp_uses_the_firms_pension_classification() -> None:
    config = {
        **_config(),
        "pension_classification_id": "48605",
        "sipp_subclassification_id": "59385",
    }
    body = enrolment_attributes(
        _client(),
        {
            "name": "Ada SIPP",
            "product_type": "SIPP",
            "scheme_product_id": "2981580",
            "investment_capacity": "advisory",
        },
        config,
    )
    assert body["accountList"][0]["accountClassificationId"] == "48605"
    assert body["accountList"][0]["accountSubClassificationId"] == "59385"


def test_a_sipp_sends_the_hmrc_subclassification_and_the_client_identity() -> None:
    client = {**_client(), "country_of_birth": "NZ", "nationality": ["GB", "NZ"]}
    config = {
        **_config(),
        "pension_classification_id": "48605",
        "sipp_subclassification_id": "59385",
    }
    body = enrolment_attributes(
        client,
        {
            "name": "Ada SIPP",
            "product_type": "SIPP",
            "scheme_product_id": "2981580",
            "investment_capacity": "advisory",
        },
        config,
    )
    account = body["accountList"][0]
    assert account["accountSubClassificationId"] == "59385"
    details = body["clientList"][0]["newClient"]["individualDetails"]
    assert details["countryOfBirth"] == "NZ"
    assert details["countryOfCitizenshipFirstPassport"] == "GB"
    assert body["clientList"][0]["newClient"]["taxInformation"]["identifier"] == {
        "identifierISOCountry": "GB",
        "taxIdTypeId": 7,
        "taxIdNumber": "AB123456C",
    }


def test_a_nino_is_type_7_in_gb_whatever_the_stored_spelling() -> None:
    client = {
        **_client(),
        "national_identifier_type": "nino",
        "tax_residency": ["FR", "GB"],
        "address_line2": "Murdishaw",
        "address_postcode": "WA7 6DR",
        "mobile": "+447840164849",
    }
    body = enrolment_attributes(client, _gia(client), _config())
    new_client = body["clientList"][0]["newClient"]
    assert new_client["taxInformation"]["identifier"] == {
        "identifierISOCountry": "GB",
        "taxIdTypeId": 7,
        "taxIdNumber": "AB123456C",
    }
    assert new_client["address"]["postalCode"] == "WA7 6DR"
    assert new_client["address"]["addressLine2"] == "Murdishaw"
    assert new_client["phone"] == {"phoneMethodId": 2, "localPhoneNumber": "7840164849"}


def test_title_marital_status_and_aml_risk_are_sei_codes() -> None:
    client = {
        **_client(),
        "title": "Prof.",
        "marital_status": "widowed",
        "aml_risk": "very_high",
        "aml_risk_reviewed": "2026-10-15T09:00:00+00:00",
    }
    body = enrolment_attributes(client, _gia(client), _config())
    details = body["clientList"][0]["newClient"]["individualDetails"]
    assert details["titleId"] == 12
    assert details["maritalStatusId"] == 10
    assert body["clientList"][0]["newClient"]["clientAMLRiskRating"] == 3
    assert body["clientList"][0]["newClient"]["clientAMLRiskLastReviewDate"] == "2026-10-15"
    unset = {**_client(), "title": "Mx", "marital_status": "civil_partner", "aml_risk": "not_set"}
    plain = enrolment_attributes(unset, _gia(unset), _config())["clientList"][0]["newClient"]
    assert "titleId" not in plain["individualDetails"]
    assert "maritalStatusId" not in plain["individualDetails"]
    assert "clientAMLRiskRating" not in plain


def test_an_aml_review_after_seis_midnight_is_sent_as_their_today() -> None:
    reviewed = {"aml_risk_reviewed": "2026-10-16T00:48:00+00:00"}
    assert _aml_reviewed(reviewed, today=date(2026, 10, 15)) == "2026-10-15"
    assert _aml_reviewed(reviewed, today=date(2026, 10, 16)) == "2026-10-16"
    earlier = {"aml_risk_reviewed": "2026-10-01T18:00:00+00:00"}
    assert _aml_reviewed(earlier, today=date(2026, 10, 16)) == "2026-10-01"


def test_a_gia_for_someone_outside_the_uk_does_not_need_a_nino() -> None:
    client = {
        **_client(),
        "nationality": ["NZ"],
        "tax_residency": ["NZ"],
        "national_identifier": "123456789",
        "national_identifier_type": "IRD number",
        "passport_country": "NZ",
    }
    body = enrolment_attributes(client, _gia(client), _config())
    assert "taxInformation" not in body["clientList"][0]["newClient"]


def test_an_isa_still_needs_a_uk_nino_when_the_identifier_is_not_one() -> None:
    client = {
        **_client(),
        "nationality": ["NZ"],
        "tax_residency": ["NZ"],
        "national_identifier": "123456789",
        "national_identifier_type": "IRD number",
    }
    with pytest.raises(OperationError, match="National Insurance number"):
        enrolment_attributes(
            client,
            {
                "name": "Ada ISA",
                "product_type": "ISA",
                "scheme_product_id": "2981459",
                "investment_capacity": "advisory",
            },
            _config(),
        )


def test_a_uk_address_without_a_postcode_is_named() -> None:
    client = {**_client(), "address_postcode": ""}
    with pytest.raises(OperationError, match="postcode"):
        enrolment_attributes(client, _gia(client), _config())


def test_a_uk_gia_without_a_nino_is_named() -> None:
    client = {**_client(), "national_identifier": "", "national_identifier_type": ""}
    with pytest.raises(OperationError, match="National Insurance number"):
        enrolment_attributes(client, _gia(client), _config())


def test_a_separate_nino_is_what_sei_is_sent() -> None:
    client = {
        **_client(),
        "nationality": ["NZ", "GB"],
        "tax_residency": ["FR"],
        "national_identifier": "FR123",
        "national_identifier_type": "Tax number",
        "uk_national_insurance": "AB123456C",
    }
    body = enrolment_attributes(client, _gia(client), _config())
    assert body["clientList"][0]["newClient"]["taxInformation"]["identifier"]["taxIdNumber"] == (
        "AB123456C"
    )


def test_an_in_progress_process_is_not_settled_until_it_finishes_or_mints_ids() -> None:
    assert enrolment_settled({"businessProcessStatus": "Enrollment Inprocess"}) is False
    assert enrolment_settled({"businessProcessStatus": "Validation Failed"}) is True
    assert enrolment_settled({"businessProcessStatus": "Enrollment Completed"}) is True
    assert (
        enrolment_settled(
            {"enrollmentData": {"enrollmentIdentifiers": {"primeryEndClientId": "4471"}}}
        )
        is True
    )


def test_a_minted_account_is_not_open_for_a_pay_to_until_sei_says_so() -> None:
    wip = {"accountOverviewDetail": {"accountStatus": "Work in Progress"}}
    opened = {"accountOverviewDetail": {"accountStatus": "Open"}}
    assert account_open_for_pay_to(wip) is False
    assert account_open_for_pay_to(opened) is True
    assert account_open_for_pay_to({"accountOverviewDetail": {"accountStatus": "Active"}}) is True
    assert account_open_for_pay_to({}) is False
    assert pay_to_should_wait("Work in Progress") is True
    assert pay_to_should_wait("Work In Progress Complete") is True
    assert pay_to_should_wait("") is True
    assert pay_to_should_wait("WIP Submitted") is True
    assert pay_to_should_wait("Open") is False
    assert pay_to_should_wait("Closed") is False
    reason = (
        "The specified party is still being enrolled and cannot be accessed "
        "outside the enrollment process. Account Status: Work in Progress."
    )
    assert still_inside_enrolment(reason) is True
    assert still_inside_enrolment("Invalid sort code") is False


@pytest.mark.asyncio
async def test_the_pay_to_waits_until_the_account_is_open() -> None:
    from sei import SeiApiError

    from app.integrations.sei.actions import wait_until_accounts_open

    replies: list[object] = [
        SeiApiError(
            "POST failed: 400 The specified party is still being enrolled. "
            "Account Status: Work in Progress.",
            status=400,
        ),
        {"accountOverviewDetail": {"accountStatus": "Work in Progress"}},
        {"accountOverviewDetail": {"accountStatus": "Open"}},
    ]

    class Api:
        def __init__(self) -> None:
            self.calls = 0

        async def account_details(self, _firm: str, _number: str, **_kwargs: object) -> dict:
            self.calls += 1
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            assert isinstance(reply, dict)
            return reply

    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    api = Api()
    await wait_until_accounts_open(api, "3108", ["AIO00215"], polls=5, pause_seconds=5, sleep=sleep)
    assert api.calls == 3
    assert slept == [5, 5]


@pytest.mark.asyncio
async def test_a_pay_to_is_not_sent_while_the_account_stays_work_in_progress() -> None:
    from app.integrations.sei.actions import wait_until_accounts_open

    class Api:
        async def account_details(self, _firm: str, _number: str, **_kwargs: object) -> dict:
            return {"accountOverviewDetail": {"accountStatus": "Work in Progress"}}

    async def sleep(_seconds: float) -> None:
        return None

    with pytest.raises(OperationError, match="was not sent") as exc:
        await wait_until_accounts_open(
            Api(), "3108", ["AIO00215"], polls=3, pause_seconds=5, sleep=sleep
        )
    assert exc.value.permanent is False


@pytest.mark.asyncio
async def test_a_closed_account_does_not_wait_for_a_pay_to() -> None:
    from app.integrations.sei.actions import wait_until_accounts_open

    class Api:
        async def account_details(self, _firm: str, _number: str, **_kwargs: object) -> dict:
            return {"accountOverviewDetail": {"accountStatus": "Closed"}}

    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    with pytest.raises(OperationError, match="Closed") as exc:
        await wait_until_accounts_open(
            Api(), "3108", ["AIO00215"], polls=5, pause_seconds=5, sleep=sleep
        )
    assert exc.value.permanent is True
    assert slept == []


@pytest.mark.asyncio
async def test_wip_submitted_waits_and_then_sends() -> None:
    from app.integrations.sei.actions import wait_until_accounts_open

    replies = [
        {"accountOverviewDetail": {"accountStatus": "WIP Submitted"}},
        {"accountOverviewDetail": {"accountStatus": "WIP Submitted"}},
        {"accountOverviewDetail": {"accountStatus": "Open"}},
    ]

    class Api:
        def __init__(self) -> None:
            self.calls = 0

        async def account_details(self, _firm: str, _number: str, **_kwargs: object) -> dict:
            reply = replies[self.calls]
            self.calls += 1
            return reply

    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    api = Api()
    await wait_until_accounts_open(
        api, "3108", ["AIO00216"], polls=10, pause_seconds=5, sleep=sleep
    )
    assert api.calls == 3
    assert slept == [5, 5]


def test_an_unsettled_enrolment_is_not_written_and_a_finished_one_is() -> None:
    assert (
        enrolment_outcome({"businessProcessStatus": "Enrollment Inprocess"})
        == "SEI has not finished this enrolment. Nothing was recorded."
    )
    assert enrolment_outcome({"businessProcessStatus": "Enrollment Completed"}) is None
    refused = enrolment_outcome(
        {
            "businessProcessStatus": "Validation Failed",
            "validationErrors": [{"detail": "E-mail is not valid."}],
        }
    )
    assert refused == "E-mail is not valid."


def test_a_validation_failure_is_a_refusal_and_an_open_process_is_not() -> None:
    refused = enrolment_refusal(
        {
            "businessProcessStatus": "Validation Failed",
            "validationErrors": [{"detail": "E-mail is not valid."}],
        }
    )
    assert refused == "E-mail is not valid."
    assert enrolment_refusal({"businessProcessStatus": "Enrollment Inprocess"}) is None


def test_a_firm_with_one_currency_adds_nothing_further() -> None:
    assert allowable_currencies(["GBP"], "GBP") == []
    assert allowable_currencies(["gbp", "GBP"], "GBP") == []
    assert allowable_currencies(None, "GBP") == []


def test_a_firms_other_currencies_are_added_and_the_base_is_not() -> None:
    assert allowable_currencies(["GBP", "EUR"], "GBP") == ["EUR"]
    assert allowable_currencies(["gbp", "eur", "usd"], "EUR") == ["GBP", "USD"]
    assert allowable_currencies(["EUR", "USD"], "GBP") == ["EUR", "USD"]


def test_a_currency_sei_will_not_add_is_named_and_left_off() -> None:
    refusal = (
        'POST /addallowablecurrencyservice failed: 400 {"errors":[{"detail":'
        '"Invalid iso currency code  : ALL"}]}'
    )
    assert currency_unavailable(refusal) is True
    assert currency_unavailable("Invalid account number") is False


def test_an_unfinished_enrolment_is_resumed_and_a_refusal_is_not() -> None:
    open_error = "SEI has not finished this enrolment. Nothing was recorded. (ENR-1)"
    assert resume_business_key(open_error, {"businessKey": "ENR-1"}) == "ENR-1"
    assert resume_business_key(open_error, '{"businessKey": "ENR-1"}') == "ENR-1"
    assert resume_business_key("E-mail is not valid. (ENR-1)", {"businessKey": "ENR-1"}) is None
    assert resume_business_key(open_error, {}) is None
    assert resume_business_key(open_error, "not-json") is None
    assert resume_business_key(None, {"businessKey": "ENR-1"}) is None
    currency_error = "SEI opened the account but could not add allowable currencies (ENR-9): no"
    assert resume_business_key(currency_error, {"businessKey": "ENR-9"}) == "ENR-9"
    pay_to_error = "SEI opened the account but could not create the pay-to profile (ENR-9): no"
    assert resume_business_key(pay_to_error, {"businessKey": "ENR-9"}) == "ENR-9"


def test_opened_accounts_are_paired_by_name_not_by_position() -> None:
    gia = {"accountNumber": "CRFP00040"}
    sipp = {"accountNumber": "CRFP00041"}
    paired = pair_by_name(
        ["Mark O'Client GIA", "Mark O'Client SIPP"],
        [("Mark O'Client SIPP", sipp), ("Mark O'Client GIA", gia)],
    )
    assert [item["accountNumber"] for item in paired] == ["CRFP00040", "CRFP00041"]
    assert pair_by_name(["Mark O'Client GIA"], [("Something else", gia)]) == []
    assert (
        pair_by_name(["Mark O'Client GIA", "Mark O'Client GIA"], [("Mark O'Client GIA", gia)]) == []
    )
    assert (
        pair_by_name(
            ["Mark O'Client GIA", "Mark O'Client SIPP"],
            [("Mark O'Client GIA", gia), ("Mark O'Client GIA", sipp)],
        )
        == []
    )


def test_opened_identifiers_read_the_client_and_the_accounts() -> None:
    client, accounts = opened_identifiers(
        {
            "enrollmentData": {
                "enrollmentIdentifiers": {
                    "primeryEndClientId": "4471",
                    "accounts": [
                        {
                            "accountNumber": "CRFP00999",
                            "globalIdOfPortfolios": [{"globalIdOfPortfolio": "90040-1"}],
                        }
                    ],
                }
            }
        }
    )
    assert client == "4471"
    assert accounts[0]["accountNumber"] == "CRFP00999"
    assert opened_identifiers({"businessProcessStatus": "Validation Failed"}) == (None, [])


def test_a_plus_address_is_the_mailbox_sei_accepts() -> None:
    assert sei_email("mark+oclient@grapheneplatforms.com") == "mark@grapheneplatforms.com"
    assert sei_email("ada@example.test") == "ada@example.test"


def test_ssas_needs_a_national_insurance_number() -> None:
    from app.integrations.sei.enrol import wrapper_flags

    assert wrapper_flags(["SSAS"]) == {"has_isa_or_pension": True, "has_gia": False}
    assert wrapper_flags(["GIA"])["has_isa_or_pension"] is False
    assert wrapper_flags(["JISA"])["has_isa_or_pension"] is False


def test_a_stored_enrolment_drops_the_aml_band_the_firm_can_read() -> None:
    from app.integrations.sei.enrol import without_firm_aml

    sent = {
        "clientList": [
            {
                "newClient": {
                    "individualDetails": {"firstName": "Ada"},
                    "clientAMLRiskRating": 2,
                    "clientAMLRiskLastReviewDate": "2026-10-01",
                }
            }
        ]
    }
    stored = without_firm_aml(sent)
    client = stored["clientList"][0]["newClient"]
    assert "clientAMLRiskRating" not in client
    assert "clientAMLRiskLastReviewDate" not in client
    assert client["individualDetails"]["firstName"] == "Ada"
    assert sent["clientList"][0]["newClient"]["clientAMLRiskRating"] == 2


def test_the_employee_picker_uses_the_full_grant_file(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.sei.enrol import employees_for_picker
    from app.integrations.sei.grants import Grant

    monkeypatch.setattr(
        "app.integrations.sei.enrol._book_grants",
        lambda _conn, _firm: ([], [Grant("account", "1", "58304024", 4, "")]),
    )

    class _Conn:
        def execute(self, sql: str, params: list[str]) -> "_Conn":
            assert "ref.dsr_teams" in sql
            assert params == ["3108"]
            return self

        def fetchall(self) -> list[tuple[str, str]]:
            return [("58304024", "Fiorenzo")]

    assert employees_for_picker(_Conn(), "3108") == [("58304024", "Fiorenzo")]


def test_the_employee_picker_falls_back_only_without_a_full_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.integrations.sei.enrol import employees_for_picker

    monkeypatch.setattr("app.integrations.sei.enrol._book_grants", lambda _conn, _firm: None)

    class _Conn:
        def execute(self, sql: str, params: list[str]) -> None:
            raise AssertionError(sql)

    assert employees_for_picker(_Conn(), "3108") is None


def test_an_unmapped_wrapper_and_a_missing_template_are_named() -> None:
    with pytest.raises(OperationError, match="account type"):
        enrolment_attributes(
            _client(),
            {
                "name": "Ada SSAS",
                "product_type": "SSAS",
                "scheme_product_id": "9",
                "investment_capacity": "advisory",
            },
            _config(),
        )
    with pytest.raises(OperationError, match="client template"):
        enrolment_attributes(_client(), {"product_type": "GIA", "scheme_product_id": "1"}, {})


def _organisation() -> dict[str, object]:
    return {
        "uid": "org123org456",
        "type": "organisation",
        "sub_type": "company",
        "name": "Lovelace Ltd",
        "email": "hello@example.test",
        "address_line1": "1 High Street",
        "address_city": "London",
        "address_postcode": "SW1A 1AA",
        "fatca_status": "w8",
    }


def test_an_organisation_uses_its_own_client_template() -> None:
    config = {
        **_config(),
        "client_template_individual_id": "2981271",
        "client_template_organisation_id": "2981999",
    }
    body = enrolment_attributes(
        _organisation(),
        {
            "uid": "acc123",
            "name": "Lovelace GIA",
            "product_type": "GIA",
            "scheme_product_id": "2981459",
            "investment_capacity": "advisory",
        },
        config,
    )
    created = body["clientList"][0]["newClient"]
    assert created["clientTypeId"] == "6"
    assert created["clientTemplateId"] == "2981999"
    assert created["organizationDetails"]["organizationName"] == "Lovelace Ltd"
    assert created["organizationDetails"]["organizationTypeId"] == "4"
    assert "individualDetails" not in created

    person = enrolment_attributes(
        _client(),
        {
            "uid": "acc123",
            "name": "Ada GIA",
            "product_type": "GIA",
            "scheme_product_id": "2981459",
            "investment_capacity": "advisory",
        },
        config,
    )
    assert person["clientList"][0]["newClient"]["clientTypeId"] == "5"
    assert person["clientList"][0]["newClient"]["clientTemplateId"] == "2981271"


def test_an_organisation_does_not_reuse_the_individual_template() -> None:
    config = {**_config(), "client_template_individual_id": "2981271"}
    config.pop("client_template_id")
    with pytest.raises(OperationError, match="organisation client template"):
        enrolment_attributes(
            _organisation(),
            {
                "name": "Lovelace GIA",
                "product_type": "GIA",
                "scheme_product_id": "1",
                "investment_capacity": "advisory",
            },
            config,
        )


def test_a_trust_and_a_charity_use_seis_organisation_types() -> None:
    account = {
        "uid": "acc123",
        "name": "Trust GIA",
        "product_type": "GIA",
        "scheme_product_id": "1",
        "investment_capacity": "advisory",
    }
    config = {**_config(), "client_template_organisation_id": "2981999"}
    trust = enrolment_attributes(
        {**_organisation(), "sub_type": "trust", "name": "Lovelace Trust"}, account, config
    )
    charity = enrolment_attributes(
        {**_organisation(), "sub_type": "charity", "name": "Lovelace Charity"}, account, config
    )
    assert trust["clientList"][0]["newClient"]["organizationDetails"]["organizationTypeId"] == "14"
    assert charity["clientList"][0]["newClient"]["organizationDetails"]["organizationTypeId"] == "2"
    with pytest.raises(OperationError, match="organisation type"):
        enrolment_attributes({**_organisation(), "sub_type": None}, account, config)


def test_a_firm_with_no_servicing_grant_can_still_enrol() -> None:
    config = {key: value for key, value in _config().items() if not key.endswith("team_ids")}
    body = enrolment_attributes(
        _client(),
        {
            "name": "Ada GIA",
            "product_type": "GIA",
            "scheme_product_id": "9",
            "investment_capacity": "advisory",
        },
        config,
    )
    client = body["clientList"][0]["newClient"]
    assert "teamServicingRelationshipList" not in client
    assert "teamServicingRelationshipList" not in body["accountList"][0]


def test_a_bare_adviser_team_is_not_sent() -> None:
    config = {key: value for key, value in _config().items() if not key.endswith("team_ids")}
    client = {**_client(), "adviser_dsr": "704899"}
    body = enrolment_attributes(
        client,
        {
            "name": "Ada GIA",
            "product_type": "GIA",
            "scheme_product_id": "9",
            "investment_capacity": "advisory",
            "owners": [client],
        },
        config,
    )
    assert "teamServicingRelationshipList" not in body["clientList"][0]["newClient"]
    assert "teamServicingRelationshipList" not in body["accountList"][0]


def _person(uid: str, first: str, surname: str) -> dict[str, object]:
    return {
        "uid": uid,
        "first_name": first,
        "surname": surname,
        "email": f"{first.lower()}@example.test",
        "dob": "1990-01-01",
        "address_line1": "1 High Street",
        "address_city": "London",
        "address_postcode": "SW1A 1AA",
        "fatca_status": "w8",
        "country_of_birth": "GB",
        "nationality": ["GB"],
        "national_identifier": "AB123456C",
        "national_identifier_type": "National Insurance Number",
        "tax_residency": ["GB"],
    }


def test_a_household_enrols_once() -> None:
    parent_one = _person("parent1uid01", "Parent", "One")
    parent_two = _person("parent2uid02", "Parent", "Two")
    child = _person("childuid0003", "Ada", "Child")
    child["dob"] = "2016-04-01"
    config = {**_config(), "joint_ownership_type_id": "123"}
    body = enrolment_attributes_for(
        [parent_one, parent_two, child],
        [
            {
                "uid": "gia-p1",
                "name": "Parent One GIA",
                "product_type": "GIA",
                "scheme_product_id": "1",
                "investment_capacity": "advisory",
                "owners": [parent_one],
                "application_uid": "appuid000001",
            },
            {
                "uid": "gia-p2",
                "name": "Parent Two GIA",
                "product_type": "GIA",
                "scheme_product_id": "1",
                "investment_capacity": "advisory",
                "owners": [parent_two],
            },
            {
                "uid": "gia-joint",
                "name": "Joint GIA",
                "product_type": "GIA",
                "scheme_product_id": "1",
                "investment_capacity": "advisory",
                "owners": [parent_one, parent_two],
            },
            {
                "uid": "jisa",
                "name": "Child JISA",
                "product_type": "JISA",
                "scheme_product_id": "1",
                "investment_capacity": "advisory",
                "owners": [child],
                "registered_contacts": [parent_one],
            },
        ],
        config,
    )
    assert [item["newClient"]["externalPartyId"] for item in body["clientList"]] == [
        "parent1uid01",
        "parent2uid02",
        "childuid0003",
    ]
    assert body["caseReference"] == "appuid000001"
    assert [item["accountTypeId"] for item in body["accountList"]] == ["157", "157", "157", "173"]
    joint = body["accountList"][2]["accountOwner"]
    assert joint["primaryOwnerExternalId"] == "parent1uid01"
    assert joint["jointAccountOwner"]["jointOwners"] == [{"jointOwnerExternalId": "parent2uid02"}]
    jisa = body["accountList"][3]
    assert jisa["accountOwner"] == {"primaryOwnerExternalId": "childuid0003"}
    assert jisa["thirdPartyRegisteredContact"]["existingThirdPartyRegisteredContact"] == {
        "externalPartyId": "parent1uid01"
    }
    with pytest.raises(OperationError, match="joint ownership type"):
        enrolment_attributes_for(
            [parent_one, parent_two],
            [
                {
                    "name": "Joint GIA",
                    "product_type": "GIA",
                    "scheme_product_id": "1",
                    "investment_capacity": "advisory",
                    "owners": [parent_one, parent_two],
                }
            ],
            _config(),
        )


def test_the_onshore_org_opens_the_account() -> None:
    config = _config()
    del config["account_org_id"]
    body = enrolment_attributes(
        _client(),
        {
            "uid": "acc123",
            "name": "Ada GIA",
            "product_type": "GIA",
            "scheme_product_id": "2981459",
            "investment_capacity": "advisory",
            "residing_org_id": "704824",
        },
        config,
    )
    assert body["accountList"][0]["residingOrgId"] == "704824"


def test_a_known_client_is_not_created_again() -> None:
    body = enrolment_attributes(
        {**_client(), "sei_id": "known"},
        {
            "uid": "acc123",
            "name": "Ada GIA",
            "product_type": "GIA",
            "scheme_product_id": "1",
            "investment_capacity": "advisory",
        },
        _config(),
    )
    assert body["clientList"][0]["existingClient"]["clientExternalPartyId"] == "abc123def456"


def test_one_catalogue_answer_is_used_and_two_are_not() -> None:
    grants = [
        ("client", "59299", "200194", 5),
        ("account", "59300", "200194", 5),
        ("account", "59304", "704877", 1),
    ]
    filled, _teams = catalogue_defaults(
        {"firm_id": "3116"},
        client_templates=["2981579"],
        individual_codes=["48598"],
        grants=grants,
    )
    assert filled["client_template_id"] == "2981579"
    assert filled["client_template_individual_id"] == "2981579"
    assert filled["client_template_organisation_id"] == "2981579"
    assert filled["individual_classification_id"] == "48598"
    assert filled["client_team_ids"] == [{"servicingRelationshipId": 59299, "teamId": 200194}]
    assert filled["account_team_ids"] == [{"servicingRelationshipId": 59300, "teamId": 200194}]
    tied, _teams = catalogue_defaults(
        {"firm_id": "3116"},
        client_templates=["1", "2"],
        individual_codes=[],
        grants=[("client", "1", "2", 3), ("client", "3", "4", 3)],
    )
    assert "client_template_id" not in tied
    chosen, _teams = catalogue_defaults(
        {"firm_id": "3108", "client_template_id": "2981279"},
        client_templates=["2981279", "2981280"],
        individual_codes=["1"],
        grants=[],
    )
    assert chosen["client_template_id"] == "2981279"
    assert "client_template_individual_id" not in chosen
    assert "client_template_organisation_id" not in chosen
    assert "client_team_ids" not in tied


def test_selected_dsrs_are_the_grants_on_a_new_client_and_account() -> None:
    # Custody is the most-used grant. The tenant picked the IFA team, so that
    # is what both sides are opened with — each under its own relationship id.
    grants = [
        ("account", "59283", "704764", 503, "SEI Custody"),
        ("client", "59282", "704764", 395, "SEI Custody"),
        ("account", "59254", "704079", 12, "IFA Firm"),
        ("client", "59260", "999001", 4, "IFA Firm"),
    ]
    filled, _teams = catalogue_defaults(
        {"firm_id": "3106", "dsr_ids": ["704079", "704718"]},
        client_templates=["2981271"],
        individual_codes=["48527"],
        grants=[
            *grants,
            ("account", "59261", "704718", 40, "Client Service Team"),
        ],
    )
    assert filled["client_team_ids"] == [
        {"servicingRelationshipId": 59260, "teamId": 704079},
        {"servicingRelationshipId": 59261, "teamId": 704718},
    ]
    assert filled["account_team_ids"] == [
        {"servicingRelationshipId": 59254, "teamId": 704079},
        {"servicingRelationshipId": 59261, "teamId": 704718},
    ]
    client = _client()
    body = enrolment_attributes(
        client,
        {
            "uid": "acc123",
            "name": "Ada GIA",
            "product_type": "GIA",
            "scheme_product_id": "2981459",
            "investment_capacity": "advisory",
            "owners": [client],
            "residing_org_id": "704254",
        },
        filled,
    )
    assert body["clientList"][0]["newClient"]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 59260, "teamId": 704079},
        {"servicingRelationshipId": 59261, "teamId": 704718},
    ]
    assert body["accountList"][0]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 59254, "teamId": 704079},
        {"servicingRelationshipId": 59261, "teamId": 704718},
    ]


def test_a_team_with_no_client_relationship_still_goes_on_the_client() -> None:
    grants = [("account", "59254", "704079", 12, "IFA Firm")]
    client, account = configured_team_grants(["704079"], grants)
    assert client == [{"servicingRelationshipId": 59254, "teamId": 704079}]
    assert account == [{"servicingRelationshipId": 59254, "teamId": 704079}]


def _gia(client: dict[str, object], **extra: object) -> dict[str, object]:
    return {
        "uid": "acc123",
        "name": "Ada GIA",
        "product_type": "GIA",
        "scheme_product_id": "2981459",
        "investment_capacity": "advisory",
        "owners": [client],
        "residing_org_id": "704254",
        **extra,
    }


def test_gender_is_sent_on_the_enrolment() -> None:
    body = enrolment_attributes(
        {**_client(), "gender": "male"},
        _gia(_client()),
        _config(),
    )
    assert body["clientList"][0]["newClient"]["individualDetails"]["genderId"] == 1


def test_a_model_adds_its_dsrs_to_the_client_and_the_account() -> None:
    grants = [
        ("account", "59254", "704079", 12, "IFA Firm"),
        ("client", "59260", "704079", 4, "IFA Firm"),
        ("account", "59261", "704718", 40, "Client Service Team"),
        ("client", "59262", "704718", 8, "Client Service Team"),
    ]
    filled, teams = catalogue_defaults(
        {
            "firm_id": "3106",
            "dsr_ids": ["704079"],
            "model_dsrs": {"PDdVYya8B8hb": ["704718"]},
        },
        client_templates=["2981271"],
        individual_codes=["48527"],
        grants=grants,
    )
    client = _client()
    using = enrolment_attributes(client, _gia(client, model_uid="PDdVYya8B8hb"), filled, teams)
    other = enrolment_attributes(client, _gia(client, model_uid="OtherModel01"), filled, teams)
    assert using["accountList"][0]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 59254, "teamId": 704079},
        {"servicingRelationshipId": 59261, "teamId": 704718},
    ]
    assert using["clientList"][0]["newClient"]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 59260, "teamId": 704079},
        {"servicingRelationshipId": 59262, "teamId": 704718},
    ]
    assert other["accountList"][0]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 59254, "teamId": 704079}
    ]


def test_model_teams_are_resolved_when_the_catalogue_is_already_filled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A firm that already stored every scalar still has to resolve model DSRs.

    Those pairs are not on the config. Skipping the warehouse when no custody
    named teams used to drop them.
    """
    from datetime import date

    from app.integrations.sei.enrol import fill_from_catalogue
    from app.lake import core

    class _Rows:
        def __init__(self, rows: list[tuple[object, ...]]) -> None:
            self._rows = rows

        def fetchall(self) -> list[tuple[object, ...]]:
            return self._rows

    class _Conn:
        def execute(self, sql: str, params: object = None) -> _Rows:
            del params
            if "GROUP BY dt" in sql:
                return _Rows([(date(2026, 1, 1), 10)])
            if "WHERE dt = ?" in sql:
                return _Rows(
                    [
                        ("account", "team", "59261", "704718", 40, "Client Service Team"),
                        ("client", "team", "59262", "704718", 8, "Client Service Team"),
                    ]
                )
            return _Rows([])

        def close(self) -> None:
            return None

    calls = {"n": 0}

    def connect(provider: str) -> _Conn:
        calls["n"] += 1
        assert provider == "sei"
        return _Conn()

    monkeypatch.setattr(core, "connect", connect)
    filled = {
        "firm_id": "3106",
        "client_template_individual_id": "2981271",
        "client_template_organisation_id": "2981272",
        "individual_classification_id": "48527",
        "client_team_ids": [{"servicingRelationshipId": 59260, "teamId": 704079}],
        "account_team_ids": [{"servicingRelationshipId": 59254, "teamId": 704079}],
        "pension_classification_id": "48528",
        "sipp_subclassification_id": "48529",
    }
    _, skipped = fill_from_catalogue(filled)
    assert calls["n"] == 0
    assert skipped.account_for_model("PDdVYya8B8hb") is None
    _, teams = fill_from_catalogue({**filled, "model_dsrs": {"PDdVYya8B8hb": ["704718"]}})
    assert calls["n"] == 1
    assert teams.account_for_model("PDdVYya8B8hb") == [
        {"servicingRelationshipId": 59261, "teamId": 704718}
    ]
    assert teams.client_for_model("PDdVYya8B8hb") == [
        {"servicingRelationshipId": 59262, "teamId": 704718}
    ]


def test_a_custody_can_name_dsrs_as_well_as_a_subfirm() -> None:
    # The firm's teams stay on both sides. Offshore adds its own team; it does
    # not replace the firm's. Onshore named none, so it is the firm's alone.
    grants = [
        ("account", "59283", "704764", 503, "SEI Custody"),
        ("client", "59282", "704764", 395, "SEI Custody"),
        ("account", "59254", "704079", 12, "IFA Firm"),
        ("client", "59260", "999001", 4, "IFA Firm"),
        ("account", "59261", "704718", 40, "Client Service Team"),
        ("client", "59262", "704718", 8, "Client Service Team"),
    ]
    filled, teams = catalogue_defaults(
        {
            "firm_id": "3106",
            "dsr_ids": ["704079"],
            "custody_dsrs": {"offshore": ["704718"]},
        },
        client_templates=["2981271"],
        individual_codes=["48527"],
        grants=grants,
    )
    assert filled["client_team_ids"] == [{"servicingRelationshipId": 59260, "teamId": 704079}]
    assert filled["account_team_ids"] == [{"servicingRelationshipId": 59254, "teamId": 704079}]
    assert "custody_team_ids" not in filled
    assert teams.account_for("offshore") == [{"servicingRelationshipId": 59261, "teamId": 704718}]
    assert teams.account_for("onshore") is None
    client = _client()
    onshore = enrolment_attributes(client, _gia(client, custody="onshore"), filled, teams)
    offshore = enrolment_attributes(
        client, _gia(client, uid="acc456", custody="offshore"), filled, teams
    )
    assert onshore["accountList"][0]["teamServicingRelationshipList"] == filled["account_team_ids"]
    assert offshore["accountList"][0]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 59254, "teamId": 704079},
        {"servicingRelationshipId": 59261, "teamId": 704718},
    ]
    assert onshore["clientList"][0]["newClient"]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 59260, "teamId": 704079}
    ]
    assert offshore["clientList"][0]["newClient"]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 59260, "teamId": 704079},
        {"servicingRelationshipId": 59262, "teamId": 704718},
    ]


def test_both_configured_teams_go_on_the_client_and_the_account() -> None:
    # The office is Branch Administration and the custody team is SEI Custody.
    # Each scope has its own relationship id. Both are required, and nobody
    # names them: the team ids already chosen are enough. Employee
    # relationships are the people the book already grants. An adviser whose
    # SEI id has never held Adviser is not substituted in: SEI refuses that
    # pair. Someone who already holds it is.
    grants = [
        ("client", "59274", "200148", 47, "Branch Administration"),
        ("account", "59275", "200148", 90, "Branch Administration"),
        ("client", "59295", "704814", 10, "SEI Custody"),
        ("account", "59296", "704814", 12, "SEI Custody"),
    ]
    employees = [
        ("client", "59297", "58304024", 40, "Adviser"),
        ("account", "59298", "58304024", 90, "Adviser"),
        ("account", "59298", "58560716", 10, "Adviser"),
        ("account", "59299", "58304063", 84, "Investment Decision Maker"),
        ("account", "59299", "58304024", 6, "Investment Decision Maker"),
    ]
    filled, teams = catalogue_defaults(
        {
            "firm_id": "3108",
            "dsr_ids": ["200148"],
            "custody_dsrs": {"onshore": ["704814"]},
        },
        client_templates=["2981279"],
        individual_codes=["48555"],
        grants=grants,
        employee_grants=employees,
    )
    client = {**_client(), "sei_employee_id": "58494889"}
    body = enrolment_attributes(client, _gia(client, custody="onshore"), filled, teams)
    assert body["clientList"][0]["newClient"]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 59274, "teamId": 200148},
        {"servicingRelationshipId": 59295, "teamId": 704814},
    ]
    assert body["accountList"][0]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 59275, "teamId": 200148},
        {"servicingRelationshipId": 59296, "teamId": 704814},
    ]
    assert body["clientList"][0]["newClient"]["employeeServicingRelationshipList"] == [
        {"servicingRelationshipId": 59297, "employeeEntityId": 58304024}
    ]
    assert body["accountList"][0]["employeeServicingRelationshipList"] == [
        {"servicingRelationshipId": 59298, "employeeEntityId": 58304024},
        {"servicingRelationshipId": 59299, "employeeEntityId": 58304063},
    ]
    held = {**_client(), "sei_employee_id": "58304024"}
    chosen = enrolment_attributes(held, _gia(held, custody="onshore"), filled, teams)
    assert chosen["clientList"][0]["newClient"]["employeeServicingRelationshipList"] == [
        {"servicingRelationshipId": 59297, "employeeEntityId": 58304024}
    ]
    assert chosen["accountList"][0]["employeeServicingRelationshipList"] == [
        {"servicingRelationshipId": 59298, "employeeEntityId": 58304024},
        {"servicingRelationshipId": 59299, "employeeEntityId": 58304024},
    ]


def test_custody_dsrs_alone_stamp_the_client_and_that_custody() -> None:
    # No firm-wide list, so the modal custody grant must not win. The offshore
    # team is what the offshore account and its new client are opened with.
    grants = [
        ("account", "59283", "704764", 503, "SEI Custody"),
        ("client", "59282", "704764", 395, "SEI Custody"),
        ("account", "59261", "704718", 40, "Client Service Team"),
        ("client", "59262", "704718", 8, "Client Service Team"),
    ]
    filled, teams = catalogue_defaults(
        {"firm_id": "3106", "custody_dsrs": {"offshore": ["704718"]}},
        client_templates=["2981271"],
        individual_codes=["48527"],
        grants=grants,
    )
    assert "client_team_ids" not in filled
    assert "account_team_ids" not in filled
    assert "custody_team_ids" not in filled
    client = _client()
    offshore = enrolment_attributes(client, _gia(client, custody="offshore"), filled, teams)
    assert offshore["clientList"][0]["newClient"]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 59262, "teamId": 704718}
    ]
    assert offshore["accountList"][0]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 59261, "teamId": 704718}
    ]
    onshore = enrolment_attributes(client, _gia(client, custody="onshore"), filled, teams)
    assert "teamServicingRelationshipList" not in onshore["clientList"][0]["newClient"]
    assert "teamServicingRelationshipList" not in onshore["accountList"][0]


def test_a_thin_later_file_is_not_the_book() -> None:
    # 15 Oct is newer and much smaller. The adviser and custody team on it are
    # not the firm's. 14 Oct still has half the fullest recent count, so it is
    # the book. A firm whose latest day is also its fullest uses that day.
    assert representative_grant_day(
        [
            (date(2026, 10, 15), 37),
            (date(2026, 10, 14), 133),
            (date(2026, 10, 9), 181),
        ]
    ) == date(2026, 10, 14)
    assert representative_grant_day([(date(2026, 10, 15), 133)]) == date(2026, 10, 15)
    assert representative_grant_day([]) is None


def test_an_unknown_custody_team_does_not_fall_back_to_the_modal_grant() -> None:
    filled, teams = catalogue_defaults(
        {"firm_id": "3106", "custody_dsrs": {"onshore": ["111111"]}},
        client_templates=["2981271"],
        individual_codes=["48527"],
        grants=[("account", "59283", "704764", 503, "SEI Custody")],
    )
    assert "client_team_ids" not in filled
    assert "account_team_ids" not in filled
    assert "custody_team_ids" not in filled
    assert teams.account_for("onshore") == []
    assert teams.client_for("onshore") == []
    client = _client()
    body = enrolment_attributes(client, _gia(client, custody="onshore"), filled, teams)
    assert "teamServicingRelationshipList" not in body["clientList"][0]["newClient"]
    assert "teamServicingRelationshipList" not in body["accountList"][0]


def test_an_unknown_custody_team_does_not_fall_back_to_the_firm_list() -> None:
    # The custody named a team the book has never granted. That replaces the
    # firm-wide list on the account; it does not leave the account on the firm's team.
    filled, teams = catalogue_defaults(
        {
            "firm_id": "3106",
            "dsr_ids": ["704079"],
            "custody_dsrs": {"onshore": ["111111"]},
        },
        client_templates=["2981271"],
        individual_codes=["48527"],
        grants=[
            ("account", "59254", "704079", 12, "IFA Firm"),
            ("client", "59260", "704079", 4, "IFA Firm"),
        ],
    )
    assert teams.account_for("onshore") == []
    client = _client()
    body = enrolment_attributes(client, _gia(client, custody="onshore"), filled, teams)
    assert "teamServicingRelationshipList" not in body["accountList"][0]
    assert body["clientList"][0]["newClient"]["teamServicingRelationshipList"] == [
        {"servicingRelationshipId": 59260, "teamId": 704079}
    ]


def test_an_unknown_selected_team_does_not_fall_back_to_the_modal_grant() -> None:
    filled, _teams = catalogue_defaults(
        {"firm_id": "3106", "dsr_ids": ["111111"]},
        client_templates=["2981271"],
        individual_codes=["48527"],
        grants=[("account", "59283", "704764", 503, "SEI Custody")],
    )
    assert "client_team_ids" not in filled
    assert "account_team_ids" not in filled


def test_a_profile_update_is_one_slice() -> None:
    assert sei_date("1978-12-06") == "06-DEC-1978"
    details = profile_update(
        {
            "dob": "1978-12-06",
            "gender": "female",
            "occupation": "Teacher",
            "source_of_funds": "Salary or employment income",
        },
        part="details",
    )
    assert details["dateOfBirth"] == "06-DEC-1978"
    assert details["gender"] == 2
    assert details["occupation"] == "Teacher"
    assert details["initialSourceOfFunds"] == "2"
    assert profile_update({"source_of_funds": "Inheritance"}, part="circumstances") == {
        "initialSourceOfFunds": "3"
    }
    with pytest.raises(OperationError, match="source-of-wealth"):
        profile_update({"source_of_wealth": "Salary"}, part="circumstances")
    assert profile_update({"is_vulnerable": True}, part="vulnerability") == {
        "vulnerableClientFlag": True
    }
    with pytest.raises(OperationError, match="pay-to"):
        profile_update({}, part="bank")
