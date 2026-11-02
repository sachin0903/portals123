"""ComplyCube webhook signatures: inbound HMAC of the raw body, outbound t=,v1= for firms."""

from __future__ import annotations

import hashlib
import hmac
import json

from litestar.testing import TestClient

from app.complycube.signature import HEADER, OUTBOUND_HEADER, digest, sign, verify
from external.app import create_app

SECRET = "whsec_test"  # noqa: S105 - a fixture value, not a credential
BODY = b'{"id":"evt_1","type":"check.completed","payload":{"id":"chk_1"}}'


def test_inbound_signature_is_the_hex_hmac_of_the_raw_body() -> None:
    expected = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
    assert digest(SECRET, BODY) == expected
    assert verify(SECRET, BODY, expected)
    # Surrounding whitespace in the header value is not part of the MAC.
    assert verify(SECRET, BODY, f"  {expected}\n")


def test_inbound_signature_rejects_a_changed_body_a_wrong_secret_or_no_header() -> None:
    good = digest(SECRET, BODY)
    assert not verify(SECRET, BODY + b" ", good)
    assert not verify("another-secret", BODY, good)
    assert not verify(SECRET, BODY, None)
    assert not verify(SECRET, BODY, "")
    assert not verify(SECRET, BODY, good[:-1] + ("0" if good[-1] != "0" else "1"))


def test_a_blank_secret_refuses_every_delivery() -> None:
    assert not verify("", BODY, digest("", BODY))


def test_the_old_timestamped_format_is_not_accepted_inbound() -> None:
    """``t=,v1=`` is our OUTBOUND format. ComplyCube does not send it, so it must not verify."""
    assert not verify(SECRET, BODY, sign(SECRET, BODY, 50))


def test_a_header_with_non_ascii_text_is_refused_not_raised() -> None:
    assert not verify(SECRET, BODY, "é" * 64)


def test_outbound_signature_is_timestamped_for_firm_webhooks() -> None:
    header = sign(SECRET, BODY, 1_700_000_000)
    stamp, mac = header.split(",")
    assert stamp == "t=1700000000"
    expected = hmac.new(SECRET.encode(), b"1700000000." + BODY, hashlib.sha256).hexdigest()
    assert mac == f"v1={expected}"
    assert HEADER == "complycube-signature"
    assert OUTBOUND_HEADER == "x-graphene-signature"


def _post(client: TestClient, body: bytes, signature: str | None) -> object:
    headers = {"content-type": "application/json"}
    if signature is not None:
        headers["ComplyCube-Signature"] = signature
    return client.post("/internal/webhooks/screening", content=body, headers=headers)


def test_the_receiver_stores_a_signed_event_and_answers_200() -> None:
    app = create_app(stub=True, screening_secret=SECRET)
    with TestClient(app) as client:
        response = client.post(
            "/internal/webhooks/screening",
            content=BODY,
            headers={"ComplyCube-Signature": digest(SECRET, BODY)},
        )
        assert response.status_code == 200
        assert response.json() == {"received": True}


def test_the_receiver_refuses_a_bad_signature_before_reading_the_event() -> None:
    accepted: list[dict[str, object]] = []

    async def accept(event: dict[str, object]) -> None:
        accepted.append(event)

    app = create_app(stub=True, screening_secret=SECRET, accept_event=accept)
    with TestClient(app) as client:
        response = client.post(
            "/internal/webhooks/screening",
            content=BODY,
            headers={"ComplyCube-Signature": digest("wrong", BODY)},
        )
        missing = client.post("/internal/webhooks/screening", content=BODY)
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_signature"
    assert missing.status_code == 400
    assert accepted == []


def test_the_receiver_refuses_an_event_without_an_id() -> None:
    accepted: list[dict[str, object]] = []

    async def accept(event: dict[str, object]) -> None:
        accepted.append(event)

    app = create_app(stub=True, screening_secret=SECRET, accept_event=accept)
    with TestClient(app) as client:
        for event in (
            {"type": "check.completed", "payload": {"id": "chk_1"}},
            {"id": "", "type": "check.completed", "payload": {"id": "chk_1"}},
            {"id": 42, "type": "check.completed"},
        ):
            body = json.dumps(event).encode()
            response = client.post(
                "/internal/webhooks/screening",
                content=body,
                headers={"ComplyCube-Signature": digest(SECRET, body)},
            )
            assert response.status_code == 400
            assert response.json()["code"] == "invalid_body"
            assert "complycube" not in response.text.lower()
    assert accepted == []


def test_the_receiver_hands_a_good_event_to_the_inbox_and_nothing_else() -> None:
    accepted: list[dict[str, object]] = []

    async def accept(event: dict[str, object]) -> None:
        accepted.append(event)

    app = create_app(stub=True, screening_secret=SECRET, accept_event=accept)
    with TestClient(app) as client:
        response = client.post(
            "/internal/webhooks/screening",
            content=BODY,
            headers={"ComplyCube-Signature": digest(SECRET, BODY)},
        )
    assert response.status_code == 200
    assert accepted == [json.loads(BODY)]
