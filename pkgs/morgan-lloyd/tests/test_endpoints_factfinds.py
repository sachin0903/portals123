"""FactfindsApi endpoint tests against a mock transport (token endpoint stubbed)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from morgan_lloyd.endpoints.factfinds import FactfindsApi
from morgan_lloyd.settings import MorganLloydSettings

CONTACT_ID = "11111111-1111-1111-1111-111111111111"
JOINT_ID = "22222222-2222-2222-2222-222222222222"


def _settings() -> MorganLloydSettings:
    return MorganLloydSettings(
        resource_url="https://crm.test", tenant_id="tid", client_id="cid", client_secret="sec"
    )


def _client(handler: Any) -> FactfindsApi:
    settings = _settings()
    client = FactfindsApi(settings)
    client._http = httpx.AsyncClient(
        base_url=settings.api_base_url + "/",
        transport=httpx.MockTransport(handler),
        timeout=10,
        headers={"OData-MaxVersion": "4.0", "OData-Version": "4.0", "Accept": "application/json"},
    )
    return client


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})


async def test_get_client_factfind_posts_action_and_returns_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "response": {"responseKey": 0, "message": "OK"},
                "factFind": {"cam_factfindid": "ff-1", "cam_smoker": False},
            },
        )

    client = _client(handler)
    body = await client.get_client_factfind(CONTACT_ID)

    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/api/data/v9.1/cam_getclientfactfind"  # exact case
    assert json.loads(seen[0].content) == {"contactId": CONTACT_ID}
    assert body["factFind"]["cam_factfindid"] == "ff-1"


async def test_upsert_personal_asset_json_encodes_joint_assets() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "message": "Created OK",
                "response": {"cam_personalpolicyid": "pa-1", "cam_jointasset": True},
            },
        )

    client = _client(handler)
    request_body = {"cam_policyholderid": CONTACT_ID, "cam_status": 1, "cam_assettype": 3}
    result = await client.upsert_personal_asset(request_body, joint_assets=[JOINT_ID])

    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/api/data/v9.1/cam_createupdatepersonalasset"
    sent = json.loads(seen[0].content)
    assert sent["cam_policyholderid"] == CONTACT_ID
    assert sent["cam_status"] == 1
    assert sent["cam_assettype"] == 3
    assert sent["jointAssets"] == json.dumps([JOINT_ID])  # a string containing a JSON array
    assert "jointAssets" not in request_body  # caller's body is not mutated
    assert result["response"]["cam_personalpolicyid"] == "pa-1"


async def test_get_employer_accounts_filtered_is_an_odata_get() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        seen.append(request)
        return httpx.Response(
            200,
            json={"value": [{"accountid": "acc-1", "name": "Acme Ltd"}]},
        )

    client = _client(handler)
    accounts = await client.get_employer_accounts_filtered()

    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/api/data/v9.1/accounts"
    assert seen[0].url.params["$select"] == "name"
    assert (
        seen[0].url.params["$filter"]
        == "statecode eq 0 and cam_accountstatus eq 0 and customertypecode eq 809130001"
    )
    assert accounts == [{"accountid": "acc-1", "name": "Acme Ltd"}]


async def test_get_all_providers_unwraps_the_response_array() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "message": None,
                "response": [
                    {"cam_providerid": "p-1", "cam_name": "Aviva"},
                    {"cam_providerid": "p-2", "cam_name": "Royal London"},
                ],
            },
        )

    client = _client(handler)
    providers = await client.get_all_providers()

    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/api/data/v9.1/cam_getallproviders"
    assert json.loads(seen[0].content) == {}
    assert providers == [
        {"cam_providerid": "p-1", "cam_name": "Aviva"},
        {"cam_providerid": "p-2", "cam_name": "Royal London"},
    ]
