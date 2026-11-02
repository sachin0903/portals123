"""The seeded onboarding flow's ACTOR SPLIT — read off the legacy app, pinned here.

Why this file exists: a demo (and a real firm's first week) dies the moment the adviser hits a step
only a fictional client can complete. The legacy Next.js onboarding drew that line precisely, and it
is recoverable from source rather than opinion:

* the client's whole surface was ONE unauthenticated page,
  ``apps/platform/app/a/[applicationId]/[applicantId]/page.tsx``;
* its only *mutating* public endpoints were ``POST …/terms`` and ``POST/PATCH …/identity``
  (``app/api/a/**``) — the fields route was **GET** only, returning field *definitions*;
* every other step on that page rendered ``ViewField`` / ``DynamicFieldsDisplay`` — read-only review
  of what the adviser had already entered;
* the identity step was conditional on a *digital* provider
  (``shouldIncludeIdentityStep = digitalVerification && !isVerified``): with manual verification the
  adviser recorded the outcome and the client never saw the step.

So: the adviser fills every fact-find; the client's recorded acts are the terms acknowledgements
(and identity, only with a digital provider). These tests fail if the seed drifts back to assigning
the client work the legacy client could not do.
"""

from datetime import date
from typing import Any

import pytest

from app.config import settings
from app.flows import onboarding_seed, onboarding_terms, planning
from app.flows.definitions import validate_definition


def _condition_fields(node: Any) -> set[str]:
    if not isinstance(node, dict):
        return set()
    found = {str(node["field"])} if node.get("field") else set()
    for child in node.get("conditions") or []:
        found |= _condition_fields(child)
    return found


def _definition() -> dict[str, Any]:
    forms = {code: f"frm_{code}" for code in onboarding_seed.FORMS}
    terms = {code: f"doc_{code}" for code in onboarding_terms.TERMS}
    return validate_definition(onboarding_seed._build_definition(forms, terms))


def _steps() -> list[dict[str, Any]]:
    return list(_definition()["steps"])


def test_the_only_client_assigned_steps_are_terms_and_digital_identity() -> None:
    """The legacy client could accept declarations, and verify their identity when the firm had a
    digital provider wired. That is it.

    A ``fill_form`` assigned to the client is the specific regression this guards: the seed used to
    carry a client-assigned "Review your details" step, which nothing in the platform portal could
    complete (``submit_step_form`` 403s a non-staff step, by design) and which every later step
    depended on. The journey was unfinishable without a client login.

    ``verify_identity`` is allowed here only because it is CONDITIONAL — the pairing test below is
    what holds it to the digital case; this set would otherwise readmit the staff-only version.

    The one upload is the signed W-9 / W-8: a file only the client holds, asked for only when
    Additional information records one (``test_flows_fatca_upload.py``).
    """
    client_steps = [s for s in _steps() if s["assignee"] == "client"]
    client_types = {s["type"] for s in client_steps}
    assert client_types <= {"accept_terms", "verify_identity", "upload_document"}
    assert "accept_terms" in client_types
    assert [s["id"] for s in client_steps if s["type"] == "upload_document"] == ["fatca_form"]


def test_every_fact_find_is_the_advisers() -> None:
    forms = [s for s in _steps() if s["type"] == "fill_form"]
    assert forms, "the seed must still gather a fact-find"
    assert all(s["assignee"] == "staff" for s in forms)


def test_family_fact_find_is_scoped_to_each_applicant_and_account() -> None:
    """David 1 account, John 3, Lisa 3 — one SIPP means one pension fact-find."""
    graph = planning.ApplicationGraph(
        participants=(
            planning.Participant(1, "individual", frozenset({"owner"}), date(2010, 2, 18)),
            planning.Participant(2, "individual", frozenset({"owner"}), date(1979, 10, 22)),
            planning.Participant(
                3,
                "individual",
                frozenset({"owner", "registered_contact"}),
                date(1981, 8, 2),
            ),
        ),
        accounts=(
            planning.AccountSpec(11, (1, 3), product="JISA", registered_contact_ids=(3,)),
            planning.AccountSpec(12, (2, 3), product="GIA"),
            planning.AccountSpec(13, (2,), product="SIPP"),
            planning.AccountSpec(14, (2,), product="ISA"),
            planning.AccountSpec(15, (3,), product="ISA"),
        ),
        custody="onshore",
    )
    cases = planning.plan_execution(_steps(), graph)
    profile_forms = {
        case.subject_id: {step.step_key for step in case.steps if step.step_type == "fill_form"}
        for case in cases
        if case.kind == "profile"
    }
    assert profile_forms == {
        1: {"jisa_child"},
        2: {"core", "additional_info", "pension", "employer"},
        3: {"core", "additional_info"},
    }
    account_forms = [
        step
        for case in cases
        if case.kind == "account"
        for step in case.steps
        if step.step_type == "fill_form"
    ]
    assert len(account_forms) == 5
    assert {step.step_key for step in account_forms} == {"account_preferences"}


