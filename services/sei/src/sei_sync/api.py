"""The SWP REST API — the second SEI source, and why it exists.

The SFTP drop is a **pure daily delta**: each ``transaction_header`` file contains only
that day's transactions (verified — 2,743 rows on 27 Mar 2026, every one dated 27 Mar).
SEI retains about five days on the endpoint, so a stretch of missed drops is a permanent
hole in history that no amount of re-pulling can fill. This API is how a hole gets filled.

**It is a drill-down, not a feed.** Verified against i31 on 04 Aug 2026:

* ``/transactiondetailsservice/transactions`` **refuses a firm-wide query** — "Either
  clientId or externalPartyId or accountNumber or externalReferenceId is mandatory". So a
  sync is one request per account, not one per firm.
* The window is capped: "The provided start date and end date range should be between 0
  to 30 days".
* ``endDate`` may not be in the future — today's date is rejected, so the horizon is
  yesterday.
* Dates are ``YYYY-MM-DD``. Anything else 400s naming the format.
* ``dateType`` is 1 Transaction / 2 Trade / 3 Settlement. **There is no last-modified**.
  The incremental read continues the day after ``covered_through``. ``api_lookback_days``
  (default 0) is an optional overlap for a correction dated further back; ``merge`` on
  ``transaction_id`` absorbs a day that is read again.
* ``pageSize`` is 1-100, default 100; the response carries ``totalPageCount``.

Cost, so it is chosen rather than discovered: one request per account per 30-day window.
1,162 accounts over ten months is ~11,600 requests. A first scan starts at the earliest
``ACCOUNT_OPEN_DATE`` for that firm (28 Jun 2024 on the book measured 14 Oct 2026).
With no open date the scan is the lookback, not the whole history.

Auth is mTLS (SEI issues a pfx) plus an OAuth password grant whose token lasts 12 hours —
so one token covers any realistic run, and it is fetched once and reused.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import ssl
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from api_log import ApiCall, Recorder, default_recorder, record

from sei_sync.settings import SeiSettings

logger = logging.getLogger(__name__)

OAUTH_PATH = "/oauthsecurity/access_token"
TRANSACTIONS_PATH = "/transactiondetailsservice/transactions"
# The two enrolment-template reads. They are here rather than in a "reference" module because
# they are the ONLY thing the enrolment payload needs that the nightly drop cannot supply:
# the 183-code-set Reference report has no template TYPE and no report carries one, so without
# these an account cannot be opened at all.
CLIENT_TEMPLATES_PATH = "/enrollmentclienttemplates/crm/client/templates"
# Lowercase proxy. The spec's `enrollmentAccountTemplates` 404s as "Unable to identify
# proxy"; `enrollmentaccounttemplates` is the deployed name, same as the client templates.
ACCOUNT_TEMPLATES_PATH = "/enrollmentaccounttemplates/crm/account/templates"
# The employee-user read, and the ONE thing it adds over the nightly `UserDetail` report is the
# EMAIL. That report is twelve columns — `ENTITY_ID|USER_DISPLAY_NAME|LOGIN_USER_ID|
# USER_CREATION_DATE|ECX_ACCESS_FLAG|FIRM_ID|LAST_LOGIN_TIME|USER_STATUS|MONEY_MOVEMENT_LIMIT|
# SSO_KEY|USER_TYPE|LOGIN_PHONE` — with no address anywhere and one `USER_DISPLAY_NAME` holding
# "Armasoiu, Maria". The email is what lets a synced user ATTACH to the Graphene login the same
# person already has, so the drop alone can only ever create placeholders.
#
# BOTH query params are required on i31, despite OpenAPI `required: false`. An unscoped GET
# 400s `processingOrgId` ("Prcossesing Organization Id is required", code 100014); firm-only
# 400s `serviceId` (100015). `processingOrgId` is NOT the firm id: firm 3102's processing org
# is 3100 ("IWA Market 7") from `firm-default-attr-cache`, and sending 3102 is HTTP 200 with
# zero rows — which a `replace` model would read as "this credential has no employees".
# `serviceId` is a product-service filter, not a DSR tag (59267 is "Service Id is Invalid").
# Service 1 is a superset of every other populated id in 1–250; the spec's example 152 is empty.
EMPLOYEE_USERS_PATH = "/employeeservice/firmemployeeusers"
FIRM_DEFAULT_ATTR_PATH = "/cachereadservices/firm-default-attr-cache"
# Lowercase proxies. CamelCase `retrieveEndClientDocumentService` and
# `docRetrieveContentService` 404 on i31 as "Unable to identify proxy"; these
# spellings return a business response. The US-tax sibling is a different path.
LIST_DOCUMENTS_PATH = "/retrieveendclientdocumentservice/documents/list"
RETRIEVE_DOCUMENT_PATH = "/docretrievecontentservice/"
# One portfolio's assigned model. SEI has no model-by-id read: the catalogue is this
# call, once per distinct model, against an account that already names it.
MODEL_BY_ACCOUNT_PATH = "/pmemodelassignmentservices/PSM/model/assignment/byAccount"
ACCOUNT_DETAILS_PATH = "/retrieveaccountdetailsservice/retrieveAccountDetails"
DEFAULT_EMPLOYEE_SERVICE_ID = "1"

# SEI's own limits, not ours. Both are enforced server-side with a 400; encoding them here
# means a window is split before the call rather than after a rejection.
MAX_WINDOW_DAYS = 30
MAX_PAGE_SIZE = 100
# The historical-price call takes a list of identifiers and one as-of date. The spec
# states no maximum. 100 is the page size the transaction API enforces, and a larger
# batch that SEI rejects must not be the thing a backfill discovers on request 1.
PRICE_BATCH = 100
ASSET_PRICES_PATH = "/assetpriceproxyservice/historicalassetprices"
_ASOF_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

# "Start Date must be within last 36 months" — a 422, measured by bisection against i31 on
# 04 Aug 2026: 2023-08-12 accepted, 2023-07-31 refused. It is a SLIDING window, so a
# configured backfill date that is fine today starts failing every account once it ages
# past the floor; `earliest_start` is what keeps that from happening silently.
MAX_HISTORY_MONTHS = 36
# The exact boundary semantics are undocumented (inclusive? evaluated per field? in which
# timezone?), and the bisection only narrowed it to a fortnight. A week inside the limit
# costs nothing and removes the guesswork.
HISTORY_SAFETY_DAYS = 7

# dateType. Transaction date is the booking date and the one the SFTP feed partitions on,
# so it is what keeps the two sources comparable.
DATE_TYPE_TRANSACTION = 1

# Retry only what is worth retrying: throttling and gateway faults. A 400/422 is a bad
# request and will be bad every time; retrying it just multiplies the damage.
RETRY_STATUSES = frozenset({429, 502, 503, 504})
RETRY_ATTEMPTS = 4
RETRY_BASE_SECONDS = 1.0
RETRY_MAX_SECONDS = 30.0


def _retry_after(response: Any) -> float | None:
    """Honour ``Retry-After`` when the server sends one — it knows better than a backoff."""
    raw = response.headers.get("Retry-After") if hasattr(response, "headers") else None
    if not raw:
        return None
    try:
        return min(float(raw), RETRY_MAX_SECONDS)
    except (TypeError, ValueError):
        return None  # the HTTP-date form; fall back to the computed backoff


class SeiApiError(RuntimeError):
    """A call failed in a way that is not one account's problem."""


