"""Fee structures and client fee groups.

Two levels, and they answer different questions. A **fee structure** is the firm's priced
product (platform fee, adviser fee) and lives at the firm; a **client fee group** is a set of
accounts whose values are added together before a tiered rate is applied — which is
materially cheaper for a household, and is the reason a firm asks for one.

Assigning a structure is not an endpoint here: it is `feeStructures.adviserFeeId` on a client,
an account or a fee group, i.e. a PATCH through
:mod:`seccl.endpoints.clients`. That asymmetry is Seccl's, and it is why "assign a fee" does
not appear as a method — a caller looking for it would otherwise conclude it is unsupported.

The register read is the source `platform.fee_packages` is populated from, and three of its
conventions are load-bearing (`docs/provider-pipeline.md`): rates are FRACTIONS (`0.0035` =
0.35%), `type` has to be DERIVED because fixed/percentage/tiered all publish one `tiers` array,
and tier bands start at the next penny (`10000.01`) so they must be normalised contiguous or
`catalogue.validate_fee_rule` refuses them as having a gap.
"""

from __future__ import annotations

from typing import Any

from seccl._base import BaseClient
from seccl.endpoints import _paths


class FeesApi(BaseClient):
    """Read the fee register; manage client fee groups."""

    async def fee_structures(self, **params: Any) -> list[dict[str, Any]]:
        """The firm's fee register (``GET /v1/fee-structures/{firm}``).

        The only ``/v1/`` path in the read set. Filter by `type` (``Platform Fee`` /
        ``Adviser Fee``).

        **An empty register is a real answer**, and one this integration has hit: Seccl's
        staging firm returns `meta.count` 0. That is why `services/seccl`'s
        `raw.fee_structures` declares its whole read shape as a spine — with no rows to infer
        from, `tiers` and `type` would not exist as columns and the conformed model would fail
        to bind on precisely the firm that has no fee structures.
        """
        return await self.get_records(
            _paths.scoped("v1/fee-structures", self.firm_id), params or None
        )

    async def fee_structure(self, fee_structure_id: str) -> dict[str, Any]:
        """One fee structure (``GET /v1/fee-structures/{firm}/{id}``)."""
        return await self.get_record(
            _paths.record("v1/fee-structures", self.firm_id, fee_structure_id)
        )

    async def create_fee_group(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create a client fee group (``POST /portfoliogroup``).

        `groupIntent` is ``Client Fee Group`` and `intentDetails` says which fee types the
        group aggregates (`adviserFee`, `platformFee`) — enabling one and not the other is
        normal, because the two are often tiered differently.
        """
        return await self.write(
            "POST", _paths.collection("portfoliogroup"), {"firmId": self.firm_id, **body}
        )

    async def fee_group(self, portfolio_group_id: str) -> dict[str, Any]:
        """One fee group (``GET /portfoliogroup/{firm}/{id}``)."""
        return await self.get_record(
            _paths.record("portfoliogroup", self.firm_id, portfolio_group_id)
        )

    async def update_fee_group(
        self, portfolio_group_id: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a fee group (``PATCH /portfoliogroup/{firm}/{id}``) — membership, name,
        enabled fee types, the adviser fee structure, archive/reinstate.

        Membership can be changed from either end (a client's own PATCH carries the group), and
        the two are not equivalent when a client belongs to several groups. Reading the group
        back is the only way to be sure which happened.
        """
        return await self.write(
            "PATCH", _paths.record("portfoliogroup", self.firm_id, portfolio_group_id), changes
        )

    async def cash_postings(self, **params: Any) -> list[dict[str, Any]]:
        """Cash postings (``GET /posting/cash/{firm}``).

        The fee-reconciliation read: filter `transactionCode` (from
        :meth:`~seccl.endpoints.transactions.TransactionsApi.transaction_codes`) with
        `postDateFrom`/`postDateTo` to see what was actually charged, as against what the
        register says should be.
        """
        return await self.get_records(_paths.scoped("posting/cash", self.firm_id), params or None)

    async def stock_postings(self, **params: Any) -> list[dict[str, Any]]:
        """Stock postings (``GET /posting/stock/{firm}``)."""
        return await self.get_records(_paths.scoped("posting/stock", self.firm_id), params or None)
