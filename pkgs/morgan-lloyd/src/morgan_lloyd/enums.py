"""Dynamics 365 option-set values for the Morgan Lloyd (Clifton Asset Management) CRM API.

Every value in this module is transcribed from the API documentation at
https://dev.api.morgan-lloyd.co.uk/ and must not be invented or extrapolated — each member
traces to a documented "Allowed Values" list, lookup table or enum list in those docs.

Integer option sets are ``enum.IntEnum``; string-valued sets ("Yes"/"No" string flags,
"True"/"False" string booleans and ``entityType`` strings) are ``enum.StrEnum``. Multiselect
option sets (:class:`ContactVulnerability`) are sent to the API as comma-separated strings —
build them with :func:`multiselect`. Long documented labels (Tax Relief, vulnerability and
investment-pathway descriptions) are preserved in full in the ``*_DESCRIPTIONS`` mappings.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


def multiselect(*options: IntEnum) -> str:
    """Encode multiselect option-set members as the comma-separated string the API expects.

    Example from the docs: ``"809130000, 809130001"`` for ``cam_contactvulnerability``.
    """
    return ", ".join(str(int(o)) for o in options)


class ResponseKey(IntEnum):
    """``responseKey`` in the generic Api Response envelope.

    Documented as Success=0, Failed=1. Some update endpoints have been observed
    returning 200 for success in examples (e.g. Update Scheme); the documented
    option set is only 0/1.
    """

    SUCCESS = 0
    FAILED = 1


class YesNoFlag(IntEnum):
    """Integer Yes/No flags: ``cam_earmarking``, ``cam_sharing`` and ``cam_splitting`` on
    Create Transfer, and ``cam_financiallydependent`` on the relationship endpoints."""

    NO = 0
    YES = 1


class BooleanFlag(IntEnum):
    """Integer false/true flags: ``cam_previouslytakenflexiaccessbenefits`` on contact
    endpoints and ``origotransfer`` in transfer responses."""

    FALSE = 0
    TRUE = 1


class StringBool(StrEnum):
    """``cam_potentiallyvulnerable`` on Create/Update Contact — literally ``"True"`` or
    ``"False"`` (a string, not a JSON boolean)."""

    TRUE = "True"
    FALSE = "False"


class YesNoString(StrEnum):
    """String "1"/"0" Yes/No fields.

    Used by ``cam_doyouhaveawill``, ``cam_isituptodatealignwithyourcurrentwishes`` and
    ``cam_doyouhavealastingpowerofattorney`` on Create/Update Review Factfind, and by
    ``guidanceAppointmentAttendance``, ``adviceAppointmentAttendance``,
    ``recentlyReceivedGuidance``, ``recentlyReceivedAdvice`` and ``doesNotWantAdvice``
    on Drawdown Setup.
    """

    YES = "1"
    NO = "0"


HTTP_ERROR_MEANINGS: dict[int, str] = {
    400: "Bad Request — Your request is invalid.",
    401: "Unauthorized — Your API key is wrong.",
    403: "Forbidden — The request is hidden for administrators only.",
    404: "Not Found — The specified request could not be found.",
    405: "Method Not Allowed — You tried to access a request with an invalid method.",
    406: "Not Acceptable — You requested a format that isn't json.",
    410: "Gone — The request has been removed from our servers.",
    429: "Too Many Requests — You're requesting too much!",
    500: "Internal Server Error — We had a problem with our server. Try again later.",
    503: "Service Unavailable — We're temporarily offline for maintenance. Please try again later.",
}
"""HTTP error codes documented in the docs' Errors table, verbatim."""


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


class ContactStatus(IntEnum):
    """``as_contactstatus`` on Create Contact. Only Customer is documented."""

    CUSTOMER = 809130002


class Title(IntEnum):
    """``cam_title`` on contact endpoints and Update MVP Profile, and ``title`` on
    Create Financial Adviser Contact.

    Major (809130013) appears only in the Get Adviser Contacts response option set,
    not in the Create Contact request set.
    """

    MR = 809130000
    MS = 809130001
    MRS = 809130002
    MISS = 809130003
    DR = 809130004
    MASTER = 809130005
    CAPT = 809130006
    PROF = 809130007
    REVEREND = 809130008
    SIR = 809130009
    LORD = 809130010
    LADY = 809130011
    OTHER = 809130012
    MAJOR = 809130013  # responses only (Get Adviser Contacts)


class Gender(IntEnum):
    """``gendercode`` on contact endpoints and ``cam_legalsex`` on Update MVP Profile."""

    MALE = 1
    FEMALE = 2


