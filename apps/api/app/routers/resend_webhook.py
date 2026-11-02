"""``POST /webhooks/resend`` — Resend's delivery events for mail we sent.

The out-of-band half of the outbound-email ledger (``docs/comms.md``). ``send_email`` records the
send and keeps the id Resend answered with; Resend then posts here as the message is delivered,
bounces, or is complained about, and ``comms.messages`` advances. Without this the ledger could
only ever say "we handed it to Resend", which is the very thing that was not good enough.

**No principal.** The caller is a vendor, so there is no bearer token and no acting human — hence
the entry on ``actions.EXEMPT_MUTATING_ROUTES``, and hence ``system_transaction``: there are no
claims for RLS to scope by, and the row is found by an id we minted ourselves.

**It always answers 200.** Resend retries a non-2xx, and every outcome here — an unparseable body,
a message id we do not know, an event type we do not model — is one that retrying cannot improve.
The ``handled``/``reason`` pair says what happened.

**Three deliberate choices:**

1. *No secret means the endpoint is OFF*, not "accept anything". This route can flip a message to
   ``bounced`` — a claim a firm will act on. The standing doctrine is ``app/email.py``'s allowlist
   and ``flows.ai_tenants``: absence of configuration means "do nothing", never "trust
   everybody".
2. *Svix signatures, not a bare HMAC.* Resend signs through Svix, so the signed payload is
   ``{svix-id}.{svix-timestamp}.{body}`` and the header may carry several space-separated
   ``v1,<sig>`` values (Svix rotates by sending both). Implemented here rather than pulled in as a
   dependency because the wire format is twenty lines — the reasoning ``pkgs/seccl`` and the SEI
   PGP work already followed.
3. *A timestamp tolerance.* Without one a captured callback is replayable for ever.

**Verified BEFORE parsed.** The body is read as bytes, the signature checked over those exact
bytes, and only then handed to a JSON parser — the order matters, because the signature covers the
raw body and re-serialising anything would check a different value than the one that was signed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.comms import ledger
from app.config import settings
from app.db import system_transaction
from app.email import sender_domains

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

#: Svix's three headers. Resend sends them lower-cased; header lookup is case-insensitive.
ID_HEADER = "svix-id"
TIMESTAMP_HEADER = "svix-timestamp"
SIGNATURE_HEADER = "svix-signature"

#: How far out of step a callback's timestamp may be. Without a bound the signature never expires,
#: so anyone who captures one callback can replay it indefinitely. Five minutes each way is Svix's
#: own recommendation and absorbs ordinary clock drift between our host and theirs.
TOLERANCE_SECONDS = 5 * 60

#: A delivery event is a few KB. Anything larger is not one, and reading an unbounded body into
#: memory on an UNAUTHENTICATED route is the hazard — so the declared length is checked first.
MAX_BODY_BYTES = 256 * 1024


class ResendWebhookAck(BaseModel):
    """What we tell Resend. Always a 200; ``handled`` says whether a message was advanced."""

    handled: bool
    reason: str


def _secret_bytes(secret: str) -> bytes | None:
    """The signing key as bytes.

    Svix writes the secret ``whsec_<base64>``, and it is the DECODED base64 that keys the HMAC —
    not the string as displayed. Signing with the literal text is the classic way to build a
    verifier that rejects every genuine callback, so the prefix is stripped and the remainder
    decoded. A secret that is not valid base64 is treated as raw key material rather than raising,
    because some tooling stores the decoded value directly.
    """
    raw = secret.strip()
    if not raw:
        return None
    raw = raw.removeprefix("whsec_")
    try:
        return base64.b64decode(raw, validate=True)
    except Exception:  # noqa: BLE001 - not base64; take the bytes as given
        return raw.encode()


def timestamp_ok(provided: str, *, now: float | None = None) -> bool:
    """Is this callback recent enough to accept? Pure, so the replay window is directly testable."""
    try:
        sent = int(provided)
    except (TypeError, ValueError):
        return False
    return abs((now if now is not None else time.time()) - sent) <= TOLERANCE_SECONDS


def signature_ok(body: bytes, headers: Any, secret: str, *, now: float | None = None) -> bool:
    """True when the body carries a valid, in-date Svix signature.

    Returns False for a missing secret as well as a bad signature: the caller checks
    ``configured`` first and reports it separately, and defaulting to False here means a future
    caller that forgets cannot accidentally accept everything.
    """
    key = _secret_bytes(secret)
    if key is None:
        return False
    getter = getattr(headers, "get", None)
    if not callable(getter):  # pragma: no cover - a real Request always has one
        return False
    msg_id = (getter(ID_HEADER) or "").strip()
    timestamp = (getter(TIMESTAMP_HEADER) or "").strip()
    provided = (getter(SIGNATURE_HEADER) or "").strip()
    if not msg_id or not timestamp or not provided:
        return False
    if not timestamp_ok(timestamp, now=now):
        return False

    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    # The header is a space-separated list of `v<version>,<signature>` — Svix sends more than one
    # while a secret is being rotated, and ANY of them matching is a pass. Compared in constant
    # time, and only the v1 scheme is understood.
    for entry in provided.split():
        version, _, candidate = entry.partition(",")
        if version == "v1" and hmac.compare_digest(candidate, expected):
            return True
    return False


def _occurred_at(payload: dict[str, Any]) -> datetime | None:
    """When the provider says it happened, not when we read it — a retried callback delivered an
    hour late must not stamp the timeline an hour late."""
    raw = payload.get("created_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _message_id(payload: dict[str, Any]) -> str | None:
    """Resend nests the message under ``data``; ``email_id`` is its id there."""
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    for key in ("email_id", "id"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value[:200]
    return None


@router.post("/resend", response_model=ResendWebhookAck)
async def resend_callback(request: Request) -> ResendWebhookAck:
    if not settings.resend_webhook_secret.strip():
        # OFF, not open. See the module header: this route can mark a message bounced, and an
        # unconfigured deployment must not accept a claim it cannot authenticate.
        logger.info(
            "comms: a Resend callback arrived but RESEND_WEBHOOK_SECRET is not set — ignored",
            extra={"event": "resend_webhook_unconfigured"},
        )
        return ResendWebhookAck(handled=False, reason="not_configured")

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        logger.warning("comms: refusing an oversized Resend callback (%s bytes declared)", declared)
        return ResendWebhookAck(handled=False, reason="body_too_large")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        logger.warning("comms: refusing an oversized Resend callback (%d bytes)", len(body))
        return ResendWebhookAck(handled=False, reason="body_too_large")

    # Over the RAW BYTES, and before anything parses them.
    if not signature_ok(body, request.headers, settings.resend_webhook_secret):
        logger.warning(
            "comms: Resend callback rejected — signature missing, wrong or out of date",
            extra={"event": "resend_webhook_bad_signature"},
        )
        return ResendWebhookAck(handled=False, reason="bad_signature")

    try:
        payload: Any = await request.json()
    except Exception:  # noqa: BLE001 - a body we cannot parse is an outcome, not a server error
        logger.warning("comms: Resend callback body was not JSON")
        return ResendWebhookAck(handled=False, reason="unparseable")
    if not isinstance(payload, dict):
        return ResendWebhookAck(handled=False, reason="unparseable")

    event_type = payload.get("type")
    if not isinstance(event_type, str) or not event_type:
        return ResendWebhookAck(handled=False, reason="no_event_type")
    if event_type in ledger.NOT_TRACKED:
        # Dropped BEFORE the lookup, so an open/click payload never reaches the database. See
        # `ledger.NOT_TRACKED`: an open is a tracking pixel, which is evidence of nothing in either
        # direction, and this product does not do read receipts.
        return ResendWebhookAck(handled=False, reason="not_tracked")
    if event_type.startswith("domain."):
        # A domain has no message id. Handling it here, before that gate, is what stops a
        # verification callback being answered `no_message_id` and never reaching the row.
        async with system_transaction() as session:
            matched = await sender_domains.apply_domain_event(
                session, event_type, payload.get("data")
            )
        if not matched:
            return ResendWebhookAck(handled=False, reason="unknown_domain")
        return ResendWebhookAck(handled=True, reason=event_type)
    message_id = _message_id(payload)
    if message_id is None:
        return ResendWebhookAck(handled=False, reason="no_message_id")

    async with system_transaction() as session:
        matched = await ledger.apply_provider_event(
            session,
            provider_message_id=message_id,
            event_type=event_type,
            # Svix's own id for this DELIVERY is the idempotency handle: Resend sends the same
            # callback more than once, and it is the same `svix-id` each time.
            provider_event_id=(request.headers.get(ID_HEADER) or "").strip() or None,
            occurred_at=_occurred_at(payload) or datetime.now(UTC),
            payload=payload,
        )

    if not matched:
        # Not an error. A callback for a pruned row, and one for mail sent by another deployment
        # sharing this Resend account, look exactly like this — and answering non-2xx to either
        # buys a retry storm and nothing else.
        logger.info(
            "comms: no ledger row for Resend message %s — callback ignored",
            message_id,
            extra={"event": "resend_webhook_unknown_message"},
        )
        return ResendWebhookAck(handled=False, reason="unknown_message")
    return ResendWebhookAck(handled=True, reason=event_type)
