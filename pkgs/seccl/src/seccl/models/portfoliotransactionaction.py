"""PortfolioTransactionAction models, generated from the vendored Postman collection.

Do not edit by hand: `moon run seccl:build-schemas && moon run seccl:generate-models`.
The types are inferred from 8 example payload(s), not from a published schema --
see pkgs/seccl/README.md for what that does and does not buy you.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class PortfolioTransactionAction(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


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
    execution_amount: Annotated[float, Field(alias="executionAmount")]
    executed_quantity: Annotated[float, Field(alias="executedQuantity")]


class PortfolioTransactionActionUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: str | None = None
    """
    observed values: Action
    """
    firm_id: Annotated[str | None, Field(alias="firmId")] = None
    transaction_action: Annotated[str | None, Field(alias="transactionAction")] = None
    """
    observed values: Complete
    """
    action_reason: Annotated[str | None, Field(alias="actionReason")] = None
    """
    observed values: Mock cash received, Order executed, Payment received
    """
    completed_date: Annotated[str | None, Field(alias="completedDate")] = None
    execution_details: Annotated[ExecutionDetails | None, Field(alias="executionDetails")] = None
    quantity: float | None = None
    amount: float | None = None
    transaction_date: Annotated[str | None, Field(alias="transactionDate")] = None
    intended_settlement_date: Annotated[str | None, Field(alias="intendedSettlementDate")] = None