class ProxyUnavailable(SeiApiError):
    """Apigee has no proxy for this path. Another call will get the same 404."""


class DocumentServiceUnavailable(SeiApiError):
    """Apigee did not route the document call, so the run must stop.

    On i31 the all-lowercase proxies are deployed. A 404 ``Unable to identify proxy``
    is the camelCase spelling (``retrieveEndClientDocumentService``,
    ``docRetrieveContentService``), the same class of miss as
    ``enrollmentAccountTemplates``. Retrying that per client would report a night of
    empty documents rather than a path the gateway does not have.
    """


@lru_cache(maxsize=4)
def _ssl_context(material: bytes, pfx_password: str) -> ssl.SSLContext:
    """Build the mTLS context from SEI's PKCS#12 bytes.

    Cached because loading a PKCS#12 runs a KDF, and a per-account client would otherwise
    pay it thousands of times in one sync. Keyed on the material itself rather than on where
    it came from, so base64 and file configurations share one context.
    """
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        pkcs12,
    )

    try:
        key, cert, _ = pkcs12.load_key_and_certificates(material, pfx_password.encode())
    except ValueError as exc:
        # Wrong password and corrupt bytes both land here; say which inputs to look at
        # rather than surfacing cryptography's "Could not deserialize PKCS12 data".
        raise SeiApiError(
            "SEI client certificate could not be loaded — check SEI_PFX_BASE64 and SEI_PFX_PASSWORD"
        ) from exc
    if key is None or cert is None:
        raise SeiApiError("SEI pfx holds no usable key/certificate pair (wrong password?)")

    # httpx wants a file, and ssl gives no way to load a cert chain from memory. Written
    # to a temp file and unlinked immediately after loading, so the decrypted key is on
    # disk for microseconds rather than for the life of the process.
    pem = cert.public_bytes(Encoding.PEM) + key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    with tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False) as handle:
        handle.write(pem)
        tmp = handle.name
    try:
        context = ssl.create_default_context()
        context.load_cert_chain(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return context


def windows(start: date, end: date, *, size: int = MAX_WINDOW_DAYS) -> Iterator[tuple[date, date]]:
    """Split an inclusive date range into spans SEI will accept.

    Yields nothing when the range is empty (``start > end``) — which is the normal state of
    an incremental run on the morning after, and must not be mistaken for an error.
    """
    if size < 1:
        raise ValueError("window size must be at least one day")
    cursor = start
    while cursor <= end:
        last = min(cursor + timedelta(days=size - 1), end)
        yield cursor, last
        cursor = last + timedelta(days=1)


def horizon(today: date) -> date:
    """The latest ``endDate`` SEI accepts: yesterday. Today's date is refused as future."""
    return today - timedelta(days=1)


def as_day(value: Any) -> date | None:
    """A warehouse date, a datetime, or an ISO string. Anything else is absent."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def format_asof(day: date) -> str:
    """The historical-price body's date (``28-Aug-2020``).

    The transaction API wants ``YYYY-MM-DD``. This operation's spec says
    ``DD-MON-YYYY``. The month abbreviations are fixed English, not the process locale.
    """
    return f"{day.day:02d}-{_ASOF_MONTHS[day.month - 1]}-{day.year}"


def parse_asof(value: Any) -> date | None:
    """A price date from the historical-price body, or an ISO date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    parts = text.split("-")
    if len(parts) != 3:
        return None
    months = {name.lower(): index for index, name in enumerate(_ASOF_MONTHS, start=1)}
    month = months.get(parts[1].lower())
    if month is None:
        return None
    try:
        return date(int(parts[2]), month, int(parts[0]))
    except ValueError:
        return None


def backfill_start(opened: date | None, settings: SeiSettings, *, today: date) -> date:
    """Where a first scan starts, clamped to the oldest day SEI will accept.

    The earliest ``ACCOUNT_OPEN_DATE`` is the day this book could have a transaction
    or a price. Inception is not: on the book measured 14 Oct 2026 open dates begin
    2024-06-28 and inception reaches 2016-11-23, which the 36-month rule refuses.
    No open date means the lookback, not the whole history.
    """
    end = horizon(today)
    floor = earliest_start(today)
    if opened is not None and opened <= today:
        start = opened
    else:
        start = end - timedelta(days=settings.api_lookback_days)
    if start < floor:
        logger.warning(
            "sei api: %s precedes SEI's %d-month floor; starting at %s",
            start,
            MAX_HISTORY_MONTHS,
            floor,
        )
        start = floor
    return start


def earliest_start(today: date) -> date:
    """The oldest ``startDate`` SEI will accept — 36 months back, less a safety margin.

    Calendar months, not 1,095 days: SEI's own message says months, and the two differ by
    up to a day across leap years. Clamping to this is what stops an account open date
    from turning into a 422 on every account the day it ages out.
    """
    month_index = (today.year * 12 + today.month - 1) - MAX_HISTORY_MONTHS
    year, month = divmod(month_index, 12)
    month += 1
    # Day-of-month can overflow the target month (31 March - 36 months is 31 March, but 31
    # May - 1 month is not 31 April); walk back to the last valid day instead of raising.
    day = today.day
    while True:
        try:
            floor = date(year, month, day)
            break
        except ValueError:
            day -= 1
    return floor + timedelta(days=HISTORY_SAFETY_DAYS)


@dataclass
class AccountRef:
    """One account to sync. ``firm_id`` travels with it — the API requires both."""

    firm_id: str
    account_number: str
    # The drop's ACCOUNT_OPEN_DATE. None when that column is absent: the first
    # scan is then the lookback, not the whole history.
    opened_on: date | None = None


@dataclass
class SyncStats:
    """What a sync did, for the model's own reporting."""

    requests: int = 0
    rows: int = 0
    accounts: int = 0
    retries: int = 0
    done: int = 0
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        return (
            f"{self.rows} transactions over {self.requests} request(s) "
            f"for {self.accounts} account(s), {self.retries} retried, "
            f"{len(self.failures)} failed"
        )


class SeiApi:
    """A token-holding client for the SWP API. One per sync run."""

    def __init__(self, settings: SeiSettings, *, recorder: Recorder | None = None) -> None:
        """`recorder` follows the contract every vendor client in this repo uses: passed in where
        the caller knows the context, otherwise the process-wide default (`install_api_log()`,
        called from `models/tenants.py` at model discovery). None everywhere is legitimate — a
        script or a test has no database to write to."""
        if not settings.api_base_url:
            raise SeiApiError("SEI_API_BASE_URL is not set; the SWP API is disabled")
        for name in ("oauth_username", "oauth_password", "appkey", "pfx_password"):
            if not getattr(settings, name):
                raise SeiApiError(f"SEI API credential missing: SEI_{name.upper()}")
        # The certificate is checked through settings, which owns the base64-then-path
        # precedence and names whichever one is wrong.
        if not settings.pfx_base64:
            raise SeiApiError("no SEI client certificate configured: set SEI_PFX_BASE64")
        self._settings = settings
        self._base = settings.api_base_url.rstrip("/")
        self._token: str | None = None
        self._client: Any = None
        self._recorder = recorder

    async def __aenter__(self) -> SeiApi:
        import httpx

        self._client = httpx.AsyncClient(
            verify=_ssl_context(self._settings.pfx_material(), self._settings.pfx_password),
            timeout=float(self._settings.api_timeout_seconds),
        )
        await self._authenticate()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
        # Drop the token with the client: it must not outlive the run that acquired it.
        self._token = None

    async def _authenticate(self) -> None:
        settings = self._settings
        basic = base64.b64encode(f"{settings.appkey}:{settings.appsecret}".encode()).decode()
        response = await self._client.post(
            self._base + OAUTH_PATH,
            data={"username": settings.oauth_username, "password": settings.oauth_password},
            headers={
                "AppKey": settings.appkey,
                "grant_type": "password",
                "Authorization": f"Basic {basic}",
            },
        )
        if response.status_code != 200:
            # Never echo the body: a rejected grant can quote back what was sent.
            raise SeiApiError(
                f"SEI OAuth rejected the credentials (HTTP {response.status_code}); "
                "check SEI_OAUTH_USERNAME/_PASSWORD and SEI_APPKEY/_APPSECRET for this environment"
            )
        token = response.json().get("access_token")
        if not token:
            raise SeiApiError("SEI OAuth returned 200 with no access_token")
        self._token = token

    def _headers(self) -> dict[str, str]:
        return {"AppKey": self._settings.appkey, "Authorization": f"Bearer {self._token}"}

    async def transactions(
        self, account: AccountRef, start: date, end: date, *, stats: SyncStats
    ) -> list[dict[str, Any]]:
        """Every transaction for one account in one window, following the paging.

        The window is NOT split here — the caller owns windowing, because it also owns the
        cursor. Passing a range wider than 30 days is a programming error SEI will reject.
        """
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = await self._get_page(account, start, end, page, stats=stats)
            data = payload.get("data") or {}
            batch = data.get("listOfTransactionDetails") or []
            rows.extend(batch)
            total_pages = data.get("totalPageCount") or 0
            # Guard on the batch as well as the page count: a response that claims more
            # pages but returns none would otherwise spin forever.
            if page >= total_pages or not batch:
                return rows
            page += 1

    async def templates(self, firm_id: str, *, scope: str) -> list[dict[str, Any]]:
        """One firm's enrolment templates (``GET`` the client or account template service).

        ``scope`` is ``client`` or ``account``. Returns the rows as SEI sends them; the model
        conforms them, because the two services answer the same SHAPE under different names and
        that reconciliation belongs next to the columns it produces.

        Not windowed, not paged and not retried per account — this is one small request per firm
        per scope, so the failure modes `_get_page` guards against (throttling over 42,000
        calls, a token expiring mid-run) do not arise. A 401 still re-authenticates once,
        because a run that starts with an old token would otherwise fail its first call.
        """
        path = CLIENT_TEMPLATES_PATH if scope == "client" else ACCOUNT_TEMPLATES_PATH
        call = ApiCall(
            provider="sei",
            service="enrollmenttemplates",
            method="GET",
            url=self._base + path,
            operation=f"list{scope.capitalize()}Templates",
            extra={"firm_id": firm_id, "scope": scope},
        )
        with record(self._recorder or default_recorder(), call):
            response = await self._client.get(
                self._base + path, params={"firmId": firm_id}, headers=self._headers()
            )
            if response.status_code == 401:
                await self._authenticate()
                response = await self._client.get(
                    self._base + path, params={"firmId": firm_id}, headers=self._headers()
                )
            call.status_code = response.status_code
            if response.status_code != 200:
                call.error = _reasons(response)
                raise SeiApiError(
                    f"SEI {scope} templates for firm {firm_id}: HTTP {response.status_code} "
                    f"{_reasons(response)}"
                )
            body = response.json()
            call.ok = True
            return _template_rows(body)

    async def model_assignment(
        self, firm_id: str, account_number: str, portfolio_id: str
    ) -> dict[str, Any]:
        """The model on one portfolio (``POST .../model/assignment/byAccount``).

        The body is flat. The account-details call below is a ``data.attributes``
        envelope; this one is not, and sending the envelope is a 400.
        """
        body = await self._post_recorded(
            MODEL_BY_ACCOUNT_PATH,
            {
                "firmId": firm_id,
                "accountNumber": account_number,
                "portfolioId": portfolio_id,
            },
            service="pmemodelassignmentservices",
            operation="getModelByAccount",
            extra={
                "firm_id": firm_id,
                "account_number": account_number,
                "portfolio_id": portfolio_id,
            },
        )
        return _attributes(body)

    async def account_details(self, firm_id: str, account_number: str) -> dict[str, Any]:
        """One account (``POST .../retrieveAccountDetails``), without its payees.

        The contribution block is ``isaJisaContributionDetails`` on the attributes.
        The four includes stay off: the recipients are bank details, and this read
        is only after the allowance.
        """
        body = await self._post_recorded(
            ACCOUNT_DETAILS_PATH,
            {
                "data": {
                    "type": "accountDetailsRequest",
                    "attributes": {
                        "firmId": firm_id,
                        "accountNumber": account_number,
                        "includeRelationships": "false",
                        "includePayToRecipients": "false",
                        "includePayForRecipients": "false",
                        "includePayers": "false",
                    },
                }
            },
            service="retrieveaccountdetailsservice",
            operation="retrieveAccountDetails",
            extra={"firm_id": firm_id, "account_number": account_number},
        )
        return _attributes(body)

    async def asset_prices(
        self, firm_id: str, criteria: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        """Prices for one as-of date (``POST /assetpriceproxyservice/historicalassetprices``).

        ``criteria`` is the spec's ``assetCriteriaList``: each item is an identifier
        scheme, the identifier, and one ``asofDate``. There is no date range. ``context``
        is a firm id. The snapshot price does not differ across firms, so one context
        covers the universe.
        """
        body = await self._post_recorded(
            ASSET_PRICES_PATH,
            {
                "sourceSystem": "swp",
                "context": int(firm_id),
                "assetCriteriaList": criteria,
            },
            service="assetpriceproxyservice",
            operation="assetPriceExtract",
            extra={"firm_id": firm_id, "assets": len(criteria)},
        )
        rows = body.get("assetPriceResultsToList") or []
        return rows if isinstance(rows, list) else []

    async def _post_recorded(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        service: str,
        operation: str,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        """One JSON POST, with the same 401 and gateway retry as a transaction page."""
        call = ApiCall(
            provider="sei",
            service=service,
            method="POST",
            url=self._base + path,
            operation=operation,
            extra=extra,
        )
        headers = {**self._headers(), "Content-Type": "application/json"}
        with record(self._recorder or default_recorder(), call):
            for attempt in range(1, RETRY_ATTEMPTS + 1):
                response = await self._client.post(self._base + path, json=payload, headers=headers)
                if response.status_code == 401 and attempt < RETRY_ATTEMPTS:
                    await self._authenticate()
                    headers = {**self._headers(), "Content-Type": "application/json"}
                    continue
                if response.status_code in RETRY_STATUSES and attempt < RETRY_ATTEMPTS:
                    await asyncio.sleep(
                        min(RETRY_BASE_SECONDS * (2 ** (attempt - 1)), RETRY_MAX_SECONDS)
                    )
                    continue
                call.status_code = response.status_code
                if response.status_code != 200:
                    call.error = _reasons(response)
                    if response.status_code == 404 and _proxy_missing(response.text):
                        raise ProxyUnavailable(f"HTTP {response.status_code}: {_reasons(response)}")
                    raise SeiApiError(f"HTTP {response.status_code}: {_reasons(response)}")
                parsed = response.json()
                call.ok = True
                return parsed if isinstance(parsed, dict) else {}
        raise SeiApiError(f"authentication kept failing across {RETRY_ATTEMPTS} attempts")

    async def firm_default_attributes(self, firm_id: str) -> Any:
        """One firm's defaults (``GET /cachereadservices/firm-default-attr-cache``).

        The employee-user sweep needs ``processingOrgId`` from this body. It is not the firm
        id: Blackfinch ``3102``'s processing org is ``3100`` ("IWA Market 7"), and sending
        ``3102`` to ``firmemployeeusers`` is HTTP 200 with zero rows.
        """
        params = {"firmId": firm_id}
        call = ApiCall(
            provider="sei",
            service="cachereadservices",
            method="GET",
            url=self._base + FIRM_DEFAULT_ATTR_PATH,
            operation="getFirmDefaultAttributes",
            extra={"firm_id": firm_id},
        )
        with record(self._recorder or default_recorder(), call):
            response = await self._client.get(
                self._base + FIRM_DEFAULT_ATTR_PATH, params=params, headers=self._headers()
            )
            if response.status_code == 401:
                await self._authenticate()
                response = await self._client.get(
                    self._base + FIRM_DEFAULT_ATTR_PATH, params=params, headers=self._headers()
                )
            call.status_code = response.status_code
            if response.status_code != 200:
                call.error = _reasons(response)
                raise SeiApiError(
                    f"SEI firm defaults for firm {firm_id}: HTTP {response.status_code} "
                    f"{_reasons(response)}"
                )
            call.ok = True
            return response.json()

    async def employee_users(
        self, *, processing_org_id: str, service_id: str, page: int = 1
    ) -> Any:
        """One page of employee users for a processing org, recorded in ``audit.api_log``.

        Returns the WHOLE body rather than the rows, because the caller needs ``metaData`` to
        know whether to ask for another page — and the two collections must be told apart by
        name (`employeeUsersForFirmList`, not the paging block).

        Both scopes are mandatory on i31 (OpenAPI ``required: false`` is wrong).
        ``processing_org_id`` is the processing org from firm defaults, not the firm id.
        """
        params = {
            "pageNumber": page,
            "pageSize": MAX_PAGE_SIZE,
            "processingOrgId": processing_org_id,
            "serviceId": service_id,
        }
        call = ApiCall(
            provider="sei",
            service="employeeservice",
            method="GET",
            url=self._base + EMPLOYEE_USERS_PATH,
            operation="fetchFirmLevelEmployeeUsers",
            extra={
                "page": page,
                "processing_org_id": processing_org_id,
                "service_id": service_id,
            },
        )
        with record(self._recorder or default_recorder(), call):
            response = await self._client.get(
                self._base + EMPLOYEE_USERS_PATH, params=params, headers=self._headers()
            )
            if response.status_code == 401:
                await self._authenticate()
                response = await self._client.get(
                    self._base + EMPLOYEE_USERS_PATH, params=params, headers=self._headers()
                )
            call.status_code = response.status_code
            if response.status_code != 200:
                call.error = _reasons(response)
                raise SeiApiError(
                    f"SEI employee users org {processing_org_id} page {page}: "
                    f"HTTP {response.status_code} {_reasons(response)}"
                )
            call.ok = True
            return response.json()

    async def list_end_client_documents(self, body: dict[str, Any]) -> dict[str, Any]:
        """One party's document list (statements, tax, contract notes, firm generic, …).

        ``body`` is the request the caller built — dates, include-flags and ``partyId`` —
        because which flags are on is a product decision, not a transport one. A 404/403 is
        :class:`DocumentServiceUnavailable`: Apigee did not route the path (the camelCase
        trap), and calling it once per client would look like an empty book.
        """
        call = ApiCall(
            provider="sei",
            service="retrieveendclientdocumentservice",
            method="POST",
            url=self._base + LIST_DOCUMENTS_PATH,
            operation="retrieveDocumentsForEntityIds",
            extra={"party_id": body.get("partyId")},
        )
        with record(self._recorder or default_recorder(), call):
            response = await self._post_json(LIST_DOCUMENTS_PATH, body)
            if response.status_code == 401:
                await self._authenticate()
                response = await self._post_json(LIST_DOCUMENTS_PATH, body)
            call.status_code = response.status_code
            if response.status_code in (403, 404):
                call.error = _reasons(response)
                raise DocumentServiceUnavailable(
                    f"SEI document list was not routed (HTTP {response.status_code}): "
                    f"{_reasons(response)}. The deployed path is all-lowercase "
                    f"{LIST_DOCUMENTS_PATH}; a camelCase service name is "
                    "Unable to identify proxy."
                )
            if response.status_code != 200:
                call.error = _reasons(response)
                raise SeiApiError(
                    f"SEI document list for party {body.get('partyId')}: "
                    f"HTTP {response.status_code} {_reasons(response)}"
                )
            payload: dict[str, Any] = response.json()
            call.ok = True
            return payload

    async def retrieve_document(self, document_id: str) -> tuple[bytes, str | None]:
        """The bytes of one document, plus the response Content-Type when SEI sends one.

        The success body is ``application/octet-stream``. A JSON body is an error that
        arrived with a success-shaped status, and is refused rather than stored as a PDF.
        """
        call = ApiCall(
            provider="sei",
            service="docretrievecontentservice",
            method="POST",
            url=self._base + RETRIEVE_DOCUMENT_PATH,
            operation="retrieveDocumentContent",
            extra={"document_id": document_id},
        )
        with record(self._recorder or default_recorder(), call):
            response = await self._post_json(RETRIEVE_DOCUMENT_PATH, {"documentId": document_id})
            if response.status_code == 401:
                await self._authenticate()
                response = await self._post_json(
                    RETRIEVE_DOCUMENT_PATH, {"documentId": document_id}
                )
            call.status_code = response.status_code
            if response.status_code in (403, 404):
                # A missing document and a missing PROXY both 404. Only the proxy is fatal:
                # one bad id must not abort a statement run, and a camelCase path must not
                # be reported as a night of individually missing files.
                reason = _reasons(response)
                call.error = reason
                if response.status_code == 403 or _proxy_missing(reason):
                    raise DocumentServiceUnavailable(
                        f"SEI document retrieve was not routed (HTTP {response.status_code}): "
                        f"{reason}. The deployed path is all-lowercase "
                        f"{RETRIEVE_DOCUMENT_PATH}; a camelCase service name is "
                        "Unable to identify proxy."
                    )
                raise SeiApiError(
                    f"SEI document {document_id}: HTTP {response.status_code} {reason}"
                )
            if response.status_code != 200:
                call.error = _reasons(response)
                raise SeiApiError(
                    f"SEI document {document_id}: HTTP {response.status_code} {_reasons(response)}"
                )
            content_type = response.headers.get("content-type")
            if content_type and "json" in content_type.lower():
                call.error = _reasons(response)
                raise SeiApiError(
                    f"SEI document {document_id} returned JSON rather than bytes: "
                    f"{_reasons(response)}"
                )
            call.ok = True
            data: bytes = response.content
            return data, content_type

    async def _post_json(self, path: str, body: dict[str, Any]) -> Any:
        headers = {**self._headers(), "Content-Type": "application/json"}
        return await self._client.post(self._base + path, json=body, headers=headers)

    async def _get_page(
        self, account: AccountRef, start: date, end: date, page: int, *, stats: SyncStats
    ) -> dict[str, Any]:
        """One page of one account's transactions, recorded in `audit.api_log`.

        THE AUDIT HOOK SITS HERE, around the retry loop, for the two reasons every other vendor
        client states: a new caller is recorded the day it is written, and one row describes one
        LOGICAL call rather than one per attempt.

        It was absent entirely. This module is a second, independent SWP client living beside
        `pkgs/sei` (down to a second class also called `SeiApiError`), and it is the one the
        pipeline actually uses — so SEI's highest-volume outbound traffic, up to ~42,000 requests
        on a backfill, was recorded nowhere at all, while `pkgs/sei`'s single cash-receipt endpoint
        was recorded faithfully. A gap that depends on which of two clients made the call is
        invisible from the log itself.
        """
        call = ApiCall(
            provider="sei",
            service="transactionservices",
            method="GET",
            url=self._base + TRANSACTIONS_PATH,
            operation="listTransactionDetails",
            # The account, not the payload: this is a READ, and the useful question of the log is
            # which account's window was asked for.
            extra={
                "firm_id": account.firm_id,
                "account_number": account.account_number,
                "page": page,
            },
        )
        with record(self._recorder or default_recorder(), call):
            return await self._get_page_recorded(account, start, end, page, stats=stats, call=call)

    async def _get_page_recorded(
        self,
        account: AccountRef,
        start: date,
        end: date,
        page: int,
        *,
        stats: SyncStats,
        call: ApiCall,
    ) -> dict[str, Any]:
        params = {
            "firmId": account.firm_id,
            "accountNumber": account.account_number,
            "dateType": DATE_TYPE_TRANSACTION,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "pageNumber": page,
            "pageSize": MAX_PAGE_SIZE,
            # Corrections and reversals arrive as their own transactions with their own
            # ids. Excluding them would leave the ledger showing the original figure of a
            # transaction SEI has since amended.
            "includeAdjustmentTransactionFlag": "true",
        }
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            response = await self._client.get(
                self._base + TRANSACTIONS_PATH, params=params, headers=self._headers()
            )
            stats.requests += 1

            if response.status_code == 401:
                # A 12-hour token outlives most runs but not a 42,000-request backfill.
                await self._authenticate()
                continue

            if response.status_code in RETRY_STATUSES and attempt < RETRY_ATTEMPTS:
                # Throttling and gateway faults are the two failures a job this size WILL
                # meet. Retried here rather than counted as an account failure, because a
                # 429 says "later", not "no" — and a whole account's window would otherwise
                # be dropped for a condition that clears in a second.
                delay = _retry_after(response) or min(
                    RETRY_BASE_SECONDS * 2 ** (attempt - 1), RETRY_MAX_SECONDS
                )
                stats.retries += 1
                logger.info(
                    "sei api: HTTP %s for %s/%s, retrying in %.1fs (attempt %d/%d)",
                    response.status_code,
                    account.firm_id,
                    account.account_number,
                    delay,
                    attempt,
                    RETRY_ATTEMPTS,
                )
                await asyncio.sleep(delay)
                continue

            call.status_code = response.status_code
            if response.status_code != 200:
                # The vendor's own message is what an incident is reconstructed from, and a
                # failure's body is the whole point of the log — `ApiCall.keeps_response` drops a
                # SUCCESS's body and keeps this one.
                call.response = _reasons(response)
                if response.status_code in RETRY_STATUSES:
                    # Exhausted, not un-retried. The distinction matters when reading a
                    # backfill's failures: "SEI throttles harder than we back off" is a
                    # different problem from "one request glitched".
                    raise SeiApiError(
                        f"gave up after {RETRY_ATTEMPTS} attempts, last HTTP {response.status_code}"
                    )
                raise SeiApiError(f"HTTP {response.status_code}: {_reasons(response)}")
            payload: dict[str, Any] = response.json()
            call.ok = True
            return payload

        # Only reachable when every attempt was a 401 and re-auth never took.
        raise SeiApiError(f"authentication kept failing across {RETRY_ATTEMPTS} attempts")


def _attributes(body: dict[str, Any]) -> dict[str, Any]:
    """``data.attributes`` when SEI wraps it, ``data`` when it wraps only that far."""
    data = body.get("data")
    if isinstance(data, dict):
        inner = data.get("attributes")
        if isinstance(inner, dict):
            return inner
        return data
    return body


def _proxy_missing(text: str) -> bool:
    """Apigee's 'no such proxy' 404, as opposed to a document id SEI does not hold.

    ``unknown-resource`` is the fault a deployed proxy raises when the path is not one
    of its flows. i31 does that for ``/assetpriceproxyservice/historicalassetprices``
    (14 Oct 2026): the proxy is there, the documented resource is not.
    """
    lowered = text.lower()
    return (
        "unable to identify proxy" in lowered
        or "sei-default" in lowered
        or "unknown-resource" in lowered
    )


def _reasons(response: Any) -> str:
    """SEI's 400s name the offending field and why; surface that, not the raw body."""
    body: str = response.text[:200]
    try:
        errors = response.json().get("error", {}).get("errors", [])
    except Exception:
        return body
    if not errors:
        return body
    return "; ".join(f"{e.get('field', '?')}: {e.get('reason', '')}" for e in errors)[:400]


def _template_rows(body: Any) -> list[dict[str, Any]]:
    """The template rows out of whichever envelope this service used.

    SEI wraps a collection three ways across its services and a caller that guesses gets an
    EMPTY list rather than an error — which reads as "this firm has no templates" and would
    make an un-openable account look like a configuration choice. So the shapes are tried in
    order: a bare array, ``data[]``, then the first list-of-objects inside ``data``.

    A THIRD COPY of `pkgs/sei/endpoints/_shapes.py::rows`, and it stays one deliberately:
    `services/sei` does not depend on `pkgs/sei` (it has its own `SeiSettings`), and coupling a
    pipeline service to the API's vendor client to share twenty lines would be the larger change.
    What is shared is the RULE — more than one candidate collection refuses rather than picking,
    because four SEI responses put an emptiable collection before a non-data one and "the first
    list of objects" then returns a warning or an exception block at HTTP 200.
    """
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if not isinstance(body, dict):
        return []
    data = body.get("data", body)
    if isinstance(data, list):
        out: list[dict[str, Any]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            attributes = row.get("attributes")
            out.append(attributes if isinstance(attributes, dict) else row)
        return out
    if isinstance(data, dict):
        attributes = data.get("attributes")
        source = attributes if isinstance(attributes, dict) else data
        candidates = [
            name
            for name, value in source.items()
            if isinstance(value, list) and value and all(isinstance(r, dict) for r in value)
        ]
        # REFUSED rather than guessed when there is more than one. Four SEI responses put an
        # emptiable collection immediately before a non-data one (`holdingsDetails` before
        # `warning`, `activityDetail` before `exceptionDetails`), so "the first list of objects"
        # returns the wrong one — and returns it at HTTP 200, which is worse than an error. The
        # template services carry one collection today; this is what makes the day they do not a
        # failure instead of an enrolment template built from a warning block.
        if len(candidates) > 1:
            raise SeiApiError(
                "ambiguous template response: more than one collection "
                f"({', '.join(sorted(candidates))})"
            )
        if candidates:
            return list(source[candidates[0]])
    return []
