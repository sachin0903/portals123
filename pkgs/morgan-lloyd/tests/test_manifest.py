"""The vendor manifest, and its reconciliation with the hand-written option sets.

`vendor_manifest.json` is a machine transcription of the vendor's documentation; `enums.py` is a
hand transcription of the SAME page, made earlier. Two transcriptions of one source is exactly
the arrangement that drifts, so the point of this file is to keep them honest: the manifest owns
the FIELD LISTS (which enums.py never held) and enums.py owns the VALUES, and where both speak
they must agree.

A mismatch here means one of three things, all worth stopping for: the vendor changed the
contract, the parser mis-read a table, or a hand-typed enum has a typo in it.
"""

from __future__ import annotations

from morgan_lloyd import enums
from morgan_lloyd.manifest import ACTION_ALIASES, Option, load, operation, operations


class TestTranscription:
    def test_the_page_parsed_into_sections_and_operations(self) -> None:
        manifest = load()
        titles = [s["title"] for s in manifest["sections"]]
        # The entity families the admin console is built around. Named individually rather than
        # counted: a count passes when the parser silently swaps one section for another.
        for expected in (
            "Contacts",
            "Financial Advisers",
            "Schemes",
            "Beneficiaries",
            "Transfers",
            "Pension Contributions",
            "Investments",
            "Financial Transactions",
            "Benefits",
            "Documents",
            "Illustrations",
        ):
            assert expected in titles

    def test_every_operation_with_fields_names_an_action(self) -> None:
        """A field list with no action is unusable — and it is how a `plaintext`-tagged code
        block silently produced an operation nobody could call (Create Investment)."""
        for op in operations():
            assert op.action, f"{op.title} has fields but no action"

    def test_actions_keep_their_documented_case(self) -> None:
        """Dynamics action names are case sensitive; `cam_CreateFinancialAdviserContact` is the
        one mixed-case action in the org, and lower-casing it produces a 404."""
        assert operation("cam_CreateFinancialAdviserContact") is not None
        assert operation("cam_createfinancialadvisercontact") is None


class TestCreateContact:
    """Pinned in detail because it is the operation the whole console was specified against,
    and because its page is the one shape every other field table follows."""

    def test_the_documented_contract(self) -> None:
        op = operation("cam_createcontact")
        assert op is not None
        assert op.method == "POST"
        assert op.section == "Contacts"

    def test_required_fields_match_the_page(self) -> None:
        op = operation("cam_createcontact")
        assert op is not None
        assert {f.name for f in op.required_fields} == {
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
        }

    def test_conditionally_required_fields_are_not_flattened_to_optional(self) -> None:
        """`No*` and `No**` are conditions the page states in prose. Treating them as plain
        optional builds payloads the vendor rejects; treating them as required blocks calls that
        are legitimate. Both spellings survive."""
        op = operation("cam_createcontact")
        assert op is not None
        adviser = op.field("cam_FinancialAdviserContact")
        assert adviser is not None and adviser.conditional and not adviser.required
        drivers = op.field("cam_vulnerabilitydrivers")
        assert drivers is not None and drivers.conditional and not drivers.required

    def test_types_are_carried_through(self) -> None:
        op = operation("cam_createcontact")
        assert op is not None
        assert op.field("cam_annualearnings").type == "decimal"  # type: ignore[union-attr]
        assert op.field("cam_retirementageint").type == "int"  # type: ignore[union-attr]
        assert op.field("cam_FinancialAdviserContact").type == "lookup"  # type: ignore[union-attr]

    def test_the_tax_relief_lookup_is_captured(self) -> None:
        """Its four values are documented bare, with the text in a separate table — so the
        options carry no labels and the lookup is the only place the meanings exist."""
        op = operation("cam_createcontact")
        assert op is not None
        relief = op.field("cam_taxrelief")
        assert relief is not None
        assert [o.value for o in relief.options] == [809130000, 809130001, 809130002, 809130003]
        assert all(o.label is None for o in relief.options)
        assert op.lookups and "809130000" in op.lookups[0]


