"""Every field `build_contact` sends is one the vendor documents, in the type they document.

THIS FILE EXISTS BECAUSE THREE DEFECTS SURVIVED A GREEN GATE, and all three are invisible to a
test that asserts on `build_contact`'s output alone:

1. **`cam_additionalvulnerabilitynotes` is not a Morgan Lloyd field.** It appears nowhere in
   `vendor_manifest.json`, only in our own code. Dynamics IGNORES a field it does not recognise
   — it does not 400 — so every vulnerable client's notes were dropped and the call reported
   success. The documented name is `cam_detailssupportingnotes`.
2. **`cam_potentiallyvulnerable: "True"` was sent with none of the detail it makes required.**
   The vendor marks the vulnerability fields `No**`, i.e. required once the flag is set, and
   `crm.contacts.vulnerability_reasons` (0054) holds exactly the four FCA drivers their
   `cam_vulnerabilitydrivers` takes — a field we held the data for and never sent.
3. **`cam_previouslytakenflexiaccessbenefits` was sent as a Python bool** against a field the
   vendor documents as `int` with allowed values 0 and 1. `True` serialises to JSON `true`, which
   is not the request `1` is, against an `Edm.Int32` attribute. `platform_actions._typed` has a
   whole docstring about this trap and the console path got it right; this path did not.

Each is a mismatch between our payload and the DOCUMENTED CONTRACT, so the contract is what the
assertions read. `vendor_manifest.json` is generated from the vendor's own page
(`moon run morgan-lloyd:build-manifest`), so a field they add, retype or rename shows up here on
the next rebuild rather than on the next live call.

This is deliberately NOT a check that we send everything they document — plenty is legitimately
absent because we hold nothing for it (`cam_taxrelief` is a declaration, not a fact we can
derive). It is the one-directional check: nothing we send is unrecognised, mistyped, or an
undocumented option-set value.
"""

import pathlib
import re
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from morgan_lloyd import enums
from morgan_lloyd.manifest import Field, operation

from app.integrations.morgan_lloyd import contacts, transfers

#: A client with every column `build_contact` reads populated, so the payload it produces is the
#: widest one this mapper can emit. A narrower fixture is what let an unrecognised field hide:
#: `cam_additionalvulnerabilitynotes` is only reachable for a VULNERABLE client, and no test
#: built one.
FULL_CLIENT: dict[str, Any] = {
    "uid": "a1b2c3d4e5f6",
    "title": "Dr",
    "first_name": "Ines",
    "middle_name": "Margarethe",
    "surname": "Okonkwo",
    "email": "ines.okonkwo@example.com",
    "dob": date(1968, 3, 11),
    "nationality": ["GB", "NG"],
    "national_identifier": "QQ123456C",
    "gender": "female",
    "mobile": "07700900000",
    "phone": "01179000000",
    "address_line1": "12 Widcombe Hill",
    "address_line2": "Widcombe",
    "address_city": "Bath",
    "address_county": "Somerset",
    "address_postcode": "BA2 6AA",
    "marital_status": "widowed",
    "retirement_age": 62,
    "employment_status": "self_employed",
    "source_of_wealth": "Business Interests",
    "relevant_uk_earnings": Decimal("84250.00"),
    "flexi_access_taken": True,
    "is_vulnerable": True,
    "vulnerability_reasons": ["life_event", "resilience"],
    "vulnerability_characteristics": ["bereavement", "low_emotional_resilience"],
    "vulnerability_notes": "Recently bereaved; asked for correspondence by post only.",
    "tax_relief": "uk_relevant_earnings",
}


@pytest.fixture(scope="module")
def documented() -> dict[str, Field]:
    """`cam_createcontact`'s field table, keyed by name."""
    found = operation("cam_createcontact")
    assert found is not None, "cam_createcontact is missing from vendor_manifest.json"
    return {f.name: f for f in found.fields}


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    return contacts.build_contact(FULL_CLIENT)


