"""SecclClient: auth modes, token refresh, pagination, retry."""

from __future__ import annotations

import base64
from typing import Any

import api_log.retry
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from seccl_sync.client import SecclApiError, SecclClient
from seccl_sync.settings import SecclSettings


def _pem_bytes() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _pem(tmp_path: Any) -> str:
    path = tmp_path / "seccl.pem"
    path.write_bytes(_pem_bytes())
    return str(path)


def _settings(tmp_path: Any, **overrides: Any) -> SecclSettings:
    defaults: dict[str, Any] = {
        "api_url": "https://api.test",
        "firm_id": "FIRM",
        "auth_type": "private_key_jwt",
        "auth0_domain": "https://auth.test/",
        "client_id": "cid",
        "kid": "kid-1",
        "private_key_path": _pem(tmp_path),
        "concurrency": 4,
    }
    defaults.update(overrides)
    return SecclSettings(**defaults)


def _client_with(transport: httpx.MockTransport, settings: SecclSettings) -> SecclClient:
    client = SecclClient(settings)
    client._http = httpx.AsyncClient(base_url=settings.api_url, transport=transport, timeout=10)
    return client


async def test_private_key_jwt_auth_uses_bearer(tmp_path: Any) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            form = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
            seen["assertion_type"] = form["client_assertion_type"]
            seen["has_assertion"] = bool(form.get("client_assertion"))
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 7200})
        seen["auth_header"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"data": [{"id": "1"}]})

    client = _client_with(httpx.MockTransport(handler), _settings(tmp_path))
    records = await client.get_records("/client/FIRM")
    assert records == [{"id": "1"}]
    assert seen["assertion_type"].startswith("urn%3Aietf")
    assert seen["has_assertion"]
    assert seen["auth_header"] == "Bearer tok-1"


async def test_private_key_b64_takes_precedence_over_path(tmp_path: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 7200})
        return httpx.Response(200, json={"data": []})

    pem_b64 = base64.b64encode(_pem_bytes()).decode()
    settings = _settings(tmp_path, private_key_path="/nonexistent/key.pem", private_key_b64=pem_b64)
    client = _client_with(httpx.MockTransport(handler), settings)
    assert await client.get_records("/client/FIRM") == []


def test_private_key_jwt_names_a_missing_key_instead_of_reading_the_working_directory(
    tmp_path: Any,
) -> None:
    client = SecclClient(_settings(tmp_path, private_key_path="", private_key_b64=""))
    with pytest.raises(RuntimeError, match="requires SECCL_PRIVATE_KEY_B64"):
        client._private_key()


def test_private_key_jwt_refuses_a_directory_as_the_key_path(tmp_path: Any) -> None:
    client = SecclClient(_settings(tmp_path, private_key_path=str(tmp_path), private_key_b64=""))
    with pytest.raises(RuntimeError, match="not a readable file"):
        client._private_key()


def test_private_key_b64_restores_padding_and_ignores_wrapping(tmp_path: Any) -> None:
    """A form that drops the trailing '=' or wraps the line must still yield the PEM.

    That missing character is ``Incorrect padding`` from ``b64decode``, and the pipeline
    reports it as the ``raw.accounts`` model failing.
    """
    pem = _pem_bytes()
    # PKCS8 length is sometimes a multiple of 3, so the base64 needs no '='. Pad the
    # bytes until it does — the production key's only padding is a single '='.
    while not base64.b64encode(pem).decode().endswith("="):
        pem += b"\n"
    encoded = base64.b64encode(pem).decode()
    wrapped = "\n".join(encoded[i : i + 64] for i in range(0, len(encoded), 64))
    for value in (encoded[:-1], wrapped, f'"{encoded}"'):
        client = SecclClient(_settings(tmp_path, private_key_path="", private_key_b64=value))
        assert client._private_key() == pem.decode()


def test_private_key_b64_names_a_value_that_is_not_a_pem(tmp_path: Any) -> None:
    client = SecclClient(_settings(tmp_path, private_key_path="", private_key_b64="not-a-pem"))
    with pytest.raises(RuntimeError, match="SECCL_PRIVATE_KEY_B64"):
        client._private_key()


def test_client_assertion_audience_always_has_the_trailing_slash(tmp_path: Any) -> None:
    client = SecclClient(_settings(tmp_path, auth0_domain="https://seccl-prod-selct.uk.auth0.com"))
    claims = jwt.decode(client._client_assertion(), options={"verify_signature": False})
    assert claims["aud"] == "https://seccl-prod-selct.uk.auth0.com/"


