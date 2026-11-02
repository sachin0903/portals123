"""ModelCreate models, generated from the vendored Postman collection.

Do not edit by hand: `moon run seccl:build-schemas && moon run seccl:generate-models`.
The types are inferred from 2 example payload(s), not from a published schema --
see pkgs/seccl/README.md for what that does and does not buy you.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class InvestmentDecision(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    firm_lei: Annotated[str, Field(alias="firmLei")]
    """
    observed values: 82787287HFJEE9393892
    """
    firm_country: Annotated[str, Field(alias="firmCountry")]
    """
    observed values: GB
    """
    person_id: Annotated[str, Field(alias="personId")]
    """
    observed values: PC893898C
    """
    person_id_type: Annotated[str, Field(alias="personIdType")]
    """
    observed values: NINO
    """
    person_country: Annotated[str, Field(alias="personCountry")]
    """
    observed values: GB
    """


class Allocation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    asset_id: Annotated[str, Field(alias="assetId")]
    """
    observed values: 26MPM, 2756G, 275F1, 27HNC
    """
    percentage: float


class AssetMix(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    instrument_type: Annotated[str, Field(alias="instrumentType")]
    """
    observed values: Cash, Corporate bonds, Equity, Gilts, Other
    """
    instrument_rate_type: Annotated[float, Field(alias="instrumentRateType")]


class ModelCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    firm_id: Annotated[str, Field(alias="firmId")]
    name: str
    """
    observed values: High-growth model, High-growth model
    """
    risk_rating: Annotated[float, Field(alias="riskRating")]
    model_type: Annotated[str, Field(alias="modelType")]
    """
    observed values: Advisory, Discretionary
    """
    product_type: Annotated[list[str], Field(alias="productType")]
    aim: str
    """
    observed values: Invest ethically
    """
    model_charge: Annotated[float | None, Field(alias="modelCharge")] = None
    vatable: bool | None = None
    suspend_rebalances: Annotated[bool, Field(alias="suspendRebalances")]
    available_to_node_id: Annotated[list[str], Field(alias="availableToNodeId")]
    allocations: list[Allocation]
    investment_decision: Annotated[InvestmentDecision | None, Field(alias="investmentDecision")] = (
        None
    )
    asset_mix: Annotated[list[AssetMix], Field(alias="assetMix")]


class ModelUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    update_id: Annotated[str, Field(alias="updateId")]
    allocations: list[Allocation] | None = None
    cash_allocation: Annotated[float | None, Field(alias="cashAllocation")] = None
    name: str | None = None
