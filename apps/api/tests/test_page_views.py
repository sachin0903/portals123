"""Page-view paths and the profile's daily series."""

from datetime import UTC, date, datetime, tzinfo
from zoneinfo import ZoneInfo

from app.page_views import (
    activity_start,
    fill_activity_days,
    london_today,
    normalise_page_path,
    visible_page_views,
)


def test_path_stores_the_pathname_only() -> None:
    assert normalise_page_path("/team/abc?tab=security") == "/team/abc"
    assert normalise_page_path("/clients/one#notes") == "/clients/one"
    assert normalise_page_path("  /actions  ") == "/actions"
    assert normalise_page_path("https://evil.test/team") is None
    assert normalise_page_path("//evil.test/team") is None
    assert normalise_page_path("team") is None
    assert normalise_page_path("") is None


def test_daily_series_is_a_zero_filled_year() -> None:
    today = date(2026, 10, 2)
    start = activity_start(today)
    assert start.weekday() == 6  # Sunday

    filled = fill_activity_days(
        today,
        {date(2026, 10, 1): 4},
        {date(2026, 10, 2): 2},
    )
    assert filled[0]["date"] == start.isoformat()
    assert filled[-1]["date"] == "2026-10-02"
    assert 365 <= len(filled) <= 371
    by_date = {row["date"]: row for row in filled}
    assert len(by_date) == len(filled)
    assert by_date["2026-10-01"] == {"date": "2026-10-01", "actions": 4, "page_views": 0}
    assert by_date["2026-10-02"]["page_views"] == 2
    assert by_date["2026-01-01"] == {"date": "2026-01-01", "actions": 0, "page_views": 0}


def test_a_firm_profile_hides_record_paths_the_viewer_cannot_open() -> None:
    client = "AbCdEfGhIj12"
    rows = [
        {"id": 1, "path": f"/team/{client}"},
        {"id": 2, "path": "/templates"},
        {"id": 3, "path": f"/clients/{client}/compliance"},
        {"id": 4, "path": f"/documents/{client}"},
        {"id": 5, "path": f"/accounts/{client}"},
        {"id": 6, "path": f"/clients/{client}/documents/{client}"},
    ]
    withheld = visible_page_views(
        rows, see_all_clients=False, see_documents=False, see_screening=False
    )
    assert [row["path"] for row in withheld] == [f"/team/{client}", "/templates"]

    screened = visible_page_views(
        rows, see_all_clients=True, see_documents=False, see_screening=True
    )
    assert [row["id"] for row in screened] == [1, 2, 3, 5]

    opened = visible_page_views(rows, see_all_clients=True, see_documents=True, see_screening=True)
    assert opened == rows


def test_london_today_is_the_london_calendar_day(monkeypatch) -> None:
    # 23:30 UTC is still Friday in UTC and already Saturday in London during British Summer Time.
    fixed = datetime(2026, 10, 2, 23, 30, tzinfo=UTC)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr("app.page_views.datetime", Frozen)
    assert london_today() == date(2026, 10, 3)
    assert fixed.astimezone(ZoneInfo("Europe/London")).date() == date(2026, 10, 3)
