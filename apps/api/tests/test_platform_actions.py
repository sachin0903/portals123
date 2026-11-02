"""The firm console's write actions, and the validation that stands between an operator and a
live Dynamics org.

`validate` is where the value is. Every rule it enforces exists because the alternative failure
is silent or misleading:

  * Dynamics IGNORES a field it does not recognise, so a mistyped field name is a no-op that
    reports success;
  * a missing required field comes back as `0x80040224 Object reference not set to an instance
    of an object`, which names nothing;
  * and an option-set value coerced to the nearest member records a client as the wrong gender
    or the wrong title, in a system with no delete.
"""

from __future__ import annotations

import pytest

from app import platform_actions as pa


@pytest.fixture
def create_contact() -> pa.ActionDef:
    found = pa.action_for("morgan-lloyd", "create_contact")
    assert found is not None
    return found


class TestRegistry:
    def test_all_sixteen_actions_are_declared(self) -> None:
        """The set the console was specified around, by the caller's own names."""
        keys = {a.key for a in pa.actions_for("morgan-lloyd")}
        assert keys == {
            "create_contact",
            "update_contact",
            "create_adviser",
            "create_scheme",
            "create_beneficiary",
            "update_beneficiary",
            "create_transfer",
            "update_transfer",
            "create_contribution",
            "update_contribution",
            "create_investment",
            "create_valuation",
            "create_benefit_application",
            "update_benefit_application",
            "drawdown_setup",
            "submit_illustration",
        }

    def test_every_action_has_a_documented_contract(self) -> None:
        """A form we cannot build from the vendor's own documentation would have to be
        hand-written, and would drift the day they changed it.

        `documented` means FIELDS, not merely a matching section: the vendor documents the
        entity-PATCH operations by pointing at the create, so a section-exists check passes
        while the drawer renders empty."""
        described = pa.describe("morgan-lloyd")
        undocumented = [a["key"] for a in described if not a["documented"]]
        assert not undocumented, f"no usable field contract for: {undocumented}"
        assert all(a["fields"] for a in described)

    def test_the_patch_actions_borrow_the_create_contract(self) -> None:
        """ "Update Contact" carries a URL-parameter table and the words "see Create Contact".
        Parsed faithfully that is an empty form."""
        described = {a["key"]: a for a in pa.describe("morgan-lloyd")}
        assert described["update_contact"]["fields_from"] == "cam_createcontact"
        names = {f["name"] for f in described["update_contact"]["fields"]}
        assert "firstname" in names and "cam_ninumber" in names

    def test_an_action_with_its_own_fields_borrows_nothing(self) -> None:
        """The pairing is the vendor's editorial choice, not a rule — this PATCH has its own
        three fields and must not inherit someone else's."""
        described = {a["key"]: a for a in pa.describe("morgan-lloyd")}
        assert described["update_benefit_application"]["fields_from"] == ""

    def test_create_investment_targets_create_wrapper(self) -> None:
        """The doc's section title is "Create Investment"; the action is cam_createwrapper, and
        cam_createinvestment does not exist in the org."""
        found = pa.action_for("morgan-lloyd", "create_investment")
        assert found is not None and found.action == "cam_createwrapper"

    def test_the_mixed_case_action_keeps_its_case(self) -> None:
        found = pa.action_for("morgan-lloyd", "create_adviser")
        assert found is not None
        assert found.action == "cam_CreateFinancialAdviserContact"

    def test_creates_carry_a_danger_note(self) -> None:
        """Morgan Lloyd has no delete. An operator should be told before, not after."""
        for key in ("create_contact", "create_scheme", "create_transfer", "create_contribution"):
            found = pa.action_for("morgan-lloyd", key)
            assert found is not None and found.danger, f"{key} has no danger note"

    def test_an_unknown_provider_offers_nothing(self) -> None:
        assert pa.actions_for("nope") == ()
        assert pa.action_for("nope", "create_contact") is None
        # And a known provider does not answer for another's action keys.
        assert pa.action_for("seccl", "create_contact") is None


