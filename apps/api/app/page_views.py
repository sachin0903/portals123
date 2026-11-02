"""Signed-in portal navigations.

A page view is not an action. ``audit.actions`` stays the mutation and sign-in stream;
this module writes ``audit.page_views`` and folds both into the daily series a profile
heatmap draws. The profile's recorded-activity list reads the latest views beside the
action rows. The path is a pathname: no query string, no host.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_stream import ROUTINE_EVENT_TYPES

MAX_PATH = 512
PORTALS = frozenset({"client", "platform", "operations"})
_LONDON = ZoneInfo("Europe/London")
# A public id is 12 alphanumeric characters. These path roots carry one that names a client,
# an account, or a document. A team or profile path names a member, which the profile's own
# permission already allows the viewer to list.
_PUBLIC_ID = re.compile(r"^[A-Za-z0-9]{12}$")
_CLIENT_ROOTS = frozenset({"clients", "accounts", "insights", "look-through"})

# Sunday of the week containing `today`, then 52 weeks before that. 53 columns,
# the same window a calendar heatmap draws, zero-filled so a quiet day is a cell.
_WEEKS = 52


def page_path_visible(
    path: str,
    *,
    see_all_clients: bool,
    see_documents: bool,
    see_screening: bool,
) -> bool:
    """Whether this viewer may see a colleague's navigation.

    Counts on the heatmap have no identifiers. A path does. A client, account, or insight
    id is visible to someone who can see the whole book. A document id needs that grant and
    the document grant, including when it sits under the client
    (``/clients/{id}/documents/{id}``) rather than at ``/documents/{id}``. A compliance path
    needs the screening grant. Anything else — the team, a form, the home page — stays.
    """
    parts = [part for part in path.split("/") if part]
    if "compliance" in parts and not see_screening:
        return False
    if "documents" in parts and not (see_documents and see_all_clients):
        return False
    if not parts or not any(_PUBLIC_ID.fullmatch(part) for part in parts):
        return True
    head = parts[0]
    if head in _CLIENT_ROOTS:
        return see_all_clients
    return True


def visible_page_views(
    rows: list[dict[str, Any]],
    *,
    see_all_clients: bool,
    see_documents: bool,
    see_screening: bool,
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for row in rows:
        path = row.get("path")
        if isinstance(path, str) and page_path_visible(
            path,
            see_all_clients=see_all_clients,
            see_documents=see_documents,
            see_screening=see_screening,
        ):
            kept.append(row)
    return kept


def london_today() -> date:
    """The calendar day the heatmap calls today. London, not the process clock."""
    return datetime.now(_LONDON).date()


def activity_start(today: date) -> date:
    days_since_sunday = (today.weekday() + 1) % 7
    this_sunday = today - timedelta(days=days_since_sunday)
    return this_sunday - timedelta(weeks=_WEEKS)


def fill_activity_days(
    today: date,
    actions: dict[date, int],
    page_views: dict[date, int],
) -> list[dict[str, Any]]:
    """One row per day from the start of the window through `today`, missing days as zero."""
    day = activity_start(today)
    filled: list[dict[str, Any]] = []
    while day <= today:
        filled.append(
            {
                "date": day.isoformat(),
                "actions": int(actions.get(day, 0)),
                "page_views": int(page_views.get(day, 0)),
            }
        )
        day += timedelta(days=1)
    return filled


def normalise_page_path(raw: str) -> str | None:
    """The pathname to store. A query, a fragment, or a host is not a page view."""
    if not isinstance(raw, str):
        return None
    path = raw.strip()
    if not path.startswith("/") or path.startswith("//") or "://" in path or "\\" in path:
        return None
    for mark in ("?", "#"):
        cut = path.find(mark)
        if cut >= 0:
            path = path[:cut]
    if not path.startswith("/"):
        return None
    if any(ord(char) < 32 for char in path):
        return None
    if len(path) > MAX_PATH:
        path = path[:MAX_PATH]
    return path


def _counts(rows: list[Any]) -> dict[date, int]:
    counted: dict[date, int] = {}
    for row in rows:
        day = row["day"]
        if hasattr(day, "date"):
            # A timestamptz that slipped through is still a calendar day.
            day = day.date()
        counted[day] = int(row["n"])
    return counted


async def daily_activity(
    session: AsyncSession,
    tenant_id: int,
    user_id: str,
    *,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Actions and page views per day for the last 53 weeks, zero-filled.

    Action days use the same AML and routine-event exclusions as the profile's
    recorded-activity list. Dates are London calendar days.
    """
    today = today or london_today()
    start = activity_start(today)
    params = {
        "t": tenant_id,
        "u": user_id,
        "start": start,
        "hidden": list(ROUTINE_EVENT_TYPES),
    }
    action_rows = (
        (
            await session.execute(
                text(
                    "SELECT (created_at AT TIME ZONE 'Europe/London')::date AS day, "
                    "       count(*) AS n "
                    "FROM audit.actions "
                    "WHERE actor_id = :u AND (tenant_id = :t OR target_tenant_id = :t) "
                    "  AND (created_at AT TIME ZONE 'Europe/London')::date >= :start "
                    "  AND event_type NOT LIKE 'aml.%' AND event_type NOT LIKE 'action.aml-%' "
                    "  AND event_type <> ALL(CAST(:hidden AS text[])) "
                    "GROUP BY 1"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    view_rows = (
        (
            await session.execute(
                text(
                    "SELECT (created_at AT TIME ZONE 'Europe/London')::date AS day, "
                    "       count(*) AS n "
                    "FROM audit.page_views "
                    "WHERE actor_id = :u AND tenant_id = :t "
                    "  AND (created_at AT TIME ZONE 'Europe/London')::date >= :start "
                    "GROUP BY 1"
                ),
                {"t": tenant_id, "u": user_id, "start": start},
            )
        )
        .mappings()
        .all()
    )
    return fill_activity_days(today, _counts(list(action_rows)), _counts(list(view_rows)))
