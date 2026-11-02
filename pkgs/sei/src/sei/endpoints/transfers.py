"""Custody transfers — assets and cash arriving from, or leaving to, another provider.

SEI has no "transfer" entity in the sense Morgan Lloyd does. A transfer here is a **custody
movement**: a *free receipt* (assets in, no payment against them) or a *free delivery* (assets
out), optionally preceded by liquidation, plus a settlement leg. That is why
`docs/contributions.md` records a transfer as completing at OUR reconciliation rather than at
a SEI status — SEI tells you the movement settled, not that the transfer is done.

Two families, and the difference is which side initiates:

* ``accountCustodyTransfer/transferIn`` / ``transferOut`` — the CLIENT-facing request, with
  the ceding provider's account details, the signing authority and the supporting documents.
  This is the one an adviser raises.
* ``freereceipt`` / ``freedelivery`` — the back-office movement, per asset, with an
  `async` variant and a status read. This is what settles.

`transferTypeId` on the way in is 1 Free Receipt-Encash (sell at the ceding provider, send
cash) or 2 Free Receipt-In Specie (send the holdings); `assetTransferInstructionId` is
1 Full Transfer / 2 Partial / 3 Full Liquidation / 4 Partial Liquidation.
"""

from __future__ import annotations

from typing import Any

from sei._base import BaseClient
from sei.endpoints._shapes import attributes as _attributes
from sei.endpoints._shapes import rows as _rows


class TransfersApi(BaseClient):
    """Raise a transfer in or out; follow and settle the custody movements."""

    # --- the client-facing request ----------------------------------------------

    async def transfer_in(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Request assets in from a ceding provider
        (``POST /accountcustodytransferservice/accountCustodyTransfer/transferIn``).

        Mandatory: `platformUser`, `account.accountNumber`,
        `externalAccount.{externalAccountName, externalAccountNumber, externalAccountType}`,
        `primarySigningAuthority.{externalPrimaryTaxInfo, primarySigningAuthorityName}`.
        `assetDetails` is not sent. A transfer-in on the i31 test environment was accepted
        with that array omitted.

        `documents[]` carries the transfer forms by `documentReferenceId` — upload them first
        through the document service; a transfer request with no forms is refused by the
        ceding provider rather than by SEI, which is a much slower failure.
        """
        return _attributes(
            await self.request(
                "POST",
                "/accountcustodytransferservice/accountCustodyTransfer/transferIn",
                json={"data": {"attributes": attributes}},
            )
        )

    async def transfer_out(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Request assets out to a receiving provider
        (``POST /accountcustodytransferoutservice/accountCustodyTransfer/transferOut``).

        Carries a `gift` block (1 No / 2 Yes-Anonymous / 3 Yes-Named) because a transfer out
        to a third party may be a gift with its own tax treatment — which is a question the
        adviser has to have asked before this call, not after.
        """
        return _attributes(
            await self.request(
                "POST",
                "/accountcustodytransferoutservice/accountCustodyTransfer/transferOut",
                json={"data": {"attributes": attributes}},
            )
        )

    async def pending_transfers(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        """Where each in-flight transfer has got to
        (``POST /pendingtransfersstatusservice/getPendingTransfersStatus``).

        The chasing list. Ours is the authoritative lifecycle
        (`platform.transfers.status`, monotonic per 0056), so this is read to advance it — not
        to replace it.
        """
        return _rows(
            await self.request(
                "POST", "/pendingtransfersstatusservice/getPendingTransfersStatus", json=request
            )
        )

    async def amend_custody_transfer(
        self, activity_id: int | str, attributes: dict[str, Any]
    ) -> dict[str, Any]:
        """Amend an in-flight custody transfer
        (``PATCH /transferscustodytransferservice/custodytransfer/{activityId}``)."""
        return _attributes(
            await self.request(
                "PATCH",
                f"/transferscustodytransferservice/custodytransfer/{activity_id}",
                json={"data": {"attributes": attributes}},
            )
        )

    # --- the back-office movement -----------------------------------------------

    async def free_receipt(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Book assets in (``POST /transfersfreereceiptservice/freereceipt``)."""
        return _attributes(
            await self.request(
                "POST",
                "/transfersfreereceiptservice/freereceipt",
                json={"data": {"attributes": attributes}},
            )
        )

    async def free_receipt_status(
        self,
        firm_id: int | str,
        *,
        activity_id: int | str | None = None,
        account_number: str | None = None,
    ) -> list[dict[str, Any]]:
        """Free-receipt outcomes (``GET /transfersfreereceiptservice/freereceiptstatus``)."""
        params: dict[str, Any] = {"firmId": str(firm_id)}
        if activity_id is not None:
            params["activityId"] = str(activity_id)
        if account_number:
            params["accountNumber"] = account_number
        # `key=`: `exceptionDetails` follows `activityDetail`, so a FAILED custody transfer
        # would be returned as a status row — at HTTP 200, with `status: "FAILURE"` discarded.
        return _rows(
            await self.request(
                "GET", "/transfersfreereceiptservice/freereceiptstatus", params=params
            ),
            key="activityDetail",
        )

    async def free_delivery(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Book assets out (``POST /transfersfreedeliveryservice/freedelivery``)."""
        return _attributes(
            await self.request(
                "POST",
                "/transfersfreedeliveryservice/freedelivery",
                json={"data": {"attributes": attributes}},
            )
        )

    async def free_delivery_status(
        self,
        firm_id: int | str,
        *,
        activity_id: int | str | None = None,
        account_number: str | None = None,
    ) -> list[dict[str, Any]]:
        """Free-delivery outcomes (``GET /transfersfreedeliveryservice/freedeliverystatus``)."""
        params: dict[str, Any] = {"firmId": str(firm_id)}
        if activity_id is not None:
            params["activityId"] = str(activity_id)
        if account_number:
            params["accountNumber"] = account_number
        return _rows(
            await self.request(
                "GET", "/transfersfreedeliveryservice/freedeliverystatus", params=params
            )
        )
