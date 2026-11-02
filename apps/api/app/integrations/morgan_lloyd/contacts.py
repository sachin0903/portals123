"""Push a Graphene client into Morgan Lloyd as a Dynamics Contact.

A drawdown can only be started for someone Morgan Lloyd already knows: ``cam_setmemberdetails``
takes a ``contactId``, which is a Dynamics GUID. So a client has to exist there first, and the
GUID has to be recorded locally (``platform.links``) so we can find it again.

``cam_createcontact`` has a hard required-field list. This module validates against it and
reports exactly what is missing rather than substituting placeholders: the target is a real
back-office system, and inventing a National Insurance number or address would put false
personal data on a client's permanent record.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import httpx
from morgan_lloyd import MorganLloydError
from morgan_lloyd.enums import (
    ContactEmploymentStatus,
    ContactStatus,
    FamilyStatus,
    Gender,
    SourceOfWealth,
    TaxRelief,
    Title,
)

from app.contacts import normalise_phone
from app.integrations.morgan_lloyd.benefits import (
    MorganLloydUnavailable,
    _explain,
    _settings,
    configured,
    unconfigured,
)

# OPTION-SET VALUES ARE IMPORTED, NOT COPIED. `morgan_lloyd.enums` is the vendored,
# manifest-pinned vocabulary and is what gets rebuilt when the vendor renumbers; every map below
# used to carry its own integers under a comment NAMING the enum it should have imported. Dynamics
# accepts any valid option-set integer, so a stale copy is a 200 that files the client under the
# wrong answer — and `test_morgan_lloyd_contact_contract.py` reads the manifest, not a second copy
# of the numbers, so it could not see a drift between the two lists of literals either.
#
# Two fields have NO enum member and keep their literals: `cam_vulnerabilitydrivers` and
# `cam_vulnerabilitycharacteristics`. `ContactVulnerability` is a different field
# (`cam_contactvulnerability`, seven members) and mapping onto it would be wrong.

# Only Customer is documented for as_contactstatus.
_CONTACT_STATUS_CUSTOMER = int(ContactStatus.CUSTOMER)
_TITLES = {
    "MR": int(Title.MR),
    "MS": int(Title.MS),
    "MRS": int(Title.MRS),
    "MISS": int(Title.MISS),
    "DR": int(Title.DR),
    "MASTER": int(Title.MASTER),
    "CAPT": int(Title.CAPT),
    "PROF": int(Title.PROF),
    "REVEREND": int(Title.REVEREND),
    "SIR": int(Title.SIR),
    "LORD": int(Title.LORD),
    "LADY": int(Title.LADY),
}
_TITLE_OTHER = int(Title.OTHER)
# Dynamics' option set on this org is exactly {1 Male, 2 Female} -- verified against
# EntityDefinitions(LogicalName='contact')/Attributes(LogicalName='gendercode'). There is no
# "unspecified" member, so a client recorded as anything else cannot be pushed; that is a
# limitation of the counterparty's data model and is reported as such rather than coerced.
_GENDERS = {"male": int(Gender.MALE), "female": int(Gender.FEMALE)}

# Dynamics `familystatuscode`, verified against the org's own option set. Ours is the superset
# (0053), so an unmappable value is dropped rather than coerced -- it is optional there.
_FAMILY_STATUS = {
    "single": int(FamilyStatus.SINGLE),
    "married": int(FamilyStatus.MARRIED),
    "civil_partner": int(FamilyStatus.CIVIL_PARTNER),
    "divorced": int(FamilyStatus.DIVORCED),
    "cohabiting": int(FamilyStatus.COHABITING),
    "widowed": int(FamilyStatus.WIDOWED),
}

# `cam_employmentstatus` (morgan_lloyd.enums.ContactEmploymentStatus).
#
# 0053 DELIBERATELY DID NOT MAP THIS, on the stated grounds that "despite the name it is an
# OCCUPATIONAL option set on their side (Business Owner, C-Suite, Doctors...)". The vendored
# manifest contradicts that: `cam_createcontact.cam_employmentstatus` documents exactly
# {Employed, Self Employed, Unemployed, Student, Child, Carer, Child Carer, Pensioner, Other},
# and the words "Business Owner" and "C-Suite" appear nowhere in `vendor_manifest.json` or in
# `morgan_lloyd.enums`. Whatever that earlier note was describing, it is not this field -- so the
# refusal was costing us a field the vendor asks for and we hold, and every one of our eight
# CHECK values (0053) has a member here.
#
# THEIRS IS THE SUPERSET (`Child` has no counterpart of ours), so this is a total map from our
# side; an unmappable value is dropped rather than coerced onto Other, which would assert
# something about a client's working life we were not told.
#
# THE INTEGERS ARE IMPORTED, NOT COPIED (`morgan_lloyd.enums`, the vendored manifest-pinned
# vocabulary). What is ours is the KEY — our column's value — and the choice of which of their
# members it maps to; the value on the right is theirs, and theirs to renumber.
_EMPLOYMENT_STATUS = {
    "employed": int(ContactEmploymentStatus.EMPLOYED),
    "self_employed": int(ContactEmploymentStatus.SELF_EMPLOYED),
    "unemployed": int(ContactEmploymentStatus.UNEMPLOYED),
    "education": int(ContactEmploymentStatus.STUDENT),
    "care_over_16": int(ContactEmploymentStatus.CARER),
    "care_under_16s": int(ContactEmploymentStatus.CHILD_CARER),
    "retired": int(ContactEmploymentStatus.PENSIONER),
    "other": int(ContactEmploymentStatus.OTHER),
}

# `cam_sourceofwealth` (morgan_lloyd.enums.SourceOfWealth). The record offers their twelve-member
# list as a picker so the INT is sent on an exact label match; `cam_sourceofwealthstring` carries
# the client's supporting statement (`source_of_wealth_detail`), with the label as a legacy
# fallback.
# A historical free-text value still goes out as the string with no INT (0053's growth argument).
# Keyed on the vendor's own labels, lowercased -- including their misspelling of "Inheritence",
# which is what the option set says and therefore what a value copied from their form will read.
#
# The keys are OURS — a normalisation of free text, which is why "inheritence" and "inheritance"
# both appear — and every value is the enum's. Not derived from the member NAMES, deliberately:
# `LOTTERY_BETTING_CASINO` de-underscores to "lottery betting casino" and the label a value is
# copied from reads "Lottery/Betting/Casino", so a derivation would stop matching the one thing
# these keys exist to match.
_SOURCE_OF_WEALTH = {
    "salary": int(SourceOfWealth.SALARY),
    "variable remuneration": int(SourceOfWealth.VARIABLE_REMUNERATION),
    "investment income or sale of investments": int(
        SourceOfWealth.INVESTMENT_INCOME_OR_SALE_OF_INVESTMENTS
    ),
    "pension income": int(SourceOfWealth.PENSION_INCOME),
    "sale of main residence": int(SourceOfWealth.SALE_OF_MAIN_RESIDENCE),
    "sale of other property": int(SourceOfWealth.SALE_OF_OTHER_PROPERTY),
    "business interests": int(SourceOfWealth.BUSINESS_INTERESTS),
    "inheritence": int(SourceOfWealth.INHERITANCE),
    "inheritance": int(SourceOfWealth.INHERITANCE),
    "gift": int(SourceOfWealth.GIFT),
    "financial settlements": int(SourceOfWealth.FINANCIAL_SETTLEMENTS),
    "lottery/betting/casino": int(SourceOfWealth.LOTTERY_BETTING_CASINO),
    "other": int(SourceOfWealth.OTHER),
}

# `cam_vulnerabilitydrivers` -- A MULTISELECT, sent as a comma-separated string of its integers.
#
# EXACTLY THE FCA'S FOUR (FG21/1), which is why this is a mapping and not a translation:
# `crm.contacts.vulnerability_reasons` (0054) chose the regulator's vocabulary, and so did Morgan
# Lloyd. An array on both sides because the drivers compound.
_VULNERABILITY_DRIVERS = {
    "health": 809130000,
    "life_event": 809130001,
    "resilience": 809130002,
    "capability": 809130003,
}

# `cam_vulnerabilitycharacteristics` -- the 24 FG21/1 characteristics beneath those drivers, and
# the other half of the `No**` detail the flag makes required (0116 gave them a column).
#
# NOTE 809130023, WHICH IS NOT IN SEQUENCE. Visual Impairment sits between Hearing impairment and
# Mental health in the vendor's LIST -- FG21/1's own table order -- while carrying the highest
# number in the set, so it was clearly added later. Deriving these values from list position would
# be wrong for that one member and right for the other 23, which is the shape of bug that gets
# found by a client with a visual impairment being recorded as something else.
_VULNERABILITY_CHARACTERISTICS = {
    # health
    "physical_disability": 809130000,
    "long_term_illness": 809130001,
    "hearing_impairment": 809130002,
    "visual_impairment": 809130023,
    "mental_health": 809130003,
    "addiction": 809130004,
    "low_mental_capacity": 809130005,
    # life events
    "retirement": 809130006,
    "bereavement": 809130007,
    "income_shock": 809130008,
    "relationship_breakdown": 809130009,
    "domestic_abuse": 809130010,
    "caring_responsibilities": 809130011,
    "other_circumstances": 809130012,
    # resilience
    "erratic_income": 809130013,
    "over_indebtedness": 809130014,
    "low_savings": 809130015,
    "low_emotional_resilience": 809130016,
    # capability
    "low_financial_knowledge": 809130017,
    "poor_literacy_numeracy": 809130018,
    "poor_english": 809130019,
    "poor_digital_skills": 809130020,
    "learning_difficulties": 809130021,
    "no_access_to_support": 809130022,
}

# `cam_taxrelief` (morgan_lloyd.enums.TaxRelief). The member's own DECLARATION, which 0116 gave a
# column so it could be answered instead of inferred: the previous note here refused to derive it
# from the presence of `relevant_uk_earnings`, and that refusal stands -- this maps an ANSWER.
#
# Our four column values are the vendor's four members one-for-one — 0116 chose that deliberately,
# so the map is a rename and the integers are imported.
_TAX_RELIEF = {
    "uk_relevant_earnings": int(TaxRelief.UK_RELEVANT_EARNINGS),
    "uk_resident_this_year": int(TaxRelief.UK_RESIDENT_THIS_YEAR),
    "overseas_crown_employment_no_uk_earnings": int(
        TaxRelief.OVERSEAS_CROWN_EMPLOYMENT_NO_UK_EARNINGS
    ),
    "spouse_overseas_crown_employment": int(TaxRelief.SPOUSE_OVERSEAS_CROWN_EMPLOYMENT),
}

# `cam_createcontact` required fields -> the Graphene column that supplies each, so the error
# names what an adviser has to go and fill in.
REQUIRED_FIELDS: tuple[tuple[str, str], ...] = (
    ("first_name", "first name"),
    ("surname", "surname"),
    ("email", "email address"),
    ("dob", "date of birth"),
    ("national_identifier", "National Insurance number"),
    ("nationality", "nationality"),
    ("address_line1", "address line 1"),
    ("address_city", "town or city"),
    ("address_postcode", "postcode"),
    # `cam_createcontact` rejects a contact without this (400 0x80040224). It was absent from
    # this list AND from the endpoint's SELECT, so every push failed at the vendor instead of
    # returning a 422 naming the field -- zero contacts were ever created. See 0052.
    ("gender", "gender"),
)


def missing_fields(client: dict[str, Any]) -> list[str]:
    """Human-readable names of the required fields this client hasn't got."""
    return [label for key, label in REQUIRED_FIELDS if not client.get(key)]


