"""ClientProduct models, generated from the vendored Postman collection.

Do not edit by hand: `moon run seccl:build-schemas && moon run seccl:generate-models`.
The types are inferred from 12 example payload(s), not from a published schema --
see pkgs/seccl/README.md for what that does and does not buy you.
"""

from __future__ import annotations

from datetime import date as date_aliased
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Declaration(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    client_id: Annotated[str, Field(alias="clientId")]
    """
    observed values: 03BG88J
    """
    accepted_date: Annotated[date_aliased, Field(alias="acceptedDate")]


class AccountDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    """
    observed values: Drawdown Account
    """


class AuditDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    application: str
    update_date: Annotated[AwareDatetime, Field(alias="updateDate")]
    user_firm_id: Annotated[str, Field(alias="userFirmId")]
    """
    observed values: BACKGROUND, SCUST, SECCI
    """
    user_id: Annotated[str, Field(alias="userId")]
    """
    observed values: 029B3F4, 029B3GJ, Background
    """
    version: int


class BenefitCrystallisationEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    date: AwareDatetime
    amount: float
    type: str
    """
    observed values: BCE1, BCE6
    """
    lifetime_allowance_used: Annotated[float, Field(alias="lifetimeAllowanceUsed")]
    account_id: Annotated[str | None, Field(alias="accountId")] = None
    """
    observed values: 027JDDC, 027JDDD
    """
    account_group_id: Annotated[str | None, Field(alias="accountGroupId")] = None
    """
    observed values: 02G2H, 02G2J
    """


class CrystallisationDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: str
    """
    observed values: FAD, UFPLS
    """
    frequency: str
    """
    observed values: Regular, Single
    """
    full_crystallisation: Annotated[bool | None, Field(alias="fullCrystallisation")] = None
    amount: float
    crystallisation_day: Annotated[float | None, Field(alias="crystallisationDay")] = None
    expiry_date: Annotated[date_aliased | None, Field(alias="expiryDate")] = None


class ExpressionOfWishNomination(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    title: str
    """
    observed values: Dr, Miss, Mr
    """
    first_name: Annotated[str, Field(alias="firstName")]
    """
    observed values: Added, James, Test
    """
    surname: str
    """
    observed values: Jon, Nominee, Nominees
    """
    beneficiary_relationship: Annotated[str, Field(alias="beneficiaryRelationship")]
    """
    observed values: DAUGHTER, HUSBAND, WIFE
    """
    beneficiary_proportion: Annotated[float, Field(alias="beneficiaryProportion")]


class ProtectionItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    protection_type: Annotated[str, Field(alias="protectionType")]
    """
    observed values: Primary
    """
    certificate_reference_number: Annotated[str, Field(alias="certificateReferenceNumber")]
    """
    observed values: 12458
    """
    scheme_admin_reference_number: Annotated[str, Field(alias="schemeAdminReferenceNumber")]
    """
    observed values: 23598
    """


class ProductDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    flexi_access_date: Annotated[AwareDatetime | None, Field(alias="flexiAccessDate")] = None
    flexible_benefits_accessed: Annotated[bool | None, Field(alias="flexibleBenefitsAccessed")] = (
        None
    )
    pension_opt_out: Annotated[bool | None, Field(alias="pensionOptOut")] = None
    transitional_tax_free_amount: Annotated[
        float | None, Field(alias="transitionalTaxFreeAmount")
    ] = None
    transitional_tax_free_amount_checked: Annotated[
        bool | None, Field(alias="transitionalTaxFreeAmountChecked")
    ] = None
    lump_sum_allowance_used_elsewhere: Annotated[
        float | None, Field(alias="lumpSumAllowanceUsedElsewhere")
    ] = None
    lump_sum_allowance_used_pre_april2024: Annotated[
        float | None, Field(alias="lumpSumAllowanceUsedPreApril2024")
    ] = None
    protection: list[ProtectionItem] | None = None
    update_id: Annotated[str | None, Field(alias="updateId")] = None
    lifetime_allowance_used_elsewhere: Annotated[
        float | None, Field(alias="lifetimeAllowanceUsedElsewhere")
    ] = None


class WithdrawalDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    transaction_type: Annotated[str, Field(alias="transactionType")]
    """
    observed values: Payment
    """
    transaction_sub_type: Annotated[str, Field(alias="transactionSubType")]
    """
    observed values: FAD, PCLS
    """
    amount: float | None = None
    frequency: str
    """
    observed values: Single
    """
    method: str
    """
    observed values: BACS Credit
    """
    movement_type: Annotated[str, Field(alias="movementType")]
    """
    observed values: Out
    """
    gross_amount: Annotated[float | None, Field(alias="grossAmount")] = None


class ClientProduct(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str | None = None
    client_ids: Annotated[list[str] | None, Field(alias="clientIds")] = None
    firm_id: Annotated[str | None, Field(alias="firmId")] = None
    """
    observed values: SDEMO, SECCI
    """
    node_id: Annotated[list[str] | str | None, Field(alias="nodeId")] = None
    wrapper_type: Annotated[str | None, Field(alias="wrapperType")] = None
    """
    observed values: ISA, PENSION
    """
    audit_details: Annotated[AuditDetails | None, Field(alias="auditDetails")] = None
    update_id: Annotated[str | None, Field(alias="updateId")] = None
    scheme_product_id: Annotated[str | None, Field(alias="schemeProductId")] = None
    """
    observed values: 004P4
    """
    expression_of_wish_nominations: Annotated[
        list[ExpressionOfWishNomination] | None, Field(alias="expressionOfWishNominations")
    ] = None
    product_detail: Annotated[ProductDetail | None, Field(alias="productDetail")] = None
    client_product_action_type: Annotated[str | None, Field(alias="clientProductActionType")] = None
    account_details: Annotated[AccountDetails | None, Field(alias="accountDetails")] = None
    account_id: Annotated[str | None, Field(alias="accountId")] = None
    """
    observed values: 027F323, 027JDDC
    """
    advised: bool | None = None
    fca_individual_reference: Annotated[str | None, Field(alias="fcaIndividualReference")] = None
    """
    observed values: ABC123
    """
    benefits_taken_before2006: Annotated[bool | None, Field(alias="benefitsTakenBefore2006")] = None
    protected_retirement_age_or_lump_sum: Annotated[
        bool | None, Field(alias="protectedRetirementAgeOrLumpSum")
    ] = None
    crystallisation_details: Annotated[
        CrystallisationDetails | None, Field(alias="crystallisationDetails")
    ] = None
    guidance: bool | None = None
    withdrawal_details: Annotated[
        list[WithdrawalDetail] | None, Field(alias="withdrawalDetails")
    ] = None
    client_product_id: Annotated[str | None, Field(alias="clientProductId")] = None
    """
    observed values: 6436cdca33a066262eae7764, 6523a3aa9c9363eaab8ebcc5
    """
    status: str | None = None
    """
    observed values: Completed
    """
    benefit_crystallisation_events: Annotated[
        list[BenefitCrystallisationEvent] | None, Field(alias="benefitCrystallisationEvents")
    ] = None
    process_action: Annotated[str | None, Field(alias="processAction")] = None
    """
    observed values: Determine Account, Generate Statement
    """


class ClientProductCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    firm_id: Annotated[str, Field(alias="firmId")]
    account_details: Annotated[AccountDetails | None, Field(alias="accountDetails")] = None
    account_id: Annotated[str, Field(alias="accountId")]
    advised: bool
    guidance: bool
    fca_individual_reference: Annotated[str, Field(alias="fcaIndividualReference")]
    """
    observed values: ABC123
    """
    benefits_taken_before2006: Annotated[bool, Field(alias="benefitsTakenBefore2006")]
    protected_retirement_age_or_lump_sum: Annotated[
        bool, Field(alias="protectedRetirementAgeOrLumpSum")
    ]
    crystallisation_details: Annotated[
        CrystallisationDetails, Field(alias="crystallisationDetails")
    ]
    withdrawal_details: Annotated[
        list[WithdrawalDetail] | None, Field(alias="withdrawalDetails")
    ] = None


class ClientProductUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    update_id: Annotated[str | None, Field(alias="updateId")] = None
    declarations: list[Declaration] | None = None
    declaration_accepted_date: Annotated[
        AwareDatetime | None, Field(alias="declarationAcceptedDate")
    ] = None
    accepted_provider_terms_date: Annotated[
        str | None, Field(alias="acceptedProviderTermsDate")
    ] = None
    accepted_provider_terms: Annotated[bool | None, Field(alias="acceptedProviderTerms")] = None
    expression_of_wish_nominations: Annotated[
        list[ExpressionOfWishNomination] | None, Field(alias="expressionOfWishNominations")
    ] = None
    product_detail: Annotated[ProductDetail | None, Field(alias="productDetail")] = None
    status: str | None = None
    """
    observed values: Cancelled
    """
    update_reason: Annotated[str | None, Field(alias="updateReason")] = None
    product_status: Annotated[str | None, Field(alias="productStatus")] = None
    """
    observed values: drawdownPending
    """
