"""The audit hook on the request choke point.

The hook is worth testing rather than eyeballing because its whole value is that it fires
without anyone remembering to call it — including on the paths where the call went wrong, which
are the ones an incident is reconstructed from.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from api_log import ApiCall, clear_default_recorder, set_default_recorder

from morgan_lloyd._base import (
    BaseClient,
    MorganLloydApiError,
    MorganLloydAuthError,
    _aad_failure,
    _mints_an_identifier,
    _operation_of,
)
from morgan_lloyd.settings import MorganLloydSettings


def _settings() -> MorganLloydSettings:
    return MorganLloydSettings(
        resource_url="https://crm.test",
        tenant_id="t",
        client_id="c",
        client_secret="s",  # noqa: S106 - dummy
    )


def _token(request: httpx.Request) -> httpx.Response | None:
    if "oauth2/token" in str(request.url):
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    return None


@pytest.fixture(autouse=True)
def _token_already_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer every cache lookup with a live token, so no test here performs the exchange.

    Every test in this module below `TestTokenFailures` is about the record of a REQUEST. They
    used to set `client._token` by hand, which has done nothing since the cache became
    process-wide — so each one silently ran the client-credentials grant against its own mock and
    simply did not notice, because the grant was not recorded. Now that it IS recorded, an
    unprimed cache adds a second `ApiCall` to every assertion. Priming states the intent instead
    of counting rows around it.

    `TestTokenFailures` calls `_fetch_token` directly and so is unaffected — which is what makes
    this safe rather than a way of hiding the thing under test.
    """

    class _AlwaysCached(dict):  # type: ignore[type-arg]
        def get(self, key: Any, default: Any = None) -> Any:
            return ("tok", time.monotonic() + 3600)

    monkeypatch.setattr("morgan_lloyd._base._TOKEN_CACHE", _AlwaysCached())


async def _call(handler: Any, *, method: str = "POST") -> list[ApiCall]:
    seen: list[ApiCall] = []
    client = BaseClient(_settings(), recorder=seen.append)
    transport = httpx.MockTransport(handler)
    async with client:
        client._http = httpx.AsyncClient(base_url=_settings().api_base_url, transport=transport)
        try:
            await client._request(method, "cam_createscheme", json={"contactId": "abc"})
        except MorganLloydApiError:
            pass
    return seen


