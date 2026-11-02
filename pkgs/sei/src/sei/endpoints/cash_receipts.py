"""Cash-receipt endpoints (``/currencyreceiptservices``).

SEI's own summary of the service: "API to initiate a cash receipt with receipt types of ACR
(Anticipated Cash Receipt), Direct Payment (US only), Direct Debit (UK only), and memo post."

**On what makes a receipt an ACR.** There is no ``receiptType = ACR`` field. An anticipated
receipt is the shape you get by scheduling a receipt for a future ``dueDate`` and supplying no
``payerInfo`` — nothing is collected, SEI simply expects the money by that date. Direct Debit
is the same call *with* a payer profile, which is why that path needs data we do not hold.
The description/treatment code pair is what actually classifies the movement at SEI's end.

**On the host.** The spec declares its server as
``https://extranettest.api.seic.com/api/v1/swp/currencyreceiptservices`` — the extranet
gateway. We reach SWP over the **mTLS** gateway instead
(``https://test.mtls.api.seic.com/api/v1/swp``, ``ENVIRONMENTS`` in ``sei.settings``), which is
the one our client certificate is issued for and the one the monitoring probes authenticate
against. Same service path, different edge.

Only ``createCurrencyReceipt`` is published on this service — there is no read-back endpoint
for a currency receipt anywhere in the spec set, so a created receipt is followed through the
ACTIVITY (:mod:`sei.endpoints.activities`) and reconciled through the transaction feed, not by
querying SEI for the receipt itself.

**One call, three products.** Which of these you have created is decided entirely by the
payload, and the differences are easy to conflate:

* *anticipated / memo receipt* — `processingScheduleDetails.once` with a `dueDate`, no
  `payerInfo`, and (for the memo Select actually accepts) `memoFlag` true with receipt
  method cheque (`3`). Nothing is collected. The request `data.type` is
  `CurrencyReceiptRequest`; `CreateCurrencyReceipt` is the response type. Set
  `once.beneficiaryValueDate` as well: omitted, SEI defaults it to the US processing day,
  which can fall before an account that opened the same UK day (error 2086). A future
  value date is rejected on a disbursement (2085), so on the open day the date is that day.
* *direct debit* — the same, WITH `payerInfo.payerId`. Now SEI collects. The payer profile is
  created by `CashApi.create_payer_recipient` and is data we do not currently hold, which is
  why `docs/contributions.md` lists BACS Direct Debit as unbuilt.
* *standing order* — `processingScheduleDetails.recurring` instead of `.once`. The recurrence
  lives on the resulting ACTIVITY, so its `activityId` must be recorded: cancelling that
  activity is the only way to stop the mandate.
"""

from __future__ import annotations

from typing import Any

from sei._base import BaseClient
from sei.endpoints._shapes import attributes as _attributes
from sei.models import Attributes, CurrencyReceiptRequest, ReceiptResult

SERVICE = "/currencyreceiptservices"


class CashReceiptsApi(BaseClient):
    """Create and amend cash receipts."""

    async def create_currency_receipt(self, attributes: Attributes) -> ReceiptResult:
        """Initiate one cash receipt (``POST /createCurrencyReceipt``).

        Returns the created activity (``activityId`` is SEI's handle for it, and the value to
        record against the local expectation). A 200 may still carry ``warnings`` — an
        approval requirement, or an instruction SEI accepted but ignored — so the caller
        should surface them rather than treat 200 as unqualified success.
        """
        body = await self.request(
            "POST",
            f"{SERVICE}/createCurrencyReceipt",
            json=CurrencyReceiptRequest.single(attributes).to_payload(),
        )
        return ReceiptResult.from_body(body)

    async def modify_cash_receipt(
        self,
        firm_id: int | str,
        account_number: str,
        activity_id: int | str,
        *,
        notes: str | None = None,
        effective_date: str | None = None,
        prevent_street_side_msg: int | None = None,
        fee_override: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Amend a lodged receipt (``POST /modifycashreceipteventservice/cashReceipt/modify``).

        A different service from the create. Notes, the effective date, the street-side
        suppression flag, and a ``feeOverride`` list. It cannot change the amount.

        ``effective_date`` is ``MM/DD/YYYY`` on this service — not the ISO form the create
        takes. Two conventions on the same object is exactly the kind of thing that fails only
        against the live vendor, so it is passed through as the caller's string rather than
        formatted here.

        **The answer carries a NEW `activityId`**: SEI supersedes the activity rather than
        editing it, so whatever we recorded against the expectation is now stale and must be
        replaced with what comes back.
        """
        payload: dict[str, Any] = {
            "firmId": str(firm_id),
            "accountNumber": account_number,
            "activityId": str(activity_id),
        }
        if notes is not None:
            payload["notes"] = notes
        if effective_date is not None:
            payload["effectiveDate"] = effective_date
        if prevent_street_side_msg is not None:
            payload["preventStreetSideMsg"] = prevent_street_side_msg
        if fee_override:
            payload["feeOverride"] = fee_override
        body = await self.request(
            "POST",
            "/modifycashreceipteventservice/cashReceipt/modify",
            json={"data": {"type": "ModifyCashReceiptRequest", "attributes": payload}},
        )
        return _attributes(body)