def test_the_fact_find_bank_form_matches_the_record_editor() -> None:
    """Applicant and organisation fact-finds ask the same bank questions, in the same order, as
    the client and account editors: UK / International, then the active rail in thirds, then
    BACS or Faster Payment on the UK rail."""
    expected = [
        "bank_style",
        "bank_account.account_name",
        "bank_account.sort_code",
        "bank_account.account_number",
        "bank_account.iban",
        "bank_account.bic",
        "bank_account.method",
    ]
    for code in ("applicant_core", "organisation_core"):
        rows = [row for row in onboarding_seed.FORMS[code][2] if str(row[0]).startswith("bank")]
        assert [row[0] for row in rows] == expected
        by_key = {row[0]: row for row in rows}
        style = by_key["bank_style"]
        assert style[1:] == (
            "segmented",
            "Account type",
            "Bank account",
            True,
            None,
            {"width": 12, "defaultValue": "uk", "shape": "square"},
            {
                "options": [
                    {"value": "uk", "label": "UK"},
                    {"value": "international", "label": "International"},
                ]
            },
            onboarding_seed._BANK_CROWN,
        )
        assert by_key["bank_account.account_name"][2] == "Account name"
        assert by_key["bank_account.account_name"][6]["width"] == 4
        assert by_key["bank_account.sort_code"][6]["width"] == 4
        assert by_key["bank_account.account_number"][6]["width"] == 4
        assert by_key["bank_account.bic"][2] == "SWIFT/BIC"
        method = by_key["bank_account.method"]
        assert method[1] == "segmented"
        assert method[2] == "Payment method"
        assert method[5] == onboarding_seed._BANK_UK
        assert method[6]["defaultValue"] == "bacs"
        assert method[6]["shape"] == "square"
        assert method[8] == onboarding_seed._BANK_METHOD_HELP
    for mapping in (onboarding_seed.CORE_MAPPING, onboarding_seed.ORG_MAPPING):
        assert any(row["source"] == "bank_account.method" for row in mapping)


def test_the_employer_step_gathers_what_the_employer_card_shows() -> None:
    """Company name, registration number AND address, each revealed by the "Receives employer
    contributions" tick — and mapped to the client record's own columns, not into `data`.

    The record's Employer card (0139) reads `employer_contributions`, `employer_name`,
    `employer_registration_number` and `employer_address`. The step used to answer all of them
    and map every one to `data.pension.employerContributions.*`, so an adviser who filled the
    form in saw three em dashes on the card and "HasEmployerContributions: Yes" filed under
    "more from the fact-find". The address is keyed FLAT (`employer_address`) because a composite
    field stores `{key: {line1, …}}` and the mapper resolves `key.line1` by nesting into the
    answers — a dotted key would leave no `pension` object to walk into.
    """
    rows = onboarding_seed.FORMS["applicant_employer"][2]
    by_key = {row[0]: row for row in rows}
    gate = onboarding_seed._cond("pension.hasEmployerContributions", "equals", True)
    assert by_key["pension.hasEmployerContributions"][1] == "checkbox"
    for key, ftype in (
        ("pension.employerContributions.name", "text"),
        ("pension.employerContributions.registrationNumber", "text"),
        ("employer_address", "address"),
    ):
        assert by_key[key][1] == ftype, key
        assert by_key[key][4] is True, f"{key} is required once the tick is on"
        assert by_key[key][5] == gate, key
    # The address is asked with the company, before the scheme's bank details.
    keys = [row[0] for row in rows]
    assert keys.index("employer_address") < keys.index(
        "pension.employerContributions.accountNumber"
    )

    targets = {row["source"]: row["target"] for row in onboarding_seed.EMPLOYER_MAPPING}
    assert targets["pension.hasEmployerContributions"] == "employer_contributions"
    assert targets["pension.employerContributions.name"] == "employer_name"
    assert targets["pension.employerContributions.registrationNumber"] == (
        "employer_registration_number"
    )
    for leaf in ("line1", "line2", "city", "region", "postcode", "country"):
        assert targets[f"employer_address.{leaf}"] == f"employer_address.{leaf}"
    # The scheme's bank details have a column of their own (0271): the same jsonb shape as the
    # client's `bank_account`, merged leaf by leaf, never nested under `data` any more.
    assert targets["pension.employerContributions.accountNumber"] == (
        "employer_bank_account.account_number"
    )
    assert targets["pension.employerContributions.sortCode"] == "employer_bank_account.sort_code"


