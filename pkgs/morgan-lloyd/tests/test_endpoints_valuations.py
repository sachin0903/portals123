"""The daily valuation push.

This module previously asserted that the client posted ``cam_updateschemevaluation`` — an
action that does not exist in the org's `$metadata` or the published docs. The test passed
because a mock answers whatever it is asked, which is precisely the shape of test that lets a
placeholder ship: it pinned the placeholder rather than the contract. What is pinned now is
the vendor's own documented contract (`cam_createpolicyvaluation`), and
`test_actions_exist.py` independently asserts every action this package names is real.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from morgan_lloyd.endpoints.valuations import FundSplit, PolicyValuation, ValuationsApi
from morgan_lloyd.settings import MorganLloydSettings


def _client(handler: Any) -> ValuationsApi:
    settings = MorganLloydSettings(
        resource_url="https://crm.test", tenant_id="tid", client_id="cid", client_secret="sec"
    )
    client = ValuationsApi(settings)
    client._http = httpx.AsyncClient(
        base_url=settings.api_base_url + "/",
        transport=httpx.MockTransport(handler),
        timeout=10,
        headers={"OData-MaxVersion": "4.0", "OData-Version": "4.0", "Accept": "application/json"},
    )
    return client


def _split(name: str, market_value: float) -> FundSplit:
    return FundSplit(
        name=name,
        valuation_holding=100.0,
        price=market_value / 100.0,
        market_value=market_value,
        price_date="2026-07-21",
    )


class TestPolicyValuation:
    def test_from_splits_derives_the_policy_value(self) -> None:
        valuation = PolicyValuation.from_splits(
            "POL-1", "2026-07-21", [_split("Fund A", 60_000.0), _split("Fund B", 40_000.0)]
        )
        assert valuation.policy_value == 100_000.0

    def test_a_value_that_disagrees_with_the_splits_is_refused(self) -> None:
        """The vendor states the equality and does not say what it does when it fails, so the
        two outcomes are a wrong valuation on a client's record or an error naming neither
        number. Refusing here names both."""
        with pytest.raises(ValidationError, match="does not match the sum"):
            PolicyValuation(
                policy_reference="POL-1",
                policy_value=99_000.0,
                valuation_date="2026-07-21",
                fund_splits=[_split("Fund A", 60_000.0), _split("Fund B", 40_000.0)],
            )

    def test_penny_rounding_across_splits_is_tolerated(self) -> None:
        """Each split is rounded independently upstream, so exact equality would reject
        arithmetic that is correct."""
        valuation = PolicyValuation(
            policy_reference="POL-1",
            policy_value=100_000.02,
            valuation_date="2026-07-21",
            fund_splits=[_split("Fund A", 60_000.0), _split("Fund B", 40_000.0)],
        )
        assert valuation.policy_value == 100_000.02

    def test_a_policy_with_no_splits_is_allowed(self) -> None:
        """An empty policy is a real state (fully disinvested, awaiting instruction) and the
        equality check has nothing to compare against."""
        assert (
            PolicyValuation(
                policy_reference="POL-1",
                policy_value=0.0,
                valuation_date="2026-07-21",
                fund_splits=[],
            ).fund_splits
            == []
        )

    def test_isin_is_the_only_optional_member(self) -> None:
        payload = _split("Fund A", 1.0).model_dump(by_alias=True, exclude_none=True)
        assert set(payload) == {"Name", "ValuationHolding", "Price", "MarketValue", "PriceDate"}


async def test_push_posts_the_documented_action_with_a_stringified_document() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"responseKey": 0, "message": "OK"})

    valuation = PolicyValuation.from_splits("POL-1", "2026-07-21", [_split("Fund A", 125_000.50)])
    result = await _client(handler).push_policy_valuations("firm-guid", [valuation])

    assert seen["path"].endswith("/cam_createpolicyvaluation")
    assert seen["body"]["provider"] == "firm-guid"
    # `policyValuation` is a JSON DOCUMENT PASSED AS A STRING, not an object.
    assert isinstance(seen["body"]["policyValuation"], str)
    assert json.loads(seen["body"]["policyValuation"]) == {
        "Policies": [
            {
                "PolicyReference": "POL-1",
                "PolicyValue": 125000.5,
                "ValuationDate": "2026-07-21",
                "FundSplits": [
                    {
                        "Name": "Fund A",
                        "ValuationHolding": 100.0,
                        "Price": 1250.005,
                        "MarketValue": 125000.5,
                        "PriceDate": "2026-07-21",
                    }
                ],
            }
        ]
    }
    assert result["responseKey"] == 0


async def test_the_untyped_path_still_works_for_callers_that_build_their_own_dicts() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"responseKey": 0})

    await _client(handler).create_policy_valuation("firm-guid", [{"PolicyReference": "POL-9"}])
    assert json.loads(seen["body"]["policyValuation"]) == {
        "Policies": [{"PolicyReference": "POL-9"}]
    }