def client_columns(alias: str = "c") -> str:
    """The `crm.contacts` projection :func:`build_contact` reads, as SQL.

    ONE PROJECTION, TWO CALLERS, for the reason `contacts.phone_expr` gives about itself: the
    push endpoint and `sync.py` were maintaining separate hand-written SELECTs of the same field
    set, and a field missing from one of them is invisible — `build_contact` reads a dict with
    `.get`, so an unselected column is indistinguishable from a client who has not answered.
    That is exactly how `gender` came to be absent from the endpoint's SELECT while sitting in
    `REQUIRED_FIELDS` (0052), and how `vulnerability_reasons` would have been absent from both
    the moment this mapper started reading it.

    So a field added here reaches every caller at once, and the mapper's inputs are declared
    beside the mapper rather than in the routers.
    """
    from app.contacts import phone_expr

    return ", ".join(
        (
            f"{alias}.id",
            f"{alias}.uid",
            f"{alias}.title",
            f"{alias}.first_name",
            f"{alias}.middle_name",
            f"{alias}.surname",
            f"{alias}.email",
            f"{alias}.dob",
            f"{alias}.nationality",
            f"{alias}.national_identifier",
            f"{alias}.gender",
            # The provider-object guard: three providers write this text column and one of them
            # wrote a whole JSON object into it, which then travelled into this very payload.
            f"{phone_expr(f'{alias}.mobile')} AS mobile",
            f"{phone_expr(f'{alias}.phone')} AS phone",
            f"{alias}.marital_status",
            f"{alias}.retirement_age",
            f"{alias}.employment_status",
            f"{alias}.occupation",
            f"{alias}.source_of_funds",
            f"{alias}.source_of_wealth",
            f"{alias}.source_of_wealth_detail",
            f"{alias}.relevant_uk_earnings",
            f"{alias}.flexi_access_taken",
            f"{alias}.flexi_access_date",
            f"{alias}.pension_protection",
            f"{alias}.is_vulnerable",
            f"{alias}.vulnerability_reasons",
            f"{alias}.vulnerability_characteristics",
            f"{alias}.vulnerability_notes",
            f"{alias}.tax_relief",
            f"{alias}.employer_name",
            f"{alias}.employer_registration_number",
            f"{alias}.employer_address",
            f"{alias}.employer_contributions",
            f"{alias}.address->>'line1' AS address_line1",
            f"{alias}.address->>'line2' AS address_line2",
            f"{alias}.address->>'city' AS address_city",
            f"{alias}.address->>'county' AS address_county",
            f"{alias}.address->>'postcode' AS address_postcode",
        )
    )


