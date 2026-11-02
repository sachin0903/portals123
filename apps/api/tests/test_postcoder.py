"""Postcoder address-lookup proxy (``GET /ref/postcoder/{find|retrieve}``).

The key and the upstream call live only here — the portals' ``/api/postcoder`` routes are thin
authenticated BFFs. These tests pin the country helper, the missing-key 503, the required
params, a successful find with httpx mocked, and that auth is required.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import Principal, get_current_principal
from app.config import settings
from app.main import app
from app.routers.postcoder import for_postcoder_country

_PRINCIPAL = Principal(
    user_id="user_postcoder_test",
    organisation_id="org_postcoder_test",
    role="adviser",
    permissions=[],
    client_id="client_postcoder_test",
)


@pytest.fixture
def as_user() -> Iterator[None]:
    app.dependency_overrides[get_current_principal] = lambda: _PRINCIPAL
    yield
    app.dependency_overrides.pop(get_current_principal, None)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def test_for_postcoder_country() -> None:
    assert for_postcoder_country("gb") == "uk"
    assert for_postcoder_country("GB") == "uk"
    assert for_postcoder_country("fr") == "fr"
    assert for_postcoder_country("  FR  ") == "fr"


@pytest.mark.asyncio
async def test_missing_key_returns_503(
    client: AsyncClient, as_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "postcoder_api_key", "")
    r = await client.get("/ref/postcoder/find", params={"query": "10 Downing"})
    assert r.status_code == 503
    assert r.json()["detail"] == "Address lookup is unavailable. Enter the address manually."


@pytest.mark.asyncio
async def test_find_requires_query(
    client: AsyncClient, as_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "postcoder_api_key", "pc_test")
    r = await client.get("/ref/postcoder/find")
    assert r.status_code == 400
    assert r.json()["detail"] == "Enter part of an address."


@pytest.mark.asyncio
async def test_retrieve_requires_id(
    client: AsyncClient, as_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "postcoder_api_key", "pc_test")
    r = await client.get("/ref/postcoder/retrieve")
    assert r.status_code == 400
    assert r.json()["detail"] == "Choose an address from the search results."


@pytest.mark.asyncio
async def test_find_success_mocks_upstream(
    client: AsyncClient, as_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "postcoder_api_key", "pc_test_key")
    seen: dict[str, Any] = {}

    class _Resp:
        is_success = True
        status_code = 200

        def json(self) -> list[dict[str, str]]:
            return [{"id": "add-1", "summaryline": "10 Downing Street"}]

    class _Client:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: object) -> bool:
            return False

        async def get(self, url: str, *, params: dict[str, str], headers: dict[str, str]) -> _Resp:
            seen["url"] = url
            seen["params"] = params
            seen["headers"] = headers
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    r = await client.get(
        "/ref/postcoder/find",
        params={"query": "10 Downing", "country": "GB", "pathfilter": "street-1"},
    )
    assert r.status_code == 200
    assert r.json() == [{"id": "add-1", "summaryline": "10 Downing Street"}]
    assert seen["url"] == "https://ws.postcoder.com/pcw/autocomplete/find"
    assert seen["params"]["apikey"] == "pc_test_key"
    assert seen["params"]["country"] == "uk"  # GB → uk
    assert seen["params"]["query"] == "10 Downing"
    assert seen["params"]["maximumresults"] == "10"
    assert seen["params"]["pathfilter"] == "street-1"
    assert "pc_test_key" not in r.text  # response body never echoes the key


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("country", "expected_country", "expected_exclude"),
    [("GB", "uk", None), ("FR", "fr", "posttown,county,postcode,country")],
)
async def test_retrieve_requests_country_appropriate_address_lines(
    client: AsyncClient,
    as_user: None,
    monkeypatch: pytest.MonkeyPatch,
    country: str,
    expected_country: str,
    expected_exclude: str | None,
) -> None:
    monkeypatch.setattr(settings, "postcoder_api_key", "pc_test_key")
    seen: dict[str, str] = {}

    class _Resp:
        is_success = True
        status_code = 200

        def json(self) -> list[dict[str, str]]:
            return [{"addressline1": "1 Rue de Test"}]

    class _Client:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: object) -> bool:
            return False

        async def get(self, _url: str, *, params: dict[str, str], headers: dict[str, str]) -> _Resp:
            seen.update(params)
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    response = await client.get(
        "/ref/postcoder/retrieve",
        params={"id": "address-1", "country": country},
    )

    assert response.status_code == 200
    assert seen["country"] == expected_country
    assert seen["lines"] == "3"
    assert seen.get("exclude") == expected_exclude


@pytest.mark.asyncio
async def test_auth_required(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """No principal override and no bearer → refused before the key is even consulted."""
    monkeypatch.setattr(settings, "postcoder_api_key", "pc_test")
    r = await client.get("/ref/postcoder/find", params={"query": "x"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_lookups_are_capped_per_user_before_the_upstream_is_touched(
    client: AsyncClient, as_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every call is paid upstream and the portals forward a keystroke each; the cap is per acting
    user so a stuck key or a scripted session cannot run up the bill — and the check runs before the
    key is looked at, so even a misconfigured deployment refuses at the same point."""
    from app import ratelimit

    ratelimit.limiter.reset()
    monkeypatch.setattr(settings, "postcoder_api_key", "")
    monkeypatch.setattr(settings, "rate_limit_postcoder_per_minute", 2)
    assert (await client.get("/ref/postcoder/find", params={"query": "a"})).status_code == 503
    assert (await client.get("/ref/postcoder/find", params={"query": "a"})).status_code == 503
    third = await client.get("/ref/postcoder/find", params={"query": "a"})
    assert third.status_code == 429
    assert "Too many address lookups" in third.json()["detail"]
    ratelimit.limiter.reset()