class TestEveryFieldIsDocumented:
    """Defect 1: an unrecognised field name is a silent no-op, not an error."""

    def test_no_field_is_undocumented(
        self, payload: dict[str, Any], documented: dict[str, Field]
    ) -> None:
        unknown = sorted(set(payload) - set(documented))
        assert not unknown, (
            f"build_contact sends field(s) cam_createcontact does not document: {unknown}. "
            "Dynamics ignores what it does not recognise, so these change nothing and the call "
            "still reports success."
        )

    def test_the_notes_field_is_the_documented_one(self, payload: dict[str, Any]) -> None:
        """The specific regression: the notes went out under a name that does not exist."""
        assert "cam_additionalvulnerabilitynotes" not in payload
        assert payload["cam_detailssupportingnotes"] == FULL_CLIENT["vulnerability_notes"]

    def test_the_undocumented_name_is_not_in_the_contract(
        self, documented: dict[str, Field]
    ) -> None:
        """Guards the assertion above from becoming vacuous if the vendor ever adds the name."""
        assert "cam_additionalvulnerabilitynotes" not in documented


class TestEveryValueMatchesTheDocumentedType:
    """Defect 3: Dynamics is typed, and the mismatch only surfaces on a live call."""

    def test_int_fields_carry_ints_not_bools(
        self, payload: dict[str, Any], documented: dict[str, Field]
    ) -> None:
        """`bool` is a subclass of `int` in Python and `true` in JSON, so `isinstance` is not
        enough — the check has to exclude bool explicitly, which is the whole defect."""
        wrong = {
            name: value
            for name, value in payload.items()
            if documented[name].type.strip().lower() == "int"
            and not (isinstance(value, int) and not isinstance(value, bool))
        }
        assert not wrong, (
            f"documented as int, sent as something else: {wrong}. A JSON bool against an "
            "Edm.Int32 attribute is a vendor 400."
        )

    def test_flexi_access_is_the_documented_int(self, payload: dict[str, Any]) -> None:
        assert payload["cam_previouslytakenflexiaccessbenefits"] == 1
        assert not isinstance(payload["cam_previouslytakenflexiaccessbenefits"], bool)

    def test_flexi_access_false_is_zero_not_omitted(self) -> None:
        """False is an ANSWER — the MPAA has not been triggered — and 0 is how the vendor spells
        it. Omitting it would leave their field empty, which reads as 'not asked'."""
        built = contacts.build_contact({**FULL_CLIENT, "flexi_access_taken": False})
        assert built["cam_previouslytakenflexiaccessbenefits"] == 0

    def test_decimal_fields_are_json_numbers(
        self, payload: dict[str, Any], documented: dict[str, Field]
    ) -> None:
        """A `Decimal` is not JSON-serialisable, so the conversion has to happen here."""
        for name, value in payload.items():
            if documented[name].type.strip().lower() == "decimal":
                assert isinstance(value, float | int), f"{name} is {type(value).__name__}"
                assert not isinstance(value, Decimal)

    def test_string_fields_are_strings(
        self, payload: dict[str, Any], documented: dict[str, Field]
    ) -> None:
        for name, value in payload.items():
            if documented[name].type.strip().lower() == "string":
                assert isinstance(value, str), f"{name} is {type(value).__name__}"

    def test_the_vulnerability_flag_is_a_string_not_a_bool(self, payload: dict[str, Any]) -> None:
        """Their one deliberate oddity, and the reason keying on the documented type beats
        guessing from the value: this one really is the STRING "True"."""
        assert payload["cam_potentiallyvulnerable"] == "True"

    def test_lookups_are_entity_references(
        self, payload: dict[str, Any], documented: dict[str, Field]
    ) -> None:
        """A bare GUID is refused with `Invalid Format for <field>`; the shape is `/set(guid)`."""
        for name, value in payload.items():
            if documented[name].type.strip().lower() == "lookup":
                assert str(value).startswith("/"), f"{name} is not an entity reference: {value!r}"