@pytest.fixture
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collapse the retry wait. The client backs off 1s, 2s, 4s, 8s, which is correct against a
    real API and turns one test into half a minute of sleeping."""
    from tenacity import wait_none

    monkeypatch.setattr("api_log.retry.BACKOFF", wait_none())


@pytest.mark.asyncio
async def test_a_successful_call_is_recorded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _token(request) or httpx.Response(200, json={"schemeReference": "MLSIPP/1"})

    (call,) = await _call(handler)

    # THE HYPHEN. `platform.provider_code` spells it `morgan-lloyd` and every reader of
    # `audit.api_log` binds that, so the underscore this used to write made the console's three
    # Morgan Lloyd outbound tabs — investments, documents, illustrations, which read our own log
    # because the vendor publishes none of them back — return zero rows on a firm with calls.
    assert call.provider == "morgan-lloyd"
    assert call.method == "POST"
    assert call.operation == "cam_createscheme"
    assert call.status_code == 200
    assert call.ok is True
    assert call.request == {"contactId": "abc"}
    assert call.duration_ms is not None
    # A create keeps its body: `schemeReference` is the id this call just minted, and after the
    # `platform.links` row is written it exists nowhere else.
    assert call.response == {"schemeReference": "MLSIPP/1"}


@pytest.mark.asyncio
async def test_an_http_error_is_recorded_with_its_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _token(request) or httpx.Response(403, json={"error": {"message": "no rights"}})

    (call,) = await _call(handler)

    assert call.ok is False
    assert call.status_code == 403
    assert "no rights" in str(call.response)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_backoff")
async def test_a_call_that_never_reached_the_network_is_still_recorded() -> None:
    # The case a naive "record after the response" hook misses entirely, and the one an
    # incident is reconstructed from.
    def handler(request: httpx.Request) -> httpx.Response:
        token = _token(request)
        if token:
            return token
        raise httpx.ConnectError("no route to host")

    (call,) = await _call(handler)

    assert call.ok is False
    assert call.status_code is None
    assert call.error is not None
    assert "ConnectError" in call.error or "no route" in call.error


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_backoff")
async def test_one_row_per_logical_call_not_per_retry() -> None:
    # A 503 on a GET is retried; the audit trail should say "we called this once and it failed",
    # not invent four calls the adviser never made.
    #
    # A GET, not the POST the other cases here drive: `api_log.retry` retries a 5xx only for an
    # idempotent method now, because Dynamics has no delete and a retried create puts a second
    # human being in the CRM. The test below pins that half.
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        token = _token(request)
        if token:
            return token
        attempts += 1
        return httpx.Response(503, text="unavailable")

    seen = await _call(handler, method="GET")

    assert attempts > 1, "precondition: the client should have retried"
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_backoff")
async def test_a_write_is_attempted_once_and_still_recorded() -> None:
    """The other half of the shared policy, at this client's own choke point.

    Dynamics HAS NO DELETE, so a 503 on `cam_createscheme` may mean the scheme was created and
    the answer lost — retrying it files a second scheme against the same member. One attempt,
    one `audit.api_log` row carrying the failure, and an operator resends deliberately.
    """
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        token = _token(request)
        if token:
            return token
        attempts += 1
        return httpx.Response(503, text="unavailable")

    (call,) = await _call(handler)

    assert attempts == 1
    assert call.status_code == 503
    assert call.ok is False


@pytest.mark.asyncio
async def test_credentials_never_reach_the_recorder() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _token(request) or httpx.Response(200, json={"ok": True})

    seen: list[ApiCall] = []
    client = BaseClient(_settings(), recorder=seen.append)
    async with client:
        client._http = httpx.AsyncClient(
            base_url=_settings().api_base_url, transport=httpx.MockTransport(handler)
        )
        client._token = "tok"
        client._token_expires_at = 9_999_999_999.0
        await client._request(
            "POST", "cam_x", json={"password": "hunter2", "apiKey": "k", "name": "keep"}
        )

    (call,) = seen
    assert call.request["password"] == "[redacted]"
    assert call.request["apiKey"] == "[redacted]"
    assert call.request["name"] == "keep"


@pytest.mark.asyncio
async def test_no_recorder_is_a_supported_configuration() -> None:
    # The SDK must stay usable from a script with no database.
    def handler(request: httpx.Request) -> httpx.Response:
        return _token(request) or httpx.Response(200, json={"ok": True})

    client = BaseClient(_settings())
    async with client:
        client._http = httpx.AsyncClient(
            base_url=_settings().api_base_url, transport=httpx.MockTransport(handler)
        )
        client._token = "tok"
        client._token_expires_at = 9_999_999_999.0
        response = await client._request("GET", "contacts")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_a_bare_client_records_to_the_process_default() -> None:
    """THE TRIPWIRE. The sync's models construct `MorganLloydClient()` in thirty-odd places and
    pass no recorder; if the fallback stops working, every one of them goes silent at once and
    nothing else fails. That is precisely how `audit.api_log` came to be a finished table with
    no producer, so it is pinned rather than trusted."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _token(request) or httpx.Response(200, json={"ok": True})

    seen: list[ApiCall] = []
    set_default_recorder(seen.append)
    try:
        client = BaseClient(_settings())  # no recorder=
        async with client:
            client._http = httpx.AsyncClient(
                base_url=_settings().api_base_url, transport=httpx.MockTransport(handler)
            )
            client._token = "tok"
            client._token_expires_at = 9_999_999_999.0
            await client._request("GET", "contacts")
    finally:
        clear_default_recorder()

    (call,) = seen
    # THE HYPHEN. `platform.provider_code` spells it `morgan-lloyd` and every reader of
    # `audit.api_log` binds that, so the underscore this used to write made the console's three
    # Morgan Lloyd outbound tabs — investments, documents, illustrations, which read our own log
    # because the vendor publishes none of them back — return zero rows on a firm with calls.
    assert call.provider == "morgan-lloyd"
    assert call.operation == "contacts"