def _title_code(title: str | None) -> int:
    """Map a free-text title onto Dynamics' option set, falling back to Other."""
    return _TITLES.get((title or "").strip().upper().rstrip("."), _TITLE_OTHER)


def _key(client: dict[str, Any], column: str) -> str:
    """One of our own text values, normalised for an option-set lookup.

    A miss returns a key no map holds, so the field is omitted -- never coerced onto the set's
    `Other` member, which would assert something about the client we were not told.
    """
    return str(client.get(column) or "").strip().lower()


def build_contact(client: dict[str, Any]) -> dict[str, Any]:
    """Map a Graphene client onto a ``cam_createcontact`` payload.

    ``cam_potentiallyvulnerable`` is the STRING "False", not a boolean — the vendor's own note.
    Sending it as False would make the dependent vulnerability fields required.

    EVERY FIELD HERE IS ONE THE VENDOR DOCUMENTS, IN THE TYPE THEY DOCUMENT IT IN, and that is a
    tripwire rather than a convention (`tests/test_morgan_lloyd_contact_contract.py`). Dynamics
    ignores a field it does not recognise, so a wrong name is a silent no-op that reports success
    — which is how `cam_additionalvulnerabilitynotes` discarded every vulnerable client's notes
    — and it is typed, so a JSON bool against an `int` field is a live-call 400 that no unit test
    on this function's output would otherwise see.
    """
    nationality = client.get("nationality")
    if isinstance(nationality, list | tuple):
        nationality = nationality[0] if nationality else None
    raw_gender = str(client.get("gender") or "").lower()
    gender = _GENDERS.get(raw_gender)
    if raw_gender and gender is None:
        raise ValueError(
            f"Morgan Lloyd cannot record a gender of '{raw_gender}': their contact record "
            "admits only male or female."
        )

    payload: dict[str, Any] = {
        "as_contactstatus": _CONTACT_STATUS_CUSTOMER,
        "cam_title": _title_code(client.get("title")),
        "firstname": client["first_name"],
        "lastname": client["surname"],
        "emailaddress1": client["email"],
        "cam_nationality": nationality,
        "cam_ninumber": client["national_identifier"],
        "cam_birthdate": str(client["dob"]),
        "address1_line1": client["address_line1"],
        "address1_city": client["address_city"],
        "address1_postalcode": client["address_postcode"],
        # The vendor's own note: this is the STRING "False", not a boolean -- sending False
        # makes the dependent vulnerability fields required. It used to be hardcoded, which
        # asserted something about every client we had no basis for; it now reflects the record.
        "cam_potentiallyvulnerable": "True" if client.get("is_vulnerable") else "False",
    }
    if gender is not None:
        payload["gendercode"] = gender

    # Held on the contact and simply never sent before now.
    _put(payload, "middlename", client.get("middle_name"))
    # `normalise_phone` here rather than trusting the caller: two different queries feed this
    # (`sync.py::_CLIENT_SQL` and the link endpoint in routers/crm.py) and only one of them wraps
    # the column in `phone_expr`. This is a real back-office record — a Seccl provider object
    # written into a Dynamics contact is not a cosmetic defect there — so the last gate before the
    # wire does it, whichever query supplied it. A plain number is unchanged.
    _put(payload, "mobilephone", normalise_phone(client.get("mobile")))
    # `telephone1` is Dynamics' primary business phone, and it is the one other number we hold.
    # `telephone2` stays off: a third slot is Morgan Lloyd's alone.
    _put(payload, "telephone1", normalise_phone(client.get("phone")))
    # The rest of the address. `line2` and `county` are the only two more the jsonb carries
    # (`ClientAddress` in pkgs/ui, and what `AddressLookup` produces) -- the vendor also documents
    # `address1_line3` and `telephone2`, and we hold NOTHING for either, so they are not sent.
    # Reading a key nobody writes would look like coverage and always be empty.
    _put(payload, "address1_line2", client.get("address_line2"))
    _put(payload, "address1_county", client.get("address_county"))

    # OUR OWN ID ON THEIR RECORD, which is what makes a Dynamics row traceable back to a client
    # without going through `platform.links` -- the vendor's stated purpose for the field
    # ("External Client Identifier to be used by select third parties"). The public uid, never
    # the serial: a serial in a counterparty's system is the addressing rule (docs/api.md) broken
    # somewhere we cannot later fix.
    _put(payload, "cam_externalclientid", client.get("uid"))

    # Added by 0053, each of which cam_createcontact has a parameter for.
    _put(payload, "familystatuscode", _FAMILY_STATUS.get(str(client.get("marital_status") or "")))
    _put(payload, "cam_retirementageint", client.get("retirement_age"))
    _put(payload, "cam_employmentstatus", _EMPLOYMENT_STATUS.get(_key(client, "employment_status")))
    # BOTH source-of-wealth fields: the int is their option set (exact match only), while the
    # string is the client's own statement. Older records predate that mandatory statement, so
    # their selected label remains the safe fallback rather than making the whole contact
    # unsendable.
    sow_key = _key(client, "source_of_wealth")
    _put(payload, "cam_sourceofwealth", _SOURCE_OF_WEALTH.get(sow_key))
    _put(
        payload,
        "cam_sourceofwealthstring",
        client.get("source_of_wealth_detail") or client.get("source_of_wealth"),
    )
    # THE HMRC EARNINGS FIGURE. `relevant_uk_earnings` (0051) is the input to the relievable-limit
    # test, and it is the same number the vendor's `cam_annualearnings` asks for. `decimal` on
    # their side, so a float -- the `_money` boundary `contributions.py` documents.
    if client.get("relevant_uk_earnings") is not None:
        payload["cam_annualearnings"] = float(Decimal(str(client["relevant_uk_earnings"])))
    # The member's tax-relief DECLARATION, from the answer rather than from an inference. Still
    # NOT derived from the presence of `relevant_uk_earnings`: the four options are first-person
    # statements ("I have relevant UK earnings...", "my spouse has..."), holding a figure is not
    # the client having declared anything, and asserting one on their behalf would put a false
    # declaration on a pension record. 0116 gave it a column so it can be answered instead.
    _put(payload, "cam_taxrelief", _TAX_RELIEF.get(_key(client, "tax_relief")))
    #
    # THE VENDOR DOCUMENTS THIS AS `int` WITH ALLOWED VALUES 0/1, NOT AS A BOOL. It was sent as a
    # Python bool, which serialises to JSON `true` -- not the request `1` is, against an
    # Edm.Int32 field. Exactly the trap `platform_actions._typed` was written to describe, and the
    # console path already got right while this one did not.
    if client.get("flexi_access_taken") is not None:
        payload["cam_previouslytakenflexiaccessbenefits"] = int(bool(client["flexi_access_taken"]))
    if client.get("is_vulnerable"):
        # `cam_detailssupportingnotes`, NOT `cam_additionalvulnerabilitynotes` -- which appears
        # nowhere in the vendor's contract and only ever in our own code. Dynamics IGNORES a field
        # it does not recognise, so every vulnerable client's notes were silently discarded and
        # the call still reported success. Pinned by the contract tripwire.
        _put(payload, "cam_detailssupportingnotes", client.get("vulnerability_notes"))
        # THE FCA'S FOUR, ON BOTH SIDES. `vulnerability_reasons` (0054) chose FG21/1's vocabulary
        # and so did the vendor, so this is a lookup and not a judgement. A multiselect goes as a
        # comma-separated string of the option numbers -- the vendor's own shape.
        #
        # It was absent while `cam_potentiallyvulnerable: "True"` was being sent, and the vendor
        # marks the vulnerability detail `No**` (required once the flag is set) -- so a vulnerable
        # client was being pushed with the flag and none of the detail it makes required.
        drivers = [
            _VULNERABILITY_DRIVERS[reason]
            for reason in client.get("vulnerability_reasons") or []
            if reason in _VULNERABILITY_DRIVERS
        ]
        _put(payload, "cam_vulnerabilitydrivers", ",".join(str(v) for v in sorted(drivers)))
        # The CHARACTERISTICS beneath those drivers -- the other half of the `No**` detail the
        # flag makes required, and the half we held no column for until 0116. Still not ENFORCED
        # here (a `**` is a condition stated in prose, and the estate's rule is to show the marker
        # and let the vendor speak) but no longer unanswerable: an adviser who recorded them is
        # now the one deciding what goes, rather than the schema deciding nothing can.
        characteristics = [
            _VULNERABILITY_CHARACTERISTICS[item]
            for item in client.get("vulnerability_characteristics") or []
            if item in _VULNERABILITY_CHARACTERISTICS
        ]
        _put(
            payload,
            "cam_vulnerabilitycharacteristics",
            ",".join(str(v) for v in sorted(characteristics)),
        )

    # The adviser firm. Must be an OData ENTITY REFERENCE, not a bare GUID: `accounts(<guid>)`
    # and `/accounts(<guid>)` are both accepted, while a bare, braced or uppercased GUID all fail
    # with "Invalid Format for cam_financialadviser_Account". Without it the contact is created
    # unattached and `cam_createscheme` later 403s on it, so this is what makes a synced client
    # actually usable rather than merely present.
    settings = _settings()
    firm = settings.adviser_firm_id.strip()
    if firm:
        payload["cam_financialadviser_Account"] = f"/accounts({firm.strip('{}')})"
        # THE PAIR IS REQUIRED TOGETHER. Attaching the firm without an adviser CONTACT is refused
        # outright -- `400 0x80040224 Financial Adviser contact must be populated for the adviser
        # firm` -- which is the `No*` conditional the vendor states in prose. It only bites once
        # the firm is configured, so this path worked for months by omitting BOTH and creating
        # every client unattached; setting `MORGAN_LLOYD_ADVISER_FIRM_ID` is what surfaces it.
        #
        # AN ENTITY REFERENCE, exactly like the firm above -- `/contacts(<guid>)`. The manifest
        # types both fields `lookup`, and that is the rule: a bare GUID is refused with
        # `400 0x80040224 Invalid Format for cam_FinancialAdviserContact` (observed against the
        # org, 09 Aug 2026, after trying the bare form on the assumption that an action parameter
        # differed from the entity PATCH -- it does not).
        adviser = (
            str(client.get("ml_adviser_contact_id") or "").strip()
            or settings.adviser_contact_id.strip()
        )
        if adviser:
            payload["cam_FinancialAdviserContact"] = f"/contacts({adviser.strip('{}')})"

    # `cam_employmentstatus` is deliberately NOT sent from `employment_status`: despite the name
    # it is an OCCUPATIONAL option set on their side (Business Owner, C-Suite, Doctors...) and
    # answers a different question. See 0053.
    return payload