class FamilyStatus(IntEnum):
    """``familystatuscode`` on contact endpoints and ``cam_familystatuscode`` on
    Update MVP Profile. Note the documented gaps at 3 and 4."""

    SINGLE = 1
    MARRIED = 2
    CIVIL_PARTNER = 5
    DIVORCED = 6
    COHABITING = 7
    WIDOWED = 8


class ContactEmploymentStatus(IntEnum):
    """``cam_employmentstatus`` on contact endpoints (Create/Update Contact).

    A different option set from :class:`FactfindEmploymentStatus`, which shares the
    field name on the factfind endpoints.
    """

    EMPLOYED = 809130000
    SELF_EMPLOYED = 809130001
    UNEMPLOYED = 809130002
    STUDENT = 809130003
    CHILD = 809130004
    CARER = 809130005
    CHILD_CARER = 809130006
    PENSIONER = 809130007
    OTHER = 809130008


class SourceOfWealth(IntEnum):
    """``cam_sourceofwealth`` on contact endpoints."""

    SALARY = 809130000
    VARIABLE_REMUNERATION = 809130001
    INVESTMENT_INCOME_OR_SALE_OF_INVESTMENTS = 809130002
    PENSION_INCOME = 809130003
    SALE_OF_MAIN_RESIDENCE = 809130004
    SALE_OF_OTHER_PROPERTY = 809130005
    BUSINESS_INTERESTS = 809130006
    INHERITANCE = 809130007  # docs spell this "Inheritence"
    GIFT = 809130008
    FINANCIAL_SETTLEMENTS = 809130009
    LOTTERY_BETTING_CASINO = 809130010
    OTHER = 809130011


class TaxRelief(IntEnum):
    """``cam_taxrelief`` on contact endpoints.

    The full documented label for each value is in :data:`TAX_RELIEF_DESCRIPTIONS`.
    """

    UK_RELEVANT_EARNINGS = 809130000
    UK_RESIDENT_THIS_YEAR = 809130001
    OVERSEAS_CROWN_EMPLOYMENT_NO_UK_EARNINGS = 809130002
    SPOUSE_OVERSEAS_CROWN_EMPLOYMENT = 809130003


TAX_RELIEF_DESCRIPTIONS: dict[TaxRelief, str] = {
    TaxRelief.UK_RELEVANT_EARNINGS: (
        "I have relevant UK earnings chargeable to income tax or general earnings from "
        "overseas Crown employment subject to UK tax, in this tax year"
    ),
    TaxRelief.UK_RESIDENT_THIS_YEAR: (
        "I have or will have been resident in the UK at some time during this year"
    ),
    TaxRelief.OVERSEAS_CROWN_EMPLOYMENT_NO_UK_EARNINGS: (
        "I, or my spouse, are in overseas Crown employment, but for this tax year do not "
        "have general earnings from overseas Crown employment subject to UK tax"
    ),
    TaxRelief.SPOUSE_OVERSEAS_CROWN_EMPLOYMENT: (
        "My spouse has for this tax year general earnings from overseas Crown employment "
        "subject to UK tax"
    ),
}
"""Full documented labels for the ``cam_taxrelief`` Tax Relief Lookup table."""


class ContactVulnerability(IntEnum):
    """``cam_contactvulnerability`` — a multiselect option set on contact endpoints.

    Sent as a comma-separated string of the integer values (use :func:`multiselect`).
    The full documented description of each value is in
    :data:`VULNERABILITY_DESCRIPTIONS`.
    """

    HEALTH = 809130000
    DISABILITY = 809130001
    LIFE_EVENTS = 809130002
    RESILIENCE = 809130003
    CAPABILITY = 809130004
    AGE = 809130005
    MISCELLANEOUS_OTHER = 809130006


VULNERABILITY_DESCRIPTIONS: dict[ContactVulnerability, str] = {
    ContactVulnerability.HEALTH: (
        "Conditions or illnesses affecting a customer's ability to carry out day-to-day "
        "tasks, e.g. long-term illness, severe illness, mental health problems, etc"
    ),
    ContactVulnerability.DISABILITY: "Physical disability, sight or hearing problems",
    ContactVulnerability.LIFE_EVENTS: (
        "Bereavement, job loss or relationship breakdown/divorce, etc"
    ),
    ContactVulnerability.RESILIENCE: (
        "Low ability to withstand emotional or financial shocks, including low income "
        "and/or debt problems"
    ),
    ContactVulnerability.CAPABILITY: (
        "Low knowledge of financial matters or low confidence in managing money (financial "
        "capability). Or low capability in other relevant areas such as literacy or "
        "digital skills"
    ),
    ContactVulnerability.AGE: (
        "E.g. 75+ (not always the case, but something to consider) or young clients (who "
        "may have less experience and may not understand the implications of decisions)"
    ),
    ContactVulnerability.MISCELLANEOUS_OTHER: (
        "Caring responsibilities (including powers of attorney), lack of English skills, other"
    ),
}
"""Full documented descriptions for the ``cam_contactvulnerability`` multiselect values."""


