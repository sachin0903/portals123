"""Fees — reading a firm's packages and assigning them to accounts and groups.

**The reads are largely redundant and the writes are not.** The nightly drop already carries
the whole fee register — `raw.fee_package_rule` (rate, basis, rate type, VAT, qualifiers,
`FEE_PACKAGE_PROFILE_ID`), `raw.fee_package_usage` (per-account assignment, discounts,
waivers, collection schedule), `raw.fee_group`, `raw.fee_computation` — so nothing here should
be used to populate `platform.fee_packages`. What the drop cannot do is *change* an
assignment, which is what this module is for.

`feePackageProfileId` is the id that ties the two together: it is on
`raw.fee_package_rule`/`raw.fee_package_usage` in the drop, it comes back from
`AccountsApi.account_characteristics`, and it is what
:meth:`FeesApi.assign_package_to_account` takes. It is the value
`platform.fee_packages.external_id` should hold for SEI.
"""

from __future__ import annotations

from typing import Any

from sei._base import BaseClient
from sei.endpoints._shapes import attributes as _attributes
from sei.endpoints._shapes import rows as _rows


class FeesApi(BaseClient):
    """Fee package reads, account/group assignment, and one-off charges."""

    # --- reads ------------------------------------------------------------------

    async def fee_packages(
        self,
        firm_id: int | str,
        *,
        account_number: str | None = None,
        fee_group_id: int | str | None = None,
    ) -> list[dict[str, Any]]:
        """Packages on an account or fee group
        (``GET /retrievefeepackagedetails/fee-package-details/retrieve``)."""
        params: dict[str, Any] = {"firmId": str(firm_id)}
        if account_number:
            params["accountNumber"] = account_number
        if fee_group_id is not None:
            params["feeGroupId"] = str(fee_group_id)
        # `key=`: `realTimeFeeDetails` sits beside the periodic ones, so a firm with no periodic
        # fees would get a DEALING charge where `platform.fee_packages.external_id` expects a
        # `feePackageProfileId`.
        return _rows(
            await self.request(
                "GET", "/retrievefeepackagedetails/fee-package-details/retrieve", params=params
            ),
            key="periodicFeesPkgDetails",
        )

    async def fee_package_rules(
        self, firm_id: int | str, fee_package_profile_id: int | str
    ) -> dict[str, Any]:
        """One package and its rules
        (``GET /periodicfeepackageruledetails/periodic/feePackageRuleDetails``).

        The rules are what `platform.fee_packages.rule` models: basis, rate type, rate, VAT,
        and the tier bands. SEI publishes bands the way Seccl does — the next band starting at
        the next penny — so anything importing these has to normalise them contiguous or
        `catalogue.validate_fee_rule` refuses the row as having a gap.
        """
        return _attributes(
            await self.request(
                "GET",
                "/periodicfeepackageruledetails/periodic/feePackageRuleDetails",
                params={
                    "firmId": str(firm_id),
                    "feePackageProfileId": str(fee_package_profile_id),
                },
            )
        )

    # --- assignment -------------------------------------------------------------

    async def assign_package_to_account(
        self,
        account_id: int | str,
        fee_package_profile_id: int | str,
        *,
        collection_portfolio_id: int | str | None = None,
        copy_package: bool = False,
        platform_user: str | None = None,
    ) -> dict[str, Any]:
        """Put an account on a periodic fee package
        (``POST /feepackageassignmentaccount/feePackageAssignmentAccount``).

        Takes SEI's numeric `accountId`, **not** the account NUMBER — the two are different
        values and the number is refused. `copyPackageFlag` = 1 tells SEI to assign a replica
        of the package rather than the package itself, which is how a per-account variation is
        made without editing the firm's shared one.

        `collectionPortfolioId` decides which portfolio the fee is taken from; omitted, SEI
        applies its own defaulting, which on a two-portfolio account is not obviously the one
        the firm meant.
        """
        attributes: dict[str, Any] = {
            "accountId": account_id,
            "feePackageProfileId": fee_package_profile_id,
            # 1 = assign a replica (per-account variation); 2 = assign the firm's shared package.
            # The spec's values are 1/2/blank (default 2); 0 is not one of them.
            "copyPackageFlag": 1 if copy_package else 2,
        }
        if collection_portfolio_id is not None:
            attributes["collectionPortfolioId"] = collection_portfolio_id
        if platform_user:
            attributes["platformUser"] = platform_user
        return _attributes(
            await self.request(
                "POST",
                "/feepackageassignmentaccount/feePackageAssignmentAccount",
                json=attributes,
            )
        )

    async def assign_package_to_group(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Put a fee GROUP on a package
        (``POST /feepackageassignmentgroup/feePackageAssignmentGroup``).

        A fee group aggregates several accounts so a tiered rate is computed on the household
        total rather than per account — which is materially cheaper for the client, and is why
        a firm asks for it.
        """
        return _attributes(
            await self.request(
                "POST", "/feepackageassignmentgroup/feePackageAssignmentGroup", json=attributes
            )
        )

    async def set_final_fee_date(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Stop a periodic fee from a date
        (``POST /feepackageassignmentfinalfeedate/finalFeeDateAPI``).

        This is how charging ends — there is no "unassign package" endpoint, so closing an
        account without setting a final fee date leaves the package computing.
        """
        return _attributes(
            await self.request(
                "POST", "/feepackageassignmentfinalfeedate/finalFeeDateAPI", json=attributes
            )
        )

    # --- fee groups -------------------------------------------------------------

    async def create_fee_group(
        self, firm_id: int | str, name: str, *, platform_user: str | None = None
    ) -> dict[str, Any]:
        """Create a fee computation group (``POST /createfeegroupservice/feegroup/create``)."""
        attributes: dict[str, Any] = {"feeGroupName": name, "firmId": firm_id}
        if platform_user:
            attributes["platformUser"] = platform_user
        return _attributes(
            await self.request(
                "POST",
                "/createfeegroupservice/feegroup/create",
                json={"data": {"type": "Create Fee Groups.", "attributes": attributes}},
            )
        )

    async def add_account_to_fee_group(
        self,
        fee_group_id: int | str,
        allocations: list[dict[str, Any]],
        *,
        firm_id: int | str | None = None,
    ) -> dict[str, Any]:
        """Add accounts to a group (``POST /addaccounttofeegroup/addAccountToFeeGroup``).

        ``allocations`` is `accountAllocationList[]` — each `accountNumber` with an optional
        `portfolioAllocationList[]` of `{portfolioId, allocationPercentage}`. Adding an account
        that is already in the group is a 400 (code 2196), not a no-op, so read the group
        first or treat that specific error as success.
        """
        attributes: dict[str, Any] = {
            "accountAllocationList": allocations,
            "feeGroupId": str(fee_group_id),
        }
        if firm_id is not None:
            attributes["firmId"] = str(firm_id)
        return _attributes(
            await self.request(
                "POST",
                "/addaccounttofeegroup/addAccountToFeeGroup",
                json={"data": {"attributes": attributes}},
            )
        )

    async def remove_account_from_fee_group(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Remove an account from a group
        (``POST /removeaccountfromfeegroupservice/removeAccountFromFeeGroup``)."""
        return _attributes(
            await self.request(
                "POST",
                "/removeaccountfromfeegroupservice/removeAccountFromFeeGroup",
                json={"data": {"attributes": attributes}},
            )
        )

    # --- one-off charges --------------------------------------------------------

    async def create_adhoc_fee(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Charge a one-off fee
        (``POST /initiateonetimefeesadhocservice/initateOneTimeFeesAdhoc``).

        Note the vendor's spelling of the path segment (``initate``). Mandatory: `firmId`,
        `currencyCode`, `feeAmount`, `feeType`, `collectionMethod`, `collectionDate` (today or
        future), and one of account number / external ref / portfolio id / portfolio name.

        This is money out of a client's account. Nothing here is reversible through the API.
        """
        return _attributes(
            await self.request(
                "POST",
                "/initiateonetimefeesadhocservice/initateOneTimeFeesAdhoc",
                json={"data": {"attributes": attributes}},
            )
        )

    async def maintain_realtime_fees(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Add or remove real-time fee packages on an account
        (``POST /maintainrealtimefeeservice/account-maintenance/realtimefee``).

        Real-time fees are the transactional ones (dealing charges), as against the periodic
        ones the assignment endpoints above cover.
        """
        return _attributes(
            await self.request(
                "POST",
                "/maintainrealtimefeeservice/account-maintenance/realtimefee",
                json={"data": {"attributes": attributes}},
            )
        )