def test_a_uk_nino_is_a_second_question_only_when_the_identifier_is_not_one() -> None:
    field = next(row for row in onboarding_seed._CORE_FIELDS if row[0] == "uk_national_insurance")
    assert field[2] == "UK National Insurance number"
    assert field[4] is True
    assert field[5]["logic"] == "AND"
    encoded = str(field[5])
    assert "isNotEmpty" not in encoded
    assert "United Kingdom" in encoded
    assert "uk_national_insurance" not in onboarding_seed._JISA_CHILD_FIELD_KEYS
    targets = {row["source"]: row["target"] for row in onboarding_seed.CORE_MAPPING}
    assert targets["uk_national_insurance"] == "uk_national_insurance"


def test_legal_gender_is_the_shared_field_type() -> None:
    gender = next(row for row in onboarding_seed._CORE_FIELDS if row[0] == "gender")
    assert gender[1] == "gender"
    assert gender[7] is None


def test_account_details_are_gathered_inside_the_journey_before_the_client_handoff() -> None:
    """Transfer must be known before the existing transfer-declaration branch is evaluated."""
    raw = onboarding_seed._build_definition(
        {code: f"frm_{code}" for code in onboarding_seed.FORMS},
        {code: f"doc_{code}" for code in onboarding_terms.TERMS},
    )
    nodes = {node["id"]: node for node in raw["nodes"]}
    choices = nodes["account_preferences"]
    assert choices["scope"] == "account"
    assert choices["assignee"] == "staff"
    assert choices["title"] == "Account details"
    assert choices["config"]["mode"] == "account_preferences"
    assert choices["next"] == "declaration"
    assert set(nodes["start"]["next"]) == {
        "core",
        "jisa_child",
        "jisa_registered_contact",
        "org",
    }
    assert nodes["core"]["next"] == [
        "additional_info",
        "pension",
        "employer",
        "account_preferences",
    ]

    fields = {row[0] for row in onboarding_seed.FORMS["account_preferences"][2]}
    assert {
        "dividend_treatment",
        "account_name",
        "currency",
        "investment_capacity",
        "model_id",
        "fee_package_ids",
        "has_transfer",
        "transfer_type",
        "transfer_extent",
        "has_contribution",
        "contribution_amount",
    } <= fields


