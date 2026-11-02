"""Cash out and cash between accounts — disbursements, transfers, recipient profiles.

Cash IN is :mod:`sei.endpoints.cash_receipts` (the same service handles a one-off, an
anticipated receipt and a standing order, by which `processingScheduleDetails` branch it
carries).

**A memo movement does not need a profile.** SEI's own Select example of a withdrawal is a
``MemoDisbursement``: no ``paymentType``, no pay-to. A memo receipt likewise has no
``payerInfo``. A *rail* payment is the one that needs a profile first — a BACS disbursement
names ``payToDetails.payToProfileId`` from :meth:`CashApi.create_pay_to_recipient`, and a
Direct Debit receipt names ``payerInfo.payerId`` from :meth:`CashApi.create_payer_recipient`.
``adhocPayToDetails`` is the alternative to a stored pay-to and leaves nothing reusable.

**Element order is part of the contract.** SEI converts this JSON to XML and validates it
against a sequence, so a field that is valid but sitting in the wrong place comes back as a
SAX parse error naming the next element it expected.

**Every method here moves real money**, which is why none of them guesses. The payment rail
(`paymentType.bacs`/`chaps`/`fasterPayment`/…) each has its own mandatory sub-block and the
amount is a one-of (`netAmount` / `grossValue` UK / `grossDistribution` US /
`disbursementPercent` / `unknown`); a body assembled by the caller is the honest interface,
and the recorder keeps what was sent.
"""

from __future__ import annotations

from typing import Any

from sei._base import BaseClient
from sei.endpoints._shapes import attributes as _attributes


def disbursement_body(
    firm_id: int | str,
    disbursement: dict[str, Any],
    *,
    used_by: str = "Graphene",
    memo: bool = False,
) -> dict[str, Any]:
    """The create-disbursement envelope.

    ``memo`` selects ``MemoDisbursement``, which is the shape SEI's Select example uses and
    which does not carry a payment rail. A rail payment stays ``CurrencyDisbursement`` and
    keeps ``usedBy``. Key order follows the working example: the disbursement block, then
    ``firmId``.
    """
    request: dict[str, Any] = {}
    if not memo:
        request["usedBy"] = used_by
    request["MemoDisbursement" if memo else "CurrencyDisbursement"] = disbursement
    request["firmId"] = firm_id
    return {"createCashDisbursement": {"CreateCashDisbursementRequest": request}}