class RelevantBusinessUnit(IntEnum):
    """``cam_relevantbusinessunit`` on Create Contact (vulnerability fields).
    Only CWP is documented."""

    CWP = 809130001


class PreferredContactMethod(IntEnum):
    """``preferredcontactmethodcode`` in the Get Adviser Contacts response."""

    ANY = 1
    EMAIL = 2
    PHONE = 3
    FAX = 4
    MAIL = 5
    TWITTER = 6


class AnnualReviewFrequency(IntEnum):
    """``cam_annualreviewfrequency`` in the Get Adviser Contacts response."""

    MONTHLY = 809130000
    QUARTERLY = 809130001
    HALF_YEARLY = 809130002
    ANNUALLY = 809130003


class OwnershipStatus(IntEnum):
    """``cam_ownershipstatus`` (address ownership) on Update MVP Profile."""

    OWN_MORTGAGED = 809130000
    OWN_NO_MORTGAGE = 809130001
    RENTING = 809130002
    OTHER = 809130003


# ---------------------------------------------------------------------------
# Factfinds
# ---------------------------------------------------------------------------


class StateOfHealth(IntEnum):
    """``cam_stateofhealthmedicalconditions`` on the factfind endpoints."""

    EXCELLENT = 809130000
    GOOD = 809130001
    POOR = 809130002


class FactfindEmploymentStatus(IntEnum):
    """``cam_employmentstatus`` on the factfind endpoints (Get Client Factfind,
    Create/Update Review Factfind).

    A different option set from :class:`ContactEmploymentStatus`, which shares the
    field name on the contact endpoints.
    """

    EMPLOYED = 809130000
    SELF_EMPLOYED = 809130001
    DIRECTOR = 809130002
    RETIRED = 809130003
    CARER = 809130004
    OTHER = 809130005


class CapacityForLoss(IntEnum):
    """``cam_agreedcapacityforloss`` on Create/Update Review Factfind and
    ``attitudetorisk_x002e_cam_agreedcapacityforloss`` in the Get Client Factfind
    response."""

    HIGH = 809130000
    MEDIUM = 809130001
    LOW = 809130002
    NOT_APPLICABLE = 809130003  # docs label this "N/A"


class RiskLevel(IntEnum):
    """``cam_risklevel1`` on Create/Update Review Factfind (the example body uses
    ``cam_risklevel``) and ``cam_risklevel`` in the Get Client Factfind response.

    Member names follow the Create/Update labels ("1. Very Cautious" … "10. Very
    Adventurous"); Get Client Factfind documents the same values with plain numeric
    labels 1–10.
    """

    VERY_CAUTIOUS = 809130000  # "1. Very Cautious"
    CAUTIOUS = 809130001  # "2. Cautious"
    LOW_END_OF_CAUTIOUS_TO_MODERATE = 809130002  # "3. Low end of Cautious to Moderate"
    CAUTIOUS_TO_MODERATE = 809130003  # "4. Cautious to Moderate"
    LOW_END_OF_MODERATE = 809130004  # "5. Low end of Moderate"
    MODERATE = 809130005  # "6. Moderate"
    LOW_END_OF_MODERATE_TO_ADVENTUROUS = 809130006  # "7. Low end of Moderate to Adventurous"
    MODERATE_TO_ADVENTUROUS = 809130007  # "8. Moderate to Adventurous"
    ADVENTUROUS = 809130008  # "9. Adventurous"
    VERY_ADVENTUROUS = 809130009  # "10. Very Adventurous"


class SuitabilityConfirmation(IntEnum):
    """``cam_suitabilityconfirmation`` on Create/Update Review Factfind and
    ``review_x002e_cam_suitabilityconfirmation`` in the Get Client Factfind response.

    Full documented labels are in :data:`SUITABILITY_CONFIRMATION_DESCRIPTIONS`.
    """

    NO_CHANGES_SUITABILITY_CONFIRMED = 809130000
    CHANGES_NOT_MATERIAL_SUITABILITY_CONFIRMED = 809130001
    CHANGES_MATERIAL_ARRANGEMENTS_REMAIN_SUITABLE = 809130002
    CHANGES_MATERIAL_ARRANGEMENTS_NO_LONGER_SUITABLE = 809130003


