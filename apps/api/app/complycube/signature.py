"""Webhook signatures, both directions.

INBOUND (ComplyCube to us): ``ComplyCube-Signature`` is the hex HMAC-SHA256 of the raw request
body, keyed with the webhook secret. There is no timestamp. Comparison is constant-time.

Replay is safe without one. ``compliance.events.provider_event_id`` is unique, so a replayed body
with the same event id is never queued twice, and the consumer does not trust the body for a
status: it fetches the check from the provider (``get_check``) and records what the provider says
now. A replay can at most make us read the truth again.

OUTBOUND (us to a firm's own webhook): ``X-Graphene-Signature: t=<unix>,v1=<hex>``, the HMAC-SHA256
of ``f"{t}.{body}"`` keyed with the firm's secret. ``sign`` is for that direction only.
"""

from __future__ import annotations

import hashlib
import hmac

HEADER = "complycube-signature"
OUTBOUND_HEADER = "x-graphene-signature"


def digest(secret: str, body: bytes) -> str:
    """The inbound signature value for ``body``."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, header: str | None) -> bool:
    """True when ``header`` is the HMAC of ``body`` under ``secret``. A blank secret refuses all."""
    if not secret or not header:
        return False
    return hmac.compare_digest(digest(secret, body).encode(), header.strip().encode())


def sign(secret: str, body: bytes, timestamp: int) -> str:
    """The outbound ``X-Graphene-Signature`` value for a firm webhook delivery."""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"
