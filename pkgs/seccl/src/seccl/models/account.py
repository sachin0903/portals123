"""Account models, generated from the vendored Postman collection.

Do not edit by hand: `moon run seccl:build-schemas && moon run seccl:generate-models`.
The types are inferred from 32 example payload(s), not from a published schema --
see pkgs/seccl/README.md for what that does and does not buy you.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class NominatedForAccounts(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    group: list[str] | None = None


class AssetTaxDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    tax_domicile: Annotated[str, Field(alias="taxDomicile")]
    """
    observed values: GB
    """
    tax_calculation: Annotated[str, Field(alias="taxCalculation")]
    """
    observed values: GROSS
    """
    report_category: Annotated[str, Field(alias="reportCategory")]
    """
    observed values: UK Dividend
    """


class AuditDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    update_date: Annotated[AwareDatetime, Field(alias="updateDate")]
    user_firm_id: Annotated[str, Field(alias="userFirmId")]
    """
    observed values: BACKGROUND, SCUST, SECCI, SECCL
    """
    user_id: Annotated[str, Field(alias="userId")]
    """
    observed values: 00031L, 019B3F3, 029B3F4, 029B3GJ, 029B844, Background, TESTUSER
    """
    version: int
    application: str
    """
    observed values: InstrumentProcessor, PfolioAPI, PfolioEventAPI, PortfolioProcessor
    """


class BankDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    sort_code: Annotated[str, Field(alias="sortCode")]
    """
    observed values: 123123, 654321
    """
    account_number: Annotated[str, Field(alias="accountNumber")]
    """
    observed values: 12321312, 87654321
    """


class CgtData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    realised_profit_loss: Annotated[float, Field(alias="realisedProfitLoss")]
    unrealised_profit_loss: Annotated[float, Field(alias="unrealisedProfitLoss")]


class Client(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    """
    observed values: 02693F2, 02693G1, 03BB94G, 03BBB8G, Client1, Client2
    """
    decision_maker: Annotated[bool | None, Field(alias="decisionMaker")] = None
    owner: float | None = None
    is_lead_client: Annotated[bool | None, Field(alias="isLeadClient")] = None


class ClosingPrice(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    asset_id: Annotated[str, Field(alias="assetId")]
    """
    observed values: 286D7, 29F55
    """
    price_date: Annotated[AwareDatetime, Field(alias="priceDate")]
    ask: float
    mid: float
    bid: float
    quote_unit: Annotated[float, Field(alias="quoteUnit")]
    minimum_transfer_unit: Annotated[float, Field(alias="minimumTransferUnit")]
    currency: str
    """
    observed values: GBP
    """
    exchange_rates: Annotated[list[Any], Field(alias="exchangeRates")]
    instrument_type: Annotated[str, Field(alias="instrumentType")]
    """
    observed values: Equity, Fund
    """
    ocf_calculated: Annotated[float | None, Field(alias="ocfCalculated")] = None
    id: str | None = None
    """
    observed values: 66b40af5f01864527b4774d2
    """
    insert_date: Annotated[AwareDatetime | None, Field(alias="insertDate")] = None
    audit_details: Annotated[AuditDetails | None, Field(alias="auditDetails")] = None
    firm_id: Annotated[str | None, Field(alias="firmId")] = None
    """
    observed values: SECCL
    """
    update_id: Annotated[str | None, Field(alias="updateId")] = None
    """
    observed values: 66b4707ee1bb04dbe09f2b7d
    """


class Detail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    asset_id: Annotated[str, Field(alias="assetId")]
    """
    observed values: 26MPD, 27426, 284JP
    """
    percentage: float
    asset_name: Annotated[str | None, Field(alias="assetName")] = None
    currency: str | None = None
    """
    observed values: GBP
    """


class FeeStructures(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    adviser_fee_id: Annotated[str, Field(alias="adviserFeeId")]
    """
    observed values: 64f1a2b3c4d5e6f7a8b9c0d1
    """


class Nomination(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    title: str
    """
    observed values:
    """
    first_name: Annotated[str, Field(alias="firstName")]
    """
    observed values: Bamm-Bamm, Pebble
    """
    surname: str
    """
    observed values: Rubble
    """
    beneficiary_relationship: Annotated[str, Field(alias="beneficiaryRelationship")]
    """
    observed values: DAUGHTER
    """
    beneficiary_proportion: Annotated[float, Field(alias="beneficiaryProportion")]


class PortfolioGroup(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    group_intent: Annotated[str, Field(alias="groupIntent")]
    """
    observed values: Pension Account Group
    """
    group_type: Annotated[str, Field(alias="groupType")]
    """
    observed values: Crystallised, CrystallisedTransfer
    """
    name: str | None = None
    """
    observed values: Old Pension Provider Account
    """


class Price(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    asset_id: Annotated[str, Field(alias="assetId")]
    """
    observed values: 2848P, 286D7, 29F55
    """
    price_date: Annotated[AwareDatetime, Field(alias="priceDate")]
    ask: float
    mid: float
    bid: float
    quote_unit: Annotated[float, Field(alias="quoteUnit")]
    minimum_transfer_unit: Annotated[float, Field(alias="minimumTransferUnit")]
    currency: str
    """
    observed values: GBP
    """
    exchange_rates: Annotated[list[Any], Field(alias="exchangeRates")]
    instrument_type: Annotated[str, Field(alias="instrumentType")]
    """
    observed values: Equity, Fund
    """
    ocf_calculated: Annotated[float | None, Field(alias="ocfCalculated")] = None
    insert_date: Annotated[AwareDatetime | None, Field(alias="insertDate")] = None
    id: str | None = None
    """
    observed values: 66b40af5f01864527b4774d2
    """
    audit_details: Annotated[AuditDetails | None, Field(alias="auditDetails")] = None
    firm_id: Annotated[str | None, Field(alias="firmId")] = None
    """
    observed values: SECCL
    """
    update_id: Annotated[str | None, Field(alias="updateId")] = None
    """
    observed values: 66b4707ee1bb04dbe09f2b7d
    """


class ProductDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    flexi_access_date: Annotated[AwareDatetime, Field(alias="flexiAccessDate")]
    flexible_benefits_accessed: Annotated[bool, Field(alias="flexibleBenefitsAccessed")]
    pension_opt_out: Annotated[bool, Field(alias="pensionOptOut")]


class RecurringOrders(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    movement_type: Annotated[str, Field(alias="movementType")]
    """
    observed values: Invest
    """
    investment_type: Annotated[str, Field(alias="investmentType")]
    """
    observed values: Bespoke
    """
    details: list[Detail]
    order_day: Annotated[float, Field(alias="orderDay")]


class RecurringPayment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    payment_day: Annotated[float, Field(alias="paymentDay")]
    amount: float
    movement_type: Annotated[str | None, Field(alias="movementType")] = None
    """
    observed values: In, Out
    """
    method: str | None = None
    """
    observed values: BACS Credit, Bank Transfer, Direct Debit
    """
    payment_type: Annotated[str | None, Field(alias="paymentType")] = None
    """
    observed values: Employee
    """
    auto_invest: Annotated[bool | None, Field(alias="autoInvest")] = None


class Tier(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_amount: Annotated[float, Field(alias="fromAmount")]
    to_amount: Annotated[float, Field(alias="toAmount")]
    amount: float
    rate: float


class ExternalId(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id_type: Annotated[str, Field(alias="idType")]
    """
    observed values: ExternalIdType
    """
    value: str
    """
    observed values: 01ABC
    """


class WrapperDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    wrapper_type: Annotated[str, Field(alias="wrapperType")]
    """
    observed values: GIA, ISA, JISA, PENSION
    """
    discretionary: bool | None = None
    advised: bool | None = None
    trust: bool | None = None
    client_product_id: Annotated[str | None, Field(alias="clientProductId")] = None
    declaration_accepted_date: Annotated[str | None, Field(alias="declarationAcceptedDate")] = None
    """
    observed values: 2025-01-01, 2025-01-01T08:21:30.521Z
    """
    scheme_product_id: Annotated[str | None, Field(alias="schemeProductId")] = None
    """
    observed values: 004P4
    """
    product_detail: Annotated[ProductDetail | None, Field(alias="productDetail")] = None
    asset_allocation_id: Annotated[str | None, Field(alias="assetAllocationId")] = None
    """
    observed values: 04DGH, 251GF, 264GJ
    """
    asset_allocation_name: Annotated[str | None, Field(alias="assetAllocationName")] = None
    """
    observed values: 8-FEB, High-growth model
    """
    accepted_provider_terms: Annotated[bool | None, Field(alias="acceptedProviderTerms")] = None
    accepted_provider_terms_date: Annotated[
        date | None, Field(alias="acceptedProviderTermsDate")
    ] = None
    nominations: list[Nomination] | None = None
    external_id: Annotated[str | None, Field(alias="externalId")] = None
    """
    observed values: ABC123456
    """
    external_ids: Annotated[list[ExternalId] | None, Field(alias="externalIds")] = None
    decision_maker_id: Annotated[str | None, Field(alias="decisionMakerId")] = None


class AdviserTieredCharges(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_date: Annotated[AwareDatetime, Field(alias="fromDate")]
    to_date: Annotated[AwareDatetime, Field(alias="toDate")]
    minimum_amount: Annotated[float, Field(alias="minimumAmount")]
    maximum_amount: Annotated[float, Field(alias="maximumAmount")]
    instrument_types: Annotated[list[str], Field(alias="instrumentTypes")]
    tiers: list[Tier]


class FromData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    wrapper_detail: Annotated[WrapperDetail, Field(alias="wrapperDetail")]


class Position(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    account_id: Annotated[str, Field(alias="accountId")]
    """
    observed values: 0000619, 031B7H5, A05WQ3V, A05X9TD
    """
    account_name: Annotated[str, Field(alias="accountName")]
    account_type: Annotated[str, Field(alias="accountType")]
    """
    observed values: Wrapper
    """
    amount: float | None = None
    audit_details: Annotated[AuditDetails, Field(alias="auditDetails")]
    currency: str
    """
    observed values: GBP
    """
    firm_id: Annotated[str, Field(alias="firmId")]
    """
    observed values: SECCI
    """
    node_id: Annotated[str, Field(alias="nodeId")]
    """
    observed values: 0
    """
    position_type: Annotated[str, Field(alias="positionType")]
    """
    observed values: Cash, Stock
    """
    update_id: Annotated[str, Field(alias="updateId")]
    current_value: Annotated[float, Field(alias="currentValue")]
    asset_id: Annotated[str | None, Field(alias="assetId")] = None
    """
    observed values: 2848P, 286D7, 29F55
    """
    asset_name: Annotated[str | None, Field(alias="assetName")] = None
    isin: str | None = None
    """
    observed values: GB0003101523, GB00B2PDGW16, GB00BZ82ZW98
    """
    quantity: float | None = None
    prices: list[Price] | None = None
    closing_price: Annotated[ClosingPrice | None, Field(alias="closingPrice")] = None
    current_price: Annotated[float | None, Field(alias="currentPrice")] = None
    current_sell_price: Annotated[float | None, Field(alias="currentSellPrice")] = None
    instrument_type: Annotated[str | None, Field(alias="instrumentType")] = None
    """
    observed values: Equity, Fund
    """
    minimum_transfer_unit: Annotated[float | None, Field(alias="minimumTransferUnit")] = None
    current_price_date: Annotated[AwareDatetime | None, Field(alias="currentPriceDate")] = None
    asset_instrument_type: Annotated[str | None, Field(alias="assetInstrumentType")] = None
    """
    observed values: Equity, Fund
    """
    asset_country_of_issue: Annotated[str | None, Field(alias="assetCountryOfIssue")] = None
    """
    observed values: GB
    """
    asset_tax_details: Annotated[list[AssetTaxDetail] | None, Field(alias="assetTaxDetails")] = None
    growth: float | None = None
    growth_percent: Annotated[float | None, Field(alias="growthPercent")] = None
    adjusted_growth: Annotated[float | None, Field(alias="adjustedGrowth")] = None
    adjusted_growth_percent: Annotated[float | None, Field(alias="adjustedGrowthPercent")] = None
    opening_value: Annotated[float | None, Field(alias="openingValue")] = None
    allocation: float | None = None
    disclaimers: list[Any] | None = None
    book_value: Annotated[float | None, Field(alias="bookValue")] = None
    transfer_book_value: Annotated[float | None, Field(alias="transferBookValue")] = None
    non_transfer_book_value: Annotated[float | None, Field(alias="nonTransferBookValue")] = None
    transfer_value: Annotated[float | None, Field(alias="transferValue")] = None
    cgt_data: Annotated[CgtData | None, Field(alias="cgtData")] = None


class ToData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    wrapper_detail: Annotated[WrapperDetail | None, Field(alias="wrapperDetail")] = None
    recurring_orders: Annotated[RecurringOrders | None, Field(alias="recurringOrders")] = None
    recurring_payments: Annotated[
        list[RecurringPayment] | None, Field(alias="recurringPayments")
    ] = None
    auto_disinvest: Annotated[str | None, Field(alias="autoDisinvest")] = None
    """
    observed values: Largest, Proportional
    """


class UpdateData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_data: Annotated[FromData | None, Field(alias="fromData")] = None
    to_data: Annotated[ToData | None, Field(alias="toData")] = None
    bank_details: Annotated[BankDetails | None, Field(alias="bankDetails")] = None
    status: str | None = None
    """
    observed values: Suspended
    """


class UpdateHistory(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    update_id: Annotated[float, Field(alias="updateId")]
    update_date: Annotated[AwareDatetime, Field(alias="updateDate")]
    update_data: Annotated[UpdateData, Field(alias="updateData")]
    updated_fields: Annotated[list[str], Field(alias="updatedFields")]


class Account(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str | None = None
    firm_id: Annotated[str | None, Field(alias="firmId")] = None
    """
    observed values: FIRM001, SDEMO, SECCI
    """
    client_id: Annotated[str | None, Field(alias="clientId")] = None
    wrapper_type: Annotated[str | None, Field(alias="wrapperType")] = None
    """
    observed values: GIA, ISA
    """
    fee_structures: Annotated[FeeStructures | None, Field(alias="feeStructures")] = None
    update_id: Annotated[str | None, Field(alias="updateId")] = None
    node_id: Annotated[str | None, Field(alias="nodeId")] = None
    """
    observed values: 0, 1
    """
    name: str | None = None
    currency: str | None = None
    """
    observed values: GBP
    """
    account_type: Annotated[str | None, Field(alias="accountType")] = None
    """
    observed values: Wrapper
    """
    status: str | None = None
    """
    observed values: Active, Pending
    """
    wrapper_detail: Annotated[WrapperDetail | None, Field(alias="wrapperDetail")] = None
    adviser_charge: Annotated[float | None, Field(alias="adviserCharge")] = None
    client_name: Annotated[str | None, Field(alias="clientName")] = None
    node_name: Annotated[str | None, Field(alias="nodeName")] = None
    """
    observed values: Firm Name, Seccl Demo, Seccl Invest
    """
    field_name: Annotated[str | None, Field(alias="_name")] = None
    audit_details: Annotated[AuditDetails | None, Field(alias="auditDetails")] = None
    client_national_insurance_no: Annotated[
        str | None, Field(alias="clientNationalInsuranceNo")
    ] = None
    """
    observed values: AB576470C, AB813641C, PC955833D
    """
    clients: list[Client] | None = None
    update_history: Annotated[list[UpdateHistory] | None, Field(alias="updateHistory")] = None
    recurring_orders: Annotated[RecurringOrders | None, Field(alias="recurringOrders")] = None
    recurring_payment: Annotated[bool | None, Field(alias="recurringPayment")] = None
    client_names: Annotated[list[str] | None, Field(alias="clientNames")] = None
    positions: list[Position] | None = None
    complete_transactions: Annotated[list[Any] | None, Field(alias="completeTransactions")] = None
    current_value: Annotated[float | None, Field(alias="currentValue")] = None
    uninvested_cash: Annotated[float | None, Field(alias="uninvestedCash")] = None
    opening_value: Annotated[float | None, Field(alias="openingValue")] = None
    opening_stock_value: Annotated[float | None, Field(alias="openingStockValue")] = None
    opening_cash_value: Annotated[float | None, Field(alias="openingCashValue")] = None
    book_value: Annotated[float | None, Field(alias="bookValue")] = None
    transfer_book_value: Annotated[float | None, Field(alias="transferBookValue")] = None
    non_transfer_book_value: Annotated[float | None, Field(alias="nonTransferBookValue")] = None
    growth: float | None = None
    adjusted_growth: Annotated[float | None, Field(alias="adjustedGrowth")] = None
    closing_cash_value: Annotated[float | None, Field(alias="closingCashValue")] = None
    closing_stock_value: Annotated[float | None, Field(alias="closingStockValue")] = None
    growth_percent: Annotated[float | None, Field(alias="growthPercent")] = None
    adjusted_growth_percent: Annotated[float | None, Field(alias="adjustedGrowthPercent")] = None
    transfer_value: Annotated[float | None, Field(alias="transferValue")] = None
    cgt_data: Annotated[CgtData | None, Field(alias="cgtData")] = None
    disclaimers: list[Any] | None = None
    recurring_payments: Annotated[
        list[RecurringPayment] | None, Field(alias="recurringPayments")
    ] = None
    auto_disinvest: Annotated[str | None, Field(alias="autoDisinvest")] = None
    """
    observed values: Largest, Proportional
    """


class AccountCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    firm_id: Annotated[str, Field(alias="firmId")]
    """
    observed values: SECCI
    """
    node_id: Annotated[str | None, Field(alias="nodeId")] = None
    """
    observed values: 0, 1, 5
    """
    account_type: Annotated[str | None, Field(alias="accountType")] = None
    """
    observed values: Wrapper
    """
    name: str | None = None
    status: str | None = None
    """
    observed values: Active, Pending
    """
    currency: str | None = None
    """
    observed values: GBP
    """
    client_id: Annotated[str | None, Field(alias="clientId")] = None
    wrapper_detail: Annotated[WrapperDetail | None, Field(alias="wrapperDetail")] = None
    recurring_orders: Annotated[RecurringOrders | None, Field(alias="recurringOrders")] = None
    recurring_payments: Annotated[
        list[RecurringPayment] | None, Field(alias="recurringPayments")
    ] = None
    discretionary_management_charge: Annotated[
        float | None, Field(alias="discretionaryManagementCharge")
    ] = None
    adviser_tiered_charges: Annotated[
        AdviserTieredCharges | None, Field(alias="adviserTieredCharges")
    ] = None
    adviser_charge: Annotated[float | None, Field(alias="adviserCharge")] = None
    clients: list[Client] | None = None
    product_detail: Annotated[ProductDetail | None, Field(alias="productDetail")] = None
    portfolio_groups: Annotated[list[PortfolioGroup] | None, Field(alias="portfolioGroups")] = None
    decision_maker_lei: Annotated[str | None, Field(alias="decisionMakerLei")] = None
    bank_details: Annotated[BankDetails | None, Field(alias="bankDetails")] = None
    advisor_charge: Annotated[float | None, Field(alias="advisorCharge")] = None
    account_id: Annotated[str | None, Field(alias="accountId")] = None
    """
    observed values: 0355D6J
    """
    payment_methods: Annotated[list[str] | None, Field(alias="paymentMethods")] = None
    auto_disinvest: Annotated[str | None, Field(alias="autoDisinvest")] = None
    """
    observed values: Largest
    """


class AccountUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    update_id: Annotated[str | None, Field(alias="updateId")] = None
    nominated_for_accounts: Annotated[
        NominatedForAccounts | None, Field(alias="nominatedForAccounts")
    ] = None
    update_action: Annotated[str | None, Field(alias="updateAction")] = None
    """
    observed values: AmendStatus, VerifyBankDetails
    """
    update_reason: Annotated[str | None, Field(alias="updateReason")] = None
    update_data: Annotated[UpdateData | None, Field(alias="updateData")] = None
    fee_structures: Annotated[FeeStructures | None, Field(alias="feeStructures")] = None
    recurring_payments: Annotated[
        list[RecurringPayment] | None, Field(alias="recurringPayments")
    ] = None
    name: str | None = None
    """
    observed values: New account name
    """
    recurring_payment: Annotated[RecurringPayment | None, Field(alias="recurringPayment")] = None
    recurring_orders: Annotated[RecurringOrders | None, Field(alias="recurringOrders")] = None
    auto_disinvest: Annotated[str | None, Field(alias="autoDisinvest")] = None
    """
    observed values: Largest
    """