class TestDescribe:
    def test_fields_carry_the_vendor_contract(self) -> None:
        described = {a["key"]: a for a in pa.describe("morgan-lloyd")}
        fields = {f["name"]: f for f in described["create_contact"]["fields"]}
        assert fields["firstname"]["required"] is True
        assert fields["cam_annualearnings"]["type"] == "decimal"
        assert len(fields["cam_title"]["options"]) == 13

    def test_every_option_label_starts_with_a_capital(self) -> None:
        """The vendor documents `cam_previouslytakenflexiaccessbenefits` as `false`/`true`."""
        described = {a["key"]: a for a in pa.describe("morgan-lloyd")}
        for action in described.values():
            for field in [*action["fields"], *action["parameters"]]:
                for option in field["options"]:
                    label = option["label"] or ""
                    assert label[:1] == label[:1].upper(), f"{field['name']}: {label!r}"
        fields = {f["name"]: f for f in described["create_contact"]["fields"]}
        flexi = fields["cam_previouslytakenflexiaccessbenefits"]["options"]
        assert flexi == [{"label": "False", "value": 0}, {"label": "True", "value": 1}]

    def test_conditional_requirements_are_flagged_not_flattened(self) -> None:
        described = {a["key"]: a for a in pa.describe("morgan-lloyd")}
        fields = {f["name"]: f for f in described["create_contact"]["fields"]}
        adviser = fields["cam_FinancialAdviserContact"]
        assert adviser["required"] is False
        assert adviser["conditional"] is True
        assert "*" in adviser["required_note"]


