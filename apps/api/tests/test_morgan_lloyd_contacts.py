"""Mapping a Graphene client onto `cam_createcontact`.

The target is a real back-office record, so the important behaviour is refusing to send an
incomplete client rather than substituting placeholders for a National Insurance number or
address. The payload mapping is checked against the vendor's documented option sets.
"""

import pytest

from app.integrations.morgan_lloyd import contacts

COMPLETE = {
    "title": "Miss",
    "first_name": "Sophie",
    "surname": "Halloway",
    "email": "sophie.halloway@example.com",
    "dob": "1954-07-23",
    "nationality": ["GB"],
    "national_identifier": "QQ123456C",
    "address_line1": "1 Example Street",
    "address_city": "Bristol",
    "address_postcode": "BS1 4XE",
    # Required by cam_createcontact; see 0052_contact_gender.
    "gender": "female",
}


class TestMissingFields:
    def test_a_complete_client_has_nothing_missing(self) -> None:
        assert contacts.missing_fields(COMPLETE) == []

    def test_names_the_fields_an_adviser_has_to_fill_in(self) -> None:
        # Exactly Sophie's seeded state: everything except NI number and address.
        partial = {
            k: v
            for k, v in COMPLETE.items()
            if k
            not in {
                "national_identifier",
                "address_line1",
                "address_city",
                "address_postcode",
                "gender",
            }
        }
        assert contacts.missing_fields(partial) == [
            "National Insurance number",
            "address line 1",
            "town or city",
            "postcode",
            "gender",
        ]

    def test_blank_strings_count_as_missing(self) -> None:
        # The CRM stores '' rather than NULL for untouched text columns.
        assert "National Insurance number" in contacts.missing_fields(
            {**COMPLETE, "national_identifier": ""}
        )

    def test_an_empty_nationality_array_counts_as_missing(self) -> None:
        assert "nationality" in contacts.missing_fields({**COMPLETE, "nationality": []})


class TestBuildContact:
    def test_maps_the_documented_required_fields(self) -> None:
        payload = contacts.build_contact(COMPLETE)
        assert payload["firstname"] == "Sophie"
        assert payload["lastname"] == "Halloway"
        assert payload["emailaddress1"] == "sophie.halloway@example.com"
        assert payload["cam_birthdate"] == "1954-07-23"
        assert payload["cam_ninumber"] == "QQ123456C"
        assert payload["address1_postalcode"] == "BS1 4XE"
        assert payload["as_contactstatus"] == 809130002  # ContactStatus.CUSTOMER

    def test_title_maps_onto_the_option_set(self) -> None:
        assert contacts.build_contact({**COMPLETE, "title": "Miss"})["cam_title"] == 809130003
        assert contacts.build_contact({**COMPLETE, "title": "mr"})["cam_title"] == 809130000
        assert contacts.build_contact({**COMPLETE, "title": "Dr."})["cam_title"] == 809130004

    def test_an_unknown_title_falls_back_to_other(self) -> None:
        assert contacts.build_contact({**COMPLETE, "title": "Wg Cdr"})["cam_title"] == 809130012

    def test_nationality_array_is_flattened_to_the_first_value(self) -> None:
        assert contacts.build_contact(COMPLETE)["cam_nationality"] == "GB"

    def test_vulnerability_flag_is_the_string_false_not_a_boolean(self) -> None:
        # Vendor gotcha: a real boolean makes the dependent vulnerability fields required.
        value = contacts.build_contact(COMPLETE)["cam_potentiallyvulnerable"]
        assert value == "False"
        assert isinstance(value, str)

    def test_gender_is_omitted_when_unknown_rather_than_guessed(self) -> None:
        """The intent still holds — never infer a gender from a title — but omission is no
        longer a viable end state: `cam_createcontact` answers 400 without `gendercode`, so an
        absent one is caught by `missing_fields` as a 422 before the call is made."""
        without = {k: v for k, v in COMPLETE.items() if k != "gender"}
        assert "gendercode" not in contacts.build_contact(without)
        assert "gender" in contacts.missing_fields(without)
        assert contacts.build_contact({**COMPLETE, "gender": "female"})["gendercode"] == 2


@pytest.mark.asyncio
async def test_an_incomplete_client_is_refused_before_any_vendor_call() -> None:
    with pytest.raises(ValueError, match="National Insurance number"):
        await contacts.create_contact({**COMPLETE, "national_identifier": None})


# --- the action envelope, and the field that made every push fail ---------------------


def test_gender_is_a_declared_required_field() -> None:
    """`cam_createcontact` rejects a contact without `gendercode` (400 0x80040224). It was
    absent from REQUIRED_FIELDS and from the endpoint's SELECT, so every push failed at the
    vendor rather than returning a 422 naming it, and no contact was ever linked."""
    assert "gender" in {key for key, _ in contacts.REQUIRED_FIELDS}
    assert "gender" in contacts.missing_fields({"first_name": "Peter"})


def test_gender_dynamics_has_no_word_for_is_refused_not_dropped() -> None:
    """Their option set is exactly {1 Male, 2 Female}. Silently omitting an unmappable value
    would resurrect the 400; coercing it would write a guess about someone's gender onto a
    permanent back-office record."""
    client = {
        "first_name": "Sam",
        "surname": "Vimes",
        "email": "s@example.com",
        "dob": "1970-01-01",
        "national_identifier": "AA000000A",
        "nationality": "GB",
        "address_line1": "1 Pseudopolis Yard",
        "address_city": "Ankh-Morpork",
        "address_postcode": "AM1 1AA",
        "gender": "non_binary",
    }
    with pytest.raises(ValueError, match="admits only male or female"):
        contacts.build_contact(client)


def test_gender_maps_onto_the_option_set() -> None:
    base = {
        "first_name": "Peter",
        "surname": "Parker",
        "email": "p@example.com",
        "dob": "2001-05-25",
        "national_identifier": "CC999999A",
        "nationality": ["GB"],
        "address_line1": "20 Ingram Street",
        "address_city": "Stroud",
        "address_postcode": "GL5 1EP",
    }
    assert contacts.build_contact({**base, "gender": "male"})["gendercode"] == 1
    assert contacts.build_contact({**base, "gender": "female"})["gendercode"] == 2


@pytest.mark.parametrize(
    "envelope",
    [
        {"contactid": "b824e120-de8e-f111-8077-6045bd0b13a1"},
        # What the action actually returns: the id is nested under `response`, not top level.
        {
            "@odata.context": "...",
            "message": "ok",
            "response": "b824e120-de8e-f111-8077-6045bd0b13a1",
        },
        {"message": "ok", "response": {"contactId": "b824e120-de8e-f111-8077-6045bd0b13a1"}},
        {"message": "Contact b824e120-de8e-f111-8077-6045bd0b13a1 created", "response": 1},
    ],
)
def test_created_id_reads_the_action_envelope(envelope: dict[str, object]) -> None:
    """The custom actions do not answer like the OData entity endpoints. Looking only for a
    top-level `contactid` threw away a contact that HAD been created and recorded no link —
    the worst of the three outcomes, because the next attempt duplicates the person."""
    assert contacts._created_id(envelope) == "b824e120-de8e-f111-8077-6045bd0b13a1"


def test_created_id_returns_none_when_there_is_genuinely_no_id() -> None:
    """A missing id must stay loud: recording an empty link would break drawdown later, far
    from the cause."""
    assert contacts._created_id({"message": "failed", "responseKey": 1}) is None