class TestOptionSetsAgreeWithEnums:
    """The reconciliation. Manifest values must be a subset of the hand-written enum.

    SUBSET, not equality, and the direction matters: `enums.py` legitimately holds members the
    request tables do not — `Title.MAJOR` appears only in the Get Adviser Contacts RESPONSE set,
    and `ResponseKey` is not a request field at all. What must never happen is the manifest
    carrying a value the enum lacks, because that is a value a form would offer and the typed
    client could not express.
    """

    def _assert_subset(self, action: str, field: str, enum: type) -> None:
        op = operation(action)
        assert op is not None, f"{action} is not in the manifest"
        found = op.field(field)
        assert found is not None, f"{action}.{field} is not in the manifest"
        assert found.options, f"{action}.{field} parsed no options"
        known = {int(member) for member in enum}
        unknown = {o.value for o in found.options} - known
        assert not unknown, f"{field} documents {unknown}, absent from {enum.__name__}"

    def test_title(self) -> None:
        self._assert_subset("cam_createcontact", "cam_title", enums.Title)

    def test_gender(self) -> None:
        self._assert_subset("cam_createcontact", "gendercode", enums.Gender)

    def test_family_status(self) -> None:
        self._assert_subset("cam_createcontact", "familystatuscode", enums.FamilyStatus)

    def test_employment_status(self) -> None:
        self._assert_subset(
            "cam_createcontact", "cam_employmentstatus", enums.ContactEmploymentStatus
        )

    def test_source_of_wealth(self) -> None:
        self._assert_subset("cam_createcontact", "cam_sourceofwealth", enums.SourceOfWealth)

    def test_tax_relief(self) -> None:
        self._assert_subset("cam_createcontact", "cam_taxrelief", enums.TaxRelief)

    def test_contact_status(self) -> None:
        self._assert_subset("cam_createcontact", "as_contactstatus", enums.ContactStatus)

    def test_vulnerability_drivers_are_the_fca_four(self) -> None:
        """FG21/1's four drivers, which `crm.contacts.vulnerability_drivers` mirrors."""
        op = operation("cam_createcontact")
        assert op is not None
        drivers = op.field("cam_vulnerabilitydrivers")
        assert drivers is not None
        assert [(o.label, o.value) for o in drivers.options] == [
            ("Health", 809130000),
            ("Life Events", 809130001),
            ("Resilience", 809130002),
            ("Capability", 809130003),
        ]

    def test_vulnerability_characteristics_are_complete(self) -> None:
        """All 24, and this is the case the parser is most likely to get wrong: two labels
        contain parentheses and one contains four commas, so both a `[^)]*` match and a split on
        ',' truncate the set — into a dropdown that looks valid and is missing half its choices.
        """
        op = operation("cam_createcontact")
        assert op is not None
        characteristics = op.field("cam_vulnerabilitycharacteristics")
        assert characteristics is not None
        assert len(characteristics.options) == 24
        assert {o.value for o in characteristics.options} == set(range(809130000, 809130024))
        labels = {o.value: o.label for o in characteristics.options}
        assert labels[809130010] == "Domestic abuse (including economic control)"
        assert labels[809130013] == "Inadequate (outgoings exceed income) or erratic income"
        assert labels[809130012] is not None and labels[809130012].startswith(
            "Other circumstances that affect people"
        )
        assert "human trafficking or modern slavery" in labels[809130012]


class TestActionAliases:
    """The catalogue's names vs the client's. The client's are the verified ones."""

    def test_every_alias_resolves_to_a_real_operation(self) -> None:
        for documented, actual in ACTION_ALIASES.items():
            assert operation(documented) is not None, f"{documented} -> {actual} resolves nowhere"

    def test_no_alias_shadows_a_real_action(self) -> None:
        """If a documented name is ALSO a live action, the live one must win — an alias that
        silently redirected a real call would be the worst kind of wrong."""
        live = {op.action for op in operations()}
        for documented in ACTION_ALIASES:
            if documented in live:
                assert operation(documented).action == documented  # type: ignore[union-attr]

    def test_create_investment_is_create_wrapper(self) -> None:
        """There is no `cam_createinvestment` in the org — $metadata established that, and the
        page confirms it by POSTing cam_createwrapper under the heading "Create Investment"."""
        assert operation("cam_createinvestment").action == "cam_createwrapper"  # type: ignore[union-attr]


class TestTheConsoleActions:
    """Every action the admin console offers must have a documented field contract, or its form
    would have to be hand-written and would drift from the vendor the day it changed."""

    ACTIONS = (
        "cam_createcontact",
        "cam_CreateFinancialAdviserContact",
        "cam_createscheme",
        "cam_createbeneficiaries",
        "cam_createtransfer",
        "cam_createcontribution",
        "cam_updatecontribution",
        "cam_createwrapper",
        "cam_createpolicyvaluation",
        "cam_setmemberdetails",
        "cam_drawdownsetup",
        "cam_illustration",
        "cam_submitdocument",
    )

    def test_each_has_fields(self) -> None:
        for action in self.ACTIONS:
            op = operation(action)
            assert op is not None, f"{action} is not documented"
            assert op.fields, f"{action} has no documented fields"


