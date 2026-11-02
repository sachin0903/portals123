"""OAuth2 token grant + adviser read, mocked at the transport with `httpx.MockTransport` --
no `respx`, matching this codebase's own convention (see
`pkgs/morgan-lloyd/tests/test_client.py`)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

import intelliflo.client as client
from intelliflo.settings import IntellifloSettings


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """The token cache is process-wide (module-level), so tests must not leak into each other."""
    client.clear_token_cache()


def _settings(**overrides: Any) -> IntellifloSettings:
    defaults: dict[str, Any] = {
        "client_id": "cid",
        "client_secret": "s3cret",
        "scope": "adviser.read",
        "tenant_id": "tid",
        "base_url": "https://api.intelliflo.test",
        "api_key": "key-1",
        "token_url": "https://identity.test/core/connect/token",
    }
    defaults.update(overrides)
    return IntellifloSettings(**defaults)


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Substitute the one place `client.py` builds an `httpx.AsyncClient` with one wired to a
    `MockTransport`, rather than patching the `httpx` module globally."""

    def _make(settings: IntellifloSettings) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=settings.timeout)

    monkeypatch.setattr(client, "_http_client", _make)


def _token_response(token: str = "tok-1", expires_in: int = 3600) -> httpx.Response:  # noqa: S107 - a test fixture value, not a credential
    return httpx.Response(200, json={"access_token": token, "expires_in": expires_in})


async def test_token_fetched_once_and_reused_across_two_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            token_calls["n"] += 1
            return _token_response()
        assert request.headers.get("Authorization") == "Bearer tok-1"
        return httpx.Response(200, json={"items": []})

    _patch_transport(monkeypatch, handler)
    settings = _settings()

    await client.get_advisers(settings=settings)
    await client.get_advisers(settings=settings)

    assert token_calls["n"] == 1


async def test_401_triggers_exactly_one_forced_refresh_and_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = iter(["tok-old", "tok-new"])
    token_calls = {"n": 0}
    adviser_auth_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            token_calls["n"] += 1
            return _token_response(next(tokens))
        auth = request.headers.get("Authorization")
        adviser_auth_headers.append(auth)
        if auth == "Bearer tok-old":
            return httpx.Response(401)
        return httpx.Response(200, json={"items": [{"id": "a1"}]})

    _patch_transport(monkeypatch, handler)

    result = await client.get_advisers(settings=_settings())

    assert result == [{"id": "a1"}]
    assert adviser_auth_headers == ["Bearer tok-old", "Bearer tok-new"]
    assert token_calls["n"] == 2  # one initial grant, one forced refresh -- never a second retry


async def test_first_name_with_a_quote_round_trips_into_the_escaped_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json={"items": []})

    _patch_transport(monkeypatch, handler)

    await client.get_advisers("O'Brien", settings=_settings())

    # The literal `'` is doubled (the OData string-literal escape) and THEN URL-quoted, so it
    # never reaches the wire unescaped -- the legacy bug this client fixes.
    assert "O%27%27Brien" in seen["query"]
    assert "filter=person.firstName" in seen["query"]


async def test_no_first_name_produces_the_page_size_100_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"items": []})

    _patch_transport(monkeypatch, handler)

    await client.get_advisers(settings=_settings())

    assert seen["url"] == "https://api.intelliflo.test/advisers?page.size=100"


async def test_response_without_items_key_falls_back_to_wrapping_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        return httpx.Response(200, json={"id": "a1", "name": "Solo Adviser"})

    _patch_transport(monkeypatch, handler)

    result = await client.get_advisers(settings=_settings())

    assert result == [{"id": "a1", "name": "Solo Adviser"}]


async def test_response_that_is_already_a_list_is_returned_as_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        return httpx.Response(200, json=[{"id": "a1"}, {"id": "a2"}])

    _patch_transport(monkeypatch, handler)

    result = await client.get_advisers(settings=_settings())

    assert result == [{"id": "a1"}, {"id": "a2"}]


