"""Next-fire calculation for interlace model schedules (app/cron.py).

Every expression asserted here is one the daemons actually return (see `GET /models`), so a
regression shows up as a wrong "next scheduled" on the Platforms overview.
"""

from datetime import datetime

import pytest

from app.cron import earliest_next_fire, next_fire, parse

# A Wednesday, mid-afternoon.
WED = datetime(2026, 7, 29, 15, 30)


class TestParse:
    def test_expands_a_plain_expression(self) -> None:
        parsed = parse("0 2 * * *")
        assert parsed is not None
        assert parsed[0] == {0}
        assert parsed[1] == {2}
        assert len(parsed[2]) == 31

    def test_expands_a_stepped_range(self) -> None:
        parsed = parse("0 6-18/6 * * 1-5")
        assert parsed is not None
        assert parsed[1] == {6, 12, 18}
        assert parsed[4] == {1, 2, 3, 4, 5}

    def test_expands_a_list(self) -> None:
        parsed = parse("0,30 9 * * *")
        assert parsed is not None
        assert parsed[0] == {0, 30}

    def test_sunday_matches_both_0_and_7(self) -> None:
        for spec in ("0 0 * * 0", "0 0 * * 7"):
            parsed = parse(spec)
            assert parsed is not None
            assert parsed[4] == {0, 7}

    @pytest.mark.parametrize(
        "expression",
        [
            "@daily",  # named shortcut
            "0 2 * *",  # too few fields
            "0 2 * * * *",  # too many
            "0 2 L * *",  # last-day-of-month
            "0 2 * * 1#2",  # nth weekday
            "60 2 * * *",  # minute out of range
            "0 24 * * *",  # hour out of range
            "0 2 * * 8",  # dow out of range
            "0 5-2 * * *",  # inverted range
            "0 */0 * * *",  # zero step
            "",
        ],
    )
    def test_rejects_what_it_does_not_support(self, expression: str) -> None:
        assert parse(expression) is None
        assert next_fire(expression, WED) is None


class TestNextFire:
    def test_nightly(self) -> None:
        assert next_fire("0 2 * * *", WED) == datetime(2026, 7, 30, 2, 0)

    def test_later_today_when_the_hour_is_still_ahead(self) -> None:
        assert next_fire("0 18 * * *", WED) == datetime(2026, 7, 29, 18, 0)

    def test_stepped_working_hours_weekdays(self) -> None:
        # 6-18/6 => 06:00, 12:00, 18:00. From Wednesday 15:30 the next is 18:00 the same day.
        assert next_fire("0 6-18/6 * * 1-5", WED) == datetime(2026, 7, 29, 18, 0)

    def test_skips_the_weekend_for_a_weekday_schedule(self) -> None:
        friday_evening = datetime(2026, 7, 31, 19, 0)
        assert next_fire("30 7 * * 1-5", friday_evening) == datetime(2026, 8, 3, 7, 30)

    def test_weekly_monday(self) -> None:
        assert next_fire("0 4 * * 1", WED) == datetime(2026, 8, 3, 4, 0)

    def test_is_strictly_after_the_given_moment(self) -> None:
        # Standing exactly on a fire time must yield the NEXT one, not the current minute.
        on_the_minute = datetime(2026, 7, 29, 2, 0)
        assert next_fire("0 2 * * *", on_the_minute) == datetime(2026, 7, 30, 2, 0)

    def test_ignores_seconds_on_the_input(self) -> None:
        assert next_fire("0 2 * * *", datetime(2026, 7, 29, 1, 59, 59)) == datetime(
            2026, 7, 29, 2, 0
        )

    def test_crosses_a_month_boundary(self) -> None:
        assert next_fire("0 3 1 * *", datetime(2026, 7, 29, 12, 0)) == datetime(2026, 8, 1, 3, 0)

    def test_crosses_a_year_boundary(self) -> None:
        assert next_fire("0 0 1 1 *", datetime(2026, 7, 29, 12, 0)) == datetime(2027, 1, 1, 0, 0)

    def test_restricting_both_day_fields_is_a_union_not_an_intersection(self) -> None:
        # POSIX quirk: `1 * 1` fires on the 1st of the month AND on every Monday.
        # 1 Aug 2026 is a Saturday, so the union hits it while an intersection never would.
        assert next_fire("0 0 1 * 1", datetime(2026, 7, 29, 12, 0)) == datetime(2026, 8, 1, 0, 0)

    def test_a_schedule_that_can_never_fire_returns_none(self) -> None:
        # 30 February.
        assert next_fire("0 0 30 2 *", WED) is None


class TestEarliestNextFire:
    def test_picks_the_soonest_across_schedules(self) -> None:
        soonest = earliest_next_fire(["0 4 * * 1", "0 2 * * *", "0 6-18/6 * * 1-5"], WED)
        assert soonest == datetime(2026, 7, 29, 18, 0)

    def test_skips_unparseable_and_never_firing_entries(self) -> None:
        assert earliest_next_fire(["@daily", "0 0 30 2 *", "0 2 * * *"], WED) == datetime(
            2026, 7, 30, 2, 0
        )

    def test_none_when_nothing_is_schedulable(self) -> None:
        # Views have no schedule; a project of only views has no next run.
        assert earliest_next_fire([], WED) is None
        assert earliest_next_fire(["", "@reboot"], WED) is None
