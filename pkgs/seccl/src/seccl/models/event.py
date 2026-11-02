"""Event models, generated from the vendored Postman collection.

Do not edit by hand: `moon run seccl:build-schemas && moon run seccl:generate-models`.
The types are inferred from 5 example payload(s), not from a published schema --
see pkgs/seccl/README.md for what that does and does not buy you.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class Event(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    """
    observed values: 00PL9L
    """


class ConfirmedAllocation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    asset_id: Annotated[str, Field(alias="assetId")]
    """
    observed values: 26MQ9, 27HNC
    """
    percentage: float
    currency: str
    """
    observed values: GBP
    """
    buy_price: Annotated[float, Field(alias="buyPrice")]
    sell_price: Annotated[float, Field(alias="sellPrice")]


class ConfirmedDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    confirmed_cash_allocation: Annotated[float, Field(alias="confirmedCashAllocation")]
    confirmed_allocations: Annotated[list[ConfirmedAllocation], Field(alias="confirmedAllocations")]


class EventCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    firm_id: Annotated[str, Field(alias="firmId")]
    event_type: Annotated[str, Field(alias="eventType")]
    """
    observed values: Portfolio Realign, Rebalance, Valuation Statement
    """
    account_id: Annotated[str | None, Field(alias="accountId")] = None
    asset_allocation_id: Annotated[str | None, Field(alias="assetAllocationId")] = None
    confirmed_details: Annotated[ConfirmedDetails | None, Field(alias="confirmedDetails")] = None
    from_date: Annotated[date | None, Field(alias="fromDate")] = None
    to_date: Annotated[date | None, Field(alias="toDate")] = None
    client_id: Annotated[str | None, Field(alias="clientId")] = None