class TestOptionSetValuesAreDocumented:
    """A value outside the documented set is the `cam_schemerefs` mistake in miniature."""

    def test_every_option_set_value_is_a_member(
        self, payload: dict[str, Any], documented: dict[str, Field]
    ) -> None:
        for name, value in payload.items():
            field = documented[name]
            if not field.options:
                continue
            allowed = {option.value for option in field.options}
            parts = str(value).split(",") if field.multiselect else [value]
            for part in parts:
                text = str(part).strip()
                if not text:
                    continue
                assert int(text) in allowed, (
                    f"{name}={text} is not a documented member of its option set "
                    f"({sorted(allowed)})"
                )

    def test_a_multiselect_is_one_comma_separated_string(
        self, payload: dict[str, Any], documented: dict[str, Field]
    ) -> None:
        """Not a list, not repeated keys — the vendor parses the string itself."""
        for name, value in payload.items():
            if documented[name].multiselect:
                assert isinstance(value, str), f"{name} is {type(value).__name__}"


class TestVulnerabilityDetail:
    """Defect 2: the flag was set and the detail it makes required was not sent."""

    def test_the_drivers_are_sent_when_the_flag_is(self, payload: dict[str, Any]) -> None:
        assert payload["cam_potentiallyvulnerable"] == "True"
        assert payload["cam_vulnerabilitydrivers"] == "809130001,809130002"

    def test_the_drivers_are_the_fcas_four_on_both_sides(
        self, documented: dict[str, Field]
    ) -> None:
        """0054 chose FG21/1's vocabulary and so did the vendor. If either side gains a member
        the mapping is incomplete, and a driver that maps to nothing is silently dropped."""
        theirs = {
            (option.label or "").strip().lower()
            for option in documented["cam_vulnerabilitydrivers"].options
        }
        assert theirs == {"health", "life events", "resilience", "capability"}
        assert set(contacts._VULNERABILITY_DRIVERS) == {
            "health",
            "life_event",
            "resilience",
            "capability",
        }

    def test_no_vulnerability_detail_when_the_client_is_not_flagged(self) -> None:
        """The detail describes a vulnerability. Sending it for an unflagged client would assert
        one, and 0054's own CHECK keeps the two coherent in the record."""
        built = contacts.build_contact(
            {**FULL_CLIENT, "is_vulnerable": False, "vulnerability_reasons": None}
        )
        assert built["cam_potentiallyvulnerable"] == "False"
        assert "cam_vulnerabilitydrivers" not in built
        assert "cam_detailssupportingnotes" not in built

    def test_a_flagged_client_with_no_recorded_reason_omits_the_drivers(self) -> None:
        """0054 allows the flag without reasons (its CHECK only forbids the reverse), so an empty
        list must not become an empty string — the vendor parses that field itself."""
        built = contacts.build_contact({**FULL_CLIENT, "vulnerability_reasons": []})
        assert "cam_vulnerabilitydrivers" not in built

    def test_the_characteristics_are_sent_too(self, payload: dict[str, Any]) -> None:
        """0116 gave them a column, so the other half of the `No**` detail is answerable. Sorted
        integers in one comma-separated string, the vendor's own multiselect shape."""
        assert payload["cam_vulnerabilitycharacteristics"] == "809130007,809130016"

    def test_characteristics_are_still_never_derived_from_a_driver(self) -> None:
        """The reasoning that survives 0116: a driver is `life_event`, and which of the seven
        characteristics under it applies is a clinical detail nobody told us. An adviser who
        recorded none gets none sent, not a guess."""
        built = contacts.build_contact({**FULL_CLIENT, "vulnerability_characteristics": None})
        assert built["cam_vulnerabilitydrivers"]  # the driver still goes
        assert "cam_vulnerabilitycharacteristics" not in built

    def test_an_unknown_characteristic_is_dropped_at_the_boundary(self) -> None:
        """`app.contacts.normalise_vulnerability` refuses one on the way IN, which is where a
        typo should fail. By the time a row is read back the column's CHECK has already vouched
        for it, so this only has to not send garbage."""
        built = contacts.build_contact(
            {**FULL_CLIENT, "vulnerability_characteristics": ["bereavement", "not_a_thing"]}
        )
        assert built["cam_vulnerabilitycharacteristics"] == "809130007"


