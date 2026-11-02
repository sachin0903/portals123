"""``POST /webhooks/resend`` — the delivery callback (``app/routers/resend_webhook.py``).

This route is unauthenticated and can mark a message `bounced`, a claim a firm will act on. So the
signature check is the whole security of it, and most of this file is about that: a valid signature
passes, and every way of not having one is refused — missing headers, a wrong signature, a stale
timestamp (replay), and an unconfigured deployment, which is OFF rather than open.

The ledger is captured rather than written here; ``test_comms_ledger.py`` drives
``apply_provider_event`` against real Postgres. What this file pins is the ROUTE's decisions.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import audit_actions as actions
from app.comms import ledger
from app.config import settings
from app.routers import resend_webhook

SECRET_BYTES = b"a-very-secret-signing-key-32bytes"
SECRET = "whsec_" + base64.b64encode(SECRET_BYTES).decode()
MESSAGE_ID = "re_abc123"
SVIX_ID = "msg_2abc"


def _body(event_type: str = "email.delivered", email_id: str = MESSAGE_ID) -> bytes:
    return json.dumps(
        {
            "type": event_type,
            "created_at": "2026-09-01T10:00:00.000Z",
            "data": {"email_id": email_id, "to": ["client@example.test"]},
        }
    ).encode()


def _sign(body: bytes, *, svix_id: str = SVIX_ID, timestamp: int | None = None) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    signed = f"{svix_id}.{ts}.".encode() + body
    return base64.b64encode(hmac.new(SECRET_BYTES, signed, hashlib.sha256).digest()).decode()


def _headers(body: bytes, *, timestamp: int | None = None, signature: str | None = None) -> dict:
    ts = timestamp if timestamp is not None else int(time.time())
    sig = signature if signature is not None else _sign(body, timestamp=ts)
    return {
        "svix-id": SVIX_ID,
        "svix-timestamp": str(ts),
        "svix-signature": f"v1,{sig}",
        "content-type": "application/json",
    }


@pytest.fixture
def client() -> TestClient:
    """The router on its own app: nothing here needs the middleware stack or a principal."""
    app = FastAPI()
    app.include_router(resend_webhook.router)
    return TestClient(app)


@pytest.fixture
def applied(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture `ledger.apply_provider_event` and report a match, so the route's own decisions are
    what is under test."""
    calls: list[dict[str, Any]] = []

    async def fake(session: Any, **kwargs: Any) -> bool:
        calls.append(kwargs)
        return True

    monkeypatch.setattr(ledger, "apply_provider_event", fake)
    return calls


@pytest.fixture
def no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(session: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(ledger, "apply_provider_event", fake)


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "resend_webhook_secret", SECRET)