class TestValidate:
    def _payload(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "as_contactstatus": 809130002,
            "cam_title": 809130000,
            "firstname": "John",
            "lastname": "Test",
            "emailaddress1": "test@example.com",
            "cam_nationality": "UK",
            "gendercode": 1,
            "cam_ninumber": "AG108877A",
            "address1_line1": "Home",
            "address1_city": "Bristol",
            "address1_postalcode": "HU3 2BA",
            "cam_birthdate": "1981-02-28",
        }
        base.update(overrides)
        return base

    def test_a_complete_payload_passes(self, create_contact: pa.ActionDef) -> None:
        out = pa.validate("morgan-lloyd", create_contact, self._payload())
        assert out["firstname"] == "John"

    def test_a_missing_required_field_is_named(self, create_contact: pa.ActionDef) -> None:
        """The vendor's own answer is a null-reference error that names nothing."""
        payload = self._payload()
        del payload["gendercode"]
        with pytest.raises(pa.ActionRefused, match="gendercode"):
            pa.validate("morgan-lloyd", create_contact, payload)

    def test_an_unknown_field_is_refused(self, create_contact: pa.ActionDef) -> None:
        """Dynamics silently ignores what it does not recognise, so a typo would look like a
        successful call that changed nothing."""
        with pytest.raises(pa.ActionRefused, match="firstnmae"):
            pa.validate("morgan-lloyd", create_contact, self._payload(firstnmae="John"))

    def test_an_undocumented_option_value_is_refused(self, create_contact: pa.ActionDef) -> None:
        """Gender on this org is exactly {1 Male, 2 Female}. Anything else is refused at the
        boundary rather than coerced — the rule `build_contact` already applies."""
        with pytest.raises(pa.ActionRefused, match="gendercode"):
            pa.validate("morgan-lloyd", create_contact, self._payload(gendercode=3))

    def test_the_refusal_lists_the_documented_values(self, create_contact: pa.ActionDef) -> None:
        with pytest.raises(pa.ActionRefused, match="Male=1"):
            pa.validate("morgan-lloyd", create_contact, self._payload(gendercode=99))

    def test_a_valid_option_value_passes(self, create_contact: pa.ActionDef) -> None:
        out = pa.validate("morgan-lloyd", create_contact, self._payload(familystatuscode=5))
        assert out["familystatuscode"] == 5

    def test_a_multiselect_is_checked_member_by_member(self, create_contact: pa.ActionDef) -> None:
        """The vendor takes a comma-separated STRING for these, so a bad member hides inside a
        value that is otherwise well-formed."""
        good = self._payload(cam_vulnerabilitydrivers="809130000, 809130001")
        assert pa.validate("morgan-lloyd", create_contact, good)
        bad = self._payload(cam_vulnerabilitydrivers="809130000, 999")
        with pytest.raises(pa.ActionRefused, match="cam_vulnerabilitydrivers"):
            pa.validate("morgan-lloyd", create_contact, bad)

    def test_a_non_numeric_option_is_refused(self, create_contact: pa.ActionDef) -> None:
        with pytest.raises(pa.ActionRefused, match="option-set number"):
            pa.validate("morgan-lloyd", create_contact, self._payload(gendercode="Male"))

    def test_blanks_are_dropped_not_sent(self, create_contact: pa.ActionDef) -> None:
        """An empty form field is "not supplied", not "set this to empty string" — which for an
        option set would be a type error at the vendor."""
        out = pa.validate("morgan-lloyd", create_contact, self._payload(middlename=""))
        assert "middlename" not in out

    def test_conditional_fields_are_not_enforced(self, create_contact: pa.ActionDef) -> None:
        """`No*`/`No**` conditions are stated in the vendor's prose. Guessing them wrong blocks
        legitimate calls, so the drawer shows the note and the server does not enforce it."""
        assert pa.validate("morgan-lloyd", create_contact, self._payload())

    def test_a_patch_does_not_demand_the_creates_required_fields(self) -> None:
        """An update sets what it names. Enforcing Create Contact's twelve required fields on a
        PATCH would refuse a one-field postcode correction."""
        update = pa.action_for("morgan-lloyd", "update_contact")
        assert update is not None
        out = pa.validate("morgan-lloyd", update, {"address1_postalcode": "BS1 4DJ"})
        assert out == {"address1_postalcode": "BS1 4DJ"}

    def test_a_patch_still_checks_option_sets(self) -> None:
        """The relaxation is about REQUIREDNESS only — a wrong option value is as bad on an
        update as on a create, and harder to notice."""
        update = pa.action_for("morgan-lloyd", "update_contact")
        assert update is not None
        with pytest.raises(pa.ActionRefused, match="gendercode"):
            pa.validate("morgan-lloyd", update, {"gendercode": 7})

    def test_an_empty_patch_is_refused(self) -> None:
        update = pa.action_for("morgan-lloyd", "update_contact")
        assert update is not None
        with pytest.raises(pa.ActionRefused, match="at least one field"):
            pa.validate("morgan-lloyd", update, {})

    def test_the_free_text_vulnerability_fields_are_not_treated_as_option_sets(
        self, create_contact: pa.ActionDef
    ) -> None:
        """`cam_potentiallyvulnerable` is a STRING "True"/"False" despite sitting beside two
        multiselects — coercing it to a number would be refused by the vendor."""
        out = pa.validate(
            "morgan-lloyd", create_contact, self._payload(cam_potentiallyvulnerable="True")
        )
        assert out["cam_potentiallyvulnerable"] == "True"