class TestTheFieldsWeHoldAndNowSend:
    """Each was held on `crm.contacts` and simply never sent."""

    def test_annual_earnings_is_the_hmrc_figure(self, payload: dict[str, Any]) -> None:
        assert payload["cam_annualearnings"] == 84250.0

    def test_employment_status_maps_onto_their_set(self, payload: dict[str, Any]) -> None:
        assert payload["cam_employmentstatus"] == enums.ContactEmploymentStatus.SELF_EMPLOYED

    @pytest.mark.parametrize(
        ("ours", "theirs"),
        [
            ("employed", enums.ContactEmploymentStatus.EMPLOYED),
            ("self_employed", enums.ContactEmploymentStatus.SELF_EMPLOYED),
            ("unemployed", enums.ContactEmploymentStatus.UNEMPLOYED),
            ("education", enums.ContactEmploymentStatus.STUDENT),
            ("care_over_16", enums.ContactEmploymentStatus.CARER),
            ("care_under_16s", enums.ContactEmploymentStatus.CHILD_CARER),
            ("retired", enums.ContactEmploymentStatus.PENSIONER),
            ("other", enums.ContactEmploymentStatus.OTHER),
        ],
    )
    def test_all_eight_of_our_check_values_map(
        self, ours: str, theirs: enums.ContactEmploymentStatus
    ) -> None:
        """0053's CHECK has eight members and every one has a counterpart, which is what makes
        refusing to map this field (on the grounds that it was an occupational taxonomy) the
        wrong call. Their set is the superset — `Child` has no counterpart of ours."""
        built = contacts.build_contact({**FULL_CLIENT, "employment_status": ours})
        assert built["cam_employmentstatus"] == theirs

    def test_an_unmappable_employment_status_is_omitted_not_coerced_to_other(self) -> None:
        built = contacts.build_contact({**FULL_CLIENT, "employment_status": "gardening leave"})
        assert "cam_employmentstatus" not in built

    def test_source_of_wealth_goes_out_as_the_code_and_the_clients_statement(self) -> None:
        built = contacts.build_contact(
            {
                **FULL_CLIENT,
                "source_of_wealth": "Business Interests",
                "source_of_wealth_detail": (
                    "Proceeds and retained profits from the sale of a family business"
                ),
            }
        )
        assert built["cam_sourceofwealth"] == enums.SourceOfWealth.BUSINESS_INTERESTS
        assert built["cam_sourceofwealthstring"] == (
            "Proceeds and retained profits from the sale of a family business"
        )

    def test_source_of_wealth_label_is_the_fallback_for_an_older_record(self) -> None:
        built = contacts.build_contact({**FULL_CLIENT, "source_of_wealth_detail": None})
        assert built["cam_sourceofwealthstring"] == "Business Interests"

    def test_other_source_of_wealth_sends_the_detail_as_the_string(self) -> None:
        """Other is the one member that means nothing without the words. The INT still goes
        (their option set), the string is the words, not the label "Other"."""
        built = contacts.build_contact(
            {
                **FULL_CLIENT,
                "source_of_wealth": "Other",
                "source_of_wealth_detail": "Sale of a vintage car collection",
            }
        )
        assert built["cam_sourceofwealth"] == enums.SourceOfWealth.OTHER
        assert built["cam_sourceofwealthstring"] == "Sale of a vintage car collection"

    def test_an_unrecognised_source_of_wealth_still_sends_the_text(self) -> None:
        """0053 holds this as free text on purpose — the list is theirs and grows. An answer
        outside it must still reach them, in the field that takes free text."""
        built = contacts.build_contact({**FULL_CLIENT, "source_of_wealth": "Crypto disposal"})
        assert "cam_sourceofwealth" not in built
        assert built["cam_sourceofwealthstring"] == "Crypto disposal"

    def test_their_misspelling_of_inheritance_is_accepted_both_ways(self) -> None:
        """Their option set says "Inheritence". A value copied off their own form will read that
        way, and one typed by an adviser will not."""
        for spelling in ("Inheritance", "Inheritence"):
            built = contacts.build_contact({**FULL_CLIENT, "source_of_wealth": spelling})
            assert built["cam_sourceofwealth"] == enums.SourceOfWealth.INHERITANCE

    def test_the_external_client_id_is_the_public_uid(self, payload: dict[str, Any]) -> None:
        assert payload["cam_externalclientid"] == FULL_CLIENT["uid"]

    def test_the_rest_of_the_address(self, payload: dict[str, Any]) -> None:
        assert payload["address1_line2"] == "Widcombe"
        assert payload["address1_county"] == "Somerset"

    def test_the_two_numbers_the_vendor_holds(self, payload: dict[str, Any]) -> None:
        """`mobilephone` is the mobile; `telephone1` is the other number."""
        assert payload["mobilephone"] == "07700900000"
        assert payload["telephone1"] == "01179000000"

    def test_a_client_with_no_other_numbers_sends_no_telephone1(self) -> None:
        """Omitted, not empty — the vendor's action validates against a fixed allowlist and an
        empty value has been seen to trip "Error identified in ..."."""
        payload = contacts.build_contact({**FULL_CLIENT, "phone": None})
        assert "telephone1" not in payload
        assert payload["mobilephone"] == "07700900000"

    def test_the_two_address_parts_we_hold_nothing_for_are_not_sent(
        self, payload: dict[str, Any]
    ) -> None:
        """`address1_line3` and `telephone2` are documented and we have no column behind either.
        `telephone2` stays unsent: the book holds one other number, and that is `telephone1`."""
        assert "address1_line3" not in payload
        assert "telephone2" not in payload

    def test_absent_optionals_are_omitted_not_sent_empty(self) -> None:
        """A present-but-empty value is not the same as an omitted one: the action validates
        against a fixed allowlist and an empty value has been seen to trip "Error identified in
        Payload"."""
        bare = {
            key: FULL_CLIENT[key]
            for key in (
                "first_name",
                "surname",
                "email",
                "dob",
                "nationality",
                "national_identifier",
                "gender",
                "address_line1",
                "address_city",
                "address_postcode",
            )
        }
        built = contacts.build_contact(bare)
        for absent in (
            "cam_annualearnings",
            "cam_employmentstatus",
            "cam_sourceofwealth",
            "cam_sourceofwealthstring",
            "cam_externalclientid",
            "address1_line2",
            "address1_county",
            "cam_previouslytakenflexiaccessbenefits",
        ):
            assert absent not in built, f"{absent} was sent with nothing to put in it"


