"""The background worker: claim runnable runs, advance them, sweep crashed ones.

A run is claimable when its ``scheduled_at`` has passed and no live lease holds it. The worker
runs ``concurrency`` slots, each claiming one run at a time through the same ``FOR UPDATE SKIP
LOCKED`` path ``Engine.advance`` uses, so several workers (or a worker beside inline door
advances) never double-claim. A ``LISTEN`` wakes idle slots; ``poll_seconds`` is the fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from baton.engine import Engine


class Worker:
    def __init__(
        self,
        engine: Engine,
        *,
        concurrency: int = 4,
        poll_seconds: float = 3.0,
        budget_seconds: float = 55.0,
        sweep_seconds: float = 15.0,
        listen: bool = True,
        listen_dsn: str | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.engine = engine
        self.concurrency = concurrency
        self.poll_seconds = poll_seconds
        self.budget_seconds = budget_seconds
        self.sweep_seconds = sweep_seconds
        self.listen = listen
        self.listen_dsn = listen_dsn
        self.worker_id = worker_id or engine.worker_id
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_once(self) -> bool:
        """Claim and advance one runnable run. True if one was claimed.

        One atomic claim (``FOR UPDATE SKIP LOCKED``) rather than read-id-then-advance-that-id, so
        several slots spread across the due runs instead of all contending on the same top one."""
        result = await self.engine.claim_and_advance(
            budget_seconds=self.budget_seconds, worker_id=self.worker_id
        )
        if result is None:
            return False
        return result.outcome not in ("noop", "busy")

    async def sweep_once(self) -> None:
        async with self.engine.store.unit() as u:
            await self.engine.store.sweep(
                u, now=self.engine.now(), registry_idempotent=self._idempotent
            )

    def _idempotent(self, action_key: str) -> bool:
        defn = self.engine.registry.get(action_key)
        return bool(defn and defn.options.idempotent)

    async def run_forever(self) -> None:
        sweeper = asyncio.create_task(self._sweep_loop())
        slots = [asyncio.create_task(self._slot()) for _ in range(self.concurrency)]
        try:
            await self._stop.wait()
        finally:
            for task in (sweeper, *slots):
                task.cancel()
            for task in (sweeper, *slots):
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _slot(self) -> None:
        while not self._stop.is_set():
            try:
                worked = await self.run_once()
            except Exception:  # noqa: BLE001 - a slot must outlive one run's failure
                worked = False
            if not worked:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)

    async def _sweep_loop(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(Exception):
                await self.sweep_once()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.sweep_seconds)
