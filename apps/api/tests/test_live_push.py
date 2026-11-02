"""Pure-logic coverage for app/live_push.py — no DB, no network. The router-level tests
(test_messaging.py) prove the ticket is correctly KEYED (client vs staff, per-tenant); this file
proves the ticket store's own mechanics: single-use, expiry, and the subscriber queues the SSE
endpoints stream (tests/test_realtime_sse.py drives those endpoints)."""

from __future__ import annotations

import asyncio
import json
import signal
import time
from typing import Any

import pytest

from app import live_push


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    """No test may inherit another's subscribers: the registry is process-global, and a leaked
    subscriber would receive a later test's push and quietly change what it asserts."""
    live_push.reset_server_stop()
    yield
    live_push._subscribers.clear()  # noqa: SLF001 - the registry under test
    live_push.reset_server_stop()


def test_client_key_and_staff_key_are_distinct_namespaces() -> None:
    assert live_push.client_key(1) != live_push.staff_key(1)


def test_a_ticket_redeems_exactly_once() -> None:
    ticket = live_push.mint_ticket("client:1")
    assert live_push.redeem_ticket(ticket) == "client:1"
    assert live_push.redeem_ticket(ticket) is None  # single-use


def test_an_unknown_ticket_does_not_redeem() -> None:
    assert live_push.redeem_ticket("not-a-real-ticket") is None


def test_an_expired_ticket_does_not_redeem(monkeypatch) -> None:  # noqa: ANN001
    ticket = live_push.mint_ticket("staff:1")
    real_monotonic = time.monotonic
    # Fast-forward past the TTL rather than sleeping in a test.
    future = real_monotonic() + live_push.TICKET_TTL_SECONDS + 1
    monkeypatch.setattr(time, "monotonic", lambda: future)
    assert live_push.redeem_ticket(ticket) is None


# ---- subscriber queues (the SSE streams read these directly) ----------------------------------


def test_subscribe_and_unsubscribe_clean_up_the_empty_key() -> None:
    key = "client:501"
    subscriber = live_push.subscribe(key)
    assert key in live_push._subscribers  # noqa: SLF001 - the invariant under test
    live_push.unsubscribe(key, subscriber)
    assert key not in live_push._subscribers  # noqa: SLF001 - the invariant under test


def test_two_subscribers_on_one_key_are_independent() -> None:
    """A client with two tabs open is two readers: unsubscribing one must not silence the other,
    which is what a single-subscriber-per-key registry would do."""
    key = "client:502"
    first = live_push.subscribe(key)
    second = live_push.subscribe(key)
    live_push.unsubscribe(key, first)
    assert live_push._subscribers[key] == {second}  # noqa: SLF001 - the invariant under test


def test_unsubscribe_of_an_unknown_subscriber_is_a_no_op() -> None:
    live_push.unsubscribe("nobody:0", live_push.Subscriber(key="nobody:0"))


async def test_push_enqueues_for_the_addressed_key_only() -> None:
    mine = live_push.subscribe("client:503")
    theirs = live_push.subscribe("client:504")
    await live_push.push("client:503", {"type": "message", "conversation_id": 7})
    assert mine.queue.get_nowait() == {"type": "message", "conversation_id": 7}
    assert theirs.queue.empty()


async def test_push_strips_sent_by_me_and_encodes_once_for_the_queue() -> None:
    """The two rules `push` already enforced for sockets hold for a queued reader too — the
    payload is shared, so a second encoding pass (or a missed strip) would be a second place for
    a raw `datetime` or another adviser's message drawn as their own to get through."""
    from datetime import UTC, datetime

    subscriber = live_push.subscribe("staff:9")
    await live_push.push(
        "staff:9",
        {
            "type": "message",
            "message": {
                "id": 1,
                "sent_by_me": True,
                "created_at": datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
            },
        },
    )
    payload = subscriber.queue.get_nowait()
    assert "sent_by_me" not in payload["message"]
    # Already JSON-ready: the stream serialises with plain `json.dumps` and would raise otherwise.
    assert json.dumps(payload)


async def test_an_unsubscribed_reader_receives_nothing_further() -> None:
    key = "client:505"
    subscriber = live_push.subscribe(key)
    live_push.unsubscribe(key, subscriber)
    await live_push.push(key, {"type": "message"})
    assert subscriber.queue.empty()


async def test_overflow_collapses_to_a_single_resync_rather_than_growing() -> None:
    """A reader that has stopped draining must cost a bounded amount of memory, and must be told
    its picture is incomplete rather than handed a truncated history it cannot detect."""
    key = "client:506"
    subscriber = live_push.subscribe(key)
    for n in range(live_push.QUEUE_MAXSIZE + 1):
        await live_push.push(key, {"type": "message", "n": n})
    assert subscriber.queue.qsize() == 1
    assert subscriber.queue.get_nowait() == live_push.RESYNC


async def test_a_reader_that_never_drains_cannot_grow_the_queue() -> None:
    key = "client:507"
    subscriber = live_push.subscribe(key)
    for n in range(live_push.QUEUE_MAXSIZE * 3):
        await live_push.push(key, {"type": "message", "n": n})
    assert subscriber.queue.qsize() <= live_push.QUEUE_MAXSIZE


async def test_a_resync_is_a_copy_so_one_reader_cannot_mutate_the_signal() -> None:
    key = "client:508"
    subscriber = live_push.subscribe(key)
    for n in range(live_push.QUEUE_MAXSIZE + 1):
        await live_push.push(key, {"type": "message", "n": n})
    signal = subscriber.queue.get_nowait()
    signal["type"] = "tampered"
    assert live_push.RESYNC == {"type": "resync"}