_ML_SLICES: dict[str, frozenset[str]] = {
    "details": frozenset(
        {
            "firstname",
            "lastname",
            "middlename",
            "emailaddress1",
            "mobilephone",
            "telephone1",
            "cam_title",
            "cam_nationality",
            "cam_ninumber",
            "cam_birthdate",
            "gendercode",
            "address1_line1",
            "address1_line2",
            "address1_city",
            "address1_county",
            "address1_postalcode",
            "cam_externalclientid",
        }
    ),
    "vulnerability": frozenset(
        {
            "cam_potentiallyvulnerable",
            "cam_detailssupportingnotes",
            "cam_vulnerabilitydrivers",
            "cam_vulnerabilitycharacteristics",
        }
    ),
    "circumstances": frozenset(
        {
            "familystatuscode",
            "cam_retirementageint",
            "cam_employmentstatus",
            "cam_sourceofwealth",
            "cam_sourceofwealthstring",
            "cam_annualearnings",
            "cam_taxrelief",
            "cam_previouslytakenflexiaccessbenefits",
        }
    ),
}


def contact_slice(client: dict[str, Any], *, part: str) -> dict[str, Any]:
    """The Dynamics fields for one part of the client. Bank is not on this contract."""
    if part == "bank":
        raise ValueError(
            "Morgan Lloyd's contact has no bank account in the mapped contract. "
            "The nominated account stays on the Graphene client until the scheme's "
            "bank block is confirmed."
        )
    keys = _ML_SLICES.get(part)
    if keys is None:
        raise ValueError("Morgan Lloyd has no update for that part of the client.")
    fields = {key: value for key, value in build_contact(client).items() if key in keys}
    if not fields:
        raise ValueError("Nothing in that part of the client is ready to send.")
    return fields


