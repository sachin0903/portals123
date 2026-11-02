"""Model portfolios — discretionary and advisory.

The two differ in who may change them and therefore in their create path:
``POST /model`` is discretionary (it carries an `investmentDecision` block naming the LEI and
the individual making the decision — a regulatory requirement, not metadata) and
``POST /model/advisory`` is advisory (no decision maker; the adviser instructs per client).

**Attaching a model is a create-time act, and that is a real constraint.**
`wrapperDetail.assetAllocationId` is on `AccountCreate` and NOT on `AccountUpdate`, so
"change this account's model" is not a field edit. It is `recurringOrders` for the money that
has not arrived yet plus a rebalance for the holdings that have — see
:meth:`~seccl.endpoints.transactions.TransactionsApi.rebalance`. Anything in our own book that
writes `platform.accounts.model_id` and calls it done has changed our record and instructed
nobody.
"""

from __future__ import annotations

from typing import Any

from seccl._base import BaseClient
from seccl.endpoints import _paths


class ModelsApi(BaseClient):
    """Create, read and update model portfolios."""

    async def create_model(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create a DISCRETIONARY model (``POST /model``).

        `allocations[]` is `{assetId, percentage}` with percentages as FRACTIONS (0.68, not
        68) — the same convention Seccl uses for fee rates, and the trap
        `docs/provider-pipeline.md` records as `_allocation_lines`. `assetMix[]` is the
        regulatory breakdown by instrument type and is separate from the allocations.
        """
        return await self.write(
            "POST", _paths.collection("model"), {"firmId": self.firm_id, **body}
        )

    async def create_advisory_model(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create an ADVISORY model (``POST /model/advisory``)."""
        return await self.write(
            "POST", _paths.collection("model/advisory"), {"firmId": self.firm_id, **body}
        )

    async def model(self, model_id: str) -> dict[str, Any]:
        """One model (``GET /model/{firm}/{id}``)."""
        return await self.get_record(_paths.record("model", self.firm_id, model_id))

    async def models(self, **params: Any) -> list[dict[str, Any]]:
        """Every model at the firm (``GET /model/{firm}``).

        This is the read `platform.models` is populated from, and `productType` is a LIST
        despite the singular name — a model can serve GIA, ISA, JISA and PENSION at once.
        """
        return await self.get_records(_paths.scoped("model", self.firm_id), params or None)

    async def update_model(self, model_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Update a model (``PATCH /model/{firm}/{id}``).

        Applies to both kinds. **Publishing a new allocation set here rebalances every account
        on the model** at the next run — it is a decision about a book, not about a record, and
        `platform.model_versions` exists on our side to make that reviewable before it is sent.
        """
        return await self.write("PATCH", _paths.record("model", self.firm_id, model_id), changes)

    async def assets(self, **params: Any) -> list[dict[str, Any]]:
        """The firm's asset universe (``GET /asset/{firm}``).

        An `assetId` in an allocation must be in this list — Seccl's master universe is larger,
        and adding one to the firm is :meth:`add_asset`.
        """
        return await self.get_records(_paths.scoped("asset", self.firm_id), params or None)

    async def asset(self, asset_id: str) -> dict[str, Any]:
        """One asset (``GET /asset/{firm}/{id}``)."""
        return await self.get_record(_paths.record("asset", self.firm_id, asset_id))

    async def add_asset(self, body: dict[str, Any]) -> dict[str, Any]:
        """Add an asset to the firm's universe (``POST /asset``).

        The create convention holds: bare collection, firm in the body. (The collection's
        "Add asset to firm" example shows ``/asset/`` with a trailing slash and four other
        examples show ``/asset`` — same route.)
        """
        return await self.write("POST", _paths.collection("asset"), body)