SUITABILITY_CONFIRMATION_DESCRIPTIONS: dict[SuitabilityConfirmation, str] = {
    SuitabilityConfirmation.NO_CHANGES_SUITABILITY_CONFIRMED: ("No changes, suitability confirmed"),
    SuitabilityConfirmation.CHANGES_NOT_MATERIAL_SUITABILITY_CONFIRMED: (
        "Changes, not material, suitability confirmed"
    ),
    SuitabilityConfirmation.CHANGES_MATERIAL_ARRANGEMENTS_REMAIN_SUITABLE: (
        "Changes material, new advice needed but existing arrangements remain suitable"
    ),
    SuitabilityConfirmation.CHANGES_MATERIAL_ARRANGEMENTS_NO_LONGER_SUITABLE: (
        "Changes material, new advice but existing arrangements no longer suitable"
    ),
}
"""Full documented labels for ``cam_suitabilityconfirmation``."""


class Priority(IntEnum):
    """``cam_priority1`` … ``cam_priority8`` on the factfind endpoints."""

    DEFERRED = 809130000
    PRIORITY_1 = 809130001
    PRIORITY_2 = 809130002
    PRIORITY_3 = 809130003
    PRIORITY_4 = 809130004
    PRIORITY_5 = 809130005
    PRIORITY_6 = 809130006
    PRIORITY_7 = 809130007
    PRIORITY_8 = 809130008
    PRIORITY_9 = 809130009
    PRIORITY_10 = 809130010


class SoftFactTopic(IntEnum):
    """``cam_topic`` on Create/Update Soft Facts."""

    LIFE_EVENT = 809130000
    FAMILY_CONTEXT = 809130001
    WORK_CONTEXT = 809130002
    HEALTH_CONTEXT = 809130003
    FINANCIAL_CONTEXT = 809130004
    PREFERENCE = 809130005
    CONSTRAINT = 809130006
    OTHER = 809130007


class CustomerTypeCode(IntEnum):
    """``customertypecode`` on the built-in ``accounts`` entity set — the documented
    Get Employer Accounts Filtered query filters on 809130001 (employer accounts)."""

    EMPLOYER = 809130001


class AssetEntityType(StrEnum):
    """``entityType`` filter values on Get Client Assets (also the entity logical name
    returned per asset)."""

    PERSONAL_POLICY = "cam_personalpolicy"
    POLICY = "cam_policy"
    SCHEME = "cam_scheme"


class AssetStatus(IntEnum):
    """``cam_status`` on Create/Update Personal Asset; Create Transfer documents only
    Transferring=4. No value 3 is documented."""

    EXISTING = 1
    TRANSFERRED = 2
    TRANSFERRING = 4
    NOT_TRANSFERRING = 5


class AssetType(IntEnum):
    """``cam_assettype`` (documented as ``cam_assetType``) on Create/Update Personal
    Asset; Create Transfer documents only "Pension Plan"=1 (singular label there).

    Mixes one 8091300xx value (Care Planning) with small integers 1–7.
    """

    CARE_PLANNING = 809130000
    PENSION_PLANS = 1  # Create Transfer labels this "Pension Plan"
    PROPERTY = 2
    INVESTMENT = 3
    RETIREMENT_PLANNING = 4
    PROTECTION = 5
    SAVINGS_AND_DEPOSITS = 6
    OTHER = 7


class InvestmentType(IntEnum):
    """``cam_investmenttype`` on Create/Update Personal Asset (Investment exclusive)."""

    INVESTMENT_BOND = 809130000
    OFFSHORE_INVESTMENT = 809130001
    COLLECTIVE_INVESTMENT = 809130002
    GIA = 809130003
    STRUCTURED_PRODUCT = 809130004
    ISA = 809130005
    OTHER = 809130006
    SHAREDEALING_ACCOUNT = 809130007
    OFFSHORE_BOND = 809130008
    JISA = 809130009
    CASH = 809130010
    SAVINGS = 809130011
    TRUST = 809130012
    EIS = 809130013
    VCT = 809130014
    BUSINESS_RELIEF = 809130015
    ISA_AIM_IHT = 809130016  # docs label this "ISA (AIM / IHT)"
    ONSHORE_BOND_LOAN_TRUST = 809130017
    ONSHORE_BOND_DISCOUNTED_GIFT_TRUST = 809130018
    OFFSHORE_BOND_LOAN_TRUST = 809130019
    OFFSHORE_BOND_DISCOUNTED_GIFT_TRUST = 809130020
    INHERITANCE_TAX_SCHEME = 809130021
    UNLISTED_SHARES = 809130022
    LISTED_SHARES = 809130023
    DIRECTORS_LOAN = 809130024
    PREMIUM_BONDS = 809130025
    CURRENT_ACCOUNT = 809130026
    ONSHORE_BOND = 809130027
    ISA_STOCKS_AND_SHARES = 809130028  # docs label this "ISA (Stocks & Shares)"
    ISA_CASH = 809130029  # docs label this "ISA (Cash)"
    ISA_LIFETIME = 809130030  # docs label this "ISA (Lifetime)"


