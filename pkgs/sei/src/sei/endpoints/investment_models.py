"""PME model assignment — attaching an investment model to an account's portfolio.

Named ``investment_models`` rather than ``models`` so it does not read as :mod:`sei.models`,
which is the payload module.

**Which variant to call is a question about the model, not the account.** SEI has five
assignment endpoints and each takes a model of a particular `MODEL_TYPE` (from the nightly
Reference report):

===========================  ==========================================
Endpoint                     `MODEL_TYPE` it takes
===========================  ==========================================
``assignment/investment``    8 Investment Model
``assignment/implementation`` 4 Implementation Model
``assignment/allocation``    3 Asset Allocation Model
===========================  ==========================================

That is worth stating because the two variants named on our API sheet are *implementation*
and *allocation*, while every real model in the firm's own `raw.models_int_ext` is type 8 —
so the sheet's pair takes model types this firm does not have. Confirm with SEI which applies
before building an assignment path on top of this.

``toleranceMultiplier`` is mandatory on all three write variants and has **no source**: SEI
refers to its own `MODEL_RAIL_MULTIPLIER` table, which appears in neither the drop nor any
lookup service. It is configuration until SEI publishes it. ``apsId`` (allowable processing
schedule) does have a source — `ALLOWABLE_PROCESSING_SCH_ID` on `raw.models_int_ext`, with
labels under Reference `TYPE = ALLOWABLE_PROCESSING_SCHEDULE`.
"""

from __future__ import annotations

from typing import Any

from sei._base import BaseClient
from sei.endpoints._shapes import attributes as _attributes

SERVICE = "/pmemodelassignmentservices/PSM/model/assignment"


class InvestmentModelsApi(BaseClient):
    """Assign, read and de-assign a model on an account's portfolio."""

    async def assigned_model(
        self,
        firm_id: int | str,
        account_number: str,
        *,
        portfolio_id: int | str | None = None,
        portfolio_name: str | None = None,
    ) -> dict[str, Any]:
        """What model this portfolio is on (``POST {SERVICE}/byAccount``).

        One of `portfolioId` / `portfolioName` is required — an account with a Capital and an
        Income portfolio can hold two different models, so "the account's model" is not a
        question SEI answers.
        """
        return _attributes(
            await self.request(
                "POST",
                f"{SERVICE}/byAccount",
                json=_target(firm_id, account_number, portfolio_id, portfolio_name),
            )
        )

    async def assign_investment_model(self, request: dict[str, Any]) -> dict[str, Any]:
        """Assign an Investment Model, `MODEL_TYPE` 8 (``POST {SERVICE}/investment``).

        `firmId` + (`accountNumber` | `investmentGroupId`) + (`portfolioId` | `portfolioName`)
        + (`modelId` | `modelName`) + `toleranceMultiplier`, and `eventProcessingId` which is
        mandatory for a FIRST assignment and optional for a change.
        """
        return _attributes(await self.request("POST", f"{SERVICE}/investment", json=request))

    async def assign_implementation_model(self, request: dict[str, Any]) -> dict[str, Any]:
        """Assign an Implementation Model, `MODEL_TYPE` 4 (``POST {SERVICE}/implementation``).

        Same shape as :meth:`assign_investment_model` plus `sleeveConfigurationType`, which
        applies only when no model is assigned yet.
        """
        return _attributes(await self.request("POST", f"{SERVICE}/implementation", json=request))

    async def assign_allocation_model(self, request: dict[str, Any]) -> dict[str, Any]:
        """Assign an Asset Allocation Model, `MODEL_TYPE` 3 (``POST {SERVICE}/allocation``)."""
        return _attributes(await self.request("POST", f"{SERVICE}/allocation", json=request))

    async def remove_model(
        self,
        firm_id: int | str,
        account_number: str,
        *,
        portfolio_id: int | str | None = None,
        portfolio_name: str | None = None,
        tagging: int | None = None,
    ) -> dict[str, Any]:
        """De-assign (``POST {SERVICE}/delete``).

        ``tagging`` distinguishes a tactical from a strategic de-association; SEI defaults it
        to 2 when absent. This removes the model from the portfolio — it does not sell
        anything, so a portfolio left with no model keeps its holdings and stops being
        rebalanced, which is a real and easily-missed state.
        """
        payload = _target(firm_id, account_number, portfolio_id, portfolio_name)
        if tagging is not None:
            payload["tagging"] = tagging
        return _attributes(await self.request("POST", f"{SERVICE}/delete", json=payload))

    async def strategy_detail(self, firm_id: int | str, strategy_id: int | str) -> dict[str, Any]:
        """One strategy's composition
        (``POST /retrievestrategydetailservice/getStrategyDetail``)."""
        return _attributes(
            await self.request(
                "POST",
                "/retrievestrategydetailservice/getStrategyDetail",
                json={
                    "data": {"attributes": {"firmId": str(firm_id), "strategyId": str(strategy_id)}}
                },
            )
        )

    async def investment_group(
        self,
        firm_id: int | str,
        *,
        investment_group_id: int | str | None = None,
        investment_group_name: str | None = None,
    ) -> dict[str, Any]:
        """An investment group (``GET /pmeinvestmentgroupservices/PSM/group/investment``).

        A group is how one model is assigned to many accounts at once, so this is the read to
        make before deciding whether a firm's rebalance is per-account or per-group.
        """
        params: dict[str, Any] = {"firmId": str(firm_id)}
        if investment_group_id is not None:
            params["investmentGroupId"] = str(investment_group_id)
        if investment_group_name:
            params["investmentGroupName"] = investment_group_name
        return _attributes(
            await self.request(
                "GET", "/pmeinvestmentgroupservices/PSM/group/investment", params=params
            )
        )


def _target(
    firm_id: int | str,
    account_number: str,
    portfolio_id: int | str | None,
    portfolio_name: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"firmId": firm_id, "accountNumber": account_number}
    if portfolio_id is not None:
        payload["portfolioId"] = portfolio_id
    if portfolio_name:
        payload["portfolioName"] = portfolio_name
    return payload