_BANK_BODY = {
    "valid": True,
    "stateid": 0,
    "sortcode": "100000",
    "accountnumber": "31510604",
    "bacs": True,
    "fasterpayments": False,
    "chaps": True,
    "directdebits": False,
    "bankbic": "BKENGB33",
    "bankname": "BANK OF ENGLAND",
    "branchname": "HEAD OFFICE",
}


def _bank_client(body: dict[str, object] | None, *, failed: bool = False) -> type:
    class _Resp:
        is_success = not failed

        def json(self) -> dict[str, object]:
            return body or {}

    class _Client:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: object) -> bool:
            return False

        async def post(self, url: str, **_k: object) -> _Resp:
            _KEY_HOLDER["url"] = url
            return _Resp()

    return _Client


_KEY_HOLDER: dict[str, str] = {}


@pytest.mark.asyncio
async def test_bank_missing_key_returns_503(
    client: AsyncClient, as_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "postcoder_api_key", "")
    r = await client.post(
        "/ref/postcoder/bank",
        json={"sort_code": "10-00-00", "account_number": "31510604"},
    )
    assert r.status_code == 503
    assert r.json()["detail"] == "Bank details can't be checked right now."


@pytest.mark.asyncio
async def test_bank_requires_a_complete_pair(
    client: AsyncClient, as_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "postcoder_api_key", "pc_test")
    r = await client.post(
        "/ref/postcoder/bank",
        json={"sort_code": "10-00", "account_number": "31510604"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "Enter a 6-digit sort code and an 8-digit account number."


@pytest.mark.asyncio
async def test_bank_success_returns_the_directory_record(
    client: AsyncClient, as_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "postcoder_api_key", "pc_test_key")
    monkeypatch.setattr(httpx, "AsyncClient", _bank_client(_BANK_BODY))
    r = await client.post(
        "/ref/postcoder/bank",
        json={"sort_code": "10-00-00", "account_number": "31510604"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True
    assert body["bank_name"] == "BANK OF ENGLAND"
    assert body["sort_code"] == "10-00-00"
    assert body["supports"]["bacs"] is True
    assert body["supports"]["faster_payments"] is False
    assert "pc_test_key" not in r.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upstream", "state", "message"),
    [
        (
            {"valid": False, "stateid": -1},
            "sort_code_unknown",
            "This sort code isn't recognised.",
        ),
        (
            {"valid": False, "stateid": -2},
            "account_invalid",
            "This account number isn't valid for that sort code.",
        ),
    ],
)
async def test_bank_rejection_names_what_is_wrong(
    client: AsyncClient,
    as_user: None,
    monkeypatch: pytest.MonkeyPatch,
    upstream: dict[str, object],
    state: str,
    message: str,
) -> None:
    monkeypatch.setattr(settings, "postcoder_api_key", "pc_test_key")
    monkeypatch.setattr(httpx, "AsyncClient", _bank_client(upstream))
    r = await client.post(
        "/ref/postcoder/bank",
        json={"sort_code": "99-99-99", "account_number": "12345678"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert body["state"] == state
    assert body["message"] == message
    assert body["sort_code"] == "99-99-99"


@pytest.mark.asyncio
async def test_bank_upstream_failure_hides_the_key(
    client: AsyncClient, as_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "postcoder_api_key", "pc_test_key")
    _KEY_HOLDER.clear()
    monkeypatch.setattr(httpx, "AsyncClient", _bank_client(None, failed=True))
    r = await client.post(
        "/ref/postcoder/bank",
        json={"sort_code": "10-00-00", "account_number": "31510604"},
    )
    assert r.status_code == 502
    assert r.json()["detail"] == "Couldn't check these bank details."
    assert "pc_test_key" not in r.text
    assert "pc_test_key" in _KEY_HOLDER["url"]


@pytest.mark.asyncio
async def test_bank_auth_required(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "postcoder_api_key", "pc_test")
    r = await client.post(
        "/ref/postcoder/bank",
        json={"sort_code": "10-00-00", "account_number": "31510604"},
    )
    assert r.status_code == 401