class TestTypedPayload:
    """A form posts strings; Dynamics is typed.

    `cam_title` is an `Edm.Int32`, so `{"cam_title": "809130000"}` is not the same request as
    `{"cam_title": 809130000}` — and the difference is invisible from here. It surfaces as a
    vendor 400 on a live call against a real org, which is the worst place to find it.

    `contacts.build_contact` — the path that has actually created contacts — sends ints for the
    option sets, a real bool for the one boolean and the STRING "False" for
    `cam_potentiallyvulnerable`. These pin that the manifest-driven path reproduces that shape,
    keyed on the DOCUMENTED TYPE rather than on what the value happens to look like.
    """

    def _contact(self, **overrides: object) -> dict[str, object]:
        return TestValidate()._payload(**overrides)

    def test_an_option_set_is_sent_as_a_number(self, create_contact: pa.ActionDef) -> None:
        out = pa.validate("morgan-lloyd", create_contact, self._contact(cam_title="809130000"))
        assert out["cam_title"] == 809130000
        assert isinstance(out["cam_title"], int)

    def test_a_documented_int_is_sent_as_a_number(self, create_contact: pa.ActionDef) -> None:
        out = pa.validate("morgan-lloyd", create_contact, self._contact(cam_retirementageint="67"))
        assert out["cam_retirementageint"] == 67

    def test_a_decimal_is_sent_as_a_number(self, create_contact: pa.ActionDef) -> None:
        out = pa.validate("morgan-lloyd", create_contact, self._contact(cam_annualearnings="52500"))
        assert out["cam_annualearnings"] == 52500.0
        assert isinstance(out["cam_annualearnings"], float)

    def test_a_string_field_stays_a_string(self, create_contact: pa.ActionDef) -> None:
        """Including one whose value is entirely digits. `cam_ninumber` would survive, but a
        postcode like "1234" or an all-numeric external client id must not become an int."""
        out = pa.validate(
            "morgan-lloyd", create_contact, self._contact(cam_externalclientid="0044")
        )
        assert out["cam_externalclientid"] == "0044"

    def test_the_free_text_boolean_stays_the_vendors_string(
        self, create_contact: pa.ActionDef
    ) -> None:
        """`cam_potentiallyvulnerable` is documented `string`. Sending a real `False` makes the
        dependent vulnerability fields required — `build_contact`'s own note."""
        out = pa.validate(
            "morgan-lloyd", create_contact, self._contact(cam_potentiallyvulnerable="False")
        )
        assert out["cam_potentiallyvulnerable"] == "False"

    def test_a_documented_bool_becomes_a_real_boolean(self) -> None:
        """`cam_createscheme.beneficiaryFund` is typed `bool` and the page says "Set to True".
        The drawer posts that word; the vendor wants a JSON boolean."""
        scheme = pa.action_for("morgan-lloyd", "create_scheme")
        assert scheme is not None
        field = pa.contract(scheme)
        assert field is not None and field.field("beneficiaryFund") is not None
        out = pa._typed(field.field("beneficiaryFund"), "True")  # type: ignore[arg-type]
        assert out is True
        assert pa._typed(field.field("beneficiaryFund"), "False") is False  # type: ignore[arg-type]

    def test_the_two_zeroes_are_not_confused(self) -> None:
        """THE REASON COERCION IS KEYED ON THE TYPE. "0" is a valid STRING answer for
        `guidanceAppointmentAttendance` (documented `string`, options `No : 0`) and the INTEGER 0
        for `cam_previouslytakenflexiaccessbenefits` (documented `int`, same two labels).
        Inferring from the value would silently swap them."""
        drawdown = pa.action_for("morgan-lloyd", "drawdown_setup")
        assert drawdown is not None
        found = pa.contract(drawdown)
        assert found is not None
        attendance = found.field("guidanceAppointmentAttendance")
        assert attendance is not None and attendance.type == "string"
        assert pa._typed(attendance, "0") == "0"

        contact = pa.contract(pa.action_for("morgan-lloyd", "create_contact"))  # type: ignore[arg-type]
        assert contact is not None
        flexi = contact.field("cam_previouslytakenflexiaccessbenefits")
        assert flexi is not None and flexi.type == "int"
        assert pa._typed(flexi, "0") == 0

    def test_a_multiselect_is_sent_as_one_comma_separated_string(
        self, create_contact: pa.ActionDef
    ) -> None:
        """Not a list, and not a number: the vendor parses this string itself. Whitespace the
        form may have introduced is dropped, because " 809130000 , 809130001" is not obviously
        safe to hand to someone else's parser."""
        out = pa.validate(
            "morgan-lloyd",
            create_contact,
            self._contact(cam_vulnerabilitydrivers="809130000, 809130001"),
        )
        assert out["cam_vulnerabilitydrivers"] == "809130000,809130001"

    def test_a_non_numeric_int_is_refused_by_name(self, create_contact: pa.ActionDef) -> None:
        with pytest.raises(pa.ActionRefused, match="cam_retirementageint"):
            pa.validate("morgan-lloyd", create_contact, self._contact(cam_retirementageint="sixty"))

    def test_a_non_numeric_decimal_is_refused_by_name(self, create_contact: pa.ActionDef) -> None:
        with pytest.raises(pa.ActionRefused, match="cam_annualearnings"):
            pa.validate("morgan-lloyd", create_contact, self._contact(cam_annualearnings="lots"))

    def test_a_date_is_passed_through_unparsed(self, create_contact: pa.ActionDef) -> None:
        """Deliberately not reformatted. The vendor's own examples are ISO, and re-rendering it
        here would be this module inventing a shape."""
        out = pa.validate("morgan-lloyd", create_contact, self._contact(cam_birthdate="1981-02-28"))
        assert out["cam_birthdate"] == "1981-02-28"


