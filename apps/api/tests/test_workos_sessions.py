"""WorkOS session rows: IP and user agent are kept, and a foreign session id is refused."""

from datetime import UTC, datetime
from enum import Enum
from types import SimpleNamespace

import pytest

from app.workos_sessions import active_session_views, require_owned_session


class _Status(Enum):
    active = "active"
    revoked = "revoked"


def _session(**kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


def test_active_rows_keep_ip_and_user_agent_and_lead_with_the_current_browser() -> None:
    rows = active_session_views(
        [
            _session(
                id="session_old",
                status=_Status.active,
                ip_address="203.0.113.8",
                user_agent="Chrome/120",
                auth_method="password",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                expires_at=datetime(2026, 1, 8, tzinfo=UTC),
            ),
            _session(
                id="session_here",
                status=_Status.active,
                ip_address="203.0.113.9",
                user_agent="Safari/17",
                auth_method="password",
                created_at=datetime(2026, 2, 1, tzinfo=UTC),
                expires_at=None,
            ),
            _session(
                id="session_dead",
                status=_Status.revoked,
                ip_address="198.51.100.1",
                user_agent="Chrome/120",
                auth_method="password",
                created_at=datetime(2026, 3, 1, tzinfo=UTC),
                expires_at=None,
            ),
        ],
        current_session_id="session_here",
    )
    assert [row["id"] for row in rows] == ["session_here", "session_old"]
    assert rows[0]["ip_address"] == "203.0.113.9"
    assert rows[0]["user_agent"] == "Safari/17"
    assert rows[0]["current"] is True
    assert rows[1]["current"] is False
    assert rows[1]["expires_at"] == "2026-01-08T00:00:00+00:00"


def test_a_session_that_is_not_on_the_users_list_is_refused() -> None:
    items = [_session(id="session_here", status=_Status.active)]
    require_owned_session(items, "session_here")
    with pytest.raises(LookupError):
        require_owned_session(items, "session_someone_else")