def test_funding_options_each_own_a_row() -> None:
    """Each option sits in the left column; the fields it reveals sit in the right.

    A span alone cannot do that: the next checkbox would land beside a field that belongs
    to the option above it.
    """
    fields = onboarding_seed.FORMS["account_preferences"][2]
    funding = [row for row in fields if row[3] == "Funding"]
    assert [row[0] for row in funding if row[1] == "checkbox" and row[6]["column"] == "left"] == [
        "has_initial_cash",
        "has_transfer",
        "has_contribution",
        "has_regular_withdrawal",
    ]
    for row in funding:
        if row[1] == "checkbox" and row[0] != "safeguarded_benefits":
            assert row[6]["column"] == "left"
        elif row[6]:
            assert row[6]["column"] == "right"

    by_key = {row[0]: row for row in funding}
    assert [row[0] for row in funding if row[5] and row[5].get("field") == "has_transfer"] == [
        "transfer_type",
        "transfer_extent",
        "transfer_provider",
        "transfer_amount",
        "transfer_reference",
    ]
    extent = by_key["transfer_extent"]
    assert extent[2] == "Full or partial transfer?"
    assert [opt["value"] for opt in extent[7]["options"]] == ["full", "partial"]
    keys = [row[0] for row in funding]
    assert keys[keys.index("transfer_reference") + 1 : keys.index("has_contribution")] == [
        "pension_type",
        "is_crystallised",
        "crystallised_amount",
        "safeguarded_benefits",
        "fca_reference",
    ]
    safeguarded = by_key["safeguarded_benefits"]
    assert safeguarded[1] == "checkbox"
    assert safeguarded[2] == onboarding_seed.SAFEGUARDED_BENEFITS_QUESTION
    assert "Pension Schemes Act 2015" in safeguarded[2]
    assert safeguarded[4] is False
    assert safeguarded[6] == {"width": 12, "column": "right"}
    assert _condition_fields(safeguarded[5]) == {
        "has_transfer",
        "account.product_type",
        "account.is_pension",
    }
    fca = by_key["fca_reference"]
    assert fca[1] == "text"
    assert fca[2] == "FCA reference"
    assert fca[4] is True
    assert fca[8] == onboarding_seed.SAFEGUARDED_FCA_INFO
    assert "Safeguarded Rights" in fca[8]
    assert _condition_fields(fca[5]) == {
        "has_transfer",
        "account.product_type",
        "account.is_pension",
        "safeguarded_benefits",
    }
    # A regular withdrawal is not offered for an Accumulation SIPP — gated on non-pension OR a
    # drawdown crystallisation status.
    assert _condition_fields(by_key["has_regular_withdrawal"][5]) == {
        "account.product_type",
        "crystallisation_status",
    }
    assert by_key["has_contribution"][2] == "Expect a payment in"
    assert by_key["has_contribution"][6]["contributionLabel"] == "Expect a contribution"
    assert by_key["contribution_amount"][2] == "Payment in amount"
    assert by_key["contribution_amount"][6]["contributionLabel"] == "Contribution amount"


def test_account_currency_dividends_and_vulnerability_are_asked_as_specified() -> None:
    prefs = {row[0]: row for row in onboarding_seed.FORMS["account_preferences"][2]}
    currency = prefs["currency"]
    assert currency[2] == "Default Account Settlement Currency"
    assert currency[5]["logic"] == "AND"
    dividends = [opt["value"] for opt in prefs["dividend_treatment"][7]["options"]]
    assert dividends == ["reinvest", "cash", "pay_away"]
    # Preferred settlement currency (SEI Security/Account base) is hidden for now while SEI enable
    # the API for it, so the field is not offered.
    assert "dividend_settlement_currency" not in prefs

    core = {row[0]: row for row in onboarding_seed._CORE_FIELDS}
    assert core["is_vulnerable"][1] == "checkbox"
    assert "vulnerability_reasons" in core
    assert "e.g. 65" not in str(onboarding_seed._PENSION_FIELDS)
    bank = {row[0]: row for row in onboarding_seed._BANK_ACCOUNT_FIELDS}
    assert "placeholder" not in (bank["bank_account.iban"][6] or {})
    assert "00-00-00" not in str(bank["bank_account.sort_code"][6])