class TestTheDrawdownContractIsClosed:
    """Drawdown is the action here that moves a client's money, and until the manifest parser was
    widened every one of its option sets was a free-text box — unvalidated on both sides."""

    def test_its_option_sets_are_known(self) -> None:
        described = {a["key"]: a for a in pa.describe("morgan-lloyd")}
        fields = {f["name"]: f for f in described["drawdown_setup"]["fields"]}
        for name in (
            "drawdownOption",
            "incomeFrequency",
            "investmentPathway",
            "paymentDay",
            "guidanceAppointmentAttendance",
        ):
            assert fields[name]["options"], f"{name} renders as free text"

    def _payload(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "benefitApplicationId": "1234",
            "accountName": "J Test",
            "accountNumber": "12345678",
            "sortCode": "20-00-00",
            "investmentPathway": 6,
            "phasedPCLS": "False",
            "phasedUFPLS": "False",
            "tfcUsed": "0",
        }
        base.update(overrides)
        return base

    def test_a_complete_drawdown_payload_passes(self) -> None:
        drawdown = pa.action_for("morgan-lloyd", "drawdown_setup")
        assert drawdown is not None
        out = pa.validate("morgan-lloyd", drawdown, self._payload(drawdownOption=2))
        assert out["drawdownOption"] == 2
        # The bools the page describes with the words True and False.
        assert out["phasedPCLS"] is False
        assert out["tfcUsed"] == 0.0

    def test_an_undocumented_drawdown_option_is_refused(self) -> None:
        """`drawdownOption` documents 30 members, and 1 is not one of them — the values start
        at 2. Before the parser fix this sent 1 to the vendor without comment."""
        drawdown = pa.action_for("morgan-lloyd", "drawdown_setup")
        assert drawdown is not None
        with pytest.raises(pa.ActionRefused, match="drawdownOption"):
            pa.validate("morgan-lloyd", drawdown, self._payload(drawdownOption=1))

    def test_a_comma_containing_label_survived_the_parse(self) -> None:
        drawdown = pa.action_for("morgan-lloyd", "drawdown_setup")
        assert drawdown is not None
        found = pa.contract(drawdown)
        assert found is not None
        option = found.field("drawdownOption")
        assert option is not None
        assert any(o.label == "Full TFC, Lump Sum Income" for o in option.options)


