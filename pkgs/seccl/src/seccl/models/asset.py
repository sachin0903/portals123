"""Asset models, generated from the vendored Postman collection.

Do not edit by hand: `moon run seccl:build-schemas && moon run seccl:generate-models`.
The types are inferred from 5 example payload(s), not from a published schema --
see pkgs/seccl/README.md for what that does and does not buy you.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class AssetCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    firm_id: Annotated[str, Field(alias="firmId")]
    id: str
    """
    observed values: 275F1
    """
    available_to_node_id: Annotated[list[str] | None, Field(alias="availableToNodeId")] = None


class AuditDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    update_date: Annotated[AwareDatetime, Field(alias="updateDate")]
    user_firm_id: Annotated[str, Field(alias="userFirmId")]
    """
    observed values: BACKGROUND, SECCL
    """
    user_id: Annotated[str, Field(alias="userId")]
    """
    observed values: 029B3F3, Background
    """
    version: int
    application: str
    """
    observed values: PfolioAPI, ProductProcessor
    """


class Classification(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    classification_type: Annotated[str, Field(alias="classificationType")]
    """
    observed values: IMA, LSE Sector, LSE Segment
    """
    value: str


class MifidCharges(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    ex_ante_ongoing: Annotated[float, Field(alias="exAnteOngoing")]
    ex_ante_incidental: Annotated[float, Field(alias="exAnteIncidental")]
    ex_ante_transaction: Annotated[float, Field(alias="exAnteTransaction")]


class Asset(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    type: str | None = None
    """
    observed values: Asset
    """
    firm_id: Annotated[str | None, Field(alias="firmId")] = None
    """
    observed values: SDEMO, SECCI
    """
    isin: str | None = None
    short_name: Annotated[str | None, Field(alias="shortName")] = None
    long_name: Annotated[str | None, Field(alias="longName")] = None
    instrument_type: Annotated[str | None, Field(alias="instrumentType")] = None
    """
    observed values: ETF, Equity, Fund
    """
    currency: str | None = None
    """
    observed values: EUR, GBP, USD
    """
    ticker: str | None = None
    mic: str | None = None
    """
    observed values: XETR, XFRA, XLON, XNCM, XOFF, XPAR
    """
    status: str | None = None
    """
    observed values: Active
    """
    firm_access_prohibited: Annotated[bool | None, Field(alias="firmAccessProhibited")] = None
    available_to_node_id: Annotated[list[str] | None, Field(alias="availableToNodeId")] = None
    audit_details: Annotated[AuditDetails | None, Field(alias="auditDetails")] = None
    update_id: Annotated[str | None, Field(alias="updateId")] = None
    minimum_transfer_unit: Annotated[float | None, Field(alias="minimumTransferUnit")] = None
    quote_unit: Annotated[float | None, Field(alias="quoteUnit")] = None
    settlement_period: Annotated[float | None, Field(alias="settlementPeriod")] = None
    custom_name: Annotated[str | None, Field(alias="customName")] = None
    """
    observed values: ARCAM calum test
    """
    node_id: Annotated[list[str] | None, Field(alias="nodeId")] = None
    classifications: list[Classification] | None = None
    ask: float | None = None
    bid: float | None = None
    mid: float | None = None
    ocf_estimated: Annotated[float | None, Field(alias="ocfEstimated")] = None
    price_date: Annotated[AwareDatetime | None, Field(alias="priceDate")] = None
    mex_id: Annotated[str | None, Field(alias="mexId")] = None
    ptm: bool | None = None
    stampable: bool | None = None
    stamp_country: Annotated[str | None, Field(alias="stampCountry")] = None
    """
    observed values: GB
    """
    ocf_calculated: Annotated[float | None, Field(alias="ocfCalculated")] = None
    kiid_document_url: Annotated[str | None, Field(alias="kiidDocumentUrl")] = None
    kidd_document_url: Annotated[str | None, Field(alias="kiddDocumentUrl")] = None
    mifid_charges: Annotated[MifidCharges | None, Field(alias="mifidCharges")] = None
    primary_market: Annotated[bool | None, Field(alias="primaryMarket")] = None
    field_long_name: Annotated[str | None, Field(alias="_longName")] = None
    field_short_name: Annotated[str | None, Field(alias="_shortName")] = None
