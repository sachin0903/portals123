"""Daily valuation push — Morgan Lloyd's copy of what a scheme's policies are worth.

**This module used to call `cam_updateschemevaluation`, which does not exist.** It was a
placeholder name modelled on the `cam_updatescheme` envelope, marked as such in its own
docstring, and wired into `services/morgan-lloyd`'s `sink.daily_valuations` — so the sink
would have failed on its first live push with an action-not-found the vendor spells unhelpfully.
The real contract is `cam_createpolicyvaluation`, documented in full in
`vendor_manifest.json`, and it is what this module now sends.

Three things about it that the field list alone does not tell you:

* **The valuation is against a POLICY, not a scheme.** `PolicyReference` is the policy number
  minted by `cam_createwrapper` ("Create Investment"), not the `MLSIPP/…` scheme reference.
  Nothing currently records that policy number — `app/integrations/morgan_lloyd/sync.py`
  records the contact and the scheme — so the caller has to have it. Sending a scheme
  reference here is the `cam_schemerefs` mistake in another costume: a plausible identifier of
  the wrong kind, refused with a message that reads as "not found".
* **`policyValuation` is a JSON document passed as a single string.** Not an object. The
  vendor takes `{"Policies": [...]}` serialised.
* **`PolicyValue` must equal the sum of the fund splits.** The vendor says so and does not
  say what it does otherwise, so :meth:`PolicyValuation.from_splits` derives it rather than
  letting a caller pass two numbers that can disagree.

A successful write is **not verifiable through the API**: `cam_policyvaluations` is one of the
85 `cam_*` tables our service principal cannot read (403). The push ledger in
`services/morgan-lloyd` is the only record that a valuation was sent.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from morgan_lloyd._base import BaseClient


class FundSplit(BaseModel):
    """One holding inside a policy valuation (the vendor's "Fund Splits JSON").

    `Isin` is the only optional member. `PriceDate` is documented as a ``double`` and is a
    date — the vendor's own field table is wrong about the type, and it takes ``YYYY-MM-DD``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(alias="Name")
    valuation_holding: float = Field(alias="ValuationHolding")
    price: float = Field(alias="Price")
    market_value: float = Field(alias="MarketValue")
    price_date: str = Field(alias="PriceDate")
    isin: str | None = Field(default=None, alias="Isin")


class PolicyValuation(BaseModel):
    """One policy's valuation on one date (the vendor's "Policy Valuations JSON")."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    policy_reference: str = Field(alias="PolicyReference")
    policy_value: float = Field(alias="PolicyValue")
    valuation_date: str = Field(alias="ValuationDate")
    fund_splits: list[FundSplit] = Field(alias="FundSplits")

    @model_validator(mode="after")
    def _value_matches_the_splits(self) -> PolicyValuation:
        """Refuse a policy whose value does not equal its splits.

        The vendor states the equality and does not state what it does when the two disagree,
        which means a mismatch is either silently accepted (a wrong valuation on a client's
        record) or rejected in a way that names neither number. Refusing here is the only
        outcome that says which figure is wrong.

        Tolerance is a penny per split: the parts are rounded independently upstream, so
        exact equality would reject arithmetic that is correct.
        """
        if not self.fund_splits:
            return self
        total = sum(Decimal(str(split.market_value)) for split in self.fund_splits)
        tolerance = Decimal("0.01") * len(self.fund_splits)
        if abs(Decimal(str(self.policy_value)) - total) > tolerance:
            raise ValueError(
                f"PolicyValue {self.policy_value} does not match the sum of "
                f"{len(self.fund_splits)} fund splits ({total})"
            )
        return self

    @classmethod
    def from_splits(
        cls, policy_reference: str, valuation_date: str, splits: list[FundSplit]
    ) -> PolicyValuation:
        """Build one, deriving `PolicyValue` from the splits so the two cannot disagree."""
        total = sum(Decimal(str(split.market_value)) for split in splits)
        return cls(
            policy_reference=policy_reference,
            valuation_date=valuation_date,
            policy_value=float(total),
            fund_splits=splits,
        )

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class ValuationsApi(BaseClient):
    """Push policy valuations into Morgan Lloyd."""

    async def create_policy_valuation(
        self, provider: str, policies: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Create policy valuations, untyped (``POST cam_createpolicyvaluation``).

        ``provider`` is the firm GUID from the API Details document (settings'
        ``investment_provider_id``, falling back to ``adviser_firm_id`` — the vendor issues one
        value under three names). ``policies`` are raw dicts in the vendor's PascalCase; the
        ``{"Policies": …}`` wrapper and the string-encoding are applied here.

        Prefer :meth:`push_policy_valuations`, which validates the value against the splits.
        This stays for the callers that already build the dicts themselves
        (``services/morgan-lloyd/scripts/populate_test_data.py``).
        """
        return await self.call_action(
            "cam_createpolicyvaluation",
            {"provider": provider, "policyValuation": json.dumps({"Policies": policies})},
        )

    async def push_policy_valuations(
        self, provider: str, valuations: list[PolicyValuation]
    ) -> dict[str, Any]:
        """Create policy valuations from typed models (``POST cam_createpolicyvaluation``).

        The typed path, and the one a scheduled push should use: each
        :class:`PolicyValuation` has already been checked to equal its fund splits, so a
        rounding error upstream fails here with both numbers named rather than landing a wrong
        figure on a client's record.
        """
        return await self.create_policy_valuation(
            provider, [valuation.to_payload() for valuation in valuations]
        )
