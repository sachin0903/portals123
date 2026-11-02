"""Illustrations and valuations — the two reads a client is actually shown.

**Seccl is the only one of the three custodians that can illustrate a GIA or an ISA.** CTC
does pensions (`SIPPEN`) and `apps/api/app/routers/illustrations.py` refuses anything that is
not a pension wrapper, correctly. So if a firm wants a non-pension illustration, this is the
route, and that is a capability decision rather than a preference.

The illustration is **asynchronous by request**: send `calculating: "ASYNCHRONOUS"`, then poll
:meth:`IllustrationsApi.illustration`. The PDF is a second step again — `POST /files/generate`
renders it and `POST /files/illustration` returns a URL to fetch. Three calls, and the middle
one is easy to miss because the illustration reads as complete without it.

One field has no source in our schema: `adviserInfo.FCANumber`. There is no FCA reference on a
staff member or on a tenant, and it appears on the client-facing document — so it is either
configuration or it is wrong.
"""

from __future__ import annotations

from typing import Any

from seccl._base import BaseClient
from seccl.endpoints import _paths


class IllustrationsApi(BaseClient):
    """Product and trade illustrations; account and portfolio valuations."""

    # --- illustrations ----------------------------------------------------------

    async def product_illustration(self, body: dict[str, Any]) -> dict[str, Any]:
        """Illustrate a product (``POST /illustration/product``).

        Covers GIA/ISA/Joint GIA, pension accumulation and pension drawdown — which one is
        decided by `wrapperType` plus `wrapperStatus`/`drawdown`, not by the path.

        `clientInfo` takes EITHER an existing `{id}` or a full identity block; the second form
        **creates a client** as a side effect, which is the one way to get a client and an
        illustration in a single call. That is a real write hiding inside a read-shaped
        endpoint.
        """
        return await self.write(
            "POST", _paths.collection("illustration/product"), {"firmId": self.firm_id, **body}
        )

    async def trade_illustration(self, body: dict[str, Any]) -> dict[str, Any]:
        """Illustrate a trade (``POST /illustration/trade``) — the effect of a set of orders on
        an existing account, rather than a whole product."""
        return await self.write(
            "POST", _paths.collection("illustration/trade"), {"firmId": self.firm_id, **body}
        )

    async def smpi(self, body: dict[str, Any]) -> dict[str, Any]:
        """A Statutory Money Purchase Illustration (``POST /illustration/smpi``)."""
        return await self.write(
            "POST", _paths.collection("illustration/smpi"), {"firmId": self.firm_id, **body}
        )

    async def illustration(self, illustration_id: str) -> dict[str, Any]:
        """One illustration (``GET /illustration/product/{firm}/{id}``) — poll this after an
        asynchronous request; `status` is ``Pending`` until it is not."""
        return await self.get_record(
            _paths.record("illustration/product", self.firm_id, illustration_id)
        )

    async def illustrations(self, **params: Any) -> list[dict[str, Any]]:
        """Illustrations, filtered (``GET /illustration/product/{firm}``)."""
        return await self.get_records(
            _paths.scoped("illustration/product", self.firm_id), params or None
        )

    async def illustration_pdf_url(self, body: dict[str, Any]) -> str:
        """Render and return a URL for the illustration PDF.

        Two calls: ``POST /files/generate`` renders, then ``POST /files/illustration`` answers
        the URL. Skipping the first returns a URL for a document that does not exist yet.
        """
        await self.write("POST", "/files/generate", {"firmId": self.firm_id, **body})
        return await self.file_url("illustration", {"firmId": self.firm_id, **body})

    # --- valuations -------------------------------------------------------------

    async def account_summary(self, account_id: str) -> dict[str, Any]:
        """An account's valuation (``GET /account/summary/{firm}/{id}``) — value, growth,
        book cost, uninvested cash and positions as at the last valuation point."""
        return await self.get_record(_paths.record("account/summary", self.firm_id, account_id))

    async def live_account_summary(self, account_id: str) -> dict[str, Any]:
        """An account's LIVE available balances (``GET /account/summary/live/{firm}/{id}``).

        The figure to test a withdrawal against. The plain summary is as at the last valuation
        point, so a gate that trusts it can overdraw an account that has since paid a fee.
        """
        return await self.get_record(
            _paths.record("account/summary/live", self.firm_id, account_id)
        )

    async def portfolio_summary(self, client_id: str) -> dict[str, Any]:
        """A client's whole position across accounts (``GET /portfolio/summary/{firm}/{id}``).

        Also where the ISA/JISA allowance is read from, which is why it is a client-level and
        not an account-level question.
        """
        return await self.get_record(_paths.record("portfolio/summary", self.firm_id, client_id))

    async def position(self, position_id: str) -> dict[str, Any]:
        """One holding (``GET /position/{firm}/{id}``)."""
        return await self.get_record(_paths.record("position", self.firm_id, position_id))

    # --- client documents -------------------------------------------------------

    async def messages(self, **params: Any) -> list[dict[str, Any]]:
        """Client messages / documents (``GET /message/{firm}``).

        Seccl publishes contract notes, valuation statements, tax packs and declaration
        replays as *messages*; `messageType` selects which. The document itself is then a
        `POST /files/{type}` away — :meth:`seccl.SecclClient.file_url`.
        """
        return await self.get_records(_paths.scoped("message", self.firm_id), params or None)

    async def message(self, message_id: str) -> dict[str, Any]:
        """One message (``GET /message/{firm}/{id}``)."""
        return await self.get_record(_paths.record("message", self.firm_id, message_id))
