"""Litestar application for the firm-facing API."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any

from litestar import Litestar, Request, Response, delete, get, patch, post, put
from litestar.enums import MediaType, RequestEncodingType, ScopeType
from litestar.exceptions import HTTPException, ValidationException
from litestar.middleware import AbstractMiddleware, DefineMiddleware
from litestar.openapi.config import OpenAPIConfig
from litestar.openapi.datastructures import ResponseSpec
from litestar.openapi.spec import Components, Example, Reference, Schema, SecurityScheme, Tag
from litestar.openapi.spec.enums import OpenAPIType
from litestar.params import Parameter
from litestar.response.base import ASGIResponse
from litestar.status_codes import HTTP_200_OK, HTTP_400_BAD_REQUEST, HTTP_401_UNAUTHORIZED
from litestar.types import ASGIApp, Message, Receive, Scope, Send
from litestar.utils.helpers import get_enum_string_value

from app.complycube.signature import HEADER, verify
from app.complycube.sync import event_id
from external.html import SCALAR_SCRIPT, guide_markdown, reference_html, scalar_bundle, tag_guide
from external.keys import SCOPES, CachedKey, KeyVerifier
from external.limiter import WindowLimiter
from external.models import (
    MAX_FILE_BYTES,
    REQUEST_MAX_BODY,
    CheckCreate,
    CheckOut,
    ChecksOut,
    ClientCreate,
    ClientOut,
    ClientPage,
    ClientPatch,
    CustodianPut,
    DocumentOut,
    DocumentUpload,
    HealthOut,
    Problem,
    WebhookCreated,
    WebhookOut,
    WebhookPut,
    held_document,
)
from external.store import ClientStore, Rejected, StubStore

LATEST = "v1"
_SCREENING_MAX_BODY = 256 * 1024
KNOWN = frozenset({LATEST})
DUMMY_BEARER = "test_dummy"
PUBLIC_PREFIXES = ("/docs", "/schema", "/health")
STUB_KEY = CachedKey(found=True, uid="stub", tenant_id=1, scopes=SCOPES)

AppRequest = Request[Any, Any, Any]
AcceptEvent = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class Ctx:
    stub: bool
    store: ClientStore
    screening_secret: str
    keys: KeyVerifier | None = None
    inbox: list[dict[str, Any]] = field(default_factory=list)
    requests: WindowLimiter = field(default_factory=lambda: WindowLimiter(limit=10, period=1.0))
    anon_requests: WindowLimiter = field(
        default_factory=lambda: WindowLimiter(limit=10, period=1.0)
    )
    accept_event: AcceptEvent | None = None


ClientUid = Annotated[str, Parameter(description="Public id of the client.")]
CheckUid = Annotated[str, Parameter(description="Public id of the check.")]
PageLimit = Annotated[
    int,
    Parameter(description="Maximum number of clients to return. Must be from 1 to 100."),
]
PageOffset = Annotated[
    int,
    Parameter(description="Number of clients to skip. Must be zero or greater."),
]

CLIENTS = "Clients"
CHECKS = "Checks"
WEBHOOKS = "Webhooks"
CUSTODIANS = "Custodians"
HEALTH = "Health"


def _problem(description: str) -> ResponseSpec:
    return ResponseSpec(data_container=Problem, description=description, generate_examples=False)


_UNAUTHORIZED = _problem(
    "The bearer token is missing or not a valid key. The code is unauthorized."
)
_FORBIDDEN = _problem(
    "This key does not include the scope this operation requires. The code is forbidden."
)
_NOT_FOUND = _problem("No resource with that id belongs to this firm. The code is not_found.")
_RATE_LIMITED = _problem(
    "The key sent more than 10 requests in a second. The code is rate_limited."
)
_VERSION = (
    "X-Version is not a version this API serves. The code is unknown_version. "
    "The rejected value is not echoed."
)


def _errors(
    bad_request: str, *, not_found: bool = False, unprocessable: str | None = None
) -> dict[int, ResponseSpec]:
    documented: dict[int, ResponseSpec] = {
        400: _problem(bad_request),
        401: _UNAUTHORIZED,
        403: _FORBIDDEN,
        429: _RATE_LIMITED,
    }
    if not_found:
        documented[404] = _NOT_FOUND
    if unprocessable is not None:
        documented[422] = _problem(unprocessable)
    return documented


def problem(code: str, message: str, status_code: int) -> Response[dict[str, str]]:
    return Response(
        content={"code": code, "message": message},
        status_code=status_code,
        media_type=MediaType.JSON,
    )


def _ctx(scope: Scope) -> Ctx:
    ctx = scope["state"].get("ext")
    if not isinstance(ctx, Ctx):
        raise HTTPException(status_code=500, detail="missing request context")
    return ctx


async def _emit(response: Response[Any], scope: Scope, receive: Receive, send: Send) -> None:
    media = response.media_type or MediaType.JSON
    asgi = ASGIResponse(
        body=response.render(response.content, get_enum_string_value(media)),
        status_code=response.status_code,
        media_type=media,
        headers=response.headers or None,
        cookies=response.cookies or None,
        encoding=response.encoding,
        background=response.background,
    )
    await asgi(scope, receive, send)


# Unknown bearers share this window. It is not the peer address: behind the
# ingress that address is the proxy, and one flood would then refuse every firm.
_UNKNOWN_LIMIT = "unknown"


def _bearer(scope: Scope) -> str | None:
    for name, value in scope.get("headers") or []:
        if name.lower() == b"authorization":
            text = value.decode()
            prefix = "Bearer "
            if text.startswith(prefix):
                return text[len(prefix) :]
            return ""
    return None


def _principal(request: AppRequest) -> CachedKey:
    principal = request.scope["state"].get("principal")
    if not isinstance(principal, CachedKey):
        raise HTTPException(status_code=500, detail="missing principal")
    return principal


def _tenant(request: AppRequest) -> int:
    tenant_id = _principal(request).tenant_id
    if tenant_id is None:
        raise HTTPException(status_code=500, detail="missing principal")
    return tenant_id


def _require(request: AppRequest, needed: str) -> Response[dict[str, str]] | None:
    principal = _principal(request)
    if needed not in principal.scopes:
        return problem("forbidden", "This key does not include the required scope.", 403)
    return None


def _validation_bits(extra: object) -> list[str]:
    """Field names and messages from a validation extra, without the request body."""
    found: list[str] = []
    items: list[object] = extra if isinstance(extra, list) else [extra]
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("message", "msg"):
            value = item.get(key)
            if isinstance(value, str) and value:
                found.append(value)
        loc = item.get("loc", item.get("key"))
        if isinstance(loc, str) and loc:
            found.append(loc)
        elif isinstance(loc, list | tuple):
            text = ".".join(str(part) for part in loc if part not in {"body", ""})
            if text:
                found.append(text)
    return found


def _safe_detail(exc: ValidationException) -> str:
    bits = _validation_bits(exc.extra)
    detail = "; ".join(bits) if bits else (exc.detail or "The request body was not accepted.")
    if "complycube" in detail.lower():
        return "The request body was not accepted."
    return detail


def invalid_body(request: AppRequest, exc: ValidationException) -> Response[dict[str, str]]:
    del request
    return problem("invalid_body", _safe_detail(exc), HTTP_400_BAD_REQUEST)


class ContextMiddleware(AbstractMiddleware):
    def __init__(self, app: ASGIApp, ctx: Ctx) -> None:
        super().__init__(app)
        self.ctx = ctx

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != ScopeType.HTTP:
            await self.app(scope, receive, send)
            return
        scope["state"]["ext"] = self.ctx
        path = scope.get("path", "")
        if path.startswith(PUBLIC_PREFIXES) or path.startswith("/internal"):
            await self.app(scope, receive, send)
            return
        token = _bearer(scope)
        if self.ctx.stub:
            if token != DUMMY_BEARER:
                await _emit(
                    problem("unauthorized", "A bearer token is required.", HTTP_401_UNAUTHORIZED),
                    scope,
                    receive,
                    send,
                )
                return
            scope["state"]["principal"] = STUB_KEY
        else:
            if not token or self.ctx.keys is None:
                await _emit(
                    problem("unauthorized", "A bearer token is required.", HTTP_401_UNAUTHORIZED),
                    scope,
                    receive,
                    send,
                )
                return
            stamp: float | None = None
            if self.ctx.keys.cached(token) is None and not self.ctx.keys.known(token):
                stamp = self.ctx.anon_requests.charge(_UNKNOWN_LIMIT)
                if stamp is None:
                    await _emit(
                        problem("rate_limited", "Too many requests.", 429),
                        scope,
                        receive,
                        send,
                    )
                    return
            record = await self.ctx.keys.authenticate(token)
            if record is not None:
                self.ctx.keys.remember(token)
                if stamp is not None:
                    self.ctx.anon_requests.release(_UNKNOWN_LIMIT, stamp)
            if record is not None and record.suspended:
                await _emit(
                    problem("forbidden", "This firm's access is suspended.", 403),
                    scope,
                    receive,
                    send,
                )
                return
            if record is None:
                await _emit(
                    problem("unauthorized", "A bearer token is required.", HTTP_401_UNAUTHORIZED),
                    scope,
                    receive,
                    send,
                )
                return
            scope["state"]["principal"] = record
        principal = scope["state"].get("principal")
        key = principal.uid if isinstance(principal, CachedKey) and principal.uid else (token or "")
        if not self.ctx.requests.allow(key):
            await _emit(problem("rate_limited", "Too many requests.", 429), scope, receive, send)
            return
        await self.app(scope, receive, send)


class VersionMiddleware(AbstractMiddleware):
    def __init__(self, app: ASGIApp, stub: bool) -> None:
        super().__init__(app)
        self.stub = stub

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != ScopeType.HTTP:
            await self.app(scope, receive, send)
            return
        raw = ""
        for name, value in scope.get("headers") or []:
            if name.lower() == b"x-version":
                raw = value.decode().strip()
                break
        version = LATEST if raw == "" else raw
        path = scope.get("path", "")
        if path == "/health":
            version = LATEST
        elif version not in KNOWN:
            response = problem(
                "unknown_version",
                f"X-Version must be one of: {', '.join(sorted(KNOWN))}.",
                HTTP_400_BAD_REQUEST,
            )
            await _emit(response, scope, receive, send)
            return

        async def send_with_version(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-version", version.encode()))
                if self.stub:
                    headers.append((b"x-graphene-stub", b"true"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_version)


# Named request examples. Scalar uses the OpenAPI examples-map keys as the labels.
CLIENT_REQUEST_EXAMPLES: dict[str, dict[str, Any]] = {
    "Minimal Individual": {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "dob": "1815-12-10",
        "email": "ada@analytical.example",
        "nationality": ["GB"],
        "address": {"country": "GB"},
    },
    "Individual": {
        "type": "person",
        "email": "ada@analytical.example",
        "first_name": "Ada",
        "middle_name": "Augusta",
        "last_name": "Lovelace",
        "dob": "1815-12-10",
        "gender": "female",
        "nationality": ["GB"],
        "birth_country": "GB",
        "identities": [{"type": "national_insurance_number", "identifier": "QQ123456C"}],
        "mobile": "+442071234567",
        "telephone": "+442079460123",
        "address": {
            "property_number": "12",
            "building_name": "Analytical House",
            "line": "1 Analytical Engine",
            "city": "London",
            "state": "Greater London",
            "postal_code": "SW1A 1AA",
            "country": "GB",
            "from_date": "1815-12-10",
        },
        "tax_residency": ["GB"],
        "reference": "firm-1",
        "metadata": {"desk": "north"},
    },
    "Organisation": {
        "type": "company",
        "email": "hello@analytical.example",
        "company": {
            "name": "Analytical Engines Ltd",
            "website": "https://analytical.example",
            "registration_number": "00123456",
            "incorporation_country": "GB",
            "incorporation_type": "private_limited_company",
        },
        "address": {
            "property_number": "12",
            "building_name": "Analytical House",
            "line": "1 Analytical Engine",
            "city": "London",
            "postal_code": "SW1A 1AA",
            "country": "GB",
        },
    },
}

_CLIENT_UIDS = {
    "Minimal Individual": "7kQm4nWp2LxR",
    "Individual": "m3Rt8vNc6YhB",
    "Organisation": "p9Ls2wKd5FjA",
}
_SELFIE_UID = "s4Hn8qWe1TzC"
_PASSPORT_UID = "d6Kp3uYb9VxM"
_LICENCE_BACK_UID = "b5Rt2nXc8WqE"
_IDENTITY_CHECK_UID = "c2Gn7rQa4HjL"
_AML_CHECK_UID = "a8Wm5tYe1BkP"


def _client_response(name: str) -> dict[str, Any]:
    """A client body a caller receives, built from the matching request example."""
    body = dict(CLIENT_REQUEST_EXAMPLES[name])
    body["uid"] = _CLIENT_UIDS[name]
    body.setdefault("type", "person")
    body["custodians"] = []
    return body


CLIENT_RESPONSE_EXAMPLES: dict[str, dict[str, Any]] = {
    name: _client_response(name) for name in ("Minimal Individual", "Individual", "Organisation")
}

CLIENT_PAGE_EXAMPLE: dict[str, Any] = {
    "items": [
        CLIENT_RESPONSE_EXAMPLES["Individual"],
        CLIENT_RESPONSE_EXAMPLES["Organisation"],
    ],
    "limit": 50,
    "offset": 0,
}

_IDENTITY_CHECK: dict[str, Any] = {
    "uid": _IDENTITY_CHECK_UID,
    "kind": "identity",
    "status": "pending",
    "created_at": "2026-09-24T09:12:00Z",
    "updated_at": "2026-09-24T09:12:00Z",
    "document_uids": [_SELFIE_UID, _PASSPORT_UID],
    "issuing_country": "GB",
}
_AML_CHECK: dict[str, Any] = {
    "uid": _AML_CHECK_UID,
    "kind": "aml",
    "status": "pending",
    "created_at": "2026-09-24T09:15:00Z",
    "updated_at": "2026-09-24T09:15:00Z",
    "name": "Ada Augusta Lovelace",
    "dob": "1815-12-10",
    "country": "GB",
}
CHECK_RESPONSE_EXAMPLES: dict[str, dict[str, Any]] = {
    "Identity check": _IDENTITY_CHECK,
    "AML check": _AML_CHECK,
}
CHECKS_RESPONSE_EXAMPLES: dict[str, dict[str, Any]] = {
    "Pending": {
        "identity": {"status": "pending"},
        "aml": {"status": "pending"},
        "checks": [_AML_CHECK, _IDENTITY_CHECK],
    },
    "Identity clear, AML refer": {
        "identity": {"status": "clear"},
        "aml": {"status": "refer"},
        "checks": [
            {**_AML_CHECK, "status": "refer", "updated_at": "2026-09-24T14:40:00Z"},
            {**_IDENTITY_CHECK, "status": "clear", "updated_at": "2026-09-24T11:05:00Z"},
        ],
    },
}
DOCUMENT_RESPONSE_EXAMPLES: dict[str, dict[str, Any]] = {
    "Selfie": {"uid": _SELFIE_UID, "type": "selfie", "side": "front", "issuing_country": None},
    "Passport": {
        "uid": _PASSPORT_UID,
        "type": "passport",
        "side": "front",
        "issuing_country": "GB",
    },
    "Driving licence back": {
        "uid": _LICENCE_BACK_UID,
        "type": "drivers_license",
        "side": "back",
        "issuing_country": "GB",
    },
}


def _json_media(content: dict[Any, Any] | None) -> Any:
    if not content:
        return None
    for key, media in content.items():
        name = key.value if isinstance(key, Enum) else key
        if name == RequestEncodingType.JSON.value:
            return media
    return None


def _is_empty_schema(schema: Schema | Reference) -> bool:
    if not isinstance(schema, Schema):
        return False
    return (
        schema.type is None
        and not schema.properties
        and schema.items is None
        and not schema.all_of
        and not schema.any_of
        and not schema.one_of
        and not schema.enum
        and schema.const is None
    )


def _collapse_union(schema: Schema | Reference) -> Schema | Reference:
    """Drop the empty branch Litestar adds for ``Model | Response``."""
    if isinstance(schema, Schema) and schema.one_of:
        kept = [item for item in schema.one_of if not _is_empty_schema(item)]
        if len(kept) == 1:
            return kept[0]
    return schema


def _is_array(schema: Schema) -> bool:
    kind = schema.type
    if kind == OpenAPIType.ARRAY:
        return True
    return isinstance(kind, list) and OpenAPIType.ARRAY in kind


def _is_list_response(schema: Schema) -> bool:
    """A bare list, or a page whose payload is a list of items."""
    if _is_array(schema):
        return True
    items = (schema.properties or {}).get("items")
    return isinstance(items, Schema) and _is_array(items)


def _named_examples(examples: dict[str, Any]) -> dict[str, Example]:
    """Media-type examples. Scalar labels each one from ``summary``."""
    return {name: Example(summary=name, value=value) for name, value in examples.items()}


def _success_media(operation: Any, status: str) -> Any:
    if operation is None or not operation.responses:
        return None
    response = operation.responses.get(status)
    if response is None:
        response = operation.responses.get(int(status))
    if response is None or isinstance(response, Reference) or response.content is None:
        return None
    return _json_media(response.content)


def _shape_openapi(app: Litestar) -> None:
    """Named request and response examples, and list responses without a schema-name title."""
    spec = app.openapi_schema
    paths = spec.paths or {}
    create = paths["/clients"].post
    if (
        create is not None
        and create.request_body is not None
        and not isinstance(create.request_body, Reference)
    ):
        media = _json_media(create.request_body.content)
        if media is not None:
            media.examples = {
                name: Example(summary=name, value=value)
                for name, value in CLIENT_REQUEST_EXAMPLES.items()
            }
    schemas = spec.components.schemas or {}
    created = schemas.get("ClientCreate")
    if isinstance(created, Schema):
        created.examples = None
    overview = schemas.get("ChecksOut")
    if isinstance(overview, Schema) and overview.properties:
        # A required model field is emitted as a bare $ref, which drops the field description.
        for name, text in (
            ("identity", "Latest identity check for this client."),
            ("aml", "Latest AML check for this client."),
        ):
            prop = overview.properties.get(name)
            if isinstance(prop, Reference):
                prop.description = text
    for schema in schemas.values():
        if isinstance(schema, Schema) and _is_list_response(schema):
            schema.title = None
    for path_item in paths.values():
        for operation in (
            path_item.get,
            path_item.post,
            path_item.put,
            path_item.patch,
            path_item.delete,
        ):
            if operation is None or not operation.responses:
                continue
            for response in operation.responses.values():
                if isinstance(response, Reference) or response.content is None:
                    continue
                media = _json_media(response.content)
                if media is None or media.schema is None:
                    continue
                media.schema = _collapse_union(media.schema)
                if isinstance(media.schema, Schema) and _is_list_response(media.schema):
                    media.schema.title = None
    illustrated: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
        ("/clients", "post", "201", CLIENT_RESPONSE_EXAMPLES),
        ("/clients/{uid}", "get", "200", CLIENT_RESPONSE_EXAMPLES),
        ("/clients", "get", "200", {"Individual and organisation": CLIENT_PAGE_EXAMPLE}),
        ("/clients/{uid}/checks", "get", "200", CHECKS_RESPONSE_EXAMPLES),
        ("/checks/{uid}", "get", "200", CHECK_RESPONSE_EXAMPLES),
        ("/clients/{uid}/checks", "post", "201", CHECK_RESPONSE_EXAMPLES),
        ("/clients/{uid}/documents", "post", "201", DOCUMENT_RESPONSE_EXAMPLES),
    )
    for path, method, status, examples in illustrated:
        item = paths.get(path)
        if item is None:
            continue
        media = _success_media(getattr(item, method, None), status)
        if media is not None:
            media.examples = _named_examples(examples)
    health = paths.get("/health")
    if health is not None and health.get is not None:
        health.get.security = []


def create_app(
    *,
    stub: bool = True,
    store: ClientStore | None = None,
    screening_secret: str = "",
    request_limit: int = 10,
    request_period: float = 1.0,
    keys: KeyVerifier | None = None,
    accept_event: AcceptEvent | None = None,
) -> Litestar:
    if stub:
        bound: ClientStore = store or StubStore()
        verifier = None
    else:
        from external.db import accept_screening, production_keys, production_store

        bound = store if store is not None else production_store()
        verifier = keys if keys is not None else production_keys()
        if accept_event is None and keys is None and store is None:
            accept_event = accept_screening
    ctx = Ctx(
        stub=stub,
        store=bound,
        screening_secret=screening_secret,
        keys=verifier,
        requests=WindowLimiter(limit=request_limit, period=request_period),
        anon_requests=WindowLimiter(limit=request_limit, period=request_period),
        accept_event=accept_event,
    )

    @get("/docs", sync_to_thread=False, include_in_schema=False)
    def docs() -> Response[str]:
        return Response(
            content=reference_html(), media_type=MediaType.HTML, status_code=HTTP_200_OK
        )

    @get(SCALAR_SCRIPT, sync_to_thread=False, include_in_schema=False)
    def docs_script() -> Response[bytes]:
        return Response(
            content=scalar_bundle(),
            media_type="text/javascript",
            status_code=HTTP_200_OK,
        )

    @get(
        "/health",
        tags=[HEALTH],
        summary="Health check",
        description="Liveness. Returns ok when this process is serving. No authentication.",
        security=[],
        sync_to_thread=False,
    )
    def health() -> HealthOut:
        return HealthOut(status="ok")

    @post(
        "/clients",
        tags=[CLIENTS],
        summary="Create a client",
        description=(
            "A person needs first_name, last_name, dob, email, at least one nationality, "
            "and address. address.country is required. A company needs email, "
            "company.name, and either an incorporation country or address.country. "
            "A company does not need a nationality."
        ),
        responses=_errors(f"{_VERSION} Or invalid_body when the JSON body is rejected."),
    )
    async def create_client(
        data: ClientCreate, request: AppRequest
    ) -> ClientOut | Response[dict[str, str]]:
        denied = _require(request, "clients:write")
        if denied is not None:
            return denied
        return await _ctx(request.scope).store.create_client(data, tenant_id=_tenant(request))

    @get(
        "/clients",
        tags=[CLIENTS],
        summary="List clients",
        description="Returns the clients that belong to this key's firm.",
        responses=_errors(
            f"{_VERSION} Or invalid_page when limit is outside 1–100 or offset is negative."
        ),
    )
    async def list_clients(
        request: AppRequest, limit: PageLimit = 50, offset: PageOffset = 0
    ) -> ClientPage | Response[dict[str, str]]:
        denied = _require(request, "clients:read")
        if denied is not None:
            return denied
        if limit < 1 or limit > 100 or offset < 0:
            return problem("invalid_page", "limit must be 1–100 and offset must be ≥ 0.", 400)
        rows = await _ctx(request.scope).store.list_clients(
            tenant_id=_tenant(request), limit=limit, offset=offset
        )
        return ClientPage(items=rows, limit=limit, offset=offset)

    @get(
        "/clients/{uid:str}",
        tags=[CLIENTS],
        summary="Get a client",
        description="Returns one client belonging to this firm.",
        responses=_errors(_VERSION, not_found=True),
    )
    async def get_client(
        uid: ClientUid, request: AppRequest
    ) -> ClientOut | Response[dict[str, str]]:
        denied = _require(request, "clients:read")
        if denied is not None:
            return denied
        client = await _ctx(request.scope).store.get_client(uid, tenant_id=_tenant(request))
        if client is None:
            return problem("not_found", "No client with that id.", 404)
        return client

    @patch(
        "/clients/{uid:str}",
        tags=[CLIENTS],
        summary="Update a client",
        description=("Send only the fields that change. Unknown fields are rejected."),
        responses=_errors(
            f"{_VERSION} Or invalid_body when the JSON body is rejected.", not_found=True
        ),
    )
    async def update_client(
        uid: ClientUid, data: ClientPatch, request: AppRequest
    ) -> ClientOut | Response[dict[str, str]]:
        denied = _require(request, "clients:write")
        if denied is not None:
            return denied
        updated = await _ctx(request.scope).store.patch_client(
            uid, data, tenant_id=_tenant(request)
        )
        if updated is None:
            return problem("not_found", "No client with that id.", 404)
        return updated

    @get(
        "/clients/{uid:str}/checks",
        tags=[CHECKS],
        summary="List checks for a client",
        description=(
            "Returns the latest identity status, the latest AML status, and every "
            "check for this client, newest first. A status is null when that kind "
            "has not been started."
        ),
        responses=_errors(_VERSION, not_found=True),
    )
    async def list_checks(
        uid: ClientUid, request: AppRequest
    ) -> ChecksOut | Response[dict[str, str]]:
        denied = _require(request, "checks:read")
        if denied is not None:
            return denied
        store = _ctx(request.scope).store
        tenant_id = _tenant(request)
        if await store.get_client(uid, tenant_id=tenant_id) is None:
            return problem("not_found", "No client with that id.", 404)
        return await store.checks_for(uid, tenant_id=tenant_id)

    @post(
        "/clients/{uid:str}/documents",
        tags=[CHECKS],
        summary="Upload a document",
        response_description="The stored document. The image is not included.",
        description=(
            "JSON body with type and file. file is a raw base64 string, "
            "with no data: URL prefix. A decoded file larger than 50 MB is rejected. "
            "type is selfie, passport, drivers_license, or identity. "
            "A passport, driving licence, or identity document requires issuing_country, an "
            "ISO alpha-2 code. A selfie does not take issuing_country. side is front (the "
            "default) or back; upload the back of a driving licence or identity document as a "
            "second document with side back. A person client requires at least one "
            "nationality. The response is uid, type, side, and issuing_country. "
            "The image is not returned."
        ),
        responses=_errors(
            f"{_VERSION} Or invalid_body when the JSON body is rejected.",
            not_found=True,
            unprocessable="file_too_large when the decoded file is larger than 50 MB.",
        ),
    )
    async def create_document(
        uid: ClientUid, data: DocumentUpload, request: AppRequest
    ) -> DocumentOut | Response[dict[str, str]]:
        denied = _require(request, "checks:write")
        if denied is not None:
            return denied
        if len(data.content()) > MAX_FILE_BYTES:
            return problem("file_too_large", "The file is larger than 50 MB.", 422)
        held = held_document(data)
        try:
            return await _ctx(request.scope).store.create_document(
                uid, held, tenant_id=_tenant(request)
            )
        except Rejected as exc:
            return problem(exc.code, exc.message, exc.status)

    @post(
        "/clients/{uid:str}/checks",
        tags=[CHECKS],
        summary="Create a check",
        response_description="The check. The status starts as pending.",
        description=(
            "kind is identity or aml. A person client must include at least one nationality. "
            "identity runs against a selfie and a passport, driving licence, or identity document "
            "already uploaded for this client, with the back of that document when one was "
            "uploaded, and returns no URL. The newest of each is used. aml runs from the stored "
            "name, date of birth, address country, and nationality. An AML check without an "
            "address country is 422 address_country_required. A check does not take or create an "
            "account."
        ),
        responses=_errors(
            f"{_VERSION} Or invalid_body when the JSON body is rejected.",
            not_found=True,
            unprocessable=(
                "documents_required when identity is requested without a selfie and a passport, "
                "driving licence, or identity document, or address_country_required when aml is "
                "requested without an address country."
            ),
        ),
    )
    async def create_check(
        uid: ClientUid, data: CheckCreate, request: AppRequest
    ) -> CheckOut | Response[dict[str, str]]:
        denied = _require(request, "checks:write")
        if denied is not None:
            return denied
        try:
            return await _ctx(request.scope).store.create_check(
                uid, data, tenant_id=_tenant(request)
            )
        except Rejected as exc:
            return problem(exc.code, exc.message, exc.status)

    @get(
        "/checks/{uid:str}",
        tags=[CHECKS],
        summary="Get a check",
        description=(
            "Returns the same check object as one entry from the client list. "
            "An identity check includes the document uids used and the issuing country "
            "when it is known. An AML check includes the name, date of birth, and "
            "country that were screened. Hit lists are never included."
        ),
        responses=_errors(_VERSION, not_found=True),
    )
    async def get_check(uid: CheckUid, request: AppRequest) -> CheckOut | Response[dict[str, str]]:
        denied = _require(request, "checks:read")
        if denied is not None:
            return denied
        check = await _ctx(request.scope).store.get_check(uid, tenant_id=_tenant(request))
        if check is None:
            return problem("not_found", "No check with that id.", 404)
        return check

    @put(
        "/clients/{uid:str}/custodians",
        tags=[CUSTODIANS],
        summary="Record a custodian id",
        description=(
            "Stores the id the custodian assigned after the firm opened "
            "the client. Sending the same provider again replaces that id."
        ),
        responses=_errors(
            f"{_VERSION} Or invalid_body when the JSON body is rejected.", not_found=True
        ),
    )
    async def put_custodian(
        uid: ClientUid, data: CustodianPut, request: AppRequest
    ) -> ClientOut | Response[dict[str, str]]:
        denied = _require(request, "links:write")
        if denied is not None:
            return denied
        updated = await _ctx(request.scope).store.put_custodian(
            uid, data, tenant_id=_tenant(request)
        )
        if updated is None:
            return problem("not_found", "No client with that id.", 404)
        return updated

    @get(
        "/webhook",
        tags=[WEBHOOKS],
        summary="Get the webhook",
        description=("Returns the current endpoint. The secret is not included."),
        responses=_errors(_VERSION),
    )
    async def get_webhook(request: AppRequest) -> WebhookOut | Response[dict[str, str]]:
        denied = _require(request, "checks:read")
        if denied is not None:
            return denied
        return await _ctx(request.scope).store.get_webhook(tenant_id=_tenant(request))

    @put(
        "/webhook",
        tags=[WEBHOOKS],
        summary="Set the webhook",
        description=(
            "Replaces the endpoint and returns a new signing secret. "
            "The secret is shown only in this response. The URL must use HTTPS and its host "
            "must resolve to a public address."
        ),
        responses=_errors(
            f"{_VERSION} Or invalid_body when the JSON body is rejected, including a URL that "
            "is not HTTPS.",
            unprocessable=(
                "invalid_webhook_url when the host cannot be resolved or resolves to an "
                "address that is not public."
            ),
        ),
    )
    async def put_webhook(
        data: WebhookPut, request: AppRequest
    ) -> WebhookCreated | Response[dict[str, str]]:
        denied = _require(request, "checks:write")
        if denied is not None:
            return denied
        try:
            return await _ctx(request.scope).store.put_webhook(data.url, tenant_id=_tenant(request))
        except Rejected as exc:
            return problem(exc.code, exc.message, exc.status)

    @delete(
        "/webhook",
        status_code=HTTP_200_OK,
        tags=[WEBHOOKS],
        summary="Remove the webhook",
        description=("Clears the endpoint. Further check updates are not pushed."),
        responses=_errors(_VERSION),
    )
    async def delete_webhook(request: AppRequest) -> WebhookOut | Response[dict[str, str]]:
        denied = _require(request, "checks:write")
        if denied is not None:
            return denied
        return await _ctx(request.scope).store.delete_webhook(tenant_id=_tenant(request))

    @post("/internal/webhooks/screening", include_in_schema=False)
    async def screening_webhook(request: AppRequest) -> Response[dict[str, Any]]:
        # Same cap as the Resend callback: refuse before the read, then
        # again after it, and only then check the signature.
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > _SCREENING_MAX_BODY:
            return problem("body_too_large", "Request body is too large.", 413)
        raw = await request.body()
        if len(raw) > _SCREENING_MAX_BODY:
            return problem("body_too_large", "Request body is too large.", 413)
        header = request.headers.get(HEADER)
        secret = _ctx(request.scope).screening_secret
        if not verify(secret, raw, header):
            return problem("invalid_signature", "Invalid signature.", 400)
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return problem("invalid_body", "Body must be JSON.", 400)
        if not isinstance(event, dict):
            return problem("invalid_body", "Body must be a JSON object.", 400)
        # The event id is the dedupe key; an event without one cannot be stored once.
        if event_id(event) is None:
            return problem("invalid_body", "Body must carry an event id.", 400)
        ctx = _ctx(request.scope)
        if ctx.accept_event is not None:
            await ctx.accept_event(event)
        else:
            ctx.inbox.append(event)
        return Response(
            content={"received": True}, status_code=HTTP_200_OK, media_type=MediaType.JSON
        )

    from app.tracing import external_otel_plugin

    otel = external_otel_plugin()
    app = Litestar(
        plugins=[otel] if otel is not None else [],
        request_max_body_size=REQUEST_MAX_BODY,
        route_handlers=[
            docs,
            docs_script,
            health,
            create_client,
            list_clients,
            get_client,
            update_client,
            list_checks,
            create_document,
            create_check,
            get_check,
            put_custodian,
            get_webhook,
            put_webhook,
            delete_webhook,
            screening_webhook,
        ],
        openapi_config=OpenAPIConfig(
            title="Graphene API",
            version="v1",
            path="/schema",
            summary="Clients, checks, custodian links, and a check webhook.",
            description=guide_markdown(),
            components=Components(
                security_schemes={
                    "bearerAuth": SecurityScheme(
                        type="http",
                        scheme="bearer",
                        bearer_format="API key",
                        description=(
                            "API key sent as Authorization: Bearer <key>. "
                            "Live keys start with live_. Test keys start with test_. "
                            "The stub accepts only test_dummy."
                        ),
                    )
                }
            ),
            security=[{"bearerAuth": []}],
            tags=[
                Tag(
                    name=HEALTH,
                    description=(
                        "Liveness. Returns ok when this process is serving. No authentication."
                    ),
                ),
                Tag(name=CLIENTS, description=tag_guide("clients")),
                Tag(name=CHECKS, description=tag_guide("checks")),
                Tag(
                    name=WEBHOOKS,
                    description=(
                        "Where check updates are delivered. The signing secret is returned "
                        "only when the webhook is set or replaced."
                    ),
                ),
                Tag(
                    name=CUSTODIANS,
                    description=(
                        "Record the id a custodian assigned after the firm opened the client "
                        "at Seccl or SEI."
                    ),
                ),
            ],
        ),
        exception_handlers={ValidationException: invalid_body},
        middleware=[
            DefineMiddleware(VersionMiddleware, stub=stub),
            DefineMiddleware(ContextMiddleware, ctx=ctx),
        ],
    )
    _shape_openapi(app)
    return app
