"""Browser page-view beacons. Validated, then one span on the posted trace.

The portal server forwards ``POST /rum`` here. A missing or malformed trace id emits nothing
and answers 204 — the page has already been served, and the body is never logged.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from app.tracing import BrowserView

_PORTALS = frozenset({"client", "platform", "admin"})
_TRACE = re.compile(r"^[0-9a-f]{32}$")
_SPAN = re.compile(r"^[0-9a-f]{16}$")
_RAW = re.compile(r"[?#@\s]|://")
_MAX_MS = 600_000.0
_MAX_CLS = 10.0
_MAX_ROUTE = 200


def parse_rum(payload: object) -> BrowserView | None:
    """A view worth a span, or ``None`` when the body must be ignored."""
    if not isinstance(payload, dict):
        return None
    trace_id = _hex_id(payload.get("trace_id"), _TRACE)
    if trace_id is None or trace_id == "0" * 32:
        return None
    route = _route(payload.get("route"))
    portal = payload.get("portal")
    settle = _clamp(payload.get("settle_ms"), 0.0, _MAX_MS)
    if route is None or portal not in _PORTALS or settle is None:
        return None
    attributes: dict[str, str | float] = {
        "rum.route": route,
        "rum.portal": str(portal),
        "rum.settle_ms": settle,
    }
    for key, field in (
        ("rum.lcp_ms", "lcp_ms"),
        ("rum.inp_ms", "inp_ms"),
        ("rum.ttfb_ms", "ttfb_ms"),
    ):
        value = _clamp(payload.get(field), 0.0, _MAX_MS)
        if value is not None:
            attributes[key] = value
    cls = _clamp(payload.get("cls"), 0.0, _MAX_CLS)
    if cls is not None:
        attributes["rum.cls"] = cls
    span_id = _hex_id(payload.get("span_id"), _SPAN)
    if span_id == "0" * 16:
        span_id = None
    return BrowserView(trace_id=trace_id, span_id=span_id, attributes=attributes)


def identified(
    view: BrowserView,
    *,
    user_id: str | None,
    tenant_id: int | None,
    session_id: str | None,
) -> BrowserView:
    """Stamp the caller the server resolved. The beacon body cannot set these.

    ``user_id`` is ``auth.uid()`` (the internal user), ``tenant_id`` is
    ``auth.tenant_id()``, and ``session_id`` is the access token's session.
    A name, an email and an IP are not span attributes.
    """
    attributes = dict(view.attributes)
    if user_id:
        attributes["enduser.id"] = user_id
    if tenant_id is not None:
        attributes["tenant.id"] = str(tenant_id)
    if session_id:
        attributes["session.id"] = session_id
    return BrowserView(trace_id=view.trace_id, span_id=view.span_id, attributes=attributes)


def _hex_id(value: object, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str) or not pattern.fullmatch(value.strip().lower()):
        return None
    return value.strip().lower()


def _route(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    route = value.strip()
    if not route.startswith("/") or len(route) > _MAX_ROUTE or _RAW.search(route):
        return None
    return route


def _clamp(value: object, low: float, high: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return max(low, min(high, number))


def rum_body(raw: bytes) -> Any:
    """JSON object from a small body, or ``None``. Callers treat ``None`` as 'emit nothing'."""
    if len(raw) > 4096:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
