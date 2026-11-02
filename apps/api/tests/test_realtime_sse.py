"""The SSE endpoints of the live channel — `GET /messaging/events` and `GET /notifications/events`.

The queue and wire-format mechanics they stream are covered in tests/test_live_push.py; what
matters here is the endpoint contract:

  * the ticket is read from the PRIVATE header and redeemed single-use, so a missing, bogus or
    already-spent one is a 401 before a stream opens rather than a stream that silently carries
    somebody else's key;
  * neither route takes a dependency, because a session held for the life of a connection is a
    pooled connection spent on a reader doing nothing.

A successful stream is exercised by calling the endpoint and reading its `body_iterator` rather
than over ASGI: httpx's `ASGITransport` buffers a response until it completes, and this one never
does, so an HTTP-level test of the happy path would simply hang. The 401s DO complete, so those
run over real HTTP and prove the routing and header wiring.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app import live_push
from app.main import app
from app.routers.messaging import messaging_events
from app.routers.notifications import notifications_events

STREAMS = (
    ("/messaging/events", messaging_events),
    ("/notifications/events", notifications_events),
)


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    yield
    live_push._subscribers.clear()  # noqa: SLF001 - the registry under test


def _routes() -> list[Any]:
    """Every route in the app. Included routers are nested rather than flattened into
    `app.routes` here, so this recurses — the walk tests/test_notifications.py uses."""

    def walk(routes: Any) -> Any:
        for route in routes:
            yield route
            inner = getattr(route, "original_router", None)
            if inner is not None:
                yield from walk(inner.routes)

    return list(walk(app.routes))


def _api_route(path: str) -> APIRoute:
    return next(r for r in _routes() if isinstance(r, APIRoute) and r.path == path)


def _request(path: str, ticket: str | None) -> Request:
    headers = [] if ticket is None else [(live_push.TICKET_HEADER.encode(), ticket.encode())]
    return Request(
        {"type": "http", "method": "GET", "path": path, "headers": headers, "query_string": b""}
    )


async def _frames(response: Any, count: int) -> list[str]:
    """The first `count` frames off a StreamingResponse, then close it — closing is what runs the
    stream's own cleanup, exactly as a client disconnect would."""
    iterator: AsyncIterator[str] = response.body_iterator
    try:
        return [await anext(iterator) for _ in range(count)]
    finally:
        await iterator.aclose()  # type: ignore[attr-defined]


# ---- routing + posture -----------------------------------------------------------------------


@pytest.mark.parametrize("path", [p for p, _ in STREAMS])
def test_the_stream_route_is_registered_as_a_get(path: str) -> None:
    assert _api_route(path).methods == {"GET"}


@pytest.mark.parametrize("path", [p for p, _ in STREAMS])
def test_the_stream_route_takes_no_dependency(path: str) -> None:
    """The tripwire for the rule that matters operationally: a `Depends(...)` here is a database
    session (or anything else pooled) held open for as long as the browser stays connected."""
    assert _api_route(path).dependant.dependencies == []


# ---- the ticket ------------------------------------------------------------------------------


@pytest.mark.parametrize("path", [p for p, _ in STREAMS])
async def test_no_ticket_header_is_a_401(path: str) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get(path)).status_code == 401


@pytest.mark.parametrize("path", [p for p, _ in STREAMS])
async def test_an_unknown_ticket_is_a_401(path: str) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path, headers={live_push.TICKET_HEADER: "not-a-real-ticket"})
    assert response.status_code == 401
    assert not live_push._subscribers  # noqa: SLF001 - a refusal subscribes nobody


@pytest.mark.parametrize(("path", "endpoint"), STREAMS)
async def test_a_ticket_cannot_open_a_second_stream(path: str, endpoint: Any) -> None:
    """Single-use is the whole of the authorisation here: a ticket that survived its redemption
    would be a bearer credential sitting in whatever handled the first request."""
    ticket = live_push.mint_ticket("client:601")
    response = await endpoint(_request(path, ticket))
    await _frames(response, 1)
    with pytest.raises(HTTPException) as refused:
        await endpoint(_request(path, ticket))
    assert refused.value.status_code == 401


# ---- the stream ------------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "endpoint"), STREAMS)
async def test_a_valid_ticket_opens_an_event_stream(path: str, endpoint: Any) -> None:
    ticket = live_push.mint_ticket("client:602")
    response = await endpoint(_request(path, ticket))
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert await _frames(response, 1) == [": open\n\n"]


@pytest.mark.parametrize(("path", "endpoint"), STREAMS)
async def test_the_stream_carries_what_is_pushed_to_its_key(path: str, endpoint: Any) -> None:
    key = "staff:603"
    ticket = live_push.mint_ticket(key)
    response = await endpoint(_request(path, ticket))
    iterator: AsyncIterator[str] = response.body_iterator
    try:
        assert await anext(iterator) == ": open\n\n"
        await live_push.push(key, {"type": "notification"})
        assert await anext(iterator) == 'data: {"type": "notification"}\n\n'
    finally:
        await iterator.aclose()  # type: ignore[attr-defined]


@pytest.mark.parametrize(("path", "endpoint"), STREAMS)
async def test_the_reader_is_subscribed_before_the_stream_is_iterated(
    path: str, endpoint: Any
) -> None:
    """Subscription happens in the endpoint, not on the first frame: anything pushed between the
    ticket being redeemed and the response being iterated would otherwise reach nobody, and a
    missed payload on connect is precisely the case a reader cannot detect."""
    key = "client:604"
    response = await endpoint(_request(path, live_push.mint_ticket(key)))
    await live_push.push(key, {"type": "message"})
    assert await _frames(response, 2) == [": open\n\n", 'data: {"type": "message"}\n\n']


@pytest.mark.parametrize(("path", "endpoint"), STREAMS)
async def test_closing_the_stream_unsubscribes(path: str, endpoint: Any) -> None:
    key = "client:605"
    response = await endpoint(_request(path, live_push.mint_ticket(key)))
    assert key in live_push._subscribers  # noqa: SLF001 - the invariant under test
    await _frames(response, 1)
    assert key not in live_push._subscribers  # noqa: SLF001 - the invariant under test
