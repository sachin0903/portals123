"""Pension income — crystallisation, the drawdown Seccl equivalent.

Taking benefits from a Seccl pension is **not** a withdrawal. It is a *Benefit Crystallisation
Instruction*: a decision about which part of the pot moves from uncrystallised to crystallised,
paired with the withdrawal legs that decision funds (`PCLS` for the tax-free cash, `FAD` or
`UFPLS` for the taxable income). Instructing the withdrawal alone would take money out without
crystallising it, which is a reportable event that did not happen.

Two shapes, and the difference is the frequency:

* ``POST /clientproduct/action/crystallisation`` — one crystallisation, `frequency: Single`.
* ``POST /clientproduct/action/regularcrystallisation`` — a standing one, with
  `crystallisationDay` and `expiryDate`. Cancelled by PATCHing it, not by deleting.

**Five FCA context fields are mandatory and none of them is held anywhere in our schema**:
`advised`, `guidance`, `fcaIndividualReference`, `benefitsTakenBefore2006` and
`protectedRetirementAgeOrLumpSum`. They are properties of the *advice event*, not of the
client, which is why they belong on a crystallisation record we do not yet have — see
`docs/api-integrations.md` §6. Morgan Lloyd asks for the same context in its own vocabulary
(nine flags on `cam_drawdownsetup`), so this is one gap, not two.

A crystallisation also needs a drawdown ACCOUNT to move into
(`portfolioGroups[].groupType: Crystallised`), so the full journey is create-account then
instruct.
"""

from __future__ import annotations

from typing import Any

from seccl._base import BaseClient
from seccl.endpoints import _paths


class PensionsApi(BaseClient):
    """Crystallise benefits; read and cancel the instructions."""

    async def crystallise(self, body: dict[str, Any]) -> dict[str, Any]:
        """One benefit crystallisation (``POST /clientproduct/action/crystallisation``).

        `crystallisationDetails` is `{type: FAD|UFPLS, frequency, fullCrystallisation, amount}`
        and `withdrawalDetails[]` are the legs it funds. Note `PCLS` takes `amount` while
        `FAD` takes `grossAmount` — the same array, two different money fields, and sending the
        wrong one is accepted as zero.
        """
        return await self.write(
            "POST",
            _paths.collection("clientproduct/action/crystallisation"),
            {"firmId": self.firm_id, **body},
        )

    async def crystallise_regularly(self, body: dict[str, Any]) -> dict[str, Any]:
        """A standing crystallisation
        (``POST /clientproduct/action/regularcrystallisation``).

        `crystallisationDay` is the day of the month; use
        :meth:`next_payment_date` rather than computing it, because Seccl's own resolution of a
        non-business day is what decides the actual date.
        """
        return await self.write(
            "POST",
            _paths.collection("clientproduct/action/regularcrystallisation"),
            {"firmId": self.firm_id, **body},
        )

    async def cancel_regular_crystallisation(
        self, instruction_id: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Stop a standing crystallisation
        (``PATCH /clientproduct/action/regularcrystallisation/{firm}/{id}``)."""
        return await self.write(
            "PATCH",
            _paths.record(
                "clientproduct/action/regularcrystallisation", self.firm_id, instruction_id
            ),
            body or {},
        )

    async def crystallisation(self, instruction_id: str) -> dict[str, Any]:
        """One instruction (``GET /clientproduct/action/{firm}/{id}``)."""
        return await self.get_record(
            _paths.record("clientproduct/action", self.firm_id, instruction_id)
        )

    async def crystallisations(self, **params: Any) -> list[dict[str, Any]]:
        """Search instructions (``GET /clientproduct/action/{firm}``)."""
        return await self.get_records(
            _paths.scoped("clientproduct/action", self.firm_id), params or None
        )

    async def next_payment_date(self, **params: Any) -> dict[str, Any]:
        """When a standing instruction will actually pay
        (``GET /v1/nextPaymentDate/{firm}``).

        Takes `currentDate`, `dayOfMonth` and `paymentType`. Worth calling rather than
        computing: the answer accounts for Seccl's own business-day resolution, and a date
        derived locally can be a day out in a way that only shows up as a missed month.
        """
        return await self.get_record(
            _paths.scoped("v1/nextPaymentDate", self.firm_id)
            + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")
        )