def test_additional_information_applies_to_ordinary_individual_applicants() -> None:
    raw = onboarding_seed._build_definition(
        {code: f"frm_{code}" for code in onboarding_seed.FORMS},
        {code: f"doc_{code}" for code in onboarding_terms.TERMS},
    )
    nodes = {node["id"]: node for node in raw["nodes"]}
    assert nodes["additional_info"]["visibility"] == {
        "logic": "AND",
        "conditions": [
            {"field": "applicant.type", "operator": "equals", "value": "individual"},
            {
                "field": "applicant.onboarding_role",
                "operator": "equals",
                "value": "applicant",
            },
        ],
    }
    fields = {row[0] for row in onboarding_seed.FORMS["applicant_offshore"][2]}
    assert {
        "country_of_birth",
        "town_of_birth",
        "occupation",
        "source_of_funds",
        "source_of_wealth",
        "us_person",
        "pep",
    } <= fields
    offshore = onboarding_seed.FORMS["applicant_offshore"][2]
    sow = next(row for row in offshore if row[0] == "source_of_wealth")
    assert sow[1] == "select"
    assert onboarding_seed.SOURCE_OF_WEALTH[-1] == "Other"
    other = next(row for row in offshore if row[0] == "source_of_wealth_other")
    assert other[1] == "textarea"
    assert other[2] == "Source of wealth statement"
    assert other[5] is None
    assert other[6]["minLength"] == 30
    us_person = next(row for row in offshore if row[0] == "us_person")
    assert us_person[1] == "select"
    assert us_person[2] == "Is a US person?"
    assert us_person[4] is True
    assert [item["value"] for item in us_person[7]["options"]] == ["Yes", "No"]
    # The W-9 or W-8 is its own question, straight after, and optional: only a client
    # investing in US assets has one.
    keys = [row[0] for row in offshore]
    assert keys[keys.index("us_person") + 1] == "fatca_status"
    held = offshore[keys.index("fatca_status")]
    assert held[1] == "select"
    assert held[4] is False
    assert [(item["value"], item["label"]) for item in held[7]["options"]] == [
        ("w9", "W-9"),
        ("w8", "W-8"),
    ]
    mapping = {row["source"]: row["target"] for row in onboarding_seed.ADDITIONAL_MAPPING}
    assert mapping["us_person"] == "us_person"
    assert mapping["fatca_status"] == "fatca_status"


def test_jisa_uses_child_and_registered_contact_forms_instead_of_applicant_forms() -> None:
    raw = onboarding_seed._build_definition(
        {code: f"frm_{code}" for code in onboarding_seed.FORMS},
        {code: f"doc_{code}" for code in onboarding_terms.TERMS},
    )
    nodes = {node["id"]: node for node in raw["nodes"]}
    assert nodes["jisa_child"]["title"] == "Child details"
    assert nodes["jisa_registered_contact"]["title"] == "Registered contact details"
    assert nodes["jisa_child"]["next"] == "account_preferences"
    assert nodes["jisa_registered_contact"]["next"] == "account_preferences"

    child_fields = {row[0] for row in onboarding_seed.FORMS["jisa_child"][2]}
    assert {"first_name", "surname", "dob", "nationality", "tax_residency"} <= child_fields
    assert not child_fields & {
        "national_identifier",
        "national_identifier_type",
        "email",
        "phone",
        "other_numbers",
        "address",
        "bank_style",
        "bank_account.account_number",
    }

    contact_fields = {row[0] for row in onboarding_seed.FORMS["jisa_registered_contact"][2]}
    assert {"first_name", "surname", "email", "phone", "address"} <= contact_fields
    assert not contact_fields & {
        "dob",
        "national_identifier",
        "national_identifier_type",
        "bank_style",
        "bank_account.account_number",
    }

    core_fields = {row[0]: row for row in onboarding_seed.FORMS["applicant_core"][2]}
    assert core_fields["nationality"][1] == "multiSelect"
    assert core_fields["tax_residency"][1] == "multiSelect"

    graph = planning.ApplicationGraph(
        participants=(
            planning.Participant(1, "individual", frozenset({"owner"})),
            planning.Participant(2, "individual", frozenset({"registered_contact"})),
        ),
        accounts=(planning.AccountSpec(9, (1, 2), product="JISA"),),
    )
    profiles = {
        case.subject_id: {step.step_key for step in case.steps}
        for case in planning.plan_execution(_definition()["steps"], graph)
        if case.kind == "profile"
    }
    assert profiles[1] == {"jisa_child"}
    assert profiles[2] == {"jisa_registered_contact", "idv", "aml"}


def test_seeded_source_of_wealth_is_the_vendor_list() -> None:
    """The picker stores their labels so `_SOURCE_OF_WEALTH` hits the INT. Inheritence is
    theirs, not a typo of ours."""
    from app.integrations.morgan_lloyd import contacts as ml_contacts

    assert onboarding_seed.SOURCE_OF_WEALTH == [
        "Salary",
        "Variable Remuneration",
        "Investment Income or Sale of Investments",
        "Pension Income",
        "Sale of Main Residence",
        "Sale of Other Property",
        "Business Interests",
        "Inheritence",
        "Gift",
        "Financial Settlements",
        "Lottery/Betting/Casino",
        "Other",
    ]
    for label in onboarding_seed.SOURCE_OF_WEALTH:
        assert label.lower() in ml_contacts._SOURCE_OF_WEALTH


