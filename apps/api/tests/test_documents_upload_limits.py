"""The upload size ceiling, tested at the layer that actually sees the bytes.

The audit's finding was that a chunked upload with no ``Content-Length`` could make the API buffer
an unbounded body. ``storage.read_capped`` bounds the in-process copy, but it runs far too late:
FastAPI resolves ``request.form()`` before the endpoint (and before its dependencies), and
Starlette's multipart parser caps NON-file parts only — a file part is spooled to disk with no
ceiling at all. So these
tests drive the real ASGI app over HTTP with a *streamed* multipart body and assert the request is
refused with 413 rather than parsed. A 401 in place of a 413 is the failure signal: it means the
body was accepted and parsed, and only then did authentication reject it.

Nothing here needs Docker — the ceiling fires before any dependency (and therefore before any DB
session) is solved, which is the whole point.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.routing import BaseRoute

from app.main import app
from app.routers import documents as docs
from tests.conftest import api_routes

BOUNDARY = "----grapheneuploadtest"
CONTENT_TYPE = {"content-type": f"multipart/form-data; boundary={BOUNDARY}"}

UPLOAD_PATH = "/documents/upload"
FULFIL_PATH = "/documents/requests/{req_uid}/items/{item_uid}/fulfill"
FILES_UPLOAD_PATH = "/files/upload"


def _route(path: str, method: str = "POST") -> BaseRoute:
    """The live route object the app would dispatch to — so a test patches the SAME instance the
    request goes through, and a route shadowed by another route is caught rather than assumed."""
    for route in api_routes(app):
        if getattr(route, "path", None) == path and method in getattr(route, "methods", ()):
            return route
    raise AssertionError(f"no {method} route registered at {path}")


async def _multipart(total_bytes: int, *, filename: str = "big.pdf") -> AsyncIterator[bytes]:
    """A streamed (chunked, no Content-Length) multipart body carrying one file part of
    ``total_bytes``. Deliberately an async generator: httpx then sends it with
    ``Transfer-Encoding: chunked``, which is exactly the request shape the cap has to survive."""
    yield (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode()
    block = b"%PDF-1.4" + b"x" * (64 * 1024 - 8)
    sent = 0
    while sent < total_bytes:
        take = min(len(block), total_bytes - sent)
        yield block[:take]
        sent += take
    yield f"\r\n--{BOUNDARY}--\r\n".encode()


async def _post(path: str, body: AsyncIterator[bytes] | bytes) -> int:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(path, content=body, headers=CONTENT_TYPE)
    return resp.status_code


# ── the wrapper itself ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capped_receive_refuses_the_first_byte_past_the_limit() -> None:
    """Exactly at the limit is fine; one byte more raises, and the remaining messages are never
    pulled (the point is to stop paying for the body, not to read it and complain)."""
    messages = [
        {"type": "http.request", "body": b"a" * 8, "more_body": True},
        {"type": "http.request", "body": b"b" * 8, "more_body": True},
        {"type": "http.request", "body": b"c", "more_body": False},
    ]
    pulled = 0

    async def receive() -> dict[str, object]:
        nonlocal pulled
        message = messages[pulled]
        pulled += 1
        return message

    capped = docs.capped_receive(receive, 16)
    assert (await capped())["body"] == b"a" * 8
    assert (await capped())["body"] == b"b" * 8
    with pytest.raises(HTTPException) as ei:
        await capped()
    assert ei.value.status_code == 413
    assert pulled == 3  # it raised on the message that broke the cap, and read no further


# ── the wiring: every upload route on the app ─────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (UPLOAD_PATH, docs.MAX_UPLOAD_BODY_BYTES),
        # The client-reachable fulfilment upload and the supporting-files upload take ONE file, so
        # they must not inherit the multi-file drop's allowance.
        (FULFIL_PATH, docs.MAX_SINGLE_UPLOAD_BODY_BYTES),
        (FILES_UPLOAD_PATH, docs.MAX_SINGLE_UPLOAD_BODY_BYTES),
    ],
)
def test_every_upload_route_declares_a_body_ceiling(path: str, expected: int) -> None:
    """A ceiling on a route nothing dispatches to is no ceiling. Resolve the live route the app
    would actually use (``include_router`` re-creates routes, so the class has to survive that) and
    check the limit it carries."""
    route = _route(path)
    assert isinstance(route, docs.CappedBodyRoute)
    assert route.body_limit == expected


# ── the ceiling must not leak off the upload routes ───────────────────────────


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/documents/generate", "POST"),
        ("/documents/requests", "POST"),
        ("/documents/{uid}/send", "POST"),
        ("/documents/{uid}", "PATCH"),
        ("/documents/requests/{req_uid}/items/{item_uid}/form-response", "POST"),
        ("/files", "GET"),
        ("/files/{uid}", "DELETE"),
    ],
)
def test_a_json_route_does_not_inherit_an_upload_allowance(path: str, method: str) -> None:
    """The cap is set on the ROUTER, so before this every JSON endpoint advertised the multi-file
    drop's 251 MiB of pre-auth buffering — 250x what any of them can use. Each of these wants
    kilobytes; the upload routes opt into more, the rest get the JSON ceiling."""
    route = _route(path, method)
    assert isinstance(route, docs.CappedBodyRoute)
    assert route.body_limit == docs.MAX_JSON_BODY_BYTES
    assert route.body_limit < docs.MAX_SINGLE_UPLOAD_BODY_BYTES


@pytest.mark.asyncio
async def test_an_over_cap_json_body_is_refused_before_it_is_parsed() -> None:
    """The probe that exposed the leak: a 2 MiB JSON body to /documents/generate was read in full
    and answered 422 (i.e. accepted under the router-wide upload cap). It must now be refused."""
    body = b'{"template_uid":"' + b"x" * (2 * 1024 * 1024) + b'"}'
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/documents/generate", content=body, headers={"content-type": "application/json"}
        )
    assert resp.status_code == 413
    # A 1 MiB ceiling has to READ as 1 MiB — see test_the_413_message_never_says_zero_mib.
    assert resp.json()["detail"] == "Request body exceeds the 1 MiB limit."


@pytest.mark.asyncio
async def test_an_ordinary_json_body_still_reaches_authentication() -> None:
    """The control: the JSON ceiling must refuse only over-cap bodies, never a real request."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/documents/generate",
            content=b'{"template_uid":"tpl_x","answers":{}}',
            headers={"content-type": "application/json"},
        )
    assert resp.status_code in (401, 403)


