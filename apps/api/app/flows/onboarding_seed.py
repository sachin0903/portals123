"""Seed the full legacy onboarding — faithfully rebuilt on the v2 orchestration engine.

This is the hardcoded onboarding from the old platform (``platform-main``) re-expressed as
**configuration**: builder forms + terms declarations + one tiered, gated flow definition. Nothing
here is special-cased in the engine — a firm gets exactly this out of the box and can then edit
form, field, condition and step in the UI.

What it lays down for a tenant (idempotent by code/uid):

* **Forms** (``documents.forms`` + ``form_fields``) — the fact-find, ported field-for-field from the
  legacy dynamic-field seed + applicant/account schemas:
  - ``applicant_core``      individual fact-find (name/DOB/nationality/tax/NI/contact/address/bank)
  - ``jisa_child``          child owner (identity facts only; no NI/contact/address/bank/adult KYC)
  - ``jisa_registered_contact`` parent contact (name/contact/address; not an applicant fact-find)
  - ``applicant_offshore``  enhanced KYC for EVERY individual — birth/occupation/source-of-funds/
    wealth/PEP. The code is historical (see the note on ``FORMS``); the form is "Additional
    information" and its step is ``additional_info``. Nothing here is gated on custody.
  - ``applicant_pension``   pension info (marital/employment/retirement age)
  - ``applicant_employer``  SIPP employer contributions
  - ``organisation_core``   the organisation/trust/charity fact-find
  - ``account_activity``    offshore account-activity expectations
* **Terms** (``terms.terms`` catalogue rows, published HTML versions) — the client
  declarations, scoped for selection by the flow's conditional accept-terms steps.
* **The onboarding flow** — participant-tier (core + conditional offshore/pension/employer/org +
  identity + AML), account-tier (activity + per-product terms + offshore EDD review + setup). The
  cross-case KYC gate and the ``crm.applications`` status projection reproduce the legacy state
  machine (``draft → with_client → identity → aml → offshore → processing → completed``).

The flow ships as a **v3 graph** (``{nodes: [...]}``) wired into a single **spine**: a ``start``
node, then every node wired one into the next, with each condition a two-armed ``condition`` node
whose arms re-converge further down. The tiers (``scope``) still fan the work out per person and
per account — that is the engine's own repetition and is orthogonal to the wiring — but the ORDER
within the journey is now the wires rather than ``depends_on``.

That is a deliberate change of ordering, not only of drawing: work a tier used to offer in parallel
is reached one node at a time. It is what makes a condition able to decide a path at all — control
only flows where there are wires — and it is what the canvas has to be able to show.

External conditions (custody/product) that the legacy evaluated *inside* the fact-find become
**condition nodes** here (a whole form is planned in or out); only intra-form conditions
(occupation = Retired → previous-occupation) stay as field conditions. That is the faithful mapping
onto the new tooling, and it keeps each form self-contained.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.contacts import (
    VULNERABILITY_CHARACTERISTICS,
    VULNERABILITY_REASONS,
    characteristic_label,
    reason_label,
)
from app.flows import onboarding_terms as terms_content
from app.flows.definitions import validate_definition
from app.flows.graph import condition_arms
from app.ids import new_id
from app.national_insurance import FIELD_MESSAGE, FIELD_PATTERN

FLOW_CODE = "onboarding"


def _complycube() -> bool:
    from app.complycube.client import complycube_configured

    return complycube_configured()


def _idv_provider() -> str:
    """ComplyCube when the deployment can verify an identity digitally; manual otherwise.

    Without a ComplyCube key ``provider: complycube`` would be refused at publish
    (``ReviewHandler.preflight``), so the seed falls back to a staff member recording the outcome —
    still a valid path. The AML step has no provider: it always screens on ComplyCube.
    """
    return "complycube" if _complycube() else "manual"


def _idv_assignee(provider: str) -> str:
    """Who is ASKED first: the CLIENT where a digital check exists, STAFF where none does.

    Whether a check runs digitally or by a person is the client's own choice — they hold the
    document and know whether they can photograph it — so where the deployment can offer both, the
    step starts with them and they may hand it to a person instead
    (``POST /applications/me/identity-manual``, which records the choice and moves the step to
    staff; ``ReviewHandler._provider`` then reads the choice rather than the pinned provider).

    Where there is no digital check there is nothing to choose: a client assigned a manual check
    has nothing they can do with it, so it goes straight to staff. That half is the legacy app's
    and unchanged — its public journey showed an identity step only when a digital provider was
    configured (``shouldIncludeIdentityStep = digitalVerification && !isVerified``).
    """
    return "client" if provider == "complycube" else "staff"


# ==================================================================================================
# Option lists (ported verbatim from platform-main pkgs/db/seed.sql).
# ==================================================================================================
OCCUPATIONS = [
    "Accountant",
    "Actuary",
    "Airline Pilot",
    "Architect",
    "Auditor",
    "Bar Staff",
    "Barrister",
    "Border Force Officer",
    "Broadcaster",
    "Builder",
    "Business Analyst",
    "Carpenter",
    "Chef",
    "Civil Engineer",
    "Civil Servant",
    "Compliance Consultant",
    "Compliance Officer",
    "Copywriter",
    "Corporate Lawyer",
    "Cybersecurity Analyst",
    "Data Scientist",
    "Delivery Driver",
    "Dentist",
    "DevOps Engineer",
    "Diplomat",
    "Director",
    "Editor",
    "Education Consultant",
    "Electrician",
    "Engineer",
    "Entrepreneur",
    "Event Planner",
    "Financial Advisor",
    "Firefighter",
    "Fleet Manager",
    "Fund Manager",
    "GP / Doctor",
    "Graphic Designer",
    "Headteacher",
    "Health & Safety Officer",
    "Healthcare Assistant",
    "Homemaker",
    "Hotel Manager",
    "Insurance Underwriter",
    "Investment Analyst",
    "IT Consultant",
    "Journalist",
    "Legal Counsel",
    "Local Government Officer",
    "Logistics Manager",
    "Management Consultant",
    "Marketing Manager",
    "Medical Researcher",
    "Military Personnel",
    "Network Administrator",
    "Notary",
    "Nurse",
    "Paralegal",
    "Paramedic",
    "Patent Attorney",
    "Pharmacist",
    "Plumber",
    "Police Officer",
    "Primary School Teacher",
    "Product Manager",
    "Psychologist",
    "Public Relations Officer",
    "QA Engineer",
    "Quantity Surveyor",
    "Radiographer",
    "Relationship Manager",
    "Researcher",
    "Retail Manager",
    "Retired",
    "Risk Manager",
    "School Administrator",
    "Secondary School Teacher",
    "Self-Employed",
    "Shipping Coordinator",
    "Site Manager",
    "Social Media Manager",
    "Social Worker",
    "Software Engineer",
    "Solicitor",
    "Store Assistant",
    "Student",
    "Surgeon",
    "Systems Analyst",
    "Teaching Assistant",
    "Tour Guide",
    "Train Driver",
    "Travel Agent",
    "Unemployed",
    "University Lecturer",
    "Waiter / Waitress",
    "Warehouse Operative",
    "Other",
]

SOURCE_OF_FUNDS = [
    "Personal savings",
    "Salary or employment income",
    "Business income",
    "Sale of property",
    "Sale of investments (e.g., shares, bonds)",
    "Loan proceeds",
    "Gift from family or friends",
    "Inheritance",
    "Pension or retirement funds",
    "Insurance payout",
    "Transfer from another bank account",
    "Transfer from another custodian or investment platform",
    "Payment from a current account",
    "Proceeds from company sale",
    "Other (please specify)",
]

# Morgan Lloyd's `cam_sourceofwealth` option set, verbatim including their misspelling of
# "Inheritence". A PICKER rather than a textarea so `_SOURCE_OF_WEALTH` hits the vendor's INT
# on an exact label match; "Other" is the only member that then asks for the words
# (`source_of_wealth_detail` → `cam_sourceofwealthstring`).
SOURCE_OF_WEALTH = [
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


# Entity type and classification store lowercase KEYS (`_cond("entityType", "equals", "charity")`
# reads one), so they go through `_keyed_opts` — the stored value stays the key and only the label
# the picker prints is capitalised. ORG_TYPES stores its label, so it is already written as words.
ORG_ENTITY_TYPES = ["corporate", "trust", "charity"]
ORG_TYPES = [
    "Cash Intensive",
    "Charitable Remainder Trust",
    "Charity",
    "Collectives",
    "Endowment / Foundation",
    "Estate",
    "Financial Institution",
    "Limited Liability Company",
    "Partnership",
    "Pension",
    "Professional Services",
    "Public Entity",
    "Trust",
    "Other",
]
ORG_CLASSIFICATIONS = [
    "charity",
    "collective",
    "corporate",
    "estate",
    "government",
    "individual",
    "pension",
    "trust",
]


# ==================================================================================================
# Field helpers. A field = (key, type, label, section, required, conditions|None, validation|None,
# options|None) plus an optional description. Conditions/validation/options are the exact new-forms
# shapes (docs/forms.md).
# ==================================================================================================
def _cond(field: str, operator: str, value: Any) -> dict[str, Any]:
    return {"field": field, "operator": operator, "value": value}


def _cond_any(*conditions: dict[str, Any]) -> dict[str, Any]:
    """Any one of these may hold — the field runtime's OR group. Bare, like ``_cond_all``."""
    return {"logic": "OR", "conditions": list(conditions)}


def _cond_all(*conditions: dict[str, Any]) -> dict[str, Any]:
    """Every one of these must hold — the field runtime's AND group.

    An AND GROUP, which is the only one of the persisted field-condition shapes that can hold more
    than one condition (pkgs/ui `FieldConditions`). Returned bare, because ``_seed_form`` is what
    wraps a field's condition in ``{"conditional": ...}`` — wrapping here too would nest it twice
    and the runtime would read no condition at all.
    """
    return {"logic": "AND", "conditions": list(conditions)}


_BANK_UK = _cond("bank_style", "notEquals", "international")
_BANK_INTL = _cond("bank_style", "equals", "international")


# A second National Insurance question. The nationality identifier is already a NINO when
# that country asks for one, and asking again would store the same number twice. An ISA or
# a pension still needs a UK NINO when the identifier is something else, including when the
# type has not been filled yet. A GIA needs one when nationality or tax residency includes
# the UK, whether that answer is a code or the country name. The product flags are on the
# step's applicant context; the UK half is the answer already on this form.
def _uk_place(field: str) -> dict[str, Any]:
    return _cond_any(
        *(
            _cond(field, "contains", place)
            for place in ("GB", "UK", "GBR", "United Kingdom", "British")
        )
    )


_UK_NINO_ASK = _cond_all(
    _cond("national_identifier_type", "notEquals", "National Insurance Number"),
    _cond_any(
        _cond("applicant.has_isa_or_pension", "equals", True),
        _cond_all(
            _cond("applicant.has_gia", "equals", True),
            _cond_any(_uk_place("nationality"), _uk_place("tax_residency")),
        ),
    ),
)

