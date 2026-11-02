"""Transport: OAuth2 client-credentials token grant (cached, refreshed on 401) and the
adviser-directory read behind the SEI/Intelliflo Office advisor-identity mapping screen."""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Any
from urllib.parse import quote

import httpx

from intelliflo.settings import IntellifloSettings

logger = logging.getLogger(__name__)

_REFRESH_MARGIN_S = 60  # re-auth this long before the token expires

# Process-wide token cache, keyed by connection identity (token_url, client_id) -- distinct
# configured tenants don't collide, and every caller in the process shares one grant instead of
# fetching a fresh token per request the way the legacy Next.js version did.
_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_token_lock = asyncio.Lock()


class IntellifloError(Exception):
    """Base for all Intelliflo client errors."""


class IntellifloAuthError(IntellifloError):
    """Token issuance failed -- Intelliflo's identity server was never reached, or refused the
    client-credentials grant.

    Typed separately from `IntellifloApiError` because it is a different fact: this means no
    adviser request was ever attempted, where an `IntellifloApiError` means Intelliflo heard us
    and refused.
    """


class IntellifloApiError(IntellifloError):
    """A request reached Intelliflo and it refused or failed it."""


def clear_token_cache() -> None:
    """Drop all cached tokens (tests; or after rotating a client secret)."""
    _TOKEN_CACHE.clear()
    clear_read_cache()


def _response_detail(response: httpx.Response) -> str:
    """The specific reason Intelliflo gave, not just the status code.

    A plain access-log line (``POST .../token 400 Bad Request``) never shows WHY -- Intelliflo's
    OAuth errors carry `{"error": "...", "error_description": "..."}` (RFC 6749 §5.2: wrong
    client_id/client_secret is `invalid_client`, a scope it doesn't recognise is `invalid_scope`,
    and so on). Fall back to the raw body when it isn't that shape (an HTML error page, an empty
    body) rather than raising on our own error-reporting path.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(body, dict) and ("error" in body or "error_description" in body):
        error = body.get("error", "?")
        description = body.get("error_description")
        return f"{error}: {description}" if description else str(error)
    return response.text[:500]


def _response_error_code(response: httpx.Response) -> str:
    """The vendor's own short error CODE only -- never `error_description` or the raw body, either
    of which could echo request content straight back (an OData filter literal, a validation
    message quoting the submitted value) into whatever reads this. For a log line specifically,
    which reaches a shared, cross-tenant stream (unlike `_response_detail`'s fuller text, which
    only ever reaches the caller who made the request as an error message): redacted on purpose,
    even at the cost of being less informative than `_response_detail`.
    """
    try:
        body = response.json()
    except ValueError:
        return "(unparseable body)"
    if isinstance(body, dict) and "error" in body:
        return str(body["error"])
    return "(no error code)"


class _Borrowed:
    """The shared client as `async with` hands it out: leaving the block does NOT close it."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._http

    async def __aexit__(self, *_exc: object) -> None:
        return None


_shared: tuple[httpx.AsyncClient, asyncio.AbstractEventLoop, float] | None = None


def _http_client(settings: IntellifloSettings) -> _Borrowed:
    """The one place the HTTP client is obtained, so tests can substitute a `MockTransport` here
    instead of patching the `httpx` module globally.

    One client is kept per process (and per event loop and timeout), so connections to Intelliflo
    are reused: a fresh client per call paid a TCP and TLS handshake every time, which is most of
    what an admin dropdown waited on. Callers still write `async with _http_client(s) as http`;
    leaving the block leaves the pool open. `aclose_shared_client` closes it on shutdown."""
    global _shared  # noqa: PLW0603 - a process-wide connection pool
    loop = asyncio.get_running_loop()
    if _shared is None or _shared[1] is not loop or _shared[2] != settings.timeout:
        _shared = (httpx.AsyncClient(timeout=settings.timeout), loop, settings.timeout)
    return _Borrowed(_shared[0])


async def aclose_shared_client() -> None:
    """Close the shared connection pool (API shutdown; tests)."""
    global _shared  # noqa: PLW0603
    if _shared is not None:
        http, _, _ = _shared
        _shared = None
        await http.aclose()


