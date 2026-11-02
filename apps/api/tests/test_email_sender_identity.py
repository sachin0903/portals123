"""The white-label ENVELOPE, not just the wordmark: who a firm's mail actually comes from.

Branding dressed the templates in the firm's name while every send still left as Graphene, because
both the queued and invite paths took their `From` from `email.sender_for` — a synchronous read of
a process-local, 5-minute TTL cache that only `resolve_sender` ever populates, with a silent
fallback to `settings.email_from` on a miss.

The outbox path can never warm that cache: the dispatcher runs in the WORKER process, which imports
its own `app.email` and so has its own permanently empty `_sender_cache`, and `_send` has no session
to resolve with anyway. The producer already resolved the identity on the request path and wrote it
into the payload — so the payload is the source of truth, and these tests keep the caches explicitly
COLD to prove the value is not coming from one.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.email import transport as email_mod
from app.flows import notifications


@pytest.fixture(autouse=True)
def _cold_caches(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A worker process's caches: empty, and the configured sender is Graphene's."""
    email_mod.clear_caches()
    monkeypatch.setattr(email_mod.settings, "email_from", "Graphene <no-reply@graphene.test>")
    yield
    email_mod.clear_caches()


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture ``send_email`` kwargs instead of calling Resend."""
    calls: list[dict[str, Any]] = []

    async def _fake(**kwargs: Any) -> bool:
        calls.append(kwargs)
        return True

    monkeypatch.setattr(email_mod, "send_email", _fake)
    return calls


@pytest.mark.asyncio
async def test_a_queued_client_email_sends_as_the_firm(captured: list[dict[str, Any]]) -> None:
    """The firm's `From` and `Reply-To` come out of the outbox payload the producer wrote. Fails
    before the fix: `_send` called `sender_for(tenant_id)`, which misses in the worker and silently
    returns the Graphene address — so the client saw Graphene's envelope on a white-labelled
    document notice, with the firm's reply address dropped entirely."""
    # The mechanism, stated: nothing has warmed this tenant, so the shim knows only Graphene.
    assert email_mod.sender_for(42) == "Graphene <no-reply@graphene.test>"

    assert await notifications._send(
        42,
        {
            "to": "client@example.test",
            "subject": "Ridley & Co: Annual Review 2026 is ready for you",
            "html": "<p>hello</p>",
            "event_type": "notify.document_sent",
            "sender": '"Ridley & Co" <no-reply@graphene.test>',
            "reply_to": "hello@ridley.test",
        },
    )
    # The ENVELOPE, asserted as a subset rather than as the whole call. `_send` also passes a
    # `ctx` for the outbound-email ledger, and this test is about the firm's From/Reply-To — an
    # exact-equality assertion here would fail every time a new, unrelated argument is added.
    assert len(captured) == 1
    envelope = {k: captured[0][k] for k in ("to", "subject", "html", "sender", "reply_to")}
    assert envelope == {
        "to": "client@example.test",
        "subject": "Ridley & Co: Annual Review 2026 is ready for you",
        "html": "<p>hello</p>",
        "sender": '"Ridley & Co" <no-reply@graphene.test>',
        "reply_to": "hello@ridley.test",
    }
    # The ledger context comes off the same payload, and is stamped `worker` — this drain is the
    # worker's only. A send recorded against the wrong tenant would put one firm's mail in
    # another's Activity feed, so the tenant is asserted here rather than left to the ledger tests.
    assert captured[0]["ctx"].tenant_id == 42
    assert captured[0]["ctx"].source == "worker"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({}, id="no sender keys at all"),
        pytest.param({"sender": None, "reply_to": None}, id="explicit JSON nulls"),
        pytest.param({"sender": "", "reply_to": ""}, id="empty strings"),
        pytest.param({"sender": 17, "reply_to": ["x"]}, id="wrong types"),
    ],
)
async def test_a_legacy_payload_still_dispatches(
    captured: list[dict[str, Any]], extra: dict[str, Any]
) -> None:
    """Rows already sitting in ``flows.outbox`` predate the payload keys — the dispatcher must not
    raise on them (a KeyError here is a dead-lettered client notice), it falls back to the
    configured sender with no reply-to."""
    payload: dict[str, Any] = {
        "to": "client@example.test",
        "subject": "Action needed",
        "html": "<p>please upload</p>",
        **extra,
    }
    assert await notifications._send(7, payload)
    assert captured[0]["sender"] == "Graphene <no-reply@graphene.test>"
    assert captured[0]["reply_to"] is None


@pytest.mark.asyncio
async def test_an_unaddressable_row_is_still_not_retried(captured: list[dict[str, Any]]) -> None:
    """Unchanged by the fix: a malformed row is 'delivered' rather than retried forever."""
    assert await notifications._send(7, {"subject": "no recipient"})
    assert captured == []


# A third case stood here — the task-notice scan's own `_send_one`. That sender is gone: a task
# notice is a notification now, so the scan emails nobody and the notification email subscriber
# resolves the envelope with `resolve_sender` before writing it into the outbox payload — which is
# the very path `test_a_queued_client_email_sends_as_the_firm` above pins.


@pytest.mark.asyncio
async def test_the_producer_captures_the_resolved_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the contract: ``queue_client_email`` writes the resolved envelope into the
    payload, because the worker cannot resolve it later."""
    from contextlib import asynccontextmanager

    from app.documents import service as documents_service

    queued: dict[str, Any] = {}

    @asynccontextmanager
    async def _fake_tx() -> Any:
        yield object()

    async def _fake_enqueue(
        _session: object, *, tenant_id: int, event_type: str, payload: dict[str, Any]
    ) -> None:
        queued.update({"tenant_id": tenant_id, "event_type": event_type, "payload": payload})

    monkeypatch.setattr(documents_service, "system_transaction", _fake_tx)
    monkeypatch.setattr(documents_service.flows_notifications, "enqueue", _fake_enqueue)

    assert await documents_service.queue_client_email(
        42,
        event_type="notify.request_raised",
        to="client@example.test",
        subject="Action needed",
        html="<p>please upload</p>",
        sender=email_mod.Sender('"Ridley & Co" <no-reply@graphene.test>', "hello@ridley.test"),
    )
    assert queued["payload"]["sender"] == '"Ridley & Co" <no-reply@graphene.test>'
    assert queued["payload"]["reply_to"] == "hello@ridley.test"

    # And the round trip: what the producer wrote is what the dispatcher sends.
    sent: list[dict[str, Any]] = []

    async def _fake_send(**kwargs: Any) -> bool:
        sent.append(kwargs)
        return True

    monkeypatch.setattr(email_mod, "send_email", _fake_send)
    assert await notifications._send(42, queued["payload"])
    assert sent[0]["sender"] == '"Ridley & Co" <no-reply@graphene.test>'
    assert sent[0]["reply_to"] == "hello@ridley.test"