@pytest.fixture(autouse=True)
def _no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """`system_transaction` is a real connection; this suite must not need a database. Replaced with
    a no-op context manager, so a test that reaches the ledger call still exercises the route."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake():  # type: ignore[no-untyped-def]
        yield object()

    monkeypatch.setattr(resend_webhook, "system_transaction", fake)


# ---------------------------------------------------------------------------------------------
# The signature — the whole security of an unauthenticated route
# ---------------------------------------------------------------------------------------------


def test_a_valid_signature_is_accepted(client: TestClient, applied: list[dict[str, Any]]) -> None:
    body = _body()
    resp = client.post("/webhooks/resend", content=body, headers=_headers(body))
    assert resp.status_code == 200
    assert resp.json() == {"handled": True, "reason": "email.delivered"}
    assert applied[0]["provider_message_id"] == MESSAGE_ID
    assert applied[0]["event_type"] == "email.delivered"
    # Svix's id for the DELIVERY is the idempotency handle — the same value on every redelivery.
    assert applied[0]["provider_event_id"] == SVIX_ID


def test_a_wrong_signature_is_refused(client: TestClient, applied: list[dict[str, Any]]) -> None:
    body = _body()
    bad = base64.b64encode(b"x" * 32).decode()
    resp = client.post("/webhooks/resend", content=body, headers=_headers(body, signature=bad))
    assert resp.json() == {"handled": False, "reason": "bad_signature"}
    assert applied == [], "nothing may reach the ledger on a bad signature"


def test_a_signature_over_a_different_body_is_refused(
    client: TestClient, applied: list[dict[str, Any]]
) -> None:
    """The signature covers the RAW BODY, so a valid signature for another payload must not admit
    this one — the attack a verifier that re-serialises before checking would allow."""
    signed_for = _body("email.delivered")
    tampered = _body("email.bounced")
    resp = client.post("/webhooks/resend", content=tampered, headers=_headers(signed_for))
    assert resp.json() == {"handled": False, "reason": "bad_signature"}
    assert applied == []


@pytest.mark.parametrize("drop", ["svix-id", "svix-timestamp", "svix-signature"])
def test_a_missing_header_is_refused(
    client: TestClient, applied: list[dict[str, Any]], drop: str
) -> None:
    body = _body()
    headers = _headers(body)
    headers.pop(drop)
    resp = client.post("/webhooks/resend", content=body, headers=headers)
    assert resp.json() == {"handled": False, "reason": "bad_signature"}
    assert applied == []


@pytest.mark.parametrize("age", [-(60 * 60), 60 * 60])
def test_a_stale_or_future_timestamp_is_refused(
    client: TestClient, applied: list[dict[str, Any]], age: int
) -> None:
    """Without a tolerance a captured callback is replayable for ever. Both directions, because
    clock skew can put a forged timestamp either side of now."""
    body = _body()
    ts = int(time.time()) + age
    resp = client.post("/webhooks/resend", content=body, headers=_headers(body, timestamp=ts))
    assert resp.json() == {"handled": False, "reason": "bad_signature"}
    assert applied == []


def test_a_rotating_secret_sends_two_signatures_and_either_may_match(
    client: TestClient, applied: list[dict[str, Any]]
) -> None:
    """Svix sends several space-separated `v1,<sig>` values while a secret is rotated. Refusing a
    header with more than one would break every callback for the duration of a rotation."""
    body = _body()
    ts = int(time.time())
    good = _sign(body, timestamp=ts)
    other = base64.b64encode(b"y" * 32).decode()
    headers = _headers(body, timestamp=ts)
    headers["svix-signature"] = f"v1,{other} v1,{good}"
    resp = client.post("/webhooks/resend", content=body, headers=headers)
    assert resp.json()["handled"] is True


def test_an_unknown_signature_scheme_is_not_trusted(
    client: TestClient, applied: list[dict[str, Any]]
) -> None:
    body = _body()
    ts = int(time.time())
    headers = _headers(body, timestamp=ts)
    headers["svix-signature"] = f"v2,{_sign(body, timestamp=ts)}"
    resp = client.post("/webhooks/resend", content=body, headers=headers)
    assert resp.json() == {"handled": False, "reason": "bad_signature"}
    assert applied == []


def test_the_secret_is_base64_decoded_past_its_prefix() -> None:
    """Svix writes the secret `whsec_<base64>` and it is the DECODED bytes that key the HMAC.
    Signing with the literal displayed string is the classic way to build a verifier that rejects
    every genuine callback."""
    assert resend_webhook._secret_bytes(SECRET) == SECRET_BYTES
    assert resend_webhook._secret_bytes("  " + SECRET + "  ") == SECRET_BYTES
    assert resend_webhook._secret_bytes("") is None
    # Not base64 — taken as raw key material rather than raising, since some tooling stores the
    # decoded value directly.
    assert resend_webhook._secret_bytes("whsec_not base64!") == b"not base64!"


# ---------------------------------------------------------------------------------------------
# Configuration: off, not open
# ---------------------------------------------------------------------------------------------


def test_no_secret_disables_the_endpoint(
    client: TestClient, applied: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absence of configuration means "do nothing", never "trust everybody" — the EMAIL_ALLOWLIST
    doctrine. A deployment with no secret must not accept a claim it cannot authenticate."""
    monkeypatch.setattr(settings, "resend_webhook_secret", "")
    body = _body()
    resp = client.post("/webhooks/resend", content=body, headers=_headers(body))
    assert resp.status_code == 200
    assert resp.json() == {"handled": False, "reason": "not_configured"}
    assert applied == []


# ---------------------------------------------------------------------------------------------
# Payload handling — every outcome is a 200, because a retry cannot improve any of them
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"not json at all", "unparseable"),
        (json.dumps({"type": "email.delivered", "data": {}}).encode(), "no_message_id"),
        (json.dumps({"data": {"email_id": MESSAGE_ID}}).encode(), "no_event_type"),
    ],
)
def test_a_malformed_payload_is_a_200_that_names_why(
    client: TestClient, applied: list[dict[str, Any]], body: bytes, reason: str
) -> None:
    """Signed but unusable. Every one is 200 and writes nothing: Resend retries a non-2xx, and no
    retry turns a body we cannot read into one we can."""
    resp = client.post("/webhooks/resend", content=body, headers=_headers(body))
    assert resp.status_code == 200
    assert resp.json() == {"handled": False, "reason": reason}
    assert applied == []


def test_an_unknown_message_is_a_200_and_not_an_error(client: TestClient, no_match: None) -> None:
    """A callback for a pruned row, and one for mail sent by another deployment sharing this Resend
    account, look exactly alike. Answering non-2xx to either buys a retry storm and nothing else."""
    body = _body()
    resp = client.post("/webhooks/resend", content=body, headers=_headers(body))
    assert resp.status_code == 200
    assert resp.json() == {"handled": False, "reason": "unknown_message"}