def _put(payload: dict[str, Any], key: str, value: Any) -> None:
    """Set an optional field only when we actually have a value for it.

    Sending a null for an optional Dynamics parameter is not the same as omitting it -- the
    action validates against a fixed allowlist and a present-but-empty value has been seen to
    trip "Error identified in Payload".
    """
    if value is not None and value != "":
        payload[key] = value


_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _created_id(response: dict[str, Any]) -> str | None:
    """Pull the new contact's GUID out of the action envelope.

    The custom actions do not answer like the OData entity endpoints. A successful
    `cam_createcontact` returns ``{"@odata.context": ..., "message": ..., "response": ...}``
    with the id inside `response` -- not a top-level `contactid`, which is what this used to
    look for. The contact WAS created every time; we then threw it away and recorded no link,
    which is the worst of the three possible outcomes.

    `response` is variously a bare GUID string, a JSON-encoded object or a nested dict
    depending on the action, so the GUID is matched by shape wherever it appears rather than
    by a key name none of the docs pin down.
    """
    for key in ("contactId", "contactid", "id"):
        value = response.get(key)
        if isinstance(value, str) and _GUID.fullmatch(value.strip("{}")):
            return value.strip("{}")
    inner = response.get("response")
    if isinstance(inner, dict):
        for value in inner.values():
            if isinstance(value, str) and (found := _GUID.search(value)):
                return found.group(0)
    if isinstance(inner, str) and (found := _GUID.search(inner)):
        return found.group(0)
    # Last resort: the message sometimes carries it ("Contact <guid> created").
    if isinstance(message := response.get("message"), str) and (found := _GUID.search(message)):
        return found.group(0)
    return None