async def test_one_slow_reader_does_not_cost_a_healthy_one_its_payloads() -> None:
    key = "staff:11"
    slow = live_push.subscribe(key)
    healthy = live_push.subscribe(key)
    for n in range(live_push.QUEUE_MAXSIZE + 1):
        await live_push.push(key, {"type": "message", "n": n})
        healthy.queue.get_nowait()  # keeping up, one at a time
    assert healthy.queue.empty()
    assert slow.queue.get_nowait() == live_push.RESYNC


# ---- the SSE wire format ---------------------------------------------------------------------


def test_a_data_frame_is_one_line_terminated_by_a_blank_one() -> None:
    frame = live_push.sse_frame({"type": "message", "body": "line one\nline two"})
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    # The body's own newline must not break the framing: exactly one data line.
    assert frame.count("\n") == 2
    assert json.loads(frame[len("data: ") :]) == {"type": "message", "body": "line one\nline two"}


async def test_the_stream_opens_with_a_comment_then_carries_payloads() -> None:
    """The opening comment is what flushes the response so an `EventSource` leaves CONNECTING —
    a stream whose first byte waits for the first message reads as a channel that never opened."""
    subscriber = live_push.subscribe("client:509")
    stream = live_push.sse_stream(subscriber)
    try:
        assert await anext(stream) == ": open\n\n"
        subscriber.queue.put_nowait({"type": "message", "conversation_id": 3})
        assert await anext(stream) == 'data: {"type": "message", "conversation_id": 3}\n\n'
    finally:
        await stream.aclose()


async def test_an_idle_stream_emits_a_heartbeat_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_push, "HEARTBEAT_SECONDS", 0.01)
    subscriber = live_push.subscribe("client:510")
    stream = live_push.sse_stream(subscriber)
    try:
        await anext(stream)  # the opening comment
        assert await anext(stream) == ": keep-alive\n\n"
    finally:
        await stream.aclose()


async def test_a_heartbeat_does_not_swallow_a_payload_racing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`wait_for` cancels the pending `get()` on every heartbeat; an item queued around that
    moment stays on the queue rather than being consumed by the cancelled getter."""
    monkeypatch.setattr(live_push, "HEARTBEAT_SECONDS", 0.01)
    subscriber = live_push.subscribe("client:511")
    stream = live_push.sse_stream(subscriber)
    try:
        await anext(stream)
        await asyncio.sleep(0.03)  # several heartbeats' worth of idling
        subscriber.queue.put_nowait({"type": "message"})
        frames = [await anext(stream) for _ in range(6)]
        assert 'data: {"type": "message"}\n\n' in frames
    finally:
        await stream.aclose()


async def test_closing_the_stream_unsubscribes_deterministically() -> None:
    """The `finally` is the whole of the cleanup: a browser that goes away closes the iteration,
    and a subscriber left behind would hold its queue (and a push's work) for ever."""
    key = "client:512"
    subscriber = live_push.subscribe(key)
    stream = live_push.sse_stream(subscriber)
    await anext(stream)
    await stream.aclose()
    assert key not in live_push._subscribers  # noqa: SLF001 - the invariant under test


async def test_a_server_stop_ends_an_idle_stream() -> None:
    """A reload signals the process while the platform tab still holds the stream. The
    generator has to return on that signal — uvicorn will not close the socket itself, and
    cancelling it after the timeout is the `CancelledError` traceback on every save."""
    key = "client:514"
    subscriber = live_push.subscribe(key)
    stream = live_push.sse_stream(subscriber)
    try:
        assert await anext(stream) == ": open\n\n"
        live_push.request_server_stop()
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert key not in live_push._subscribers  # noqa: SLF001 - the invariant under test
    finally:
        live_push.reset_server_stop()
        await stream.aclose()


def test_a_shutdown_signal_notifies_then_calls_the_previous_handler() -> None:
    """Uvicorn's handler is what actually stops the process. Ours only wakes the streams,
    and it has to run first so they can finish inside the graceful window."""
    sig = getattr(signal, "SIGUSR1", None)
    if sig is None:
        pytest.skip("SIGUSR1 is how this test avoids touching SIGINT")
    order: list[str] = []
    old = signal.getsignal(sig)
    live_push._chained_signals.discard(sig)  # noqa: SLF001 - the guard under test

    def previous(signum: int, _frame: object) -> None:
        order.append(f"previous:{signum}")

    signal.signal(sig, previous)
    try:
        live_push.chain_stop_signal(sig, lambda: order.append("notify"))
        handler = signal.getsignal(sig)
        assert callable(handler)
        handler(sig, None)
        assert order == ["notify", f"previous:{sig}"]
        # A second install must not chain our own handler, or the signal recurses.
        live_push.chain_stop_signal(sig, lambda: order.append("again"))
        signal.getsignal(sig)(sig, None)  # type: ignore[operator]
        assert order == ["notify", f"previous:{sig}", "notify", f"previous:{sig}"]
    finally:
        signal.signal(sig, old)
        live_push._chained_signals.discard(sig)  # noqa: SLF001


async def test_cancelling_the_stream_unsubscribes_too() -> None:
    """The other way a stream really ends: the task carrying it is cancelled (a shutdown, or
    Starlette reaping the response when the client disconnects)."""
    key = "client:513"
    subscriber = live_push.subscribe(key)
    stream = live_push.sse_stream(subscriber)
    await anext(stream)
    task = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0.01)  # let it reach the queue wait
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert key not in live_push._subscribers  # noqa: SLF001 - the invariant under test