class PensionType(IntEnum):
    """``cam_pensiontype`` on Create/Update Personal Asset (Pension exclusive) and
    Create Transfer — the same 1–20 option set on both."""

    ADDITIONAL_VOLUNTARY_CONTRIBUTION = 1
    ANNUITY = 2
    APPP_PROTECTED_RIGHTS = 3
    BULK_BUY_OUT_PLAN = 4
    CONTRACTED_IN_MONEY_PURCHASE = 5
    CONTRACTED_OUT_MONEY_PURCHASE = 6
    DRAWDOWN = 7
    EXECUTIVE_PERSONAL_PENSION = 8
    FINAL_SALARY_SCHEME = 9
    FREE_STANDING_ADDITIONAL_VOLUNTARY_CONTRIBUTION = 10
    GROUP_PERSONAL_PENSION = 11
    GROUP_STAKEHOLDER_PENSION = 12
    OTHER = 13
    PERSONAL_PENSION_PLAN = 14
    QROPS = 15
    RETIREMENT_ANNUITY_CONTRACT = 16
    SECTION_32_POLICY_BUY_OUT_BOND = 17
    SELF_INVESTED_PERSONAL_PENSION = 18
    SMALL_SELF_ADMINISTERED_SCHEME = 19
    STAKEHOLDER = 20


class ProtectionType(IntEnum):
    """``cam_protectiontype`` on Create/Update Personal Asset (Protection exclusive)."""

    WHOLE_OF_LIFE = 809130000
    RELEVANT_LIFE = 809130001
    ENDOWMENT = 809130002
    INCREASING = 809130003
    FAMILY_INCOME_BENEFIT = 809130004
    INCOME_PROTECTION = 809130005
    LEVEL_TERM_ASSURANCE = 809130006
    DECREASING = 809130007
    KEY_PERSON = 809130008
    EXECUTIVE_INCOME_PROTECTION = 809130009
    PERSONAL_PROTECTION = 809130010
    GIFT_INTER_VIVOS = 809130011
    SHAREHOLDER = 809130012
    BUSINESS_LOAN = 809130013
    PERMANENT_HEALTH_INSURANCE = 809130014
    MEDICAL = 809130015
    CRITICAL_ILLNESS_COVER = 809130016
    DEATH_IN_SERVICE = 809130017
    MEDICAL_INDIVIDUAL = 809130018  # docs label this "Medical (Individual)"
    GROUP_PRIVATE_MEDICAL = 809130019
    DECREASING_TERM_ASSURANCE = 809130020


class ProtectionPremiumBasis(IntEnum):
    """``cam_protectionpremiumbasis`` on Create/Update Personal Asset."""

    GUARANTEED = 809130000
    REVIEWABLE = 809130001
    RENEWABLE = 809130002


class ProtectionPremiumFrequency(IntEnum):
    """``cam_protectionfrequency`` on Create/Update Personal Asset.

    Documented as type "string" but the allowed values are option-set integers (the
    docs' example sends a bare ``0`` — an inconsistency flagged in the docs extract).
    """

    MONTHLY = 809130000
    ANNUALLY = 809130001
    QUARTERLY = 809130002
    BI_ANNUALLY = 809130003
    ADHOC = 809130004


class UnderwritingTerms(IntEnum):
    """``cam_protectionunderwritingterms`` on Create/Update Personal Asset."""

    ORDINARY_RATES = 809130000
    RATED = 809130001
    DECLINED = 809130002
    DEFERRED = 809130003


class ContributionFrequency(IntEnum):
    """Pension-plan asset contribution frequency: ``cam_personalcontributionfrequency``
    and ``cam_employercontributionsfrequency`` on Create/Update Personal Asset, and
    ``personalContributionFrequency``/``employerContributionFrequency`` in the
    Get Client Assets response."""

    MONTHLY = 809130000
    ANNUALLY = 809130001


class LiabilityType(IntEnum):
    """``cam_liabilitytype`` on the liability endpoints."""

    MORTGAGE = 809130000
    CREDIT_CARD = 809130001
    LOAN = 809130002
    CAR_FINANCE = 809130003
    OTHER = 809130004


class MortgageType(IntEnum):
    """``cam_mortgagetype`` on the liability endpoints."""

    REPAYMENT = 809130000
    INTEREST_ONLY = 809130001
    INTEREST_AND_REPAYMENT = 809130002