class TestOptionSetsThePageIntroducesLoosely:
    """The vendor names an option set five different ways, and naming only two of them cost
    eleven sets — ten of them on `cam_drawdownsetup`.

    A field with no parsed options renders as a FREE TEXT BOX for a closed set, and
    `platform_actions.validate` can only check a set it has. So the whole drawdown instruction —
    the one action here that moves a client's money — was typed by hand, unchecked on both sides.
    These pin the phrasings found in the page, values verified against it member by member.
    """

    def test_a_bare_values_introducer_is_read(self) -> None:
        """`drawdownOption` says "Values: (…)" with no qualifier."""
        op = operation("cam_drawdownsetup")
        assert op is not None
        field = op.field("drawdownOption")
        assert field is not None
        assert len(field.options) == 30
        assert field.options[0] == Option(label="Full TFC", value=2)
        # THE ONE THAT MATTERS: four labels contain their own comma. Splitting the description on
        # ',' would shred this into three bogus members.
        assert Option(label="Partial TFC, Lump Sum Income, Regular Income", value=23) in (
            field.options
        )

    def test_an_accepted_values_introducer_is_read(self) -> None:
        op = operation("cam_drawdownsetup")
        assert op is not None
        field = op.field("incomeFrequency")
        assert field is not None
        # Payments per annum, so the VALUE is the count and 3 is absent by design.
        assert {o.value for o in field.options} == {1, 2, 4, 6, 12}

    def test_a_doubled_introducer_is_read(self) -> None:
        """One field says "Possible Values: Values (…)". The anchor is the `values` immediately
        before the paren, which is why this parses at all."""
        op = operation("cam_createupdateliability")
        assert op is not None
        field = op.field("cam_liabilitytype")
        assert field is not None
        assert [o.label for o in field.options] == [
            "Mortgage",
            "Credit Card",
            "Loan",
            "Car Finance",
            "Other",
        ]

    def test_bare_numbers_keep_a_null_label(self) -> None:
        """`paymentDay` lists "( 6 , 14 , 21 , 28 )" — days of the month, no labels. Inventing
        one is the thing this file must not do."""
        op = operation("cam_drawdownsetup")
        assert op is not None
        field = op.field("paymentDay")
        assert field is not None
        assert field.options == (
            Option(label=None, value=6),
            Option(label=None, value=14),
            Option(label=None, value=21),
            Option(label=None, value=28),
        )

    def test_prose_without_a_values_keyword_is_not_an_option_set(self) -> None:
        """The guard against the loosened regex. "must be between 20 and 110" is a bound, not a
        member list, and reading it as one would offer a dropdown of two nonsense choices."""
        op = operation("cam_createcontact")
        assert op is not None
        for name in ("cam_retirementageint", "cam_annualearnings", "firstname"):
            field = op.field(name)
            assert field is not None
            assert field.options == (), f"{name} should have no option set"


class TestMultiselect:
    """A multiselect is several members at once, sent as ONE comma-separated string.

    It cannot be inferred from the type — the vendor types these `string`, exactly like a
    free-text name — so the flag comes from the page's own wording. It matters because the FCA's
    four vulnerability drivers COMPOUND: a control offering one member cannot record a client in
    poor health who has also just been bereaved, and nothing errors.
    """

    def test_the_vulnerability_fields_are_marked(self) -> None:
        op = operation("cam_createcontact")
        assert op is not None
        for name in ("cam_vulnerabilitydrivers", "cam_vulnerabilitycharacteristics"):
            field = op.field(name)
            assert field is not None
            assert field.multiselect, f"{name} is documented as a multiselect"
            assert field.options, f"{name} must carry the members to choose from"

    def test_the_characteristics_set_is_not_truncated(self) -> None:
        """24, not 11: two labels contain their own parentheses, and a non-greedy match to the
        first `)` silently halved the set."""
        op = operation("cam_createcontact")
        assert op is not None
        field = op.field("cam_vulnerabilitycharacteristics")
        assert field is not None
        assert len(field.options) == 24

    def test_a_single_valued_option_set_is_not_marked(self) -> None:
        """`cam_title` is one member, and treating it as a multiselect would let a contact be
        created as both Mr and Dr."""
        op = operation("cam_createcontact")
        assert op is not None
        for name in ("cam_title", "gendercode", "as_contactstatus"):
            field = op.field(name)
            assert field is not None
            assert not field.multiselect, f"{name} takes one value"

    def test_the_free_text_boolean_beside_them_is_neither(self) -> None:
        """`cam_potentiallyvulnerable` sits between the two multiselects and is a plain string
        "True"/"False" — no options, no flag."""
        op = operation("cam_createcontact")
        assert op is not None
        field = op.field("cam_potentiallyvulnerable")
        assert field is not None
        assert field.options == ()
        assert not field.multiselect
        assert field.type == "string"