@pytest.mark.pkgs_ui_pin
def test_person_titles_match_the_shared_field_type() -> None:
    """The fact-find uses the `title` field type, whose list is the titles every custodian stores.

    The API container ships without pkgs/ui, so the seed keeps its own copy and this
    test reads the TypeScript. Morgan Lloyd also accepts Master, Capt, Reverend, Sir,
    Lord and Lady; those are not offered, because Seccl's enum does not include them.
    """
    import re
    from pathlib import Path

    vocab = (
        Path(__file__).resolve().parents[3] / "pkgs/ui/src/lib/forms/vocabularies.ts"
    ).read_text(encoding="utf-8")
    block = vocab.split("export const TITLE_OPTIONS = [", 1)[1].split("] as const", 1)[0]
    assert re.findall(r"value: '([^']*)'", block) == onboarding_seed.PERSON_TITLES
    core = onboarding_seed.FORMS["applicant_core"][2]
    title = next(field for field in core if field[0] == "title")
    assert title[1] == "title"
    assert title[7] is None
    pension = onboarding_seed.FORMS["applicant_pension"][2]
    marital = next(field for field in pension if field[0] == "pension.maritalStatus")
    employment = next(field for field in pension if field[0] == "pension.employmentStatus")
    assert marital[1] == "marital" and marital[7] is None
    assert employment[1] == "employment" and employment[7] is None
    from morgan_lloyd.enums import Title

    named = {member.name.title() for member in Title if member.name not in {"OTHER", "MAJOR"}}
    assert set(onboarding_seed.PERSON_TITLES) < named
    assert "Prof" in onboarding_seed.PERSON_TITLES
    assert "Master" not in onboarding_seed.PERSON_TITLES


def test_pep_is_an_optional_checkbox_defaulting_to_no() -> None:
    """A required checkbox means "this must be TRUE", not "answer yes or no".

    Most applicants are not PEPs, so an untouched control must record false and must not block the
    form. The self-declaration still maps into the contact's overflow data; it is not an AML
    screening outcome.
    """
    pep = next(row for row in onboarding_seed.FORMS["applicant_offshore"][2] if row[0] == "pep")
    assert pep[1] == "checkbox"
    assert pep[4] is False
    assert pep[6] == {"defaultValue": False}


def test_seeded_forms_author_a_responsive_width_for_every_field() -> None:
    for _code, (_name, _entity, fields) in onboarding_seed.FORMS.items():
        for field in fields:
            validation = dict(field[6] or {})
            expected = (
                12 if field[1] in {"textarea", "address", "beneficiaries", "multiSelect"} else 6
            )
            # Explicit account-detail widths may be narrower, but no seeded field is left to a
            # single-column fallback.
            assert validation.get("width", expected) in {4, 6, 12}


def test_every_seeded_option_label_starts_with_a_capital() -> None:
    """The organisation fact-find printed "charity", "trust" beside "Charitable Remainder Trust".

    A list whose stored value is a lowercase key keeps the key and capitalises only its label
    (`_keyed_opts`), because conditions and the mapping read the value.
    """
    for code, (_name, _entity, fields) in onboarding_seed.FORMS.items():
        for field in fields:
            for option in (field[7] or {}).get("options", []):
                label = option["label"]
                assert label[:1] == label[:1].upper(), f"{code}.{field[0]}: {label!r}"
    org_fields = onboarding_seed.FORMS["organisation_core"][2]
    org = {row[0]: (row[7] or {}).get("options", []) for row in org_fields}
    assert {"value": "charity", "label": "Charity"} in org["entityType"]
    assert {"value": "collective", "label": "Collective"} in org["classification"]


def test_who_verifies_identity_follows_how_it_is_verified() -> None:
    """The PAIRING, asserted rather than the value — the value depends on the deployment.

    ``provider: manual`` means a person records the outcome, and in the legacy app that person was
    the adviser; the client only ever saw an identity step when a digital provider was wired. So
    manual ⇒ staff and complycube ⇒ client, and a seed that produced either half without the other
    would be broken in one of two ways: a client with nothing to do, or a request for photos
    parked where the person holding the document cannot reach it.
    """
    idv = [s for s in _steps() if s["type"] == "verify_identity"]
    assert idv, "the seed must still verify identity"
    for step in idv:
        provider = step["config"].get("provider")
        assert provider in {"manual", "complycube"}
        assert step["assignee"] == ("client" if provider == "complycube" else "staff")


