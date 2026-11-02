from __future__ import annotations

import json
from typing import Any

import httpx

from morgan_lloyd.endpoints.contacts import ContactsApi
from morgan_lloyd.settings import MorganLloydSettings


def _settings() -> MorganLloydSettings:
    return MorganLloydSettings(
        resource_url="https://crm.test", tenant_id="tid", client_id="cid", client_secret="sec"
    )


def _client(handler: Any) -> ContactsApi:
    settings = _settings()
    client = ContactsApi(settings)
    client._http = httpx.AsyncClient(
        base_url=settings.api_base_url + "/",
        transport=httpx.MockTransport(handler),
        timeout=10,
        headers={"OData-MaxVersion": "4.0", "OData-Version": "4.0", "Accept": "application/json"},
    )
    return client


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})


async def test_create_contact_posts_action_and_returns_body() -> None:
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
                "message": "Created OK",
                "response": {
                    "@odata.type": "#Microsoft.Dynamics.CRM.contact",
                    "responseKey": 0,
                    "fullname": "Jane Doe",
                    "contactid": "fcdab33b-beaf-ea11-a812-000d3a86f6ce",
                },
            },
        )

    payload = {
        "as_contactstatus": 809130002,
        "cam_title": 809130002,
        "firstname": "Jane",
        "lastname": "Doe",
        "emailaddress1": "jane@example.test",
        "cam_potentiallyvulnerable": "False",
    }
    result = await _client(handler).create_contact(payload)

    assert seen["method"] == "POST"
    assert seen["path"] == "/api/data/v9.1/cam_createcontact"
    assert seen["body"] == payload
    assert result["response"]["contactid"] == "fcdab33b-beaf-ea11-a812-000d3a86f6ce"


async def test_get_adviser_contacts_unwraps_contacts_array() -> None:
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
                "response": {"responseKey": 0, "message": "2 Contacts found"},
                "contacts": [
                    {"contactid": "guid-1", "firstname": "Ann"},
                    {"contactid": "guid-2", "firstname": "Bob"},
                ],
            },
        )

    contacts = await _client(handler).get_adviser_contacts("adviser@example.test")

    assert seen["method"] == "POST"
    assert seen["path"] == "/api/data/v9.1/cam_getadvisercontacts"
    assert seen["body"] == {"adviserEmailAddress": "adviser@example.test"}
    assert [c["contactid"] for c in contacts] == ["guid-1", "guid-2"]


async def test_update_contact_patches_and_returns_entity_id() -> None:
    seen: dict[str, Any] = {}
    entity_id = "https://crm.test/api/data/v9.1/contacts(fcdab33b-beaf-ea11-a812-000d3a86f6ce)"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(204, headers={"OData-EntityId": entity_id})

    fields = {
        "mobilephone": "07000000000",
        "cam_FinancialAdviserContact@odata.bind": "/contacts(g)",
    }
    result = await _client(handler).update_contact("fcdab33b-beaf-ea11-a812-000d3a86f6ce", fields)

    assert seen["method"] == "PATCH"
    assert seen["path"] == "/api/data/v9.1/contacts(fcdab33b-beaf-ea11-a812-000d3a86f6ce)"
    assert seen["body"] == fields
    assert result == entity_id


async def test_submit_atr_serialises_atr_data_as_json_string() -> None:
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
                "contactId": "guid-9",
                "response": {"responseKey": 0, "message": "ATR record created succesfully"},
            },
        )

    atr_data = [{"question_id": 1, "question": "Q1?", "response": "Agree"}]
    result = await _client(handler).submit_atr(1234, 7, "2020-04-05", atr_data)

    assert seen["method"] == "POST"
    assert seen["path"] == "/api/data/v9.1/cam_submitatr"
    assert seen["body"]["clientId"] == 1234
    assert seen["body"]["riskProfile"] == 7
    assert seen["body"]["dateGenerated"] == "2020-04-05"
    assert isinstance(seen["body"]["atrData"], str)
    assert json.loads(seen["body"]["atrData"]) == atr_data
    assert result["contactId"] == "guid-9"
