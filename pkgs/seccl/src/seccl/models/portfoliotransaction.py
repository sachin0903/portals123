"""PortfolioTransaction models, generated from the vendored Postman collection.

Do not edit by hand: `moon run seccl:build-schemas && moon run seccl:generate-models`.
The types are inferred from 9 example payload(s), not from a published schema --
see pkgs/seccl/README.md for what that does and does not buy you.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class RequestedBy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    user_id: Annotated[str, Field(alias="userId")]
    """
    observed values: 029B3HD
    """
    user_firm_id: Annotated[str, Field(alias="userFirmId")]
    """
    observed values: SECCL
    """
    user_type: Annotated[str, Field(alias="userType")]
    """
    observed values: Machine
    """
    user_name: Annotated[str, Field(alias="userName")]
    """
    observed values: Integration
    """


class DeletionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    requested_on: Annotated[str, Field(alias="requestedOn")]
    """
    observed values: 2024-12-09T16:13:46.233Z
    """
    requested_by: Annotated[RequestedBy, Field(alias="requestedBy")]


class Information(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    cash_interest_retained_reason: Annotated[str, Field(alias="cashInterestRetainedReason")]
    """
    observed values: Client Opted Out
    """


class PortfolioTransactionCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    firm_id: Annotated[str, Field(alias="firmId")]
    account_id: Annotated[str, Field(alias="accountId")]
    transaction_type: Annotated[str, Field(alias="transactionType")]
    """
    observed values: Cash, Order
    """
    transaction_sub_type: Annotated[str, Field(alias="transactionSubType")]
    """
    observed values: At Best, Platform Fee
    """
    movement_type: Annotated[str, Field(alias="movementType")]
    """
    observed values: Out, Raise
    """
    currency: str
    """
    observed values: GBP
    """
    amount: float
    asset_id: Annotated[str | None, Field(alias="assetId")] = None


class PortfolioTransactionUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    firm_id: Annotated[str | None, Field(alias="firmId")] = None
    id: str | None = None
    """
    observed values: 00008BM7P, 00008F19P
    """
    action_reason: Annotated[str | None, Field(alias="actionReason")] = None
    transaction_action: Annotated[str | None, Field(alias="transactionAction")] = None
    """
    observed values: Request Deletion
    """
    book_value: Annotated[float | str | None, Field(alias="bookValue")] = None
    update_id: Annotated[str | None, Field(alias="updateId")] = None
    created_by_firm: Annotated[str | None, Field(alias="createdByFirm")] = None
    update_note: Annotated[str | None, Field(alias="updateNote")] = None
    """
    observed values: My Update Note
    """
    effective_from: Annotated[str | None, Field(alias="effectiveFrom")] = None
    """
    observed values: 2025-01-01
    """


class AuditDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    update_date: Annotated[AwareDatetime, Field(alias="updateDate")]
    user_firm_id: Annotated[str, Field(alias="userFirmId")]
    """
    observed values: BACKGROUND, SECCI, SECCL, TEST1
    """
    user_id: Annotated[str, Field(alias="userId")]
    """
    observed values: 029B3F3, 029B3FF, 029B3HD, Background, TESTUSER
    """
    version: int
    application: str
    """
    observed values: PfolioAPI, PfolioEventAPI, PortfolioProcessor
    """


class CheckingResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    rule: str
    passed: bool


class ExecutionDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    currency: str
    """
    observed values: GBP
    """
    price: float
    transaction_time: Annotated[str, Field(alias="transactionTime")]
    """
    observed values: 00:00:00
    """
    venue: str
    """
    observed values: XLON
    """


class StatusChange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    status: str | None = None
    """
    observed values: Completed, Deleted, Pending, Waiting
    """
    status_date: Annotated[AwareDatetime, Field(alias="statusDate")]
    description: str
    transfer_status: Annotated[str | None, Field(alias="transferStatus")] = None
    """
    observed values: Onboarding Assets, With Previous Provider
    """


class TransferAccountAddress(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    flat_number: Annotated[str | None, Field(alias="flatNumber")] = None
    """
    observed values: 1, test
    """
    building_number: Annotated[str | None, Field(alias="buildingNumber")] = None
    """
    observed values: 1, 15, 2, 40
    """
    address1: str
    address2: str | None = None
    """
    observed values: 15 Waterloo Road, Doncaster, LONDON
    """
    country: str
    """
    observed values: GB
    """
    post_code: Annotated[str, Field(alias="postCode")]
    """
    observed values: AB1C 2DE, DN11 6RJ, KT13 8RB, SK152AU, SW17 8RB, TS1 1ST, W4 1EX
    """
    building_name: Annotated[str | None, Field(alias="buildingName")] = None
    """
    observed values: 1, 3
    """


class PortfolioTransaction(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str | None = None
    firm_id: Annotated[str | None, Field(alias="firmId")] = None
    """
    observed values: SDEMO, SECCI, TEST1
    """
    transaction_type: Annotated[str | None, Field(alias="transactionType")] = None
    """
    observed values: Cash, Notional, Order, Payment, Product Transfer, Stock Transfer
    """
    transaction_sub_type: Annotated[str | None, Field(alias="transactionSubType")] = None
    movement_type: Annotated[str | None, Field(alias="movementType")] = None
    """
    observed values: In, Invest, Out, Raise
    """
    account_id: Annotated[str | None, Field(alias="accountId")] = None
    currency: str | None = None
    """
    observed values: GBP
    """
    amount: float | None = None
    method: str | None = None
    """
    observed values: BACS Credit, Bank Transfer, Transfer
    """
    provider_id: Annotated[str | None, Field(alias="providerId")] = None
    """
    observed values: Test Provider
    """
    transaction_date: Annotated[AwareDatetime | None, Field(alias="transactionDate")] = None
    status: str | None = None
    """
    observed values: Checking, Completed, Deleted, Pending, Waiting
    """
    account_name: Annotated[str | None, Field(alias="accountName")] = None
    """
    observed values: GIA test, Raise Trades Testing, Test Account
    """
    account_type: Annotated[str | None, Field(alias="accountType")] = None
    """
    observed values: Wrapper
    """
    status_changes: Annotated[list[StatusChange] | None, Field(alias="statusChanges")] = None
    checking_results: Annotated[list[CheckingResult] | None, Field(alias="checkingResults")] = None
    node_id: Annotated[str | None, Field(alias="nodeId")] = None
    """
    observed values: 0, 1, 4, 5
    """
    created_date: Annotated[AwareDatetime | None, Field(alias="createdDate")] = None
    audit_details: Annotated[AuditDetails | None, Field(alias="auditDetails")] = None
    update_id: Annotated[str | None, Field(alias="updateId")] = None
    core_id: Annotated[str | None, Field(alias="coreId")] = None
    """
    observed values: 00007828J, JHZTIAAE
    """
    link_id: Annotated[str | None, Field(alias="linkId")] = None
    completed_date: Annotated[AwareDatetime | None, Field(alias="completedDate")] = None
    asset_id: Annotated[str | None, Field(alias="assetId")] = None
    """
    observed values: 26MM1, 26R3G, 27DQN, 29F55
    """
    intended_settlement_date: Annotated[
        AwareDatetime | None, Field(alias="intendedSettlementDate")
    ] = None
    asset_name: Annotated[str | None, Field(alias="assetName")] = None
    isin: str | None = None
    """
    observed values: GB0030720733, GB00B7T92B14, GB00B90TBZ18, GB00BZ82ZW98
    """
    mic: str | None = None
    """
    observed values: XOFF
    """
    instrument_type: Annotated[str | None, Field(alias="instrumentType")] = None
    """
    observed values: Fund
    """
    consideration: float | None = None
    executed_amount: Annotated[float | None, Field(alias="executedAmount")] = None
    executed_quantity: Annotated[float | None, Field(alias="executedQuantity")] = None
    execution_details: Annotated[ExecutionDetails | None, Field(alias="executionDetails")] = None
    trading_firm: Annotated[str | None, Field(alias="tradingFirm")] = None
    """
    observed values: SCUST
    """
    auto_disinvest: Annotated[str | None, Field(alias="autoDisinvest")] = None
    """
    observed values: Largest
    """
    auto_disinvest_sell_type: Annotated[str | None, Field(alias="autoDisinvestSellType")] = None
    """
    observed values: Amount
    """
    auto_disinvestment_method: Annotated[str | None, Field(alias="autoDisinvestmentMethod")] = None
    """
    observed values: Largest
    """
    deletion_request: Annotated[DeletionRequest | None, Field(alias="deletionRequest")] = None
    transfer_account_address: Annotated[
        TransferAccountAddress | None, Field(alias="transferAccountAddress")
    ] = None
    previous_provider: Annotated[str | None, Field(alias="previousProvider")] = None
    previous_provider_ref: Annotated[str | None, Field(alias="previousProviderRef")] = None
    transfer_value: Annotated[float | None, Field(alias="transferValue")] = None
    transfer_status: Annotated[str | None, Field(alias="transferStatus")] = None
    """
    observed values: Onboarding Assets, With Previous Provider
    """
    wrapper_type: Annotated[str | None, Field(alias="wrapperType")] = None
    """
    observed values: GIA, ISA, PENSION
    """
    client_name: Annotated[str | None, Field(alias="clientName")] = None
    client_id: Annotated[str | None, Field(alias="clientId")] = None
    action_required: Annotated[bool | None, Field(alias="actionRequired")] = None
    estimated_value: Annotated[float | None, Field(alias="estimatedValue")] = None
    product_drawdown_status: Annotated[str | None, Field(alias="productDrawdownStatus")] = None
    """
    observed values: No Drawdown
    """
    quantity: int | None = None
    book_value: Annotated[float | None, Field(alias="bookValue")] = None
    primary_client_id: Annotated[str | None, Field(alias="primaryClientId")] = None
    """
    observed values: ABC123
    """
    information: Information | None = None