def test_identity_is_manual_staff_work_without_a_complycube_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unconfigured deployment: there is no digital check to offer, so a person does it."""
    monkeypatch.setattr(settings, "complycube_api_key", "")
    idv = [s for s in _steps() if s["type"] == "verify_identity"]
    assert [(s["config"]["provider"], s["assignee"]) for s in idv] == [("manual", "staff")] * len(
        idv
    )


def test_identity_is_the_clients_own_step_with_a_complycube_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "complycube_api_key", "test-key")
    idv = [s for s in _steps() if s["type"] == "verify_identity"]
    assert idv
    for step in idv:
        assert step["config"]["provider"] == "complycube"
        assert step["assignee"] == "client"


@pytest.mark.parametrize("key", ["test-key", ""])
def test_aml_names_no_provider_with_or_without_a_key(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """An AML step always screens on ComplyCube, so the seed names no provider, key or not. A
    screen is Graphene's work, so the step is always the adviser's to watch, never the client's."""
    monkeypatch.setattr(settings, "complycube_api_key", key)
    aml = [s for s in _steps() if s["type"] == "aml_check"]
    assert aml, "the seed must still screen"
    assert {("provider" in s["config"], s["assignee"]) for s in aml} == {(False, "staff")}


def test_no_step_depends_on_a_step_that_no_longer_exists() -> None:
    """Removing a step without rewiring what depended on it strands the dependants forever: the
    engine holds an instance whose prerequisite key will never be reported complete."""
    ids = {s["id"] for s in _steps()}
    for step in _steps():
        for dep in step.get("depends_on") or []:
            assert dep in ids, f"{step['id']} depends on missing step {dep}"


def test_declaration_invites_clients_on_complete() -> None:
    """Legacy declare → email: completing the adviser declaration invites KYC-relevant owners."""
    handoff = next(s for s in _steps() if s["id"] == "declaration")
    assert handoff["type"] == "task"
    assert handoff["scope"] == "application"
    assert handoff["config"].get("invite_clients") is True


def test_terms_acknowledgements_are_per_participant() -> None:
    """An account-scoped ``accept_terms`` with no fan-out has nobody to ask — the handler blocks it
    with ``no_actor`` and the account tier can never clear. Publish preflight refuses the shape;
    this asserts the seed never produces it."""
    for step in _steps():
        if step["type"] == "accept_terms" and step.get("scope") == "account":
            assert step.get("per_participant") is True


def test_organisation_core_uses_composite_address_field() -> None:
    """``organisation_core`` must use one composite ``address`` field (same shape as
    ``applicant_core``), not fragmented ``address.line1`` text inputs — the mapping reads nested
    ``address.*`` off the composite value."""
    org_fields = onboarding_seed.FORMS["organisation_core"][2]
    by_key = {row[0]: row[1] for row in org_fields}
    assert by_key.get("address") == "address"
    assert not any(str(key).startswith("address.") for key in by_key)


def _account_preference_fields() -> dict[str, tuple]:
    return {row[0]: row for row in onboarding_seed.FORMS["account_preferences"][2]}


def test_the_third_party_provider_section_is_asked_of_the_three_products_that_have_one() -> None:
    """A third-party provider belongs to the third-party bond and pension wrappers, and to nothing
    else. The whole section was unconditional, so every GIA and ISA was asked whether it uses one —
    the fields UNDER the checkbox were already conditional, which is what made the section itself
    look intended rather than overlooked.
    """
    fields = _account_preference_fields()
    checkbox = fields["has_third_party_provider"]
    assert checkbox[5] == {
        "field": "account.product_type",
        "operator": "in",
        "value": ["TPONBOND", "TPOFFBOND", "TPPENSION"],
    }
    # Every field under it carries BOTH the product test and the checkbox: the section can be
    # absent, and inside it the details wait on the answer.
    for key in (
        "third_party_provider",
        "third_party_account_name",
        "third_party_account_number",
        "third_party_sort_code",
    ):
        conditions = fields[key][5]
        assert conditions["logic"] == "AND"
        assert conditions["conditions"] == [
            {
                "field": "account.product_type",
                "operator": "in",
                "value": ["TPONBOND", "TPOFFBOND", "TPPENSION"],
            },
            {"field": "has_third_party_provider", "operator": "equals", "value": True},
        ]