# ── the 413 is part of the declared contract ──────────────────────────────────


@pytest.mark.parametrize(
    ("path", "method"),
    [
        (UPLOAD_PATH, "post"),
        (FILES_UPLOAD_PATH, "post"),
        (FULFIL_PATH, "post"),
        ("/documents/generate", "post"),
    ],
)
def test_the_413_is_declared_in_the_schema(path: str, method: str) -> None:
    """A limit that appears in no route's OpenAPI responses gives the generated client (and so the
    portals) nothing to handle: the UI can only discover it by guessing at an untyped error. The
    route class declares it, so it cannot be forgotten on a route that carries the cap."""
    op = app.openapi()["paths"][path][method]
    assert "413" in op["responses"]
    schema = op["responses"]["413"]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("/BodyTooLarge")


def test_a_bodyless_method_does_not_advertise_a_413() -> None:
    """Honesty in the other direction: a GET cannot breach the cap in practice, so declaring it
    would just be noise in every portal's generated types."""
    paths = app.openapi()["paths"]
    assert "413" not in paths["/documents/{uid}"]["get"]["responses"]
    assert "413" in paths["/documents/{uid}"]["patch"]["responses"]


def test_the_413_message_never_says_zero_mib() -> None:
    """``limit // (1024*1024)`` renders any sub-MiB ceiling as "0 MiB" — nonsense for an operator to
    decode from a support ticket, and reachable the moment a ceiling is tuned below 1 MiB."""
    assert docs.body_too_large(128 * 1024).detail == "Request body exceeds the 128 KiB limit."
    assert docs.body_too_large(900).detail == "Request body exceeds the 900 bytes limit."
    assert docs.body_too_large(26 * 1024 * 1024).detail == "Request body exceeds the 26 MiB limit."


@pytest.mark.asyncio
async def test_a_streamed_over_cap_upload_is_refused_before_it_is_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audit's case: no Content-Length, so nothing can pre-check the declared size. Before the
    fix Starlette spooled the whole part to disk and FastAPI then rejected the *request* on auth —
    i.e. we paid for every byte. Now it is a 413, and a 401 here would mean the body got through."""
    monkeypatch.setattr(_route(UPLOAD_PATH), "body_limit", 128 * 1024)
    assert await _post(UPLOAD_PATH, _multipart(1024 * 1024)) == 413


@pytest.mark.asyncio
async def test_an_under_cap_upload_still_reaches_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control for the test above: the ceiling must refuse only over-cap bodies. An ordinary
    upload parses and is then rejected for having no session — never 413."""
    monkeypatch.setattr(_route(UPLOAD_PATH), "body_limit", 1024 * 1024)
    assert await _post(UPLOAD_PATH, _multipart(32 * 1024)) in (401, 403)


@pytest.mark.asyncio
async def test_a_declared_over_cap_content_length_is_refused_outright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client that DOES declare its size is refused from the header alone, without reading it."""
    monkeypatch.setattr(_route(UPLOAD_PATH), "body_limit", 4096)
    body = b"".join([b"--" + BOUNDARY.encode(), b"\r\n", b"x" * 32 * 1024])
    assert await _post(UPLOAD_PATH, body) == 413


@pytest.mark.asyncio
async def test_the_client_fulfilment_upload_is_capped_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one upload path a CLIENT can reach. It is registered imperatively to carry the tighter
    ceiling, so this also proves no other route shadows it."""
    route = _route(FULFIL_PATH)
    monkeypatch.setattr(route, "body_limit", 64 * 1024)
    path = "/documents/requests/req_x/items/rqi_x/fulfill"
    assert await _post(path, _multipart(512 * 1024)) == 413


@pytest.mark.asyncio
async def test_the_real_single_upload_ceiling_holds() -> None:
    """No patched limits: stream one byte past the shipped ``MAX_SINGLE_UPLOAD_BODY_BYTES`` and the
    request is refused. Proves the constant, not just the mechanism."""
    over = docs.MAX_SINGLE_UPLOAD_BODY_BYTES + 1
    assert await _post(FILES_UPLOAD_PATH, _multipart(over)) == 413


@pytest.mark.asyncio
async def test_a_bodyless_request_is_untouched() -> None:
    """The wrapper sits on every route of the documents/files routers, so a GET must be unaffected
    (it reaches auth and is refused there, not at the body layer)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/documents")
    assert resp.status_code in (401, 403)
