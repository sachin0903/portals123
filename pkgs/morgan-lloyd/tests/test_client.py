"""BaseClient transport: token grant, refresh-on-401, retry, pagination, envelope."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote_plus

import api_log.retry
import httpx
import pytest

from morgan_lloyd._base import BaseClient, MorganLloydActionError, MorganLloydApiError
from morgan_lloyd.settings import MorganLloydSettings

_API_HEADERS = {"OData-MaxVersion": "4.0", "OData-Version": "4.0", "Accept": "application/json"}


def _settings(**overrides: Any) -> MorganLloydSettings:
    defaults: dict[str, Any] = {
        "resource_url": "https://crm.test",
        "tenant_id": "tid",
        "client_id": "cid",
        "client_secret": "s3cret",
        "concurrency": 4,
    }
    defaults.update(overrides)
    return MorganLloydSettings(**defaults)


def _client(handler: Any, settings: MorganLloydSettings | None = None) -> BaseClient:
    settings = settings or _settings()
    client = BaseClient(settings)
    client._http = httpx.AsyncClient(
        base_url=settings.api_base_url + "/",
        transport=httpx.MockTransport(handler),
        timeout=10,
        headers=_API_HEADERS,
    )
    return client


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3599})


async def test_token_grant_posts_client_credentials_form() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            seen["host"] = request.url.host
            seen["form"] = {
                unquote_plus(k): unquote_plus(v)
                for k, v in (pair.split("=", 1) for pair in request.content.decode().split("&"))
            }
            return _token_response()
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"value": [{"contactid": "1"}]})

    client = _client(handler)
    records = await client.get_collection("contacts")
    assert records == [{"contactid": "1"}]
    assert seen["host"] == "login.microsoftonline.com"
    assert seen["form"] == {
        "grant_type": "client_credentials",
        "client_id": "cid",
        "client_secret": "s3cret",
        "resource": "https://crm.test",
    }
    assert seen["auth"] == "Bearer tok-1"


async def test_token_shared_across_client_instances() -> None:
    """Extractors open a fresh client per model pull — the grant must not re-run
    for every pull (the cache is process-wide, keyed by connection identity)."""
    grants = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            grants["n"] += 1
            return _token_response()
        return httpx.Response(200, json={"value": []})

    await _client(handler).get_collection("contacts")
    await _client(handler).get_collection("cam_personalpolicies")
    assert grants["n"] == 1

    # A different connection (other Microsoft tenant) gets its own grant.
    other = _settings(tenant_id="tid-2")
    await _client(handler, other).get_collection("contacts")
    assert grants["n"] == 2


async def test_401_triggers_reauth_and_retry() -> None:
    tokens = iter(["tok-old", "tok-new"])
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return httpx.Response(200, json={"access_token": next(tokens), "expires_in": 3599})
        auth = request.headers.get("Authorization")
        calls.append(auth)
        if auth == "Bearer tok-old":
            return httpx.Response(401)
        return httpx.Response(200, json={"contactid": "ok"})

    client = _client(handler)
    body = await client.get("contacts(x)")
    assert body == {"contactid": "ok"}
    assert calls == ["Bearer tok-old", "Bearer tok-new"]


async def test_5xx_retries_then_raises_with_detail() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        attempts["n"] += 1
        return httpx.Response(503, json={"error": "down"})

    client = _client(handler)
    with pytest.raises(MorganLloydApiError, match="503"):
        await client.get("contacts")
    assert attempts["n"] == api_log.retry.MAX_ATTEMPTS  # one policy for all three clients


async def test_collection_follows_odata_next_link() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json={"value": [{"id": "b"}]})
        return httpx.Response(
            200,
            json={
                "value": [{"id": "a"}],
                "@odata.nextLink": "https://crm.test/api/data/v9.1/contacts?page=2",
            },
        )

    client = _client(handler)
    records = await client.get_collection("contacts")
    assert [record["id"] for record in records] == ["a", "b"]


async def test_action_envelope_failure_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        return httpx.Response(
            200,
            json={"response": {"responseKey": 1, "message": "Contact not found"}},
        )

    client = _client(handler)
    with pytest.raises(MorganLloydActionError, match="Contact not found"):
        await client.call_action("cam_getadvisercontacts", {"adviserEmailAddress": "a@b.c"})


async def test_action_flat_envelope_and_response_message_variant() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        return httpx.Response(200, json={"responseKey": 1, "responseMessage": "nope"})

    client = _client(handler)
    with pytest.raises(MorganLloydActionError, match="nope"):
        await client.call_action("cam_drawdownsetup", {})


async def test_action_success_returns_body_and_204_returns_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        if request.url.path.endswith("cam_ok"):
            return httpx.Response(200, json={"response": {"responseKey": 0}, "contactId": "c-1"})
        return httpx.Response(204)

    client = _client(handler)
    assert (await client.call_action("cam_ok", {}))["contactId"] == "c-1"
    assert await client.call_action("cam_silent", {}) == {}


async def test_patch_entity_returns_odata_entity_id() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            return _token_response()
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(
            204, headers={"OData-EntityId": "https://crm.test/api/data/v9.1/contacts(g-1)"}
        )

    client = _client(handler)
    entity_id = await client.patch_entity("contacts(g-1)", {"firstname": "Jo"})
    assert seen["method"] == "PATCH"
    assert seen["path"] == "/api/data/v9.1/contacts(g-1)"
    assert entity_id == "https://crm.test/api/data/v9.1/contacts(g-1)"


async def test_token_is_reused_until_expiry() -> None:
    token_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tid/oauth2/token":
            token_calls["n"] += 1
            return _token_response()
        return httpx.Response(200, json={"value": []})

    client = _client(handler)
    await client.get_collection("contacts")
    await client.get_collection("accounts")
    assert token_calls["n"] == 1