class CashApi(BaseClient):
    """Disbursements, cash transfers and the recipient profiles they need."""

    # --- money out --------------------------------------------------------------

    async def create_disbursement(
        self,
        firm_id: int | str,
        disbursement: dict[str, Any],
        *,
        used_by: str = "Graphene",
        memo: bool = False,
    ) -> dict[str, Any]:
        """Pay cash away from an account
        (``POST /currencydisbursementservices/createcashdisbursement``).

        ``memo=True`` sends a ``MemoDisbursement``. That is a ledger posting, not a payment
        to a bank, and it does not need a pay-to profile. The block still needs
        ``accountNumber``, ``portfolioId``, ``descriptionTypeCode``, ``treatmentTypeCode``,
        ``currencyCode`` and one ``amountType.*``. ``beneficiaryValueDate`` must not be in
        the future; SEI rejects one (2085).

        ``memo=False`` (the default) sends a ``CurrencyDisbursement``. For a UK BACS
        withdrawal the mandatory set is ``paymentType.bacs.bacsPaymentMethod`` (5) with
        ``bacsDetails.{destinationStatementText, originationStatementText}``, and either
        ``payToDetails.payToProfileId`` or a full ``adhocPayToDetails``. Create the profile
        first. Leave ``beneficiaryValueDate`` off this block: the live sequence does not
        accept it where the memo block does, and a misplaced one is a SAX error.

        A **recurring** withdrawal is the same call with `processingScheduleDetails.recurring`
        instead of `.once`; `processingDateOptions.raiseCashMethodOptions` is SEI's
        auto-disinvest, and `userSpecifiedLimitAmt` caps the total a standing instruction may
        ever pay out — worth setting, because nothing else bounds it.

        Returns `currencyDisbursementId`, which is the activity id
        :meth:`sei.SeiClient.activity` takes.
        """
        body = await self.request(
            "POST",
            "/currencydisbursementservices/createcashdisbursement",
            json=disbursement_body(firm_id, disbursement, used_by=used_by, memo=memo),
        )
        return _attributes(body)

    async def modify_disbursement(
        self, firm_id: int | str, disbursement: dict[str, Any], *, used_by: str = "Graphene"
    ) -> dict[str, Any]:
        """Amend a pending disbursement
        (``POST /currencydisbursementservices/modifycashdisbursement``).

        Only while it is still pending — once the activity reaches state 22 (*Sent to Payment
        Processing System*) the instruction has left SEI and this is refused. Read the state
        first rather than discovering it from the error.
        """
        body = await self.request(
            "POST",
            "/currencydisbursementservices/modifycashdisbursement",
            json={
                "modifyCashDisbursement": {
                    "ModifyCashDisbursementRequest": {
                        "usedBy": used_by,
                        "CurrencyDisbursement": disbursement,
                        "firmId": firm_id,
                    }
                }
            },
        )
        return _attributes(body)

    # --- money between accounts -------------------------------------------------

    async def create_transfer(self, request: dict[str, Any]) -> dict[str, Any]:
        """Move cash between two accounts
        (``POST /currencytransferservices/currencytransfercreate``).

        ``request`` is `CreateCurrencyTransferRequest` — a `CurrencyTransferFrom` (debit
        account, portfolio, description/treatment codes, amount) and a `CurrencyTransferTo`
        (credit account, portfolio, its own description/treatment codes). **Both sides need
        their own code pair**: the same movement is a different kind of transaction at each
        end, and sending one pair for both is how a transfer books as the wrong type on the
        receiving account.

        Set `externalSystemTxnId` from our own reference; it is the only duplicate handle.
        """
        body = await self.request(
            "POST",
            "/currencytransferservices/currencytransfercreate",
            json={"CreateCurrencyTransferRequest": request},
        )
        return _attributes(body)

    async def modify_transfer(self, request: dict[str, Any]) -> dict[str, Any]:
        """Amend a pending cash transfer
        (``POST /currencytransferservices/currencytransfermodify``)."""
        body = await self.request(
            "POST",
            "/currencytransferservices/currencytransfermodify",
            json={"ModifyCurrencyTransferRequest": request},
        )
        return _attributes(body)

    async def delete_transfer(self, request: dict[str, Any]) -> dict[str, Any]:
        """Cancel a pending cash transfer
        (``POST /currencytransferservices/currencytransferdelete``)."""
        body = await self.request(
            "POST",
            "/currencytransferservices/currencytransferdelete",
            json={"DeleteCurrencyTransferRequest": request},
        )
        return _attributes(body)

    # --- recipients -------------------------------------------------------------

    async def allowable_currency_recipients(
        self, firm_id: int | str, account_number: str
    ) -> dict[str, Any]:
        """Which recipients this account may pay, per currency (``POST
        /currencyrecipientforaccountservicesv2service/retrieveallowablecurrencyrecipients``).

        The read to make before a withdrawal: a recipient profile that exists is not
        necessarily permitted for the currency being sent.
        """
        body = await self.request(
            "POST",
            "/currencyrecipientforaccountservicesv2service/retrieveallowablecurrencyrecipients",
            json={"accountNumber": account_number, "firmId": str(firm_id)},
        )
        return _attributes(body)

    async def create_pay_to_recipient(self, request: dict[str, Any]) -> dict[str, Any]:
        """Register somewhere the account may pay (``POST
        /currencyrecipientforaccountservicesv2service/createpaytorecipientforaccount2``).

        Returns `profileId` — record it against the client's bank details, because it is what
        every later disbursement names and it is not derivable from the sort code and account
        number. `newPartyIndicator` decides whether SEI creates a party for the recipient or
        attaches to an existing one.

        **A pay-to recipient is a standing permission to send money out of a client's
        account.** It is the most sensitive write in this package; the vendor applies its own
        AML screening to the payee and may return an approval activity rather than a live
        profile.
        """
        body = await self.request(
            "POST",
            "/currencyrecipientforaccountservicesv2service/createpaytorecipientforaccount2",
            json={"createPayToRecipientForAccount2": {"CreatePayToRecipientRequest2": request}},
        )
        return _attributes(body)

    async def remove_pay_to_recipient(self, request: dict[str, Any]) -> dict[str, Any]:
        """Revoke one (``POST /…/removepaytorecipientforaccount``)."""
        body = await self.request(
            "POST",
            "/currencyrecipientforaccountservicesv2service/removepaytorecipientforaccount",
            json=request,
        )
        return _attributes(body)

    async def create_payer_recipient(self, request: dict[str, Any]) -> dict[str, Any]:
        """Register a PAYER — where money may be collected FROM (``POST
        /currencyrecipientforaccountservicesv2service/createpayerrecipientforaccount``).

        This is the prerequisite for a BACS Direct Debit: a cash receipt with
        `receiptInstructions.payerInfo.payerId` collects, where the same call without it
        merely anticipates. `docs/contributions.md` lists Direct Debit as unbuilt for exactly
        this reason — the payer profile is data we do not hold.
        """
        body = await self.request(
            "POST",
            "/currencyrecipientforaccountservicesv2service/createpayerrecipientforaccount",
            json=request,
        )
        return _attributes(body)

    async def remove_payer_recipient(self, request: dict[str, Any]) -> dict[str, Any]:
        """Revoke a payer (``POST /…/removepayerrecipientforaccount``)."""
        body = await self.request(
            "POST",
            "/currencyrecipientforaccountservicesv2service/removepayerrecipientforaccount",
            json=request,
        )
        return _attributes(body)