@pytest.mark.asyncio
async def test_an_explicit_recorder_beats_the_default() -> None:
    """The API's call sites know the acting tenant and user; the process default cannot."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _token(request) or httpx.Response(200, json={"ok": True})

    fallback: list[ApiCall] = []
    explicit: list[ApiCall] = []
    set_default_recorder(fallback.append)
    try:
        client = BaseClient(_settings(), recorder=explicit.append)
        async with client:
            client._http = httpx.AsyncClient(
                base_url=_settings().api_base_url, transport=httpx.MockTransport(handler)
            )
            client._token = "tok"
            client._token_expires_at = 9_999_999_999.0
            await client._request("GET", "contacts")
    finally:
        clear_default_recorder()

    assert len(explicit) == 1
    assert fallback == []


@pytest.mark.asyncio
async def test_the_default_is_resolved_per_call_not_at_construction() -> None:
    """Model discovery imports every module under `models/` before a daemon has installed its
    recorder, so a client built at import time must not capture the absence for ever."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _token(request) or httpx.Response(200, json={"ok": True})

    client = BaseClient(_settings())  # built BEFORE the install
    seen: list[ApiCall] = []
    set_default_recorder(seen.append)
    try:
        async with client:
            client._http = httpx.AsyncClient(
                base_url=_settings().api_base_url, transport=httpx.MockTransport(handler)
            )
            client._token = "tok"
            client._token_expires_at = 9_999_999_999.0
            await client._request("GET", "contacts")
    finally:
        clear_default_recorder()
    assert len(seen) == 1


def test_only_creates_keep_their_success_body() -> None:
    """The prefix test that decides it, pinned against the real action names."""
    assert _mints_an_identifier("cam_createcontact")
    assert _mints_an_identifier("cam_createscheme")
    assert _mints_an_identifier("cam_createbeneficiaries")
    assert not _mints_an_identifier("cam_getscheme")
    assert not _mints_an_identifier("cam_updatecontribution")
    assert not _mints_an_identifier("contacts")
    assert not _mints_an_identifier(None)


class TestTheOperationName:
    """`operation` is the audit trail's DOMAIN VERB — what the log groups by, what the outbound
    console's tabs match on, and what an operator filters by during an incident.

    It has to be the action, not the action plus whatever arguments this particular call carried.
    Observed live: OData reads landed as `cam_schemes?$select=cam_name`, so one entity set became
    a distinct "operation" per projection — every count over it fragments, and the exact-match
    filter behind `platform_entities`' outbound tabs misses the call altogether.
    """

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("cam_createcontact", "cam_createcontact"),
            ("/cam_createcontact", "cam_createcontact"),
            # The two shapes that carry arguments.
            ("cam_schemes?$select=cam_name", "cam_schemes"),
            ("cam_schemes?$select=cam_name&$top=100", "cam_schemes"),
            ("contacts(fcdab33b-0000-0000-0000-000000000000)", "contacts"),
            # Both at once: a keyed read with an expansion.
            ("contacts(fcdab33b-1111)?$expand=cam_schemes", "contacts"),
        ],
    )
    def test_arguments_are_stripped(self, path: str, expected: str) -> None:
        assert _operation_of(path) == expected

    def test_an_empty_path_records_no_operation(self) -> None:
        """None, not "": the column is nullable and a blank string would sort and group as a
        real value."""
        assert _operation_of("/") is None


