"""Populate the Morgan Lloyd (Clifton Asset Management) DEV CRM with test data.

Creates deterministic, realistic-looking UK test records: contacts, one scheme
per contact, then a transfer, beneficiary and pension contribution per scheme,
and one ATR per contact. Every record is marked as Graphene test data
(``cam_externalclientid`` / note fields) and all personal details are fake —
NI numbers use a clearly-fake ``TN......A`` pattern, phone numbers sit in
Ofcom's reserved drama range (07700 900xxx) and emails land on
``example.grapheneplatforms.com``.

It also EXERCISES the write surface end to end, which is the other reason to run
it: every documented create plus the PATCH paths, so a vendor change or a broken
credential shows up here rather than in a client-facing feature. Coverage is
contact, scheme, beneficiary, transfer, investment, contribution, ATR,
valuation, benefit application and the contact PATCH by default; drawdown and
illustration are opt-in (``--drawdown`` / ``--illustration-url``) because one
sets up income payments to a member and the other needs a PDF the vendor can
fetch. ``--only`` runs a named subset.

``--adviser {firm,none,both}`` decides whether contacts are attached to the
adviser firm. Worth both: an unattached contact is created HAPPILY and fails one
step later, when ``cam_createscheme`` answers ``403 0x8004f502 ... ML Team
secured field`` — so ``both`` produces the comparison in a single run. Every
contact this script made before that flag existed was unattached.

It REFUSES to run against a resource URL that does not look like a development
org (``--allow-non-dev`` overrides). Morgan Lloyd has no delete, so a run
against a real org cannot be undone.

Run via ``moon run morgan-lloyd-sync:populate`` (the ``populate`` task in
``services/morgan-lloyd/moon.yml`` runs
``uv run python scripts/populate_test_data.py``). Credentials come from the
``MORGAN_LLOYD_*`` environment variables read by ``MorganLloydSettings`` — no
secrets live in this file. ``--dry-run`` prints the planned payloads as JSON
without constructing the client or touching the network. Re-runs are
idempotent: contacts whose ``emailaddress1`` already exists in the CRM are
skipped, along with their downstream records.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from collections.abc import Iterable
from typing import Any

# morgan_lloyd ships no py.typed marker yet, so mypy sees it as untyped.
from morgan_lloyd.enums import (
    AssetStatus,
    AssetType,
    ContactEmploymentStatus,
    ContactStatus,
    ContactVulnerability,
    ContributionStatus,
    ContributionType,
    CrystallisedType,
    FamilyStatus,
    Gender,
    PensionContributionFrequency,
    PensionType,
    RelevantBusinessUnit,
    SchemeType,
    SippType,
    SourceOfWealth,
    StringBool,
    Title,
    TransferType,
    TypeOfTransfer,
    multiselect,
)

# Placeholders in built payloads, swapped for real IDs once the parent record
# is created (they survive verbatim in --dry-run output).
CONTACT_ID_PLACEHOLDER = "$CONTACT_ID"
SCHEME_REF_PLACEHOLDER = "$SCHEME_REF"

# Keys on the contact entity that plausibly hold the CRM's int Client Reference
# Number (Submit ATR needs it; Create Contact doesn't return it, so it is
# discovered from the created contact record — see _client_reference).
_CLIENT_REF_KEY_HINTS = ("clientref", "clientreference", "referencenumber", "clientnumber")


# Fixed reference values issued in "Morgan Lloyd API Details- Graphene.docx"
# (27 May 2026): our firm's Create Scheme pairing and Create Investment provider.
#
# CONFIGURATION FIRST, these literals only as the fallback. They lived here alone
# for months while `MorganLloydSettings.scheme_type` / `sipp_type` stayed unset,
# so `sync.py` refused to create a scheme for want of a value written down two
# directories away. The literals stay because this script's whole job is the dev
# org and it should run with no environment at all -- but a deployment that has
# configured a different firm's values must win over them.
def _configured(attribute: str, fallback: object) -> object:
    try:
        from morgan_lloyd.settings import MorganLloydSettings

        value = getattr(MorganLloydSettings(), attribute, None)
    except Exception:  # noqa: BLE001 - unconfigured is the normal case here
        return fallback
    return value if value not in (None, "") else fallback


DEFAULT_SCHEME_TYPE = int(_configured("scheme_type", 16))  # type: ignore[call-overload]
DEFAULT_SIPP_TYPE = int(_configured("sipp_type", 809130016))  # type: ignore[call-overload]
DEFAULT_INVESTMENT_PROVIDER = str(
    _configured("investment_provider_id", "6ac39921-d8b7-f011-bbd3-7ced8d6b85ff")
)

_MALE_FIRST = ("James", "Oliver", "Harry", "Thomas", "Arthur", "George")
_FEMALE_FIRST = ("Olivia", "Amelia", "Isla", "Emily", "Sophie", "Grace")
_MALE_TITLES = (Title.MR, Title.DR, Title.PROF)
_FEMALE_TITLES = (Title.MS, Title.MRS, Title.MISS, Title.DR)
_LAST_NAMES = ("Ackroyd", "Bramley", "Carrington", "Dunmore", "Ellerby", "Fairweather", "Garside")
_STREETS = ("Alder Grove", "Birch Row", "Castle Wynd", "Drovers Lane", "Elm Croft")
_TOWNS = (  # (city, postcode) pairs — plausible UK formats, fixed per town
    ("Hull", "HU3 2BA"),
    ("York", "YO1 7HH"),
    ("Leeds", "LS6 2QD"),
    ("Sheffield", "S10 2TN"),
    ("Bristol", "BS8 4ND"),
    ("Norwich", "NR2 1RH"),
)
# Real ISINs would imply a real holding; these are clearly-synthetic GB00 codes.
_FUNDS = (
    ("Graphene Test Global Equity", "GB00TEST0001"),
    ("Graphene Test Sterling Bond", "GB00TEST0002"),
    ("Graphene Test Balanced", "GB00TEST0003"),
    ("Graphene Test Cash", "GB00TEST0004"),
)
_PROVIDERS = ("Aviva", "Scottish Widows", "Standard Life", "Aegon", "Royal London")
_EMPLOYMENT = (
    ContactEmploymentStatus.EMPLOYED,
    ContactEmploymentStatus.SELF_EMPLOYED,
    ContactEmploymentStatus.PENSIONER,
)
_WEALTH = (SourceOfWealth.SALARY, SourceOfWealth.BUSINESS_INTERESTS, SourceOfWealth.PENSION_INCOME)
_PENSION_TYPES = (
    PensionType.PERSONAL_PENSION_PLAN,
    PensionType.SELF_INVESTED_PERSONAL_PENSION,
    PensionType.GROUP_PERSONAL_PENSION,
    PensionType.STAKEHOLDER,
)
_FREQUENCIES = (
    PensionContributionFrequency.MONTHLY,
    PensionContributionFrequency.QUARTERLY,
    PensionContributionFrequency.ANNUALLY,
)
_RELATIONSHIPS = ("Wife", "Husband", "Daughter", "Son", "Sister", "Brother")
_EMPLOYERS = ("Humber Fabrications Ltd", "Ouse Valley Consulting Ltd", "Pennine Joinery Ltd")

_ATR_QUESTIONS = (
    "How would you react if your investments fell 20% in a year?",
    "How long do you expect to keep this money invested?",
    "How much investment experience do you have?",
)
_ATR_RESPONSES = (
    ("Sell everything", "Hold", "Buy more"),
    ("Under 5 years", "5-10 years", "Over 10 years"),
    ("None", "Some", "Extensive"),
)


def _date(rng: random.Random, year_from: int, year_to: int) -> str:
    """A deterministic ``YYYY-MM-DD`` date (days capped at 28 for validity)."""
    return f"{rng.randint(year_from, year_to)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"


def _recent(rng: random.Random) -> str:
    """A deterministic date inside the last month or so.

    Not `date.today()`: a valuation or transfer date has to be plausible against
    the record it belongs to, and a fixed seed has to keep producing the same
    payload — which a real clock defeats. `--dry-run` output stays comparable.
    """
    return f"2026-0{rng.randint(7, 8)}-{rng.randint(1, 28):02d}"


def build_contact(
    rng: random.Random,
    index: int,
    vulnerability_added_by: str | None = None,
    adviser_firm: str | None = None,
    adviser_contact: str | None = None,
) -> dict[str, Any]:
    """A Create Contact payload (``cam_createcontact``) for test client *index*.

    Vulnerable contacts are only generated when *vulnerability_added_by* is a
    real SystemUser GUID — ``cam_vulnerabilityaddedby`` is validated server-side
    (a made-up GUID 400s the whole create with "Entity 'SystemUser' ... Does
    Not Exist").

    *adviser_firm* attaches the contact to an adviser firm, as an OData ENTITY
    REFERENCE (``/accounts(<guid>)``) — a bare, braced or uppercased GUID all
    fail with "Invalid Format for cam_financialadviser_Account". None leaves the
    contact UNATTACHED, which is worth exercising deliberately: the create
    succeeds either way and the consequence lands one step later, when
    ``cam_createscheme`` answers ``403 0x8004f502 ... ML Team secured field``.
    Every contact this script made before ``--adviser`` existed was unattached,
    which is why its schemes needed the combination probe.

    THE PAIR IS REQUIRED TOGETHER: a firm with no *adviser_contact* is refused
    outright with ``400 0x80040224 Financial Adviser contact must be populated
    for the adviser firm``. That is the vendor\'s ``No*`` conditional, and it
    only bites once the firm is supplied — which is why omitting both worked."""
    gender = rng.choice((Gender.MALE, Gender.FEMALE))
    if gender is Gender.MALE:
        title, first = rng.choice(_MALE_TITLES), rng.choice(_MALE_FIRST)
    else:
        title, first = rng.choice(_FEMALE_TITLES), rng.choice(_FEMALE_FIRST)
    city, postcode = rng.choice(_TOWNS)
    # keep the rng draw unconditional so payloads stay deterministic either way
    vulnerable = rng.random() < 0.15 and vulnerability_added_by is not None
    contact: dict[str, Any] = {
        "as_contactstatus": int(ContactStatus.CUSTOMER),
        "cam_title": int(title),
        "firstname": first,
        "lastname": rng.choice(_LAST_NAMES),
        "emailaddress1": f"ml-test-{index:03d}@example.grapheneplatforms.com",
        "mobilephone": f"07700 900{rng.randint(0, 999):03d}",  # Ofcom reserved drama range
        "cam_nationality": "British",
        "gendercode": int(gender),
        "cam_ninumber": f"TN{rng.randint(0, 999999):06d}A",  # valid shape, clearly fake
        "address1_line1": f"{rng.randint(1, 99)} {rng.choice(_STREETS)}",
        "address1_city": city,
        "address1_postalcode": postcode,
        "cam_birthdate": _date(rng, 1950, 1995),
        "familystatuscode": int(rng.choice(tuple(FamilyStatus))),
        "cam_employmentstatus": int(rng.choice(_EMPLOYMENT)),
        "cam_sourceofwealth": int(rng.choice(_WEALTH)),
        "cam_annualearnings": rng.randrange(24000, 120000, 500),
        "cam_externalclientid": f"GRAPHENE-TEST-{index:04d}",
        "cam_potentiallyvulnerable": str(StringBool.TRUE if vulnerable else StringBool.FALSE),
    }
    if adviser_firm:
        contact["cam_financialadviser_Account"] = f"/accounts({adviser_firm.strip('{}')})"
        if adviser_contact:
            # An ENTITY REFERENCE, like the firm: the manifest types both fields `lookup`, and a
            # bare GUID is refused with "Invalid Format for cam_FinancialAdviserContact".
            contact["cam_FinancialAdviserContact"] = f"/contacts({adviser_contact.strip('{}')})"
    if vulnerable:  # the spec's ** fields — only when "True"
        picks = rng.sample(tuple(ContactVulnerability), k=rng.randint(1, 2))
        contact["cam_contactvulnerability"] = multiselect(*picks)
        contact["cam_vulnerabilityaddedby"] = vulnerability_added_by
        contact["cam_additionalvulnerabilitynotes"] = "Graphene test data — synthetic flag only."
        contact["cam_relevantbusinessunit"] = int(RelevantBusinessUnit.CWP)
    return contact


def build_scheme(rng: random.Random, index: int, contact_id: str) -> dict[str, Any]:
    """A Create Scheme payload (``cam_createscheme``).

    Only the required trio is sent: the fee-percentage fields are ML-Team
    field-secured in the dev CRM (sending them 403s the whole create). The
    schemeType/sippType pairing is the one Morgan Lloyd issued for our firm
    (API Details docx); ``--probe`` walks all documented combinations instead
    (see ``_create_scheme_with_probe``)."""
    return {
        "contactId": contact_id,
        "schemeType": DEFAULT_SCHEME_TYPE,
        "sippType": DEFAULT_SIPP_TYPE,
    }


def build_transfer(rng: random.Random, index: int, scheme_reference: str) -> dict[str, Any]:
    """A Create Transfer payload (``cam_createtransfer``)."""
    uncrystallised = round(rng.uniform(20_000, 250_000), 2)
    crystallised = round(rng.uniform(0, 50_000), 2) if rng.random() < 0.25 else 0.0
    return {
        "schemeReference": scheme_reference,
        "cam_policyno": f"GRTEST{index:04d}{rng.randint(100, 999)}",
        "cam_providername": rng.choice(_PROVIDERS),
        "cam_assettype": int(AssetType.PENSION_PLANS),
        "cam_status": int(AssetStatus.TRANSFERRING),
        # Verbatim triple-l field name — do not "correct" it.
        "cam_uncrystalllisedtransfervalue": uncrystallised,
        "cam_crystallisedtransfervalue": crystallised,
        "cam_nonprtv": round(uncrystallised + crystallised, 2),
        "cam_typeoftransfer": int(rng.choice(tuple(TypeOfTransfer))),
        "cam_transfertype": int(rng.choice(tuple(TransferType))),
        "cam_crystallised": int(
            CrystallisedType.PARTIAL if crystallised else CrystallisedType.FULL
        ),
        "cam_pensiontype": int(rng.choice(_PENSION_TYPES)),
        "transferNotes": "Graphene test data — synthetic transfer, not a real policy.",
    }


def build_investment(
    index: int, scheme_reference: str, policy_number: str, provider: str
) -> dict[str, Any]:
    """A Create Investment payload (``cam_createwrapper``): registers the
    platform policy against the scheme (the diagram's "Investment set up on
    Platform" step). *provider* is the firm ID Morgan Lloyd issued us."""
    return {
        "provider": provider,
        "schemeReference": scheme_reference,
        "policyNumber": policy_number,
    }


def build_beneficiary(rng: random.Random, index: int, scheme_reference: str) -> dict[str, Any]:
    """A Create Beneficiary payload (``cam_createbeneficiaries``)."""
    city, postcode = rng.choice(_TOWNS)
    first = rng.choice(_MALE_FIRST + _FEMALE_FIRST)
    return {
        "schemeReference": scheme_reference,
        "cam_nameofbeneficiary": f"{first} {rng.choice(_LAST_NAMES)}",
        "cam_percentageoffund": 100.0,
        "cam_nominationdate": _date(rng, 2024, 2026),
        "cam_relationship": rng.choice(_RELATIONSHIPS),
        "cam_address_line1": f"{rng.randint(1, 99)} {rng.choice(_STREETS)}",
        "cam_city": city,
        "cam_postcode": postcode,
        "cam_country": "United Kingdom",
    }


def build_contribution(
    rng: random.Random, index: int, scheme_reference: str, contributor_id: str
) -> dict[str, Any]:
    """A Create Pension Contribution payload (``cam_createcontribution``)."""
    contribution_type = rng.choice((ContributionType.PERSONAL, ContributionType.EMPLOYER))
    contribution: dict[str, Any] = {
        "schemeReference": scheme_reference,
        "cam_contributiontype": int(contribution_type),
        "cam_frequency": int(rng.choice(_FREQUENCIES)),
        "cam_amount": float(rng.randrange(100, 2000, 25)),
        "cam_startdate": _date(rng, 2025, 2026),
        "cam_status": int(rng.choice((ContributionStatus.APPLICATION, ContributionStatus.ACTIVE))),
        "cam_investorref": f"GRAPHENE-TEST-{index:04d}",
    }
    if contribution_type is ContributionType.PERSONAL:  # the spec's ^ rule
        contribution["cam_contributor"] = contributor_id
    else:  # the spec's * rule: required when NOT Personal
        contribution["cam_employerothername"] = rng.choice(_EMPLOYERS)
    return contribution


def build_atr(rng: random.Random, index: int) -> dict[str, Any]:
    """A Submit ATR payload (``cam_submitatr``). ``clientId`` is the CRM's int
    Client Reference Number — not returned by Create Contact, so live mode
    discovers it from the created contact record (see ``_client_reference``)
    and skips the ATR when no reference field can be found."""
    return {
        "clientId": CONTACT_ID_PLACEHOLDER,  # swapped for the discovered reference
        "riskProfile": rng.randint(1, 10),
        "dateGenerated": _date(rng, 2026, 2026),
        "atrData": [
            {"question_id": qid, "question": question, "response": rng.choice(responses)}
            for qid, (question, responses) in enumerate(
                zip(_ATR_QUESTIONS, _ATR_RESPONSES, strict=True), start=1
            )
        ],
    }


def build_client_plan(
    rng: random.Random,
    index: int,
    vulnerability_added_by: str | None = None,
    provider: str = DEFAULT_INVESTMENT_PROVIDER,
    adviser_firm: str | None = None,
    adviser_contact: str | None = None,
) -> dict[str, Any]:
    """Every payload for one test client, with placeholder IDs to be wired in."""
    transfer = build_transfer(rng, index, SCHEME_REF_PLACEHOLDER)
    policy_number = str(transfer["cam_policyno"])
    return {
        "contact": build_contact(rng, index, vulnerability_added_by, adviser_firm, adviser_contact),
        "scheme": build_scheme(rng, index, CONTACT_ID_PLACEHOLDER),
        "beneficiary": build_beneficiary(rng, index, SCHEME_REF_PLACEHOLDER),
        "transfer": transfer,
        "investment": build_investment(index, SCHEME_REF_PLACEHOLDER, policy_number, provider),
        "contribution": build_contribution(
            rng, index, SCHEME_REF_PLACEHOLDER, CONTACT_ID_PLACEHOLDER
        ),
        "atr": build_atr(rng, index),
        "valuation": build_valuation(rng, index, policy_number),
        "benefit": build_benefit(rng, index),
        "drawdown": build_drawdown(rng, index),
        "updates": build_updates(rng, index),
        # On the plan so `--dry-run` shows which clients are attached — the whole point of
        # `--adviser both` is that the two halves behave differently one step later.
        "_adviser_firm": adviser_firm,
        # The client's own index, which is what its email and external id are keyed on. Carried
        # because plans no longer start at 0: a re-run offsets past the contacts already there.
        "_index": index,
    }


def build_valuation(rng: random.Random, index: int, policy_number: str) -> dict[str, Any]:
    """A Create Policy Valuation payload (``cam_createpolicyvaluation``).

    ``PolicyValue`` MUST equal the combined fund splits\' market value — the vendor
    validates the arithmetic — so the splits are generated first and the total
    summed from them rather than drawn independently.
    """
    splits: list[dict[str, Any]] = []
    for fund in rng.sample(_FUNDS, k=rng.randint(1, 3)):
        holding = round(rng.uniform(100, 5_000), 4)
        price = round(rng.uniform(0.8, 12.5), 4)
        splits.append(
            {
                "Name": fund[0],
                "Isin": fund[1],
                "ValuationHolding": holding,
                "Price": price,
                "MarketValue": round(holding * price, 2),
                "PriceDate": _recent(rng),
            }
        )
    return {
        "PolicyReference": policy_number,
        "PolicyValue": round(sum(float(s["MarketValue"]) for s in splits), 2),
        "ValuationDate": _recent(rng),
        "FundSplits": splits,
    }


def build_benefit(rng: random.Random, index: int) -> dict[str, Any]:
    """A benefit application (``cam_setmemberdetails``), which also returns the
    DCube ``ssoUrl`` the drawdown journey opens.

    ``advised`` is a real boolean here, unlike drawdown\'s Yes/No fields which
    are the STRINGS "0"/"1" — the vendor is not consistent and both shapes are
    exercised on purpose.
    """
    return {
        "advised": True,
        "assetValuation": round(rng.uniform(50_000, 400_000), 2),
        "cashValuation": round(rng.uniform(500, 20_000), 2),
        "crystallisedValue": 0.0,
    }


def build_drawdown(rng: random.Random, index: int) -> dict[str, Any]:
    """A drawdown instruction (``cam_drawdownsetup``).

    The most consequential call here — it sets up income payments to a member —
    so it is behind ``--drawdown`` rather than in the default set.

    Two shapes the vendor documents and nothing else does: the guidance/advice
    fields are STRINGS "0"/"1" and not booleans, and ``paymentDay`` accepts only
    6, 14, 21 or 28.
    """
    return {
        "drawdownOption": 2,  # Full TFC — the simplest documented option
        "phasedUFPLS": False,
        "phasedPCLS": False,
        "investmentPathway": 6,  # remain invested in current investments
        "sortCode": "20-00-00",
        "accountNumber": f"{rng.randint(10_000_000, 99_999_999)}",
        "accountName": "Graphene Test Account",
        "tfcUsed": 0.0,
        "taxFreeCashAmount": round(rng.uniform(1_000, 25_000), 2),
        "incomeFrequency": 12,  # Monthly
        "paymentDay": rng.choice((6, 14, 21, 28)),
        "guidanceAppointmentAttendance": "0",
        "adviceAppointmentAttendance": "1",
        "recentlyReceivedGuidance": "0",
        "recentlyReceivedAdvice": "1",
        "doesNotWantAdvice": "0",
        "pensionWiseAccessed": False,
    }


def build_updates(rng: random.Random, index: int) -> dict[str, dict[str, Any]]:
    """The PATCH bodies, one per updatable entity.

    Exercised because a PATCH is a genuinely different code path from its
    create — a bare GUID in a lookup field fails where an entity reference
    works, and the vendor answers 204 with no body, so a caller that expects
    JSON breaks only here.
    """
    return {
        "contact": {"address1_line1": f"{rng.randint(1, 99)} Updated Street"},
        "beneficiary": {"cam_percentageoffund": 100},
        "transfer": {
            "cam_transferdate": _recent(rng),
            "cam_transferamount": round(rng.uniform(10_000, 90_000), 2),
            "cam_fundnotes": "Graphene test data — synthetic update.",
        },
    }


def build_plans(
    seed: int,
    clients: int,
    vulnerability_added_by: str | None = None,
    provider: str = DEFAULT_INVESTMENT_PROVIDER,
    adviser: str = "firm",
    adviser_firm: str | None = None,
    start_index: int = 0,
    adviser_contact: str | None = None,
) -> list[dict[str, Any]]:
    """*adviser* is ``firm`` (attach every contact to the adviser firm), ``none`` (attach none)
    or ``both`` (alternate, so one run produces the comparison).

    *start_index* offsets the whole batch. Every client's email and external id are keyed on its
    index alone, so a second run at 0 collides with the first and every client is skipped —
    including all of its downstream records, which is how the newer paths (valuation, benefit
    application, the PATCHes) went unexercised for a whole run.

    The rng is seeded PER CLIENT from (seed, index) rather than threaded through the batch, so
    client 12's payload is the same whether it was reached in a batch starting at 0 or at 8.
    Otherwise an offset would silently re-roll everybody and `--dry-run` output would stop being
    comparable between runs.
    """
    plans: list[dict[str, Any]] = []
    for index in range(start_index, start_index + clients):
        # A STRING seed: `Random()` takes None/int/float/str/bytes and refuses a tuple.
        rng = random.Random(f"{seed}:{index}")  # noqa: S311 - fixtures, not cryptography
        attach = adviser == "firm" or (adviser == "both" and index % 2 == 0)
        plans.append(
            build_client_plan(
                rng,
                index,
                vulnerability_added_by,
                provider,
                adviser_firm if attach else None,
                adviser_contact if attach else None,
            )
        )
    return plans


# --- live wiring ----------------------------------------------------------------

# creation order follows the vendor's SIPP Application diagram:
# contact -> scheme -> beneficiaries -> transfers -> investment -> contribution
RECORD_KINDS = (
    "contact",
    "scheme",
    "beneficiary",
    "transfer",
    "investment",
    "contribution",
    "atr",
    "valuation",
    "benefit",
    "drawdown",
    "updates",
    "illustration",
)

# Off unless asked for. `drawdown` sets up income payments to a member -- the one call here whose
# consequence is a payment rather than a record -- and `illustration` needs a PDF at a URL the
# vendor can reach, which a dev box does not have.
OPT_IN_KINDS = ("drawdown", "illustration")


def envelope_value(answer: dict[str, Any], key: str) -> str | None:
    """A value out of an action's answer, wherever the vendor happens to put it.

    THE ENVELOPE IS NOT CONSISTENT, and assuming it is has now cost two bugs in opposite
    directions. `cam_createcontact` nests its `contactid` under `response`, so reading the top
    level alone loses it -- which is how `sync-to-morgan-lloyd` created a contact, discarded its
    id and duplicated the person on every retry. `cam_setmemberdetails` does the reverse: it puts
    `benefitApplicationId` and `ssoUrl` at the TOP level and uses `response` for the status
    envelope alone (`responseKey`/`message`) -- so reading the nested object first found neither,
    which is why the benefit application reported "no ssoUrl in answer" and drawdown was always
    skipped for want of an id that had been returned.

    So: look in both, prefer whichever actually carries it, and never assume the shape.
    """
    nested = answer.get("response")
    for source in (answer, nested if isinstance(nested, dict) else {}):
        value = source.get(key)
        if value:
            return str(value)
    return None


def _contact_id(response: dict[str, Any]) -> str | None:
    return envelope_value(response, "contactid")


def _client_reference(contact_record: dict[str, Any]) -> int | None:
    """The CRM's int Client Reference Number, hunted from a contact record —
    the field isn't documented, so match key names against the usual suspects."""
    for key, value in contact_record.items():
        lowered = key.lower().replace("_", "")
        if any(hint in lowered for hint in _CLIENT_REF_KEY_HINTS):
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
    return None


def _scheme_combos(scheme_type: int | None, sipp_type: int | None) -> list[tuple[int, int]]:
    """Candidate (schemeType, sippType) pairings, honouring any pinned value.
    The valid pairing is firm-specific ("Invalid Scheme and Sipp Type
    combination" otherwise), so live mode walks these until one creates."""
    scheme_types = [scheme_type] if scheme_type is not None else [int(v) for v in SchemeType]
    sipp_types = [sipp_type] if sipp_type is not None else [int(v) for v in SippType]
    return [(st, sp) for st in scheme_types for sp in sipp_types]


async def populate(
    plans: list[dict[str, Any]],
    scheme_type: int | None = DEFAULT_SCHEME_TYPE,
    sipp_type: int | None = DEFAULT_SIPP_TYPE,
    resume_existing: bool = False,
    kinds: tuple[str, ...] = RECORD_KINDS,
    provider: str = DEFAULT_INVESTMENT_PROVIDER,
    illustration_url: str = "",
) -> int:
    """Create the planned records in the dev CRM; returns the exit code.

    *kinds* is which record types to attempt — the opt-in ones (`OPT_IN_KINDS`) are absent unless
    the caller asked for them.
    """
    from morgan_lloyd import (
        MorganLloydActionError,
        MorganLloydClient,
        MorganLloydError,
    )

    counts: dict[str, dict[str, int]] = {
        kind: {"created": 0, "skipped": 0, "failed": 0} for kind in RECORD_KINDS
    }

    def _mark(kind: str, outcome: str, label: str, detail: str = "") -> None:
        counts[kind][outcome] += 1
        print(f"{outcome:7s} {kind:12s} {label}" + (f" — {detail}" if detail else ""))

    # discovered on the first successful create, reused for every later scheme
    known_combo: tuple[int, int] | None = (
        (scheme_type, sipp_type) if scheme_type is not None and sipp_type is not None else None
    )

    async def _create_scheme_with_probe(
        api: Any, payload: dict[str, Any], label: str
    ) -> str | None:
        nonlocal known_combo
        combos = [known_combo] if known_combo else _scheme_combos(scheme_type, sipp_type)
        last_error = ""
        # The two refusals mean opposite things and the summary has to tell them apart. "Invalid
        # Scheme and Sipp Type combination" is about the VALUES -- a different pairing may work.
        # A `secured field` 403 means the pairing was accepted and the caller cannot write a field
        # the action sets, which no value can fix.
        rejected_pairing = 0
        secured_field = 0
        # Quoted alongside the field-security diagnosis. `last_error` is whatever the LAST
        # attempt said, which for a full probe is usually an invalid-pairing message — so
        # quoting it under a field-security headline contradicts the headline.
        secured_error = ""
        for combo in combos:
            attempt = {**payload, "schemeType": combo[0], "sippType": combo[1]}
            try:
                response = await api.create_scheme(attempt)
            except MorganLloydActionError as exc:
                if "Invalid Scheme and Sipp Type combination" in str(exc):
                    last_error = str(exc)
                    rejected_pairing += 1
                    continue  # firm-specific pairing — try the next candidate
                _mark("scheme", "failed", label, str(exc))
                return None
            except MorganLloydError as exc:
                # some SIPP types create scheme flavours that touch ML-Team
                # field-secured columns (403 "secured field") — also combo-
                # dependent, so keep probing
                if "secured field" in str(exc):
                    last_error = secured_error = str(exc)
                    secured_field += 1
                    continue
                _mark("scheme", "failed", label, str(exc))
                return None
            if known_combo is None:
                known_combo = combo
                print(
                    "probe   scheme       valid combination: "
                    f"schemeType={combo[0]} sippType={combo[1]}"
                )
            ref = str(response.get("schemeReference") or "") or None
            if ref is None:
                _mark("scheme", "failed", label, "create returned no schemeReference")
            return ref
        # The diagnosis has to match what actually happened. With the pair PINNED only one
        # combination is tried, so "no combination worked" is both untrue and points at the wrong
        # question -- and a `secured field` 403 is not a pairing problem in either case: it says
        # the service principal cannot write a field on Scheme, which is a permissions request to
        # Morgan Lloyd rather than anything a value can fix.
        if secured_field and not rejected_pairing and len(combos) == 1:
            # One pinned pair, accepted as valid, then refused on field security. Cannot yet tell
            # whether ANOTHER pairing would be accepted, so point at the test that would settle it
            # rather than asserting a conclusion. Saying "this is a permissions problem" here was
            # wrong once already.
            _mark(
                "scheme",
                "failed",
                label,
                "the pinned schemeType/sippType pair is VALID and was then refused on field "
                "security — the caller cannot write a field the action sets. Re-run with --probe "
                f"to see whether any pairing is accepted ({secured_error})",
            )
        elif secured_field and not rejected_pairing:
            _mark(
                "scheme",
                "failed",
                label,
                f"every one of the {secured_field} valid pairing(s) was refused on field security: "
                "the caller cannot write a field on entity Scheme that the action sets. No value "
                f"fixes this — it is a permissions question for Morgan Lloyd ({secured_error})",
            )
        elif secured_field:
            _mark(
                "scheme",
                "failed",
                label,
                f"{rejected_pairing} pairing(s) rejected as invalid and {secured_field} accepted "
                "and then refused on FIELD SECURITY — the pairing space is understood, the caller "
                f"cannot write a field the action sets ({secured_error})",
            )
        else:
            _mark(
                "scheme",
                "failed",
                label,
                "no schemeType/sippType combination worked — ask Morgan Lloyd which "
                "SIPP Type applies to your firm and pin it with --sipp-type "
                f"(last error: {last_error})",
            )
        return None

    async with MorganLloydClient() as api:
        existing = {
            str(c.get("emailaddress1") or ""): str(c.get("contactid") or "")
            for c in await api.get_all_contacts()
        }
        for plan in plans:
            email = str(plan["contact"]["emailaddress1"])
            label = f"[{plan['_index']:03d}] {email}"
            if email in existing and not resume_existing:
                for kind in kinds:
                    _mark(kind, "skipped", label, "contact already exists")
                continue
            if email in existing:
                # --resume-existing: reuse the contact, build its downstream
                # records (a repaired earlier run left them missing). NB this
                # duplicates downstream records if they DO already exist.
                contact_id: str | None = existing[email] or None
                _mark("contact", "skipped", label, "exists — resuming downstream")
            else:
                # Contact first — everything else hangs off it.
                try:
                    contact_id = _contact_id(await api.create_contact(plan["contact"]))
                except MorganLloydError as exc:
                    _mark("contact", "failed", label, str(exc))
                    contact_id = None
                if contact_id is not None:
                    _mark("contact", "created", label)
            if contact_id is None:
                for kind in kinds:
                    if kind != "contact":
                        _mark(kind, "skipped", label, "no contact created")
                continue
            investments_before = counts["investment"]["created"]
            # Scheme — the transfer/beneficiary/contribution need its reference.
            scheme_ref = await _create_scheme_with_probe(
                api, {**plan["scheme"], "contactId": contact_id}, label
            )
            if scheme_ref is None:
                for kind in ("beneficiary", "transfer", "investment", "contribution"):
                    _mark(kind, "skipped", label, "no scheme reference")
            else:
                _mark("scheme", "created", f"{label} {scheme_ref}")
                for kind, call in (
                    ("beneficiary", api.create_beneficiary),
                    ("transfer", api.create_transfer),
                    ("investment", api.create_investment),
                    ("contribution", api.create_pension_contribution),
                ):
                    if kind not in kinds:
                        continue
                    payload = {**plan[kind], "schemeReference": scheme_ref}
                    if "cam_contributor" in payload:
                        payload["cam_contributor"] = contact_id
                    try:
                        await call(payload)
                        _mark(kind, "created", label)
                    except MorganLloydError as exc:
                        _mark(kind, "failed", label, str(exc))
            # ATR needs the CRM's int Client Reference Number, which Create
            # Contact doesn't return — hunt it on the created contact record.
            atr = plan["atr"]
            try:
                client_ref = _client_reference(await api.get_contact(contact_id))
            except MorganLloydError:
                client_ref = None
            if client_ref is None:
                _mark("atr", "skipped", label, "no client reference number on contact record")
            else:
                try:
                    await api.submit_atr(
                        client_id=client_ref,
                        risk_profile=int(atr["riskProfile"]),
                        date_generated=str(atr["dateGenerated"]),
                        atr_data=list(atr["atrData"]),
                    )
                    _mark("atr", "created", label)
                except MorganLloydError as exc:
                    _mark("atr", "failed", label, str(exc))

            # --- valuation: keyed on the POLICY, not the scheme, so it needs the investment's
            # policy number rather than the scheme reference. `provider` is the firm id.
            if "valuation" in kinds:
                if not provider:
                    _mark("valuation", "skipped", label, "no provider firm id configured")
                elif counts["investment"]["created"] == investments_before:
                    # Keyed on the POLICY, so without the investment there is nothing to value:
                    # the vendor answers `Failed Policies: … (missing policy)`, which reads as a
                    # valuation problem when it is the missing prerequisite.
                    _mark("valuation", "skipped", label, "no investment, so no policy to value")
                else:
                    try:
                        await api.create_policy_valuation(provider, [plan["valuation"]])
                        _mark("valuation", "created", label)
                    except MorganLloydError as exc:
                        _mark("valuation", "failed", label, str(exc))

            # --- benefit application: also mints the DCube ssoUrl the drawdown journey opens,
            # and its id is what drawdown needs, so the two are ordered and coupled.
            benefit_id: str | None = None
            if "benefit" in kinds:
                application = {
                    **plan["benefit"],
                    "contactId": contact_id,
                    **({"schemeRef": scheme_ref} if scheme_ref else {}),
                }
                try:
                    answer = await api.create_benefit_application(application)
                    benefit_id = envelope_value(answer, "benefitApplicationId")
                    _mark(
                        "benefit",
                        "created",
                        label,
                        "sso link returned"
                        if envelope_value(answer, "ssoUrl")
                        else "no ssoUrl in answer",
                    )
                except MorganLloydError as exc:
                    _mark("benefit", "failed", label, str(exc))

            if "drawdown" in kinds:
                if benefit_id is None:
                    _mark("drawdown", "skipped", label, "no benefitApplicationId")
                else:
                    try:
                        await api.drawdown_setup(
                            {**plan["drawdown"], "benefitApplicationId": benefit_id}
                        )
                        _mark("drawdown", "created", label)
                    except MorganLloydError as exc:
                        _mark("drawdown", "failed", label, str(exc))

            if "illustration" in kinds:
                if not illustration_url:
                    _mark("illustration", "skipped", label, "no --illustration-url")
                else:
                    try:
                        await api.submit_illustration(illustration_url, contact_id=contact_id)
                        _mark("illustration", "created", label)
                    except MorganLloydError as exc:
                        _mark("illustration", "failed", label, str(exc))

            # --- the PATCH paths. A different code path from each create: lookup fields need an
            # entity reference rather than a bare GUID, and the vendor answers 204 with no body.
            if "updates" in kinds:
                updates = plan["updates"]
                failures: list[str] = []
                try:
                    await api.update_contact(contact_id, updates["contact"])
                except MorganLloydError as exc:
                    failures.append(f"contact: {exc}")
                if failures:
                    _mark("updates", "failed", label, "; ".join(failures)[:200])
                else:
                    _mark("updates", "created", label, "contact patched")

    failed_total = sum(c["failed"] for c in counts.values())
    print("\nsummary:")
    for kind in RECORD_KINDS:
        c = counts[kind]
        # An unattempted kind is stated as such rather than printed as three zeros, which reads
        # as "tried and did nothing".
        if kind not in kinds:
            print(f"  {kind:12s} not attempted")
            continue
        print(f"  {kind:12s} created={c['created']} skipped={c['skipped']} failed={c['failed']}")
    return 1 if failed_total else 0


_TEST_EMAIL = re.compile(r"^ml-test-(\d+)@example\.grapheneplatforms\.com$", re.IGNORECASE)


def next_free_index(emails: Iterable[str]) -> int:
    """One past the highest ``ml-test-NNN`` already in the CRM.

    Keyed on the HIGHEST rather than the first gap: a gap means a client that failed part way
    through, and reusing its index would collide with the contact that did get created while
    leaving the hole anyway. Counting up is boring and always right.

    Only this script's own addresses count. A real contact at some other domain must not push the
    numbering along, and one at `ml-test-abc@` is not a numbered record at all.
    """
    highest = -1
    for email in emails:
        found = _TEST_EMAIL.match((email or "").strip())
        if found:
            highest = max(highest, int(found.group(1)))
    return highest + 1


def _chosen_kinds(only: str, *, drawdown: bool, illustration: bool) -> tuple[str, ...]:
    """Which record kinds to attempt.

    `--only` is exact: naming `drawdown` there runs it without `--drawdown`, because asking for
    one thing by name is already the explicit request the opt-in exists to require.
    """
    if only.strip():
        return tuple(k.strip() for k in only.split(",") if k.strip())
    chosen = [k for k in RECORD_KINDS if k not in OPT_IN_KINDS]
    if drawdown:
        chosen.append("drawdown")
    if illustration:
        chosen.append("illustration")
    return tuple(chosen)


async def _discover_adviser_contact(firm_id: str) -> str | None:
    """The firm's one adviser contact, or None when there is not exactly one.

    None on both zero and several, deliberately: with none there is nothing to attach clients to,
    and with several, choosing would file every test client under an adviser nobody named. The
    caller states the problem rather than guessing.
    """
    from morgan_lloyd import MorganLloydClient

    async with MorganLloydClient() as api:
        found = await api.get_financial_adviser_contacts(firm_id)
    ids = [str(c.get("contactid") or "") for c in found if c.get("contactid")]
    return ids[0] if len(ids) == 1 else None


async def _discover_start_index() -> int:
    """Ask the CRM where this script's numbering has got to.

    Its own connection, before the batch: `populate` fetches the same list to decide skips, but
    the emails have to exist before the plans are built, and the plans are built before `populate`
    is called. A second read of a dev org's contacts costs nothing next to guessing wrong.
    """
    from morgan_lloyd import MorganLloydClient

    async with MorganLloydClient() as api:
        contacts = await api.get_all_contacts()
    return next_free_index(str(c.get("emailaddress1") or "") for c in contacts)


def _is_dev_org() -> bool:
    """Whether the configured org looks like a development one.

    A host test rather than an allowlist: the dev org is `cliftonassetdev`, and any Dynamics
    sandbox follows the same convention. Crude, and crude in the safe direction -- a production
    host has to be waved through explicitly.
    """
    try:
        from morgan_lloyd.settings import MorganLloydSettings

        host = MorganLloydSettings().resource_url.lower()
    except Exception:  # noqa: BLE001 - unconfigured cannot reach a real org anyway
        return True
    return "dev" in host or "test" in host or "sandbox" in host or "uat" in host


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clients", type=int, default=8, help="number of test clients to create")
    parser.add_argument("--seed", type=int, default=20260714, help="deterministic RNG seed")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned payloads as JSON; no client, no HTTP",
    )
    parser.add_argument(
        "--scheme-type",
        type=int,
        default=DEFAULT_SCHEME_TYPE,
        help="schemeType for Create Scheme (default: the value Morgan Lloyd issued us)",
    )
    parser.add_argument(
        "--sipp-type",
        type=int,
        default=DEFAULT_SIPP_TYPE,
        help="firm-specific sippType (default: the value Morgan Lloyd issued us)",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="ignore the pinned scheme/SIPP types and walk every documented combination",
    )
    parser.add_argument(
        "--provider",
        default=DEFAULT_INVESTMENT_PROVIDER,
        help="Create Investment provider firm ID (default: the GUID Morgan Lloyd issued us)",
    )
    parser.add_argument(
        "--vulnerability-added-by",
        default=None,
        metavar="GUID",
        help="a real SystemUser GUID; enables the occasional vulnerable test contact "
        "(without it the CRM rejects the whole create)",
    )
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help="for contacts that already exist, reuse their GUID and create the "
        "downstream records anyway (repairs a partial earlier run; duplicates "
        "downstream records that DO already exist)",
    )
    parser.add_argument(
        "--adviser",
        choices=("firm", "none", "both"),
        default="firm",
        help="attach contacts to the adviser firm: every one (firm), none, or alternate (both). "
        "An unattached contact CREATES FINE and then fails at cam_createscheme with a 403 on an "
        "ML Team secured field, so `both` is how you see the difference in one run.",
    )
    parser.add_argument(
        "--adviser-firm",
        default=None,
        metavar="GUID",
        help="the adviser firm's Dynamics `accounts` GUID (default: MORGAN_LLOYD_ADVISER_FIRM_ID)",
    )
    parser.add_argument(
        "--drawdown",
        action="store_true",
        help="also set up drawdown. Opt-in: it is the one call here whose consequence is a "
        "payment to a member rather than a record.",
    )
    parser.add_argument(
        "--illustration-url",
        default="",
        metavar="URL",
        help="also submit an illustration from this URL. The vendor fetches it, so a localhost "
        "address will not do.",
    )
    parser.add_argument(
        "--only",
        default="",
        metavar="KINDS",
        help="comma-separated record kinds to attempt (default: everything except "
        f"{', '.join(OPT_IN_KINDS)}). Known: {', '.join(RECORD_KINDS)}",
    )
    parser.add_argument(
        "--adviser-contact",
        default=None,
        metavar="GUID",
        help="the adviser CONTACT's GUID, required alongside the firm (default: "
        "MORGAN_LLOYD_ADVISER_CONTACT_ID, else discovered from the firm when it has exactly one). "
        "A firm attached without one is refused: 'Financial Adviser contact must be populated'.",
    )
    parser.add_argument(
        "--start-index",
        default="auto",
        metavar="N",
        help="index of the first test client (default: auto — one past the highest ml-test-NNN "
        "already in the CRM, so a re-run creates NEW contacts instead of skipping every one and "
        "its downstream records). Pass a number to pin it — the same (seed, index) always "
        "builds the same payload, so a batch can be reproduced exactly.",
    )
    parser.add_argument(
        "--allow-non-dev",
        action="store_true",
        help="permit running against a resource URL that is not the dev org. The guard exists "
        "because none of these records can be deleted.",
    )
    args = parser.parse_args(argv)

    adviser_firm = args.adviser_firm or _configured("adviser_firm_id", None)
    if args.adviser != "none" and not adviser_firm:
        print(
            "error: --adviser "
            f"{args.adviser} needs the adviser firm's GUID — set MORGAN_LLOYD_ADVISER_FIRM_ID or "
            "pass --adviser-firm. It is the same value the vendor issues as Create Investment's "
            "`provider`, and it is NOT the Microsoft tenant id.",
            file=sys.stderr,
        )
        return 2

    adviser_contact = args.adviser_contact or _configured("adviser_contact_id", None)

    kinds = _chosen_kinds(
        args.only, drawdown=args.drawdown, illustration=bool(args.illustration_url)
    )
    unknown = sorted(set(kinds) - set(RECORD_KINDS))
    if unknown:
        print(f"error: unknown record kind(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    # Discovered rather than demanded: the vendor documents it as "See Get Financial Adviser
    # Contact to return the GUID", and a firm with one adviser needs no configuration for it.
    # REFUSES to choose when there are several -- picking one would silently file every test
    # client under an adviser nobody named.
    if args.adviser != "none" and not adviser_contact and not args.dry_run:
        adviser_contact = asyncio.run(_discover_adviser_contact(str(adviser_firm)))
        if adviser_contact is None:
            print(
                "error: the adviser firm has no single adviser contact to attach clients to. "
                "Set MORGAN_LLOYD_ADVISER_CONTACT_ID or pass --adviser-contact. The vendor "
                "refuses a contact attached to a firm without one.",
                file=sys.stderr,
            )
            return 2
        print(f"adviser contact      {adviser_contact} (discovered from the firm)")

    if args.start_index == "auto" and args.dry_run:
        # A dry run must not touch the network, and "auto" needs the CRM to answer. 0 keeps the
        # output reproducible, which is the only thing --dry-run is for.
        start_index = 0
    elif args.start_index == "auto":
        start_index = asyncio.run(_discover_start_index())
        print(f"start   index        {start_index} (one past the highest ml-test-NNN in the CRM)")
    else:
        try:
            start_index = int(args.start_index)
        except ValueError:
            print("error: --start-index takes a number or 'auto'", file=sys.stderr)
            return 2

    plans = build_plans(
        args.seed,
        args.clients,
        args.vulnerability_added_by,
        args.provider,
        args.adviser,
        str(adviser_firm) if adviser_firm else None,
        start_index,
        str(adviser_contact) if adviser_contact else None,
    )
    if args.dry_run:
        print(json.dumps(plans, indent=2))
        return 0

    if not args.allow_non_dev and not _is_dev_org():
        print(
            "refusing to run: MORGAN_LLOYD_RESOURCE_URL does not look like the dev org "
            "(expected a host containing 'dev'). Morgan Lloyd has no delete, so a run against a "
            "real org cannot be undone. Pass --allow-non-dev if you mean it.",
            file=sys.stderr,
        )
        return 2

    scheme_type = None if args.probe else args.scheme_type
    sipp_type = None if args.probe else args.sipp_type
    return asyncio.run(
        populate(
            plans,
            scheme_type,
            sipp_type,
            resume_existing=args.resume_existing,
            kinds=kinds,
            provider=args.provider,
            illustration_url=args.illustration_url,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