async def test_non_200_token_response_raises_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return httpx.Response(400, json={"error": "invalid_client"})
        raise AssertionError("adviser endpoint must not be reached without a token")

    _patch_transport(monkeypatch, handler)

    with pytest.raises(client.IntellifloAuthError, match="400"):
        await client.get_advisers(settings=_settings())


async def test_token_error_body_is_parsed_into_the_exception_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain '400 Bad Request' never says WHY -- the OAuth error/error_description (RFC 6749
    §5.2) must reach the exception message, since that's the only place an operator can see it
    (a bad client_id/secret is `invalid_client`, a bad scope is `invalid_scope`, etc.)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return httpx.Response(
                400,
                json={"error": "invalid_scope", "error_description": "scope not recognised"},
            )
        raise AssertionError("adviser endpoint must not be reached without a token")

    _patch_transport(monkeypatch, handler)

    with pytest.raises(client.IntellifloAuthError, match="invalid_scope: scope not recognised"):
        await client.get_advisers(settings=_settings())


async def test_token_error_falls_back_to_raw_text_when_not_oauth_shaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not every failure is a clean OAuth error body -- a gateway/HTML error page must still
    surface something, not raise a second error trying to parse it as one."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return httpx.Response(502, text="<html>Bad Gateway</html>")
        raise AssertionError("adviser endpoint must not be reached without a token")

    _patch_transport(monkeypatch, handler)

    with pytest.raises(client.IntellifloAuthError, match="Bad Gateway"):
        await client.get_advisers(settings=_settings())


async def test_non_200_advisers_response_raises_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        return httpx.Response(500, text="upstream down")

    _patch_transport(monkeypatch, handler)

    with pytest.raises(client.IntellifloApiError, match="500"):
        await client.get_advisers(settings=_settings())


# ============================================================================
# get_clients / "Link to IO" search
# ============================================================================


async def test_clients_search_with_every_field_builds_the_specified_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"items": []})

    _patch_transport(monkeypatch, handler)

    await client.get_clients(
        first_name="John",
        last_name="Smith",
        date_of_birth="1980-01-31",
        ni_number="AB123456C",
        settings=_settings(),
    )

    assert seen["url"] == (
        "https://api.intelliflo.test/clients?filter="
        "person.firstName%20eq%20'John'%20and%20person.lastName%20eq%20'Smith'"
        "%20and%20person.dateOfBirth%20eq%20'1980-01-31'"
        "%20and%20person.niNumber%20eq%20'AB123456C'"
    )


async def test_changing_just_the_ni_number_changes_the_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinned against a real regression: an earlier version put date of birth and NI number in
    their own bare `&`-joined segments with no `key=` in front, which isn't a query parameter at
    all (a standard parser reads the whole fragment as one key with an empty value) -- so
    Intelliflo never saw it and a second search that only changed the NI number came back with the
    same results as the first, name-only one. The fix folds every typed field into the one
    `filter=` clause, so two searches that differ only in NI number must produce different URLs."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        seen.append(str(request.url))
        return httpx.Response(200, json={"items": []})

    _patch_transport(monkeypatch, handler)

    await client.get_clients(first_name="Test", settings=_settings())
    await client.get_clients(ni_number="WM356171D", settings=_settings())

    assert seen[0] != seen[1]
    assert "niNumber" in seen[1] and "WM356171D" in seen[1]
    assert "filter=" in seen[1]  # a real query param, not a bare trailing fragment


async def test_clients_search_omits_blank_fields_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"items": []})

    _patch_transport(monkeypatch, handler)

    await client.get_clients(ni_number="WM356171D", settings=_settings())

    assert (
        seen["url"]
        == "https://api.intelliflo.test/clients?filter=person.niNumber%20eq%20'WM356171D'"
    )


async def test_clients_search_with_nothing_typed_hits_the_bare_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"items": []})

    _patch_transport(monkeypatch, handler)

    await client.get_clients(settings=_settings())

    assert seen["url"] == "https://api.intelliflo.test/clients"


