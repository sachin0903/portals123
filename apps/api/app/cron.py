"""Next-fire calculation for the 5-field cron expressions interlace model schedules use.

The Platforms overview shows "next scheduled" beside "last run", which needs an actual timestamp,
not the raw expression. `croniter` would do it but is not a dependency, and the expressions in
play are a small, well-defined subset:

    0 2 * * *          nightly
    0 6-18/6 * * 1-5   every six hours through the working day, weekdays
    0 4 * * 1          Mondays
    30 7 * * 1-5       weekday mornings

So: minute, hour, day-of-month, month, day-of-week, each accepting `*`, a number, a
comma-separated list, an `a-b` range, and a `*/n` or `a-b/n` step. Anything outside that
(`@daily`, `L`, `#`, `?`) returns None rather than guessing — the caller renders "—".

Semantics that matter: when BOTH day-of-month and day-of-week are restricted, cron fires when
EITHER matches (a documented POSIX quirk, not an oversight). Day-of-week accepts 0 and 7 for
Sunday.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# Field bounds, in cron order.
_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
# A schedule that hasn't fired within this window never will (`0 0 30 2 *`). Bounded by DATE, not
# by iteration count: month-skipping otherwise walks past datetime.max and raises OverflowError.
_SEARCH_DAYS = 400


def _parse_field(spec: str, low: int, high: int) -> set[int] | None:
    """Expand one cron field to the set of values it matches. None if unparseable."""
    values: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            return None
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                return None
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif "-" in part.lstrip("-"):
            start_text, _, end_text = part.partition("-")
            if not (start_text.isdigit() and end_text.isdigit()):
                return None
            start, end = int(start_text), int(end_text)
        elif part.isdigit():
            start = end = int(part)
        else:
            return None
        if start < low or end > high or start > end:
            return None
        values.update(range(start, end + 1, step))
    return values or None


def parse(expression: str) -> tuple[set[int], ...] | None:
    """The five expanded field sets, or None when the expression isn't the supported subset."""
    fields = expression.split()
    if len(fields) != 5:
        return None
    parsed = []
    for spec, (low, high) in zip(fields, _BOUNDS, strict=True):
        values = _parse_field(spec, low, high)
        if values is None:
            return None
        parsed.append(values)
    # Normalise Sunday: cron accepts both 0 and 7, so match either once expanded.
    if 0 in parsed[4] or 7 in parsed[4]:
        parsed[4] |= {0, 7}
    return tuple(parsed)


def next_fire(expression: str, after: datetime) -> datetime | None:
    """The first minute strictly after `after` that the expression matches.

    Returns None for an unsupported expression or one that can never fire (e.g. `0 0 30 2 *`).
    Seconds/microseconds are dropped: cron granularity is the minute.
    """
    parsed = parse(expression)
    if parsed is None:
        return None
    minutes, hours, doms, months, dows = parsed

    moment = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    # Restricting both day fields makes them a union, not an intersection (POSIX).
    day_union = len(doms) < 31 and len(dows) < 8

    try:
        limit = moment + timedelta(days=_SEARCH_DAYS)
    except OverflowError:  # pragma: no cover - only near datetime.max
        return None

    while moment <= limit:
        if moment.month not in months:
            # Jump to the first minute of the next month rather than stepping a minute at a time.
            moment = (moment.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0)
            continue
        # cron's day-of-week is 0=Sunday; Python's weekday() is 0=Monday.
        dow = (moment.weekday() + 1) % 7
        day_ok = (
            (moment.day in doms or dow in dows)
            if day_union
            else (moment.day in doms and dow in dows)
        )
        if not day_ok:
            moment = (moment + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if moment.hour not in hours:
            moment += timedelta(hours=1)
            moment = moment.replace(minute=0)
            continue
        if moment.minute not in minutes:
            moment += timedelta(minutes=1)
            continue
        return moment
    return None


def earliest_next_fire(expressions: list[str], after: datetime) -> datetime | None:
    """The soonest next fire across several schedules — "when does this pipeline next run".
    Unsupported or never-firing expressions are skipped, not fatal."""
    candidates = [f for f in (next_fire(e, after) for e in expressions if e) if f is not None]
    return min(candidates) if candidates else None
