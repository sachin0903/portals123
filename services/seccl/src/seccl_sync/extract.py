"""Shared helpers for the extraction models."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any


def date_floor(cursor: Any, overlap_days: int = 0) -> str:
    """A warehouse cursor value (str/date/datetime) -> YYYY-MM-DD, minus an
    overlap window so late-arriving rows around the boundary are re-pulled
    (the keyed merge makes the overlap idempotent)."""
    if isinstance(cursor, datetime):
        day = cursor.date()
    elif isinstance(cursor, date):
        day = cursor
    else:
        day = date.fromisoformat(str(cursor)[:10])
    return (day - timedelta(days=overlap_days)).isoformat()


def dedupe_by(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Keep the last occurrence per key (later pulls supersede earlier ones)."""
    by_key: dict[Any, dict[str, Any]] = {}
    keyless: list[dict[str, Any]] = []
    for record in records:
        value = record.get(key)
        if value is None:
            keyless.append(record)
        else:
            by_key[value] = record
    return list(by_key.values()) + keyless