async def test_a_name_with_an_apostrophe_is_never_put_in_the_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Intelliflo answers the OData quote-doubling (`'O''Client'`) with 400 "Invalid query string
    filter", so an apostrophe stays out of the query: the server gets the prefix before it."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json={"items": []})

    _patch_transport(monkeypatch, handler)

    await client.get_clients(first_name="Mark", last_name="O'Client", settings=_settings())

    assert "%27%27" not in seen["query"]
    assert "O%27Client" not in seen["query"]
    assert seen["query"] == (
        "filter=person.firstName%20eq%20'Mark'%20and%20person.lastName%20startswith%20'O'"
    )


async def test_a_name_with_an_apostrophe_is_matched_exactly_on_what_comes_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    people = [
        {"id": "1", "person": {"firstName": "Mark", "lastName": "O'Client"}},
        {"id": "2", "person": {"firstName": "Mark", "lastName": "O\u2019Client"}},  # typographic
        {"id": "3", "person": {"firstName": "Mark", "lastName": "Ohara"}},  # same prefix, not him
        {"id": "4", "person": {"firstName": "Mark", "lastName": "O'Clientele"}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        return httpx.Response(200, json={"items": people})

    _patch_transport(monkeypatch, handler)

    found = await client.get_clients(first_name="Mark", last_name="O'Client", settings=_settings())

    assert [c["id"] for c in found] == ["1", "2"]


async def test_a_search_that_is_only_an_opening_apostrophe_is_not_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No filter could be built, and an unfiltered request is the whole directory."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call Intelliflo with nothing to filter on")

    _patch_transport(monkeypatch, handler)

    assert await client.get_clients(last_name="'Client", settings=_settings()) == []


async def test_a_name_without_an_apostrophe_still_filters_on_equality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json={"items": [{"id": "1", "person": {"lastName": "Smith"}}]})

    _patch_transport(monkeypatch, handler)

    found = await client.get_clients(first_name="Mark", last_name="Smith", settings=_settings())

    assert seen["query"] == (
        "filter=person.firstName%20eq%20'Mark'%20and%20person.lastName%20eq%20'Smith'"
    )
    assert [c["id"] for c in found] == ["1"]  # no local filtering when nothing was quoted


