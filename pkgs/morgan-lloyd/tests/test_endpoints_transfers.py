"""Tests for the transfers/pension-contributions endpoint module."""

from __future__ import annotations

import json
from typing import Any

import httpx

from morgan_lloyd.endpoints.transfers import TransfersApi
from morgan_lloyd.settings import MorganLloydSettings


def _settings() -> MorganLloydSettings:
    return MorganLloydSettings(
        resource_url="https://crm.test", tenant_id="tid", client_id="cid", client_secret="sec"
    )


def _client(handler: Any) -> TransfersApi:
    settings = _settings()
    client = TransfersApi(settings)
    client._http = httpx.AsyncClient(
        base_url=settings.api_base_url + "/",
        transport=httpx.MockTransport(handler),
        timeout=10,
        headers={"OData-MaxVersion": "4.0", "OData-Version": "4.0", "Accept": "application/json"},
    )
    return client


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})


async def test_create_transfer_posts_action_with_body() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "response": {"responseKey": 200, "message": "Transfer Created OK"},
                "transferId": {"cam_personalpolicyid": "11111111-1111-1111-1111-111111111111"},
            },
        )

    transfer = {
        "schemeReference": "SCH-001",
        "cam_policyno": "2973974874",
        "cam_uncrystalllisedtransfervalue": 1000.5,  # verbatim triple-l field name
    }
    result = await _client(handler).create_transfer(transfer)

    assert seen["method"] == "POST"
    assert seen["path"] == "/api/data/v9.1/cam_createtransfer"
    assert seen["body"]["cam_uncrystalllisedtransfervalue"] == 1000.5
    assert result["transferId"]["cam_personalpolicyid"] == "11111111-1111-1111-1111-111111111111"


async def test_get_all_transfers_by_scheme_unwraps_transfers_array() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "message": "2 Transfers found",
                "transfers": [
                    {"cam_policyno": "P1", "cam_transferstatus": 3},
                    {"cam_policyno": "P2", "cam_transferstatus": 0},
                ],
            },
        )

    transfers = await _client(handler).get_all_transfers_by_scheme("SCH-001")

    assert seen["path"] == "/api/data/v9.1/cam_getalltransfersbyscheme"
    assert seen["body"] == {"schemeReference": "SCH-001"}
    assert [t["cam_policyno"] for t in transfers] == ["P1", "P2"]


async def test_update_pension_contribution_status_patches_by_guid() -> None:
    seen: dict[str, Any] = {}
    guid = "22222222-2222-2222-2222-222222222222"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            204,
            headers={"OData-EntityId": f"https://crm.test/api/data/v9.1/cam_contributions({guid})"},
        )

    entity_id = await _client(handler).update_pension_contribution_status(guid, 809130002)

    assert seen["method"] == "PATCH"
    assert seen["path"] == f"/api/data/v9.1/cam_contributions({guid})"
    assert seen["body"] == {"cam_status": 809130002}
    assert entity_id == f"https://crm.test/api/data/v9.1/cam_contributions({guid})"


async def test_upload_pension_contributions_encodes_escaped_json_string() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "response": {"responseKey": 0, "message": "Pension upload completed OK"},
                "results": [{"SchemeReference": "SCH-001", "Message": "Created OK"}],
            },
        )

    items = [
        {"schemeReference": "SCH-001", "cam_frequency": 809130004, "cam_amount": 250.0},
        {"schemeReference": "SCH-002", "cam_frequency": 809130000, "cam_amount": 100.0},
    ]
    result = await _client(handler).upload_pension_contributions(items)

    assert seen["path"] == "/api/data/v9.1/cam_uploadpensioncontributions"
    # The one body field is a stringified-JSON payload keyed "Contributions".
    assert set(seen["body"]) == {"pensioncontributions"}
    assert isinstance(seen["body"]["pensioncontributions"], str)
    assert json.loads(seen["body"]["pensioncontributions"]) == {"Contributions": items}
    assert result["results"][0]["SchemeReference"] == "SCH-001"
