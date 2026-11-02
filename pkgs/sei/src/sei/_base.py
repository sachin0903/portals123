"""Transport core for SEI's Wealth Platform (SWP) API.

Three credentials stack on every call, and all three have to be right or the request looks
like an outage rather than a misconfiguration:

- **mTLS.** SEI issues a PKCS#12 bundle that is the machine identity. httpx cannot load a
  ``.pfx`` directly, so it is unwrapped with ``cryptography`` and loaded into an
  ``ssl.SSLContext`` (see :func:`ssl_context`).
- **OAuth.** A password grant at ``/oauthsecurity/access_token``. Unusually, ``AppKey`` and
  ``grant_type`` go in as **headers** while the username/password go in the form body, and
  the app pair is *also* sent as HTTP Basic. That is what SEI's spec asks for.
- **AppKey.** An app-gateway header on every request, not just the token call.

SEI additionally restricts by **source IP**, so a correctly-configured client still gets
401/403 from anywhere but the allowlisted host. That is a deployment fact, not a bug: it
surfaces as :class:`SeiAuthError` so a caller can report it as such.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import ssl
import tempfile
import time
from collections.abc import Iterator
from functools import lru_cache
from types import TracebackType
from typing import Any, Self

import httpx
from api_log import ApiCall, Recorder, default_recorder, operation_of, record
from api_log.retry import policy as retry_policy
from tenacity import AsyncRetrying

from sei.settings import OAUTH_PATH, SeiSettings

_REFRESH_MARGIN_S = 120  # re-auth this long before the token expires

# Process-wide token cache keyed by (base_url, appkey, username). A caller that opens a
# short-lived client per operation would otherwise re-run the password grant every time,
# even though the token lives far longer than one request.
_TOKEN_CACHE: dict[tuple[str, str, str], tuple[str, float]] = {}


def clear_token_cache() -> None:
    """Drop all cached tokens (tests; or after rotating a credential)."""
    _TOKEN_CACHE.clear()


class SeiError(Exception):
    """Base for all SEI client errors."""


class SeiConfigError(SeiError):
    """Credentials or the client certificate are missing/unusable."""


class SeiAuthError(SeiError):
    """SEI rejected the credentials (401/403). Also what a non-allowlisted source IP looks
    like, since the gateway refuses before the credentials are ever considered."""


class SeiEntitlementError(SeiAuthError):
    """The credentials are fine; **this AppKey is not entitled to this RESOURCE.**

    SEI's app gateway answers `401 {"message": "Invalid ApiKey for given resource"}`, and that is
    a completely different problem from a wrong credential or a non-allowlisted IP — which is why
    it gets its own type rather than being one more 401.

    It cost real time: `_from_status` collapsed every 401 into "check the AppKey, the OAuth user,
    and that the caller's IP is allowlisted", the integration suite's reachability probe happened
    to call one un-entitled resource, and the whole suite refused with a message blaming the VPN.
    Meanwhile the OAuth token and the heartbeat were both answering 200 from the same machine.

    A subclass of :class:`SeiAuthError` on purpose: every existing `except SeiAuthError` keeps
    working, and only code that wants the distinction has to know about it.
    """


class SeiApiError(SeiError):
    """A request failed after retries (transport or HTTP status)."""

    def __init__(self, message: str, *, status: int | None = None, errors: Any = None) -> None:
        super().__init__(message)
        self.status = status
        #: SEI's ``errors[]`` array when the body carried one — each entry has
        #: ``code``/``title``/``detail`` (e.g. code 1001 "The firm is invalid or missing.").
        self.errors = errors


#: THE RETRY POLICY IS SHARED — `api_log.retry`, which is where the reasoning now lives.
#:
#: It was written out here (and twice more, in `pkgs/seccl` and `pkgs/morgan-lloyd`, with diverged
#: attempt counts and no `Retry-After`). What it says has not changed: 429 is retried for any
#: method because the request was refused BEFORE it was processed, while a 5xx or a transport
#: error is retried only for an idempotent one — SEI's writes include `create_cash_disbursement`,
#: "MONEY OUT. A disbursement pays cash away from the account", and there is no idempotency key to
#: lean on (`endpoints/cash.py` sends no `externalSystemTxnId` and `enrollment.py`'s docstring
#: records that SEI has none).


@contextlib.contextmanager
def _pem_file(pem: bytes) -> Iterator[str]:
    """Write ``pem`` to a private temp file for the lifetime of the block.

    ``ssl.SSLContext.load_cert_chain`` only accepts filesystem paths — there is no in-memory
    form in the stdlib — so an unencrypted private key must briefly exist on disk. It is
    created 0600 by ``mkstemp`` (never world-readable, not even for an instant) and removed
    in the ``finally``, so the window is the duration of one ``load_cert_chain``.
    """
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(pem)
        yield path
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


@lru_cache(maxsize=8)
def ssl_context(pfx_base64: str, pfx_password: str) -> ssl.SSLContext:
    """Build the mTLS context from SEI's PKCS#12 bundle, base64-encoded.

    Base64 is the only form. A file path was accepted until Aug 2026 and is gone: a path needs a
    file on the host and a bind mount to reach it, which a one-env-list deployment has nowhere to
    put, so nothing used it — and two forms meant two precedence rules and an error message that
    named whichever one was absent rather than the one to set.

    Cached: unwrapping a ``.pfx`` is expensive and the identity never changes within a
    process. Raises :class:`SeiConfigError` rather than returning None so a caller cannot
    accidentally fall through to an unauthenticated request.
    """
    if not pfx_base64:
        raise SeiConfigError("SEI client certificate is not configured (SEI_PFX_BASE64 unset)")
    if not pfx_password:
        raise SeiConfigError("SEI client certificate password is not configured")
    try:
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
            pkcs12,
        )

        # `validate=False` (the default): a value pasted through a web form may carry newlines.
        bundle = base64.b64decode(pfx_base64)
        key, cert, extra = pkcs12.load_key_and_certificates(bundle, pfx_password.encode())
    except SeiConfigError:
        raise
    except Exception as exc:
        raise SeiConfigError(f"SEI client certificate failed to load: {exc}") from exc
    if key is None or cert is None:
        raise SeiConfigError("SEI client certificate bundle has no key/certificate pair")

    pem = cert.public_bytes(Encoding.PEM)
    # Intermediates, if the bundle ships them — without the chain SEI's edge rejects the
    # handshake on some environments.
    for ca in extra or []:
        pem += ca.public_bytes(Encoding.PEM)
    pem += key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())

    context = ssl.create_default_context()
    with _pem_file(pem) as path:
        context.load_cert_chain(path)
    return context


class BaseClient:
    """Authenticated SWP access. Use as an async context manager."""

    def __init__(
        self,
        settings: SeiSettings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        recorder: Recorder | None = None,
    ) -> None:
        """`recorder`, when supplied, is handed one `ApiCall` per request.

        Optional because this is an SDK: a script or a test constructs the client with no
        database in sight. The API passes one that writes `audit.api_log`.

        When it is NOT supplied the process-wide default is used instead, resolved per call
        (see `_record`), so a bare `SeiClient()` in a sync model is still recorded.
        """
        self.settings = settings or SeiSettings()
        self._recorder = recorder
        #: Test seam. httpx ignores ``verify`` when a transport is supplied, so the mTLS
        #: identity is still built and validated — it just never reaches a socket.
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._auth_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(self.settings.concurrency)

    def _record(self) -> Recorder | None:
        """The recorder for the next call: this client's own, else the process default.

        Resolved per call rather than in `__init__`, so a client built before the process
        installed its recorder still records.
        """
        return self._recorder or default_recorder()

    async def __aenter__(self) -> Self:
        s = self.settings
        if not s.configured:
            raise SeiConfigError(f"SEI is not configured: {', '.join(s.missing())} unset")
        self._http = httpx.AsyncClient(
            base_url=s.base_url.rstrip("/"),
            verify=ssl_context(s.pfx_base64, s.pfx_password),
            transport=self._transport,
            timeout=httpx.Timeout(s.timeout, connect=s.connect_timeout),
            headers={"Accept": "application/json", "AppKey": s.appkey},
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
        """SEI's OAuth password grant. AppKey + grant_type are HEADERS (per the SWP spec),
        the app pair is HTTP Basic, and only the user credentials go in the form body."""
        assert self._http is not None  # noqa: S101 - context manager contract
        s = self.settings
        headers = {"AppKey": s.appkey, "grant_type": "password"}
        if s.appsecret:
            basic = base64.b64encode(f"{s.appkey}:{s.appsecret}".encode()).decode()
            headers["Authorization"] = f"Basic {basic}"
        response = await self._http.post(
            OAUTH_PATH,
            data={"username": s.oauth_username, "password": s.oauth_password},
            headers=headers,
        )
        if response.status_code in (401, 403):
            raise SeiAuthError(
                f"SEI rejected the {s.environment} credentials (HTTP {response.status_code}). "
                "SEI also allowlists by source IP, so this is expected from anywhere but the "
                "allowlisted host."
            )
        response.raise_for_status()
        body = response.json()
        token = body.get("access_token")
        if not token:
            raise SeiAuthError("SEI's token response carried no access_token")
        return str(token), float(body.get("expires_in", 3599))

    async def _auth_headers(self, *, force: bool = False) -> dict[str, str]:
        s = self.settings
        key = (s.base_url, s.appkey, s.oauth_username)
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

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One authenticated SWP call, returning the parsed body.

        ``path`` is relative to the SWP root and includes the service segment, e.g.
        ``/currencyreceiptservices/createCurrencyReceipt``.
        """
        # Every call goes through here, which is why the audit hook sits here rather than on
        # each endpoint method: a new endpoint is recorded the day it is written. Taken AFTER
        # the retry loop, so one row describes one logical call, not one per attempt.
        assert self._http is not None  # noqa: S101 - context manager contract
        http = self._http
        headers = await self._auth_headers()
        segments = [seg for seg in path.split("/") if seg]
        call = ApiCall(
            provider="sei",
            # The first path segment is the SWP service the call belongs to
            # (`currencyreceiptservices`), which is the useful grouping in an audit log.
            service=segments[0] if segments else None,
            method=method,
            url=path,
            operation=operation_of(path, take="last"),
            request=json,
            extra={"params": params} if params else {},
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
                        body: Any = response.json() if response.content else {}
                        call.response = body
                        call.ok = True
                        return body if isinstance(body, dict) else {"value": body}
            except httpx.HTTPStatusError as exc:
                call.status_code = exc.response.status_code
                call.response = _safe_body(exc.response)
                call.error = exc.response.text[:500]
                raise _from_status(method, path, exc) from exc
            except httpx.HTTPError as exc:
                call.error = f"{type(exc).__name__}: {exc}"
                raise SeiApiError(f"{method} {path} failed: {exc}") from exc
            # pragma: no cover — AsyncRetrying either returns or raises
            raise SeiApiError(f"{method} {path}: retrying yielded no outcome")


def _safe_body(response: httpx.Response) -> Any:
    """JSON where it is JSON, else truncated text -- a large HTML error page must not land in an
    append-only table."""
    try:
        return response.json()
    except ValueError:
        return response.text[:4000] or None


def _from_status(method: str, path: str, exc: httpx.HTTPStatusError) -> SeiError:
    """Turn an HTTP failure into the narrowest error we can justify.

    SEI answers a rejected payload with 400/422 and an ``errors[]`` array whose ``code`` is
    the actionable part (1001 = firm missing, 1006 = a description/treatment code is not
    valid, 1002 = bad account). Those codes are lifted onto the exception so a caller can
    tell "you sent the wrong reference code" from "SEI is down" without re-parsing.
    """
    status = exc.response.status_code
    if status in (401, 403):
        # SEI says WHICH kind of refusal this is, and the two need different actions. An
        # entitlement gap is a request to SEI to add the resource to this AppKey's product; a
        # bare 401 is a credential or an IP. Reading the body is what tells them apart.
        body = exc.response.text or ""
        with contextlib.suppress(ValueError):
            parsed = exc.response.json()
            if isinstance(parsed, dict):
                body = str(parsed.get("message") or body)
        if "apikey" in body.lower() and "resource" in body.lower():
            resource = next((seg for seg in path.split("/") if seg), path)
            return SeiEntitlementError(
                f"{method} {path}: this AppKey is not entitled to the {resource!r} resource "
                f"(HTTP {status}: {body.strip()}). The credentials and the network are fine — "
                "SEI has to add this resource to the AppKey's product. Check "
                "`/heartbeatapiproxy/status`, which needs no entitlement, before suspecting them."
            )
        return SeiAuthError(
            f"{method} {path}: SEI refused the request (HTTP {status}). Check the AppKey, "
            "the OAuth user, and that the caller's IP is allowlisted."
        )
    errors: Any = None
    with contextlib.suppress(ValueError):
        parsed = exc.response.json()
        if isinstance(parsed, dict):
            errors = parsed.get("errors")
    detail = exc.response.text[:500]
    return SeiApiError(f"{method} {path} failed: {status} {detail}", status=status, errors=errors)
