"""Intelliflo webhook — exposes a router to be mounted by the central webhook."""

import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qs

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from intelliflo_sync.providers.intelliflo import config
from intelliflo_sync.shared.base.auth import OAuth2Auth
from intelliflo_sync.shared.db import upsert_integration

logger = logging.getLogger(__name__)

router = APIRouter()


# WebSub signs the body with HMAC and sends "<algorithm>=<hex digest>" in X-Hub-Signature.
_HASHES = {
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
    "sha384": hashlib.sha384,
    "sha512": hashlib.sha512,
}


def validate_webhook_signature(raw_body: bytes, secret: str, signature_header: str) -> bool:
    """True if `signature_header` ("sha256=<hex>", also sha1/384/512) is the HMAC of the exact body bytes.

    False for a missing secret, a missing header, an unknown algorithm or a mismatch. Compared in constant time.
    """
    if not secret or not signature_header:
        return False
    algorithm, separator, digest = signature_header.strip().partition("=")
    hash_function = _HASHES.get(algorithm.strip().lower()) if separator else None
    if hash_function is None:
        return False
    expected = hmac.new(secret.encode(), raw_body, hash_function).hexdigest()
    return hmac.compare_digest(expected, digest.strip().lower())


def _app_id(payload: dict) -> str | None:
    """The Intelliflo app id from an AppInstalled/AppUninstalled payload (payload.id)."""
    app = payload.get("payload")
    app_id = app.get("id") if isinstance(app, dict) else None
    return str(app_id) if app_id else None


@router.get("/webhook")
async def verify_webhook(request: Request):
    """Handle WebSub subscription verification from Intelliflo."""
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    challenge = params.get("hub.challenge")
    topic = params.get("hub.topic")

    logger.info("Webhook verification — mode: %s, topic: %s", mode, topic)

    if mode == "subscribe" and challenge:
        logger.info("Returning hub.challenge: %s", challenge)
        return PlainTextResponse(content=challenge)

    raise HTTPException(status_code=400, detail="Invalid verification request")


@router.post("/webhook")
async def receive_webhook(request: Request):
    """Receive incoming webhook data from Intelliflo. Nothing is read or written unless the body is signed."""
    raw_body = await request.body()

    # Authenticate first, on the exact bytes, before parsing or touching the database. Fail closed: with no
    # secret configured nothing can be verified, so nothing is accepted.
    secret = config.WEBHOOK_SECRET
    if not secret:
        logger.error("Webhook rejected: INTELLIFLO_WEBHOOK_SECRET is not configured, so no request can be verified")
        raise HTTPException(status_code=503, detail="Webhook not configured")
    signature = request.headers.get("x-hub-signature-256") or request.headers.get("x-hub-signature") or ""
    if not validate_webhook_signature(raw_body, secret, signature):
        client = request.client.host if request.client else "unknown"
        logger.warning("Webhook rejected: missing or invalid signature (from %s)", client)
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        content_type = request.headers.get("content-type", "")
        query_params = dict(request.query_params)

        if "application/json" in content_type:
            payload = json.loads(raw_body)
        elif "application/x-www-form-urlencoded" in content_type or not content_type:
            payload = parse_qs(raw_body.decode("utf-8"))
        else:
            payload = raw_body.decode("utf-8")

        challenge = query_params.get("hub.challenge") or (
            payload.get("hub.challenge", [None])[0] if isinstance(payload, dict) else None
        )
        if challenge:
            logger.info("Returning hub.challenge: %s", challenge)
            return PlainTextResponse(content=challenge)

        if isinstance(payload, dict):
            event = payload.get("event", "")
            firm_id = payload.get("firm_id")
            logger.info("Webhook %s received (event id %s)", event or "(no event)", payload.get("id"))

            if event == "AppInstalled":
                installed = True
                platform = payload.get("installedFor", {})
            elif event == "AppUninstalled":
                installed = False
                platform = payload.get("uninstalledFor", {})
            else:
                logger.warning("Unrecognised event '%s' — skipping upsert", event)
                return JSONResponse(status_code=200, content={"status": "received"})

            tenant = platform.get("tenant", {})
            tenant_id = tenant.get("id")
            tenant_name = tenant.get("name")

            if tenant_id:
                integration_id = upsert_integration(
                    firm_id=firm_id,
                    platform="intelliflo",
                    tenant_name=tenant_name,
                    tenant_id=str(tenant_id),
                    application_data=payload,
                    enabled=False,
                    installed=installed,
                    app_id=_app_id(payload),
                )
                logger.info("Upserted integration %s (event=%s) for firm %s", integration_id, event, firm_id)
            else:
                logger.warning(
                    "No tenant id in the %s event (id %s) — skipping upsert",
                    event,
                    payload.get("id"),
                )

        return JSONResponse(status_code=200, content={"status": "received"})

    except Exception as exc:
        logger.exception("Webhook processing failed")
        raise HTTPException(status_code=500, detail="Webhook processing failed") from exc


def register() -> None:
    """Register this webhook with Intelliflo Hub API on startup for all configured topics."""
    if not config.WEBHOOK_CALLBACK_URL:
        logger.warning("INTELLIFLO_WEBHOOK_CALLBACK_URL not set — skipping webhook registration")
        return
    missing = config.missing_credentials() + ([] if config.WEBHOOK_SECRET else ["INTELLIFLO_WEBHOOK_SECRET"])
    if missing:
        logger.error("Skipping webhook registration — not configured: %s", ", ".join(missing))
        return

    auth = OAuth2Auth(
        token_url=config.TOKEN_URL,
        client_id=config.CLIENT_ID,
        client_secret=config.CLIENT_SECRET,
        grant_type=config.GRANT_TYPE,
        scope=config.SCOPE,
        extra_data={"tenant_id": config.TENANT_ID},
    )

    url = f"{config.API_BASE_URL}/v2/hub/webhooks"
    headers = {
        "x-api-key": config.API_KEY,
        "Content-Type": "application/json",
        **auth.get_headers(),
    }

    for topic in config.WEBHOOK_TOPICS:
        payload = {
            "topic": topic,
            "callback": config.WEBHOOK_CALLBACK_URL,
            "secret": config.WEBHOOK_SECRET,
        }
        logger.info(
            "Registering Intelliflo webhook — topic: %s, callback: %s",
            topic,
            config.WEBHOOK_CALLBACK_URL,
        )
        response = requests.post(url, headers=headers, json=payload, timeout=30)

        if response.ok:
            logger.info("Webhook registered successfully for topic '%s': %s", topic, response.json())
        elif response.status_code == 409:
            logger.info("Webhook already registered for topic '%s' — skipping (409 Conflict)", topic)
        else:
            logger.error(
                "Webhook registration failed for topic '%s': %s — %s",
                topic,
                response.status_code,
                response.text,
            )
