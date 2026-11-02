"""Reads for testing and for the money-out gate — holdings, balances, transactions.

None of this is the reporting path. The nightly drop is the book (`eod_positions`,
`transaction_header`, `eod_pos_val_aggr`) and it is complete, free and already conformed;
these endpoints are point-in-time reads for two narrow jobs:

* **Confirming a write landed** without waiting for tomorrow's drop — the reason every module
  in this package ships GETs.
* **Answering "is there enough cash"** before instructing a withdrawal.
  :meth:`HoldingsApi.net_available_balance` is the live figure; the drop's is last night's,
  and a withdrawal gate that trusts an overnight number can overdraw an account that has
  since paid a fee.

:meth:`HoldingsApi.transactions` is the same endpoint `services/sei`'s
`raw.api_transactions` model uses, and its caveats are recorded there: it refuses a firm-wide
query (one request per account), the window is capped at 30 days, `endDate` may not be today,
and it returns a **disjoint population** from the drop — the cash side (fees, interest,
receipts, disbursements) and not the trade side.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sei._base import BaseClient
from sei.endpoints._shapes import attributes as _attributes
from sei.endpoints._shapes import rows as _rows


class HoldingsApi(BaseClient):
    """Live positions, balances and transaction history."""

    async def realtime_holdings(
        self,
        firm_id: int | str,
        *,
        account_number: str | None = None,
        client_id: str | None = None,
        portfolio_id: int | str | None = None,
    ) -> list[dict[str, Any]]:
        """Positions as they stand now (``GET /realtimeholdingsservice/realtimeholdings``)."""
        params: dict[str, Any] = {"firmId": str(firm_id)}
        if account_number:
            params["accountNumber"] = account_number
        if client_id:
            params["clientId"] = client_id
        if portfolio_id is not None:
            params["portfolioId"] = str(portfolio_id)
        # `key=`: this response carries `holdingsDetails` AND `warning`, so an account with no
        # positions would return SEI's own `{code, title, detail}` warning as one holding.
        return _rows(
            await self.request("GET", "/realtimeholdingsservice/realtimeholdings", params=params),
            key="holdingsDetails",
        )

    async def net_available_balance(self, request: dict[str, Any]) -> dict[str, Any]:
        """Uncommitted cash, per currency (``POST /holdingservices/netavailablecurrencybalance``).

        "Net available" and not "balance": it is the figure after committed activity, which is
        the one a withdrawal has to be tested against. A balance that looks sufficient can
        already be spoken for by a pending disbursement.
        """
        return _attributes(
            await self.request("POST", "/holdingservices/netavailablecurrencybalance", json=request)
        )

    async def transactions(
        self,
        firm_id: int | str,
        account_number: str,
        start: date,
        end: date,
        *,
        date_type: int = 1,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        """Transactions for one account over at most 30 days
        (``GET /transactiondetailsservice/transactions``).

        `date_type` is 1 Transaction / 2 Trade / 3 Settlement — there is no last-modified,
        which is why any incremental read has to overlap its own cursor to see a correction
        booked today against an older row.

        Returns the whole envelope (not just the rows) because `totalPageCount` and
        `totalNumberOfRecords` are needed to page, and `warning[]` carries SEI's note when it
        silently widened the page size.
        """
        return dict(
            await self.request(
                "GET",
                "/transactiondetailsservice/transactions",
                params={
                    "firmId": str(firm_id),
                    "accountNumber": account_number,
                    "dateType": str(date_type),
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "pageNumber": str(page),
                    "pageSize": str(page_size),
                },
            )
        )

    async def historic_market_values(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        """Multi-account historic market values
        (``POST /historicmarketvalueservice/accountsHistoricMarketValues``).

        The valuation series. Relevant to the Morgan Lloyd daily push, which needs an account
        value AND its fund splits — this gives the value; the splits come from holdings.
        """
        # `key=`: per-account values come beside per-ROLLUP ones, so a household total would be
        # returned as one account's value with nothing on the row saying so.
        return _rows(
            await self.request(
                "POST", "/historicmarketvalueservice/accountsHistoricMarketValues", json=request
            ),
            key="accountHistoricMarketvalues",
        )

    async def taxlots(
        self, firm_id: int | str, account_number: str, *, portfolio_id: int | str | None = None
    ) -> list[dict[str, Any]]:
        """CGT cost basis by lot (``GET /investmentmanagementtaxlotsservice/portfolio/taxlots``)."""
        params: dict[str, Any] = {"firmId": str(firm_id), "accountNumber": account_number}
        if portfolio_id is not None:
            params["portfolioId"] = str(portfolio_id)
        return _rows(
            await self.request(
                "GET", "/investmentmanagementtaxlotsservice/portfolio/taxlots", params=params
            )
        )