RELATIONSHIP_TYPE_IDS: dict[str, str] = {
    "Child": "df0bf69f-333c-4e9b-86e7-4ff737bc9343",
    "Grandchild": "66141238-4859-ea11-b9a1-00155d0a0d10",
    "Grandparent": "053ef59a-4959-ea11-b9a1-00155d0a0d10",
    "Parent": "eda69fc6-0b5f-44fb-b584-7dfeb8a925af",
    "Sibling": "14b87289-dacf-e511-80c8-00155d0a2405",
    "Spouse": "ee375944-5415-437d-9336-7698cf665b26",
}
"""Relationship Type GUIDs for ``record1roleid``/``record2roleid`` on
Create/Update Relationship, verbatim from the docs' lookup table."""

RELATIONSHIP_TYPE_RECIPROCALS: dict[str, str] = {
    "Child": "Parent",
    "Grandchild": "Grandparent",
    "Grandparent": "Grandchild",
    "Parent": "Child",
    "Sibling": "Sibling",
    "Spouse": "Spouse",
}
"""The "Related Relationship Type" column of the docs' Relationship Types lookup table."""


class RegardingEntityType(StrEnum):
    """``entityType`` on Create Note and Create Task — the only documented value is
    ``cam_annualreview``."""

    ANNUAL_REVIEW = "cam_annualreview"


class OwnerEntityType(StrEnum):
    """``ownerEntityType`` on Create Task."""

    SYSTEMUSER = "systemuser"
    TEAM = "team"


# ---------------------------------------------------------------------------
# Schemes & Beneficiaries
# ---------------------------------------------------------------------------


class SchemeType(IntEnum):
    """``schemeType`` on Create Scheme.

    Both 16 and 17 are documented under the single label "ML SIPP".
    """

    ML_SIPP_16 = 16
    ML_SIPP_17 = 17


class SippType(IntEnum):
    """``sippType`` on Create Scheme.

    The docs list allowed values only, with no labels — "For more information on what
    SIPP Type to use for your firm, please contact Morgan Lloyd" — so members carry
    ``VALUE_``-style names. Note 809130004 is NOT in the documented allowed list.
    """

    VALUE_809130003 = 809130003
    VALUE_809130005 = 809130005
    VALUE_809130006 = 809130006
    VALUE_809130007 = 809130007
    VALUE_809130008 = 809130008
    VALUE_809130009 = 809130009
    VALUE_809130010 = 809130010
    VALUE_809130011 = 809130011
    VALUE_809130012 = 809130012
    VALUE_809130013 = 809130013
    VALUE_809130014 = 809130014
    VALUE_809130015 = 809130015
    VALUE_809130016 = 809130016
    VALUE_809130017 = 809130017


class BeneficiaryStatus(IntEnum):
    """``cam_beneficiarystatus`` on Update Beneficiary.

    Removed=0 is the only documented value (set it to remove a beneficiary). Examples
    elsewhere show ``1`` for an active beneficiary but that label is undocumented, so
    no member is defined for it.
    """

    REMOVED = 0


# ---------------------------------------------------------------------------
# Transfers
# ---------------------------------------------------------------------------


class TypeOfTransfer(IntEnum):
    """``cam_typeoftransfer`` on Create Transfer."""

    CASH = 0
    IN_SPECIE = 1


class TransferType(IntEnum):
    """``cam_transfertype`` on Create Transfer."""

    FULL = 809130000
    PARTIAL = 809130001


class CrystallisedType(IntEnum):
    """``cam_crystallised`` on Create Transfer."""

    FULL = 0
    PARTIAL = 1


class DrawdownType(IntEnum):
    """``cam_drawdowntype`` on Create Transfer."""

    CAPPED_DRAWDOWN = 0
    FLEXI_ACCESS_DRAWDOWN = 1


class TransferStatus(IntEnum):
    """Transfer progress status: ``transferstatus`` in the Get Transfer response and
    ``cam_transferstatus`` in the Get All Transfers by Scheme response.

    Distinct from the transfer record's own ``cam_status`` (:class:`AssetStatus`).
    """

    AWAITING_FORMS = 0
    ML_TO_REQUEST_TRANSFER = 1
    AWAITING_ACTION_FROM_CEDING_SCHEME = 2
    FUNDS_RECEIVED = 3
    FUNDS_SENT = 4
    NO_LONGER_TRANSFERRING = 5


# ---------------------------------------------------------------------------
# Pension Contributions
# ---------------------------------------------------------------------------


class ContributionType(IntEnum):
    """``cam_contributiontype`` on the ``cam_contribution`` entity — Create Pension
    Contribution, Update Pension Contribution, Upload Pension Contributions and the
    Get Pension Contribution Applications response.

    NOT the option set used by Get Received Contributions — see
    :class:`ReceivedContributionType`.
    """

    PERSONAL = 809130000
    EMPLOYER = 809130001
    OTHER = 809130002