# The same editor the client record and the account record use (`bank-fields.svelte`): account
# type full width, then the active rail in thirds, then the UK payment method. Values stay
# `uk` / `international` — those are what in-flight answers and the conditions below already
# store. An optional 9th element is the field description (`_write_form_fields`).
_BANK_CROWN = (
    "UK bank accounts include the following crown territories: Jersey, Guernsey, "
    "Isle of Man, Gibraltar."
)
_BANK_METHOD_HELP = (
    "BACS is free. This is the default for withdrawals. A withdrawal can use the other method."
)
_BANK_ACCOUNT_FIELDS: list[tuple[Any, ...]] = [
    (
        "bank_style",
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
        _BANK_CROWN,
    ),
    (
        "bank_account.account_name",
        "text",
        "Account name",
        "Bank account",
        True,
        None,
        {"minLength": 2, "width": 4},
        None,
    ),
    (
        "bank_account.sort_code",
        "sortCode",
        "Sort code",
        "Bank account",
        True,
        _BANK_UK,
        {
            "width": 4,
            "pattern": r"^\d{2}\s*-\s*\d{2}\s*-\s*\d{2}$",
            "patternMessage": "Enter a 6-digit sort code",
        },
        None,
    ),
    (
        "bank_account.account_number",
        "bankAccountNumber",
        "Account number",
        "Bank account",
        True,
        _BANK_UK,
        {
            "width": 4,
            "pattern": r"^\d{8}$",
            "patternMessage": "Enter an 8-digit account number",
        },
        None,
    ),
    (
        "bank_account.iban",
        "text",
        "IBAN",
        "Bank account",
        True,
        _BANK_INTL,
        {"width": 4, "minLength": 15},
        None,
    ),
    (
        "bank_account.bic",
        "text",
        "SWIFT/BIC",
        "Bank account",
        True,
        _BANK_INTL,
        {"width": 4, "minLength": 8},
        None,
    ),
    (
        "bank_account.method",
        "segmented",
        "Payment method",
        "Bank account",
        True,
        _BANK_UK,
        {"width": 12, "defaultValue": "bacs", "shape": "square"},
        {
            "options": [
                {"value": "bacs", "label": "BACS"},
                {"value": "faster_payment", "label": "Faster Payment"},
            ]
        },
        _BANK_METHOD_HELP,
    ),
]


