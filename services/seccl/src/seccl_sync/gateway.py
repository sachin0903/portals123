"""Seccl webhook gateway: verify, enrich, publish into interlace streams.

Seccl's webhook payloads are thin (an id and a status), so the gateway fetches
the full entity before publishing — the streams then hold current records and
the staging merges stay trivial. A failed publish returns non-2xx to Seccl so
the delivery isn't silently lost.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

from seccl_sync.client import SecclApiError, SecclClient
from seccl_sync.records import event_key, normalise_record
from seccl_sync.settings import GatewaySettings, SecclSettings

logger = logging.getLogger("seccl_sync.gateway")

app = FastAPI(title="seccl-gateway")

# Seccl webhook kind (their config path segment) -> interlace stream name
KIND_TO_STREAM = {
    "transaction": "transactions",
    "position": "positions",
    "client": "clients",
    "exception": "exceptions",
    "message": "messages",
    "transfer": "transfers",
}
_ENRICH_CONCURRENCY = 8

_client: SecclClient | None = None


async def _api() -> SecclClient:
    global _client
    if _client is None:
        client = SecclClient()
        await client.__aenter__()
        _client = client
    return _client


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Seccl signs deliveries with HMAC-SHA256 of the body; hex or base64."""
    if not signature:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256)
    if hmac.compare_digest(digest.hexdigest(), signature.strip().lower()):
        return True
    encoded = base64.b64encode(digest.digest()).decode()
    return hmac.compare_digest(encoded, signature.strip())


async def enrich_event(kind: str, event: dict[str, Any]) -> dict[str, Any]:
    """Fetch the full entity for a thin event; fall back to the event itself."""
    try:
        api = await _api()
        if kind == "transaction" and event.get("id"):
            return {
                **event,
                **await api.get_record(f"/portfoliotransaction/{api.firm_id}/{event['id']}"),
            }
        if kind == "position" and event.get("id"):
            # There is no GET /position/{firm}/{id} — position ids are composite
            # "{accountId}|{assetIdOrC}|{ccy}" strings, and the position lives
            # inside its account's summary. Fetch the account, pick the position.
            account_id = str(event.get("accountId") or str(event["id"]).split("|")[0])
            summary = await api.get_record(f"/account/summary/{api.firm_id}/{account_id}")
            positions = summary.get("positions") or []
            match = next(
                (p for p in positions if isinstance(p, dict) and p.get("id") == event["id"]),
                None,
            )
            if match is not None:
                return {**event, **match}
            return event  # sold out between event and fetch: keep the thin payload
        if kind == "client":
            data = event.get("data")
            if isinstance(data, dict):  # create events embed the whole entity
                return {**{k: v for k, v in event.items() if k != "data"}, **data}
            if event.get("clientId"):
                return {
                    **event,
                    **await api.get_record(f"/client/{api.firm_id}/{event['clientId']}"),
                }
    except SecclApiError as exc:
        logger.warning("enrichment failed for %s event, publishing thin payload: %s", kind, exc)
    if kind == "exception":
        notification = event.get("notification")
        if isinstance(notification, dict):
            return {**{k: v for k, v in event.items() if k != "notification"}, **notification}
    return event


async def enrich_events(kind: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(_ENRICH_CONCURRENCY)

    async def one(event: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            enriched = await enrich_event(kind, event)
        row = normalise_record(enriched)
        row["_event_key"] = event_key(kind, event)  # keyed on the ORIGINAL event identity
        return row

    return list(await asyncio.gather(*(one(event) for event in events)))


async def publish_rows(stream: str, rows: list[dict[str, Any]]) -> None:
    settings = GatewaySettings()
    headers = (
        {"Authorization": f"Bearer {settings.interlace_api_key}"}
        if settings.interlace_api_key
        else {}
    )
    async with httpx.AsyncClient(timeout=30) as http:
        response = await http.post(
            f"{settings.interlace_url}/streams/{stream}", json=rows, headers=headers
        )
        response.raise_for_status()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhooks/seccl/{kind}")
async def receive(kind: str, request: Request) -> dict[str, int]:
    stream = KIND_TO_STREAM.get(kind)
    if stream is None:
        raise HTTPException(status_code=404, detail=f"unknown webhook kind {kind!r}")
    secret = SecclSettings().webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="SECCL_WEBHOOK_SECRET is not configured")
    body = await request.body()
    if not verify_signature(body, request.headers.get("webhook-signature", ""), secret):
        raise HTTPException(status_code=401, detail="bad webhook signature")

    payload = json.loads(body)
    if isinstance(payload, dict):
        raw_events = payload.get("events", [payload])
    else:
        raw_events = list(payload)
    events = [event for event in raw_events if isinstance(event, dict)]
    rows = await enrich_events(kind, events)
    if rows:
        await publish_rows(stream, rows)  # non-2xx from interlace -> 500 back to Seccl
    return {"accepted": len(rows)}
