"""THE MONEY RAIL — payments in, withdrawals, transfers, fees and trades.

**One endpoint does all of it.** `POST /portfoliotransactiongroup` is how a contribution, a
withdrawal, a transfer in or out, an ad-hoc fee and a buy or sell order are each instructed;
what distinguishes them is the `transactionType` / `transactionSubType` / `movementType` triple
inside `transactions[]`. That is worth stating plainly because it has one consequence that
governs every caller: **a retried group is a second instruction.** There is no idempotency on
the group itself — Seccl offers an idempotency key as a separate mechanism (the collection's
*Getting started → Idempotency*), and `externalReference` is the field to carry ours.

The combinations this integration uses:

======================  ==================  =============  ==========================
`transactionType`       `transactionSubType` `movementType` what it is
======================  ==================  =============  ==========================
``Payment``             ``Deposit``         ``In``         a contribution / payment in
``Payment``             ``Employer``        ``In``         an employer pension contribution
``Payment``             ``Withdrawal``      ``Out``        a withdrawal
``Payment``             ``Transfer``        ``Out``        the cash leg of a transfer out
``Product Transfer``    ``Cash``/``In Specie`` ``In``/``Out`` a transfer
``Cash``                ``Adviser Adhoc Fee`` ``Out``      an ad-hoc adviser fee
``Cash``                ``Platform Fee``    ``Out``        a platform fee posting
======================  ==================  =============  ==========================

``autoDisinvest`` (``Proportional``/``Largest``) makes a withdrawal sell to fund itself;
``autoInvest`` makes a payment in invest on arrival. A *Raise Trade* — sell by AMOUNT rather
than by quantity — is a single `POST /portfoliotransaction`, not a group.
"""

from __future__ import annotations

from typing import Any

from seccl._base import BaseClient
from seccl.endpoints import _paths


