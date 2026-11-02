"""Transport: the mTLS unwrap, SEI's unusual token call, caching, re-auth and error mapping."""

from __future__ import annotations

import base64
import ssl
import tempfile
from pathlib import Path

import api_log.retry
import httpx
import pytest

from sei import (
    SeiApiError,
    SeiAuthError,
    SeiClient,
    SeiConfigError,
    SeiSettings,
)
from sei._base import ssl_context

TOKEN_PATH = "/oauthsecurity/access_token"
CALL_PATH = "/currencyreceiptservices/createCurrencyReceipt"


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})


# --- mTLS ---------------------------------------------------------------------------


def test_ssl_context_unwraps_a_pkcs12_bundle(pfx_base64: str, pfx_password: str) -> None:
    assert isinstance(ssl_context(pfx_base64, pfx_password), ssl.SSLContext)


def test_ssl_context_leaves_no_key_on_disk(pfx_base64: str, pfx_password: str) -> None:
    """The unencrypted PEM is written to a temp file because ``load_cert_chain`` takes only
    paths. It must not survive the call."""
    tmp = Path(tempfile.gettempdir())
    before = set(tmp.glob("*.pem"))
    ssl_context(pfx_base64, pfx_password)
    assert set(tmp.glob("*.pem")) == before


def test_ssl_context_tolerates_newlines_in_the_base64(pfx_base64: str, pfx_password: str) -> None:
    # A value pasted through a deployment UI's textarea arrives wrapped.
    wrapped = "\n".join(pfx_base64[i : i + 64] for i in range(0, len(pfx_base64), 64))
    assert isinstance(ssl_context(wrapped, pfx_password), ssl.SSLContext)


def test_ssl_context_rejects_a_missing_certificate() -> None:
    """Names the one thing to set. While a path form also existed this message had to name both,
    and the `configured` check named only the path — so a container supplying the base64 was
    refused for want of a file it could not mount."""
    with pytest.raises(SeiConfigError, match="SEI_PFX_BASE64"):
        ssl_context("", "x")


def test_ssl_context_rejects_a_bad_password(pfx_base64: str) -> None:
    with pytest.raises(SeiConfigError, match="failed to load"):
        ssl_context(pfx_base64, "wrong-password")


# --- configuration ------------------------------------------------------------------


async def test_entering_unconfigured_raises_before_any_network_call() -> None:
    settings = SeiSettings(environment="i31", base_url="https://x", pfx_base64="", pfx_password="")
    with pytest.raises(SeiConfigError) as exc:
        async with SeiClient(settings):
            pass
    assert "SEI_PFX_BASE64" in str(exc.value)


# --- the token call -----------------------------------------------------------------