# Directory reads (client search, a client's plans) are what the admin dropdowns wait on, and
# the same read is repeated: every open of a dropdown, every row of a client. A short cache
# answers repeats without Intelliflo. It is per process and in memory only (the keys carry search
# terms and are never logged), bounded, and copied on the way out so a caller cannot change what is
# cached. A write that changes a client's plans drops that client's entry; anything else is at
# most `_READ_TTL_S` stale.
_READ_TTL_S = 60.0
_READ_CACHE_MAX = 256
_READ_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _cached(key: str) -> list[dict[str, Any]] | None:
    hit = _READ_CACHE.get(key)
    if hit is None or time.monotonic() >= hit[0]:
        _READ_CACHE.pop(key, None)
        return None
    return copy.deepcopy(hit[1])


def _remember(key: str, value: list[dict[str, Any]]) -> None:
    if len(_READ_CACHE) >= _READ_CACHE_MAX:
        _READ_CACHE.pop(next(iter(_READ_CACHE)), None)  # oldest inserted
    _READ_CACHE[key] = (time.monotonic() + _READ_TTL_S, copy.deepcopy(value))


def clear_read_cache() -> None:
    """Drop every cached directory read (tests)."""
    _READ_CACHE.clear()


async def _fetch_token(settings: IntellifloSettings) -> tuple[str, float]:
    """POST the (non-standard -- see `IntellifloSettings.grant_type`) client-credentials grant.
    Raises `IntellifloAuthError` on any non-200 response, else returns
    ``(access_token, expires_in)``.

    `tenant_id` is deliberately NOT sent here -- confirmed against Intelliflo's own working curl
    example, the token grant takes only `grant_type`/`scope`; `settings.tenant_id` exists for
    whichever other call needs it (or for logging), not this one."""
    form_body = {
        "grant_type": settings.grant_type,
        "scope": settings.scope,
    }
    # The full form body -- safe to log whole: client_id/client_secret never go here, only in the
    # Authorization header below (logged separately, and only as "present", never the value).
    logger.info(
        "Intelliflo token request -> %s body=%r auth_client_id=%r has_client_secret=%s",
        settings.token_url,
        form_body,
        settings.client_id,
        bool(settings.client_secret),
    )
    async with _http_client(settings) as http:
        try:
            response = await http.post(
                settings.token_url,
                headers={
                    "Authorization": f"Basic {settings.basic_auth}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data=form_body,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "Intelliflo token request could not reach %s: %s", settings.token_url, exc
            )
            raise IntellifloAuthError(f"could not reach Intelliflo: {exc}") from exc

    if response.status_code != httpx.codes.OK:
        detail = _response_detail(response)
        logger.warning(
            "Intelliflo token request failed (%s) for client_id=%r tenant_id=%r: %s",
            response.status_code,
            settings.client_id,
            settings.tenant_id,
            detail,
        )
        raise IntellifloAuthError(f"token request failed: {response.status_code} {detail}")
    body: Any = response.json()
    # The vendor's docs don't guarantee `expires_in` is always sent; default to the grant's own
    # advertised 3600s rather than refusing outright over a missing, non-essential field.
    return str(body["access_token"]), float(body.get("expires_in", 3600))


async def _access_token(settings: IntellifloSettings, *, force: bool = False) -> str:
    """Serve the cached token unless it is expired (or `force=True`), else fetch a new one."""
    key = (settings.token_url, settings.client_id)
    async with _token_lock:
        cached = None if force else _TOKEN_CACHE.get(key)
        if cached is not None and time.monotonic() < cached[1]:
            return cached[0]
        token, expires_in = await _fetch_token(settings)
        _TOKEN_CACHE[key] = (token, time.monotonic() + expires_in - _REFRESH_MARGIN_S)
        return token


def _advisers_url(settings: IntellifloSettings, first_name: str | None) -> str:
    base = settings.base_url.rstrip("/")
    if first_name is not None:
        # `'`-doubling is the OData string-literal escape; `quote` then URL-encodes the result
        # (each `'` becomes `%27%27`). This FIXES the legacy Next.js version, which interpolated
        # the raw first name straight into the OData filter unescaped -- an injection/breakage
        # risk for any name containing a quote (e.g. "O'Brien").
        escaped = quote(first_name.replace("'", "''"))
        return f"{base}/advisers?filter=person.firstName startswith '{escaped}'"
    # No pagination past 100 rows -- a known, deliberately-kept limitation ported from the
    # legacy version, not built out further: there is no live Intelliflo sandbox to verify
    # deeper pagination against.
    return f"{base}/advisers?page.size=100"


def _odata_literal(value: str) -> str:
    """`'`-doubling (the OData string-literal escape) then URL-quoting -- same fix as
    `_advisers_url`'s first name, applied everywhere a raw value is embedded in a filter."""
    return quote(value.replace("'", "''"))


# An apostrophe in a name ("O'Client") cannot be sent in the filter: Intelliflo answers the OData
# quote-doubling (`'O''Client'`) with `400 Invalid query string filter`, so it is kept OUT of the
# query. The server is asked for names that START with the text before the apostrophe
# (`startswith`), and the exact name is checked here on what comes back. Both the straight (')
# and the typographic (U+2019) apostrophe count, because the directory may hold either.
_APOSTROPHES = ("'", "\u2019")
_NAME_FIELDS = ("first_name", "last_name", "corporate_name")


def _has_apostrophe(value: str) -> bool:
    return any(mark in value for mark in _APOSTROPHES)


def _before_apostrophe(value: str) -> str:
    cut = min((value.index(mark) for mark in _APOSTROPHES if mark in value), default=len(value))
    return value[:cut]


def _fold(value: object) -> str:
    """A name as compared locally: typographic apostrophe folded to a straight one, case ignored."""
    return str(value or "").replace("\u2019", "'").strip().casefold()


def _is_exact_name_match(found: dict[str, Any], quoted: dict[str, str]) -> bool:
    """Whether a returned client carries exactly the quoted names that were searched for."""
    person = found.get("person") or {}
    corporate = found.get("corporate") or {}
    actual = {
        "first_name": person.get("firstName"),
        "last_name": person.get("lastName"),
        "corporate_name": corporate.get("name") or found.get("name"),
    }
    return all(_fold(actual[field]) == _fold(value) for field, value in quoted.items())


def _clients_url(
    settings: IntellifloSettings,
    *,
    first_name: str | None,
    last_name: str | None,
    date_of_birth: str | None,
    ni_number: str | None,
    corporate_name: str | None = None,
    prefix_only: frozenset[str] = frozenset(),
) -> str:
    """The "Link to IO" search box's fields, as whichever of them were actually typed.

    `corporate_name` is a company client's name: IO keeps those under `corporate.name`, not under
    `person`, so a person filter can never match one.

    ALL FOUR AND TOGETHER INTO ONE `filter=` CLAUSE -- the only form that is actually a query
    parameter. An earlier version put date of birth and NI number in their own bare `&`-joined
    segments (`&person.niNumber eq '...'`, no `key=` in front), which is not a query parameter at
    all: a standard parser reads that whole fragment as one key with an empty value, so the
    server never saw it and silently kept filtering on name alone -- reproduced as "changing the
    NI number doesn't narrow the results". A blank field is left out of the filter entirely rather
    than sent empty.
    """
    base = settings.base_url.rstrip("/")
    clauses = []

    def name_clause(field: str, path: str, value: str | None) -> None:
        # A name with an apostrophe is asked for by its prefix (`startswith`) -- see _APOSTROPHES.
        if not value:
            return
        if field in prefix_only:
            prefix = _before_apostrophe(value)
            if prefix:
                clauses.append(f"{path} startswith '{_odata_literal(prefix)}'")
            return
        clauses.append(f"{path} eq '{_odata_literal(value)}'")

    name_clause("first_name", "person.firstName", first_name)
    name_clause("last_name", "person.lastName", last_name)
    if date_of_birth:
        clauses.append(f"person.dateOfBirth eq '{_odata_literal(date_of_birth)}'")
    if ni_number:
        clauses.append(f"person.niNumber eq '{_odata_literal(ni_number)}'")
    name_clause("corporate_name", "corporate.name", corporate_name)

    if not clauses:
        return f"{base}/clients"
    return f"{base}/clients?filter=" + " and ".join(clauses)


def _unreachable(method: str, exc: httpx.HTTPError) -> IntellifloApiError:
    """A transport failure (timeout, refused or dropped connection) as the API error every caller
    already handles. Names only the exception TYPE: neither the URL nor `str(exc)` goes in, because
    a client search URL carries a name, date of birth or NI number and these messages are logged."""
    logger.warning("Intelliflo %s could not reach the API: %s", method, type(exc).__name__)
    return IntellifloApiError(f"{method} request failed: {type(exc).__name__}")


async def _get_once(settings: IntellifloSettings, url: str, token: str) -> httpx.Response:
    async with _http_client(settings) as http:
        try:
            return await http.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-api-key": settings.api_key,
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise _unreachable("GET", exc) from exc


async def _get_list(settings: IntellifloSettings, url: str, *, what: str) -> list[dict[str, Any]]:
    """GET `url` as a directory read, retrying once on a stale token, and unwrap Intelliflo's
    `{"items": [...]}` envelope -- shared by `get_advisers` and `get_clients`, which differ only
    in which URL they build.

    A 401 forces exactly one token refresh and one retry of the same request -- the cached token
    can go stale (a rotated secret, clock skew against `_REFRESH_MARGIN_S`) without the caller
    ever seeing an auth error for it.

    Deliberately never logs `url` itself: `get_clients`' URL carries the "Link to IO" search
    box's OData filter -- name, date of birth, NI number -- `quote`-encoded but trivially
    reversible (a space is `%20`), and these logs land in a shared, cross-tenant operator stream,
    not scoped to the one adviser allowed to see that client. `what` plus the ambient
    `correlation_id` every record already carries (`app/logging.py`) is enough to find this
    request in context without writing out what was searched for.
    """
    logger.info("Intelliflo %s request", what)

    token = await _access_token(settings)
    response = await _get_once(settings, url, token)
    if response.status_code == httpx.codes.UNAUTHORIZED:
        token = await _access_token(settings, force=True)
        response = await _get_once(settings, url, token)

    if response.status_code != httpx.codes.OK:
        detail = _response_detail(response)
        logger.warning(
            "Intelliflo %s request failed (%s): %s",
            what,
            response.status_code,
            _response_error_code(response),
        )
        raise IntellifloApiError(f"GET {what} failed: {response.status_code} {detail}")

    body: Any = response.json()
    if isinstance(body, dict):
        items = body.get("items")
        if items is not None:
            logger.info(
                "Intelliflo %s response: %d item(s), keys=%r", what, len(items), list(body.keys())
            )
            return list(items)
        logger.info("Intelliflo %s response had no 'items' key, keys=%r", what, list(body.keys()))
        return [body]
    if isinstance(body, list):
        return list(body)
    return [body]


async def get_advisers(
    first_name: str | None = None, *, settings: IntellifloSettings | None = None
) -> list[dict[str, Any]]:
    """The SEI/Intelliflo Office adviser directory, optionally filtered by first name."""
    s = settings or IntellifloSettings()
    return await _get_list(s, _advisers_url(s, first_name), what="advisers")


async def get_clients(
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    date_of_birth: str | None = None,
    ni_number: str | None = None,
    corporate_name: str | None = None,
    settings: IntellifloSettings | None = None,
) -> list[dict[str, Any]]:
    """The Intelliflo Office client directory, filtered by whichever of first/last name, date of
    birth, NI number and company name the "Link to IO" search box was given. Backs the search step
    of linking a client record to its Intelliflo counterpart -- the matching and the write of the
    link itself are the caller's job."""
    s = settings or IntellifloSettings()
    names = {
        "first_name": first_name,
        "last_name": last_name,
        "corporate_name": corporate_name,
    }
    quoted = {field: value for field, value in names.items() if value and _has_apostrophe(value)}
    if quoted and not (
        date_of_birth
        or ni_number
        or any(
            (_before_apostrophe(value) if field in quoted else value)
            for field, value in names.items()
            if value
        )
    ):
        # Nothing but names that open with an apostrophe: no filter could be sent, and an unfiltered
        # request is the whole directory, so there is no search to make.
        return []
    url = _clients_url(
        s,
        first_name=first_name,
        last_name=last_name,
        date_of_birth=date_of_birth,
        ni_number=ni_number,
        corporate_name=corporate_name,
        prefix_only=frozenset(quoted),
    )
    cached = _cached(url)
    if cached is None:
        cached = await _get_list(s, url, what="clients")
        _remember(url, cached)
    if quoted:
        return [client for client in cached if _is_exact_name_match(client, quoted)]
    return cached


async def get_client_plans(
    client_id_io: str, *, settings: IntellifloSettings | None = None
) -> list[dict[str, Any]]:
    """This Intelliflo Office client's plans — the account record's "IO Plans" dropdown, scoped
    to whichever IO client this account's contact is linked to (platform.links)."""
    s = settings or IntellifloSettings()
    base = s.base_url.rstrip("/")
    url = f"{base}/clients/{quote(client_id_io)}/plans"
    cached = _cached(url)
    if cached is not None:
        return cached
    found = await _get_list(s, url, what="client plans")
    _remember(url, found)
    return found


async def _patch_once(
    settings: IntellifloSettings, url: str, token: str, body: list[dict[str, Any]]
) -> httpx.Response:
    async with _http_client(settings) as http:
        try:
            return await http.patch(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-api-key": settings.api_key,
                    "Accept": "application/json",
                    "Content-Type": "application/json-patch+json",
                },
            )
        except httpx.HTTPError as exc:
            raise _unreachable("PATCH", exc) from exc