class TestConfiguredDefaults:
    """The four values the vendor issues in a Word document and the API cannot tell us.

    They had no home: `populate_test_data.py` carried its own copies of all three Create
    Scheme / Create Investment values while the settings the real paths read stayed blank — so
    `sync.py` refused to create a scheme for want of a number written down two directories away.
    """

    def test_the_fixed_fields_are_named(self) -> None:
        assert set(pa._MORGAN_LLOYD_DEFAULTS) == {
            "schemeType",
            "sippType",
            "provider",
            "cam_financialadviser_Account",
            "accountId",
            # Required TOGETHER with the firm: the vendor refuses a contact attached to a firm
            # with no adviser contact ("Financial Adviser contact must be populated").
            "cam_FinancialAdviserContact",
        }

    def test_the_adviser_firm_is_the_same_value_under_both_names(self) -> None:
        """The vendor calls one GUID `cam_financialadviser_Account` on Create Contact and
        `accountId` on Create Adviser. One setting, or they drift."""
        by_field = pa._MORGAN_LLOYD_DEFAULTS
        assert (
            by_field["cam_financialadviser_Account"] == by_field["accountId"] == "adviser_firm_id"
        )

    def test_an_unconfigured_deployment_offers_no_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Blank must stay blank, not fall back to the dev org's numbers. These are FIRM-specific,
        and a wrong scheme pairing is refused by the vendor as one opaque error covering both
        halves."""
        for var in (
            "SCHEME_TYPE",
            "SIPP_TYPE",
            "INVESTMENT_PROVIDER_ID",
            "ADVISER_FIRM_ID",
            "ADVISER_CONTACT_ID",
        ):
            monkeypatch.delenv(f"MORGAN_LLOYD_{var}", raising=False)
        monkeypatch.setattr(
            "morgan_lloyd.settings.MorganLloydSettings.model_config",
            {"env_prefix": "MORGAN_LLOYD_", "extra": "ignore"},
        )
        assert pa.defaults_for("morgan-lloyd") == {}

    def test_another_provider_has_none(self) -> None:
        assert pa.defaults_for("seccl") == {}

    def test_a_default_only_appears_on_an_action_that_takes_the_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`provider` is a Create Investment / Create Valuation field. Offering it on Create
        Contact would be a value the contract does not document, which `validate` then refuses."""
        monkeypatch.setattr(
            pa, "defaults_for", lambda *_a: {"provider": "guid-1", "schemeType": "16"}
        )
        described = {a["key"]: a for a in pa.describe("morgan-lloyd")}
        assert described["create_investment"]["defaults"] == {"provider": "guid-1"}
        assert described["create_scheme"]["defaults"] == {"schemeType": "16"}
        assert described["create_contact"]["defaults"] == {}

    def test_every_default_field_is_reachable_from_an_action(self) -> None:
        """A default keyed on a field no action's CONTRACT offers would silently never appear —
        which is what `FirmId` was: real on the `contacts` PATCH's own parameter table, but
        `update_contact` borrows Create Contact's fields, so nothing ever rendered it."""
        documented: set[str] = set()
        for action in pa.actions_for("morgan-lloyd"):
            found = pa.contract(action)
            if found:
                documented.update(f.name for f in found.fields)
        unknown = set(pa._MORGAN_LLOYD_DEFAULTS) - documented
        assert not unknown, f"defaults for fields no action documents: {unknown}"


