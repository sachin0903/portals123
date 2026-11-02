from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from morgan_lloyd.endpoints.benefits import BenefitsApi
from morgan_lloyd.settings import MorganLloydSettings


def _settings() -> MorganLloydSettings:
    return MorganLloydSettings(
        resource_url="https://crm.test", tenant_id="tid", client_id="cid", client_secret="sec"
    )


def _client(handler: Any) -> BenefitsApi:
    settings = _settings()
    client = BenefitsApi(settings)
    client._http = httpx.AsyncClient(
        base_url=settings.api_base_url + "/",
        transport=httpx.MockTransport(handler),
        timeout=10,
        headers={"OData-MaxVersion": "4.0", "OData-Version": "4.0", "Accept": "application/json"},
    )
    return client


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})


async def test_get_bank_account_transactions_unwraps_array() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "@odata.context": "ctx",
                "response": {"responseKey": 0, "message": ""},
                "financialTransactions": [
                    {
                        "financialTransactionName": "FT-001",
                        "cashDate": "2026-01-31T00:00:00Z",
                        "grossAmount": 1500.0,
                        "direction": "1",
                    },
                    {
                        "financialTransactionName": "FT-002",
                        "cashDate": "2026-02-28T00:00:00Z",
                        "grossAmount": 250.0,
                        "direction": "2",
                    },
                ],
            },
        )

    client = _client(handler)
    transactions = await client.get_bank_account_transactions("SSAS/00297")
    assert [t["financialTransactionName"] for t in transactions] == ["FT-001", "FT-002"]
    assert transactions[0]["direction"] == "1"
    assert seen[0].url.path == "/api/data/v9.1/cam_getaccounttransactions"
    assert json.loads(seen[0].content) == {"schemeRef": "SSAS/00297"}


async def test_cancel_drawdown_enforces_xor_addressing() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        calls.append(request)
        return httpx.Response(
            200, json={"@odata.context": "ctx", "response": {"responseKey": 0, "message": ""}}
        )

    client = _client(handler)

    with pytest.raises(ValueError, match="scheme_member_event_id"):
        await client.cancel_drawdown()
    with pytest.raises(ValueError, match="not both"):
        await client.cancel_drawdown(
            scheme_member_event_id="11111111-1111-1111-1111-111111111111",
            policy_number="22222222-2222-2222-2222-222222222222",
        )
    with pytest.raises(ValueError, match="ALL of"):
        await client.cancel_drawdown(policy_number="22222222-2222-2222-2222-222222222222")
    assert calls == []  # nothing hit the API on the invalid combinations

    await client.cancel_drawdown(scheme_member_event_id="11111111-1111-1111-1111-111111111111")
    assert json.loads(calls[0].content) == {
        "schemeMemberEventId": "11111111-1111-1111-1111-111111111111"
    }

    await client.cancel_drawdown(
        policy_number="22222222-2222-2222-2222-222222222222",
        withdrawal_type=12,
        withdrawal_id=7,
    )
    assert json.loads(calls[1].content) == {
        "policyNumber": "22222222-2222-2222-2222-222222222222",
        "withdrawalType": 12,
        "withdrawalId": 7,
    }


async def test_update_benefit_application_illustration_template_returns_entity_id() -> None:
    seen: list[httpx.Request] = []
    entity_id = "https://crm.test/api/data/v9.1/annotations(33333333-3333-3333-3333-333333333333)"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        seen.append(request)
        return httpx.Response(204, headers={"OData-EntityId": entity_id})

    client = _client(handler)
    result = await client.update_benefit_application_illustration_template(
        "44444444-4444-4444-4444-444444444444", "<ctc1>xml</ctc1>"
    )
    assert result == entity_id
    assert seen[0].url.path == "/api/data/v9.1/annotations"
    assert json.loads(seen[0].content) == {
        "objectid_cam_benefitsapplication@odata.bind": (
            "/cam_benefitsapplications(44444444-4444-4444-4444-444444444444)"
        ),
        "subject": "Illustration Template",
        "notetext": "<ctc1>xml</ctc1>",
    }