class TestTheRequiredFieldsAreTheVendorsRequiredFields:
    """A missing required field should be a 422 naming it, not the vendor's
    `0x80040224 Object reference not set to an instance of an object`."""

    def test_we_declare_every_unconditionally_required_field(
        self, documented: dict[str, Field]
    ) -> None:
        """`REQUIRED_FIELDS` maps a vendor field to the Graphene column that supplies it, so the
        two lists are compared through `build_contact`'s own output: a required vendor field we
        never emit is one nothing would name before the call."""
        theirs = {
            name for name, field in documented.items() if field.required and not field.conditional
        }
        # `as_contactstatus` is required and is ours to decide (always Customer), so it is not a
        # client detail an adviser could be asked for.
        ours_from_the_record = theirs - {"as_contactstatus"}
        built = contacts.build_contact(FULL_CLIENT)
        assert ours_from_the_record <= set(built), (
            "required vendor field(s) build_contact never sends: "
            f"{sorted(ours_from_the_record - set(built))}"
        )

    @pytest.mark.parametrize("column", [key for key, _ in contacts.REQUIRED_FIELDS])
    def test_dropping_any_required_column_is_named_before_the_call(self, column: str) -> None:
        absent = contacts.missing_fields({**FULL_CLIENT, column: None})
        assert absent, f"{column} is declared required but its absence is not reported"