class TestThreeVendorsOneContractShape:
    """Each custodian publishes its contract as a different artefact, and none is an OpenAPI spec.

    `platform_contracts` adapts all three to one shape so the rules that matter — an unknown field
    is refused, a missing required one is named, values are typed on the documented type — are
    written once instead of drifting into three per-vendor copies.
    """

    def test_every_action_of_every_provider_has_a_contract(self) -> None:
        for provider in ("morgan-lloyd", "seccl", "sei"):
            described = pa.describe(provider)
            assert described, provider
            undocumented = [a["key"] for a in described if not a["documented"]]
            assert not undocumented, f"{provider}: no usable contract for {undocumented}"

    def test_an_action_carries_its_own_provider(self) -> None:
        """Threading the provider alongside the ActionDef is how one ends up validated against
        one vendor's contract and sent to another."""
        for provider in ("morgan-lloyd", "seccl", "sei"):
            for action in pa.actions_for(provider):
                assert action.provider == provider, action.key

    def test_seccl_reads_its_generated_json_schemas(self) -> None:
        found = pa.action_for("seccl", "create_client")
        assert found is not None and found.contract_ref == "client:ClientCreate"
        names = {f.name for f in pa.contract(found).fields}  # type: ignore[union-attr]
        assert {"firmId", "clientType", "firstName", "surname"} <= names

    def test_seccl_names_the_blocks_a_flat_form_cannot_build(self) -> None:
        """`addressDetail` and `bankDetails` are objects. Dropping them silently would make the
        drawer look complete while omitting half the payload."""
        found = pa.action_for("seccl", "create_client")
        assert found is not None
        nested = set(pa.contract(found).nested)  # type: ignore[union-attr]
        assert {"addressDetail", "bankDetails"} <= nested

    def test_sei_fields_are_dotted_paths_into_its_envelope(self) -> None:
        found = pa.action_for("sei", "change_account_status")
        assert found is not None
        names = [f.name for f in pa.contract(found).fields]  # type: ignore[union-attr]
        assert "data.attributes.accountNumber" in names

    def test_nothing_is_required_for_sei_and_that_is_deliberate(self) -> None:
        """The API sheet has no Required column. An unknown asserted as required blocks
        legitimate partial calls; asserted as optional it merely lets the vendor answer, and the
        vendor's answer names itself."""
        for action in pa.actions_for("sei"):
            found = pa.contract(action)
            assert found is not None
            assert not found.required_fields, action.key

    def test_the_money_rails_are_never_auto_retried(self) -> None:
        """Seccl's transaction group IS the money rail — a retried group is a second
        contribution — and SEI's disbursement pays cash away."""
        for provider, key in (
            ("seccl", "create_transaction_group"),
            ("sei", "create_cash_disbursement"),
            ("sei", "create_cash_transfer"),
        ):
            found = pa.action_for(provider, key)
            assert found is not None and found.retry == "manual", f"{provider}.{key}"

    def test_no_provider_can_guard_a_create_it_does_not_link(self) -> None:
        """`guarded` means an attempt can ask `platform.links` whether it already landed. Only
        Morgan Lloyd records those identifiers, so only its creates may claim it."""
        for provider in ("seccl", "sei"):
            guarded = [a.key for a in pa.actions_for(provider) if a.retry == "guarded"]
            assert not guarded, f"{provider} records no links, so {guarded} cannot be guarded"


class TestSeiEnvelope:
    """SEI wraps every request; the contract's names are paths into that wrapper."""

    def test_a_dotted_payload_is_rebuilt(self) -> None:
        assert pa._nest(
            {"data.type": "string", "data.attributes.firmId": 3106, "data.attributes.x": "y"}
        ) == {"data": {"type": "string", "attributes": {"firmId": 3106, "x": "y"}}}

    def test_a_flat_payload_is_untouched(self) -> None:
        """Not every service wraps — the PME assignments post flat bodies."""
        assert pa._nest({"firmId": 3106, "accountNumber": "1"}) == {
            "firmId": 3106,
            "accountNumber": "1",
        }

    def test_siblings_share_a_branch(self) -> None:
        """Rebuilding each path independently would leave the last one winning and quietly drop
        the rest of the attributes."""
        out = pa._nest({"a.b.c": 1, "a.b.d": 2, "a.e": 3})
        assert out == {"a": {"b": {"c": 1, "d": 2}, "e": 3}}