async def test_clients_search_returns_the_items(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        return httpx.Response(200, json={"items": [{"id": "c1", "name": "John Smith"}]})

    _patch_transport(monkeypatch, handler)

    result = await client.get_clients(first_name="John", settings=_settings())

    assert result == [{"id": "c1", "name": "John Smith"}]


async def test_non_200_clients_response_raises_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        return httpx.Response(500, text="upstream down")

    _patch_transport(monkeypatch, handler)

    with pytest.raises(client.IntellifloApiError, match="500"):
        await client.get_clients(first_name="John", settings=_settings())


_NI_NUMBER = "AB123456C"
_DATE_OF_BIRTH = "1980-01-01"


def _no_log_record_leaks_the_search(caplog: pytest.LogCaptureFixture) -> None:
    """None of the search's own values -- raw, `quote`-encoded (a space is `%20`), or the filter
    syntax that would prove a URL was logged -- appear in any record THIS PACKAGE'S OWN logger
    produced (`intelliflo.client` -- never httpx's own request-logging line, which this library
    does not own or configure; silencing that is `apps/api/app/logging.py::configure_logging`'s
    job, pinned separately in `apps/api/tests/test_logging.py`). These logs reach a shared,
    cross-tenant operator stream, not just the one adviser who is allowed to see this client (see
    `_get_list`'s own docstring)."""
    ours = [r for r in caplog.records if r.name == "intelliflo.client"]
    assert ours, "expected at least one log record from intelliflo.client"
    for record in ours:
        text = record.getMessage()
        assert _NI_NUMBER not in text
        assert "Jane" not in text
        assert "Doe" not in text
        assert _DATE_OF_BIRTH not in text
        assert "niNumber" not in text  # the OData filter field name -- proves no filter leaked
        assert "%20" not in text  # `quote`-encoded space -- proves no URL leaked
        assert "filter=" not in text


async def test_clients_search_success_does_not_log_the_filter_or_url(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        return httpx.Response(200, json={"items": [{"id": "c1"}]})

    _patch_transport(monkeypatch, handler)

    with caplog.at_level("DEBUG"):
        await client.get_clients(
            first_name="Jane",
            last_name="Doe",
            date_of_birth=_DATE_OF_BIRTH,
            ni_number=_NI_NUMBER,
            settings=_settings(),
        )

    _no_log_record_leaks_the_search(caplog)


async def test_clients_search_failure_does_not_log_the_filter_or_url(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        return httpx.Response(500, json={"error": "server_error"})

    _patch_transport(monkeypatch, handler)

    with caplog.at_level("DEBUG"), pytest.raises(client.IntellifloApiError):
        await client.get_clients(
            first_name="Jane",
            last_name="Doe",
            date_of_birth=_DATE_OF_BIRTH,
            ni_number=_NI_NUMBER,
            settings=_settings(),
        )

    _no_log_record_leaks_the_search(caplog)
    # The failure log still names the operation and status, and now a redacted vendor error
    # code -- it is not reduced to nothing, just stripped of anything that could be request data.
    warning = next(r for r in caplog.records if r.levelname == "WARNING")
    assert "clients" in warning.getMessage()
    assert "500" in warning.getMessage()
    assert "server_error" in warning.getMessage()


# ============================================================================
# get_client_plans / the account record's "IO Plans" dropdown
# ============================================================================


async def test_client_plans_hits_the_scoped_url(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"items": []})

    _patch_transport(monkeypatch, handler)

    await client.get_client_plans("io-42", settings=_settings())

    assert seen["url"] == "https://api.intelliflo.test/clients/io-42/plans"


async def test_client_plans_returns_the_items(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        return httpx.Response(200, json={"items": [{"id": "p1", "accountNumber": "ACC-001"}]})

    _patch_transport(monkeypatch, handler)

    result = await client.get_client_plans("io-42", settings=_settings())

    assert result == [{"id": "p1", "accountNumber": "ACC-001"}]


async def test_non_200_client_plans_response_raises_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        return httpx.Response(500, text="upstream down")

    _patch_transport(monkeypatch, handler)

    with pytest.raises(client.IntellifloApiError, match="500"):
        await client.get_client_plans("io-42", settings=_settings())


# ============================================================================
# update_plan_policy_number / the account record's "IO Plans" Link write-back
# ============================================================================


async def test_update_plan_policy_number_sends_the_bare_json_patch_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["content_type"] = request.headers.get("Content-Type")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    _patch_transport(monkeypatch, handler)

    await client.update_plan_policy_number("io-42", "p1", "ACC-001", settings=_settings())

    assert seen["url"] == "https://api.intelliflo.test/clients/io-42/plans/p1"
    assert seen["method"] == "PATCH"
    assert seen["content_type"] == "application/json-patch+json"
    # A BARE array, not wrapped in an envelope -- the shape that actually parses.
    assert seen["body"] == [{"op": "replace", "path": "/policyNumber", "value": "ACC-001"}]


async def test_update_plan_policy_number_accepts_a_204(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        return httpx.Response(204)

    _patch_transport(monkeypatch, handler)

    await client.update_plan_policy_number(
        "io-42", "p1", "ACC-001", settings=_settings()
    )  # must not raise


async def test_non_200_update_plan_policy_number_raises_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        return httpx.Response(
            400, json={"field": "", "message": "The JSON patch document was malformed"}
        )

    _patch_transport(monkeypatch, handler)

    with pytest.raises(client.IntellifloApiError, match="400"):
        await client.update_plan_policy_number("io-42", "p1", "ACC-001", settings=_settings())


@pytest.mark.asyncio
async def test_get_clients_filters_a_company_by_its_corporate_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A company lives under `corporate.name`, never `person`: a person filter cannot match it."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"items": []})

    _patch_transport(monkeypatch, handler)

    await client.get_clients(corporate_name="Daniscan O'Services Limited", settings=_settings())

    assert "corporate.name" in seen["url"] and "person." not in seen["url"]
    # The apostrophe never reaches the query (no doubling either): the company is asked for by the
    # text before it, and the exact name is checked on what comes back.
    assert "%27%27" not in seen["url"]
    assert "corporate.name%20startswith%20'Daniscan%20O'" in seen["url"]


# ----------------------------------------------------------------------------------------------
# A transport failure (timeout, refused or dropped connection) is an IntellifloApiError
# ----------------------------------------------------------------------------------------------


def _transport_fails(exc: Exception) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/connect/token":
            return _token_response()
        raise exc

    return handler


_TRANSPORT_FAILURES = [
    httpx.ReadTimeout("timed out"),
    httpx.ConnectTimeout("timed out"),
    httpx.ConnectError("connection refused"),
    httpx.RemoteProtocolError("server disconnected"),
]


@pytest.mark.parametrize("failure", _TRANSPORT_FAILURES, ids=lambda f: type(f).__name__)
async def test_a_transport_failure_on_a_read_is_an_api_error_not_a_raw_httpx_one(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    """Every caller catches IntellifloApiError (the account page degrades to an empty plans
    dropdown on it); a bare httpx error used to sail past them as a 500."""
    _patch_transport(monkeypatch, _transport_fails(failure))

    with pytest.raises(client.IntellifloApiError):
        await client.get_client_plans("io-42", settings=_settings())
    with pytest.raises(client.IntellifloApiError):
        await client.get_clients(first_name="Jane", settings=_settings())


@pytest.mark.parametrize("failure", _TRANSPORT_FAILURES, ids=lambda f: type(f).__name__)
async def test_a_transport_failure_on_the_plan_patch_is_an_api_error(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    _patch_transport(monkeypatch, _transport_fails(failure))

    with pytest.raises(client.IntellifloApiError):
        await client.update_plan_policy_number("io-42", "p-9", "MSP001", settings=_settings())


async def test_a_transport_failure_does_not_leak_the_search_into_the_error_or_the_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The client search URL carries a name, date of birth and NI number; the error text and the
    log line name only the exception type."""
    _patch_transport(
        monkeypatch, _transport_fails(httpx.ReadTimeout("GET https://x/clients?filter=niNumber"))
    )

    with caplog.at_level("DEBUG"), pytest.raises(client.IntellifloApiError) as raised:
        await client.get_clients(first_name="Jane", ni_number=_NI_NUMBER, settings=_settings())

    seen = [str(raised.value), *(r.getMessage() for r in caplog.records)]
    assert any("ReadTimeout" in text for text in seen)
    for text in seen:
        assert _NI_NUMBER not in text and "Jane" not in text and "filter=" not in text
        assert "niNumber" not in text and "https://x" not in text


async def test_a_repeated_directory_read_is_answered_from_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return _token_response()
        calls.append(request.url.path)
        return httpx.Response(200, json={"items": [{"id": 1}]})

    _patch_transport(monkeypatch, handler)
    s = _settings()
    first = await client.get_client_plans("42", settings=s)
    first[0]["id"] = 999  # a caller changing its copy must not change the cache
    second = await client.get_client_plans("42", settings=s)
    assert second == [{"id": 1}] and len(calls) == 1


async def test_a_plan_write_drops_that_clients_cached_plans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return _token_response()
        if request.method == "PATCH":
            return httpx.Response(204)
        reads.append(request.url.path)
        return httpx.Response(200, json={"items": []})

    _patch_transport(monkeypatch, handler)
    s = _settings()
    await client.get_client_plans("42", settings=s)
    await client.update_plan_policy_number("42", "7", "POL1", settings=s)
    await client.get_client_plans("42", settings=s)
    assert len(reads) == 2


async def test_the_shared_connection_pool_is_reused_and_closable() -> None:
    s = _settings()
    async with client._http_client(s) as a:
        pass
    async with client._http_client(s) as b:
        assert b is a and not b.is_closed
    await client.aclose_shared_client()
    assert a.is_closed
