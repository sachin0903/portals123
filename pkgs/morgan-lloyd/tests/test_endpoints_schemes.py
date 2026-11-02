from __future__ import annotations

import json
from typing import Any

import httpx

from morgan_lloyd.endpoints.schemes import SchemesApi
from morgan_lloyd.settings import MorganLloydSettings


def _settings() -> MorganLloydSettings:
    return MorganLloydSettings(
        resource_url="https://crm.test", tenant_id="tid", client_id="cid", client_secret="sec"
    )


def _client(handler: Any) -> SchemesApi:
    settings = _settings()
    client = SchemesApi(settings)
    client._http = httpx.AsyncClient(
        base_url=settings.api_base_url + "/",
        transport=httpx.MockTransport(handler),
        timeout=10,
        headers={"OData-MaxVersion": "4.0", "OData-Version": "4.0", "Accept": "application/json"},
    )
    return client


async def test_create_financial_adviser_contact_preserves_mixed_case_action() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})
        seen.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "financialAdviserContactId": "abc-123",
                "response": {"responseKey": 0, "message": "OK"},
            },
        )

    client = _client(handler)
    result = await client.create_financial_adviser_contact(
        {"accountId": "firm-guid", "title": 809130000, "firstName": "Ada", "lastName": "Lovelace"}
    )
    assert seen == ["/api/data/v9.1/cam_CreateFinancialAdviserContact"]
    assert result["financialAdviserContactId"] == "abc-123"


async def test_bulk_update_schemes_double_encodes_the_updates_array() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})
        assert request.url.path == "/api/data/v9.1/cam_bulkupdateschemes"
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "responseKey": 200,
                "message": json.dumps({"successfulUpdates": ["SSAS/01407"], "failedUpdates": []}),
            },
        )

    client = _client(handler)
    updates = [{"schemeRef": "SSAS/01407", "platformFeePercentage": 0.5}]
    await client.bulk_update_schemes(updates)
    (body,) = bodies
    assert isinstance(body["schemeUpdates"], str)  # double-encoded: array shipped as a string
    assert json.loads(body["schemeUpdates"]) == updates


async def test_update_beneficiary_patches_by_guid_and_returns_none_on_204() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})
        seen.append((request.method, request.url.path))
        return httpx.Response(
            204,
            headers={
                "OData-EntityId": (
                    "https://crm.test/api/data/v9.1/cam_nominationofbeneficiaries(guid-1)"
                )
            },
        )

    client = _client(handler)
    result = await client.update_beneficiary("guid-1", {"cam_beneficiarystatus": 0})
    assert result is None
    assert seen == [("PATCH", "/api/data/v9.1/cam_nominationofbeneficiaries(guid-1)")]


async def test_get_beneficiaries_by_scheme_ref_unwraps_the_value_array() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})
        assert request.url.path == "/api/data/v9.1/cam_schemebeneficiaries"
        assert json.loads(request.content) == {"schemeReference": "MLSIPP/01101"}
        return httpx.Response(
            200,
            json={
                "@odata.context": "https://crm.test/api/data/v9.1/$metadata#cam_schemes",
                "value": [
                    {
                        "cam_schemeid": "scheme-guid",
                        "nom_x002e_cam_nameofbeneficiary": "Jane Doe",
                        "nom_x002e_cam_percentageoffund": 50.0,
                    }
                ],
            },
        )

    client = _client(handler)
    records = await client.get_beneficiaries_by_scheme_ref("MLSIPP/01101")
    assert records == [
        {
            "cam_schemeid": "scheme-guid",
            "nom_x002e_cam_nameofbeneficiary": "Jane Doe",
            "nom_x002e_cam_percentageoffund": 50.0,
        }
    ]