async def update_plan_policy_number(
    client_id_io: str,
    plan_id_io: str,
    policy_number: str,
    *,
    settings: IntellifloSettings | None = None,
) -> None:
    """PATCH this Intelliflo plan's `policyNumber` -- the write side of the account record's "IO
    Plans" link: once an adviser picks a plan, this account's own custody account number becomes
    that plan's policy number in Intelliflo too, so both sides agree on the number an adviser
    would actually recognise.

    The plan endpoint is scoped under its owning client, same as the read (`get_client_plans`) --
    a bare `/plans/{id}` 403s. `client_id_io` is the same IO client id the dropdown's plans were
    fetched with, not re-derived here.

    The body is a bare RFC 6902 JSON Patch array, NOT wrapped in an envelope -- confirmed by
    reproducing Intelliflo's own "The JSON patch document was malformed and could not be parsed"
    error against a wrapped `{"Operation": {...}}` body (a Swagger component name, not an
    instruction to nest the request). Same 401-retry-once posture as the GET helpers.
    """
    s = settings or IntellifloSettings()
    base = s.base_url.rstrip("/")
    url = f"{base}/clients/{quote(client_id_io)}/plans/{quote(plan_id_io)}"
    body = [{"op": "replace", "path": "/policyNumber", "value": policy_number}]
    logger.info("Intelliflo plan PATCH -> %s", url)
    # The client's plans change with this write, whatever the outcome (a failure may have applied).
    _READ_CACHE.pop(f"{base}/clients/{quote(client_id_io)}/plans", None)

    token = await _access_token(s)
    response = await _patch_once(s, url, token, body)
    if response.status_code == httpx.codes.UNAUTHORIZED:
        token = await _access_token(s, force=True)
        response = await _patch_once(s, url, token, body)

    _READ_CACHE.pop(
        f"{base}/clients/{quote(client_id_io)}/plans", None
    )  # a read that raced the write
    if response.status_code not in (httpx.codes.OK, httpx.codes.NO_CONTENT):
        detail = _response_detail(response)
        logger.warning("Intelliflo PATCH %s failed (%s): %s", url, response.status_code, detail)
        raise IntellifloApiError(f"PATCH plan {plan_id_io} failed: {response.status_code} {detail}")