async def test_token_request_matches_seis_spec(settings: SeiSettings) -> None:
    """AppKey and grant_type are HEADERS, the app pair is HTTP Basic, and only the user
    credentials go in the form body. Unusual enough to pin."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith(TOKEN_PATH):
            return _token_response()
        return httpx.Response(200, json={"data": []})

    async with SeiClient(settings, transport=httpx.MockTransport(handler)) as api:
        await api.request("POST", CALL_PATH, json={})

    token_request = seen[0]
    assert token_request.headers["AppKey"] == "app-key"
    assert token_request.headers["grant_type"] == "password"
    expected = base64.b64encode(b"app-key:app-secret").decode()
    assert token_request.headers["Authorization"] == f"Basic {expected}"
    assert b"username=i31pographeneapi50" in token_request.content
    assert b"password=secret" in token_request.content


async def test_appkey_is_sent_on_the_business_call_too(settings: SeiSettings) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith(TOKEN_PATH):
            return _token_response()
        return httpx.Response(200, json={"data": []})

    async with SeiClient(settings, transport=httpx.MockTransport(handler)) as api:
        await api.request("POST", CALL_PATH, json={})

    business = calls[-1]
    assert business.headers["AppKey"] == "app-key"
    assert business.headers["Authorization"] == "Bearer tok-1"


async def test_token_is_reused_across_calls(settings: SeiSettings) -> None:
    tokens = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tokens
        if request.url.path.endswith(TOKEN_PATH):
            tokens += 1
            return _token_response()
        return httpx.Response(200, json={"data": []})

    async with SeiClient(settings, transport=httpx.MockTransport(handler)) as api:
        await api.request("POST", CALL_PATH, json={})
        await api.request("POST", CALL_PATH, json={})
        await api.request("POST", CALL_PATH, json={})

    assert tokens == 1


async def test_401_on_a_call_re_authenticates_once_and_retries(settings: SeiSettings) -> None:
    tokens: list[str] = []
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith(TOKEN_PATH):
            tokens.append(f"tok-{len(tokens) + 1}")
            return httpx.Response(200, json={"access_token": tokens[-1], "expires_in": 3600})
        attempts += 1
        if attempts == 1:  # stale token
            return httpx.Response(401, json={"errors": []})
        return httpx.Response(200, json={"data": [{"attributes": {"activityId": "857458"}}]})

    async with SeiClient(settings, transport=httpx.MockTransport(handler)) as api:
        body = await api.request("POST", CALL_PATH, json={})

    assert len(tokens) == 2  # forced re-auth
    assert body["data"][0]["attributes"]["activityId"] == "857458"


async def test_rejected_credentials_raise_seiautherror(settings: SeiSettings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    with pytest.raises(SeiAuthError, match="allowlist"):
        async with SeiClient(settings, transport=httpx.MockTransport(handler)) as api:
            await api.request("POST", CALL_PATH, json={})


async def test_403_on_a_business_call_is_an_auth_error_not_an_outage(
    settings: SeiSettings,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(TOKEN_PATH):
            return _token_response()
        return httpx.Response(403, text="Forbidden")

    with pytest.raises(SeiAuthError):
        async with SeiClient(settings, transport=httpx.MockTransport(handler)) as api:
            await api.request("POST", CALL_PATH, json={})


# --- error mapping ------------------------------------------------------------------


async def test_business_errors_are_lifted_off_the_body(settings: SeiSettings) -> None:
    """SEI answers a bad payload with 400 and an ``errors[]`` array whose ``code`` is the
    actionable part — 1001 is "the firm is invalid or missing", which is what a PO-level
    caller gets when firmId is omitted."""
    errors = [{"code": "1001", "title": "The firm is invalid or missing.", "status": 400}]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(TOKEN_PATH):
            return _token_response()
        return httpx.Response(400, json={"errors": errors})

    with pytest.raises(SeiApiError) as exc:
        async with SeiClient(settings, transport=httpx.MockTransport(handler)) as api:
            await api.request("POST", CALL_PATH, json={})

    assert exc.value.status == 400
    assert exc.value.errors == errors


async def test_5xx_is_retried_then_surfaces(settings: SeiSettings) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith(TOKEN_PATH):
            return _token_response()
        attempts += 1
        return httpx.Response(503, text="unavailable")

    with pytest.raises(SeiApiError):
        async with SeiClient(settings, transport=httpx.MockTransport(handler)) as api:
            await api.request("GET", CALL_PATH)

    assert attempts == api_log.retry.MAX_ATTEMPTS  # one policy for all three clients


async def test_a_5xx_on_a_post_is_not_retried(settings: SeiSettings) -> None:
    """MONEY. A 5xx may mean SEI committed the write and lost the answer on the way back, and
    `create_cash_disbursement` pays cash away from the account — so a retry is a second payment.
    SEI has no idempotency key (`endpoints/cash.py` sends no `externalSystemTxnId`), so nothing
    downstream can collapse the duplicates. This was `stop_after_attempt(4)` for every method,
    which is up to four payments, and the `retry` class each operation declares gated nothing."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith(TOKEN_PATH):
            return _token_response()
        attempts += 1
        return httpx.Response(503, text="unavailable")

    with pytest.raises(SeiApiError):
        async with SeiClient(settings, transport=httpx.MockTransport(handler)) as api:
            await api.request("POST", CALL_PATH, json={})

    assert attempts == 1


async def test_a_429_on_a_post_IS_retried(settings: SeiSettings) -> None:
    """A rate limit is the one failure that PROVES the call was not processed — refused at the
    gate, before any effect — so retrying it cannot pay anybody twice. This is what the loop is
    worth keeping for on a write."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith(TOKEN_PATH):
            return _token_response()
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json={"ok": True})

    async with SeiClient(settings, transport=httpx.MockTransport(handler)) as api:
        assert await api.request("POST", CALL_PATH, json={}) == {"ok": True}
    assert attempts == 3


async def test_a_transport_error_on_a_post_is_not_retried(settings: SeiSettings) -> None:
    """Nothing distinguishes "never sent" from "sent, answer lost", so the safe reading is that
    it arrived."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith(TOKEN_PATH):
            return _token_response()
        attempts += 1
        raise httpx.ConnectError("connection reset", request=request)

    with pytest.raises(SeiApiError):
        async with SeiClient(settings, transport=httpx.MockTransport(handler)) as api:
            await api.request("POST", CALL_PATH, json={})

    assert attempts == 1


async def test_400_is_not_retried(settings: SeiSettings) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith(TOKEN_PATH):
            return _token_response()
        attempts += 1
        return httpx.Response(400, json={"errors": []})

    with pytest.raises(SeiApiError):
        async with SeiClient(settings, transport=httpx.MockTransport(handler)) as api:
            await api.request("POST", CALL_PATH, json={})

    assert attempts == 1