def test_an_oversized_body_is_refused_before_it_is_read(
    client: TestClient, applied: list[dict[str, Any]]
) -> None:
    """Reading an unbounded body into memory on an unauthenticated route is the hazard, so the
    declared length is checked first."""
    body = b"x" * (resend_webhook.MAX_BODY_BYTES + 1)
    resp = client.post("/webhooks/resend", content=body, headers=_headers(body))
    assert resp.json() == {"handled": False, "reason": "body_too_large"}
    assert applied == []


def test_an_unmodelled_event_type_still_records_the_callback(
    client: TestClient, applied: list[dict[str, Any]]
) -> None:
    """A type we do not map to a status is still worth a timeline entry — refusing to record it
    would lose the very callback that explains a delivery failure."""
    body = _body("email.delivery_delayed")
    resp = client.post("/webhooks/resend", content=body, headers=_headers(body))
    assert resp.json()["handled"] is True
    assert applied[0]["event_type"] == "email.delivery_delayed"


def test_the_provider_timestamp_is_used_not_our_clock(
    client: TestClient, applied: list[dict[str, Any]]
) -> None:
    """A retried callback delivered an hour late must not stamp the timeline an hour late."""
    body = _body()
    client.post("/webhooks/resend", content=body, headers=_headers(body))
    occurred = applied[0]["occurred_at"]
    assert occurred is not None
    assert (occurred.year, occurred.month, occurred.day, occurred.hour) == (2026, 9, 1, 10)


# ---------------------------------------------------------------------------------------------
# The middleware contract
# ---------------------------------------------------------------------------------------------


def test_the_route_is_exempt_from_the_action_stream() -> None:
    """A vendor callback has no principal, so it cannot be attributed to an actor. What it records
    is the comms.messages row it advances and its own comms.message_events entry."""
    assert ("POST", "/webhooks/resend") in actions.EXEMPT_MUTATING_ROUTES


def test_the_route_is_mounted_on_the_real_app() -> None:
    """The fixtures above mount the router in isolation, which would pass just as happily if
    `main.py` never included it — and a webhook nobody can reach fails silently for ever.

    Uses `api_routes` rather than reading `app.routes` directly: this app includes its routers
    lazily (they appear as `_IncludedRouter` entries carrying no `.path`), so scanning `app.routes`
    for a path finds only the four docs routes and would fail for every endpoint in the API.
    """
    from app.main import app
    from tests.conftest import api_routes

    paths = {route.path for route in api_routes(app)}
    assert "/webhooks/resend" in paths


@pytest.mark.parametrize("event_type", ["email.opened", "email.clicked"])
def test_a_read_receipt_callback_is_refused_before_the_ledger_is_touched(
    client: TestClient, applied: list[dict[str, Any]], event_type: str
) -> None:
    """This product does not do read receipts, and a correctly SIGNED open still gets nowhere.

    Refused before the lookup, so the provider's tracking payload never reaches
    `comms.message_events` — `applied` staying empty is the whole assertion. Resend only emits
    these when open/click tracking is switched on in its dashboard, which puts a pixel in a
    client's email; the firm can leave it off, and if it is on we still decline to keep the data.
    """
    body = _body(event_type=event_type)
    resp = client.post("/webhooks/resend", content=body, headers=_headers(body))
    assert resp.status_code == 200
    assert resp.json() == {"handled": False, "reason": "not_tracked"}
    assert applied == []


def test_a_domain_update_is_applied_before_the_message_lookup(
    client: TestClient, applied: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A domain callback has no email id. It must update the sending domain, not be dropped as
    `no_message_id`, and it must not be written through the message ledger."""
    seen: dict[str, Any] = {}

    async def fake(session: Any, event_type: str, data: object) -> bool:
        seen["event"] = event_type
        seen["data"] = data
        return True

    monkeypatch.setattr(resend_webhook.sender_domains, "apply_domain_event", fake)
    body = json.dumps(
        {
            "type": "domain.updated",
            "created_at": "2026-10-20T10:00:00.000Z",
            "data": {"id": "dom_1", "status": "verified", "name": "mail.example.test"},
        }
    ).encode()
    resp = client.post("/webhooks/resend", content=body, headers=_headers(body))
    assert resp.status_code == 200
    assert resp.json() == {"handled": True, "reason": "domain.updated"}
    assert seen["event"] == "domain.updated"
    assert seen["data"]["id"] == "dom_1"
    assert applied == []


def test_an_unknown_domain_is_acknowledged_and_not_retried(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(session: Any, event_type: str, data: object) -> bool:
        return False

    monkeypatch.setattr(resend_webhook.sender_domains, "apply_domain_event", fake)
    body = json.dumps({"type": "domain.deleted", "data": {"id": "dom_missing"}}).encode()
    resp = client.post("/webhooks/resend", content=body, headers=_headers(body))
    assert resp.status_code == 200
    assert resp.json() == {"handled": False, "reason": "unknown_domain"}