async def test_client_secret_auth_uses_api_token_header(tmp_path: Any) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "tok-2", "expires_in": 7200})
        seen["api_token"] = request.headers.get("api-token")
        return httpx.Response(200, json={"data": []})

    settings = _settings(tmp_path, auth_type="client_secret", client_secret="s3cret")
    client = _client_with(httpx.MockTransport(handler), settings)
    await client.get_records("/client/FIRM")
    assert seen["api_token"] == "tok-2"


async def test_small_set_refetched_as_one_exact_page(tmp_path: Any) -> None:
    """Seccl's page-boundary ordering is unstable (rows duplicate/vanish across
    pages — verified live), so sets under single_page_cap are refetched as one
    exactly-sized page, the only exact read."""
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
        size = int(request.url.params.get("pageSize", 100))
        sizes.append(size)
        rows = [{"id": f"r{i}"} for i in range(min(size, 6))]
        return httpx.Response(200, json={"data": rows, "meta": {"count": 6}})

    client = _client_with(httpx.MockTransport(handler), _settings(tmp_path, page_size=2))
    records = await client.get_list("/client/FIRM")
    assert sizes == [2, 6]  # probe page, then one exactly-sized fetch
    assert {record["id"] for record in records} == {f"r{i}" for i in range(6)}


async def test_large_set_pages_and_dedupes(tmp_path: Any) -> None:
    """Beyond single_page_cap the walk pages concurrently and dedupes by id —
    a boundary row served twice must not land twice."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
        page = int(request.url.params.get("page", 1))
        rows = [{"id": f"p{page}-{i}"} for i in range(2)]
        if page == 2:  # the boundary duplicate Seccl's unstable ordering produces
            rows[0] = {"id": "p1-1"}
        return httpx.Response(200, json={"data": rows, "meta": {"count": 6}})

    client = _client_with(
        httpx.MockTransport(handler), _settings(tmp_path, page_size=2, single_page_cap=4)
    )
    records = await client.get_list("/client/FIRM")
    ids = [record["id"] for record in records]
    assert len(ids) == len(set(ids)) == 5  # deduplicated; shortfall logged, not fatal


async def test_explicit_page_size_is_never_upgraded(tmp_path: Any) -> None:
    """A caller-pinned pageSize (illustrations: huge payloads) must stay pinned —
    no single-page refetch."""
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
        sizes.append(int(request.url.params.get("pageSize", 0)))
        page = int(request.url.params.get("page", 1))
        rows = [{"id": f"p{page}-{i}"} for i in range(2)]
        return httpx.Response(200, json={"data": rows, "meta": {"count": 4}})

    client = _client_with(httpx.MockTransport(handler), _settings(tmp_path))
    records = await client.get_list("/client/FIRM", {"pageSize": 2})
    assert set(sizes) == {2}
    assert len(records) == 4


async def test_419_triggers_reauth_and_retry(tmp_path: Any) -> None:
    tokens = iter(["tok-old", "tok-new"])
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": next(tokens), "expires_in": 7200})
        auth = request.headers.get("Authorization")
        calls.append(auth)
        if auth == "Bearer tok-old":
            return httpx.Response(419)
        return httpx.Response(200, json={"data": [{"id": "ok"}]})

    client = _client_with(httpx.MockTransport(handler), _settings(tmp_path))
    records = await client.get_records("/client/FIRM")
    assert records == [{"id": "ok"}]
    assert calls == ["Bearer tok-old", "Bearer tok-new"]


async def test_5xx_retries_then_raises(tmp_path: Any) -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
        attempts["n"] += 1
        return httpx.Response(503, json={"error": "down"})

    client = _client_with(httpx.MockTransport(handler), _settings(tmp_path))
    with pytest.raises(SecclApiError):
        await client.get_records("/client/FIRM")
    assert attempts["n"] == api_log.retry.MAX_ATTEMPTS  # one policy for all three clients


async def test_get_many_propagates_errors(tmp_path: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
        if request.url.path.endswith("/bad"):
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"data": {"id": "fine"}})

    client = _client_with(httpx.MockTransport(handler), _settings(tmp_path))
    with pytest.raises(SecclApiError):
        await client.get_many(["/account/FIRM/ok", "/account/FIRM/bad"])


async def test_extract_handles_dict_and_list_payloads(tmp_path: Any) -> None:
    assert SecclClient._extract({"data": [{"a": 1}]}) == [{"a": 1}]
    assert SecclClient._extract({"data": {"a": 1}}) == [{"a": 1}]
    assert SecclClient._extract([{"a": 1}]) == [{"a": 1}]
    assert SecclClient._extract({"data": None}) == []
    # no data attribute: the body itself is the record (v1 behaviour)
    assert SecclClient._extract({"meta": {}}) == [{"meta": {}}]