class TestTableClassification:
    """Which table on a page is the request body — the question three heuristics got wrong.

    Reading ORDER fails: every entity PATCH leads with a URL Parameters table, so "the first
    field table" put `GUID` in `fields` and pushed the real contract into `response_fields`.
    Update Transfer then offered ONE field called GUID instead of the three the vendor documents.

    The `Required` COLUMN fails too: 54 request tables have one and three do not (Update
    Beneficiary, and the token call's Headers and Authentication), so keying on it silently
    emptied those.

    The HEADING is the document's own answer and covers all 63.
    """

    def test_a_patch_offers_its_documented_fields_not_its_url_parameter(self) -> None:
        """The case the vendor's own docs make plainest: three fields, all required."""
        op = operation("cam_personalpolicies", "PATCH")
        assert op is not None
        assert [f.name for f in op.fields] == [
            "cam_transferdate",
            "cam_transferamount",
            "cam_fundnotes",
        ]
        assert all(f.required for f in op.fields)

    def test_the_record_id_is_a_parameter_not_a_field(self) -> None:
        """It IS part of the contract — just of the path, not the payload. A form that posted it
        as a body field would send the vendor a field it does not accept."""
        op = operation("cam_personalpolicies", "PATCH")
        assert op is not None
        assert [f.name for f in op.parameters] == ["GUID"]
        assert "GUID" not in {f.name for f in op.fields}

    def test_a_request_table_without_a_required_column_is_still_a_request(self) -> None:
        """Update Beneficiary's one field has no Required column. Keying on that column dropped
        it, and the console covered the hole by borrowing Create Beneficiary's twelve fields —
        eleven of which that endpoint is not documented to take."""
        op = operation("cam_nominationofbeneficiaries", "PATCH")
        assert op is not None
        assert [f.name for f in op.fields] == ["cam_beneficiarystatus"]

    def test_response_fields_stay_out_of_the_request(self) -> None:
        op = operation("cam_createscheme")
        assert op is not None
        assert "schemeReference" not in {f.name for f in op.fields}
        assert "schemeReference" in {f.name for f in op.response_fields}

    def test_a_nested_request_object_is_kept_not_dropped(self) -> None:
        """`policyValuation` is a JSON string whose shape the page documents in its own tables.
        A form cannot render it, but losing it silently means rediscovering it by hand."""
        op = operation("cam_createpolicyvaluation")
        assert op is not None
        assert {f.name for f in op.fields} == {"provider", "policyValuation"}


class TestOperationLookup:
    """An entity set names SEVERAL operations, and the first is rarely the one you want."""

    def test_the_method_picks_the_right_operation(self) -> None:
        """`contacts` is Get All Contacts, Get Individual Contact, Update Contact AND Get
        Financial Adviser Contacts. Without the method the lookup returned a GET — which has no
        request fields — so a PATCH looked like an operation with no documented contract."""
        assert operation("contacts", "PATCH").method == "PATCH"  # type: ignore[union-attr]
        assert operation("cam_nominationofbeneficiaries", "PATCH").method == "PATCH"  # type: ignore[union-attr]

    def test_a_unique_action_needs_no_method(self) -> None:
        assert operation("cam_createcontact").action == "cam_createcontact"  # type: ignore[union-attr]

    def test_an_unmatched_method_falls_back_rather_than_vanishing(self) -> None:
        """Better the wrong-method operation than None: the caller gets a contract to show, and
        every pre-existing call site keeps working."""
        assert operation("cam_createcontact", "DELETE") is not None

    def test_an_alias_still_resolves(self) -> None:
        assert operation("cam_createinvestment", "POST").action == "cam_createwrapper"  # type: ignore[union-attr]
