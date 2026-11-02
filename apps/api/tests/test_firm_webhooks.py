"""Firm webhooks: which URLs may be saved and sent to, and the worker's delivery pass.

The URL rules and a single send are tested without a database: the pinned transport wraps an
``httpx.MockTransport``, so what is checked is the request that would have gone on the wire. The
pass itself (claim, lease, record, back-off, the final failure) runs against a real Postgres.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import crypto
from app.complycube import deliveries, routing
from app.complycube.deliveries import (
    BACKOFF_SECONDS,
    MAX_ATTEMPTS,
    NO_WEBHOOK,
    Delivery,
    deliver_once,
    next_step,
    send,
)
from app.complycube.webhook_url import (
    PinnedTransport,
    UnsafeWebhookUrl,
    check_syntax,
    check_url,
    is_public,
    resolve_public,
)
from app.config import settings
from external.live import LiveStore
from external.models import WebhookPut
from external.store import Rejected
from tests.complycube_support import Bed, Maker, fetch, insert_check, seed_bed

PUBLIC = "93.184.216.34"
SECRET = "whsec-test-secret"  # noqa: S105 - a test fixture


def _resolver(*answers: str, calls: list[str] | None = None) -> Any:
    async def resolve(host: str, port: int) -> list[str]:
        if calls is not None:
            calls.append(host)
        return list(answers)

    return resolve


@pytest.fixture
def master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())


# ---- which addresses are public -----------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.5.4",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",  # noqa: S104 - an address under test, not a bind
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "2002:7f00:1::1",
        "2002:a00:1::1",
        "64:ff9b::a00:1",
        "64:ff9b::7f00:1",
    ],
)
def test_a_non_public_address_is_refused(address: str) -> None:
    assert not is_public(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", [PUBLIC, "8.8.8.8", "2606:4700:4700::1111"])
def test_a_public_address_is_allowed(address: str) -> None:
    assert is_public(ipaddress.ip_address(address))


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://firm.example/hooks", "HTTPS"),
        ("ftp://firm.example/hooks", "HTTPS"),
        ("https://user:pass@firm.example/hooks", "user name or password"),
        ("https://localhost/hooks", "public host"),
        ("https://hooks.localhost/x", "public host"),
        ("https://service.internal/x", "public host"),
        ("https://127.0.0.1/hooks", "public address"),
        ("https://10.1.2.3/hooks", "public address"),
        ("https://[::1]/hooks", "public address"),
        ("https://[::ffff:10.0.0.1]/hooks", "public address"),
        ("https:///hooks", "host"),
    ],
)
def test_a_url_graphene_will_never_send_to_is_refused(url: str, reason: str) -> None:
    with pytest.raises(UnsafeWebhookUrl, match=reason):
        check_syntax(url)


def test_an_https_url_to_a_name_is_accepted() -> None:
    assert str(check_syntax("https://firm.example/hooks?x=1")) == "https://firm.example/hooks?x=1"


async def test_a_name_that_resolves_to_a_private_address_is_refused() -> None:
    for answers in (["127.0.0.1"], ["10.0.0.7"], [PUBLIC, "10.0.0.7"], ["::ffff:192.168.0.1"]):
        with pytest.raises(UnsafeWebhookUrl, match="public address"):
            await resolve_public("firm.example", 443, resolver=_resolver(*answers))
    assert await resolve_public("firm.example", 443, resolver=_resolver(PUBLIC, PUBLIC)) == [PUBLIC]


async def test_a_name_that_does_not_resolve_is_refused() -> None:
    async def broken(host: str, port: int) -> list[str]:
        raise OSError("no such host")

    with pytest.raises(UnsafeWebhookUrl, match="could not be resolved"):
        await resolve_public("missing.example", 443, resolver=broken)
    with pytest.raises(UnsafeWebhookUrl, match="could not be resolved"):
        await resolve_public("empty.example", 443, resolver=_resolver())


# ---- saving a webhook ---------------------------------------------------------------------------


def test_the_firm_api_refuses_a_url_that_is_not_https_at_validation() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="HTTPS"):
        WebhookPut(url="http://firm.example/hooks")
    with pytest.raises(ValidationError, match="public address"):
        WebhookPut(url="https://10.0.0.1/hooks")
    assert WebhookPut(url=" https://firm.example/hooks ").url == "https://firm.example/hooks"


class _Recorder:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.executed.append((str(statement), dict(params or {})))
        return None


@pytest.mark.usefixtures("master_key")
@pytest.mark.parametrize("answer", ["127.0.0.1", "10.20.30.40"])
async def test_the_firm_api_refuses_a_host_resolving_to_a_private_address_at_save(
    answer: str,
) -> None:
    session = _Recorder()
    store = LiveStore(session, resolver=_resolver(answer))  # type: ignore[arg-type]
    with pytest.raises(Rejected) as refused:
        await store.put_webhook("https://firm.example/hooks", tenant_id=7)
    assert refused.value.code == "invalid_webhook_url"
    assert refused.value.status == 422
    assert session.executed == []


@pytest.mark.usefixtures("master_key")
async def test_the_firm_api_saves_a_host_resolving_to_a_public_address() -> None:
    session = _Recorder()
    store = LiveStore(session, resolver=_resolver(PUBLIC))  # type: ignore[arg-type]
    saved = await store.put_webhook("https://firm.example/hooks", tenant_id=7)
    assert saved.url == "https://firm.example/hooks"
    assert saved.secret
    assert "compliance.webhooks" in session.executed[0][0]


async def test_check_url_runs_both_halves() -> None:
    with pytest.raises(UnsafeWebhookUrl):
        await check_url("http://firm.example/hooks", resolver=_resolver(PUBLIC))
    with pytest.raises(UnsafeWebhookUrl):
        await check_url("https://firm.example/hooks", resolver=_resolver("10.0.0.1"))
    assert await check_url("https://firm.example/x", resolver=_resolver(PUBLIC)) == (
        "https://firm.example/x"
    )


# ---- one send -----------------------------------------------------------------------------------


def _delivery(**overrides: Any) -> Delivery:
    values: dict[str, Any] = {
        "id": 1,
        "uid": "delivery0001",
        "event": "check.updated",
        "check_uid": "checkuid0001",
        "kind": "aml",
        "status": "clear",
        "attempts": 0,
        "url": "https://firm.example/hooks",
    }
    values.update(overrides)
    if "secret_ct" not in values:
        values["secret_ct"] = crypto.encrypt(SECRET)
    return Delivery(**values)


class _Endpoint:
    """The firm's endpoint, behind the pinned transport."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses) or [httpx.Response(204)]
        self.requests: list[httpx.Request] = []
        self.pinned_to: list[str] = []

    def factory(self, address: str) -> httpx.AsyncBaseTransport:
        self.pinned_to.append(address)
        return PinnedTransport(address, inner=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def _verify(header: str, body: bytes) -> bool:
    """What a firm runs: split the header, recompute the HMAC of ``t.body``, compare."""
    parts = dict(item.split("=", 1) for item in header.split(","))
    expected = hmac.new(SECRET.encode(), f"{parts['t']}.".encode() + body, hashlib.sha256)
    return hmac.compare_digest(expected.hexdigest(), parts["v1"])


@pytest.mark.usefixtures("master_key")
async def test_a_delivery_is_signed_and_the_signature_verifies() -> None:
    endpoint = _Endpoint()
    outcome = await send(
        _delivery(), resolver=_resolver(PUBLIC), transport=endpoint.factory, clock=lambda: 1_700
    )
    assert outcome.delivered and outcome.error is None
    [request] = endpoint.requests
    assert request.method == "POST"
    body = request.content
    assert json.loads(body) == {
        "event": "check.updated",
        "uid": "checkuid0001",
        "kind": "aml",
        "status": "clear",
    }
    header = request.headers["x-graphene-signature"]
    assert header.startswith("t=1700,v1=")
    assert _verify(header, body)
    assert not _verify(header, body + b" ")
    assert request.headers["x-graphene-delivery"] == "delivery0001"
    assert request.headers["content-type"] == "application/json"


@pytest.mark.usefixtures("master_key")
async def test_the_request_goes_to_the_pinned_address_with_the_host_name_for_http_and_tls() -> None:
    endpoint = _Endpoint()
    calls: list[str] = []
    await send(
        _delivery(url="https://firm.example:8443/hooks"),
        resolver=_resolver(PUBLIC, calls=calls),
        transport=endpoint.factory,
    )
    [request] = endpoint.requests
    assert endpoint.pinned_to == [PUBLIC]
    assert request.url.host == PUBLIC
    assert request.url.port == 8443
    assert request.url.path == "/hooks"
    assert request.headers["host"] == "firm.example:8443"
    assert request.extensions["sni_hostname"] == "firm.example"
    # Resolved once: nothing after the check can swap the address.
    assert calls == ["firm.example"]


async def test_an_ipv6_address_is_pinned_in_brackets() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    transport = PinnedTransport("2606:4700:4700::1111", inner=httpx.MockTransport(handle))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.post("https://firm.example/hooks", content=b"{}")
    assert seen[0].url.host == "2606:4700:4700::1111"
    assert "[2606:4700:4700::1111]" in str(seen[0].url)
    assert seen[0].headers["host"] == "firm.example"
    assert seen[0].extensions["sni_hostname"] == "firm.example"


@pytest.mark.usefixtures("master_key")
async def test_a_name_rebound_to_a_private_address_is_refused_at_send() -> None:
    """Saved while it resolved publicly; at send time it answers 10.0.0.5. Nothing is sent."""
    endpoint = _Endpoint()
    outcome = await send(_delivery(), resolver=_resolver("10.0.0.5"), transport=endpoint.factory)
    assert not outcome.delivered
    assert outcome.error is not None and "public address" in outcome.error
    assert endpoint.requests == []
    assert endpoint.pinned_to == []


@pytest.mark.usefixtures("master_key")
async def test_a_url_that_stopped_being_https_is_refused_at_send() -> None:
    endpoint = _Endpoint()
    outcome = await send(
        _delivery(url="http://firm.example/hooks"),
        resolver=_resolver(PUBLIC),
        transport=endpoint.factory,
    )
    assert not outcome.delivered
    assert outcome.error is not None and "HTTPS" in outcome.error
    assert endpoint.requests == []


@pytest.mark.usefixtures("master_key")
async def test_a_redirect_is_not_followed() -> None:
    endpoint = _Endpoint(
        httpx.Response(302, headers={"location": "https://10.0.0.1/internal"}),
        httpx.Response(204),
    )
    outcome = await send(_delivery(), resolver=_resolver(PUBLIC), transport=endpoint.factory)
    assert not outcome.delivered
    assert outcome.error == "The endpoint answered 302. Redirects are not followed."
    assert len(endpoint.requests) == 1


@pytest.mark.usefixtures("master_key")
async def test_a_non_2xx_answer_is_a_failed_attempt() -> None:
    endpoint = _Endpoint(httpx.Response(500))
    outcome = await send(_delivery(), resolver=_resolver(PUBLIC), transport=endpoint.factory)
    assert not outcome.delivered and not outcome.final
    assert outcome.error == "The endpoint answered 500."


@pytest.mark.usefixtures("master_key")
async def test_the_send_has_a_ten_second_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    timeouts: list[dict[str, float]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"])
        return httpx.Response(200)

    def factory(address: str) -> httpx.AsyncBaseTransport:
        return PinnedTransport(address, inner=httpx.MockTransport(handle))

    await send(_delivery(), resolver=_resolver(PUBLIC), transport=factory)
    assert deliveries.TIMEOUT_SECONDS == 10.0
    assert timeouts == [{"connect": 10.0, "read": 10.0, "write": 10.0, "pool": 10.0}]

    # The whole attempt is bounded too, so a slow endpoint (or a slow resolver) cannot hold it.
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    def slow_factory(address: str) -> httpx.AsyncBaseTransport:
        return PinnedTransport(address, inner=httpx.MockTransport(slow))

    monkeypatch.setattr(deliveries, "TIMEOUT_SECONDS", 0.05)
    outcome = await send(_delivery(), resolver=_resolver(PUBLIC), transport=slow_factory)
    assert not outcome.delivered
    assert outcome.error is not None and "did not answer within" in outcome.error


async def test_a_delivery_with_no_webhook_fails_for_good() -> None:
    outcome = await send(_delivery(url=None, secret_ct=None), resolver=_resolver(PUBLIC))
    assert outcome == deliveries.Outcome(False, NO_WEBHOOK, final=True)


def test_the_back_off_schedule_then_the_final_failure() -> None:
    assert BACKOFF_SECONDS == (60, 300, 1800, 7200, 21600, 86400)
    assert [next_step(n) for n in range(1, MAX_ATTEMPTS + 1)] == [
        (False, 60),
        (False, 300),
        (False, 1800),
        (False, 7200),
        (False, 21600),
        (False, 86400),
        (True, 0),
    ]


# ---- the pass, against Postgres -----------------------------------------------------------------


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def firm(maker: Maker, master_key: None) -> AsyncIterator[Bed]:
    bed = await seed_bed(maker, webhook=True)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text("UPDATE compliance.webhooks SET secret_ct = :ct WHERE id = :w"),
            {"ct": crypto.encrypt(SECRET), "w": bed.webhook_id},
        )
    yield bed
    await bed.cleanup()


async def _queue(bed: Bed, *, status: str = "clear") -> str:
    """One delivery, queued the way routing queues it for a real change."""
    chk = bed.ref("chk")
    check_pk = await insert_check(bed, chk)
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        check = await routing.load_check(s, check_pk)
        assert check is not None
        assert await routing.queue_partner_delivery(s, check, "pending", status)
    rows = await fetch(
        bed.maker,
        "SELECT uid FROM compliance.webhook_deliveries WHERE tenant_id = :t",
        {"t": bed.tenant_id},
    )
    return str(rows[0]["uid"])


async def _row(bed: Bed, uid: str) -> dict[str, Any]:
    rows = await fetch(
        bed.maker,
        "SELECT status, attempts, last_error, sent_at, "
        "  EXTRACT(EPOCH FROM (next_attempt_at - now())) AS wait "
        "FROM compliance.webhook_deliveries WHERE uid = :u",
        {"u": uid},
    )
    return rows[0]


async def _only(bed: Bed) -> None:
    """Park every other due delivery in the database so a pass sees this bed's row only."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "UPDATE compliance.webhook_deliveries "
                "SET next_attempt_at = now() + interval '1 day' "
                "WHERE tenant_id <> :t AND status = 'pending'"
            ),
            {"t": bed.tenant_id},
        )


async def test_a_pass_sends_a_signed_delivery_and_records_it(firm: Bed) -> None:
    uid = await _queue(firm)
    await _only(firm)
    endpoint = _Endpoint()
    result = await deliver_once(firm.maker, resolver=_resolver(PUBLIC), transport=endpoint.factory)
    assert (result.claimed, result.sent) == (1, 1)
    [request] = endpoint.requests
    body = json.loads(request.content)
    assert body["event"] == "check.updated"
    assert body["kind"] == "aml"
    assert body["status"] == "clear"
    assert _verify(request.headers["x-graphene-signature"], request.content)
    assert request.headers["x-graphene-delivery"] == uid
    row = await _row(firm, uid)
    assert row["status"] == "sent"
    assert row["attempts"] == 1
    assert row["sent_at"] is not None
    assert row["last_error"] is None


async def test_a_failed_send_backs_off_then_fails_after_the_last_wait(firm: Bed) -> None:
    uid = await _queue(firm)
    await _only(firm)
    endpoint = _Endpoint(httpx.Response(503))
    await deliver_once(firm.maker, resolver=_resolver(PUBLIC), transport=endpoint.factory)
    row = await _row(firm, uid)
    assert (row["status"], row["attempts"]) == ("pending", 1)
    assert row["last_error"] == "The endpoint answered 503."
    assert 55 <= float(row["wait"]) <= 61
    assert row["sent_at"] is None

    # Not due yet: a second pass leaves it alone.
    again = await deliver_once(firm.maker, resolver=_resolver(PUBLIC), transport=endpoint.factory)
    assert again.claimed == 0

    for attempts, wait in ((2, 300), (3, 1800), (4, 7200), (5, 21600), (6, 86400)):
        await _due_now(firm, uid)
        await deliver_once(firm.maker, resolver=_resolver(PUBLIC), transport=endpoint.factory)
        row = await _row(firm, uid)
        assert (row["status"], row["attempts"]) == ("pending", attempts)
        assert wait - 5 <= float(row["wait"]) <= wait + 1

    await _due_now(firm, uid)
    result = await deliver_once(firm.maker, resolver=_resolver(PUBLIC), transport=endpoint.factory)
    assert result.failed == 1
    row = await _row(firm, uid)
    assert (row["status"], row["attempts"]) == ("failed", MAX_ATTEMPTS)
    assert row["last_error"] == "The endpoint answered 503."
    assert len(endpoint.requests) == MAX_ATTEMPTS

    # A failed row is never claimed again.
    await _due_now(firm, uid)
    final = await deliver_once(firm.maker, resolver=_resolver(PUBLIC), transport=endpoint.factory)
    assert final.claimed == 0


async def _due_now(bed: Bed, uid: str) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text("UPDATE compliance.webhook_deliveries SET next_attempt_at = now() WHERE uid = :u"),
            {"u": uid},
        )


async def test_a_claim_leases_the_row_so_a_second_worker_skips_it(firm: Bed) -> None:
    uid = await _queue(firm)
    await _only(firm)
    async with firm.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        first = await deliveries.claim(s)
    async with firm.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        second = await deliveries.claim(s)
    assert [item.uid for item in first] == [uid]
    assert first[0].url == "https://firm.example/hooks"
    assert first[0].kind == "aml"
    assert second == []
    row = await _row(firm, uid)
    assert row["status"] == "pending"
    assert float(row["wait"]) > 100


async def test_a_host_rebound_to_a_private_address_is_a_failed_attempt(firm: Bed) -> None:
    uid = await _queue(firm)
    await _only(firm)
    endpoint = _Endpoint()
    await deliver_once(firm.maker, resolver=_resolver("127.0.0.1"), transport=endpoint.factory)
    row = await _row(firm, uid)
    assert (row["status"], row["attempts"]) == ("pending", 1)
    assert "public address" in row["last_error"]
    assert endpoint.requests == []