async def create_contact(client: dict[str, Any]) -> str:
    """Create the Dynamics contact and return its GUID.

    Raises ``ValueError`` when required fields are missing (caller turns that into a 422),
    ``MorganLloydNotConfigured`` without credentials, ``MorganLloydUnavailable`` when Dynamics
    or Azure refuses.
    """
    absent = missing_fields(client)
    if absent:
        raise ValueError(
            "Morgan Lloyd requires these details before a client can be created: "
            + ", ".join(absent)
        )
    if not configured():
        raise unconfigured("MORGAN_LLOYD_TENANT_ID / _CLIENT_ID / _CLIENT_SECRET")
    from morgan_lloyd.client import MorganLloydClient

    try:
        async with MorganLloydClient(settings=_settings()) as api:
            response = await api.create_contact(build_contact(client))
    # THE CLIENT DOES NOT RAISE httpx. `morgan_lloyd._base` wraps every transport and status
    # error into `MorganLloydApiError`, which descends from `MorganLloydError` and NOT from
    # `httpx.HTTPError` — so an `except httpx.HTTPError` here caught nothing and a rejected vendor
    # call, or an Azure Conditional Access block, escaped as an unhandled 500 with a stack trace
    # instead of the 502 this route's docstring promises. httpx stays in the tuple for anything
    # raised outside the client.
    except (MorganLloydError, httpx.HTTPError) as exc:
        raise MorganLloydUnavailable(_explain(exc)) from exc

    guid = _created_id(response)
    if guid is None:
        raise MorganLloydUnavailable(
            f"Morgan Lloyd created the contact but returned no id (keys: {sorted(response)})."
        )
    return guid