class TestSecclValidation:
    """`validate` is reused unchanged across the three, so the rules hold for Seccl too."""

    def _create_client(self) -> pa.ActionDef:
        found = pa.action_for("seccl", "create_client")
        assert found is not None
        return found

    def test_a_documented_payload_passes(self) -> None:
        out = pa.validate(
            "seccl", self._create_client(), {"firmId": "SELCT", "clientType": "Individual"}
        )
        assert out == {"firmId": "SELCT", "clientType": "Individual"}

    def test_inferred_requiredness_is_not_enforced(self) -> None:
        """Seccl's schemas infer required from "present in every example", which its own README
        calls decent evidence and not a guarantee. Refusing on that blocks a legitimate call and
        can only be fixed by editing code; letting it through lets the vendor answer."""
        contract = pa.contract(self._create_client())
        assert contract is not None
        assert {f.name for f in contract.required_fields} >= {"currency", "taxDomicile"}
        # ...and yet a payload without them is sent, because we do not know that it is wrong.
        assert pa.validate("seccl", self._create_client(), {"firmId": "SELCT"}) == {
            "firmId": "SELCT"
        }

    def test_morgan_lloyd_still_enforces_what_its_vendor_documents(self) -> None:
        """The distinction: ML's field tables carry a Required column, so a gap there IS known."""
        create = pa.action_for("morgan-lloyd", "create_contact")
        assert create is not None
        with pytest.raises(pa.ActionRefused, match="requires"):
            pa.validate("morgan-lloyd", create, {"firstname": "Ada"})

    def test_an_unknown_field_is_refused(self) -> None:
        with pytest.raises(pa.ActionRefused, match="frstName"):
            pa.validate("seccl", self._create_client(), {"frstName": "Ada"})

    def test_a_nested_block_is_refused_rather_than_half_sent(self) -> None:
        """`addressDetail` is documented but not offered. Accepting a string for it would send
        Seccl a scalar where it expects an object."""
        with pytest.raises(pa.ActionRefused, match="addressDetail"):
            pa.validate("seccl", self._create_client(), {"addressDetail": "1 High St"})


class TestRetryClasses:
    """Whether an operation is safe to send twice — declared on the ACTION, shown to the operator.

    These were written for the outbound delivery queue (`platform.provider_instructions`, 0074),
    which had a worker, backoff, idempotency and no producer at all, and has been removed. The
    classification survived it because it is a fact about the VENDOR rather than about a
    transport, and every way of getting it wrong is silent and expensive:

      * Morgan Lloyd has NO DELETE. A resent `cam_createcontact` puts a second human being in the
        CRM -- the documented `sync-to-morgan-lloyd` scar, where the contact was created every
        time and its id discarded;
      * a resent contribution reclaims relief from HMRC twice.

    So the default must be "do not repeat", and the class must come from the operation rather than
    from whoever is submitting the form.
    """

    def test_every_action_declares_a_known_class(self) -> None:
        for provider in pa.PROVIDER_ACTIONS:
            for action in pa.actions_for(provider):
                assert action.retry in ("safe", "guarded", "manual"), f"{provider}/{action.key}"

    def test_the_default_is_manual(self) -> None:
        """THE RULE THAT MATTERS. An operation added without a decision must not be presented as
        repeatable; the cost of guessing wrong runs one way only."""
        assert pa.ActionDef(key="x", label="X", action="cam_x", group="G").retry == "manual"

    def test_every_patch_is_safe(self) -> None:
        """An update sets absolute values, so sending it twice leaves the same state."""
        for action in pa.actions_for("morgan-lloyd"):
            if action.method == "patch_entity":
                assert action.retry == "safe", action.key

    def test_only_the_linked_creates_are_guarded(self) -> None:
        """`guarded` means "we record an identifier for this on `platform.links`, so the question
        'did it already land?' is answerable". Exactly two creates qualify; claiming a third
        without recording its link would call a create repeatable that we cannot check."""
        guarded = {a.key for a in pa.actions_for("morgan-lloyd") if a.retry == "guarded"}
        assert guarded == {"create_contact", "create_scheme"}

    def test_the_money_instructions_are_manual(self) -> None:
        for key in ("create_contribution", "create_transfer", "drawdown_setup"):
            found = pa.action_for("morgan-lloyd", key)
            assert found is not None and found.retry == "manual", key

    def test_the_class_reaches_the_drawer(self) -> None:
        """It is only useful if the operator sees it before submitting — which is the whole
        reason it outlived the queue it was written for."""
        described = {a["key"]: a for a in pa.describe("morgan-lloyd")}
        assert described["create_contact"]["retry"] == "guarded"
        assert described["create_contribution"]["retry"] == "manual"
