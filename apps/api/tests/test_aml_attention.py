"""What needs a person in the AML console: a client row's reasons, steps and age.

The SQL that fills a row is exercised against Postgres in ``test_aml_router``; these pin what a
row means once it is read.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.complycube import attention
from app.complycube.attention import REASONS, STEPS, age_of, reasons_of, steps_of

UTC = dt.UTC
#: A Monday.
MONDAY = dt.date(2026, 10, 12)


def _row(**values: Any) -> dict[str, Any]:
    """A client row with every reason at zero, then ``values``."""
    row: dict[str, Any] = {"client_id": 1}
    for code in REASONS:
        row[f"{code}_count"] = 0
        row[f"{code}_since"] = None
        row[f"{code}_check"] = None
    row.update(values)
    return row


def _states(row: dict[str, Any]) -> dict[str, str]:
    return {str(step["key"]): str(step["state"]) for step in steps_of(row)}


def test_monday_is_a_monday() -> None:
    assert MONDAY.weekday() == 0


@pytest.mark.parametrize(
    ("start", "end", "days"),
    [
        (MONDAY, MONDAY, 0),
        (MONDAY, MONDAY - dt.timedelta(days=1), 0),
        (MONDAY, MONDAY + dt.timedelta(days=1), 1),
        # Friday to Saturday and Sunday: no working day passes.
        (MONDAY + dt.timedelta(days=4), MONDAY + dt.timedelta(days=6), 0),
        # Friday to Monday: one.
        (MONDAY + dt.timedelta(days=4), MONDAY + dt.timedelta(days=7), 1),
        # Saturday to Monday: one.
        (MONDAY + dt.timedelta(days=5), MONDAY + dt.timedelta(days=7), 1),
        (MONDAY, MONDAY + dt.timedelta(days=7), 5),
        (MONDAY, MONDAY + dt.timedelta(days=14), 10),
        (MONDAY + dt.timedelta(days=3), MONDAY + dt.timedelta(days=15), 8),
    ],
)
def test_working_days_between_counts_monday_to_friday(
    start: dt.date, end: dt.date, days: int
) -> None:
    assert attention.working_days_between(start, end) == days


def _at(day: dt.date, hour: int = 9) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


@pytest.mark.parametrize(
    ("after", "days", "band"),
    [
        (0, 0, "fresh"),
        (2, 2, "fresh"),
        (3, 3, "ageing"),
        (4, 4, "ageing"),
        # The next Monday is five working days on: still ageing.
        (7, 5, "ageing"),
        (8, 6, "overdue"),
    ],
)
def test_age_is_working_days_in_bands(after: int, days: int, band: str) -> None:
    since = _at(MONDAY)
    now = _at(MONDAY + dt.timedelta(days=after), hour=17)
    assert age_of(since, now) == {"working_days": days, "band": band}


def test_age_counts_london_dates() -> None:
    # 23:30 UTC on Sunday is 00:30 on Monday in London (BST): no working day has passed by Monday
    # noon, though a UTC date would count one.
    since = dt.datetime(2026, 10, 11, 23, 30, tzinfo=UTC)
    assert age_of(since, _at(MONDAY, hour=12)) == {"working_days": 0, "band": "fresh"}


def test_nothing_waiting_has_no_age() -> None:
    assert age_of(None, _at(MONDAY)) is None


def test_every_reason_opens_a_step() -> None:
    assert {reason.step for reason in REASONS.values()} <= set(STEPS)


@pytest.mark.parametrize("code", list(REASONS))
def test_a_reason_opens_its_step_and_only_its_step(code: str) -> None:
    row = _row(**{f"{code}_count": 2, f"{code}_since": _at(MONDAY), f"{code}_check": "chk_1"})
    open_steps = {key for key, state in _states(row).items() if state == "open"}
    assert open_steps == {REASONS[code].step}
    (step,) = [s for s in steps_of(row) if s["state"] == "open"]
    assert step["count"] == 2
    assert step["hint"] == REASONS[code].label


@pytest.mark.parametrize(
    "row",
    [
        _row(),
        _row(identity_status="clear", aml_status="clear", aml_monitoring=True, risk="low"),
        _row(identity_status="pending", aml_status="pending"),
        _row(identity_status="refer", identity_decided=True),
        _row(awaiting=True, screen_queued=True, client_id=None),
        _row(aml_status="clear", aml_monitoring=False),
    ],
)
def test_no_reason_no_open_step(row: dict[str, Any]) -> None:
    """The rule the queue rests on: a client needs attention exactly when a step is open."""
    assert reasons_of(row) == []
    assert "open" not in _states(row).values()


def test_reasons_carry_count_wait_and_check_in_order() -> None:
    since = _at(MONDAY)
    row = _row(
        open_hits_count=3,
        open_hits_since=since,
        open_hits_check="chk_hits",
        identity_refer_count=1,
        identity_refer_since=since - dt.timedelta(days=1),
        identity_refer_check="chk_id",
    )
    assert reasons_of(row) == [
        {
            "code": "identity_refer",
            "label": "Identity to decide",
            "step": "identity",
            "count": 1,
            "since": since - dt.timedelta(days=1),
            "check_uid": "chk_id",
        },
        {
            "code": "open_hits",
            "label": "Hits to review",
            "step": "hits",
            "count": 3,
            "since": since,
            "check_uid": "chk_hits",
        },
    ]


def test_two_reasons_on_one_step_add_up() -> None:
    row = _row(screen_due_count=1, screen_stale_count=1)
    (screened,) = [s for s in steps_of(row) if s["key"] == "screened"]
    assert screened["state"] == "open"
    assert screened["count"] == 2
    assert screened["hint"] == "Screen to run, Screened details changed"


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        # A portal client due a first screen, with no compliance client yet.
        (
            _row(awaiting=True, screen_queued=False, screen_due_count=1, client_id=None),
            {
                "identity": "none",
                "screened": "open",
                "monitoring": "none",
                "hits": "none",
                "risk": "none",
            },
        ),
        # The same with the screen queued: it starts by itself.
        (
            _row(awaiting=True, screen_queued=True, client_id=None),
            {
                "identity": "none",
                "screened": "waiting",
                "monitoring": "none",
                "hits": "none",
                "risk": "none",
            },
        ),
        # Everything done.
        (
            _row(
                identity_status="clear",
                aml_status="clear",
                aml_monitoring=True,
                risk="low",
            ),
            {
                "identity": "done",
                "screened": "done",
                "monitoring": "done",
                "hits": "done",
                "risk": "done",
            },
        ),
        # Monitoring off does not block; ComplyCube has not rated yet.
        (
            _row(identity_status="clear", aml_status="clear", aml_monitoring=False, risk=None),
            {
                "identity": "done",
                "screened": "done",
                "monitoring": "advisory",
                "hits": "done",
                "risk": "waiting",
            },
        ),
        # `not_set` is not a rating.
        (
            _row(identity_status="clear", aml_status="clear", aml_monitoring=True, risk="not_set"),
            {
                "identity": "done",
                "screened": "done",
                "monitoring": "done",
                "hits": "done",
                "risk": "waiting",
            },
        ),
        # Both checks in flight.
        (
            _row(identity_status="pending", aml_status="pending"),
            {
                "identity": "waiting",
                "screened": "waiting",
                "monitoring": "none",
                "hits": "none",
                "risk": "waiting",
            },
        ),
        # New photos asked for: the identity refer stays refer, decided, and waits on the client.
        (
            _row(identity_status="refer", identity_decided=True),
            {
                "identity": "waiting",
                "screened": "none",
                "monitoring": "none",
                "hits": "none",
                "risk": "waiting",
            },
        ),
        # A refer with open hits, monitoring on: only the hits are open.
        (
            _row(aml_status="refer", aml_monitoring=True, open_hits_count=2, risk="high"),
            {
                "identity": "none",
                "screened": "done",
                "monitoring": "done",
                "hits": "open",
                "risk": "done",
            },
        ),
        # A screen due again reads as the new screen, not the old one.
        (
            _row(
                awaiting=True,
                screen_queued=False,
                screen_due_count=1,
                aml_status="clear",
                aml_monitoring=True,
            ),
            {
                "identity": "none",
                "screened": "open",
                "monitoring": "none",
                "hits": "none",
                "risk": "waiting",
            },
        ),
    ],
)
def test_steps_read_the_latest_checks(row: dict[str, Any], expected: dict[str, str]) -> None:
    assert _states(row) == expected


def test_steps_are_in_order_with_labels() -> None:
    steps = steps_of(_row())
    assert [(s["key"], s["label"]) for s in steps] == list(STEPS.items())


def test_a_rating_names_its_level() -> None:
    (risk,) = [s for s in steps_of(_row(risk="medium")) if s["key"] == "risk"]
    assert risk["hint"] == "medium"
