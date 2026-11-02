"""ComplyCube HTTP client.

One Graphene account serves the portal and the firm API. The firm API never names the provider,
these URLs, or these ids. Authorisation is the raw API key, which is what ComplyCube expects in
``Authorization``.

Every call on one ``ScreeningClient`` shares one ``httpx.AsyncClient``. A response of 429 or 5xx,
or a transport error, is retried with back-off: a read (GET) on any of them, a write (POST) only on
429 and on a connect failure, because a POST that reached the provider may have created a paid check
and a second send would create another.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from urllib.parse import quote

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://api.complycube.com/v1"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MAX_RETRIES = 3
BACKOFF_BASE_S = 0.5
RETRY_AFTER_CAP_S = 10.0
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# Firm document types to the provider's document type names.
PROVIDER_DOCUMENT_TYPES = {
    "passport": "passport",
    "drivers_license": "driving_license",
    "identity": "national_identity_card",
}

Sleep = Callable[[float], Awaitable[None]]


NOT_CONFIGURED = "ComplyCube is not configured."
LIVE_KEY_REFUSED = (
    "A live ComplyCube key is set outside production, so it is not used. Use a sandbox test_ key,"
    " or set COMPLYCUBE_ALLOW_LIVE_KEY=true to use it on purpose."
)


class ComplyCubeNotConfigured(RuntimeError):
    """No usable API key. A route maps this to 503 with the message."""

    def __init__(self, message: str = NOT_CONFIGURED) -> None:
        super().__init__(message)


def live_key_refused() -> bool:
    """A live key outside production that nobody opted in to. Real checks are billed and hold real
    people's data, so outside production a live key is treated as unset unless
    ``complycube_allow_live_key`` is on."""
    key = settings.complycube_api_key.strip()
    return (
        key.startswith("live_")
        and settings.app_stage != "production"
        and not settings.complycube_allow_live_key
    )


def usable_key() -> str:
    """The API key this deployment may use, or blank."""
    return "" if live_key_refused() else settings.complycube_api_key.strip()


def complycube_configured() -> bool:
    """True when a usable API key is set. The worker's loops and the ops routes read this."""
    return bool(usable_key())


def not_configured() -> ComplyCubeNotConfigured:
    """The refusal to raise, saying why when a live key was set outside production."""
    return ComplyCubeNotConfigured(LIVE_KEY_REFUSED if live_key_refused() else NOT_CONFIGURED)


class ScreeningClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: httpx.Timeout = TIMEOUT,
        max_retries: int = MAX_RETRIES,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._transport = transport
        self._timeout = timeout
        self._max_retries = max_retries
        self._sleep = sleep
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def configured(self) -> bool:
        return bool(self._api_key.strip())

    def _http(self) -> httpx.AsyncClient:
        """The shared client. A new event loop gets a new one, so a client bound to a closed
        loop is never reused."""
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop:
            self._client = httpx.AsyncClient(
                transport=self._transport, base_url=self._base_url, timeout=self._timeout
            )
            self._loop = loop
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._loop = None

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        if response is not None:
            header = response.headers.get("retry-after", "")
            try:
                return min(max(float(header), 0.0), RETRY_AFTER_CAP_S)
            except ValueError:
                pass
        backoff: float = BACKOFF_BASE_S * float(2**attempt)
        return min(backoff, RETRY_AFTER_CAP_S)

    async def _send(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        if not self.configured:
            raise not_configured()
        idempotent = method == "GET"
        attempt = 0
        while True:
            response: httpx.Response | None = None
            try:
                response = await self._http().request(
                    method,
                    path,
                    json=payload,
                    headers={"Authorization": self._api_key},
                )
            except httpx.TransportError as exc:
                # A write is retried only when it is known not to have reached the provider.
                unsent = isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout)
                if attempt >= self._max_retries or not (idempotent or unsent):
                    raise
            else:
                status = response.status_code
                retry = status in RETRYABLE_STATUSES and (idempotent or status == 429)
                if not retry or attempt >= self._max_retries:
                    response.raise_for_status()
                    return response.json() if response.content else None
            await self._sleep(self._retry_delay(response, attempt))
            attempt += 1

    async def find_client_by_external_id(
        self, external_id: str, *, env: str | None = None
    ) -> str | None:
        """The provider client already created for this contact uid, if there is one.

        ComplyCube does not keep ``externalId`` unique, so with ``env`` (``environment()``) only a
        client stamped with that deployment counts: another deployment on the same key can hold
        the same uid. Both filters match exactly (sandbox, 7 October 2026).
        """
        query = f"/clients?externalId={quote(external_id, safe='')}"
        if env:
            query += f"&metadata.{ENV_KEY}={quote(env, safe='')}"
        try:
            payload = await self._send("GET", query)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        if isinstance(payload, dict):
            items = payload.get("items")
        elif isinstance(payload, list):
            items = payload
        else:
            return None
        if not isinstance(items, list):
            return None
        for item in items:
            if not isinstance(item, dict) or item.get("externalId") != external_id:
                continue
            if env and environment_of(item) != env:
                continue
            found = item.get("id")
            if isinstance(found, str) and found:
                return found
        return None

    async def create_client(self, payload: dict[str, Any]) -> Any:
        return await self._send("POST", "/clients", payload)

    async def update_client(self, client_id: str, payload: dict[str, Any]) -> Any:
        """``POST /clients/{id}``; answers the whole client as it now stands.

        A field not sent is kept, and ``personDetails`` / ``companyDetails`` merge field by field
        (sending only ``lastName`` keeps ``firstName`` and ``dob``). ``type`` is refused with a
        400 even when unchanged. Both confirmed against the sandbox, 7 October 2026.
        """
        return await self._send("POST", f"/clients/{client_id}", payload)

    async def list_addresses(self, client_id: str) -> list[dict[str, Any]]:
        """Addresses already stored for this provider client.

        A missing ``items`` list is empty, so a create response is not treated as a
        listing. A 404 is an empty list.
        """
        try:
            payload = await self._send("GET", f"/addresses?clientId={quote(client_id, safe='')}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise
        if isinstance(payload, dict):
            items = payload.get("items")
        elif isinstance(payload, list):
            items = payload
        else:
            return []
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    async def create_address(self, payload: dict[str, Any]) -> Any:
        return await self._send("POST", "/addresses", payload)

    async def create_document(
        self, client_id: str, *, document_type: str, issuing_country: str
    ) -> Any:
        return await self._send(
            "POST",
            "/documents",
            {
                "clientId": client_id,
                "type": document_type,
                "classification": "proof_of_identity",
                "issuingCountry": issuing_country,
            },
        )

    async def upload_document_image(
        self,
        document_id: str,
        *,
        side: Literal["front", "back"],
        file_name: str,
        data: str,
    ) -> Any:
        """Upload one side of a document as base64 JSON."""
        return await self._send(
            "POST",
            f"/documents/{document_id}/upload/{side}",
            {"fileName": file_name, "data": data},
        )

    async def upload_document_front(self, document_id: str, *, file_name: str, data: str) -> Any:
        return await self.upload_document_image(
            document_id, side="front", file_name=file_name, data=data
        )

    async def upload_document_back(self, document_id: str, *, file_name: str, data: str) -> Any:
        return await self.upload_document_image(
            document_id, side="back", file_name=file_name, data=data
        )

    async def create_live_photo(self, client_id: str, *, data: str) -> Any:
        return await self._send("POST", "/livePhotos", {"clientId": client_id, "data": data})

    async def create_identity_check(
        self, client_id: str, *, document_id: str, live_photo_id: str
    ) -> Any:
        return await self._send(
            "POST",
            "/checks",
            {
                "clientId": client_id,
                "type": "identity_check",
                "documentId": document_id,
                "livePhotoId": live_photo_id,
            },
        )

    async def submit_identity(
        self,
        client_id: str,
        *,
        document_type: str,
        issuing_country: str,
        document: bytes,
        document_file_name: str,
        selfie: bytes,
        document_back: bytes | None = None,
        document_back_file_name: str | None = None,
    ) -> Any:
        """Upload an identity document (front, and back when there is one) and a selfie, then
        start an identity check.

        Each image is sent as base64 JSON. This is not a multipart upload and not a hosted session.
        """
        created = await self.create_document(
            client_id,
            document_type=PROVIDER_DOCUMENT_TYPES[document_type],
            issuing_country=issuing_country,
        )
        document_id = str(created["id"])
        await self.upload_document_front(
            document_id,
            file_name=_image_name(document_file_name),
            data=base64.b64encode(document).decode("ascii"),
        )
        if document_back is not None:
            await self.upload_document_back(
                document_id,
                file_name=_image_name(document_back_file_name or "", fallback="back.jpg"),
                data=base64.b64encode(document_back).decode("ascii"),
            )
        photo = await self.create_live_photo(
            client_id, data=base64.b64encode(selfie).decode("ascii")
        )
        return await self.create_identity_check(
            client_id, document_id=document_id, live_photo_id=str(photo["id"])
        )

    async def create_screening(self, client_id: str, *, monitoring: bool) -> Any:
        """Start an AML screen. ``monitoring`` asks for the provider's ongoing monitoring.

        A ComplyCube plan without continuous monitoring refuses the whole screen (403). The screen
        is then started again without monitoring, so the client is still screened, and the result
        carries ``enableMonitoring: false``: read it with ``monitoring_of``, never assume the ask
        was granted. Every other refusal is raised as it came.
        """
        body = {"clientId": client_id, "type": "extensive_screening_check"}
        try:
            return await self._send("POST", "/checks", {**body, "enableMonitoring": monitoring})
        except httpx.HTTPStatusError as exc:
            if not monitoring or not monitoring_not_in_plan(exc.response):
                raise
        logger.warning(
            "complycube: the plan has no continuous monitoring; screening without it",
            extra={"event": "complycube_monitoring_not_in_plan"},
        )
        created = await self._send("POST", "/checks", {**body, "enableMonitoring": False})
        if isinstance(created, dict):
            created["enableMonitoring"] = False
        return created

    async def update_monitoring(self, check_id: str, *, enabled: bool) -> Any:
        """Turn ongoing monitoring on or off for an existing screening check.

        ``POST /checks/{id}`` with ``enableMonitoring``. ComplyCube allows it on screening checks
        only.
        """
        return await self._send("POST", f"/checks/{check_id}", {"enableMonitoring": enabled})

    async def validate_check(
        self,
        check_id: str,
        outcome: str,
        match_id: str | None = None,
        comment: str | None = None,
    ) -> Any:
        """Record a reviewer's outcome on a check, or on one match of a screening check.

        ``POST /checks/{id}/validate`` with ``{"outcome", "matchId", "comment"}``; keys whose value
        is None are left out. ``outcome`` is ``confirmed``, ``rejected`` or ``attention``, and a
        screening check needs the ``matchId``.
        """
        body: dict[str, Any] = {"outcome": outcome}
        if match_id is not None:
            body["matchId"] = match_id
        if comment is not None:
            body["comment"] = comment
        return await self._send("POST", f"/checks/{check_id}/validate", body)

    async def get_client(self, client_id: str) -> Any:
        return await self._send("GET", f"/clients/{client_id}")

    async def get_risk_profile(self, client_id: str) -> Any:
        """ComplyCube's risk profile for a client: ``overall`` plus a breakdown."""
        return await self._send("GET", f"/clients/{client_id}/riskProfile")

    async def get_check(self, check_id: str) -> Any:
        return await self._send("GET", f"/checks/{check_id}")

    async def get_address(self, address_id: str) -> Any:
        return await self._send("GET", f"/addresses/{address_id}")

    async def get_document(self, document_id: str) -> Any:
        return await self._send("GET", f"/documents/{document_id}")

    async def get_live_photo(self, photo_id: str) -> Any:
        return await self._send("GET", f"/livePhotos/{photo_id}")

    async def download_document_image(
        self, document_id: str, *, side: Literal["front", "back"]
    ) -> Any:
        """One side of a document: ``{"fileName", "contentType", "data"}``, ``data`` in base64."""
        return await self._send("GET", f"/documents/{quote(document_id, safe='')}/download/{side}")

    async def download_live_photo(self, photo_id: str) -> Any:
        """A live photo: ``{"fileName", "contentType", "data"}``, ``data`` in base64."""
        return await self._send("GET", f"/livePhotos/{quote(photo_id, safe='')}/download")


def screening_from_env() -> ScreeningClient:
    """A client built from settings. A blank key still builds one; its calls raise
    ``ComplyCubeNotConfigured`` without reaching the network."""
    return ScreeningClient(usable_key(), base_url=settings.complycube_base_url)


_shared: ScreeningClient | None = None


def require_client() -> ScreeningClient:
    """The process's shared client. Raises ``ComplyCubeNotConfigured`` when no key is set."""
    global _shared
    if not complycube_configured():
        raise not_configured()
    key = usable_key()
    base = settings.complycube_base_url
    if _shared is None or _shared._api_key != key or _shared._base_url != base:
        _shared = ScreeningClient(key, base_url=base)
    return _shared


def _image_name(file_name: str, *, fallback: str = "front.jpg") -> str:
    """The provider accepts an image name ending in jpg, jpeg, png, or pdf."""
    lower = file_name.lower()
    for suffix in (".jpg", ".jpeg", ".png", ".pdf"):
        if lower.endswith(suffix):
            return file_name
    return fallback


#: Every metadata key Graphene sets on a ComplyCube client starts with this. The firm API reserves
#: the same prefix in a firm's own metadata (``external/models.py``), so the two never collide.
METADATA_PREFIX = "graphene_"
#: The metadata key naming the deployment a client belongs to (``environment()``).
ENV_KEY = f"{METADATA_PREFIX}env"
#: The metadata key saying how the client reached us (portal or partner). Set when the client is
#: made and never changed: a partner client later screened from the console stays a partner one.
ORIGIN_KEY = f"{METADATA_PREFIX}origin"
#: ComplyCube's limit on a metadata value; a longer one is refused with a 400.
METADATA_VALUE_LIMIT = 500


def environment() -> str:
    """This deployment's name on its ComplyCube clients: its ``APP_STAGE`` (dev, staging,
    production). Every dev box is ``dev`` too; they get no webhooks and poll only their own
    checks, so sharing the name with the dev deployment costs nothing the worker's own match
    (``sync.record_event``) does not already stop."""
    return settings.app_stage.strip()


def environment_of(client: object) -> str | None:
    """The deployment a ComplyCube client is stamped with, or None for an unstamped one."""
    metadata = client.get("metadata") if isinstance(client, dict) else None
    value = metadata.get(ENV_KEY) if isinstance(metadata, dict) else None
    return value if isinstance(value, str) and value else None


def client_metadata(
    *,
    origin: Literal["portal", "partner"],
    firm_uid: str | None,
    firm: str | None,
    contact_uid: str | None,
    client_uid: str | None = None,
    reference: str | None = None,
) -> dict[str, str]:
    """What identifies a ComplyCube client as ours, for its ``metadata``: the deployment
    (``environment()``), how it reached us, the firm, and our ids for it (the contact uid, and for a
    partner client its public uid and the firm's own reference).

    ComplyCube takes string values of at most 500 characters under keys of at most 40, and an
    update replaces ``metadata`` whole (sandbox, 7 October 2026), so empty values are left out
    rather than sent blank.
    """
    values = {
        ENV_KEY: environment(),
        ORIGIN_KEY: origin,
        f"{METADATA_PREFIX}firm_uid": firm_uid,
        f"{METADATA_PREFIX}firm": firm,
        f"{METADATA_PREFIX}contact_uid": contact_uid,
        f"{METADATA_PREFIX}client_uid": client_uid,
        f"{METADATA_PREFIX}firm_reference": reference,
    }
    return {
        key: value.strip()[:METADATA_VALUE_LIMIT]
        for key, value in values.items()
        if isinstance(value, str) and value.strip()
    }


def client_payload(
    client: dict[str, Any], *, external_id: str, metadata: dict[str, str] | None = None
) -> dict[str, Any]:
    """Provider create body. ``metadata`` is ``client_metadata``'s.

    A nationality list is stored in full on our side; only the first code is sent.
    """
    kind = client.get("type") or "person"
    payload: dict[str, Any] = {
        "type": "company" if kind == "company" else "person",
        "externalId": external_id,
    }
    if metadata:
        payload["metadata"] = dict(metadata)
    email = client.get("email")
    if isinstance(email, str):
        payload["email"] = email
    nationalities = client.get("nationality") or []
    if isinstance(nationalities, str):
        nationalities = [nationalities]
    first = next((code for code in nationalities if isinstance(code, str) and code), None)
    if payload["type"] == "person":
        details: dict[str, Any] = {}
        if client.get("first_name"):
            details["firstName"] = client["first_name"]
        if client.get("last_name"):
            details["lastName"] = client["last_name"]
        if client.get("dob"):
            details["dob"] = client["dob"]
        if first:
            details["nationality"] = first
        payload["personDetails"] = details
    else:
        raw_company = client.get("company")
        company = raw_company if isinstance(raw_company, dict) else {}
        details = {}
        name = company.get("name")
        if isinstance(name, str) and name:
            details["name"] = name
        country = company.get("incorporation_country")
        if isinstance(country, str):
            details["incorporationCountry"] = country
        payload["companyDetails"] = details
    return payload


def address_payload(client_id: str, address: dict[str, Any]) -> dict[str, Any] | None:
    """An address is sent only once line, city, and country are all present."""
    line = address.get("line")
    city = address.get("city")
    country = address.get("country")
    if not (line and city and country):
        return None
    payload: dict[str, Any] = {
        "clientId": client_id,
        "line": line,
        "city": city,
        "country": country,
    }
    for source, target in (
        ("state", "state"),
        ("postal_code", "postalCode"),
        ("from_date", "fromDate"),
    ):
        value = address.get(source)
        if value:
            payload[target] = value
    return payload


def monitoring_not_in_plan(response: httpx.Response) -> bool:
    """ComplyCube refused a request because the account's plan has no continuous monitoring."""
    if response.status_code not in (401, 403):
        return False
    try:
        text = response.text
    except (UnicodeDecodeError, httpx.ResponseNotRead):
        return False
    lowered = text.lower()
    return "monitoring" in lowered and "plan" in lowered


def monitoring_of(created: Any, asked: bool) -> bool:
    """Whether a screen ComplyCube started is monitored: its ``enableMonitoring`` when it says,
    else what was asked."""
    if isinstance(created, dict) and isinstance(created.get("enableMonitoring"), bool):
        return bool(created["enableMonitoring"])
    return asked