def _terms_party_visibility(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Who accepts client terms on an account.

    Individuals only (an organisation owner is not terms-gated). On a JISA the child is the owner
    and must not be asked; the registered contact is. Combined with a per-product extra (ISA /
    transfer) when the declaration itself is wrapper-specific.
    """
    who = _cond_all(
        _cond("applicant.type", "equals", "individual"),
        _cond_any(
            _cond("account.product", "notEquals", "JISA"),
            _cond("applicant.roles", "contains", "registered_contact"),
        ),
    )
    return _cond_all(who, extra) if extra else who


def _opts(values: list[str]) -> dict[str, Any]:
    return {"options": [{"value": v, "label": v} for v in values]}


def _keyed_opts(keys: list[str]) -> dict[str, Any]:
    """Options whose stored value is a lowercase key: the label is the key in sentence case."""
    return {"options": [{"value": k, "label": k[:1].upper() + k[1:]} for k in keys]}


# The `title` field type (`pkgs/ui` forms/vocabularies.ts TITLE_OPTIONS). A pin test
# reads that file: the API container does not import pkgs/ui. See
# docs/client-vocabularies.md.
PERSON_TITLES = [
    "Mr",
    "Mrs",
    "Miss",
    "Ms",
    "Dr",
    "Prof",
]


# --- applicant_core: the individual fact-find --------------------------------------------------
_CORE_FIELDS: list[tuple[Any, ...]] = [
    (
        "title",
        "title",
        "Title",
        "Personal details",
        True,
        None,
        None,
        # The `title` field type carries PERSON_TITLES. Options authored here would be ignored.
        None,
    ),
    ("first_name", "text", "First name", "Personal details", True, None, {"minLength": 2}, None),
    ("middle_name", "text", "Middle name(s)", "Personal details", False, None, None, None),
    ("surname", "text", "Surname", "Personal details", True, None, {"minLength": 2}, None),
    ("dob", "dateOfBirth", "Date of birth", "Personal details", True, None, None, None),
    (
        "gender",
        "gender",
        "Gender",
        "Personal details",
        True,
        None,
        None,
        None,
    ),
    (
        "nationality",
        "multiSelect",
        "Nationality",
        "Personal details",
        True,
        None,
        None,
        {"source": "nationalities"},
    ),
    (
        "tax_residency",
        "multiSelect",
        "Countries of tax residence",
        "Personal details",
        True,
        None,
        None,
        {"source": "countries", "valueKey": "code", "labelKey": "name"},
    ),
    (
        "national_identifier",
        "nationalIdentifier",
        "National identifier (e.g. NI number)",
        "Personal details",
        True,
        None,
        None,
        None,
    ),
    (
        "national_identifier_type",
        "text",
        "Identifier type",
        "Personal details",
        False,
        None,
        # Derived by the nationalIdentifier control from nationality. It must be submitted beside
        # the identifier, but an adviser must never type or override it.
        {"hidden": True},
        None,
    ),
    (
        "uk_national_insurance",
        "text",
        "UK National Insurance number",
        "Personal details",
        True,
        _UK_NINO_ASK,
        {"pattern": FIELD_PATTERN, "patternMessage": FIELD_MESSAGE},
        None,
        "Required for an ISA or a pension, and for a GIA when the client is British or "
        "UK-resident, unless the nationality identifier is already a National Insurance number.",
    ),
    ("email", "email", "Email address", "Contact", True, None, None, None),
    # The KEY stays `phone` and the LABEL becomes "Mobile number". Renaming the key would have
    # read better and would have orphaned the answer of every in-flight case: `_write_form_fields`
    # deletes and re-inserts the seeded fields on every reseed, while `documents.form_responses`
    # is keyed by `field_key` — so a renamed key leaves the stored answer stranded under the old
    # name and asks a client half way through onboarding for their number a second time.
    ("phone", "phoneNumber", "Mobile number", "Contact", True, None, None, None),
    # Optional. The key stays `other_numbers` so an answer already stored against an in-flight
    # case still resolves; the question is one phone number, mapped to `crm.contacts.phone`.
    ("other_numbers", "phoneNumber", "Phone", "Contact", False, None, None, None),
    ("address", "address", "Residential address", "Address", True, None, None, None),
    *_BANK_ACCOUNT_FIELDS,
    (
        "is_vulnerable",
        "checkbox",
        "This client is vulnerable",
        "Vulnerability",
        False,
        None,
        None,
        None,
    ),
    (
        "vulnerability_reasons",
        "multiSelect",
        "Drivers",
        "Vulnerability",
        True,
        _cond("is_vulnerable", "equals", True),
        None,
        {
            "options": [
                {"value": reason, "label": reason_label(reason)} for reason in VULNERABILITY_REASONS
            ]
        },
    ),
    (
        "vulnerability_characteristics",
        "multiSelect",
        "Characteristics",
        "Vulnerability",
        False,
        _cond("is_vulnerable", "equals", True),
        None,
        {
            "options": [
                {"value": slug, "label": characteristic_label(slug)}
                for slug in VULNERABILITY_CHARACTERISTICS
            ]
        },
    ),
    (
        "vulnerability_notes",
        "textarea",
        "Notes",
        "Vulnerability",
        False,
        _cond("is_vulnerable", "equals", True),
        {"rows": 3},
        None,
    ),
]

# A JISA owner is the child. They need enough identity data to open the account, but no National
# Insurance number, contact details, bank details or adult fact-find. The adult is represented by a
# separate registered-contact form below rather than receiving an applicant form.
_JISA_CHILD_FIELD_KEYS = {
    "title",
    "first_name",
    "middle_name",
    "surname",
    "dob",
    "gender",
    "nationality",
    "tax_residency",
}
_JISA_CHILD_FIELDS = [field for field in _CORE_FIELDS if field[0] in _JISA_CHILD_FIELD_KEYS]

_JISA_REGISTERED_CONTACT_FIELD_KEYS = {
    "title",
    "first_name",
    "middle_name",
    "surname",
    "email",
    "phone",
    "other_numbers",
    "address",
}
_JISA_REGISTERED_CONTACT_FIELDS = [
    field for field in _CORE_FIELDS if field[0] in _JISA_REGISTERED_CONTACT_FIELD_KEYS
]

# --- applicant_offshore: enhanced KYC, asked of every INDIVIDUAL (not gated on custody — 0116
# gave these answers columns because the platform asks them of everyone). Intra-form conditions
# only. The form code is historical; see the note on `FORMS`. ------------------------------------
_ADDITIONAL_FIELDS: list[tuple[Any, ...]] = [
    (
        "country_of_birth",
        "combobox",
        "Country of birth",
        "Additional information",
        True,
        None,
        None,
        # `ref.countries` (0050) via the live data source, storing the CODE and showing the name.
        # It was a hardcoded list of 40-odd display NAMES, which made this the one country field
        # in the fact-find that stored a name: `nationality` and the individual's `tax_residency`
        # already use a source and already store codes. So `crm.contacts.country_of_birth` held
        # "United Kingdom" beside a `nationality` of `{GB}` — the same fact spelled two ways in
        # one row, unjoinable to `ref.countries` and unusable at a boundary that wants ISO.
        #
        # `countries`, NOT `nationalities`: `resolveOptions` special-cases the latter to prefer
        # `adjective`, so a country-of-birth picker would offer "British". You are born in a
        # country, not in a nationality.
        {"source": "countries", "valueKey": "code", "labelKey": "name"},
    ),
    ("town_of_birth", "text", "Town of birth", "Additional information", True, None, None, None),
    (
        "occupation",
        "combobox",
        "Occupation",
        "Additional information",
        True,
        None,
        None,
        _opts(OCCUPATIONS),
    ),
    (
        "occupation_retired",
        "textarea",
        "Previous occupation & aggregated wealth",
        "Additional information",
        True,
        _cond("occupation", "equals", "Retired"),
        None,
        None,
    ),
    (
        "occupation_other",
        "text",
        "Occupation (other)",
        "Additional information",
        True,
        _cond("occupation", "equals", "Other"),
        None,
        None,
    ),
    (
        "source_of_funds",
        "combobox",
        "Source of funds",
        "Additional information",
        True,
        None,
        None,
        _opts(SOURCE_OF_FUNDS),
    ),
    (
        "source_of_funds_other",
        "text",
        "Source of funds (other)",
        "Additional information",
        True,
        _cond("source_of_funds", "equals", "Other (please specify)"),
        None,
        None,
    ),
    (
        "source_of_wealth",
        "select",
        "Source of wealth",
        "Additional information",
        True,
        None,
        None,
        _opts(SOURCE_OF_WEALTH),
    ),
    (
        "source_of_wealth_other",
        "textarea",
        "Source of wealth statement",
        "Additional information",
        True,
        None,
        {
            "minLength": 30,
            "rows": 3,
            "errorMessage": "Describe the source of wealth in at least 30 characters",
        },
        None,
    ),
    # Two separate facts. Every client is or is not a US person. Only a client investing in
    # US assets has a W-9 or a W-8, so that one is optional.
    (
        "us_person",
        "select",
        "Is a US person?",
        "Additional information",
        True,
        None,
        None,
        _opts(["Yes", "No"]),
        "A US citizen or US tax resident.",
    ),
    (
        "fatca_status",
        "select",
        "Does this client have a W-9 or a W-8?",
        "Additional information",
        False,
        None,
        None,
        {"options": [{"value": "w9", "label": "W-9"}, {"value": "w8", "label": "W-8"}]},
        "Only needed for US assets.",
    ),
    (
        "pep",
        "checkbox",
        "I am a Politically Exposed Person (PEP)",
        "Additional information",
        False,
        None,
        {"defaultValue": False},
        None,
    ),
]

# --- applicant_pension: pension information -----------------------------------------------------
_PENSION_FIELDS: list[tuple[Any, ...]] = [
    (
        "pension.maritalStatus",
        "marital",
        "Marital status",
        "Pension information",
        True,
        None,
        None,
        None,
    ),
    (
        "pension.employmentStatus",
        "employment",
        "Employment status",
        "Pension information",
        True,
        None,
        None,
        None,
    ),
    (
        "pension.retirementAge",
        "number",
        "Retirement age",
        "Pension information",
        True,
        None,
        {"min": 55, "max": 75},
        None,
    ),
]

# --- applicant_employer: SIPP employer contributions -------------------------------------------
_EMPLOYER_FIELDS: list[tuple[Any, ...]] = [
    (
        "pension.hasEmployerContributions",
        "checkbox",
        "My employer contributes to this SIPP",
        "Employer contributions",
        False,
        None,
        None,
        None,
    ),
    (
        "pension.employerContributions.name",
        "text",
        "Employer name",
        "Employer contributions",
        True,
        _cond("pension.hasEmployerContributions", "equals", True),
        None,
        None,
    ),
    (
        "pension.employerContributions.registrationNumber",
        "text",
        "Registration number",
        "Employer contributions",
        True,
        _cond("pension.hasEmployerContributions", "equals", True),
        None,
        None,
    ),
    # The company's address, which the client record's Employer card shows and Morgan Lloyd needs
    # for the payer of an employer contribution. Keyed FLAT (`employer_address`, not
    # `pension.employerContributions.address`) for the same reason the core form's is `address`:
    # a composite field stores `{key: {line1, …}}`, and the mapper resolves `key.line1` by
    # nesting into the answers — which a dotted key defeats, because the answers are keyed by the
    # dotted string and hold no `pension` object to walk into.
    (
        "employer_address",
        "address",
        "Employer address",
        "Employer contributions",
        True,
        _cond("pension.hasEmployerContributions", "equals", True),
        None,
        None,
    ),
    (
        "pension.employerContributions.accountNumber",
        "bankAccountNumber",
        "Account number",
        "Employer contributions",
        True,
        _cond("pension.hasEmployerContributions", "equals", True),
        {"pattern": r"^\d{8}$"},
        None,
    ),
    (
        "pension.employerContributions.sortCode",
        "sortCode",
        "Sort code",
        "Employer contributions",
        True,
        _cond("pension.hasEmployerContributions", "equals", True),
        {"pattern": r"^\d{2}\s*-\s*\d{2}\s*-\s*\d{2}$"},
        None,
    ),
]

# --- organisation_core: organisation / trust / charity fact-find --------------------------------
_ORG_FIELDS: list[tuple[Any, ...]] = [
    ("name", "text", "Organisation name", "Organisation", True, None, {"minLength": 2}, None),
    (
        "entityType",
        "select",
        "Entity type",
        "Organisation",
        True,
        None,
        None,
        _keyed_opts(ORG_ENTITY_TYPES),
    ),
    (
        "organisationType",
        "select",
        "Organisation type",
        "Organisation",
        True,
        None,
        None,
        _opts(ORG_TYPES),
    ),
    (
        "classification",
        "select",
        "Classification",
        "Organisation",
        True,
        None,
        None,
        _keyed_opts(ORG_CLASSIFICATIONS),
    ),
    ("subClassification", "text", "Sub-classification", "Organisation", False, None, None, None),
    (
        "legalIdentity",
        "text",
        "Legal Entity Identifier (LEI)",
        "Organisation",
        True,
        None,
        {"pattern": r"^[A-Z0-9]{20}$", "patternMessage": "Enter a 20-character LEI"},
        None,
    ),
    (
        "charityId",
        "text",
        "Charity number",
        "Organisation",
        True,
        _cond("entityType", "equals", "charity"),
        None,
        None,
    ),
    ("registrationNumber", "text", "Registration number", "Organisation", False, None, None, None),
    (
        "taxResidency",
        "combobox",
        "Country of tax residence",
        "Organisation",
        True,
        None,
        None,
        # The same switch, and here the inconsistency was starker: an INDIVIDUAL's tax residency
        # uses `{"source": "nationalities"}` and stores a code, so the same question about an
        # organisation was storing "Jersey" where the individual's stored "JE".
        {"source": "countries", "valueKey": "code", "labelKey": "name"},
    ),
    (
        "noTaxId",
        "checkbox",
        "No tax identification number",
        "Organisation",
        False,
        None,
        None,
        None,
    ),
    (
        "taxId",
        "text",
        "Tax identification number",
        "Organisation",
        True,
        _cond("noTaxId", "notEquals", True),
        None,
        None,
    ),
    (
        "reasonNoTaxId",
        "text",
        "Reason for no tax ID",
        "Organisation",
        True,
        _cond("noTaxId", "equals", True),
        None,
        None,
    ),
    (
        "address",
        "address",
        "Registered address",
        "Registered address",
        True,
        None,
        None,
        None,
    ),
    *_BANK_ACCOUNT_FIELDS,
]

# --- account_activity: offshore account-activity expectations (entity 'account') ----------------
_ACTIVITY_FIELDS: list[tuple[Any, ...]] = [
    (
        "account_activity_nature",
        "textarea",
        "Nature of activities (purpose & intended nature of the business relationship)",
        "Account activity",
        True,
        None,
        None,
        None,
    ),
    (
        "account_activity_standing_orders",
        "text",
        "Standing order instructions on the account",
        "Account activity",
        True,
        None,
        None,
        None,
    ),
    (
        "account_activity_funds_in",
        "currency",
        "Estimated total flow of funds into account p/a",
        "Account activity",
        True,
        None,
        None,
        None,
    ),
    (
        "account_activity_funds_out",
        "currency",
        "Estimated total flow of funds out of account p/a",
        "Account activity",
        True,
        None,
        None,
        None,
    ),
    (
        "account_activity_max_turnover",
        "percentage",
        "Estimated maximum account turnover expected",
        "Account activity",
        True,
        None,
        None,
        None,
    ),
    (
        "account_activity_transaction_scale",
        "currency",
        "Estimated scale and volume of transactions",
        "Account activity",
        True,
        None,
        None,
        None,
    ),
]

# --- account_preferences: choices made while WORKING the journey, not before it starts -----------
_ACCOUNT_IS_PENSION = _cond("account.product_type", "equals", "SIPP")
# A pension account is sterling. Shown when the wrapper is neither SIPP nor a third-party
# pension. Not `_ACCOUNT_IS_NOT_PENSION`: that gate also owns crystallisation and beneficiaries,
# and widening it would ask those on a third-party pension.
_ACCOUNT_ASKS_CURRENCY = _cond_all(
    _cond("account.product_type", "notEquals", "SIPP"),
    _cond("account.product_type", "notEquals", "TPPENSION"),
)
# `notEquals`, not `not_equals`: the field runtime's operator switch knows the camel spelling
# (and `!==`) and falls through to "visible" on anything else, so the snake one meant every
# not-a-pension question was asked on a SIPP too. It is the same vocabulary
# `app/flows/conditions.py::OPERATORS` declares.
_ACCOUNT_IS_NOT_PENSION = _cond("account.product_type", "notEquals", "SIPP")
_ACCOUNT_IS_OFFSHORE = _cond("account.custody", "equals", "offshore")
# The form context's `account.custodian` is the account's provider code, falling back to the
# application's. `_write_account_preferences` reads the same pair, so a field shown here is kept.
_ACCOUNT_IS_SEI = _cond("account.custodian", "equals", "sei")
# Regular withdrawals belong to an account that can pay them out: any non-pension, or a SIPP in a
# drawdown basis. An Accumulation SIPP has nothing to withdraw, so the option is not offered.
_WITHDRAWAL_ALLOWED = _cond_any(
    _ACCOUNT_IS_NOT_PENSION,
    _cond("crystallisation_status", "notEquals", "accumulation"),
)
# A third-party provider is a property of THREE wrappers — third-party onshore bond, offshore bond
# and pension — and of no other. The whole section was unconditional, so every GIA and ISA was
# asked whether it uses one; the fields UNDER the checkbox were already conditional, which is what
# made the section itself look intended rather than overlooked.
_ACCOUNT_IS_THIRD_PARTY = _cond(
    "account.product_type", "in", ["TPONBOND", "TPOFFBOND", "TPPENSION"]
)
_HAS_THIRD_PARTY_PROVIDER = _cond("has_third_party_provider", "equals", True)
# The raise drawer asks this same sentence. A pension transfer, including a third-party
# pension. Unchecked is No.
SAFEGUARDED_BENEFITS_QUESTION = (
    "Does the pension that is being transferred contain Safeguarded Rights "
    "(as defined in Section 48(8) of the Pension Schemes Act 2015)?"
)
SAFEGUARDED_FCA_INFO = (
    "The FCA reference number of the advice firm that provided the advice "
    "around transferring the pension with Safeguarded Rights."
)
# A firm can tick a product as a pension without using one of these codes. The form
# context carries that flag beside the code.
_PENSION_ACCOUNT = _cond_any(
    _cond("account.product_type", "in", ["SIPP", "PENSION", "SSAS", "TPPENSION"]),
    _cond("account.is_pension", "equals", True),
)
_PENSION_WITH_TRANSFER = _cond_all(
    _cond("has_transfer", "equals", True),
    _PENSION_ACCOUNT,
)
_SAFEGUARDED_TRANSFER = _cond_all(
    _cond("has_transfer", "equals", True),
    _PENSION_ACCOUNT,
    _cond("safeguarded_benefits", "equals", True),
)
# The mirror of `_WITHDRAWAL_ALLOWED`: a SIPP in drawdown is paying OUT, so the initial-cash and
# expected-payment-in questions are not offered on a drawdown basis (any non-pension still asks
# them). `crystallisation_status` carries the basis — Accumulation or Drawdown.
_PAYMENTS_ALLOWED = _cond_any(
    _ACCOUNT_IS_NOT_PENSION,
    _cond("crystallisation_status", "notEquals", "drawdown"),
)
_DRAWDOWN_BASIS = _cond_all(
    _ACCOUNT_IS_PENSION,
    _cond("crystallisation_status", "equals", "drawdown"),
)
# The crystallised split is a fact of a pension transfer taken in drawdown — the counterpart of
# the account's own transfer drawer, captured while the journey is worked.
_DRAWDOWN_WITH_TRANSFER = _cond_all(
    _DRAWDOWN_BASIS,
    _cond("has_transfer", "equals", True),
)
_ACCOUNT_PREFERENCE_FIELDS: list[tuple[Any, ...]] = [
    (
        "account_name",
        "text",
        "Account name",
        "Investment details",
        True,
        None,
        {"minLength": 2, "width": 6},
        None,
    ),
    (
        "currency",
        "select",
        "Default Account Settlement Currency",
        "Investment details",
        True,
        _ACCOUNT_ASKS_CURRENCY,
        {"width": 6},
        {"source": "currencies", "valueKey": "code", "labelKey": "label"},
    ),
    (
        "investment_capacity",
        "select",
        "Investment capacity",
        "Investment details",
        True,
        None,
        {"width": 6},
        {
            "options": [
                {"value": "advisory", "label": "Advisory"},
                {"value": "discretionary", "label": "Discretionary"},
                {"value": "execution_only", "label": "Execution only"},
            ]
        },
    ),
    (
        "model_id",
        "select",
        "Investment model",
        "Investment details",
        False,
        None,
        {"width": 6},
        {"source": "models", "valueKey": "id", "labelKey": "name"},
    ),
    (
        "dividend_treatment",
        "select",
        "Dividend treatment",
        "Investment details",
        False,
        _ACCOUNT_IS_NOT_PENSION,
        {"width": 6},
        {
            "options": [
                {"value": "reinvest", "label": "Reinvest"},
                {"value": "cash", "label": "Cash"},
                # Pay away is an Epworth option. The field still carries it so an Epworth
                # custodian can offer it; the form hides it for every other custodian.
                {"value": "pay_away", "label": "Pay away"},
            ]
        },
    ),
    # Preferred settlement currency (SEI "Security" vs "Account base") is HIDDEN for now while SEI
    # enable the API for it — advisers were unsure what "Security" meant and M&G are hard-coding the
    # currency. Restore this tuple (visibility `_ACCOUNT_IS_SEI`) once the SEI API is live.
    # (
    #     "dividend_settlement_currency",
    #     "select",
    #     "Preferred settlement currency",
    #     "Investment details",
    #     False,
    #     _ACCOUNT_IS_SEI,
    #     {"width": 6},
    #     {"options": [{"value": "security", "label": "Security"},
    #                  {"value": "account_base", "label": "Account base"}]},
    # ),
    (
        "crystallisation_status",
        "segmented",
        "Basis",
        "Pension details",
        False,
        _ACCOUNT_IS_PENSION,
        {"width": 6},
        {
            "options": [
                {"value": "accumulation", "label": "Accumulation"},
                {"value": "drawdown", "label": "Drawdown"},
            ]
        },
    ),
    (
        "access_option",
        "segmented",
        "Access option",
        "Pension details",
        False,
        _DRAWDOWN_BASIS,
        {"width": 6},
        {
            "options": [
                {"value": "ufpls", "label": "UFPLS"},
                {"value": "fad", "label": "FAD"},
            ]
        },
    ),
    (
        "money_purchase_trigger",
        "segmented",
        "Money purchase annual allowance triggered?",
        "Pension details",
        False,
        _ACCOUNT_IS_PENSION,
        {"width": 6},
        _opts(["Yes", "No"]),
    ),
    (
        "money_purchase_trigger_date",
        "date",
        "Money purchase trigger date",
        "Pension details",
        False,
        {
            "logic": "and",
            "conditions": [
                _ACCOUNT_IS_PENSION,
                _cond("money_purchase_trigger", "equals", "Yes"),
            ],
        },
        {"width": 6},
        None,
    ),
    (
        "sipp_employer_scheme",
        "segmented",
        "Result of opting out or not joining an employer scheme?",
        "Pension details",
        False,
        _ACCOUNT_IS_PENSION,
        {"width": 6},
        _opts(["Yes", "No"]),
    ),
    (
        "fee_package_ids",
        "multiSelect",
        "Fee packages",
        "Fees",
        False,
        None,
        {"width": 12},
        {"source": "fee_packages", "valueKey": "id", "labelKey": "name"},
    ),
    (
        "has_initial_cash",
        "checkbox",
        "Make an initial cash deposit",
        "Funding",
        False,
        _PAYMENTS_ALLOWED,
        {"width": 6, "column": "left"},
        None,
    ),
    (
        "initial_cash_amount",
        "currency",
        "Initial cash amount",
        "Funding",
        True,
        _cond_all(_cond("has_initial_cash", "equals", True), _PAYMENTS_ALLOWED),
        {"width": 12, "column": "right"},
        None,
    ),
    (
        "has_transfer",
        "checkbox",
        "Transfer in from another provider",
        "Funding",
        False,
        None,
        {"width": 6, "column": "left"},
        None,
    ),
    (
        "transfer_type",
        "select",
        "Transfer type",
        "Funding",
        True,
        _cond("has_transfer", "equals", True),
        {"width": 6, "column": "right"},
        {
            "options": [
                {"value": "cash", "label": "Cash"},
                {"value": "in_specie", "label": "In-specie"},
            ]
        },
    ),
    (
        "transfer_extent",
        "select",
        "Full or partial transfer?",
        "Funding",
        True,
        _cond("has_transfer", "equals", True),
        {"width": 6, "column": "right"},
        {
            "options": [
                {"value": "full", "label": "Full"},
                {"value": "partial", "label": "Partial"},
            ]
        },
    ),
    (
        # A combobox, not a plain select: the ceding-provider register runs to hundreds of names,
        # so it is typed-to-search here exactly as the account's own transfer drawer does, rather
        # than a scroll through a fixed list.
        "transfer_provider",
        "combobox",
        "Transfer provider",
        "Funding",
        False,
        _cond("has_transfer", "equals", True),
        {"width": 6, "column": "right"},
        {"source": "transfer_providers", "valueKey": "id", "labelKey": "name"},
    ),
    (
        "transfer_amount",
        "currency",
        "Estimated transfer amount",
        "Funding",
        False,
        _cond("has_transfer", "equals", True),
        {"width": 6, "column": "right"},
        None,
    ),
    (
        "transfer_reference",
        "text",
        "Transfer reference",
        "Funding",
        False,
        _cond("has_transfer", "equals", True),
        {"width": 6, "column": "right"},
        None,
    ),
    (
        # The ceding scheme's product type — the Morgan Lloyd `cam_pensiontype` codes, the same
        # list the account's own transfer drawer offers (pkgs/ui transfers/api.ts
        # PENSION_TYPE_OPTIONS). Kept in step with that file.
        "pension_type",
        "select",
        "Pension type",
        "Funding",
        False,
        _PENSION_WITH_TRANSFER,
        {"width": 6, "column": "right"},
        {
            "options": [
                {"value": "1", "label": "Additional Voluntary Contribution"},
                {"value": "2", "label": "Annuity"},
                {"value": "3", "label": "APPP - Protected Rights"},
                {"value": "4", "label": "Bulk Buy-Out Plan"},
                {"value": "5", "label": "Contracted In Money Purchase"},
                {"value": "6", "label": "Contracted Out Money Purchase"},
                {"value": "7", "label": "Drawdown"},
                {"value": "8", "label": "Executive Personal Pension"},
                {"value": "9", "label": "Final Salary Scheme"},
                {"value": "10", "label": "Free Standing Additional Voluntary Contribution"},
                {"value": "11", "label": "Group Personal Pension"},
                {"value": "12", "label": "Group Stakeholder Pension"},
                {"value": "13", "label": "Other"},
                {"value": "14", "label": "Personal Pension Plan"},
                {"value": "15", "label": "QROPS"},
                {"value": "16", "label": "Retirement Annuity Contract"},
                {"value": "17", "label": "Section 32 Policy Buy Out Bond"},
                {"value": "18", "label": "Self Invested Personal Pension"},
                {"value": "19", "label": "Small Self Administered Scheme"},
                {"value": "20", "label": "Stakeholder"},
            ]
        },
    ),
    (
        # A drawdown transfer may already be crystallised at the ceding scheme. Yes reveals the
        # crystallised portion; No is a wholly uncrystallised transfer.
        "is_crystallised",
        "segmented",
        "Crystallised",
        "Funding",
        False,
        _DRAWDOWN_WITH_TRANSFER,
        {"width": 6, "column": "right"},
        _opts(["Yes", "No"]),
    ),
    (
        "crystallised_amount",
        "currency",
        "Crystallised amount",
        "Funding",
        False,
        _cond_all(_DRAWDOWN_WITH_TRANSFER, _cond("is_crystallised", "equals", "Yes")),
        {"width": 6, "column": "right"},
        None,
        "Portion of total transfer amount which is crystallised",
    ),
    (
        "safeguarded_benefits",
        "checkbox",
        SAFEGUARDED_BENEFITS_QUESTION,
        "Funding",
        False,
        _PENSION_WITH_TRANSFER,
        {"width": 12, "column": "right"},
        None,
    ),
    (
        "fca_reference",
        "text",
        "FCA reference",
        "Funding",
        True,
        _SAFEGUARDED_TRANSFER,
        {"width": 12, "column": "right"},
        None,
        SAFEGUARDED_FCA_INFO,
    ),
    (
        "has_contribution",
        "checkbox",
        "Expect a payment in",
        "Funding",
        False,
        _PAYMENTS_ALLOWED,
        {"width": 6, "column": "left", "contributionLabel": "Expect a contribution"},
        None,
    ),
    (
        "contribution_amount",
        "currency",
        "Payment in amount",
        "Funding",
        True,
        _cond_all(_cond("has_contribution", "equals", True), _PAYMENTS_ALLOWED),
        {"width": 4, "column": "right", "contributionLabel": "Contribution amount"},
        None,
    ),
    (
        "contribution_frequency",
        "select",
        "Frequency",
        "Funding",
        True,
        _cond_all(_cond("has_contribution", "equals", True), _PAYMENTS_ALLOWED),
        {"width": 4, "column": "right"},
        {
            "options": [
                {"value": "single", "label": "Single"},
                {"value": "monthly", "label": "Monthly"},
                {"value": "quarterly", "label": "Quarterly"},
                {"value": "half_yearly", "label": "Half-yearly"},
                {"value": "annually", "label": "Annually"},
            ]
        },
    ),
    (
        "contribution_expected_by",
        "date",
        "Expected by",
        "Funding",
        False,
        _cond_all(_cond("has_contribution", "equals", True), _PAYMENTS_ALLOWED),
        {"width": 4, "column": "right"},
        None,
    ),
    (
        "has_regular_withdrawal",
        "checkbox",
        "Set up regular withdrawals",
        "Funding",
        False,
        _WITHDRAWAL_ALLOWED,
        {"width": 6, "column": "left"},
        None,
    ),
    (
        "withdrawal_amount",
        "currency",
        "Withdrawal amount",
        "Funding",
        True,
        _cond_all(_cond("has_regular_withdrawal", "equals", True), _WITHDRAWAL_ALLOWED),
        {"width": 4, "column": "right"},
        None,
    ),
    (
        "withdrawal_frequency",
        "select",
        "Withdrawal frequency",
        "Funding",
        True,
        _cond_all(_cond("has_regular_withdrawal", "equals", True), _WITHDRAWAL_ALLOWED),
        {"width": 4, "column": "right"},
        {
            "options": [
                {"value": "monthly", "label": "Monthly"},
                {"value": "quarterly", "label": "Quarterly"},
                {"value": "half_yearly", "label": "Half-yearly"},
                {"value": "annually", "label": "Annually"},
            ]
        },
    ),
    (
        "withdrawal_start_date",
        "date",
        "First withdrawal date",
        "Funding",
        True,
        _cond_all(_cond("has_regular_withdrawal", "equals", True), _WITHDRAWAL_ALLOWED),
        {"width": 4, "column": "right"},
        None,
    ),
    (
        "has_third_party_provider",
        "checkbox",
        "This account is held through a third-party provider",
        "Third-party provider",
        True,
        _ACCOUNT_IS_THIRD_PARTY,
        {"width": 12},
        None,
    ),
    (
        "third_party_provider",
        "text",
        "Provider name",
        "Third-party provider",
        True,
        _cond_all(_ACCOUNT_IS_THIRD_PARTY, _HAS_THIRD_PARTY_PROVIDER),
        {"width": 6},
        None,
    ),
    (
        "third_party_account_name",
        "text",
        "Provider's account holder name",
        "Third-party provider",
        False,
        _cond_all(_ACCOUNT_IS_THIRD_PARTY, _HAS_THIRD_PARTY_PROVIDER),
        {"width": 6},
        None,
    ),
    (
        "third_party_account_number",
        "bankAccountNumber",
        "Provider's account number",
        "Third-party provider",
        False,
        _cond_all(_ACCOUNT_IS_THIRD_PARTY, _HAS_THIRD_PARTY_PROVIDER),
        {"width": 6},
        None,
    ),
    (
        "third_party_sort_code",
        "sortCode",
        "Provider's sort code",
        "Third-party provider",
        False,
        _cond_all(_ACCOUNT_IS_THIRD_PARTY, _HAS_THIRD_PARTY_PROVIDER),
        {"width": 6},
        None,
    ),
    (
        "beneficiaries",
        "beneficiaries",
        "Beneficiaries",
        "Pension beneficiaries",
        False,
        _ACCOUNT_IS_PENSION,
        {"width": 12},
        None,
    ),
    (
        "account_activity_nature",
        "textarea",
        "Nature of activities",
        "Account activity",
        True,
        _ACCOUNT_IS_OFFSHORE,
        {"width": 12, "rows": 3},
        None,
    ),
    (
        "account_activity_standing_orders",
        "text",
        "Standing order instructions",
        "Account activity",
        True,
        _ACCOUNT_IS_OFFSHORE,
        {"width": 12},
        None,
    ),
    (
        "account_activity_funds_in",
        "currency",
        "Estimated annual funds in",
        "Account activity",
        True,
        _ACCOUNT_IS_OFFSHORE,
        {"width": 6},
        None,
    ),
    (
        "account_activity_funds_out",
        "currency",
        "Estimated annual funds out",
        "Account activity",
        True,
        _ACCOUNT_IS_OFFSHORE,
        {"width": 6},
        None,
    ),
    (
        "account_activity_max_turnover",
        "percentage",
        "Estimated maximum turnover",
        "Account activity",
        True,
        _ACCOUNT_IS_OFFSHORE,
        {"width": 6},
        None,
    ),
    (
        "account_activity_transaction_scale",
        "currency",
        "Estimated transaction scale",
        "Account activity",
        True,
        _ACCOUNT_IS_OFFSHORE,
        {"width": 6},
        None,
    ),
]

# form code -> (name, entity_type, fields)
FORMS: dict[str, tuple[str, str, list[tuple[Any, ...]]]] = {
    "applicant_core": ("Applicant details", "applicant", _CORE_FIELDS),
    "jisa_child": ("Child details", "applicant", _JISA_CHILD_FIELDS),
    "jisa_registered_contact": (
        "Registered contact details",
        "applicant",
        _JISA_REGISTERED_CONTACT_FIELDS,
    ),
    # THE CODE STAYS `applicant_offshore` DELIBERATELY. It is the key this seed upserts forms on
    # (`WHERE tenant_id = :t AND code = :c`), so renaming it mints a SECOND form on every tenant
    # that already has one and orphans every answer captured against the first. The name, the step
    # and the route were the parts a person reads, and those are corrected; the code is internal
    # and reaches no URL. Renaming it needs a migration, not an edit here.
    "applicant_offshore": ("Additional information", "applicant", _ADDITIONAL_FIELDS),
    "applicant_pension": ("Pension information", "applicant", _PENSION_FIELDS),
    "applicant_employer": ("Employer contributions", "applicant", _EMPLOYER_FIELDS),
    "organisation_core": ("Organisation details", "applicant", _ORG_FIELDS),
    "account_preferences": ("Account details", "account", _ACCOUNT_PREFERENCE_FIELDS),
}

# The mapping the core fact-find writes back to crm.contacts (the profile IS the client). Only the
# whitelisted columns in step_handlers._write_contact_patch are set as columns; ``address.*`` /
# ``bank_account.*`` merge into those jsonb columns; anything else folds into crm.contacts.data.
CORE_MAPPING: list[dict[str, Any]] = [
    {"source": "first_name", "target": "first_name", "transforms": ["trim"]},
    {"source": "middle_name", "target": "middle_name", "transforms": ["trim"]},
    {"source": "surname", "target": "surname", "transforms": ["trim"]},
    {"source": "title", "target": "title", "transforms": ["trim"]},
    {"source": "email", "target": "email", "transforms": ["trim", "lower"]},
    {"source": "phone", "target": "mobile", "transforms": ["trim"]},
    # No transforms: a leftover answer is still a list, and the mapper refuses a transform on a
    # structured value. `contact_phone` takes the first number of that list, or the string a
    # phoneNumber field posts now.
    {"source": "other_numbers", "target": "phone"},
    {"source": "dob", "target": "dob"},
    {"source": "gender", "target": "gender"},
    {"source": "nationality", "target": "nationality"},
    {"source": "tax_residency", "target": "tax_residency"},
    {
        "source": "national_identifier",
        "target": "national_identifier",
        "transforms": ["trim", "upper"],
    },
    {"source": "national_identifier_type", "target": "national_identifier_type"},
    {
        "source": "uk_national_insurance",
        "target": "uk_national_insurance",
        "transforms": ["trim", "upper"],
    },
    {"source": "address.line1", "target": "address.line1", "transforms": ["trim"]},
    {"source": "address.line2", "target": "address.line2", "transforms": ["trim"]},
    {"source": "address.city", "target": "address.city", "transforms": ["trim"]},
    {"source": "address.region", "target": "address.region", "transforms": ["trim"]},
    {"source": "address.postcode", "target": "address.postcode", "transforms": ["postcode"]},
    {"source": "address.country", "target": "address.country", "transforms": ["trim"]},
    {
        "source": "bank_account.account_name",
        "target": "bank_account.name",
        "transforms": ["trim"],
    },
    {
        "source": "bank_account.account_number",
        "target": "bank_account.account_number",
        "transforms": ["digits"],
    },
    {
        "source": "bank_account.sort_code",
        "target": "bank_account.sort_code",
        "transforms": ["digits"],
    },
    {
        "source": "bank_account.iban",
        "target": "bank_account.iban",
        "transforms": ["trim", "upper"],
    },
    {
        "source": "bank_account.bic",
        "target": "bank_account.bic",
        "transforms": ["trim", "upper"],
    },
    {
        "source": "bank_account.method",
        "target": "bank_account.method",
        "transforms": ["trim", "lower"],
    },
    {"source": "is_vulnerable", "target": "is_vulnerable"},
    {"source": "vulnerability_reasons", "target": "vulnerability_reasons"},
    {"source": "vulnerability_characteristics", "target": "vulnerability_characteristics"},
    {"source": "vulnerability_notes", "target": "vulnerability_notes", "transforms": ["trim"]},
]

JISA_CHILD_MAPPING = [
    mapping for mapping in CORE_MAPPING if mapping["source"] in _JISA_CHILD_FIELD_KEYS
]
JISA_REGISTERED_CONTACT_MAPPING = [
    mapping
    for mapping in CORE_MAPPING
    if mapping["source"] in _JISA_REGISTERED_CONTACT_FIELD_KEYS
    or str(mapping["source"]).startswith("address.")
]

# Organisation fact-find → the same address / bank_account jsonb columns on the org contact.
ORG_MAPPING = [
    {"source": "address.line1", "target": "address.line1", "transforms": ["trim"]},
    {"source": "address.line2", "target": "address.line2", "transforms": ["trim"]},
    {"source": "address.city", "target": "address.city", "transforms": ["trim"]},
    {"source": "address.region", "target": "address.region", "transforms": ["trim"]},
    {"source": "address.postcode", "target": "address.postcode", "transforms": ["postcode"]},
    {"source": "address.country", "target": "address.country", "transforms": ["trim"]},
    {
        "source": "bank_account.account_name",
        "target": "bank_account.name",
        "transforms": ["trim"],
    },
    {
        "source": "bank_account.account_number",
        "target": "bank_account.account_number",
        "transforms": ["digits"],
    },
    {
        "source": "bank_account.sort_code",
        "target": "bank_account.sort_code",
        "transforms": ["digits"],
    },
    {
        "source": "bank_account.iban",
        "target": "bank_account.iban",
        "transforms": ["trim", "upper"],
    },
    {
        "source": "bank_account.bic",
        "target": "bank_account.bic",
        "transforms": ["trim", "upper"],
    },
    {
        "source": "bank_account.method",
        "target": "bank_account.method",
        "transforms": ["trim", "lower"],
    },
]


# The pension fact-find's three answers, which 0053 added columns for BECAUSE onboarding collects
# them as required questions — and then nothing wrote to them. `snake` canonicalises the display
# label the combobox shows ("Self-Employed") into the value everything downstream expects
# ("self_employed"): the columns are plain `text`, so the database would accept the label silently
# and the client details form's own Literals would then not match it.
PENSION_MAPPING = [
    {"source": "pension.maritalStatus", "target": "marital_status", "transforms": ["snake"]},
    {"source": "pension.employmentStatus", "target": "employment_status", "transforms": ["snake"]},
    {"source": "pension.retirementAge", "target": "retirement_age", "transforms": ["digits"]},
]

# Offshore enhanced KYC. EVERY ONE OF THESE NOW HAS A COLUMN except `pep` (0116) — the note this
# replaced said "`source_of_wealth` is a real column; the rest have none and fold into
# `crm.contacts.data` ... an answer in `data` is at least ON the client, where a form response is
# not", which was the right thing to do about a shortfall and not a reason to leave it standing.
# `data` is the right home for a firm's OWN added questions; it is the wrong home for a fact the
# platform asks for, shows on a profile and sends to a custodian.
#
# TWO PAIRS COLLAPSE ONTO ONE COLUMN EACH, and safely: `occupation_retired` and
# `occupation_other` are asked under mutually exclusive conditions (Retired / Other), so at most
# one is ever answered — and `apply_mapping` omits an absent source from the patch rather than
# writing a null over the other. The source-of-wealth statement is different: it is always asked
# alongside the closed category and maps to its own detail column.
#
# `pep` stays in `data`: a self-declaration and a screening outcome are different facts with
# different rules, and 0116 declines to put it one careless join away from being read as an
# adjudication.
ADDITIONAL_MAPPING = [
    {"source": "source_of_wealth", "target": "source_of_wealth", "transforms": ["trim"]},
    {
        "source": "source_of_wealth_other",
        "target": "source_of_wealth_detail",
        "transforms": ["trim"],
    },
    {"source": "country_of_birth", "target": "country_of_birth"},
    {"source": "town_of_birth", "target": "town_of_birth", "transforms": ["trim"]},
    {"source": "occupation", "target": "occupation"},
    {"source": "occupation_retired", "target": "occupation_detail", "transforms": ["trim"]},
    {"source": "occupation_other", "target": "occupation_detail", "transforms": ["trim"]},
    {"source": "source_of_funds", "target": "source_of_funds"},
    {
        "source": "source_of_funds_other",
        "target": "source_of_funds_detail",
        "transforms": ["trim"],
    },
    # Yes / No, and separately the W-9 or W-8 held. The write stamps the certification date
    # and refuses a pair that disagrees.
    {"source": "us_person", "target": "us_person"},
    {"source": "fatca_status", "target": "fatca_status"},
    {"source": "pep", "target": "pep"},
]

#: What Additional information keeps in the journey's case memory: the W-9 / W-8 answer, which the
#: `needs_fatca_form` fork reads. Nothing else on the form is copied there.
FATCA_REMEMBER = {"fatca_status": "fatca_status"}

#: The answers for which the client is asked to upload the signed form (`app.fatca.FORM_NAMES`).
FATCA_SIGNED_FORM_ANSWERS = ("w9", "w8")


# Employer contributions. The answers the client record's Employer card shows go to ITS columns —
# `employer_contributions` (the gate), `employer_name`, `employer_registration_number` and the
# `employer_address` jsonb (0139) — and `_write_contact_patch` routes them there. They used to
# nest under `crm.contacts.data.pension.employerContributions.*`, where the legacy app kept them
# and where nothing on the record read them: the fact-find asked for the employer, the adviser
# answered, and the Employer card still showed three em dashes with "HasEmployerContributions:
# Yes" filed under "more from the fact-find". 0210 backfills the rows written that way.
#
# The employer's BANK details go to `employer_bank_account` (0271), the same jsonb shape as the
# client's own `bank_account`, so the Employer card shows and edits them with the one bank editor
# and the one directory check; they used to fold into `data` as two bare digit strings. 0271
# backfills the rows written that way. Organisation address/bank map via ORG_MAPPING into the
# real `address` / `bank_account` jsonb columns (same path shape as CORE_MAPPING).
EMPLOYER_MAPPING = [
    {
        "source": "pension.employerContributions.sortCode",
        "target": "employer_bank_account.sort_code",
        "transforms": ["digits"],
    },
    {
        "source": "pension.employerContributions.accountNumber",
        "target": "employer_bank_account.account_number",
        "transforms": ["digits"],
    },
    {
        "source": "pension.hasEmployerContributions",
        "target": "employer_contributions",
    },
    {
        "source": "pension.employerContributions.name",
        "target": "employer_name",
        "transforms": ["trim"],
    },
    {
        "source": "pension.employerContributions.registrationNumber",
        "target": "employer_registration_number",
        "transforms": ["trim", "upper"],
    },
    {
        "source": "employer_address.line1",
        "target": "employer_address.line1",
        "transforms": ["trim"],
    },
    {
        "source": "employer_address.line2",
        "target": "employer_address.line2",
        "transforms": ["trim"],
    },
    {"source": "employer_address.city", "target": "employer_address.city", "transforms": ["trim"]},
    {
        "source": "employer_address.region",
        "target": "employer_address.region",
        "transforms": ["trim"],
    },
    {
        "source": "employer_address.postcode",
        "target": "employer_address.postcode",
        "transforms": ["postcode"],
    },
    {
        "source": "employer_address.country",
        "target": "employer_address.country",
        "transforms": ["trim"],
    },
]


async def _write_form_fields(
    session: AsyncSession, tenant_id: int, form_id: int, fields: list[tuple[Any, ...]]
) -> None:
    """Replace a form's field rows with the seed definition.

    Seeded forms are platform-owned configuration — re-running seed must pick up Phase 0 field
    changes (address / nationalIdentifier / gender / ISO nationalities) without requiring
    ``--reset``. In-flight answers keep their stored keys; a missing field just stops collecting.
    """
    await session.execute(
        text("DELETE FROM documents.form_fields WHERE form_id = :f AND tenant_id = :t"),
        {"f": form_id, "t": tenant_id},
    )
    for order, field in enumerate(fields):
        key, ftype, label, section, required, conditions, validation, options = field[:8]
        description = field[8] if len(field) > 8 else None
        authored_validation = dict(validation or {})
        authored_validation.setdefault(
            "width",
            12 if ftype in {"textarea", "address", "beneficiaries", "multiSelect"} else 6,
        )
        await session.execute(
            text(
                "INSERT INTO documents.form_fields "
                "  (uid, tenant_id, form_id, field_key, field_type, label, section, description, "
                "   required, conditions, validation, options, sort_order) "
                "VALUES (:u, :t, :f, :k, :ft, :l, :sec, :desc, :req, CAST(:cond AS jsonb), "
                "  CAST(:val AS jsonb), CAST(:opt AS jsonb), :o)"
            ),
            {
                "u": new_id(),
                "t": tenant_id,
                "f": form_id,
                "k": key,
                "ft": ftype,
                "l": label,
                "sec": section,
                "desc": description,
                "req": required,
                "cond": json.dumps({"conditional": conditions}) if conditions else None,
                "val": json.dumps(authored_validation),
                "opt": json.dumps(options) if options else None,
                "o": order,
            },
        )


async def _ensure_form(session: AsyncSession, tenant_id: int, code: str) -> str:
    name, entity, fields = FORMS[code]
    existing = (
        (
            await session.execute(
                text("SELECT id, uid FROM documents.forms WHERE tenant_id = :t AND code = :c"),
                {"t": tenant_id, "c": code},
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing:
        await _write_form_fields(session, tenant_id, int(existing["id"]), fields)
        return str(existing["uid"])
    form_uid = new_id()
    form_id = (
        await session.execute(
            text(
                "INSERT INTO documents.forms (uid, tenant_id, code, name, status, entity_type) "
                "VALUES (:u, :t, :c, :n, 'published', :e) RETURNING id"
            ),
            {"u": form_uid, "t": tenant_id, "c": code, "n": name, "e": entity},
        )
    ).scalar_one()
    await _write_form_fields(session, tenant_id, int(form_id), fields)
    return form_uid


async def _ensure_term(
    session: AsyncSession, tenant_id: int, code: str, name: str, content: str, scope: dict[str, Any]
) -> str:
    """A firm terms catalogue row — published once, then referenced by accept_terms steps."""
    from app import terms as terms_svc

    applicability: dict[str, Any] = {"always": True} if scope.get("always") else {}
    if scope.get("when") == "has_transfer":
        applicability = {"has_transfer": True}
    if scope.get("when_product") == "ISA":
        applicability = {"products": ["ISA", "JISA"]}
    return await terms_svc.ensure_published_term(
        session,
        owner="tenant",
        tenant_id=tenant_id,
        code=code,
        title=name,
        content=content,
        audience="client",
        required_at=["onboarding"],
        applicability=applicability,
    )


def _legacy_definition(forms: dict[str, str], terms: dict[str, str]) -> dict[str, Any]:
    """The pre-v3 ``{steps: [...]}`` onboarding definition — **not called in production**.

    It exists to be compared against, and what it now proves is narrower than it once did. The
    journey ships as a wired spine (``_build_definition``), whose plan is deliberately NOT this
    one's: a spine compiles its wires into prerequisites, so work this shape offered in parallel is
    now reached one node at a time. What the comparison still pins is that the SAME WORK is there —
    the same step ids, types, scopes, assignees and config — so a rebuild cannot quietly drop a
    terms declaration or re-assign a fact-find while changing the order
    (``tests/test_flows_graph.py``).

    Keep the two in lockstep. A step added to one and not the other fails that equality
    immediately, which is the point of holding on to it.

    Participant tier: core fact-find (all) + additional info (individuals) + pension (pension
    products) + employer contributions (SIPP) + organisation fact-find (organisations); identity +
    AML for KYC-relevant individuals (AML depends on identity). Account tier: offshore activity
    (offshore), per-product terms acknowledgements (general/DFM/platform always; ISA + transfer
    conditional), an offshore EDD review (offshore), then account setup — gated on the owners' KYC
    via the engine's cross-case gate.

    **The actor split is the legacy app's, not an invention.** The old Next.js onboarding exposed
    exactly one unauthenticated surface to the client
    (``apps/platform/app/a/[applicationId]/[applicantId]``), and its only *mutating* public
    endpoints were ``…/terms`` and ``…/identity``. Every other page on that journey rendered
    ``ViewField``/``DynamicFieldsDisplay`` — read-only review of what the adviser had already
    entered — and the fields route was GET-only (field *definitions*, not values). So: the adviser
    fills every fact-find; the client's recorded acts are the terms acknowledgements and (with a
    digital provider) identity. Nothing here is assigned to the client that the legacy client could
    not actually do.
    """
    individual = _cond("applicant.type", "equals", "individual")
    ordinary_applicant = _cond("applicant.onboarding_role", "equals", "applicant")
    jisa_child = _cond("applicant.onboarding_role", "equals", "jisa_child")
    jisa_registered_contact = _cond(
        "applicant.onboarding_role", "equals", "jisa_registered_contact"
    )
    account_offshore = _cond("account.custody", "equals", "offshore")
    # ISA declaration covers stocks-and-shares ISA and JISA (both wrappers). GIA/SIPP must not
    # plan it — runtime gates skip too late, and the client checklist listed pending ISA/Transfer
    # steps before the walk reached them. The transfer extra stays for the same reason: Account
    # details has not been answered yet. The walk creates that declaration once a transfer
    # exists (``orchestrator._materialize_omitted_step``).
    isa_wrapper = _cond("account.product", "in", ["ISA", "JISA"])

    def _and(*conds: dict[str, Any]) -> dict[str, Any]:
        return {"logic": "AND", "conditions": list(conds)}

    def terms_step(
        key: str, doc_code: str, title: str, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        # Client terms: individual parties only; on a JISA the registered contact, not the child.
        visible = _terms_party_visibility(extra)
        return {
            "id": key,
            "type": "accept_terms",
            "title": title,
            "scope": "account",
            "per_participant": True,
            "assignee": "client",
            "visibility": visible,
            "config": {"term_uid": terms[doc_code], "title": title, "category": "terms"},
        }

    return {
        "steps": [
            # -- participant tier -------------------------------------------------------------
            {
                "id": "core",
                "type": "fill_form",
                "title": "Applicant details",
                "scope": "participant",
                "assignee": "staff",
                "visibility": _and(individual, ordinary_applicant),
                "config": {
                    "form_uid": forms["applicant_core"],
                    "mode": "gather",
                    "mapping": CORE_MAPPING,
                    "title": "Applicant details",
                },
            },
            {
                "id": "jisa_child",
                "type": "fill_form",
                "title": "Child details",
                "scope": "participant",
                "assignee": "staff",
                "visibility": jisa_child,
                "config": {
                    "form_uid": forms["jisa_child"],
                    "mode": "gather",
                    "mapping": JISA_CHILD_MAPPING,
                    "title": "Child details",
                },
            },
            {
                "id": "jisa_registered_contact",
                "type": "fill_form",
                "title": "Registered contact details",
                "scope": "participant",
                "assignee": "staff",
                "visibility": jisa_registered_contact,
                "config": {
                    "form_uid": forms["jisa_registered_contact"],
                    "mode": "gather",
                    "mapping": JISA_REGISTERED_CONTACT_MAPPING,
                    "title": "Registered contact details",
                },
            },
            {
                "id": "org",
                "type": "fill_form",
                "title": "Organisation details",
                "scope": "participant",
                "assignee": "staff",
                "visibility": _cond("applicant.type", "equals", "organisation"),
                "config": {
                    "form_uid": forms["organisation_core"],
                    "mode": "gather",
                    "mapping": ORG_MAPPING,
                    "title": "Organisation details",
                },
            },
            {
                # WAS `offshore_kyc`, AND THE NAME WAS WRONG RATHER THAN MERELY DATED: the step's
                # visibility is "is this applicant an individual", never custody. 0116 gave these
                # answers (country/town of birth, occupation, source of funds and wealth) their own
                # columns because the platform asks EVERY individual for them, shows them on the
                # record and sends them to Morgan Lloyd — so the step has been correct for every
                # onshore application all along and only its key said otherwise.
                #
                # A step id is a URL: the adviser filling this in on a Third-party ONSHORE bond was
                # looking at `/journeys/…/steps/…/offshore_kyc`, which reads as the journey having
                # taken a branch it did not take. The offshore gate is real and lives elsewhere —
                # `offshore_review` and the account-activity fields both test
                # `account.custody = 'offshore'` (see `_ACCOUNT_IS_OFFSHORE`, correctly named).
                "id": "additional_info",
                "type": "fill_form",
                "title": "Additional information",
                "scope": "participant",
                "assignee": "staff",
                "visibility": _and(individual, ordinary_applicant),
                "config": {
                    "form_uid": forms["applicant_offshore"],
                    "mapping": ADDITIONAL_MAPPING,
                    "title": "Additional information",
                },
            },
            {
                "id": "pension",
                "type": "fill_form",
                "title": "Pension information",
                "scope": "participant",
                "assignee": "staff",
                "visibility": _and(
                    ordinary_applicant,
                    _cond("applicant.has_pension_product", "equals", True),
                ),
                "config": {
                    "form_uid": forms["applicant_pension"],
                    "mapping": PENSION_MAPPING,
                    "title": "Pension information",
                },
            },
            {
                "id": "employer",
                "type": "fill_form",
                "title": "Employer contributions",
                "scope": "participant",
                "assignee": "staff",
                "visibility": _and(
                    ordinary_applicant,
                    _cond("applicant.has_sipp_product", "equals", True),
                ),
                "config": {
                    "form_uid": forms["applicant_employer"],
                    "mapping": EMPLOYER_MAPPING,
                    "title": "Employer contributions",
                },
            },
            {
                # Starts with the client where a digital check exists — it is their document and
                # their choice — and they can hand it to a person instead, which moves the step to
                # staff. Straight to staff where the deployment offers no digital check at all.
                # See `_idv_assignee`.
                "id": "idv",
                "type": "verify_identity",
                "title": "Verify identity",
                "scope": "participant",
                "assignee": _idv_assignee(_idv_provider()),
                "depends_on": ["core"],
                "config": {"provider": _idv_provider()},
            },
            {
                "id": "aml",
                "type": "aml_check",
                "title": "AML screening",
                "scope": "participant",
                "assignee": "staff",
                "depends_on": ["idv"],
                "config": {},
            },
            # -- account tier -----------------------------------------------------------------
            {
                "id": "account_preferences",
                "type": "fill_form",
                "title": "Account details",
                "scope": "account",
                "assignee": "staff",
                "config": {
                    "form_uid": forms["account_preferences"],
                    "mode": "account_preferences",
                    "title": "Account details",
                },
            },
            terms_step("terms_general", "general_declaration", "General declaration"),
            terms_step(
                "terms_isa",
                "isa_declaration",
                "ISA declaration",
                extra=isa_wrapper,
            ),
            terms_step(
                "terms_transfer",
                "transfer_declaration",
                "Transfer declaration",
                extra=_cond("account.has_transfer", "equals", True),
            ),
            terms_step("terms_platform", "platform_terms", "Platform terms"),
            {
                "id": "offshore_review",
                "type": "review",
                "title": "Offshore compliance review",
                "scope": "account",
                "assignee": "staff",
                "visibility": account_offshore,
                "config": {
                    "gate": "offshore_approved",
                    "category": "offshore",
                    "title": "Offshore compliance review",
                },
            },
            {
                "id": "setup",
                "type": "setup_account",
                "title": "Open the account",
                "scope": "account",
                "assignee": "staff",
                "config": {},
            },
        ],
    }


# ==================================================================================================
# The v3 graph — the shape the seed actually ships
# ==================================================================================================
def _step(step: dict[str, Any], nxt: str | list[str] | None) -> dict[str, Any]:
    """One authored step plus its next node or parallel frontier."""
    return {**step, "kind": "step", "next": nxt}


def _gate(
    nid: str, title: str, when: dict[str, Any], *, then: str, otherwise: str | None
) -> dict[str, Any]:
    """A fork in the spine: a two-armed ``condition`` node.

    ``then`` is where the case goes when the condition holds, ``otherwise`` where it goes when it
    does not — and in this journey ``otherwise`` is almost always the node ``then`` itself leads to,
    which is the re-convergence: the path splits around one piece of work and comes back together.

    Minted through ``graph.condition_arms``, the same helper the converter and the builder use, so
    the seeded document and an authored one cannot be different shapes.
    """
    return {
        "id": nid,
        "kind": "condition",
        "title": title,
        "config": {"branches": condition_arms(when, when=then, otherwise=otherwise)},
        "position": None,
        "depends_on": [],
    }


def _build_definition(forms: dict[str, str], terms: dict[str, str]) -> dict[str, Any]:
    """The onboarding journey as a **v3 graph** — what every tenant is seeded with.

    The same work as ``_legacy_definition``, step for step: the same ids, types, ``scope``,
    ``assignee``, ``per_participant`` and ``config``. What changed in Aug 2026 is the ORDER and how
    it is said. The adviser fact-find is a short fork-and-join: the applicable initial
    person/organisation form runs first, then every other applicable adviser form is released
    together, and all of them converge on the declaration. Applicability is plan-time
    ``visibility`` because product and party type are already known at kickoff; irrelevant forms
    are never shown as incomplete work and later made to disappear.

    After the declaration the client/compliance work remains a spine. Its conditions are genuine
    runtime control flow, evaluated as each case reaches them.

    Participant tier: core fact-find (individuals) or the organisation fact-find, then additional
    information (individuals), pension information (pension products), employer contributions
    (SIPP), identity and AML. Account tier: account preferences, the per-product terms
    acknowledgements (general/DFM/platform for individual owners; ISA + transfer conditional on
    top), an offshore EDD review (offshore), then account setup — still gated on the owners' KYC by
    the engine's cross-case gate.

    **The actor split is the legacy app's, not an invention.** The old Next.js onboarding exposed
    exactly one unauthenticated surface to the client
    (``apps/platform/app/a/[applicationId]/[applicantId]``), and its only *mutating* public
    endpoints were ``…/terms`` and ``…/identity``. Every other page on that journey rendered
    ``ViewField``/``DynamicFieldsDisplay`` — read-only review of what the adviser had already
    entered — and the fields route was GET-only (field *definitions*, not values). So: the adviser
    fills every fact-find; the client's recorded acts are the terms acknowledgements and (with a
    digital provider) identity. Nothing here is assigned to the client that the legacy client could
    not actually do.
    """
    # THE TWO VOCABULARIES, and the seed is where the difference bites. A FORK is evaluated at
    # runtime against the CASE (`orchestrator.case_context`), whose subject is one contact, one
    # account or the application — so it speaks `contact.*` / `account.*` / `application.*`. A
    # step's `visibility` is evaluated at PLAN time, per participant instance
    # (`planning._participant_context`), which is the only place `applicant.*` exists. Neither can
    # answer the other's question, and using the wrong one is silent: an unresolved field is None,
    # which reads as "no" rather than as an error.
    ordinary_applicant = _cond("applicant.onboarding_role", "equals", "applicant")
    individual_visible = _cond_all(
        _cond("applicant.type", "equals", "individual"),
        ordinary_applicant,
    )
    jisa_child_visible = _cond("applicant.onboarding_role", "equals", "jisa_child")
    jisa_registered_contact_visible = _cond(
        "applicant.onboarding_role", "equals", "jisa_registered_contact"
    )
    organisation_visible = _cond("applicant.type", "equals", "organisation")
    pension_visible = _cond_all(
        ordinary_applicant,
        _cond("applicant.has_pension_product", "equals", True),
    )
    sipp_visible = _cond_all(
        ordinary_applicant,
        _cond("applicant.has_sipp_product", "equals", True),
    )
    account_offshore = _cond("account.custody", "equals", "offshore")
    fact_find = ["additional_info", "pension", "employer", "account_preferences"]

    def terms_step(
        key: str, doc_code: str, title: str, nxt: str, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        # Per-participant visibility (not a fork): asked once per party. On a JISA that is the
        # registered contact, not the child owner. Wrapper-specific extras (ISA / transfer) keep
        # those declarations off the plan until they apply, so a GIA never lists an ISA
        # declaration and a transfer is not pending work before Account details. The transfer
        # fact is written later; the walk creates ``terms_transfer`` when that fork is taken.
        return _step(
            {
                "id": key,
                "type": "accept_terms",
                "title": title,
                "scope": "account",
                "per_participant": True,
                "assignee": "client",
                "visibility": _terms_party_visibility(extra),
                "config": {"term_uid": terms[doc_code], "title": title, "category": "terms"},
            },
            nxt,
        )

    return {
        "nodes": [
            {
                "id": "start",
                "kind": "start",
                "title": "Start",
                "config": {},
                "position": None,
                "depends_on": [],
                "next": ["core", "jisa_child", "jisa_registered_contact", "org"],
            },
            # -- participant tier -------------------------------------------------------------
            _step(
                {
                    "id": "core",
                    "type": "fill_form",
                    "title": "Applicant details",
                    "scope": "participant",
                    "assignee": "staff",
                    "visibility": individual_visible,
                    "config": {
                        "form_uid": forms["applicant_core"],
                        "mode": "gather",
                        "mapping": CORE_MAPPING,
                        "title": "Applicant details",
                    },
                },
                fact_find,
            ),
            _step(
                {
                    "id": "jisa_child",
                    "type": "fill_form",
                    "title": "Child details",
                    "scope": "participant",
                    "assignee": "staff",
                    "visibility": jisa_child_visible,
                    "config": {
                        "form_uid": forms["jisa_child"],
                        "mode": "gather",
                        "mapping": JISA_CHILD_MAPPING,
                        "title": "Child details",
                    },
                },
                "account_preferences",
            ),
            _step(
                {
                    "id": "jisa_registered_contact",
                    "type": "fill_form",
                    "title": "Registered contact details",
                    "scope": "participant",
                    "assignee": "staff",
                    "visibility": jisa_registered_contact_visible,
                    "config": {
                        "form_uid": forms["jisa_registered_contact"],
                        "mode": "gather",
                        "mapping": JISA_REGISTERED_CONTACT_MAPPING,
                        "title": "Registered contact details",
                    },
                },
                "account_preferences",
            ),
            _step(
                {
                    "id": "org",
                    "type": "fill_form",
                    "title": "Organisation details",
                    "scope": "participant",
                    "assignee": "staff",
                    "visibility": organisation_visible,
                    "config": {
                        "form_uid": forms["organisation_core"],
                        "mode": "gather",
                        "mapping": ORG_MAPPING,
                        "title": "Organisation details",
                    },
                },
                fact_find,
            ),
            _step(
                {
                    "id": "additional_info",
                    "type": "fill_form",
                    "title": "Additional information",
                    "scope": "participant",
                    "assignee": "staff",
                    "visibility": individual_visible,
                    "config": {
                        "form_uid": forms["applicant_offshore"],
                        "mapping": ADDITIONAL_MAPPING,
                        "title": "Additional information",
                        # The W-9 / W-8 answer, for the `needs_fatca_form` fork below.
                        "remember": dict(FATCA_REMEMBER),
                    },
                },
                "declaration",
            ),
            _step(
                {
                    "id": "pension",
                    "type": "fill_form",
                    "title": "Pension information",
                    "scope": "participant",
                    "assignee": "staff",
                    "visibility": pension_visible,
                    "config": {
                        "form_uid": forms["applicant_pension"],
                        "mapping": PENSION_MAPPING,
                        "title": "Pension information",
                    },
                },
                "declaration",
            ),
            _step(
                {
                    "id": "employer",
                    "type": "fill_form",
                    "title": "Employer contributions",
                    "scope": "participant",
                    "assignee": "staff",
                    "visibility": sipp_visible,
                    "config": {
                        "form_uid": forms["applicant_employer"],
                        "mapping": EMPLOYER_MAPPING,
                        "title": "Employer contributions",
                    },
                },
                "declaration",
            ),
            # -- account tier -----------------------------------------------------------------
            # The tier itself is `scope`, not a wire: the account work still fans out per account
            # behind the cross-case KYC gate. The wire only says what comes after what.
            _step(
                {
                    "id": "account_preferences",
                    "type": "fill_form",
                    "title": "Account details",
                    "scope": "account",
                    "assignee": "staff",
                    "config": {
                        "form_uid": forms["account_preferences"],
                        "mode": "account_preferences",
                        "title": "Account details",
                    },
                },
                "declaration",
            ),
            # -- the handoff --------------------------------------------------------------------
            # The legacy app's central mechanic. There `applications.adviser_declaration` was the
            # SOLE trigger for draft -> with_client, which emitted `application.ready` — the email
            # carrying each client their link
            # (`lib/services/application-actions.ts::updateAdviserDeclaration`). `invite_clients`
            # restores that declare → email handoff: completing this step invites every
            # KYC-relevant owner (best-effort via `provision_client_login`).
            #
            # `task`, not `review`: a review records a compliance OUTCOME and can raise an
            # application gate flag, which this is not. This is the adviser attesting to their own
            # work, and its whole effect is that the next node belongs to the client.
            _step(
                {
                    "id": "declaration",
                    "type": "task",
                    "title": "Declare and send to the client",
                    "scope": "application",
                    "assignee": "staff",
                    "config": {
                        "description": (
                            "Confirm the information entered is correct, complete and not "
                            "misleading, then hand the application to the client. Everything "
                            "after this point is theirs — check they can reach the portal first."
                        ),
                        "invite_clients": True,
                    },
                },
                ["terms_general", "needs_fatca_form"],
            ),
            # -- with the client ----------------------------------------------------------------
            # A W-9 or W-8 recorded on Additional information is asked for, signed, beside the
            # terms. The fork is entered when the declaration is sent, so it reads the answers the
            # declaration locked, and each client's profile case reads its own remembered answer.
            # The step waits on nothing else, and only opening the account waits for it.
            _gate(
                "needs_fatca_form",
                "A signed W-9 or W-8 to collect?",
                _cond("memory.fatca_status", "in", list(FATCA_SIGNED_FORM_ANSWERS)),
                then="fatca_form",
                otherwise="setup",
            ),
            _step(
                {
                    "id": "fatca_form",
                    "type": "upload_document",
                    "title": "Upload your signed W-9 / W-8",
                    "scope": "participant",
                    "assignee": "client",
                    "visibility": individual_visible,
                    "config": {
                        "category": "fatca_form",
                        "format_hint": "PDF or a photo",
                        "title": "Upload your signed W-9 / W-8",
                    },
                },
                "setup",
            ),
            terms_step("terms_general", "general_declaration", "General declaration", "is_isa"),
            _gate(
                "is_isa",
                "An ISA?",
                _cond("account.product", "in", ["ISA", "JISA"]),
                then="terms_isa",
                otherwise="has_transfer_in",
            ),
            terms_step(
                "terms_isa",
                "isa_declaration",
                "ISA declaration",
                "has_transfer_in",
                extra=_cond("account.product", "in", ["ISA", "JISA"]),
            ),
            _gate(
                "has_transfer_in",
                "A transfer in?",
                _cond("account.has_transfer", "equals", True),
                then="terms_transfer",
                otherwise="terms_platform",
            ),
            terms_step(
                "terms_transfer",
                "transfer_declaration",
                "Transfer declaration",
                "terms_platform",
                extra=_cond("account.has_transfer", "equals", True),
            ),
            terms_step("terms_platform", "platform_terms", "Platform terms", "idv"),
            _step(
                {
                    # Starts with the client where a digital check exists — it is their document
                    # and their choice — and they can hand it to a person instead, which moves the
                    # step to staff. Straight to staff where there is no digital check to offer.
                    # See `_idv_assignee`.
                    #
                    # No `depends_on: ["core"]` any more: the spine already puts it after the
                    # fact-find, and an edge compiles into exactly that prerequisite.
                    "id": "idv",
                    "type": "verify_identity",
                    "title": "Verify identity",
                    "scope": "participant",
                    "assignee": _idv_assignee(_idv_provider()),
                    "config": {"provider": _idv_provider()},
                },
                "aml",
            ),
            _step(
                {
                    "id": "aml",
                    "type": "aml_check",
                    "title": "AML screening",
                    "scope": "participant",
                    "assignee": "staff",
                    "config": {},
                },
                "needs_offshore_review",
            ),
            _gate(
                "needs_offshore_review",
                "Offshore compliance sign-off?",
                account_offshore,
                then="offshore_review",
                otherwise="setup",
            ),
            _step(
                {
                    "id": "offshore_review",
                    "type": "review",
                    "title": "Offshore compliance review",
                    "scope": "account",
                    "assignee": "staff",
                    "config": {
                        "gate": "offshore_approved",
                        "category": "offshore",
                        "title": "Offshore compliance review",
                    },
                },
                "setup",
            ),
            _step(
                {
                    "id": "setup",
                    "type": "setup_account",
                    "title": "Open the account",
                    "scope": "account",
                    "assignee": "staff",
                    "config": {},
                },
                None,
            ),
        ],
    }


async def _republish(
    session: AsyncSession, *, tenant_id: int, flow_id: int, definition: dict[str, Any]
) -> bool:
    """Publish ``definition`` as the flow's next version when it differs from the live one.

    Expand-only on purpose: a new ``flows.flow_versions`` row becomes the published version and
    ``flows.executions`` in flight keep pointing at the version they started on, so correcting the
    seed never rewrites a journey mid-flight. Returns whether a new version was written.
    """
    live = (
        (
            await session.execute(
                text(
                    "SELECT fv.id, fv.version, fv.definition FROM flows.flows f "
                    "JOIN flows.flow_versions fv ON fv.id = f.current_published_version_id "
                    "WHERE f.id = :f"
                ),
                {"f": flow_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    current = live["definition"] if live else None
    if isinstance(current, str):
        current = json.loads(current)
    if current == definition:
        return False
    # Next version is MAX(version)+1 across ALL rows for this flow — not live.version+1.
    # The builder can leave a draft at live+1 (or a failed prior republish can), and bumping
    # only from the published pointer then collides on flow_versions_flow_version_key.
    next_version = int(
        (
            await session.execute(
                text(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM flows.flow_versions "
                    "WHERE flow_id = :f"
                ),
                {"f": flow_id},
            )
        ).scalar_one()
    )
    vid = (
        await session.execute(
            text(
                "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status, "
                "  definition, published_at) "
                "VALUES (:u, :t, :f, :v, 'published', CAST(:d AS jsonb), now()) RETURNING id"
            ),
            {
                "u": new_id(),
                "t": tenant_id,
                "f": flow_id,
                "v": next_version,
                "d": json.dumps(definition),
            },
        )
    ).scalar_one()
    await session.execute(
        text("UPDATE flows.flows SET current_published_version_id = :v WHERE id = :f"),
        {"v": vid, "f": flow_id},
    )
    return True


async def seed_onboarding(session: AsyncSession, *, tenant_id: int) -> dict[str, object]:
    """Create the forms, terms and the onboarding flow for a tenant (idempotent by code). Returns
    the flow uid, whether it was newly created, and whether an existing flow was republished.

    Re-running on a tenant that already has the flow is **not** a no-op any more: if the built
    definition has moved on (the actor split was corrected, a step was added, the journey was
    rebuilt as a graph), it publishes the new definition as the next version. Without that, every
    tenant provisioned before the fix stayed on the old graph forever with no route to the new one
    but hand-written SQL.
    """
    forms = {code: await _ensure_form(session, tenant_id, code) for code in FORMS}
    terms = {
        code: await _ensure_term(
            session, tenant_id, code, spec["name"], spec["content"], spec["scope"]
        )
        for code, spec in terms_content.TERMS.items()
    }
    # Stored VALIDATED, which a v3 definition has to be: the derived ``steps`` projection is what
    # the orchestrator reads off the pinned version (``_definition_steps``), and it only exists once
    # ``validate_definition`` has compiled the condition nodes into each step's ``visibility``. It
    # is also what every publish from the builder writes (``routers/flows.py``), so the seeded flow
    # and an author-published one are now the same kind of document. Idempotent, so ``_republish``
    # still compares like with like.
    definition = validate_definition(_build_definition(forms, terms))

    existing = (
        (
            await session.execute(
                text("SELECT id, uid FROM flows.flows WHERE tenant_id = :t AND code = :c"),
                {"t": tenant_id, "c": FLOW_CODE},
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing:
        updated = await _republish(
            session, tenant_id=tenant_id, flow_id=int(existing["id"]), definition=definition
        )
        return {
            "flow_uid": str(existing["uid"]),
            "created": False,
            "updated": updated,
            "forms": forms,
            "terms": terms,
        }

    flow_uid = new_id()
    flow_id = (
        await session.execute(
            text(
                "INSERT INTO flows.flows (uid, tenant_id, code, name, description, entity, status) "
                "VALUES (:u, :t, :c, 'Onboarding', "
                "  'Client onboarding — fact-find, KYC, terms and account setup.', 'onboarding', "
                "  'published') RETURNING id"
            ),
            {"u": flow_uid, "t": tenant_id, "c": FLOW_CODE},
        )
    ).scalar_one()
    vid = (
        await session.execute(
            text(
                "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status, "
                "  definition, published_at) "
                "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id"
            ),
            {"u": new_id(), "t": tenant_id, "f": flow_id, "d": json.dumps(definition)},
        )
    ).scalar_one()
    await session.execute(
        text("UPDATE flows.flows SET current_published_version_id = :v WHERE id = :f"),
        {"v": vid, "f": flow_id},
    )
    return {
        "flow_uid": flow_uid,
        "created": True,
        "updated": False,
        "forms": forms,
        "terms": terms,
    }
