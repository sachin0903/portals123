"""Account reads and profile maintenance.

Two reads, and the difference matters when you are testing: `retrieveAccountDetails` answers
the account, its owners and its **pay-to / pay-for / payer recipients with full bank details**
— which is where a disbursement's `payToProfileId` comes from — while
`getAccountDetailsinfo` ("Retrieve Account Characteristics") answers the servicing and
commercial view: **DSR tags**, **fee packages** (`feePackageProfileId`), account groups, goals,
beneficiaries and optional fields.

Note the casing of that second path. SEI's own Swagger spells it
``getAccountDetailsinfo`` — lower-case ``i`` — while the per-firm API sheet spells it
``getAccountDetailsInfo``. The spec is right; the sheet's spelling 404s.
"""

from __future__ import annotations

from typing import Any

from sei._base import BaseClient
from sei.endpoints._shapes import attributes as _attributes


class AccountsApi(BaseClient):
    """Read an account; maintain its names, status, DSR tags and currencies."""

    # --- reads ------------------------------------------------------------------

    async def account_details(
        self,
        firm_id: int | str,
        account_number: str,
        *,
        include_relationships: bool = True,
        include_pay_to: bool = True,
        include_pay_for: bool = True,
        include_payers: bool = True,
    ) -> dict[str, Any]:
        """The account, its owners and its money-movement recipients
        (``POST /retrieveaccountdetailsservice/retrieveAccountDetails``).

        The four includes default on because the recipients are the point: `listOfpayToRecipients[]`
        carries each recipient's `profileId` and `bankInfo`, and a disbursement needs that
        `profileId` unless it is going to carry a whole ad-hoc bank block instead.
        """
        body = await self.request(
            "POST",
            "/retrieveaccountdetailsservice/retrieveAccountDetails",
            json={
                "data": {
                    "type": "accountDetailsRequest",
                    "attributes": {
                        "firmId": firm_id,
                        "accountNumber": account_number,
                        "includeRelationships": str(include_relationships).lower(),
                        "includePayToRecipients": str(include_pay_to).lower(),
                        "includePayForRecipients": str(include_pay_for).lower(),
                        "includePayers": str(include_payers).lower(),
                    },
                }
            },
        )
        return _attributes(body)

    async def account_characteristics(
        self, firm_id: int | str, account_number: str
    ) -> dict[str, Any]:
        """DSR tags, fee packages, groups and goals
        (``POST /getaccountdetailswithdsrfeegoalservice/getAccountDetailsinfo``).

        `accountFeeInfoDetails.accountFeePackageInfoList[]` is where `feePackageProfileId`
        comes from, and `accountDsrDetails[]` is the account's live servicing grants — the
        two things a new account has to be given and this is how you check they were.
        """
        body = await self.request(
            "POST",
            "/getaccountdetailswithdsrfeegoalservice/getAccountDetailsinfo",
            json={
                "data": {
                    "type": "AccountDetailsInfoRequest",
                    "attributes": {"firmId": firm_id, "accountNumber": account_number},
                }
            },
        )
        return _attributes(body)

    # --- writes -----------------------------------------------------------------

    async def update_account_details(
        self, firm_id: int | str, account_number: str, update: dict[str, Any]
    ) -> dict[str, Any]:
        """Rename an account
        (``POST /accountbasicdetail/updateAccountBasicDetails``).

        ``update`` is `accountBasicUpdateDetails` — `accountDisplayName` and/or
        `accountRegistrationName`. The display name is what a client sees; the registration
        name is what appears on a contract note.
        """
        body = await self.request(
            "POST",
            "/accountbasicdetail/updateAccountBasicDetails",
            json={
                "data": {
                    "type": "accountBasicUpdateRequest",
                    "attributes": {
                        "firmId": firm_id,
                        "accountNumber": account_number,
                        "accountBasicUpdateDetails": update,
                    },
                }
            },
        )
        return _attributes(body)

    async def change_account_status(
        self, firm_id: int | str, account_number: str
    ) -> dict[str, Any]:
        """Advance the account's status
        (``POST /swpaccountstatuschangeservice/accountStatusChange``).

        **It takes no target status.** SEI advances the account one step along its own
        lifecycle (`ACCT_STATUS`: 1 Pre-Open → 3 Open → 5 Pre-Close → 6 Closed) and the answer
        names where it landed in `currentStatus`. So this cannot be used to set a status, only
        to progress one, and the caller must read `currentStatus` rather than assume.
        """
        body = await self.request(
            "POST",
            "/swpaccountstatuschangeservice/accountStatusChange",
            json={
                "data": {
                    "type": "accountStatusChangeRequest",
                    "attributes": {"firmId": firm_id, "accountNumber": account_number},
                }
            },
        )
        return _attributes(body)

    async def update_dsr_tags(
        self,
        firm_id: int | str,
        dsr_details: list[dict[str, Any]],
        *,
        account_number: str | None = None,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        """Set servicing relationships (``POST /dsrdetailsupdateservice/dsrDetails/update``).

        ``dsr_details`` is a list of ``{"dsrTagName": …, "employeeId": …}`` — **by tag NAME**
        ("Adviser", "Investment Manager"), which is the one place SEI takes the name rather
        than the `servicingRelationshipId` the enrolment body wants. The names come from
        `ReferenceApi.firm_dsrs`, and they are per firm: 3102/3106 say "IFA Firm" where
        3110/3114 say "Adviser Firm".
        """
        search: dict[str, Any] = {}
        if account_number:
            search["accountNumber"] = account_number
        if client_id:
            search["clientId"] = client_id
        body = await self.request(
            "POST",
            "/dsrdetailsupdateservice/dsrDetails/update",
            json={
                "data": {
                    "attributes": {
                        "searchCriteria": search,
                        "dsrDetails": dsr_details,
                        "firmId": str(firm_id),
                    }
                }
            },
        )
        return _attributes(body)

    async def add_allowable_currencies(
        self,
        firm_id: int | str,
        account_number: str,
        currencies: list[str],
        *,
        fx_order_processing: str | None = None,
        preferred_settlement_currency: str | None = None,
    ) -> dict[str, Any]:
        """Permit further currencies on the account
        (``POST /addallowablecurrencyservice/allowableCurrencies/addAllowableCurrencyToAccount``).

        Required before any cash movement in a currency other than the account's base — a
        receipt in an unpermitted currency is refused with error 1016, which reads as a bad
        currency code rather than a missing permission.

        SEI rejects the call unless it also carries one of ``fXOrderProcessing``,
        ``preferredSettlementCurrency`` or ``allowTransactionLevelOverride`` (error 2196).
        The override flag is the one that does not change how the account settles or
        creates FX orders. The published example sends it as the string ``"true"``.
        ``intRateSchedule`` is left off: a schedule id this firm has not configured is
        error 2196, and none means no interest schedule.
        """
        attributes: dict[str, Any] = {
            "accountNumber": account_number,
            "allowTransactionLevelOverride": "true",
            "allowableCurrenciesList": [{"isoCurrencyCode": code} for code in currencies],
            "firmId": str(firm_id),
        }
        if fx_order_processing:
            attributes["fXOrderProcessing"] = fx_order_processing
        if preferred_settlement_currency:
            attributes["preferredSettlementCurrency"] = preferred_settlement_currency
        body = await self.request(
            "POST",
            "/addallowablecurrencyservice/allowableCurrencies/addAllowableCurrencyToAccount",
            json={"data": {"type": "string", "attributes": attributes}},
        )
        return _attributes(body)

    async def add_portfolio(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Add a principal portfolio to an existing account
        (``POST /addportfolioservice/v1/addPortfolioToAccount``)."""
        return _attributes(
            await self.request(
                "POST",
                "/addportfolioservice/v1/addPortfolioToAccount",
                json={"data": {"attributes": attributes}},
            )
        )

    async def add_income_portfolio(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Add an INCOME portfolio (``POST
        /addincomeportfoliotoaccount/v1/addIncomePortfolioToAccount``).

        Separate from :meth:`add_portfolio` at the vendor because the two portfolio types
        (`PORTFOLIO_TYPE` 189 Capital / 190 Income) mean different things to SEI's accounting:
        income is where distributions land when they are not reinvested.
        """
        return _attributes(
            await self.request(
                "POST",
                "/addincomeportfoliotoaccount/v1/addIncomePortfolioToAccount",
                json={"data": {"attributes": attributes}},
            )
        )

    async def update_isa_subscription(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Change ISA subscription status / auto-renewal (``POST
        /updateisaaccountservice/account/updateAccountSubscription``)."""
        return _attributes(
            await self.request(
                "POST",
                "/updateisaaccountservice/account/updateAccountSubscription",
                json={"data": {"attributes": attributes}},
            )
        )
