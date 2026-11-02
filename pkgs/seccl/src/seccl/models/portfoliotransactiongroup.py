"""PortfolioTransactionGroup models, generated from the vendored Postman collection.

Do not edit by hand: `moon run seccl:build-schemas && moon run seccl:generate-models`.
The types are inferred from 44 example payload(s), not from a published schema --
see pkgs/seccl/README.md for what that does and does not buy you.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class PortfolioTransactionGroup(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str


class PayUkDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    early_closure_waiver: Annotated[bool, Field(alias="earlyClosureWaiver")]
    account_designation: Annotated[str, Field(alias="accountDesignation")]
    """
    observed values: 3928293848
    """


class TransferFee(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    fee_type: Annotated[str, Field(alias="feeType")]
    """
    observed values: Adviser Initial Fee
    """
    rate_percentage: Annotated[float, Field(alias="ratePercentage")]


class TransferAccountAddress(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    flat_number: Annotated[str | None, Field(alias="flatNumber")] = None
    """
    observed values: 34, FIRST FLOOR FLAT
    """
    building_number: Annotated[str, Field(alias="buildingNumber")]
    """
    observed values: 22, 69
    """
    address1: str
    """
    observed values: CORONATION ROAD, Rocky Way
    """
    address2: str
    """
    observed values: BRISTOL, Bedrock
    """
    country: str
    """
    observed values: GB
    """
    post_code: Annotated[str, Field(alias="postCode")]
    """
    observed values: BD7 O77, BS3 1AT
    """


class Transaction(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    firm_id: Annotated[str, Field(alias="firmId")]
    transaction_type: Annotated[str, Field(alias="transactionType")]
    """
    observed values: Cash, Cash Transfer, Order, Payment, Product Transfer, Stock Transfer
    """
    transaction_sub_type: Annotated[str, Field(alias="transactionSubType")]
    movement_type: Annotated[str, Field(alias="movementType")]
    """
    observed values: In, Invest, Out, Raise, Sell
    """
    account_id: Annotated[str, Field(alias="accountId")]
    """
    observed values: 0352GDJ
    """
    previous_provider: Annotated[str | None, Field(alias="previousProvider")] = None
    """
    observed values: 021FC19
    """
    previous_provider_ref: Annotated[str | None, Field(alias="previousProviderRef")] = None
    previous_provider_account_type: Annotated[
        str | None, Field(alias="previousProviderAccountType")
    ] = None
    """
    observed values: Cash
    """
    transfer_account_address: Annotated[
        TransferAccountAddress | None, Field(alias="transferAccountAddress")
    ] = None
    pay_uk_details: Annotated[PayUkDetails | None, Field(alias="payUkDetails")] = None
    partial_transfer: Annotated[bool | None, Field(alias="partialTransfer")] = None
    amount_to_take: Annotated[float | None, Field(alias="amountToTake")] = None
    subscription_applies_to: Annotated[str | None, Field(alias="subscriptionAppliesTo")] = None
    """
    observed values: Current Year, Previous Year
    """
    currency: str | None = None
    """
    observed values: GBP
    """
    amount: float | None = None
    method: str | None = None
    """
    observed values: BACS Credit, Bank Transfer, Bulk Bank Transfer, Direct Debit, Transfer
    """
    asset_id: Annotated[str | None, Field(alias="assetId")] = None
    """
    observed values: 26NHB, 275F1, 284PF, 286Q5
    """
    charge_account: Annotated[str | None, Field(alias="chargeAccount")] = None
    company_reg_no: Annotated[str | None, Field(alias="companyRegNo")] = None
    external_reference: Annotated[str | None, Field(alias="externalReference")] = None
    """
    observed values: <externalReference>
    """
    auto_invest: Annotated[bool | None, Field(alias="autoInvest")] = None
    quantity: int | float | str | None = None
    percentage: float | None = None
    auto_disinvest: Annotated[str | None, Field(alias="autoDisinvest")] = None
    """
    observed values: Full, Largest, Proportional
    """
    auto_disinvest_sell_type: Annotated[str | None, Field(alias="autoDisinvestSellType")] = None
    """
    observed values: Amount
    """
    provider_ref: Annotated[str | None, Field(alias="providerRef")] = None
    """
    observed values: AB00001234
    """
    transfer_value: Annotated[float | None, Field(alias="transferValue")] = None
    is_pension_transfer: Annotated[bool | None, Field(alias="isPensionTransfer")] = None
    product_drawdown_status: Annotated[str | None, Field(alias="productDrawdownStatus")] = None
    """
    observed values: No Drawdown
    """
    primary_client_id: Annotated[str | None, Field(alias="primaryClientId")] = None
    """
    observed values: ABC123
    """
    receiving_provider: Annotated[str | None, Field(alias="receivingProvider")] = None
    receiving_provider_ref: Annotated[str | None, Field(alias="receivingProviderRef")] = None
    """
    observed values: ABC324234234, test
    """
    bank_ref: Annotated[str | None, Field(alias="bankRef")] = None
    """
    observed values: Bank Ref, Testing
    """
    receiving_provider_id: Annotated[str | None, Field(alias="receivingProviderId")] = None
    uploaded_file_details_id: Annotated[str | None, Field(alias="uploadedFileDetailsId")] = None
    transfer_fee: Annotated[TransferFee | None, Field(alias="transferFee")] = None
    destination_account: Annotated[str | None, Field(alias="destinationAccount")] = None


class PortfolioTransactionGroupCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    firm_id: Annotated[str, Field(alias="firmId")]
    account_id: Annotated[str, Field(alias="accountId")]
    """
    observed values: 0352GDJ
    """
    transactions: list[Transaction]