class TransactionsApi(BaseClient):
    """Instruct and follow money movements."""

    # --- instruct ---------------------------------------------------------------

    def transaction_group_body(
        self, account_id: str, transactions: list[dict[str, Any]], **extra: Any
    ) -> dict[str, Any]:
        """The JSON ``POST /portfoliotransactiongroup`` sends.

        Every transaction needs its own `firmId` and `accountId` as well as the group's —
        Seccl's own redundancy, and omitting them on a member is refused rather than defaulted.
        """
        return {
            "firmId": self.firm_id,
            "accountId": account_id,
            "transactions": [
                {"firmId": self.firm_id, "accountId": account_id, **transaction}
                for transaction in transactions
            ],
            **extra,
        }

    async def create_transaction_group(
        self, account_id: str, transactions: list[dict[str, Any]], **extra: Any
    ) -> dict[str, Any]:
        """Instruct a group of transactions (``POST /portfoliotransactiongroup``).

        **This moves money.** Nothing about a group is idempotent; carry an
        `externalReference` so a duplicate can at least be recognised afterwards.
        """
        return await self.write(
            "POST",
            _paths.collection("portfoliotransactiongroup"),
            self.transaction_group_body(account_id, transactions, **extra),
        )

    async def create_transaction(self, body: dict[str, Any]) -> dict[str, Any]:
        """Instruct ONE transaction (``POST /portfoliotransaction``).

        The single-transaction form, and the only way to raise a *Raise Trade* — a sell
        specified by amount rather than by quantity, which is what a withdrawal from an
        invested account actually needs.
        """
        return await self.write(
            "POST", _paths.collection("portfoliotransaction"), {"firmId": self.firm_id, **body}
        )

    async def rebalance(self, **target: Any) -> dict[str, Any]:
        """Instruct a rebalance (``POST /event/``).

        ``accountId`` rebalances one account; ``modelId`` rebalances every account on a
        discretionary model. The second is a bulk trading instruction across a book — worth
        naming, because the call looks identical to the first.
        """
        return await self.write(
            "POST", "/event/", {"firmId": self.firm_id, "eventType": "Rebalance", **target}
        )

    # --- follow -----------------------------------------------------------------

    async def transaction(self, transaction_id: str) -> dict[str, Any]:
        """One transaction (``GET /portfoliotransaction/{firm}/{id}``)."""
        return await self.get_record(
            _paths.record("portfoliotransaction", self.firm_id, transaction_id)
        )

    async def transactions(self, **params: Any) -> list[dict[str, Any]]:
        """Transactions, filtered (``GET /portfoliotransaction/{firm}``).

        `linkId` is the group id and is how you find what a group actually created — a group
        that instructed "sell and withdraw" becomes several transactions, and only the group id
        ties them together. `status`, `transactionType` and `transactionSubType` are the other
        useful filters.
        """
        return await self.get_records(
            _paths.scoped("portfoliotransaction", self.firm_id), params or None
        )

    async def transaction_group(self, link_id: str) -> list[dict[str, Any]]:
        """Every transaction one group created (``GET …?linkId=…``) — the read to make after
        instructing, and the only way to see whether the auto-disinvest leg was raised."""
        return await self.transactions(linkId=link_id)

    async def product_transfers(self, **params: Any) -> list[dict[str, Any]]:
        """The transfers workbench (``GET /portfoliotransaction/producttransfer/{firm}``)."""
        return await self.get_records(
            _paths.scoped("portfoliotransaction/producttransfer", self.firm_id), params or None
        )

    # --- amend / cancel ---------------------------------------------------------

    async def request_deletion(self, transaction_id: str) -> dict[str, Any]:
        """Ask for a pending transaction to be deleted
        (``PUT /portfoliotransaction/action/requestdeletion/{firm}/{id}``).

        A *request*, not a delete: Seccl decides whether the instruction has gone too far. So
        a 200 here is not proof the money will not move — re-read the transaction.
        """
        return await self.write(
            "PUT",
            _paths.record(
                "portfoliotransaction/action/requestdeletion", self.firm_id, transaction_id
            ),
            {},
        )

    async def request_transfer_cancellation(self, transaction_id: str) -> dict[str, Any]:
        """Ask for a transfer in to be cancelled
        (``PUT /portfoliotransaction/action/requestCancellation/{firm}/{id}``).

        Note the capital ``C`` — the sibling deletion path is all lower case. Seccl is
        inconsistent here and both spellings 404 in the other's place.
        """
        return await self.write(
            "PUT",
            _paths.record(
                "portfoliotransaction/action/requestCancellation", self.firm_id, transaction_id
            ),
            {},
        )

    async def set_transfer_book_value(
        self, transaction_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Set a stock transfer's book cost
        (``PUT /portfoliotransaction/action/bookvalue/{firm}/{id}``).

        Required for CGT to be right on transferred-in holdings: without a book cost the whole
        transfer value reads as gain. There is a bulk form (``POST /upload/bookvalue/{firm}``)
        for a transfer with many lines.
        """
        return await self.write(
            "PUT",
            _paths.record("portfoliotransaction/action/bookvalue", self.firm_id, transaction_id),
            body,
        )

    async def add_transfer_update_event(
        self, transaction_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Record a chasing event against a transfer
        (``PUT /portfoliotransaction/action/firmTransferHistoryUpdate/{firm}/{id}``)."""
        return await self.write(
            "PUT",
            _paths.record(
                "portfoliotransaction/action/firmTransferHistoryUpdate",
                self.firm_id,
                transaction_id,
            ),
            body,
        )

    # --- transfer counterparties ------------------------------------------------

    async def ceding_providers(self, **params: Any) -> list[dict[str, Any]]:
        """Providers a transfer IN may come from (``GET /masterparty/platform/{firm}``).

        The `masterparty` id this returns is what `previousProvider` takes, and it is the value
        `platform.transfer_providers.masterparty_id` exists to hold. Filters worth knowing:
        `supportsPayUk`, `supportsManuals`, `transferWrapperType` + `cedingCashSupported`.
        """
        return await self.get_records(
            _paths.scoped("masterparty/platform", self.firm_id), params or None
        )

    async def transfer_out_providers(self, **params: Any) -> list[dict[str, Any]]:
        """Providers a transfer OUT may go to
        (``GET /masterparty/transferoutprovider/{firm}``) — a different and smaller
        population than the ceding list, so it has to be read separately."""
        return await self.get_records(
            _paths.scoped("masterparty/transferoutprovider", self.firm_id), params or None
        )

    async def transaction_codes(self) -> list[dict[str, Any]]:
        """Seccl's transaction-code vocabulary (``GET /static/transactiontype``).

        Needed to read `posting/cash` — a fee reconciliation filters on a code (``PFOF``…) and
        the codes are not documented anywhere else.
        """
        return await self.get_records("/static/transactiontype")
