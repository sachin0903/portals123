"""Retry timing. A delay is a number, a list indexed by attempt, or a function of the attempt."""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from typing import Literal

type OnFailure = Literal["fail", "needs_attention", "continue"]
type Delay = float | Sequence[float] | Callable[[int], float]

ON_FAILURE_VALUES: frozenset[str] = frozenset({"fail", "needs_attention", "continue"})


def exponential_backoff(
    base: float = 2.0, factor: float = 2.0, max_delay: float = 300.0
) -> Callable[[int], float]:
    """``base * factor ** (attempt_no - 1)``, capped. ``attempt_no`` is 1 for the first retry."""

    def delay(attempt_no: int) -> float:
        n = max(1, attempt_no)
        return float(min(max_delay, base * (factor ** (n - 1))))

    return delay


def delay_for(
    delay: Delay,
    attempt_no: int,
    *,
    jitter: float = 0.0,
    rng: random.Random | None = None,
) -> float:
    """Seconds to wait before retry number ``attempt_no`` (1-based). Never negative."""
    if callable(delay):
        seconds = float(delay(attempt_no))
    elif isinstance(delay, int | float):
        seconds = float(delay)
    else:
        if not delay:
            seconds = 0.0
        else:
            index = min(max(attempt_no, 1), len(delay)) - 1
            seconds = float(delay[index])
    if jitter and seconds > 0:
        r = rng or random.Random()  # noqa: S311 - timing jitter, not a secret
        seconds *= 1.0 + r.uniform(-jitter, jitter)
    return max(0.0, seconds)
