"""Unit tests for the pure payload builders in scripts/populate_test_data.py.

No HTTP and no MorganLloydClient construction — builders only. Required-field
lists are transcribed from the API field specs (Create Contact / Submit ATR,
Create Scheme / Create Beneficiary, Create Transfer / Create Pension
Contribution).
"""

from __future__ import annotations

import random
import re
import sys
from pathlib import Path
from typing import Any

from morgan_lloyd.enums import (
    AssetStatus,
    AssetType,
    ContactEmploymentStatus,
    ContactStatus,
    ContactVulnerability,
    ContributionStatus,
    ContributionType,
    FamilyStatus,
    Gender,
    PensionContributionFrequency,
    PensionType,
    RelevantBusinessUnit,
    SchemeType,
    SippType,
    SourceOfWealth,
    Title,
    TransferType,
    TypeOfTransfer,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import populate_test_data as ptd  # noqa: E402

# Required fields transcribed from the create-endpoint specs.
REQUIRED_CONTACT = (
    "as_contactstatus",
    "cam_title",
    "firstname",
    "lastname",
    "emailaddress1",
    "cam_nationality",
    "gendercode",
    "cam_ninumber",
    "address1_line1",
    "address1_city",
    "address1_postalcode",
    "cam_birthdate",
)
REQUIRED_SCHEME = ("contactId", "schemeType", "sippType")
REQUIRED_TRANSFER = (
    "schemeReference",
    "cam_policyno",
    "cam_providername",
    "cam_assettype",
    "cam_status",
    "cam_uncrystalllisedtransfervalue",  # verbatim triple-l spelling
    "cam_crystallisedtransfervalue",
    "cam_nonprtv",
    "cam_typeoftransfer",
    "cam_transfertype",
    "cam_pensiontype",  # required for pension products, which these all are
)
REQUIRED_BENEFICIARY = (
    "schemeReference",
    "cam_nameofbeneficiary",
    "cam_percentageoffund",
    "cam_nominationdate",
    "cam_relationship",
    "cam_address_line1",
    "cam_city",
    "cam_postcode",
)
REQUIRED_CONTRIBUTION = (
    "schemeReference",
    "cam_frequency",
    "cam_amount",
    "cam_startdate",
    "cam_status",
)
REQUIRED_ATR = ("clientId", "riskProfile", "dateGenerated", "atrData")

# The spec's ** fields — required iff cam_potentiallyvulnerable == "True".
VULNERABILITY_FIELDS = (
    "cam_contactvulnerability",
    "cam_vulnerabilityaddedby",
    "cam_additionalvulnerabilitynotes",
    "cam_relevantbusinessunit",
)

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def sample_plans(seed: int = 20260714, clients: int = 30) -> list[dict[str, dict[str, Any]]]:
    return ptd.build_plans(seed, clients)


def assert_member(value: Any, enum_cls: Any) -> None:
    assert value in {int(member) for member in enum_cls}, f"{value!r} not in {enum_cls.__name__}"


def test_contact_required_fields_present() -> None:
    for plan in sample_plans():
        for field in REQUIRED_CONTACT:
            assert field in plan["contact"], f"missing {field}"


def test_scheme_required_fields_present() -> None:
    for plan in sample_plans():
        for field in REQUIRED_SCHEME:
            assert field in plan["scheme"], f"missing {field}"


def test_transfer_required_fields_present() -> None:
    for plan in sample_plans():
        for field in REQUIRED_TRANSFER:
            assert field in plan["transfer"], f"missing {field}"


def test_beneficiary_required_fields_present() -> None:
    for plan in sample_plans():
        for field in REQUIRED_BENEFICIARY:
            assert field in plan["beneficiary"], f"missing {field}"


def test_contribution_required_fields_present() -> None:
    for plan in sample_plans():
        for field in REQUIRED_CONTRIBUTION:
            assert field in plan["contribution"], f"missing {field}"


def test_atr_required_fields_present() -> None:
    for plan in sample_plans():
        atr = plan["atr"]
        for field in REQUIRED_ATR:
            assert field in atr, f"missing {field}"
        assert 1 <= atr["riskProfile"] <= 10
        assert DATE_RE.fullmatch(atr["dateGenerated"])
        for item in atr["atrData"]:
            assert set(item) == {"question_id", "question", "response"}
            assert isinstance(item["question_id"], int)


def test_contact_enum_fields_are_valid_members() -> None:
    for plan in sample_plans():
        contact = plan["contact"]
        assert_member(contact["as_contactstatus"], ContactStatus)
        assert_member(contact["cam_title"], Title)
        assert_member(contact["gendercode"], Gender)
        assert_member(contact["familystatuscode"], FamilyStatus)
        assert_member(contact["cam_employmentstatus"], ContactEmploymentStatus)
        assert_member(contact["cam_sourceofwealth"], SourceOfWealth)
        assert contact["cam_potentiallyvulnerable"] in ("True", "False")


def test_scheme_enum_fields_are_valid_members() -> None:
    for plan in sample_plans():
        scheme = plan["scheme"]
        assert_member(scheme["schemeType"], SchemeType)
        assert_member(scheme["sippType"], SippType)
        # the ML-Team field-secured fee fields must NOT be sent (403s the create)
        for field in (
            "ongoingAdviserFeePercentage",
            "platformFeePercentage",
            "investmentCostsPercentage",
        ):
            assert field not in scheme


def test_transfer_enum_fields_are_valid_members() -> None:
    for plan in sample_plans():
        transfer = plan["transfer"]
        assert transfer["cam_assettype"] == int(AssetType.PENSION_PLANS)
        assert transfer["cam_status"] == int(AssetStatus.TRANSFERRING)
        assert_member(transfer["cam_typeoftransfer"], TypeOfTransfer)
        assert_member(transfer["cam_transfertype"], TransferType)
        assert_member(transfer["cam_pensiontype"], PensionType)
        total = (
            transfer["cam_uncrystalllisedtransfervalue"]
            + (transfer["cam_crystallisedtransfervalue"])
        )
        assert transfer["cam_nonprtv"] == round(total, 2)


def test_contribution_enum_fields_and_conditional_contributor() -> None:
    saw_personal = saw_other = False
    for plan in sample_plans(clients=60):
        contribution = plan["contribution"]
        assert_member(contribution["cam_contributiontype"], ContributionType)
        assert_member(contribution["cam_frequency"], PensionContributionFrequency)
        assert_member(contribution["cam_status"], ContributionStatus)
        assert DATE_RE.fullmatch(contribution["cam_startdate"])
        if contribution["cam_contributiontype"] == int(ContributionType.PERSONAL):
            saw_personal = True
            assert "cam_contributor" in contribution  # the ^ rule
            assert "cam_employerothername" not in contribution
        else:
            saw_other = True
            assert "cam_employerothername" in contribution  # the * rule
            assert "cam_contributor" not in contribution
    assert saw_personal and saw_other


def test_determinism_same_seed_identical_payloads() -> None:
    assert ptd.build_plans(20260714, 10) == ptd.build_plans(20260714, 10)
    assert ptd.build_plans(1, 5) != ptd.build_plans(2, 5)


def test_individual_builders_deterministic() -> None:
    for builder, args in (
        (ptd.build_contact, ()),
        (ptd.build_scheme, ("$CONTACT_ID",)),
        (ptd.build_transfer, ("REF/001",)),
        (ptd.build_beneficiary, ("REF/001",)),
        (ptd.build_contribution, ("REF/001", "$CONTACT_ID")),
        (ptd.build_atr, ()),
    ):
        first = builder(random.Random(7), 3, *args)  # type: ignore[operator]  # noqa: S311
        second = builder(random.Random(7), 3, *args)  # type: ignore[operator]  # noqa: S311
        assert first == second


def test_contact_identifier_formats() -> None:
    for index, plan in enumerate(sample_plans()):
        contact = plan["contact"]
        assert re.fullmatch(r"TN\d{6}A", contact["cam_ninumber"])
        assert contact["emailaddress1"] == f"ml-test-{index:03d}@example.grapheneplatforms.com"
        assert re.fullmatch(r"07700 900\d{3}", contact["mobilephone"])
        assert contact["cam_externalclientid"] == f"GRAPHENE-TEST-{index:04d}"
        assert DATE_RE.fullmatch(contact["cam_birthdate"])
        assert 1950 <= int(contact["cam_birthdate"][:4]) <= 1995


def test_no_vulnerable_contacts_without_a_system_user_guid() -> None:
    # cam_vulnerabilityaddedby is validated server-side; without a real GUID
    # the builder must never mark a contact vulnerable
    for plan in sample_plans(clients=80):
        contact = plan["contact"]
        assert contact["cam_potentiallyvulnerable"] == "False"
        for field in VULNERABILITY_FIELDS:
            assert field not in contact


def test_client_reference_discovery() -> None:
    assert ptd._client_reference({"cam_clientreferencenumber": 12345}) == 12345
    assert ptd._client_reference({"cam_clientref": "678"}) == 678
    assert ptd._client_reference({"contactid": "guid", "firstname": "Jo"}) is None
    assert ptd._client_reference({"cam_clientreferencenumber": "not-a-number"}) is None


def test_scheme_combos_honour_pins() -> None:
    assert ptd._scheme_combos(16, 809130003) == [(16, 809130003)]
    only_sipp = ptd._scheme_combos(None, 809130003)
    assert {combo[1] for combo in only_sipp} == {809130003}
    assert len({combo[0] for combo in only_sipp}) == len(tuple(SchemeType))
    full = ptd._scheme_combos(None, None)
    assert len(full) == len(tuple(SchemeType)) * len(tuple(SippType))


def test_vulnerability_conditional_rule() -> None:
    saw_true = saw_false = False
    guid = "11111111-2222-3333-4444-555555555555"
    for plan in ptd.build_plans(20260714, 80, vulnerability_added_by=guid):
        contact = plan["contact"]
        if contact["cam_potentiallyvulnerable"] == "True":
            saw_true = True
            for field in VULNERABILITY_FIELDS:
                assert field in contact, f"missing ** field {field}"
            values = [int(v) for v in contact["cam_contactvulnerability"].split(",")]
            assert values, "multiselect must not be empty"
            for value in values:
                assert_member(value, ContactVulnerability)
            assert_member(contact["cam_relevantbusinessunit"], RelevantBusinessUnit)
            assert contact["cam_vulnerabilityaddedby"] == guid
        else:
            saw_false = True
            for field in VULNERABILITY_FIELDS:
                assert field not in contact, f"unexpected ** field {field}"
    assert saw_true and saw_false  # both branches exercised


def test_placeholders_wired_into_plan() -> None:
    plan = sample_plans(clients=1)[0]
    assert plan["scheme"]["contactId"] == ptd.CONTACT_ID_PLACEHOLDER
    for kind in ("transfer", "beneficiary", "investment", "contribution"):
        assert plan[kind]["schemeReference"] == ptd.SCHEME_REF_PLACEHOLDER


def test_scheme_uses_the_issued_reference_values() -> None:
    # "Morgan Lloyd API Details- Graphene.docx": schemeType 16, sippType 809130016
    for plan in sample_plans():
        assert plan["scheme"]["schemeType"] == 16
        assert plan["scheme"]["sippType"] == 809130016


def test_investment_links_transfer_policy_and_provider() -> None:
    for plan in sample_plans():
        investment = plan["investment"]
        assert investment["policyNumber"] == plan["transfer"]["cam_policyno"]
        assert investment["provider"] == ptd.DEFAULT_INVESTMENT_PROVIDER
        assert set(investment) == {"provider", "schemeReference", "policyNumber"}


# --- the adviser attachment ---------------------------------------------------------------------
#
# The interesting property is that BOTH answers are valid at the create and only one of them works
# a step later: an unattached contact is created happily and then `cam_createscheme` refuses it
# with `403 0x8004f502 ... ML Team secured field`. Every contact this script made before
# `--adviser` existed was unattached, which is why its schemes needed the combination probe.

FIRM = "6ac39921-d8b7-f011-bbd3-7ced8d6b85ff"


def test_the_adviser_firm_is_sent_as_an_entity_reference() -> None:
    """A bare, braced or uppercased GUID all fail with "Invalid Format for
    cam_financialadviser_Account" — the vendor wants `/accounts(<guid>)`."""
    contact = ptd.build_contact(random.Random(1), 0, None, FIRM)  # noqa: S311
    assert contact["cam_financialadviser_Account"] == f"/accounts({FIRM})"


def test_braces_are_stripped_from_the_guid() -> None:
    """A GUID copied out of a Windows tool arrives braced, and the vendor refuses it."""
    contact = ptd.build_contact(random.Random(1), 0, None, "{" + FIRM + "}")  # noqa: S311
    assert contact["cam_financialadviser_Account"] == f"/accounts({FIRM})"


def test_no_adviser_omits_the_field_entirely() -> None:
    """Omitted, not null: a present-but-empty optional parameter has been seen to trip
    "Error identified in Payload" on these actions."""
    contact = ptd.build_contact(random.Random(1), 0, None, None)  # noqa: S311
    assert "cam_financialadviser_Account" not in contact


def test_adviser_both_alternates_so_one_run_shows_the_difference() -> None:
    plans = ptd.build_plans(1, 4, None, FIRM, "both", FIRM)
    attached = [bool(p["_adviser_firm"]) for p in plans]
    assert attached == [True, False, True, False]


def test_adviser_firm_attaches_every_contact() -> None:
    plans = ptd.build_plans(1, 3, None, FIRM, "firm", FIRM)
    assert all(p["contact"].get("cam_financialadviser_Account") for p in plans)


def test_adviser_none_attaches_nothing() -> None:
    plans = ptd.build_plans(1, 3, None, FIRM, "none", FIRM)
    assert not any("cam_financialadviser_Account" in p["contact"] for p in plans)


def test_the_adviser_choice_does_not_change_the_rest_of_the_payload() -> None:
    """The rng draw has to stay unconditional, or `--adviser both` would produce two
    populations that differ in more than their attachment and nothing would be comparable."""
    attached = ptd.build_plans(7, 2, None, FIRM, "firm", FIRM)
    plain = ptd.build_plans(7, 2, None, FIRM, "none", FIRM)
    for a, b in zip(attached, plain, strict=True):
        assert {k: v for k, v in a["contact"].items() if k != "cam_financialadviser_Account"} == b[
            "contact"
        ]


# --- the new record kinds ------------------------------------------------------------------------


def test_a_valuation_totals_its_fund_splits() -> None:
    """THE ARITHMETIC THE VENDOR CHECKS. `PolicyValue` must equal the combined fund splits'
    market value, so the splits are generated first and the total summed from them."""
    valuation = ptd.build_valuation(random.Random(3), 0, "GRTEST0001")  # noqa: S311
    assert valuation["PolicyValue"] == round(
        sum(float(s["MarketValue"]) for s in valuation["FundSplits"]), 2
    )


def test_a_valuation_names_the_policy_the_investment_created() -> None:
    """It is keyed on the POLICY, not the scheme — so a valuation for a policy number nothing
    created is refused, and that is easy to do by accident."""
    plan = ptd.build_client_plan(random.Random(3), 0)  # noqa: S311
    assert plan["valuation"]["PolicyReference"] == plan["transfer"]["cam_policyno"]


def test_a_valuation_uses_synthetic_isins() -> None:
    """A real ISIN in a test record implies a real holding."""
    valuation = ptd.build_valuation(random.Random(3), 0, "GRTEST0001")  # noqa: S311
    assert all(str(s["Isin"]).startswith("GB00TEST") for s in valuation["FundSplits"])


def test_drawdowns_yes_no_fields_are_strings() -> None:
    """The vendor documents these as the STRINGS "0"/"1", not booleans, and is not consistent
    about it — `advised` on the benefit application is a real bool."""
    drawdown = ptd.build_drawdown(random.Random(4), 0)  # noqa: S311
    for field in (
        "guidanceAppointmentAttendance",
        "adviceAppointmentAttendance",
        "recentlyReceivedGuidance",
        "recentlyReceivedAdvice",
        "doesNotWantAdvice",
    ):
        assert isinstance(drawdown[field], str), field
    assert isinstance(ptd.build_benefit(random.Random(4), 0)["advised"], bool)  # noqa: S311


def test_drawdown_payment_day_is_a_documented_value() -> None:
    """`paymentDay` accepts only 6, 14, 21 or 28."""
    for seed in range(20):
        assert ptd.build_drawdown(random.Random(seed), 0)["paymentDay"] in (6, 14, 21, 28)  # noqa: S311


def test_drawdown_and_illustration_are_opt_in() -> None:
    """Drawdown sets up income payments to a member — the one call here whose consequence is a
    payment rather than a record — and an illustration needs a URL the vendor can fetch."""
    assert set(ptd.OPT_IN_KINDS) == {"drawdown", "illustration"}
    default = ptd._chosen_kinds("", drawdown=False, illustration=False)
    assert "drawdown" not in default
    assert "illustration" not in default


def test_asking_for_an_opt_in_kind_by_name_runs_it() -> None:
    """`--only drawdown` IS the explicit request the opt-in exists to require."""
    assert ptd._chosen_kinds("drawdown", drawdown=False, illustration=False) == ("drawdown",)


def test_the_flags_add_the_opt_in_kinds() -> None:
    chosen = ptd._chosen_kinds("", drawdown=True, illustration=True)
    assert "drawdown" in chosen and "illustration" in chosen


def test_every_planned_kind_is_a_known_record_kind() -> None:
    """A plan key nothing in the live loop reads would be built and silently never sent."""
    plan = ptd.build_client_plan(random.Random(5), 0)  # noqa: S311
    planned = {k for k in plan if not k.startswith("_")}
    assert planned <= set(ptd.RECORD_KINDS)


def test_the_patch_bodies_carry_the_transfers_required_trio() -> None:
    """`cam_personalpolicies` documents all three as required on the PATCH."""
    updates = ptd.build_updates(random.Random(6), 0)  # noqa: S311
    assert set(updates["transfer"]) == {
        "cam_transferdate",
        "cam_transferamount",
        "cam_fundnotes",
    }


def test_the_dev_org_guard_recognises_the_dev_host(monkeypatch: Any) -> None:
    """Crude, and crude in the safe direction: a production host has to be waved through."""
    monkeypatch.setenv("MORGAN_LLOYD_RESOURCE_URL", "https://cliftonassetdev.crm11.dynamics.com")
    assert ptd._is_dev_org()
    monkeypatch.setenv("MORGAN_LLOYD_RESOURCE_URL", "https://cliftonasset.crm11.dynamics.com")
    assert not ptd._is_dev_org()


# --- the numbering ------------------------------------------------------------------------------
#
# Every client's email and external id are keyed on its INDEX alone, so a second run starting at 0
# collides with the first and every client is skipped — including all of its downstream records.
# That is how a whole run reported `skipped` for valuation, benefit application and the PATCHes
# while looking like it had done something.


def test_the_next_free_index_is_one_past_the_highest() -> None:
    emails = [
        "ml-test-000@example.grapheneplatforms.com",
        "ml-test-007@example.grapheneplatforms.com",
    ]
    assert ptd.next_free_index(emails) == 8


def test_a_gap_is_counted_over_not_filled() -> None:
    """A gap means a client that failed part way through. Reusing its index would collide with
    the contact that DID get created and leave the hole anyway."""
    assert (
        ptd.next_free_index(
            [
                "ml-test-000@example.grapheneplatforms.com",
                "ml-test-005@example.grapheneplatforms.com",
            ]
        )
        == 6
    )


def test_an_empty_crm_starts_at_zero() -> None:
    assert ptd.next_free_index([]) == 0


def test_only_this_scripts_addresses_count() -> None:
    """A real contact must not push the numbering along, and `ml-test-abc@` is not a numbered
    record at all."""
    assert (
        ptd.next_free_index(
            [
                "ml-test-002@example.grapheneplatforms.com",
                "someone.real@clifton-asset.co.uk",
                "ml-test-abc@example.grapheneplatforms.com",
                "ml-test-999@somewhere.else.com",
                "",
            ]
        )
        == 3
    )


def test_an_offset_batch_uses_the_offset_emails() -> None:
    plans = ptd.build_plans(1, 2, None, FIRM, "none", None, 8)
    assert [p["contact"]["emailaddress1"] for p in plans] == [
        "ml-test-008@example.grapheneplatforms.com",
        "ml-test-009@example.grapheneplatforms.com",
    ]
    assert [p["contact"]["cam_externalclientid"] for p in plans] == [
        "GRAPHENE-TEST-0008",
        "GRAPHENE-TEST-0009",
    ]


def test_a_clients_payload_does_not_depend_on_where_the_batch_started() -> None:
    """THE REASON THE RNG IS SEEDED PER CLIENT rather than threaded through the batch. Otherwise
    an offset silently re-rolls everybody and no two runs are comparable."""
    from_zero = ptd.build_plans(1, 12, None, FIRM, "none", None, 0)
    from_eight = ptd.build_plans(1, 4, None, FIRM, "none", None, 8)
    assert from_zero[8]["contact"] == from_eight[0]["contact"]
    assert from_zero[11]["contact"] == from_eight[3]["contact"]


def test_the_plan_carries_its_own_index() -> None:
    """The live loop labels each line by it. Enumerating position instead would print `[000]`
    for a client whose email says 008."""
    plans = ptd.build_plans(1, 2, None, FIRM, "none", None, 8)
    assert [p["_index"] for p in plans] == [8, 9]


# --- the adviser PAIR ---------------------------------------------------------------------------
#
# Both fields are `lookup` type and BOTH take an entity reference. Learned from the org in two
# steps: the firm alone is refused with "Financial Adviser contact must be populated for the
# adviser firm", and a bare GUID for the contact is refused with "Invalid Format for
# cam_FinancialAdviserContact". Neither is discoverable from the field table alone.

ADVISER_CONTACT = "4dc533b3-708c-f111-8077-6045bd0e08b2"


def test_the_adviser_contact_is_an_entity_reference_too() -> None:
    contact = ptd.build_contact(random.Random(1), 0, None, FIRM, ADVISER_CONTACT)  # noqa: S311
    assert contact["cam_FinancialAdviserContact"] == f"/contacts({ADVISER_CONTACT})"


def test_the_adviser_contact_is_only_sent_with_a_firm() -> None:
    """Alone it means nothing — the vendor's rule is about a contact attached to a FIRM."""
    contact = ptd.build_contact(random.Random(1), 0, None, None, ADVISER_CONTACT)  # noqa: S311
    assert "cam_FinancialAdviserContact" not in contact
    assert "cam_financialadviser_Account" not in contact


def test_both_halves_of_the_pair_are_sent_together() -> None:
    plans = ptd.build_plans(1, 1, None, FIRM, "firm", FIRM, 0, ADVISER_CONTACT)
    contact = plans[0]["contact"]
    assert contact["cam_financialadviser_Account"] == f"/accounts({FIRM})"
    assert contact["cam_FinancialAdviserContact"] == f"/contacts({ADVISER_CONTACT})"


def test_adviser_none_sends_neither_half() -> None:
    plans = ptd.build_plans(1, 1, None, FIRM, "none", FIRM, 0, ADVISER_CONTACT)
    contact = plans[0]["contact"]
    assert "cam_financialadviser_Account" not in contact
    assert "cam_FinancialAdviserContact" not in contact


# --- the action envelope ------------------------------------------------------------------------
#
# It is NOT consistent, and assuming it is has cost two bugs in opposite directions.


def test_a_nested_identifier_is_found() -> None:
    """`cam_createcontact` nests `contactid` under `response`. Reading the top level alone loses
    it — which is how `sync-to-morgan-lloyd` created a contact, discarded its id, and duplicated
    the person on the next attempt."""
    answer = {"@odata.context": "…", "response": {"contactid": "guid-1", "responseKey": 0}}
    assert ptd.envelope_value(answer, "contactid") == "guid-1"


def test_a_top_level_identifier_is_found() -> None:
    """`cam_setmemberdetails` does the REVERSE: `benefitApplicationId` and `ssoUrl` sit at the top
    level and `response` carries only `responseKey`/`message`. Reading the nested object first
    found neither, so the benefit application reported "no ssoUrl" and drawdown was always
    skipped for want of an id the vendor had returned. Observed live, 09 Aug 2026."""
    answer = {
        "@odata.context": "…",
        "benefitApplicationId": "guid-2",
        "ssoUrl": "https://digitaltest.ctc.uk.com/DCube/…",
        "response": {"responseKey": 0, "message": ""},
    }
    assert ptd.envelope_value(answer, "benefitApplicationId") == "guid-2"
    assert ptd.envelope_value(
        answer,
        "ssoUrl",
    ).startswith("https://")


def test_the_top_level_wins_when_both_carry_it() -> None:
    answer = {"contactid": "outer", "response": {"contactid": "inner"}}
    assert ptd.envelope_value(answer, "contactid") == "outer"


def test_a_missing_key_is_none_not_empty_string() -> None:
    assert ptd.envelope_value({"response": {"responseKey": 0}}, "contactid") is None
    assert ptd.envelope_value({"contactid": ""}, "contactid") is None


def test_a_non_dict_response_does_not_break_the_lookup() -> None:
    """Some actions answer with `response` as a string."""
    assert ptd.envelope_value({"contactid": "guid", "response": "ok"}, "contactid") == "guid"