def test_an_and_group_is_written_bare_so_the_seeder_wraps_it_once() -> None:
    """``_seed_form`` wraps a field's condition in ``{"conditional": …}``. Wrapping in the helper
    too would nest it twice, and the runtime would read no condition at all."""
    group = onboarding_seed._cond_all(
        onboarding_seed._cond("a", "equals", 1), onboarding_seed._cond("b", "equals", 2)
    )
    assert "conditional" not in group
    assert group["logic"] == "AND"
    assert len(group["conditions"]) == 2


def test_the_third_party_fields_say_whose_details_they_are() -> None:
    """Everything under that heading is the PROVIDER's. "Account number" and "Sort code" read as
    the client's own bank details, which is the account money would be sent to."""
    fields = _account_preference_fields()
    assert fields["third_party_provider"][2] == "Provider name"
    assert fields["third_party_account_name"][2] == "Provider's account holder name"
    assert fields["third_party_account_number"][2] == "Provider's account number"
    assert fields["third_party_sort_code"][2] == "Provider's sort code"
    assert fields["has_third_party_provider"][2] == (
        "This account is held through a third-party provider"
    )


def test_every_field_condition_uses_an_operator_the_runtime_KNOWS() -> None:
    """An unknown operator falls through to "visible", so a wrong spelling shows the field always.

    `_ACCOUNT_IS_NOT_PENSION` was authored `not_equals`; the field runtime's switch
    (`pkgs/ui/src/lib/forms/editor/visibility.ts`) knows `notEquals` and `!==` and returns `true`
    on anything else — so every not-a-pension question was asked on a SIPP as well, silently,
    because the server exempts conditional fields from its required check.
    """
    known = {
        "equals",
        "notEquals",
        "in",
        "notIn",
        "contains",
        "notContains",
        "isEmpty",
        "isNotEmpty",
        # The aliases the runtime accepts alongside the named forms.
        "===",
        "!==",
    }
    seen: list[str] = []

    def walk(conditions: Any) -> None:
        if not isinstance(conditions, dict):
            return
        if "conditions" in conditions:
            for inner in conditions["conditions"]:
                walk(inner)
            return
        operator = conditions.get("operator")
        if isinstance(operator, str):
            seen.append(operator)

    for _code, (_name, _entity, fields) in onboarding_seed.FORMS.items():
        for field in fields:
            walk(field[5])
    assert seen, "no field conditions found — the walk is not reaching them"
    assert set(seen) <= known, f"unknown field operator(s): {sorted(set(seen) - known)}"


def test_gia_and_sipp_do_not_plan_isa_or_transfer_terms() -> None:
    from app.flows.planning import AccountSpec, ApplicationGraph, Participant, plan_execution

    steps = list(_definition()["steps"])
    owner = Participant(1, "individual", frozenset({"owner"}))
    for product in ("GIA", "SIPP"):
        graph = ApplicationGraph(
            participants=(owner,),
            accounts=(AccountSpec(9, (1,), product=product, has_transfer=False),),
        )
        acc = next(c for c in plan_execution(steps, graph) if c.kind == "account")
        keys = {s.step_key for s in acc.steps}
        assert "terms_isa" not in keys, keys
        assert "terms_transfer" not in keys, keys
        assert "terms_general" in keys


def test_isa_with_transfer_plans_both_extra_terms() -> None:
    from app.flows.planning import AccountSpec, ApplicationGraph, Participant, plan_execution

    steps = list(_definition()["steps"])
    graph = ApplicationGraph(
        participants=(Participant(1, "individual", frozenset({"owner"})),),
        accounts=(AccountSpec(9, (1,), product="ISA", has_transfer=True),),
    )
    keys = {
        s.step_key for c in plan_execution(steps, graph) if c.kind == "account" for s in c.steps
    }
    assert "terms_isa" in keys
    assert "terms_transfer" in keys
