"""Live push for the messaging prototype — a single-process mock, not the production design.

A ticket is minted by an authenticated REST call (`POST /messaging/ticket`) and redeemed once
by the SSE reader that follows, because an `EventSource` cannot carry the usual
`Authorization: Bearer` header — see docs/messaging.md for why this exists instead of the raw
access token ever reaching browser JS.

A push reaches every queued SUBSCRIBER (`subscribe`), which is what the SSE endpoints stream. The
queue is deliberately transport-neutral — it knows nothing about SSE beyond `sse_stream` at the
bottom of this module, which is only a wire format over the same queue — so another transport (or
a test) consumes a subscriber without touching `push`.

Both stores are plain module-level dicts: correct for exactly one API process. Scaling past one
instance needs the registry replaced with a Postgres `LISTEN`/`NOTIFY` fan-out (every instance
listens on the same channel, so delivery works regardless of which instance holds the connection
or handled the POST) — noted here rather than guessed at, since nothing in this prototype depends
on which one you'd pick later. The notification centre already does exactly that
(`app/notifications/realtime.py`), and it pushes through this module, so the SSE readers get the
cross-process half for free.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import signal
import sys
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from types import FrameType
from typing import Any

from fastapi.encoders import jsonable_encoder

TICKET_TTL_SECONDS = 30

#: The private request header an SSE reader carries its ticket in. A header, never a query
#: parameter: an `EventSource` URL lands in access logs, browser history and any proxy in front of
#: us, and a ticket in a log line is a ticket somebody else can redeem within its TTL. (The
#: portals reach this through their own server-side proxy, which is what lets a header be set at
#: all — `EventSource` itself cannot.)
TICKET_HEADER = "x-realtime-ticket"

#: How many undelivered payloads one subscriber may hold before the queue is collapsed to a
#: resync (see `_offer`). Generous for a reader that is keeping up, and bounded so one that is
#: not cannot cost us memory in proportion to how far behind it has fallen.
QUEUE_MAXSIZE = 64

#: Sent when a subscriber's queue overflowed: the reader's picture is incomplete and it must
#: refetch. The same "invalidate, don't trust the payload" discipline the notification push uses —
#: which is why dropping the queued payloads is safe rather than merely cheap.
RESYNC: dict[str, Any] = {"type": "resync"}

#: How long a stream waits for a payload before emitting a heartbeat comment. Anything between us
#: and the browser (nginx, a load balancer, a phone's radio) will eventually reap a connection
#: that has said nothing; the comment is the cheapest possible "still here", and it is what makes
#: a half-open connection surface as a write failure rather than as silence.
HEARTBEAT_SECONDS = 15

#: `no-transform` as well as `no-cache` because a compressing proxy will happily buffer a stream
#: to get something worth compressing, and `X-Accel-Buffering` is nginx's own opt-out — without
#: them the first frames sit in a buffer and the channel reads as broken.
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@dataclass
class Ticket:
    key: str
    expires_at: float


@dataclass(eq=False)
class Subscriber:
    """One live reader of `key`.

    `eq=False` so the identity hash stands: two readers of the same key are two subscribers, and
    the one that goes away is the one that is removed.
    """

    key: str
    queue: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    )


_tickets: dict[str, Ticket] = {}
_subscribers: dict[str, set[Subscriber]] = {}


def client_key(contact_id: int) -> str:
    return f"client:{contact_id}"


def staff_key(tenant_id: int) -> str:
    return f"staff:{tenant_id}"


def mint_ticket(key: str) -> str:
    ticket = secrets.token_urlsafe(24)
    _tickets[ticket] = Ticket(key=key, expires_at=time.monotonic() + TICKET_TTL_SECONDS)
    return ticket


def redeem_ticket(ticket: str) -> str | None:
    """Single-use: pops the ticket regardless of outcome, so a leaked/retried value never works
    twice."""
    entry = _tickets.pop(ticket, None)
    if entry is None or time.monotonic() > entry.expires_at:
        return None
    return entry.key


def subscribe(key: str) -> Subscriber:
    """Register a queued reader of `key` and hand back its own queue.

    Called BEFORE the response starts streaming, not from inside the stream: everything between
    redeeming the ticket and the first frame would otherwise be pushed to nobody, and a missed
    payload on connect is exactly the case a reader has no way to notice.
    """
    subscriber = Subscriber(key=key)
    _subscribers.setdefault(key, set()).add(subscriber)
    return subscriber


def unsubscribe(key: str, subscriber: Subscriber) -> None:
    """Idempotent: the stream's `finally` may run after the key has already been reaped, and a
    disconnect must never raise on the way out."""
    subscribers = _subscribers.get(key)
    if not subscribers:
        return
    subscribers.discard(subscriber)
    if not subscribers:
        _subscribers.pop(key, None)


def _offer(subscriber: Subscriber, payload: dict[str, Any]) -> None:
    """Enqueue one payload for one subscriber — never blocking, never growing.

    A queue only fills when the reader has stopped draining it (a suspended tab, a stalled
    connection, a browser that will never come back until its TCP timeout does). Growing it would
    make one such reader's memory cost proportional to how long it stays away, and the payloads
    are messages and notification nudges: unbounded is not a size we get to choose.

    So overflow DISCARDS the backlog and leaves a single `RESYNC` frame. That is honest rather
    than lossy: the reader is told its picture is incomplete and refetches, which is what it does
    on connect anyway. Dropping the newest payload instead would leave the reader believing a
    complete history it no longer has, and dropping just the oldest would keep the queue full for
    ever with a reader that is not reading. The drain leaves the queue empty, so the `put_nowait`
    after it cannot fail — nothing awaits in between, so no other coroutine can refill it.
    """
    try:
        subscriber.queue.put_nowait(payload)
        return
    except asyncio.QueueFull:
        pass
    while True:
        try:
            subscriber.queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    subscriber.queue.put_nowait(dict(RESYNC))


async def push(key: str, payload: dict[str, object]) -> None:
    """Best-effort: the REST call that triggered this already succeeded and persisted the
    message; live delivery is a bonus, never the thing that makes a send fail.

    `jsonable_encoder` first: `payload["message"]` is a DB row mapping straight from
    app/messaging.py, so its `created_at` is a real `datetime`, not a string, and the SSE frame
    is written with plain stdlib `json.dumps` (`sse_frame`, below) — NOT FastAPI's own encoder the
    way an HTTP JSONResponse gets it for free — so a raw datetime would raise a `TypeError` deep
    inside the stream."""
    # `sent_by_me` is a statement about ONE reader, and a broadcast has many. The row being
    # pushed is the one handed back to the SENDER, where the flag is true — delivered as-is,
    # every other adviser on the tenant would see that message drawn as their own. Stripped
    # here rather than at each call site because this is the chokepoint every broadcast passes
    # through, and a per-site rule is one a new call site can forget. Its absence reads as
    # "not mine", which is right for everyone this payload can reach: the sender already has
    # the message from their own response and de-duplicates the echo by id.
    message = payload.get("message")
    if isinstance(message, dict) and "sent_by_me" in message:
        payload = {**payload, "message": {k: v for k, v in message.items() if k != "sent_by_me"}}
    # Encoded ONCE: the SSE frames need it because `json.dumps` (the bug the docstring above
    # records) cannot serialise a raw `datetime`.
    encoded = jsonable_encoder(payload)
    # A copy of the set: `_offer` never awaits, but a subscriber that goes away mid-fan-out
    # would still mutate the set this loop is walking.
    for subscriber in tuple(_subscribers.get(key, ())):
        _offer(subscriber, encoded)


# Set when this process is asked to stop (SIGINT/SIGTERM: a reload, a deploy, Ctrl+C).
# An open stream waits on it, so the socket closes before uvicorn's graceful-shutdown
# timeout cancels the task and logs that cancellation as an application exception.
# A bool plus futures, not asyncio.Event: an Event binds to the first loop that waits
# on it, and the test suite builds a fresh loop per test.
_stopping = False
_stop_waiters: list[asyncio.Future[None]] = []
_HEARTBEAT = object()
_STOP = object()
_chained_signals: set[int] = set()


def request_server_stop() -> None:
    """End every open SSE stream. The generators return; their ``finally`` unsubscribes."""
    global _stopping
    _stopping = True
    waiters = list(_stop_waiters)
    _stop_waiters.clear()
    for fut in waiters:
        if fut.done():
            continue
        try:
            fut.set_result(None)
        except RuntimeError:
            # The waiter belongs to a loop that has already closed.
            pass


def reset_server_stop() -> None:
    """Test hook. A stop flag left set would end the next stream before its first payload."""
    global _stopping
    _stopping = False
    _stop_waiters.clear()


async def _wait_for_stop() -> None:
    if _stopping:
        return
    fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    _stop_waiters.append(fut)
    try:
        await fut
    finally:
        try:
            _stop_waiters.remove(fut)
        except ValueError:
            # request_server_stop already dropped this waiter and completed it.
            pass


def chain_stop_signal(sig: int, notify: Callable[[], None]) -> None:
    """Run ``notify`` and then the handler already installed for ``sig``.

    Uvicorn installs its own handlers before the lifespan, and a reload depends on them.
    Wrapping rather than replacing keeps that path. Idempotent per signal: a second call
    would otherwise chain our own handler and recurse when the signal arrives.
    """
    if sig in _chained_signals:
        return
    previous = signal.getsignal(sig)

    def handler(signum: int, frame: FrameType | None) -> None:
        try:
            notify()
        except Exception:  # noqa: BLE001 — a failed wake must not swallow the real handler
            request_server_stop()
        if callable(previous):
            previous(signum, frame)

    signal.signal(sig, handler)
    _chained_signals.add(sig)


def arm_server_stop() -> None:
    """Close live streams when the process is signalled, ahead of uvicorn's shutdown wait.

    Not installed under pytest: the runner owns SIGINT. No-op when there is no running loop
    (``call_soon_threadsafe`` is how a signal handler, which is not an asyncio callback, wakes
    the streams).
    """
    if "pytest" in sys.modules:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    def notify() -> None:
        loop.call_soon_threadsafe(request_server_stop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            chain_stop_signal(sig, notify)
        except (ValueError, OSError):
            return


def _drain_task(task: asyncio.Task[Any]) -> None:
    """Retrieve a task's exception so a cancelled getter does not warn later.

    ``Queue.get`` leaves an item on the deque when its waiter is cancelled, which is what
    the heartbeat relies on. The retrieval here only stops the loop logging that cancellation.
    """
    try:
        task.exception()
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        return


async def _next_payload(queue: asyncio.Queue[Any]) -> Any:
    """The next queued payload, ``_STOP`` when the process is stopping, or ``_HEARTBEAT``.

    The wait is the race of those three. A timeout cancels the pending ``get()`` without
    taking an item that arrived as the timeout fired — the same guarantee ``wait_for`` gave.
    """
    if _stopping:
        return _STOP
    get_task: asyncio.Task[Any] = asyncio.create_task(queue.get())
    stop_task: asyncio.Task[None] = asyncio.create_task(_wait_for_stop())
    get_task.add_done_callback(_drain_task)
    stop_task.add_done_callback(_drain_task)
    try:
        done, _pending = await asyncio.wait(
            {get_task, stop_task},
            timeout=HEARTBEAT_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        for task in (get_task, stop_task):
            if not task.done():
                task.cancel()
    if get_task in done and not get_task.cancelled():
        return get_task.result()
    if _stopping or (
        stop_task.done() and not stop_task.cancelled() and stop_task.exception() is None
    ):
        return _STOP
    return _HEARTBEAT


def sse_frame(payload: dict[str, Any]) -> str:
    """One SSE data frame. `json.dumps` escapes every newline, so the payload cannot break the
    framing by carrying one — a message body is client-typed text, so this is not theoretical."""
    return f"data: {json.dumps(payload)}\n\n"


async def sse_stream(subscriber: Subscriber) -> AsyncIterator[str]:
    """The SSE wire form of one subscriber's queue, and the owner of its lifetime.

    The opening comment goes out immediately and exists to be flushed: a browser's `EventSource`
    stays in `CONNECTING` until the response body has actually begun, so without a first byte a
    perfectly healthy stream looks like a connection that never opened, and any buffering proxy
    in the path has nothing to push through. A comment rather than an event because a reader must
    not have to filter a synthetic "hello" out of its own event handling.

    The heartbeat is the same shape: nothing to send becomes a comment on a fixed cadence, so an
    idle channel keeps proving itself against the timeouts between us and the browser. The wait
    also ends when the process is signalled (`arm_server_stop`), and the generator returns so
    the socket closes. Uvicorn's graceful shutdown does not close an unfinished response — it
    only clears keep-alive, then after the timeout cancels the task and logs that
    ``CancelledError`` as an application exception. Finishing here is what keeps a reload from
    printing that traceback. Cancelling the pending `get()` cannot lose a payload —
    `asyncio.Queue.get` leaves the item on the deque when its getter is cancelled.

    Unregistering is this generator's `finally`, so it runs on a clean end (including that
    stop), on a client disconnect (the iteration is closed, raising `GeneratorExit` here) and
    on the cancellation a shutdown delivers when the stream did not finish in time. Putting it
    in the endpoint instead would only run it for the first of those. The opening comment is
    INSIDE the `try` for the same reason: suspended at a yield outside it, a `GeneratorExit`
    unwinds past no `finally` at all, so a reader that went away before its first payload —
    the fastest reconnect loop there is — left its subscriber behind for ever.
    """
    try:
        yield ": open\n\n"
        while True:
            nxt = await _next_payload(subscriber.queue)
            if nxt is _STOP:
                return
            if nxt is _HEARTBEAT:
                yield ": keep-alive\n\n"
                continue
            yield sse_frame(nxt)
    finally:
        unsubscribe(subscriber.key, subscriber)