class ReceivedContributionType(IntEnum):
    """``cam_contributiontype`` on the ``cam_pensioncontribution`` entity — the
    Get Received Contributions response ONLY.

    A different option set from :class:`ContributionType` (verbatim from the docs,
    inconsistent with the 8091300xx set used elsewhere).
    """

    PERSONAL = 0
    EMPLOYER = 1
    OTHER = 809130000


class PensionContributionFrequency(IntEnum):
    """``cam_frequency`` on the pension contribution endpoints.

    Get Pension Contribution Applications labels 809130002 "Half-Yearly" (hyphenated)
    where the other endpoints say "Half Yearly" — same value.
    """

    MONTHLY = 809130000
    QUARTERLY = 809130001
    HALF_YEARLY = 809130002
    ANNUALLY = 809130003
    SINGLE = 809130004


class ContributionStatus(IntEnum):
    """``cam_status`` on the pension contribution endpoints.

    Update Contribution Application documents Stopped as the only allowed status value.
    """

    APPLICATION = 809130000
    ACTIVE = 809130001
    STOPPED = 809130002
    RECEIVED = 809130003


class PaymentType(IntEnum):
    """``cam_paymenttype`` in the Get Pension Contribution Applications response.
    Uses plain 0–3, not 8091300xx values."""

    STANDING_ORDER = 0
    DIRECT_DEBIT = 1
    ELECTRONIC_TRANSFER = 2
    CHEQUE = 3


# ---------------------------------------------------------------------------
# Benefits
# ---------------------------------------------------------------------------


class TransactionDirection(IntEnum):
    """``direction`` in the Get Bank Account Transactions response (returned as a
    string containing the integer)."""

    IN = 1
    OUT = 2


class WithdrawalFrequency(IntEnum):
    """``frequency`` on the ``withdrawals`` items in the Retrieve Member Details
    response."""

    SINGLE = 0
    ANNUAL = 1
    HALF_YEARLY = 2
    QUARTERLY = 4
    BIMONTHLY = 6
    MONTHLY = 12
    FORTNIGHTLY = 26
    WEEKLY = 52


class DrawdownOption(IntEnum):
    """``drawdownOption`` on Drawdown Setup, ``withdrawalType`` on Cancel Drawdown and
    ``drawdownOption`` on Retrieve Member Details withdrawals — the docs give the same
    option set for all three."""

    FULL_TFC = 2
    PARTIAL_TFC = 3
    FULL_UFPLS = 4
    PARTIAL_UFPLS = 5
    FULL_TFC_LUMP_SUM_INCOME = 6
    PARTIAL_TFC_LUMP_SUM_INCOME = 7
    FULL_LUMP_SUM_INCOME = 8
    PARTIAL_LUMP_SUM_INCOME = 9
    REGULAR_INCOME = 12
    REGULAR_UFPLS = 13
    REGULAR_TFC = 14
    CUSTOM_REGULAR_INCOME = 15
    FULL_TFC_REGULAR_INCOME = 21
    PARTIAL_TFC_REGULAR_INCOME = 22
    PARTIAL_TFC_LUMP_SUM_INCOME_REGULAR_INCOME = 23
    PARTIAL_TFC_REGULAR_UFPLS = 24
    PARTIAL_TFC_LUMP_SUM_INCOME_REGULAR_UFPLS = 25
    PARTIAL_TFC_REGULAR_TFC = 26
    PARTIAL_TFC_LUMP_SUM_INCOME_REGULAR_TFC = 27
    FULL_LUMP_SUM_INCOME_REGULAR_INCOME = 28
    PARTIAL_LUMP_SUM_INCOME_REGULAR_INCOME = 29
    PARTIAL_UFPLS_PHASED_UFPLS_INCOME = 30
    FULL_PCLS_CUSTOM_REGULAR_INCOME = 32
    PARTIAL_PCLS_CUSTOM_REGULAR_INCOME = 33
    FULL_UFPLS_CUSTOM_REGULAR_INCOME = 34
    PARTIAL_UFPLS_CUSTOM_REGULAR_INCOME = 35
    FULL_PCLS_SINGLE_PAYMENT_CUSTOM_REGULAR_INCOME = 36
    PARTIAL_PCLS_SINGLE_PAYMENT_CUSTOM_REGULAR_INCOME = 37
    FULL_SINGLE_PAYMENT_CUSTOM_REGULAR_INCOME = 38
    PARTIAL_SINGLE_PAYMENT_CUSTOM_REGULAR_INCOME = 39


