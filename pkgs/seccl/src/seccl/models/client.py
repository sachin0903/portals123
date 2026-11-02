"""Client models, generated from the vendored Postman collection.

Do not edit by hand: `moon run seccl:build-schemas && moon run seccl:generate-models`.
The types are inferred from 39 example payload(s), not from a published schema --
see pkgs/seccl/README.md for what that does and does not buy you.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class AddressDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    flat_number: Annotated[str | None, Field(alias="flatNumber")] = None
    """
    observed values: 301, Bedrock
    """
    address1: str
    address2: str | None = None
    """
    observed values: Bedrock, Doncaster, New Edlington
    """
    country: str
    """
    observed values: GB
    """
    post_code: Annotated[str, Field(alias="postCode")]
    """
    observed values: B70 777, BA2 4DS, BE1 R0K, BR1 6PR, BR1 6RK, DN13 4GG, XXXX XXX
    """
    building_number: Annotated[str | None, Field(alias="buildingNumber")] = None
    """
    observed values: 1, 179, 201, 301
    """
    address3: str | None = None
    """
    observed values: Leicester
    """
    address4: str | None = None
    """
    observed values: South Yorkshire
    """
    building_name: Annotated[str | None, Field(alias="buildingName")] = None
    """
    observed values: Rubble House
    """


class AmlCheck(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    vendor: str
    """
    observed values: Northrow
    """
    trigger: str
    """
    observed values: Bank Details Changed, New Client Onboarding
    """
    outcome: str
    """
    observed values: Accepted, Verified
    """
    check_profile: Annotated[str, Field(alias="checkProfile")]
    """
    observed values: northrow-profile-uuid
    """
    check_uuid: Annotated[str, Field(alias="checkUuid")]
    """
    observed values: cool-northrow-check-id
    """
    completed_at: Annotated[AwareDatetime, Field(alias="completedAt")]


class AmlData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    check_date: Annotated[AwareDatetime, Field(alias="checkDate")]
    check_node: Annotated[list[str], Field(alias="checkNode")]
    aml_evidence_seen: Annotated[bool, Field(alias="amlEvidenceSeen")]
    check_result: Annotated[str, Field(alias="checkResult")]
    """
    observed values: BYPASSED, PENDING
    """
    checks: list[Any]


class AuditDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    update_date: Annotated[AwareDatetime, Field(alias="updateDate")]
    user_firm_id: Annotated[str, Field(alias="userFirmId")]
    """
    observed values: BACKGROUND, SCUST, TEST1
    """
    user_id: Annotated[str, Field(alias="userId")]
    """
    observed values: 029B3F4, 029B3FF, Background
    """
    version: int
    application: str


class ClientGroups(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    client_fee_group_id: Annotated[str | None, Field(alias="clientFeeGroupId")]


class CommsStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    welcome_sent: Annotated[bool, Field(alias="welcomeSent")]


class CreatedBy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    user_id: Annotated[str, Field(alias="userId")]
    """
    observed values: 029B3F3, 029B3FF
    """
    user_firm_id: Annotated[str, Field(alias="userFirmId")]
    """
    observed values: SECCL, TEST1
    """
    user_type: Annotated[str, Field(alias="userType")]
    """
    observed values: Machine, User
    """
    user_name: Annotated[str, Field(alias="userName")]
    """
    observed values: Machine User
    """


class ExternalId(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id_type: Annotated[str, Field(alias="idType")]
    """
    observed values: NorthrowCaseId
    """
    value: str


class FeeStructures(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    adviser_fee_id: Annotated[str | None, Field(alias="adviserFeeId")] = None
    """
    observed values: 64f1a2b3c4d5e6f7a8b9c0d1
    """
    platform_fee_id: Annotated[str | None, Field(alias="platformFeeId")] = None


class Identifier(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    value: str | None = None
    """
    observed values: ABCDEF99A31A123A, AL790272C, PC672672C, PC892898C, PC892898D
    """
    available: bool | None = None
    regex: list[str] | None = None
    locale_name: Annotated[str | None, Field(alias="localeName")] = None
    """
    observed values: CONCAT, Henkilötunnus, National Passport Number
    """
    example: str | None = None
    """
    observed values: 19360821MARY#MIGGI, 311299+123X, AB12345678
    """
    priority: float | None = None


class KnowYourBusiness(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    checks_performed: Annotated[str, Field(alias="checksPerformed")]
    """
    observed values: 2024-05-20
    """


class KycData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    domestic_pep: Annotated[bool, Field(alias="domesticPep")]
    domestic_associated_pep: Annotated[bool, Field(alias="domesticAssociatedPep")]
    foreign_pep: Annotated[bool, Field(alias="foreignPep")]
    foreign_associated_pep: Annotated[bool, Field(alias="foreignAssociatedPep")]
    sanctioned: bool


class Mobile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    number: str
    """
    observed values: 07123456789, 07777000000
    """
    locale: str
    """
    observed values: en-GB
    """
    is_mobile: Annotated[bool, Field(alias="isMobile")]


class Nationality(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    code: str
    """
    observed values: AT, AU, FI, GB, IE, IT, PL, RO
    """
    identifiers: list[Identifier] | None = None
    id: str | None = None
    """
    observed values: AU, FI, IE
    """
    name: str | None = None
    """
    observed values: Australia, Finland, Ireland
    """
    adjective: str | None = None
    """
    observed values: Australian, Finnish, Irish
    """
    in_eea: Annotated[bool | None, Field(alias="inEEA")] = None


class Source(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    method: str
    """
    observed values: Automatic, Manual
    """
    reference_id: Annotated[str | None, Field(alias="referenceId")] = None
    external_system: Annotated[str | None, Field(alias="externalSystem")] = None
    """
    observed values: Northrow
    """


class Stakeholder(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    role: str
    """
    observed values: Beneficiary, Director, Settlor, Trustee
    """
    client_id: Annotated[str, Field(alias="clientId")]
    """
    observed values: 03B2D6F, 03B2D6G, 03B2D70
    """
    client_and_account_management: Annotated[
        bool | None, Field(alias="clientAndAccountManagement")
    ] = None
    relationship_to_beneficiary: Annotated[str | None, Field(alias="relationshipToBeneficiary")] = (
        None
    )
    """
    observed values: Parent
    """
    source_of_wealth: Annotated[str | None, Field(alias="sourceOfWealth")] = None
    """
    observed values: Inheritance
    """


class VerifiedBy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    user_id: Annotated[str, Field(alias="userId")]
    """
    observed values: 029B3FF
    """
    user_firm_id: Annotated[str, Field(alias="userFirmId")]
    """
    observed values: TEST1
    """
    user_type: Annotated[str, Field(alias="userType")]
    """
    observed values: Machine, User
    """
    user_name: Annotated[str, Field(alias="userName")]
    """
    observed values: Machine User
    """


class Vulnerability(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    is_vulnerable: Annotated[bool, Field(alias="isVulnerable")]
    reason: list[str] | None = None
    is_support_temporary: Annotated[bool | None, Field(alias="isSupportTemporary")] = None


class EntityDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    company_reg_no: Annotated[str | None, Field(alias="companyRegNo")] = None
    """
    observed values: 07845123
    """
    stakeholders: list[Stakeholder]
    legal_entity_identifier: Annotated[str, Field(alias="legalEntityIdentifier")]
    """
    observed values: 529900T8BM49AURDDO55, 529900T8BM49AURSDO55
    """
    know_your_business: Annotated[KnowYourBusiness, Field(alias="knowYourBusiness")]
    declaration_accepted: Annotated[bool, Field(alias="declarationAccepted")]
    type: str | None = None
    """
    observed values: Charity, Discretionary
    """
    unique_trust_reference: Annotated[str | None, Field(alias="uniqueTrustReference")] = None
    """
    observed values: 1234567890
    """
    unique_charityreference: Annotated[str | None, Field(alias="uniqueCharityreference")] = None
    """
    observed values: 1234567890
    """


class VerificationInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    verified_on: Annotated[str, Field(alias="verifiedOn")]
    source: Source
    verified_by: Annotated[VerifiedBy | None, Field(alias="verifiedBy")] = None


class BankDetailsHistoryItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    created_by: Annotated[CreatedBy, Field(alias="createdBy")]
    created_on: Annotated[str, Field(alias="createdOn")]
    """
    observed values: 2024-04-04T12:25:14.112Z
    """
    verification_status: Annotated[str, Field(alias="verificationStatus")]
    """
    observed values: Active
    """
    verification_info: Annotated[VerificationInfo, Field(alias="verificationInfo")]
    sort_code: Annotated[str, Field(alias="sortCode")]
    """
    observed values: 309493
    """
    account_number: Annotated[str, Field(alias="accountNumber")]
    """
    observed values: 01273801
    """


class BankDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    sort_code: Annotated[str | None, Field(alias="sortCode")] = None
    """
    observed values: 000000, 123122, 123456, 209493, 222222, 304050, 309493, 993049
    """
    account_number: Annotated[str | None, Field(alias="accountNumber")] = None
    created_by: Annotated[CreatedBy | None, Field(alias="createdBy")] = None
    created_on: Annotated[str | None, Field(alias="createdOn")] = None
    verification_status: Annotated[str | None, Field(alias="verificationStatus")] = None
    """
    observed values: Active, Bypassed, Pending
    """
    verification_info: Annotated[VerificationInfo | None, Field(alias="verificationInfo")] = None
    payment_method: Annotated[str | None, Field(alias="paymentMethod")] = None
    """
    observed values: Bank Transfer
    """
    reference: str | None = None
    """
    observed values: Payment
    """
    debit_payment_mandate: Annotated[bool | None, Field(alias="debitPaymentMandate")] = None
    debit_payment_mandate_agreement_date: Annotated[
        str | None, Field(alias="debitPaymentMandateAgreementDate")
    ] = None


class FromData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    bank_details: Annotated[BankDetails, Field(alias="bankDetails")]


class UpdateData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_data: Annotated[FromData | None, Field(alias="fromData")] = None
    to_data: Annotated[FromData | None, Field(alias="toData")] = None
    status: str | None = None
    """
    observed values: Pending
    """
    title: str | None = None
    """
    observed values: Ms
    """
    email: str | None = None
    """
    observed values: wilmaflintstone@seccl.tech
    """
    first_name: Annotated[str | None, Field(alias="firstName")] = None
    """
    observed values: Wilma
    """
    surname: str | None = None
    """
    observed values: Flintstone
    """
    address_detail: Annotated[AddressDetail | None, Field(alias="addressDetail")] = None
    bank_details: Annotated[BankDetails | None, Field(alias="bankDetails")] = None
    requires_debit_mandate: Annotated[bool | None, Field(alias="requiresDebitMandate")] = None
    currency: str | None = None
    """
    observed values: GBP
    """
    aml_status: Annotated[str | None, Field(alias="amlStatus")] = None
    """
    observed values: Approved
    """
    terms_accepted: Annotated[bool | None, Field(alias="termsAccepted")] = None
    client_type: Annotated[str | None, Field(alias="clientType")] = None
    """
    observed values: Individual
    """
    date_of_birth: Annotated[AwareDatetime | None, Field(alias="dateOfBirth")] = None
    tax_domicile: Annotated[str | None, Field(alias="taxDomicile")] = None
    """
    observed values: GB
    """
    national_insurance_no: Annotated[str | None, Field(alias="nationalInsuranceNo")] = None
    """
    observed values: PC672672C
    """
    nationality: str | None = None
    """
    observed values: GB
    """
    nationalities: list[Nationality] | None = None
    date_of_death_confirmation: Annotated[date | None, Field(alias="dateOfDeathConfirmation")] = (
        None
    )
    date_of_death: Annotated[date | None, Field(alias="dateOfDeath")] = None
    debit_payment_mandate: Annotated[bool | None, Field(alias="debitPaymentMandate")] = None
    debit_payment_mandate_agreement_date: Annotated[
        AwareDatetime | None, Field(alias="debitPaymentMandateAgreementDate")
    ] = None


class UpdateHistory(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    update_id: Annotated[float, Field(alias="updateId")]
    update_action: Annotated[str, Field(alias="updateAction")]
    """
    observed values: AmendBank, VerifyBankDetails
    """
    update_reason: Annotated[str, Field(alias="updateReason")]
    update_date: Annotated[AwareDatetime, Field(alias="updateDate")]
    updated_fields: Annotated[list[str], Field(alias="updatedFields")]
    update_data: Annotated[UpdateData, Field(alias="updateData")]


class Client(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str | None = None
    firm_id: Annotated[str | None, Field(alias="firmId")] = None
    """
    observed values: FIRM001, SECCI, TEST1
    """
    node_id: Annotated[list[str] | None, Field(alias="nodeId")] = None
    client_type: Annotated[str | None, Field(alias="clientType")] = None
    """
    observed values: Individual
    """
    title: str | None = None
    """
    observed values: Dr, Mr, Mrs
    """
    first_name: Annotated[str | None, Field(alias="firstName")] = None
    """
    observed values: Fred, Maxie, Wilma
    """
    surname: str | None = None
    """
    observed values: Flinstone, Flintstone, Zieme
    """
    gender: str | None = None
    """
    observed values: Female, Male
    """
    currency: str | None = None
    """
    observed values: GBP
    """
    address_detail: Annotated[AddressDetail | None, Field(alias="addressDetail")] = None
    language: str | None = None
    """
    observed values: en
    """
    email: str | None = None
    mobile: Mobile | None = None
    nationality: Nationality | str | None = None
    national_insurance_no: Annotated[str | None, Field(alias="nationalInsuranceNo")] = None
    """
    observed values: AB266151C, AB325567C, AB767888C, AL790272C, PC989833C
    """
    date_of_birth: Annotated[AwareDatetime | None, Field(alias="dateOfBirth")] = None
    tax_domicile: Annotated[str | None, Field(alias="taxDomicile")] = None
    """
    observed values: GB
    """
    terms_accepted: Annotated[bool | None, Field(alias="termsAccepted")] = None
    bank_details: Annotated[BankDetails | None, Field(alias="bankDetails")] = None
    email_verified: Annotated[bool | None, Field(alias="emailVerified")] = None
    email_token: Annotated[str | None, Field(alias="emailToken")] = None
    email_token_expiry: Annotated[str | None, Field(alias="emailTokenExpiry")] = None
    mobile_verified: Annotated[bool | None, Field(alias="mobileVerified")] = None
    sms_token: Annotated[str | None, Field(alias="smsToken")] = None
    """
    observed values: 012782, 228397, 534464, 620273, 830512
    """
    sms_token_expiry: Annotated[str | None, Field(alias="smsTokenExpiry")] = None
    status: str | None = None
    """
    observed values: Active, Pending
    """
    node_name: Annotated[list[str] | None, Field(alias="nodeName")] = None
    sms_token_verified: Annotated[bool | None, Field(alias="smsTokenVerified")] = None
    comms_status: Annotated[CommsStatus | None, Field(alias="commsStatus")] = None
    marketing_consent: Annotated[bool | None, Field(alias="marketingConsent")] = None
    terms_accepted_date: Annotated[AwareDatetime | None, Field(alias="termsAcceptedDate")] = None
    aml_data: Annotated[AmlData | None, Field(alias="amlData")] = None
    field_surname: Annotated[str | None, Field(alias="_surname")] = None
    """
    observed values: FLINSTONE, FLINTSTONE, ZIEME
    """
    field_first_name: Annotated[str | None, Field(alias="_firstName")] = None
    """
    observed values: FRED, MAXIE, WILMA
    """
    audit_details: Annotated[AuditDetails | None, Field(alias="auditDetails")] = None
    update_id: Annotated[str | None, Field(alias="updateId")] = None
    external_ids: Annotated[list[ExternalId] | None, Field(alias="externalIds")] = None
    aml_checks: Annotated[list[AmlCheck] | None, Field(alias="amlChecks")] = None
    update_history: Annotated[list[UpdateHistory] | None, Field(alias="updateHistory")] = None
    nationalities: list[Nationality] | None = None
    employment_status: Annotated[str | None, Field(alias="employmentStatus")] = None
    """
    observed values: Employed
    """
    marital_status: Annotated[str | None, Field(alias="maritalStatus")] = None
    """
    observed values: Single
    """
    retirement_age: Annotated[float | None, Field(alias="retirementAge")] = None
    record_creation_date: Annotated[AwareDatetime | None, Field(alias="recordCreationDate")] = None
    bank_details_history: Annotated[
        list[BankDetailsHistoryItem] | None, Field(alias="bankDetailsHistory")
    ] = None
    fee_structures: Annotated[FeeStructures | None, Field(alias="feeStructures")] = None


class Employer(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    company_name: Annotated[str, Field(alias="companyName")]
    """
    observed values: Company Ltd
    """
    company_reg_no: Annotated[str, Field(alias="companyRegNo")]
    """
    observed values: 1234567
    """
    address_detail: Annotated[AddressDetail, Field(alias="addressDetail")]
    bank_details: Annotated[BankDetails, Field(alias="bankDetails")]


class ClientCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    firm_id: Annotated[str, Field(alias="firmId")]
    """
    observed values: SCUST, TEST1
    """
    node_id: Annotated[list[str], Field(alias="nodeId")]
    client_type: Annotated[str, Field(alias="clientType")]
    """
    observed values: Corporate, Individual, Individual Minor, Trust
    """
    title: str | None = None
    """
    observed values: Miss, Mr, Mrs
    """
    first_name: Annotated[str | None, Field(alias="firstName")] = None
    """
    observed values: Billy, Gerald, Pebbles, Wilma
    """
    surname: str | None = None
    """
    observed values: Flinstone, Flintstone, NoMates, Smith
    """
    gender: str | None = None
    """
    observed values: Female, Male
    """
    currency: str
    """
    observed values: GBP
    """
    address_detail: Annotated[AddressDetail | None, Field(alias="addressDetail")] = None
    language: str | None = None
    """
    observed values: en
    """
    email: str | None = None
    mobile: Mobile | None = None
    nationality: str | None = None
    """
    observed values: AT, GB
    """
    national_insurance_no: Annotated[str | None, Field(alias="nationalInsuranceNo")] = None
    """
    observed values: PC955833D
    """
    date_of_birth: Annotated[date | None, Field(alias="dateOfBirth")] = None
    tax_domicile: Annotated[str, Field(alias="taxDomicile")]
    """
    observed values: GB
    """
    aml_status: Annotated[str | None, Field(alias="amlStatus")] = None
    """
    observed values: Approved
    """
    terms_accepted: Annotated[bool | None, Field(alias="termsAccepted")] = None
    bank_details: Annotated[BankDetails | None, Field(alias="bankDetails")] = None
    nationalities: list[Nationality] | None = None
    vulnerability: Vulnerability | None = None
    email_verified: Annotated[bool | None, Field(alias="emailVerified")] = None
    kyc_data: Annotated[KycData | None, Field(alias="kycData")] = None
    entity_name: Annotated[str | None, Field(alias="entityName")] = None
    """
    observed values: Bedrock Corp, Bedrock Trust
    """
    entity_details: Annotated[EntityDetails | None, Field(alias="entityDetails")] = None
    employment_status: Annotated[str | None, Field(alias="employmentStatus")] = None
    """
    observed values: Employed
    """
    marital_status: Annotated[str | None, Field(alias="maritalStatus")] = None
    """
    observed values: Married
    """
    retirement_age: Annotated[float | None, Field(alias="retirementAge")] = None
    fee_structures: Annotated[FeeStructures | None, Field(alias="feeStructures")] = None
    status: str | None = None
    """
    observed values: Active
    """
    parent_id: Annotated[list[str] | None, Field(alias="parentId")] = None
    employers: list[Employer] | None = None
    requires_debit_mandate: Annotated[bool | None, Field(alias="requiresDebitMandate")] = None


class ClientUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str | None = None
    update_action: Annotated[str | None, Field(alias="updateAction")] = None
    update_reason: Annotated[str | None, Field(alias="updateReason")] = None
    update_data: Annotated[UpdateData | None, Field(alias="updateData")] = None
    update_id: Annotated[str | None, Field(alias="updateId")] = None
    """
    observed values: 6826028f5ba4d613a16d4a22
    """
    name: str | None = None
    """
    observed values: Steven
    """
    bank_details: Annotated[BankDetails | None, Field(alias="bankDetails")] = None
    email: str | None = None
    """
    observed values: steven1@example.com
    """
    kyc_data: Annotated[KycData | None, Field(alias="kycData")] = None
    vulnerability: Vulnerability | None = None
    client_groups: Annotated[ClientGroups | None, Field(alias="clientGroups")] = None
    fee_structures: Annotated[FeeStructures | None, Field(alias="feeStructures")] = None
    requires_debit_mandate: Annotated[bool | None, Field(alias="requiresDebitMandate")] = None
    cash_interest_opt_out: Annotated[bool | None, Field(alias="cashInterestOptOut")] = None
    clients: list[str] | None = None
    target_node: Annotated[str | None, Field(alias="targetNode")] = None
    """
    observed values: 50
    """
    source_node: Annotated[str | None, Field(alias="sourceNode")] = None
    """
    observed values: 2
    """
