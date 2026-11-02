"""Transport core: Azure AD client-credentials auth with refresh, retry with
backoff, and the three Dynamics Web API call shapes the CRM exposes —

- ``call_action``: POST a custom action (``cam_*``; names are case sensitive).
  Responses carry an envelope (``responseKey``/``message``, flat or nested
  under ``response``); a failed envelope raises ``MorganLloydActionError``.
- ``get`` / ``get_collection``: OData entity reads; collections follow
  ``@odata.nextLink`` pagination.
- ``patch_entity``: PATCH an entity by GUID path (returns 204; lookup fields
  must use the ``@odata.bind`` suffix with entity paths).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from types import TracebackType
from typing import Any, Self

import httpx
from api_log import ApiCall, Recorder, default_recorder, operation_of, record
from api_log.retry import policy as retry_policy
from tenacity import AsyncRetrying

from morgan_lloyd.settings import MorganLloydSettings

_REFRESH_MARGIN_S = 120  # re-auth this long before the token expires
_ODATA_HEADERS = {
    "OData-MaxVersion": "4.0",
    "OData-Version": "4.0",
    "Accept": "application/json",
}

# Process-wide token cache, keyed by connection identity. Callers (the sync's
# extractors) open a short-lived client per pull — with only per-instance state
# every model pull would re-run the client-credentials grant, even though the
# token lives ~1h. Shared here, a sync run costs one grant per connection.
_TOKEN_CACHE: dict[tuple[str, str, str], tuple[str, float]] = {}


def clear_token_cache() -> None:
    """Drop all cached tokens (tests; or after rotating a client secret)."""
    _TOKEN_CACHE.clear()


class MorganLloydError(Exception):
    """Base for all Morgan Lloyd client errors."""


class MorganLloydApiError(MorganLloydError):
    """A request failed after retries (transport or HTTP status)."""


class MorganLloydActionError(MorganLloydError):
    """A custom action returned HTTP 200 but its envelope reported failure
    (``responseKey`` = 1)."""


class MorganLloydAuthError(MorganLloydError):
    """Azure AD would not issue a token, so no call was attempted.

    Typed separately because it is a DIFFERENT FACT from a failed request, and conflating the two
    wastes an operator's time in both directions. A `MorganLloydApiError` means Morgan Lloyd
    heard us and refused; this means we never reached them, and the cause is on the Azure side --
    an expired secret, a revoked app registration, or a Conditional Access policy.

    Carries the AADSTS code and description verbatim, because they are specific and actionable
    where the raw `400 Bad Request` is not: `AADSTS53003 Access has been blocked by Conditional
    Access policies` (observed 09 Aug 2026 -- the credentials were correct and the directory
    refused issuance from that network, the same source-IP shape as SEI's SFTP allowlist) reads
    as "the credentials are wrong" if all you see is the status code.
    """


# THE RETRY POLICY IS SHARED — `api_log.retry`, which is where the reasoning lives.
#
# What was here was method-BLIND: `status == 429 or status >= 500`, plus every transport error, for
# five attempts. Dynamics HAS NO DELETE, so a retried `cam_createcontact` puts a second human being
# in the CRM and a retried contribution reclaims relief from HMRC twice — which is exactly why
# `app/provider_queue.py` defaults an operation's `retry` class to `manual` and forces
# `max_attempts` to 1 for it. That queue sits BEHIND the console's drawers, so a form's call comes
# straight through here and the class it declares gated nothing at this level.
#
# 429 is still retried for any method, because the request was refused BEFORE it was processed. A
# 5xx or a transport error is retried only for an idempotent one: this org answers a duplicate
# create with a 200 and a second record.


def _envelope_failure(body: Any) -> str | None:
    """The failure message if the body carries a failed response envelope."""
    if not isinstance(body, dict):
        return None
    nested = body.get("response")
    envelope: dict[str, Any] = nested if isinstance(nested, dict) else body
    if envelope.get("responseKey") in (1, "1"):
        message = envelope.get("message") or envelope.get("responseMessage")
        return str(message or "action reported responseKey=1 with no message")
    return None


def _aad_failure(response: httpx.Response) -> str:
    """Azure's own reason for refusing a token, in one line.

    The `error_description` opens with the AADSTS code and a sentence that names the actual
    problem -- `AADSTS7000215 Invalid client secret`, `AADSTS53003 Access has been blocked by
    Conditional Access policies` -- and then continues with trace ids over several lines. The
    first line is the actionable part; the rest is for a support ticket.
    """
    try:
        body = response.json()
    except ValueError:
        return f"Azure AD refused the token request ({response.status_code})."
    description = str(body.get("error_description") or "").strip()
    if description:
        return description.splitlines()[0].strip()
    code = body.get("error") or response.status_code
    return f"Azure AD refused the token request ({code})."


def _record_token(
    recorder: Recorder | None,
    call: ApiCall,
    started: float,
    *,
    ok: bool,
    status: int | None,
    error: str | None,
) -> None:
    """Best-effort audit of the token exchange. Never raises: failing to record a failure must
    not replace it with a different one."""
    if recorder is None:
        return
    finished = call.__class__(
        provider=call.provider,
        service=call.service,
        method=call.method,
        url=call.url,
        operation=call.operation,
        ok=ok,
        status_code=status,
        error=error,
        duration_ms=int((time.monotonic() - started) * 1000),
        extra=call.extra,
    )
    with contextlib.suppress(Exception):
        recorder(finished.redacted())


def _operation_of(path: str) -> str | None:
    """The action or entity set a request path names, without its arguments.

    Delegates to `api_log.operation_of`, which is now the one implementation. This one was the
    only one of three that was right, and it was right the hard way: OData reads landed one
    distinct "operation" per `$select` projection, which fragments any count over them and makes
    an exact-match filter miss the call entirely. Seccl's recorded `v1` for its one versioned
    endpoint and SEI's took the LAST segment, which is an identifier on any path carrying one.
    """
    return operation_of(path)


def _mints_an_identifier(operation: str | None) -> bool:
    """Whether this action's success body is the only place its new id will ever exist.

    Dynamics names every creating action `cam_create*` -- `cam_createcontact`,
    `cam_createscheme`, `cam_createbeneficiaries`, `cam_createcontribution` -- and each answers
    with the identifier it minted. A prefix test is crude, but it is crude in the safe
    direction: a false positive keeps a body that did not need keeping, while a false negative
    loses an id and duplicates a client on the retry.
    """
    return operation is not None and operation.lower().startswith("cam_create")


def _body(response: httpx.Response) -> Any:
    """The response as JSON where it is JSON, else a truncated string.

    Truncated because a rogue endpoint returning a 5MB HTML error page should not put 5MB into
    an append-only table once per retry cycle.
    """
    try:
        return response.json()
    except ValueError:
        return response.text[:4000] or None


class BaseClient:
    """Authenticated Dynamics Web API access. Use as an async context manager."""

    def __init__(
        self,
        settings: MorganLloydSettings | None = None,
        *,
        recorder: Recorder | None = None,
    ) -> None:
        """`recorder`, when supplied, is handed one `ApiCall` per request.

        Optional because this package is an SDK: a script or a test constructs the client with
        no database in sight. The API passes one that writes `audit.api_log`.

        When it is NOT supplied the process-wide default is used instead, resolved per call (see
        `_record`) -- so the sync's models, which construct a bare `MorganLloydClient()` in
        thirty-odd places, are recorded without each one being told to be.
        """
        self.settings = settings or MorganLloydSettings()
        self._recorder = recorder
        self._http: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._auth_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(self.settings.concurrency)

    def _record(self) -> Recorder | None:
        """The recorder for the next call: this client's own, else the process default.

        Resolved per call rather than in `__init__` on purpose. Model discovery imports every
        module under `models/` before a daemon's startup has installed anything, so a client
        built at import time would capture `None` and stay silent for the life of the process.
        """
        return self._recorder or default_recorder()

    async def __aenter__(self) -> Self:
        self._http = httpx.AsyncClient(
            base_url=self.settings.api_base_url + "/",
            timeout=self.settings.timeout,
            headers=_ODATA_HEADERS,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # --- auth -----------------------------------------------------------------

    async def _fetch_token(self) -> tuple[str, float]:
        """Azure AD v1 client-credentials grant (the docs' token flow). The docs
        label it GET, but the AAD token endpoint takes a form-encoded POST.

        RECORDED, and typed on failure. Both halves close the gap
        `docs/provider-routing.md` section 6 lists as known: the audit hook wrapped the REQUEST
        and not the token fetch, so "we could not authenticate to Morgan Lloyd at 3am" -- exactly
        what an ops log exists to show -- appeared nowhere, and the raw `httpx.HTTPStatusError`
        escaped every caller's `except MorganLloydError` to surface as a 500 with a stack trace.

        Only the OUTCOME is recorded, never the form body: it carries the client secret. That is
        also why this is written here rather than by routing the call through `_request`, whose
        job is to record what it sent.
        """
        assert self._http is not None  # noqa: S101 - context manager contract
        s = self.settings
        started = time.monotonic()
        call = ApiCall(
            provider="morgan-lloyd",
            method="POST",
            url=s.token_url,
            operation="oauth2_token",
            # No `request`: the body is the credential.
            extra={"resource": s.resource_url, "client_id": s.client_id},
        )
        recorder = self._record()
        try:
            response = await self._http.post(
                s.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": s.client_id,
                    "client_secret": s.client_secret,
                    "resource": s.resource_url,
                },
            )
        except Exception as exc:
            _record_token(recorder, call, started, ok=False, status=None, error=str(exc))
            raise MorganLloydAuthError(f"could not reach Azure AD: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            reason = _aad_failure(response)
            _record_token(
                recorder, call, started, ok=False, status=response.status_code, error=reason
            )
            raise MorganLloydAuthError(reason)

        _record_token(recorder, call, started, ok=True, status=response.status_code, error=None)
        body = response.json()
        return str(body["access_token"]), float(body.get("expires_in", 3599))

    async def _auth_headers(self, *, force: bool = False) -> dict[str, str]:
        key = (self.settings.token_url, self.settings.client_id, self.settings.resource_url)
        async with self._auth_lock:
            cached = None if force else _TOKEN_CACHE.get(key)
            if cached is not None and time.monotonic() < cached[1]:
                self._token = cached[0]
            else:
                token, expires_in = await self._fetch_token()
                self._token = token
                _TOKEN_CACHE[key] = (token, time.monotonic() + expires_in - _REFRESH_MARGIN_S)
            return {"Authorization": f"Bearer {self._token}"}

    # --- requests ---------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        # Every call goes through here, which is why the audit hook sits here rather than on
        # each endpoint method: a new endpoint is recorded the day it is written. The record is
        # taken AFTER the retry loop, so one row describes one logical call rather than one per
        # attempt -- the retries are the client's business, not the audit trail's.
        assert self._http is not None  # noqa: S101 - context manager contract
        http = self._http
        headers = await self._auth_headers()
        operation = _operation_of(path)
        call = ApiCall(
            provider="morgan-lloyd",
            method=method,
            url=f"{self.settings.api_base_url}/{path.lstrip('/')}",
            operation=operation,
            request=json,
            # A create's response is the only record of the id it minted -- `cam_createcontact`
            # answers with `contactid`, `cam_createscheme` with `schemeReference` -- so it
            # survives the "drop successful responses" rule. See `ApiCall.keep_response`.
            keep_response=_mints_an_identifier(operation),
        )

        async def attempt() -> httpx.Response:
            nonlocal headers
            async with self._semaphore:
                response = await http.request(
                    method, path, params=params, json=json, headers=headers
                )
            if response.status_code == 401:  # expired token: re-auth once, in-attempt
                headers = await self._auth_headers(force=True)
                async with self._semaphore:
                    response = await http.request(
                        method, path, params=params, json=json, headers=headers
                    )
            response.raise_for_status()
            return response

        with record(self._record(), call):
            try:
                async for outcome in AsyncRetrying(**retry_policy(method)):
                    with outcome:
                        response = await attempt()
                        call.status_code = response.status_code
                        call.response = _body(response)
                        # An action envelope can report failure inside a 200; call_action
                        # raises on that separately, and flips `ok` when it does.
                        call.ok = True
                        return response
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:500]
                call.status_code = exc.response.status_code
                call.response = _body(exc.response)
                call.error = detail
                raise MorganLloydApiError(
                    f"{method} {path} failed: {exc.response.status_code} {detail}"
                ) from exc
            except httpx.HTTPError as exc:
                call.error = f"{type(exc).__name__}: {exc}"
                raise MorganLloydApiError(f"{method} {path} failed: {exc}") from exc
            # pragma: no cover — AsyncRetrying either returns or raises
            raise MorganLloydApiError(f"{method} {path}: retrying yielded no outcome")

    async def call_action(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST a custom action (name is case sensitive) and return the parsed
        body after checking its response envelope."""
        response = await self._request("POST", action, json=body)
        if response.status_code == 204 or not response.content:
            return {}
        parsed: Any = response.json()
        failure = _envelope_failure(parsed)
        if failure is not None:
            raise MorganLloydActionError(f"{action}: {failure}")
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """One entity/function read; returns the parsed body."""
        body: Any = (await self._request("GET", path, params=params)).json()
        return body if isinstance(body, dict) else {"value": body}

    async def get_collection(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """An OData collection read, following ``@odata.nextLink`` pagination."""
        body = await self.get(path, params)
        records = [item for item in body.get("value", []) if isinstance(item, dict)]
        while isinstance(next_link := body.get("@odata.nextLink"), str) and next_link:
            body = await self.get(next_link)
            records.extend(item for item in body.get("value", []) if isinstance(item, dict))
        return records

    async def patch_entity(self, path: str, body: dict[str, Any]) -> str | None:
        """PATCH an entity (e.g. ``contacts(<guid>)``); returns the updated
        entity's ``OData-EntityId`` header when the API supplies one."""
        response = await self._request("PATCH", path, json=body)
        entity_id: str | None = response.headers.get("OData-EntityId")
        return entity_id

    async def call_many(self, calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        """Fan out several custom-action calls with bounded concurrency
        (the semaphore caps in-flight requests); errors propagate."""
        return list(
            await asyncio.gather(*(self.call_action(action, body) for action, body in calls))
        )
