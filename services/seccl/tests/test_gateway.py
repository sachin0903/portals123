"""Webhook gateway: signature verification, event unwrap, enrich + publish."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from seccl_sync import gateway

SECRET = "shhh-test-secret"  # noqa: S105 - test fixture


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SECCL_WEBHOOK_SECRET", SECRET)
    return TestClient(gateway.app, raise_server_exceptions=False)


@pytest.fixture()
def published(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[dict[str, Any]]]]:
    calls: list[tuple[str, list[dict[str, Any]]]] = []

    async def fake_publish(stream: str, rows: list[dict[str, Any]]) -> None:
        calls.append((stream, rows))

    async def fake_enrich(kind: str, event: dict[str, Any]) -> dict[str, Any]:
        return {**event, "enriched": True}

    monkeypatch.setattr(gateway, "publish_rows", fake_publish)
    monkeypatch.setattr(gateway, "enrich_event", fake_enrich)
    return calls


def _sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _post(client: TestClient, kind: str, payload: Any, signature: str | None = None) -> Any:
    body = json.dumps(payload).encode()
    return client.post(
        f"/webhooks/seccl/{kind}",
        content=body,
        headers={"webhook-signature": signature if signature is not None else _sign(body)},
    )


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_unknown_kind_404(client: TestClient) -> None:
    assert _post(client, "nonsense", {"events": []}).status_code == 404


def test_missing_secret_503(monkeypatch: pytest.MonkeyPatch) -> None:
    # setenv to empty, not delenv: pydantic-settings falls back to the .env file
    # for a DELETED var (a real local .env would leak in), but an empty env var
    # takes priority and reads as unset.
    monkeypatch.setenv("SECCL_WEBHOOK_SECRET", "")
    client = TestClient(gateway.app, raise_server_exceptions=False)
    body = json.dumps({"events": []}).encode()
    response = client.post("/webhooks/seccl/transaction", content=body)
    assert response.status_code == 503


def test_bad_signature_401(client: TestClient) -> None:
    response = _post(client, "transaction", {"events": []}, signature="deadbeef")
    assert response.status_code == 401


def test_base64_signature_accepted(
    client: TestClient, published: list[tuple[str, list[dict[str, Any]]]]
) -> None:
    payload = {"events": [{"id": "T1", "updateDate": "2026-07-13"}]}
    body = json.dumps(payload).encode()
    signature = base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()
    assert _post(client, "transaction", payload, signature=signature).status_code == 200


def test_events_enriched_and_published(
    client: TestClient, published: list[tuple[str, list[dict[str, Any]]]]
) -> None:
    payload = {
        "events": [
            {"id": "T1", "firmId": "F", "updateDate": "2026-07-13T10:00:00Z"},
            {"id": "T2", "firmId": "F", "updateDate": "2026-07-13T10:05:00Z"},
        ]
    }
    response = _post(client, "transaction", payload)
    assert response.status_code == 200
    assert response.json() == {"accepted": 2}
    assert len(published) == 1
    stream, rows = published[0]
    assert stream == "transactions"
    assert [row["id"] for row in rows] == ["T1", "T2"]
    assert all(row["enriched"] for row in rows)
    assert all(row["_event_key"] for row in rows)
    assert rows[0]["_event_key"] != rows[1]["_event_key"]


def test_bare_object_payload_treated_as_single_event(
    client: TestClient, published: list[tuple[str, list[dict[str, Any]]]]
) -> None:
    response = _post(client, "position", {"id": "A|C|GBP", "firmId": "F"})
    assert response.status_code == 200
    assert response.json() == {"accepted": 1}
    assert published[0][0] == "positions"


async def test_position_enrichment_reads_account_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no GET /position/{firm}/{id} on the Seccl API (position ids are
    composite account|asset|ccy strings) — enrichment must pull the account
    summary and pick the matching position out of positions[]."""
    fetched: list[str] = []

    class FakeApi:
        firm_id = "F"

        async def get_record(self, path: str) -> dict[str, Any]:
            fetched.append(path)
            return {
                "id": "A1",
                "positions": [
                    {"id": "A1|C|GBP", "positionType": "Cash", "currentValue": 12.5},
                    {"id": "A1|24LJD|GBP", "positionType": "Stock", "quantity": 3},
                ],
            }

    async def fake_api() -> Any:
        return FakeApi()

    monkeypatch.setattr(gateway, "_api", fake_api)

    event = {"id": "A1|24LJD|GBP", "accountId": "A1", "updateDate": "2026-07-20"}
    enriched = await gateway.enrich_event("position", event)
    assert fetched == ["/account/summary/F/A1"]
    assert enriched["positionType"] == "Stock"
    assert enriched["quantity"] == 3
    assert enriched["updateDate"] == "2026-07-20"  # event fields survive the merge

    # No accountId on the event: derived from the composite id.
    fetched.clear()
    enriched = await gateway.enrich_event("position", {"id": "A1|C|GBP"})
    assert fetched == ["/account/summary/F/A1"]
    assert enriched["positionType"] == "Cash"

    # Position vanished (sold out): the thin event still publishes.
    thin = {"id": "A1|GONE|GBP", "accountId": "A1"}
    assert await gateway.enrich_event("position", thin) == thin


def test_publish_failure_returns_500(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_enrich(kind: str, event: dict[str, Any]) -> dict[str, Any]:
        return event

    async def broken_publish(stream: str, rows: list[dict[str, Any]]) -> None:
        raise RuntimeError("interlace down")

    monkeypatch.setattr(gateway, "enrich_event", fake_enrich)
    monkeypatch.setattr(gateway, "publish_rows", broken_publish)
    response = _post(client, "transaction", {"events": [{"id": "T1"}]})
    assert response.status_code == 500
