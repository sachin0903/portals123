"""Per-key windows. In-process: each replica counts on its own."""

from __future__ import annotations

import time
from collections.abc import Callable


class WindowLimiter:
    def __init__(
        self,
        *,
        limit: int,
        period: float = 60.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.limit = limit
        self.period = period
        self._clock = clock or time.monotonic
        self._hits: dict[str, list[float]] = {}

    def charge(self, key: str) -> float | None:
        """Record a hit and return its stamp, or None when the window is full."""
        now = self._clock()
        recent = [stamp for stamp in self._hits.get(key, []) if now - stamp < self.period]
        if len(recent) >= self.limit:
            self._hits[key] = recent
            return None
        recent.append(now)
        self._hits[key] = recent
        return now

    def allow(self, key: str) -> bool:
        return self.charge(key) is not None

    def release(self, key: str, stamp: float) -> None:
        """Drop one charge. A key that authenticated must not keep it."""
        recent = self._hits.get(key)
        if not recent:
            return
        try:
            recent.remove(stamp)
        except ValueError:
            return
        if recent:
            self._hits[key] = recent
        else:
            self._hits.pop(key, None)
