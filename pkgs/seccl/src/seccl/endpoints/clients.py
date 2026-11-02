"""Clients and their investment accounts.

**Ordering is fixed and it is the one respect in which Seccl is stricter than SEI.** An
account cannot be created without a `clientId`, so "new client with a new account" is two
calls; SEI does both in one. The single exception is
:meth:`~seccl.endpoints.illustrations.IllustrationsApi.product_illustration` with a
`clientInfo` block, which mints a client as a side effect of illustrating.

**Every update needs the record's current `updateId`.** Seccl uses it as an optimistic-lock
token: PATCH without it, or with a stale one, is refused. So an update is always read-then-
write, which is why :meth:`ClientsApi.update_client` and
:meth:`ClientsApi.update_account` take it explicitly rather than fetching it themselves —
a helper that re-read the record would quietly overwrite a concurrent change.

`accountType` is always ``Wrapper``; the product is `wrapperDetail.wrapperType`
(``GIA``/``ISA``/``JISA``/``PENSION``). A pension additionally needs
`wrapperDetail.schemeProductId`, from :meth:`ClientsApi.scheme_products`.
"""

from __future__ import annotations

from typing import Any

from seccl._base import BaseClient
from seccl.endpoints import _paths


class ClientsApi(BaseClient):
    """Create, read and update clients, accounts and their client products."""

    # --- clients ----------------------------------------------------------------

    async def create_client(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create a client (``POST /client``).

        The firm goes in the body (`firmId`), not the path. `nodeId` is a LIST on a client and
        a scalar on an account — Seccl's own asymmetry, and a list of one is the usual value.

        The vulnerability block maps straight onto ours: Seccl's four `reason` members are the
        FCA FG21/1 drivers `crm.contacts.vulnerability_reasons` stores, so no translation is
        needed in either direction.

        **`firmId` is stamped here** rather than left to the caller, as it is on every other
        create in this package. It was not, and the docstring above said it belonged in the body
        while the method did not put it there — so a caller who believed the documentation got
        `500 Internal Server Error` from Seccl, with no field error and nothing naming the
        omission. An explicit `firmId` in `body` still wins.
        """
        return await self.write(
            "POST", _paths.collection("client"), {"firmId": self.firm_id, **body}
        )

    async def client(self, client_id: str) -> dict[str, Any]:
        """One client (``GET /client/{firm}/{id}``)."""
        return await self.get_record(_paths.record("client", self.firm_id, client_id))

    async def clients(self, **params: Any) -> list[dict[str, Any]]:
        """Every client at the firm (``GET /client/{firm}``), paginated.

        Goes through ``get_list``, which refetches a set under the single-page cap as one
        exactly-sized page — Seccl's page-boundary ordering is unstable and a naive walk both
        duplicates and DROPS rows.
        """
        return await self.get_list(_paths.scoped("client", self.firm_id), params or None)

    async def update_client(
        self, client_id: str, update_id: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        """Partially update a client (``PATCH /client/{firm}/{id}``).

        Sets what it names and leaves the rest. ``update_id`` is the `updateId` from the
        record as read; a stale one is refused rather than applied.
        """
        return await self.write(
            "PATCH",
            _paths.record("client", self.firm_id, client_id),
            {**changes, "updateId": update_id},
        )

    async def set_client_status(self, client_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """A client status transition (``PUT /client/{firm}/{id}``).

        PUT rather than PATCH is Seccl's marker for a *lifecycle* change as against a field
        edit — Registered → Active on terms acceptance, mandate accept/cancel, date-of-death
        confirmation, bank-detail verification. Each has its own body shape; the collection's
        "Update a client" folder is the reference.
        """
        return await self.write("PUT", _paths.record("client", self.firm_id, client_id), body)

    # --- accounts ---------------------------------------------------------------

    async def create_account(self, body: dict[str, Any]) -> dict[str, Any]:
        """Open an investment account (``POST /account``).

        Requires an existing `clientId`. `wrapperDetail.assetAllocationId` attaches a model
        **at create time only** — `AccountUpdate` carries no `wrapperDetail`, so changing a
        model later is `recurringOrders` plus a rebalance event, not a field edit
        (:mod:`seccl.endpoints.models`).

        `firmId` is stamped here for the same reason it is on :meth:`create_client`; an explicit
        one in `body` still wins.
        """
        return await self.write(
            "POST", _paths.collection("account"), {"firmId": self.firm_id, **body}
        )

    async def account(self, account_id: str) -> dict[str, Any]:
        """One account (``GET /account/{firm}/{id}``)."""
        return await self.get_record(_paths.record("account", self.firm_id, account_id))

    async def accounts_for_client(self, client_id: str) -> list[dict[str, Any]]:
        """A client's accounts (``GET /account/{firm}/?clientId=…``)."""
        return await self.get_records(
            _paths.scoped("account", self.firm_id) + "/", {"clientId": client_id}
        )

    async def accounts(self, **params: Any) -> list[dict[str, Any]]:
        """Every account at the firm (``GET /account/list/{firm}``), paginated."""
        return await self.get_list(_paths.scoped("account/list", self.firm_id), params or None)

    async def update_account(
        self, account_id: str, update_id: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        """Partially update an account (``PATCH /account/{firm}/{id}``).

        This is the door to `recurringPayments`, `recurringOrders`, `autoDisinvest` and
        `feeStructures.adviserFeeId` — i.e. to every standing arrangement on the account.
        """
        return await self.write(
            "PATCH",
            _paths.record("account", self.firm_id, account_id),
            {**changes, "updateId": update_id},
        )

    async def set_account_status(self, account_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """An account status transition (``PUT /account/{firm}/{id}``)."""
        return await self.write("PUT", _paths.record("account", self.firm_id, account_id), body)

    # --- client products --------------------------------------------------------

    async def client_product(self, client_product_id: str) -> dict[str, Any]:
        """One client product (``GET /clientproduct/{firm}/{id}``).

        The product is the wrapper's contractual layer, and it is where the things a *client*
        agreed to live: declaration timestamps, pension terms, nominees, LTA/LSA figures and
        protections. The account is the investment container; the client product is the
        contract. Almost every pension write is against this, not the account.
        """
        return await self.get_record(
            _paths.record("clientproduct", self.firm_id, client_product_id)
        )

    async def update_client_product(
        self, client_product_id: str, update_id: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a client product (``PATCH /clientproduct/{firm}/{id}``) — declarations
        accepted, nominees, pension protections, lump-sum allowances."""
        return await self.write(
            "PATCH",
            _paths.record("clientproduct", self.firm_id, client_product_id),
            {**changes, "updateId": update_id},
        )

    # --- reference --------------------------------------------------------------

    async def scheme_products(self, **params: Any) -> list[dict[str, Any]]:
        """Pension scheme products (``GET /schemeProduct/{firm}``).

        `schemeProductId` is required to open a PENSION account and is not derivable; filter
        by `schemeName` (e.g. ``Seccl Personal Pension``) or list them all. Third-party
        pensions are in the same collection.
        """
        return await self.get_records(_paths.scoped("schemeProduct", self.firm_id), params or None)

    async def firm_nodes(self) -> list[dict[str, Any]]:
        """The firm's node tree (``GET /firm/node/list/{firm}``).

        A node is Seccl's org unit and it is what `platform.orgs` mirrors for this provider.
        Every client and account is created against one, and the node decides which models and
        fee structures are available.
        """
        return await self.get_records(_paths.scoped("firm/node/list", self.firm_id))

    async def nationalities(self) -> list[dict[str, Any]]:
        """The nationality code list (``GET /static/nationality``).

        Needed because a client's `nationalities[]` carries per-country IDENTIFIERS (NINO,
        passport, CONCAT) and which identifier a country requires is not guessable.
        """
        return await self.get_records("/static/nationality")
