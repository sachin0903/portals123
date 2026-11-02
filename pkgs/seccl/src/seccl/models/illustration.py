"""Illustration models, generated from the vendored Postman collection.

Do not edit by hand: `moon run seccl:build-schemas && moon run seccl:generate-models`.
The types are inferred from 8 example payload(s), not from a published schema --
see pkgs/seccl/README.md for what that does and does not buy you.
"""

from __future__ import annotations

from datetime import date as date_aliased
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class AdditionalClientsInfoItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    title: str
    """
    observed values: Mrs
    """
    first_name: Annotated[str, Field(alias="firstName")]
    """
    observed values: Joanna
    """
    surname: str
    """
    observed values: Flintstone
    """
    email: str
    """
    observed values: joannaflintstone@example.com
    """
    date_of_birth: Annotated[AwareDatetime, Field(alias="dateOfBirth")]
    client_type: Annotated[str, Field(alias="clientType")]
    """
    observed values: Individual
    """
    language: str
    """
    observed values: en-GB
    """


class Drawdown(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    funds_not_in_drawdown: Annotated[float, Field(alias="fundsNotInDrawdown")]
    drawdown_method: Annotated[str, Field(alias="drawdownMethod")]
    """
    observed values: FAD
    """


class AdviserInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    adviser_name: Annotated[str, Field(alias="adviserName")]
    """
    observed values: Adviser A, Bob Brian, Jane Adviser, Tom Jones
    """
    fca_number: Annotated[str, Field(alias="FCANumber")]
    """
    observed values: 123123, 12345, 290920, 787291
    """
    company: str


class AssetCharge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    asset_id: Annotated[str, Field(alias="assetId")]
    """
    observed values: 286D7, 286D8, 286D9
    """
    asset_name: Annotated[str, Field(alias="assetName")]
    """
    observed values: WH Smith PLC
    """
    charges: list[Any]
    total_charges: Annotated[float, Field(alias="totalCharges")]


class Charges(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    trading_charge: Annotated[bool, Field(alias="tradingCharge")]
    platform_charge: Annotated[bool, Field(alias="platformCharge")]
    product_charge: Annotated[bool, Field(alias="productCharge")]


class ClientInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    title: str | None = None
    """
    observed values: Mr, Mrs
    """
    first_name: Annotated[str | None, Field(alias="firstName")] = None
    """
    observed values: Barney, Colin, Jake, Wilma
    """
    surname: str | None = None
    """
    observed values: Flintstone, Potato, Rubble
    """
    email: str | None = None
    date_of_birth: Annotated[str | None, Field(alias="dateOfBirth")] = None
    client_type: Annotated[str | None, Field(alias="clientType")] = None
    """
    observed values: Client, Individual
    """
    language: str | None = None
    """
    observed values: en-GB
    """
    middle_name: Annotated[str | None, Field(alias="middleName")] = None
    """
    observed values:
    """
    id: str | None = None


class Dealing(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    gbp: Annotated[float, Field(alias="GBP")]


class InitialAdviserCharge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    amount: float | None = None
    percent: float | None = None


class InitialDiscretionaryManagementCharge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    amount: float | None = None


class OngoingCharges(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    portfolio_charges: Annotated[list[Any], Field(alias="portfolioCharges")]
    asset_charges: Annotated[list[AssetCharge], Field(alias="assetCharges")]


class RegularPayment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    frequency: str
    """
    observed values: Monthly
    """
    from_date: Annotated[date_aliased, Field(alias="fromDate")]
    to_date: Annotated[date_aliased, Field(alias="toDate")]
    payment_amount: Annotated[float, Field(alias="paymentAmount")]
    percentage_growth: Annotated[float, Field(alias="percentageGrowth")]
    movement_type: Annotated[str, Field(alias="movementType")]
    """
    observed values: In
    """
    contributor: str | None = None
    """
    observed values: Employee, Employer, Personal
    """


class RegularWithdrawal(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    date: date_aliased
    end_date: Annotated[date_aliased | None, Field(alias="endDate")] = None
    amount: float
    percentage_growth: Annotated[float, Field(alias="percentageGrowth")]
    frequency: str
    """
    observed values: Monthly
    """
    type: str | None = None
    """
    observed values: FAD
    """


class SinglePayment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    payment_date: Annotated[date_aliased, Field(alias="paymentDate")]
    payment_amount: Annotated[float, Field(alias="paymentAmount")]
    contributor: str
    """
    observed values: Employee, Employer, Transfer
    """
    movement_type: Annotated[str, Field(alias="movementType")]
    """
    observed values: In
    """


class SingleWithdrawal(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    date: date_aliased
    amount: float
    type: str | None = None
    """
    observed values: PCLS
    """


class Transaction(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    asset_id: Annotated[str, Field(alias="assetId")]
    amount: float | None = None
    currency: str
    """
    observed values: GBP
    """
    percentage: float | None = None


class TransactionCharges(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    stamp: Annotated[Dealing, Field(alias="Stamp")]
    dealing: Annotated[Dealing, Field(alias="Dealing")]
    ptm: Annotated[Dealing, Field(alias="PTM")]
    total: Dealing


class ValuationData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    account_id: Annotated[str, Field(alias="accountId")]
    start_valuation: Annotated[float, Field(alias="startValuation")]


class Withdrawals(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    single_withdrawals: Annotated[list[SingleWithdrawal], Field(alias="singleWithdrawals")]
    regular_withdrawals: Annotated[list[RegularWithdrawal], Field(alias="regularWithdrawals")]


class CalculationsAfterEachPeriod(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    period_index: Annotated[int, Field(alias="periodIndex")]
    currency: str
    """
    observed values: GBP
    """
    opening_amount: Annotated[float, Field(alias="openingAmount")]
    growth_amount: Annotated[float, Field(alias="growthAmount")]
    ongoing_charges: Annotated[OngoingCharges, Field(alias="ongoingCharges")]
    total_charges: Annotated[float, Field(alias="totalCharges")]
    closing_amount: Annotated[float, Field(alias="closingAmount")]
    transaction_charges: Annotated[TransactionCharges, Field(alias="transactionCharges")]
    transactions: list[Transaction] | None = None


class Fees(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    enable_tiered_charging: Annotated[bool | None, Field(alias="enableTieredCharging")] = None
    initial_adviser_charge: Annotated[InitialAdviserCharge, Field(alias="initialAdviserCharge")]
    initial_discretionary_management_charge: Annotated[
        InitialDiscretionaryManagementCharge, Field(alias="initialDiscretionaryManagementCharge")
    ]
    adviser_charge: Annotated[float | None, Field(alias="adviserCharge")] = None
    discretionary_management_charge: Annotated[
        float | None, Field(alias="discretionaryManagementCharge")
    ] = None
    adviser_fixed_charge: Annotated[float | None, Field(alias="adviserFixedCharge")] = None
    apply_adviser_charge_across_portfolio: Annotated[
        bool | None, Field(alias="applyAdviserChargeAcrossPortfolio")
    ] = None
    discretionary_management_fixed_charge: Annotated[
        float | None, Field(alias="discretionaryManagementFixedCharge")
    ] = None


class Payments(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    initial_payment: Annotated[float, Field(alias="initialPayment")]
    single_payments: Annotated[list[SinglePayment], Field(alias="singlePayments")]
    regular_payments: Annotated[list[RegularPayment], Field(alias="regularPayments")]


class Illustration(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str | None = None
    client_id: Annotated[str | None, Field(alias="clientId")] = None
    """
    observed values: 0268HC7, 026933B, 026C3JF
    """
    firm_id: Annotated[str, Field(alias="firmId")]
    """
    observed values: SDEMO, SECCI
    """
    status: str | None = None
    """
    observed values: Calculating
    """
    account_id: Annotated[str | None, Field(alias="accountId")] = None
    """
    observed values: 00GBDC9
    """
    account_name: Annotated[str | None, Field(alias="accountName")] = None
    """
    observed values: 1
    """
    currency: str | None = None
    """
    observed values: GBP
    """
    initial_charges: Annotated[list[AssetCharge] | None, Field(alias="initialCharges")] = None
    ongoing_charges: Annotated[OngoingCharges | None, Field(alias="ongoingCharges")] = None
    calculations_after_each_period: Annotated[
        list[CalculationsAfterEachPeriod] | None, Field(alias="calculationsAfterEachPeriod")
    ] = None
    investment_amount: Annotated[float | None, Field(alias="investmentAmount")] = None
    final_amount: Annotated[float | None, Field(alias="finalAmount")] = None
    total_initial_charges: Annotated[float | None, Field(alias="totalInitialCharges")] = None
    total_ongoing_charges: Annotated[float | None, Field(alias="totalOngoingCharges")] = None
    total_transaction_charges: Annotated[float | None, Field(alias="totalTransactionCharges")] = (
        None
    )
    total_charges: Annotated[float | None, Field(alias="totalCharges")] = None
    total_charges_percent: Annotated[float | None, Field(alias="totalChargesPercent")] = None
    total_growth: Annotated[float | None, Field(alias="totalGrowth")] = None
    additional_client_ids: Annotated[list[str] | None, Field(alias="additionalClientIds")] = None


class IllustrationCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    calculating: str | None = None
    """
    observed values: ASYNCHRONOUS
    """
    illustration_name: Annotated[str | None, Field(alias="illustrationName")] = None
    firm_id: Annotated[str, Field(alias="firmId")]
    """
    observed values: SDEMO, SECCI
    """
    adviser_info: Annotated[AdviserInfo | None, Field(alias="adviserInfo")] = None
    client_info: Annotated[ClientInfo | None, Field(alias="clientInfo")] = None
    start_date: Annotated[date_aliased | None, Field(alias="startDate")] = None
    node_id: Annotated[str | None, Field(alias="nodeId")] = None
    """
    observed values: 0, 11, 12
    """
    plan_length_years: Annotated[float | None, Field(alias="planLengthYears")] = None
    periods: float | None = None
    wrapper_type: Annotated[str | None, Field(alias="wrapperType")] = None
    """
    observed values: GIA, ISA, PENSION
    """
    withdrawals: Withdrawals | None = None
    charges: Charges | None = None
    period_type: Annotated[str | None, Field(alias="periodType")] = None
    """
    observed values: MONTHS, Monthly
    """
    currency: str | None = None
    """
    observed values: GBP
    """
    fees: Fees | None = None
    other_platform_holdings: Annotated[float | None, Field(alias="otherPlatformHoldings")] = None
    payments: Payments | None = None
    transactions: list[Transaction] | None = None
    retirement_age: Annotated[float | None, Field(alias="retirementAge")] = None
    scheme_product_id: Annotated[str | None, Field(alias="schemeProductId")] = None
    """
    observed values: 00002
    """
    model_id: Annotated[str | None, Field(alias="modelId")] = None
    """
    observed values: 04926
    """
    type: str | None = None
    """
    observed values: SMPI
    """
    client_id: Annotated[str | None, Field(alias="clientId")] = None
    client_product_id: Annotated[str | None, Field(alias="clientProductId")] = None
    valuation_date: Annotated[date_aliased | None, Field(alias="valuationDate")] = None
    valuation_data: Annotated[list[ValuationData] | None, Field(alias="valuationData")] = None
    retirement_date: Annotated[date_aliased | None, Field(alias="retirementDate")] = None
    account_id: Annotated[str | None, Field(alias="accountId")] = None
    period: float | None = None
    growth_percentage: Annotated[float | None, Field(alias="growthPercentage")] = None
    illustration_type: Annotated[str | None, Field(alias="illustrationType")] = None
    """
    observed values: Trade
    """
    additional_clients_info: Annotated[
        list[AdditionalClientsInfoItem] | None, Field(alias="additionalClientsInfo")
    ] = None
    wrapper_status: Annotated[str | None, Field(alias="wrapperStatus")] = None
    """
    observed values: DRAWDOWN
    """
    drawdown: Drawdown | None = None
    salary: float | None = None
    salary_growth: Annotated[float | None, Field(alias="salaryGrowth")] = None