class TestTokenFailures:
    """Azure refusing to issue a token is its own outcome, and it used to be neither typed nor
    recorded.

    `_fetch_token` called `raise_for_status()`, so a refusal escaped as a raw
    `httpx.HTTPStatusError` — past every caller's `except MorganLloydError` — and surfaced to an
    operator as a 500 with a stack trace. It was also invisible in `audit.api_log`, the gap
    `docs/provider-routing.md` section 6 lists as known: the hook wrapped the REQUEST, so "we
    could not authenticate at 3am" appeared nowhere.

    Observed live 09 Aug 2026 with correct credentials: `AADSTS53003 Access has been blocked by
    Conditional Access policies` — a directory-side policy, not a wrong secret, and telling those
    two apart is the entire value of carrying the message.
    """

    def test_the_aad_reason_is_the_first_line(self) -> None:
        response = httpx.Response(
            400,
            json={
                "error": "interaction_required",
                "error_description": (
                    "AADSTS53003: Access has been blocked by Conditional Access policies. "
                    "The access policy does not allow token issuance.\r\n"
                    "Trace ID: 1683a78e-babc-40da-b7b5-34f664892d00\r\n"
                    "Correlation ID: ff5affa6-385d-4901-883f-39e94d60a67a"
                ),
            },
        )
        reason = _aad_failure(response)
        assert reason.startswith("AADSTS53003")
        assert "Trace ID" not in reason, "the trace ids are for a support ticket, not a banner"

    def test_an_invalid_secret_is_distinguishable_from_a_policy_block(self) -> None:
        """The two failures need completely different actions — rotate a credential, or ask
        Clifton about a policy — and `400 Bad Request` says neither."""
        response = httpx.Response(
            400,
            json={
                "error": "invalid_client",
                "error_description": "AADSTS7000215: Invalid client secret provided.",
            },
        )
        assert "AADSTS7000215" in _aad_failure(response)

    def test_a_non_json_refusal_still_says_something(self) -> None:
        response = httpx.Response(502, text="<html>gateway</html>")
        assert "502" in _aad_failure(response)

    def test_a_json_body_with_no_description_falls_back_to_the_code(self) -> None:
        response = httpx.Response(400, json={"error": "unauthorized_client"})
        assert "unauthorized_client" in _aad_failure(response)

    @pytest.mark.asyncio
    async def test_a_refused_token_raises_the_typed_error_and_is_recorded(self) -> None:
        """Both halves at once: the caller gets a `MorganLloydError` it can catch, and the
        attempt lands in the audit trail as a failure."""
        seen: list[ApiCall] = []

        def transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400, json={"error_description": "AADSTS53003: Access has been blocked."}
            )

        client = BaseClient(
            settings=MorganLloydSettings(
                tenant_id="t", client_id="c", client_secret="s", resource_url="https://x"
            ),
            recorder=seen.append,
        )
        async with client:
            client._http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
            with pytest.raises(MorganLloydAuthError, match="AADSTS53003"):
                await client._fetch_token()

        assert len(seen) == 1
        assert seen[0].operation == "oauth2_token"
        assert seen[0].ok is False
        assert "AADSTS53003" in (seen[0].error or "")

    @pytest.mark.asyncio
    async def test_the_credential_is_never_recorded(self) -> None:
        """THE RULE. The token exchange's body IS the client secret, so the recorded call carries
        the outcome and no request at all — which is also why this is not routed through
        `_request`, whose job is to record what it sent."""
        seen: list[ApiCall] = []

        def transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error_description": "AADSTS7000215: Invalid."})

        client = BaseClient(
            settings=MorganLloydSettings(
                tenant_id="t", client_id="c", client_secret="super-secret", resource_url="https://x"
            ),
            recorder=seen.append,
        )
        async with client:
            client._http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
            with pytest.raises(MorganLloydAuthError):
                await client._fetch_token()

        recorded = repr(seen[0])
        assert "super-secret" not in recorded
        assert seen[0].request is None

    @pytest.mark.asyncio
    async def test_an_unreachable_directory_is_also_typed(self) -> None:
        """A DNS or TLS failure reaching login.microsoftonline.com is the same fact for a caller:
        nothing was sent."""

        def transport(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        client = BaseClient(
            settings=MorganLloydSettings(
                tenant_id="t", client_id="c", client_secret="s", resource_url="https://x"
            )
        )
        async with client:
            client._http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
            with pytest.raises(MorganLloydAuthError, match="could not reach Azure AD"):
                await client._fetch_token()
