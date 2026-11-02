"""Shape a WorkOS session list into the fields the portals show.

The pages read ``auth.sessions``. This is the live list, used when a revoke has to
confirm an id the mirror does not have yet. IP and user agent are on the session;
device type is not.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from app.sign_in_context import device_label


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    if hasattr(value, "value") and not isinstance(value, str):
        inner = value.value
        return str(inner) if inner else None
    text = str(value).strip()
    return text or None


def _when(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return _text(value)


def _status(item: Any) -> str:
    return _text(getattr(item, "status", None)) or ""


def active_session_views(
    items: list[Any], *, current_session_id: str | None = None
) -> list[dict[str, object]]:
    """Active sessions only, current browser first."""
    views: list[dict[str, object]] = []
    for item in items:
        if _status(item) != "active":
            continue
        session_id = _text(getattr(item, "id", None))
        if not session_id:
            continue
        views.append(
            {
                "id": session_id,
                "ip_address": _text(getattr(item, "ip_address", None)),
                "user_agent": _text(getattr(item, "user_agent", None)),
                "device": device_label(_text(getattr(item, "user_agent", None))),
                "auth_method": _text(getattr(item, "auth_method", None)),
                "created_at": _when(getattr(item, "created_at", None)),
                "expires_at": _when(getattr(item, "expires_at", None)),
                "current": bool(current_session_id and session_id == current_session_id),
            }
        )
    # Newest first, then the current browser to the top. Python's sort is stable, so the
    # second pass keeps the timestamp order inside each group.
    views.sort(key=lambda row: str(row["created_at"] or ""), reverse=True)
    views.sort(key=lambda row: 0 if row["current"] else 1)
    return views


def require_owned_session(items: list[Any], session_id: str) -> None:
    """Raise unless `session_id` is one of this user's sessions.

    WorkOS revokes by id alone. A guessed id from another user must not be forwarded.
    """
    for item in items:
        if _text(getattr(item, "id", None)) == session_id:
            return
    raise LookupError("That session does not belong to this user")