class TestTheOptionSetMapsMatchTheVendorExactly:
    """Every slug↔number map here is checked against `vendor_manifest.json`, not against a second
    hand-written copy of it. A map that is merely *plausible* is how the `cam_schemerefs` and the
    `cam_employmentstatus` mistakes both happened."""

    def test_the_24_characteristics_are_a_complete_bijection(
        self, documented: dict[str, Field]
    ) -> None:
        """All 24, no duplicates, no invented members. A missing one is a characteristic an
        adviser can record and we silently drop; a duplicate means two of ours collapse onto one
        of theirs."""
        theirs = {option.value for option in documented["cam_vulnerabilitycharacteristics"].options}
        ours = contacts._VULNERABILITY_CHARACTERISTICS
        assert len(theirs) == 24
        assert set(ours.values()) == theirs
        assert len(set(ours.values())) == len(ours), "two slugs share one option-set value"

    def test_visual_impairment_is_the_out_of_sequence_one(
        self, documented: dict[str, Field]
    ) -> None:
        """809130023 sits between Hearing impairment and Mental health in the vendor's LIST while
        carrying the highest number in the set. Deriving these values from list position would be
        right for 23 members and wrong for this one — found in production by a client with a
        visual impairment being recorded as something else."""
        options = documented["cam_vulnerabilitycharacteristics"].options
        by_label = {(o.label or "").strip().lower(): o.value for o in options}
        assert by_label["visual impairment"] == 809130023
        assert contacts._VULNERABILITY_CHARACTERISTICS["visual_impairment"] == 809130023
        # And it is genuinely out of order in the list, which is what makes position unusable.
        positions = [o.value for o in options]
        assert positions != sorted(positions)

    def test_the_characteristics_cover_the_apps_own_vocabulary(self) -> None:
        """The two ends of one contract: `app.contacts` declares what may be stored, this maps
        what gets sent. A slug in one and not the other is a value that saves and never arrives."""
        from app import contacts as app_contacts

        assert set(contacts._VULNERABILITY_CHARACTERISTICS) == set(
            app_contacts.VULNERABILITY_CHARACTERISTICS
        )

    def test_the_four_tax_reliefs_match(self, documented: dict[str, Field]) -> None:
        theirs = {option.value for option in documented["cam_taxrelief"].options}
        assert (
            set(contacts._TAX_RELIEF.values())
            == theirs
            == {
                809130000,
                809130001,
                809130002,
                809130003,
            }
        )

    def test_the_tax_reliefs_cover_the_apps_own_vocabulary(self) -> None:
        from app import contacts as app_contacts

        assert set(contacts._TAX_RELIEF) == set(app_contacts.TAX_RELIEFS)
        assert set(app_contacts.TAX_RELIEF_LABELS) == set(app_contacts.TAX_RELIEFS)

    def test_the_employment_statuses_match(self, documented: dict[str, Field]) -> None:
        theirs = {option.value for option in documented["cam_employmentstatus"].options}
        assert set(contacts._EMPLOYMENT_STATUS.values()) <= theirs
        # Ours is the subset: their `Child` (809130004) has no counterpart, which is fine — the
        # map only has to be TOTAL from our side.
        from app import contacts as app_contacts

        assert set(contacts._EMPLOYMENT_STATUS) == set(app_contacts.EMPLOYMENT_STATUSES)

    def test_the_vulnerability_drivers_match(self, documented: dict[str, Field]) -> None:
        theirs = {option.value for option in documented["cam_vulnerabilitydrivers"].options}
        assert set(contacts._VULNERABILITY_DRIVERS.values()) == theirs


class TestTaxRelief:
    def test_the_declaration_is_sent_as_the_documented_int(self, payload: dict[str, Any]) -> None:
        assert payload["cam_taxrelief"] == 809130000

    def test_it_is_never_derived_from_holding_an_earnings_figure(self) -> None:
        """The refusal that survives 0116. The four options are first-person statements, and
        holding £84,250 in `relevant_uk_earnings` is not the client having declared anything —
        asserting one on their behalf puts a false declaration on a pension record."""
        built = contacts.build_contact({**FULL_CLIENT, "tax_relief": None})
        assert built["cam_annualearnings"] == 84250.0, "the figure still goes"
        assert "cam_taxrelief" not in built, "the declaration does not"

    def test_an_unmappable_declaration_is_omitted_not_coerced(self) -> None:
        built = contacts.build_contact({**FULL_CLIENT, "tax_relief": "something_else"})
        assert "cam_taxrelief" not in built


