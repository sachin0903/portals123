"""The Intelliflo webhook accepts only signed requests and never leaks or logs the payload."""

import asyncio
import hashlib
import hmac
import json
import logging

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from intelliflo_sync.providers.intelliflo import config, webhook

SECRET = "s3cret-for-tests"
INSTALLED = {
    "id": "956bd9dd-adca-429e-9620-fa82947f798d",
    "event": "AppInstalled",
    "payload": {"id": "89c73fd", "name": "Graphene Valuations App - Dev"},
    "installedFor": {
        "user": {"id": 957937, "username": "A Person"},
        "tenant": {"id": 12551, "name": "Intelliflo Ltd (iO Store)"},
    },
}


def sign(body: bytes, secret: str = SECRET, algorithm: str = "sha256") -> str:
    return f"{algorithm}=" + hmac.new(secret.encode(), body, getattr(hashlib, algorithm)).hexdigest()


def make_request(body: bytes, headers: dict | None = None, query: str = "") -> Request:
    merged = {"content-type": "application/json", **(headers or {})}
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/webhook",
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in merged.items()],
        "client": ("203.0.113.9", 4000),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def call(request: Request):
    return asyncio.run(webhook.receive_webhook(request))


@pytest.fixture
def upserts(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(webhook, "upsert_integration", lambda **kw: calls.append(kw) or "14")
    return calls


def body_of(payload: dict = INSTALLED) -> bytes:
    return json.dumps(payload).encode()


# ── validate_webhook_signature ───────────────────────────────────────────────


def test_signature_validation():
    body = b'{"a": 1}'
    for algorithm in ("sha256", "sha1", "sha384", "sha512"):
        assert webhook.validate_webhook_signature(body, SECRET, sign(body, algorithm=algorithm))
    assert webhook.validate_webhook_signature(
        body, SECRET, sign(body).upper().replace("SHA256=", "sha256=")
    )  # hex case
    assert not webhook.validate_webhook_signature(body, SECRET, sign(b'{"a": 2}'))  # different body
    assert not webhook.validate_webhook_signature(body, "other-secret", sign(body))  # different secret
    assert not webhook.validate_webhook_signature(body, SECRET, "")  # no header
    assert not webhook.validate_webhook_signature(body, SECRET, "sha256=")  # empty digest
    assert not webhook.validate_webhook_signature(body, SECRET, sign(body).split("=")[1])  # no algorithm prefix
    assert not webhook.validate_webhook_signature(
        body, SECRET, "md5=" + hashlib.md5(body).hexdigest()
    )  # unknown algorithm
    assert not webhook.validate_webhook_signature(body, "", sign(body, secret=""))  # no secret: never valid


# ── the route ────────────────────────────────────────────────────────────────


def test_signed_request_is_accepted_and_recorded(upserts):
    body = body_of()
    response = call(make_request(body, {"X-Hub-Signature": sign(body)}))
    assert response.status_code == 200
    assert len(upserts) == 1 and upserts[0]["tenant_id"] == "12551" and upserts[0]["app_id"] == "89c73fd"
    assert upserts[0]["installed"] is True


def test_the_256_header_is_accepted_too(upserts):
    body = body_of()
    assert call(make_request(body, {"X-Hub-Signature-256": sign(body)})).status_code == 200
    assert len(upserts) == 1


@pytest.mark.parametrize(
    "headers",
    [
        {},  # unsigned
        {"X-Hub-Signature": "sha256=" + "0" * 64},  # wrong digest
        {"X-Hub-Signature": "garbage"},
    ],
)
def test_unsigned_or_wrongly_signed_requests_are_rejected_and_touch_nothing(upserts, headers):
    with pytest.raises(HTTPException) as err:
        call(make_request(body_of(), headers))
    assert err.value.status_code == 401
    assert upserts == []


def test_a_tampered_body_is_rejected(upserts):
    signed_for = body_of()
    tampered = body_of({**INSTALLED, "payload": {"id": "evil99"}})
    with pytest.raises(HTTPException) as err:
        call(make_request(tampered, {"X-Hub-Signature": sign(signed_for)}))
    assert err.value.status_code == 401 and upserts == []


def test_without_a_configured_secret_everything_is_refused(monkeypatch, upserts):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "")
    body = body_of()
    with pytest.raises(HTTPException) as err:
        call(make_request(body, {"X-Hub-Signature": sign(body, secret="")}))
    assert err.value.status_code == 503 and upserts == []


def test_the_challenge_handshake_also_needs_a_valid_signature(upserts):
    with pytest.raises(HTTPException):
        call(make_request(b"", {}, query="hub.challenge=abc"))


def test_processing_errors_do_not_leak_details(monkeypatch, upserts):
    monkeypatch.setattr(
        webhook, "upsert_integration", lambda **kw: (_ for _ in ()).throw(RuntimeError("password=hunter2 refused"))
    )
    body = body_of()
    with pytest.raises(HTTPException) as err:
        call(make_request(body, {"X-Hub-Signature": sign(body)}))
    assert err.value.status_code == 500
    assert err.value.detail == "Webhook processing failed" and "hunter2" not in str(err.value.detail)


def test_the_payload_is_not_logged(upserts, caplog):
    body = body_of()
    with caplog.at_level(logging.DEBUG):
        call(make_request(body, {"X-Hub-Signature": sign(body)}))
    for sensitive in ("A Person", "957937", "Intelliflo Ltd (iO Store)", SECRET):
        assert sensitive not in caplog.text
    assert "AppInstalled" in caplog.text  # the event type and id are logged, not the content


def test_an_unrecognised_event_is_acknowledged_without_recording(upserts):
    body = body_of({"event": "Something", "id": "x"})
    assert call(make_request(body, {"X-Hub-Signature": sign(body)})).status_code == 200
    assert upserts == []
