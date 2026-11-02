"""Local copy of live WorkOS sessions.

The worker's events poller writes ``session.created`` and ``session.revoked``.
Opening a profile also lists that person after the response (``session_refresh``).
Painting the page only reads this table. Ending a session still calls WorkOS, which
is what signs the browser out; the row is removed in the same request when this
app revoked it, and by ``session.revoked`` otherwise.

WorkOS does not emit an expiry event. A row past ``expires_at`` stays stored and
is left out of the read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.sign_in_context import MAX_IP, MAX_USER_AGENT, device_label

# People whose sessions have never been listed, per environment, per poll tick.
# The event stream does not replay sessions from before the cursor, so this is
# how a session that is already live gets into the table. It is not a poll of
# every person: once ``sessions_synced_at`` is set, only events touch the row.
BACKFILL_BATCH = 25

_MAX_SESSION_ID = 128
_MAX_AUTH_METHOD = 64


def _plain(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str):
        return None
    cleaned = "".join(ch for ch in value if ch >= " " and ch != "\x7f").strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def _session_id(value: Any) -> str | None:
    sid = _plain(value, _MAX_SESSION_ID)
    if sid is None or any(ch.isspace() for ch in sid):
        return None
    return sid


def _status(item: Any) -> str:
    return _plain(getattr(item, "status", None), 32) or ""


def _timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text_value = _plain(value, 64)
    return text_value


async def forget(session: AsyncSession, session_id: str) -> None:
    """Drop one session. A miss is a no-op.

    The user's ``sessions_synced_at`` moves forward first, while the row still
    exists to name them. A list that was already in flight read the old value
    and then refuses to write, so it cannot put this session back.
    """
    await session.execute(
        text(
            "UPDATE auth.users u SET sessions_synced_at = clock_timestamp() "
            "FROM auth.sessions s "
            "WHERE s.workos_session_id = :sid AND u.id = s.user_id"
        ),
        {"sid": session_id},
    )
    await session.execute(
        text("DELETE FROM auth.sessions WHERE workos_session_id = :sid"),
        {"sid": session_id},
    )


async def owns(session: AsyncSession, *, tenant_id: int, user_id: str, session_id: str) -> bool:
    """True when this live row belongs to this person in this firm."""
    found = (
        await session.execute(
            text(
                "SELECT 1 FROM auth.sessions "
                "WHERE workos_session_id = :sid AND tenant_id = :t "
                "  AND user_id = CAST(:u AS uuid) "
                "  AND (expires_at IS NULL OR expires_at > clock_timestamp())"
            ),
            {"sid": session_id, "t": tenant_id, "u": user_id},
        )
    ).first()
    return found is not None


async def _upsert(session: AsyncSession, *, tenant_id: int, user_id: str, item: Any) -> None:
    session_id = _session_id(getattr(item, "id", None))
    if session_id is None:
        return
    if _status(item) != "active":
        await forget(session, session_id)
        return
    user_agent = _plain(getattr(item, "user_agent", None), MAX_USER_AGENT)
    await session.execute(
        text(
            "INSERT INTO auth.sessions ("
            "  workos_session_id, tenant_id, user_id, ip_address, user_agent, "
            "  auth_method, created_at, expires_at, updated_at"
            ") VALUES ("
            "  :sid, :t, CAST(:u AS uuid), :ip, :ua, :method, :created, :expires, "
            "  clock_timestamp()"
            ") ON CONFLICT (workos_session_id) DO UPDATE SET "
            "  tenant_id = EXCLUDED.tenant_id, "
            "  user_id = EXCLUDED.user_id, "
            "  ip_address = EXCLUDED.ip_address, "
            "  user_agent = EXCLUDED.user_agent, "
            "  auth_method = EXCLUDED.auth_method, "
            "  created_at = EXCLUDED.created_at, "
            "  expires_at = EXCLUDED.expires_at, "
            "  updated_at = clock_timestamp()"
        ),
        {
            "sid": session_id,
            "t": tenant_id,
            "u": user_id,
            "ip": _plain(getattr(item, "ip_address", None), MAX_IP),
            "ua": user_agent,
            "method": _plain(getattr(item, "auth_method", None), _MAX_AUTH_METHOD),
            "created": _timestamp(getattr(item, "created_at", None)),
            "expires": _timestamp(getattr(item, "expires_at", None)),
        },
    )


async def apply_event(session: AsyncSession, tenant_id: int, event: Any) -> None:
    """Apply one ``session.created`` or ``session.revoked`` event.

    A session for a login this firm does not mirror is skipped. The project
    stream can mention people who are not in this tenant, and that is not a
    conflict to park.
    """
    kind = getattr(event, "event", None)
    data = getattr(event, "data", None)
    if data is None or kind not in ("session.created", "session.revoked"):
        return
    session_id = _session_id(getattr(data, "id", None))
    if session_id is None:
        return
    if kind == "session.revoked" or _status(data) != "active":
        await forget(session, session_id)
        return
    workos_user_id = _plain(getattr(data, "user_id", None), 128)
    if workos_user_id is None:
        return
    user_id = (
        await session.execute(
            text(
                "SELECT id::text FROM auth.users "
                "WHERE tenant_id = :t AND workos_user_id = :wu AND deleted_at IS NULL"
            ),
            {"t": tenant_id, "wu": workos_user_id},
        )
    ).scalar_one_or_none()
    if user_id is None:
        return
    await _upsert(session, tenant_id=tenant_id, user_id=str(user_id), item=data)


async def store_listed_sessions(
    session: AsyncSession,
    *,
    tenant_id: int,
    user_id: str,
    items: list[Any],
    not_before: datetime,
    seen: datetime | None,
) -> bool:
    """Write one WorkOS list, unless a revoke landed after it was started.

    ``seen`` is this person's ``sessions_synced_at`` from before the list call.
    ``forget`` moves that column, so a list already in flight no longer matches
    and is dropped. The next list, taken after the revoke, is the one that lands.
    """
    won = (
        await session.execute(
            text(
                "UPDATE auth.users SET sessions_synced_at = clock_timestamp() "
                "WHERE id = CAST(:u AS uuid) AND tenant_id = :t "
                "  AND sessions_synced_at IS NOT DISTINCT FROM :seen "
                "RETURNING id"
            ),
            {"u": user_id, "t": tenant_id, "seen": seen},
        )
    ).scalar_one_or_none()
    if won is None:
        return False
    await replace_user_sessions(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        items=items,
        not_before=not_before,
    )
    return True


async def replace_user_sessions(
    session: AsyncSession,
    *,
    tenant_id: int,
    user_id: str,
    items: list[Any],
    not_before: datetime,
) -> None:
    """Replace this person's stored sessions with one WorkOS list.

    ``not_before`` is a database ``clock_timestamp()`` taken before the list
    call. Rows written after that — a session event that landed while the list
    was in flight — are left alone. Rows the list does not mention, and that
    were already stored, are deleted.
    """
    for item in items:
        session_id = _session_id(getattr(item, "id", None))
        if session_id is None:
            continue
        if _status(item) != "active":
            await forget(session, session_id)
            continue
        await _upsert(session, tenant_id=tenant_id, user_id=user_id, item=item)
    await session.execute(
        text(
            "DELETE FROM auth.sessions "
            "WHERE tenant_id = :t AND user_id = CAST(:u AS uuid) AND updated_at < :cutoff"
        ),
        {"t": tenant_id, "u": user_id, "cutoff": not_before},
    )


async def pending_backfill(
    session: AsyncSession, tenant_id: int, limit: int
) -> list[tuple[str, str]]:
    """``(user id, WorkOS user id)`` for people not yet listed, oldest first."""
    rows = (
        await session.execute(
            text(
                "SELECT id::text, workos_user_id FROM auth.users "
                "WHERE tenant_id = :t AND workos_user_id IS NOT NULL "
                "  AND deleted_at IS NULL AND sessions_synced_at IS NULL "
                "ORDER BY created_at, id "
                "LIMIT :n"
            ),
            {"t": tenant_id, "n": limit},
        )
    ).all()
    return [(str(row[0]), str(row[1])) for row in rows]


def _view(row: Any, *, current: bool) -> dict[str, object]:
    user_agent = row["user_agent"]
    return {
        "id": row["workos_session_id"],
        "ip_address": row["ip_address"],
        "user_agent": user_agent,
        "device": device_label(user_agent),
        "auth_method": row["auth_method"],
        "created_at": _iso(row["created_at"]),
        "expires_at": _iso(row["expires_at"]),
        "current": current,
    }


async def sessions_for_user(
    session: AsyncSession,
    *,
    tenant_id: int,
    user_id: str,
    current_session_id: str | None = None,
) -> list[dict[str, object]]:
    """Active stored sessions, current browser first.

    When the caller's own session is not in the table yet, a blank current row
    is added in memory so the page can name this browser from the request.
    That row is not written.
    """
    rows = (
        (
            await session.execute(
                text(
                    "SELECT workos_session_id, ip_address, user_agent, auth_method, "
                    "       created_at, expires_at "
                    "FROM auth.sessions "
                    "WHERE tenant_id = :t AND user_id = CAST(:u AS uuid) "
                    "  AND (expires_at IS NULL OR expires_at > clock_timestamp()) "
                    "ORDER BY created_at DESC NULLS LAST, workos_session_id"
                ),
                {"t": tenant_id, "u": user_id},
            )
        )
        .mappings()
        .all()
    )
    current_id = _session_id(current_session_id)
    views = [
        _view(row, current=bool(current_id and row["workos_session_id"] == current_id))
        for row in rows
    ]
    if current_id and not any(view["id"] == current_id for view in views):
        views.append(
            {
                "id": current_id,
                "ip_address": None,
                "user_agent": None,
                "device": None,
                "auth_method": None,
                "created_at": None,
                "expires_at": None,
                "current": True,
            }
        )
    views.sort(key=lambda view: str(view["created_at"] or ""), reverse=True)
    views.sort(key=lambda view: 0 if view["current"] else 1)
    return views