class TestOccupationIsOursAlone:
    """The answer to "ML appears to have the right idea, it's separate to occupation"."""

    def test_the_vendor_documents_no_occupation_field(self, documented: dict[str, Field]) -> None:
        """They model employment STATUS and have no occupation field at all — so `occupation`
        (0116) is Graphene's own, and nothing in this payload should carry it. That is the whole
        reason the two are separate columns rather than one."""
        assert not [
            name
            for name in documented
            if "occupation" in name.lower() or "jobtitle" in name.lower()
        ]

    def test_occupation_is_not_smuggled_into_the_employment_status_field(self) -> None:
        """The failure this separation prevents: sending "GP / Doctor" where an option-set number
        belongs, or picking `Other` because the occupation did not match a status."""
        built = contacts.build_contact(
            {**FULL_CLIENT, "occupation": "GP / Doctor", "employment_status": "retired"}
        )
        assert built["cam_employmentstatus"] == enums.ContactEmploymentStatus.PENSIONER
        assert "GP / Doctor" not in str(built.values())


class TestTheOptionSetIntegersAreImportedNotCopied:
    """`morgan_lloyd.enums` is the vendored, manifest-pinned vocabulary, and the adapters that can
    import it must.

    Every map in `contacts.py` and `transfers.py` used to carry its own integers under a comment
    NAMING the enum it should have imported — `ASSET_TYPE_PENSION_PLAN = 1` beside
    "`morgan_lloyd.enums.AssetType`". Dynamics accepts any valid option-set integer, so when the
    vendor renumbers, `enums.py` is what gets rebuilt and a stale copy is a 200 that files the
    client, or the transfer, under the wrong answer. `cam_typeoftransfer` is the one whose wrong
    value instructs the ceding scheme to LIQUIDATE the portfolio, which `transfers.py`'s own
    docstring says the vendor cannot object to because both values are well-formed.

    The tests above read the manifest rather than a second copy of the numbers, so they could not
    see a divergence between two lists of literals. This one refuses the literals.
    """

    #: `cam_vulnerabilitydrivers` and `cam_vulnerabilitycharacteristics` have NO enum member —
    #: `ContactVulnerability` is a different field (`cam_contactvulnerability`, seven members) and
    #: mapping onto it would be wrong. So those two maps keep their literals, named here.
    _NO_ENUM_EXISTS = ("_VULNERABILITY_DRIVERS", "_VULNERABILITY_CHARACTERISTICS")

    @pytest.mark.parametrize("module", ["contacts", "transfers"])
    def test_no_option_set_literal_survives(self, module: str) -> None:
        source = (
            pathlib.Path(__file__).resolve().parents[1]
            / "app"
            / "integrations"
            / "morgan_lloyd"
            / f"{module}.py"
        ).read_text()
        # Drop the two enum-less maps and every comment, which legitimately quote the numbers.
        for name in self._NO_ENUM_EXISTS:
            if name in source:
                start = source.index(name)
                source = source[:start] + source[source.index("}", start) :]
        body = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        body = re.sub(r'""".*?"""', "", body, flags=re.S)
        assert not re.search(r"\b8091300\d\d\b", body), (
            f"{module}.py carries a raw Morgan Lloyd option-set integer — import it from "
            "`morgan_lloyd.enums`"
        )

    def test_the_transfer_basis_and_extent_are_the_enums_own_values(self) -> None:
        assert transfers.BASIS_TO_ML == {
            "cash": enums.TypeOfTransfer.CASH,
            "in_specie": enums.TypeOfTransfer.IN_SPECIE,
        }
        assert transfers.EXTENT_TO_ML == {
            "full": enums.TransferType.FULL,
            "partial": enums.TransferType.PARTIAL,
        }
        assert transfers.ASSET_TYPE_PENSION_PLAN == enums.AssetType.PENSION_PLANS
        assert transfers.RECORD_STATUS_TRANSFERRING == enums.AssetStatus.TRANSFERRING