class IncomeFrequency(IntEnum):
    """``incomeFrequency`` on Drawdown Setup — number of payments per annum."""

    YEARLY = 1
    HALF_YEARLY = 2
    QUARTERLY = 4
    BI_MONTHLY = 6
    MONTHLY = 12


class InvestmentPathway(IntEnum):
    """``investmentPathway`` on Drawdown Setup.

    Full documented labels are in :data:`INVESTMENT_PATHWAY_DESCRIPTIONS`.
    """

    NO_PLANS_TO_TOUCH_MONEY = 1
    GUARANTEED_INCOME_ANNUITY = 2
    LONG_TERM_INCOME = 3
    TAKE_OUT_ALL_MONEY = 4
    SELECT_OWN_INVESTMENTS = 5
    REMAIN_IN_CURRENT_INVESTMENTS = 6


INVESTMENT_PATHWAY_DESCRIPTIONS: dict[InvestmentPathway, str] = {
    InvestmentPathway.NO_PLANS_TO_TOUCH_MONEY: (
        "I have no plans to touch my money in the next 5 years"
    ),
    InvestmentPathway.GUARANTEED_INCOME_ANNUITY: (
        "I plan to use my money to set up a guaranteed income (annuity) within the next 5 years"
    ),
    InvestmentPathway.LONG_TERM_INCOME: (
        "I plan to start taking my money as a long-term income within the next 5 years"
    ),
    InvestmentPathway.TAKE_OUT_ALL_MONEY: (
        "I plan to take out all my money within the next 5 years"
    ),
    InvestmentPathway.SELECT_OWN_INVESTMENTS: (
        "I want to select investments without using the Investment Pathways"
    ),
    InvestmentPathway.REMAIN_IN_CURRENT_INVESTMENTS: (
        "I want to remain invested in my current investments"
    ),
}
"""Full documented labels for the ``investmentPathway`` options on Drawdown Setup."""


class PaymentDay(IntEnum):
    """``paymentDay`` on Drawdown Setup — the only accepted values are 6, 14, 21, 28."""

    DAY_6 = 6
    DAY_14 = 14
    DAY_21 = 21
    DAY_28 = 28


class BenefitEventType(IntEnum):
    """``eventType`` in the Get Benefit Events response."""

    NOMINATION_OF_BENEFICIARY = 2
    WITHDRAWAL = 4
    DRAWDOWN_REVIEW = 5
    DEATH_BENEFITS = 7
    SERIOUS_ILL_HEALTH = 8
    TRANSITIONAL_PROTECTION = 809130002


class BenefitEventStatus(IntEnum):
    """``eventStatus`` on Get Benefit Events (request filter and response)."""

    IN_PROGRESS = 1
    LIVE = 2
    NOT_TAKEN_UP = 3
    STOPPED = 4
    APPLICATION = 809130000


class BenefitsType(IntEnum):
    """``benefitsType`` in the Get Benefit Events response."""

    TAX_FREE_CASH = 0
    TAX_FREE_CASH_AND_INCOME = 1
    UFPLS = 2
    INCOME = 3


class BenefitPaymentFrequency(IntEnum):
    """``paymentFrequency`` in the Get Benefit Events response."""

    MONTHLY = 1
    QUARTERLY = 2
    ANNUALLY = 3
    HALF_YEARLY = 4
    BIMONTHLY = 5
    AD_HOC = 100000000


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


class DocumentType(IntEnum):
    """``documentType`` on Submit Document — how the ``document`` field is supplied."""

    BASE64_ENCODE = 1
    URL = 2


class DocumentFolder(IntEnum):
    """``documentFolder`` on Submit Document — the destination folder."""

    BCE = 1
    ML_MY_VIEWPOINT = 2  # docs label this "ML - My Viewpoint"
    ADVISER_MY_VIEWPOINT = 3  # docs label this "Adviser - My Viewpoint"
    KFI_SMPI = 4
    ILLUSTRATION = 5
    APPLICATION = 6
    ATR = 7
    ANNUAL_REVIEW = 8
    FACTFIND = 9


# ---------------------------------------------------------------------------
# Threads & Messages
# ---------------------------------------------------------------------------


class MessageType(IntEnum):
    """``cam_messagetype`` on Create Message — the message origin. Documented as type
    "string" but takes these integer option-set values (the example sends ``0``)."""

    FROM_OPERATOR = 0
    FROM_CLIENT = 1


class MessageFlag(IntEnum):
    """``cam_flag`` on Create Message and Update Message."""

    NONE = 0
    READ = 1
    DELETED = 2
    VIEWED = 3


class AttachmentFlag(IntEnum):
    """``cam_attachmentflag`` on Create Message and Update Message."""

    NOT_READ = 0
    READ = 1
