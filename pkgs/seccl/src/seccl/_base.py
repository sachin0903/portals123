"""Transport core for the Seccl API.

Split out of `client.py` so this package matches the shape of `pkgs/sei` and
`pkgs/morgan-lloyd`: a transport base, endpoint mixins grouped by capability, and a composed
client. The behaviour here is unchanged and remains the implementation validated against the
live API — `services/seccl` was its only consumer for a long time and its notes on Seccl's
pagination and auth are the load-bearing part.

Three credentials shapes, decided by `auth_type`: `private_key_jwt` (production — Auth0 M2M
with a public key registered at Seccl), `client_secret` (staging Auth0 client-credentials) and
`legacy` (the deprecated `/authenticate` endpoint). The first two answer a bearer token; the
legacy one answers an `api-token` header, which is why the header set is returned alongside the
token rather than assumed.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import time
import uuid
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx
import jwt
from api_log import ApiCall, Recorder, default_recorder, operation_of
from api_log import record as record_call
from api_log.retry import policy as retry_policy
from tenacity import AsyncRetrying

from seccl.settings import SecclSettings

_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
_REFRESH_MARGIN_S = 120  # re-auth this long before the token expires
_REQUEST_TIMEOUT_S = 120


def _pem_from_b64(value: str) -> str:
    """PEM text from ``SECCL_PRIVATE_KEY_B64``.

    A deployment form often wraps the value, or drops the trailing ``=``. Either one
    used to reach ``base64.b64decode`` raw and raise ``binascii.Error: Incorrect padding``,
    which the pipeline then reported as ``model 'raw.accounts' failed`` — the scheduler
    was only the caller. Whitespace is removed and missing padding restored; anything
    that still is not a PEM is a named error.
    """
    compact = "".join(value.split())
    if len(compact) >= 2 and compact[0] == compact[-1] and compact[0] in "\"'":
        compact = compact[1:-1]
    padded = compact + ("=" * (-len(compact) % 4))
    try:
        pem = base64.b64decode(padded, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "SECCL_PRIVATE_KEY_B64 is not valid base64 of a PEM private key"
        ) from exc
    if "PRIVATE KEY" not in pem:
        raise RuntimeError("SECCL_PRIVATE_KEY_B64 did not decode to a PEM private key")
    return pem


def _auth0_audience(domain: str) -> str:
    """Auth0 requires the tenant URL with a trailing slash as the assertion ``aud``."""
    return domain.rstrip("/") + "/"


logger = logging.getLogger("seccl.client")


def _safe_body(response: httpx.Response) -> Any:
    """JSON where it is JSON, else truncated text -- a 5MB HTML error page must not land in an
    append-only table."""
    try:
        return response.json()
    except ValueError:
        return response.text[:4000] or None


class SecclApiError(Exception):
    """A Seccl request failed after retries.

    ``status`` is the HTTP status when Seccl answered (a 4xx refusal, a 5xx), and ``None`` when the
    request never got an answer (a transport error or a timeout). A caller tells a refusal that
    will not fix itself (4xx, bar 429) from one worth another try (5xx, 429, transport) by this,
    not by parsing the message — the status used to live only in the message string.
    """

    def __init__(self, message: str, *, status: int | None = None, response: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.response = response


def _explain(response: httpx.Response) -> str:
    """Seccl's own account of a refusal, in the exception message.

    **Seccl names the problem and this used to throw the naming away.** A refused write answers

        {"error": {"type": "DataValidationError", "errorMessage": "The request has failed
         validation", "fieldErrors": [{"fieldName": "surname", "errorMessage": "Surname can't be
         blank", "type": "presence"}, …]}}

    and the exception said `POST /client failed: Client error '400 Bad Request'`. The body was
    kept for the audit log and never shown to the caller, so a developer holding a wrong payload
    got a status code and had to reach for a raw client to find out which field — which is what
    the first live run of the integration suite did, three times.

    `fieldErrors` is rendered as `field: message` because that is the actionable part; the
    envelope's own `errorMessage` ("The request has failed validation") never is.
    """
    body = _safe_body(response)
    if not isinstance(body, dict):
        return str(body or "")[:400]
    error = body.get("error")
    if not isinstance(error, dict):
        return str(body)[:400]

    fields = error.get("fieldErrors")
    if isinstance(fields, list) and fields:
        named = ", ".join(
            f"{item.get('fieldName')}: {item.get('errorMessage')}"
            for item in fields
            if isinstance(item, dict)
        )
        return named[:600]
    return str(error.get("errorMessage") or error)[:400]


#: THE RETRY POLICY IS SHARED — `api_log.retry`, which is where the reasoning now lives.
#:
#: What it says has not changed: 429 is retried for any method because the request was refused
#: BEFORE it was processed, while a 5xx or a transport error is retried only for an idempotent one.
#: THIS IS THE MONEY RAIL — `create_transaction_group`'s own action definition reads "THE MONEY
#: RAIL. Contributions, transfers and withdrawals are all lodged as a transaction group; sending
#: one twice instructs twice" — and it reaches that loop, because the console's writes go through
#: `write()` to get their `audit.api_log` row.


class BaseClient:
    """Authenticated Seccl API access. Use as an async context manager."""

    def __init__(
        self, settings: SecclSettings | None = None, *, recorder: Recorder | None = None
    ) -> None:
        """`recorder`, when supplied, is handed one `ApiCall` per request.

        Optional because this is an SDK: the sync worker and a one-off script both construct it
        with no database in sight. The API passes one that writes `audit.api_log`.

        When it is NOT supplied the process-wide default is used instead, resolved per call
        (see `_record`), so a bare `SecclClient()` in a sync model is still recorded.
        """
        self.settings = settings or SecclSettings()
        self._recorder = recorder
        self._http: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._headers: dict[str, str] = {}
        self._token_expires_at = 0.0
        self._auth_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(self.settings.concurrency)

    def _record(self) -> Recorder | None:
        """The recorder for the next call: this client's own, else the process default.

        Resolved per call rather than in `__init__`, so a client built before the daemon
        installed its recorder still records.
        """
        return self._recorder or default_recorder()

    @property
    def firm_id(self) -> str:
        return self.settings.firm_id

    async def __aenter__(self) -> Self:
        self._http = httpx.AsyncClient(base_url=self.settings.api_url, timeout=_REQUEST_TIMEOUT_S)
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

    def _private_key(self) -> str:
        if self.settings.private_key_b64:
            return _pem_from_b64(self.settings.private_key_b64)
        configured = self.settings.private_key_path.strip()
        if not configured:
            raise RuntimeError(
                "SECCL_AUTH_TYPE=private_key_jwt requires SECCL_PRIVATE_KEY_B64 "
                "or SECCL_PRIVATE_KEY_PATH"
            )
        path = Path(configured)
        if not path.is_file():
            raise RuntimeError(f"SECCL_PRIVATE_KEY_PATH is not a readable file: {path}")
        return path.read_text()

    def _client_assertion(self) -> str:
        key = self._private_key()
        now = int(time.time())
        payload = {
            "iss": self.settings.client_id,
            "sub": self.settings.client_id,
            "aud": _auth0_audience(self.settings.auth0_domain),
            "iat": now,
            "exp": now + 60,
            "jti": str(uuid.uuid4()),
        }
        return jwt.encode(payload, key, algorithm="RS256", headers={"kid": self.settings.kid})

    async def _fetch_token(self) -> tuple[str, float, dict[str, str]]:
        """Returns (token, expires_in_seconds, auth headers for API requests)."""
        assert self._http is not None  # noqa: S101 - context manager contract
        s = self.settings
        if s.auth_type == "private_key_jwt":
            response = await self._http.post(
                f"{s.auth0_domain.rstrip('/')}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": s.client_id,
                    "client_assertion_type": _ASSERTION_TYPE,
                    "client_assertion": self._client_assertion(),
                    "audience": s.oauth_audience,
                },
            )
            response.raise_for_status()
            body = response.json()
            token = str(body["access_token"])
            return token, float(body.get("expires_in", 7200)), {"Authorization": f"Bearer {token}"}
        if s.auth_type == "client_secret":
            response = await self._http.post(
                f"{s.auth0_domain.rstrip('/')}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": s.client_id,
                    "client_secret": s.client_secret,
                    "audience": s.oauth_audience,
                },
            )
            response.raise_for_status()
            body = response.json()
            token = str(body["access_token"])
            return token, float(body.get("expires_in", 7200)), {"api-token": token}
        response = await self._http.post(
            "/authenticate", json={"firmId": s.firm_id, "id": s.user_id, "password": s.password}
        )
        response.raise_for_status()
        token = str(response.json()["data"]["token"])
        return token, 7200.0, {"api-token": token}

    async def _auth_headers(self, *, force: bool = False) -> dict[str, str]:
        async with self._auth_lock:
            if force or self._token is None or time.monotonic() >= self._token_expires_at:
                token, expires_in, headers = await self._fetch_token()
                self._token = token
                self._token_expires_at = time.monotonic() + expires_in - _REFRESH_MARGIN_S
                self._headers = headers
            return self._headers

    # --- requests ---------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        # Every call goes through here, which is why the audit hook sits here rather than on
        # each endpoint method: a new endpoint is recorded the day it is written. Taken AFTER
        # the retry loop, so one row describes one logical call, not one per attempt.
        assert self._http is not None  # noqa: S101 - context manager contract
        http = self._http
        headers = await self._auth_headers()
        call = ApiCall(
            provider="seccl",
            method=method,
            url=f"{self.settings.api_url.rstrip('/')}/{path.lstrip('/')}",
            operation=operation_of(path),
            request=json,
            extra={"params": params} if params else {},
        )

        async def attempt() -> httpx.Response:
            nonlocal headers
            async with self._semaphore:
                response = await http.request(
                    method, path, params=params, json=json, headers=headers
                )
            if response.status_code in (401, 419):  # expired session: re-auth once, in-attempt
                headers = await self._auth_headers(force=True)
                async with self._semaphore:
                    response = await http.request(
                        method, path, params=params, json=json, headers=headers
                    )
            response.raise_for_status()
            return response

        with record_call(self._record(), call):
            try:
                async for outcome in AsyncRetrying(**retry_policy(method)):
                    with outcome:
                        response = await attempt()
                        call.status_code = response.status_code
                        body = response.json()
                        call.response = body
                        call.ok = True
                        return body
            except httpx.HTTPStatusError as exc:
                call.status_code = exc.response.status_code
                call.response = _safe_body(exc.response)
                call.error = exc.response.text[:500]
                detail = _explain(exc.response)
                raise SecclApiError(
                    f"{method} {path} failed: {exc.response.status_code}"
                    + (f" — {detail}" if detail else ""),
                    status=exc.response.status_code,
                    response=call.response,
                ) from exc
            except httpx.HTTPError as exc:
                # No status: the request never got an answer (transport error, timeout). The caller
                # treats this as worth another try, like a 5xx.
                call.error = f"{type(exc).__name__}: {exc}"
                raise SecclApiError(f"{method} {path} failed: {exc}", status=None) from exc
            raise SecclApiError(  # pragma: no cover
                f"{method} {path}: retrying yielded no outcome"
            )

    async def write(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """One mutating call, returning the single record it answers with.

        Deliberately routed through ``_request`` rather than ``request_json``: the latter
        bypasses the retry loop AND the ``audit.api_log`` recorder, so a write made through it
        leaves no evidence. For a money instruction that is the one thing that must not happen,
        so the endpoint mixins all come through here.
        """
        records = self._extract(await self._request(method, path, json=body))
        return records[0] if records else {}

    async def request_json(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        """One mutating/management call (webhook registration etc.), same auth."""
        assert self._http is not None  # noqa: S101 - context manager contract
        headers = await self._auth_headers()
        response = await self._http.request(method, path, json=json, headers=headers)
        if response.status_code in (401, 419):
            headers = await self._auth_headers(force=True)
            response = await self._http.request(method, path, json=json, headers=headers)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _extract(body: Any) -> list[dict[str, Any]]:
        data = body.get("data", body) if isinstance(body, dict) else body
        if data is None:
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return [data] if isinstance(data, dict) else []

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params)

    async def get_record(self, path: str) -> dict[str, Any]:
        """One entity fetch; returns the record inside the response's data attribute."""
        records = self._extract(await self._request("GET", path))
        if not records:
            raise SecclApiError(f"GET {path}: response contained no record")
        return records[0]

    async def get_records(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """A non-paginated endpoint's records (data attribute, list or single dict)."""
        return self._extract(await self._request("GET", path, params))

    async def get_list(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """A list endpoint, following ``meta.count`` pagination.

        Seccl's pagination has unstable tie-ordering at page boundaries: a walk
        can return ``count`` rows with a handful duplicated and their
        counterparts permanently missing (verified live — deterministic, not a
        concurrency artefact). Missing rows would be diff-DELETED by full_merge
        models, so when the set fits under ``single_page_cap`` it is refetched
        as one exactly-sized page (which the API serves reliably). Beyond the
        cap — or when the caller pinned a small pageSize for payload-size
        reasons — pages are fetched concurrently, deduplicated by id, and a
        shortfall against meta.count is logged.
        """
        explicit_page_size = bool(params and "pageSize" in params)
        params = dict(params or {})
        params.setdefault("pageSize", self.settings.page_size)
        first = await self._request("GET", path, params)
        records = self._extract(first)
        meta = first.get("meta") if isinstance(first, dict) else None
        count = int(meta.get("count", 0)) if isinstance(meta, dict) else 0
        page_size = int(params["pageSize"])
        if count <= page_size:
            return records
        if not explicit_page_size and count <= self.settings.single_page_cap:
            return self._extract(await self._request("GET", path, {**params, "pageSize": count}))
        pages = range(2, (count + page_size - 1) // page_size + 1)
        results = await asyncio.gather(  # errors propagate: a failed page fails the pull
            *(self._request("GET", path, {**params, "page": page}) for page in pages)
        )
        for body in results:
            records.extend(self._extract(body))
        seen: dict[Any, dict[str, Any]] = {}
        keyless: list[dict[str, Any]] = []
        for record in records:
            if record.get("id") is None:
                keyless.append(record)
            else:
                seen[record["id"]] = record
        deduped = list(seen.values()) + keyless
        if seen and len(deduped) < count:
            logger.warning(
                "GET %s: %d distinct rows of meta.count=%d (page-boundary loss)",
                path,
                len(deduped),
                count,
            )
        return deduped

    async def get_many(
        self, paths: list[str], params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Detail fan-out: fetch every path (bounded concurrency), flatten the records."""
        results = await asyncio.gather(*(self._request("GET", path, params) for path in paths))
        records: list[dict[str, Any]] = []
        for body in results:
            records.extend(self._extract(body))
        return records

    # --- files ------------------------------------------------------------------

    async def file_url(self, message_type: str, body: dict[str, Any]) -> str:
        """Mint a pre-signed download URL for one client document.

        ``POST /files/{type}`` with ``{id, firmId, clientId}`` answers
        ``{"data": {"url": ...}}``. Goes through the retrying, semaphore-bounded
        request path — a statement run fans out over thousands of documents.
        """
        record = self._extract(await self._request("POST", f"/files/{message_type}", json=body))
        url = record[0].get("url") if record else None
        if not url:
            raise SecclApiError(f"POST /files/{message_type}: response carried no url")
        return str(url)

    async def download(self, url: str) -> bytes:
        """Fetch a pre-signed URL's bytes. The URL is absolute and already carries
        its own credentials, so it takes no auth header — but it is still counted
        against the shared semaphore so downloads can't outrun the API budget."""
        assert self._http is not None  # noqa: S101 - context manager contract
        http = self._http

        async def attempt() -> httpx.Response:
            async with self._semaphore:
                response = await http.get(url, follow_redirects=True)
            response.raise_for_status()
            return response

        try:
            async for outcome in AsyncRetrying(**retry_policy("GET")):
                with outcome:
                    return (await attempt()).content
        except httpx.HTTPError as exc:
            raise SecclApiError(f"GET {url.split('?')[0]} failed: {exc}") from exc
        raise SecclApiError("download: retrying yielded no outcome")  # pragma: no cover
