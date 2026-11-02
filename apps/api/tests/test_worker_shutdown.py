"""Ctrl+C must stop the worker, not print a CancelledError traceback.

asyncio's own signal handler cancels the main task. Left alone, that cancellation leaves
``run_forever`` and ``asyncio.run`` re-raises it as ``KeyboardInterrupt``, so a normal
shutdown looks like a crash. The worker clears the cancellation, drains, and returns.
"""

from __future__ import annotations

import asyncio

import pytest

from app import worker


@pytest.mark.asyncio
async def test_ctrl_c_drains_the_worker_and_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    disposed = asyncio.Event()

    class Engine:
        async def dispose(self) -> None:
            await asyncio.sleep(0)
            disposed.set()

    monkeypatch.setattr(worker, "create_async_engine", lambda *_a, **_k: Engine())

    entered = asyncio.Event()

    async def idle(*_args: object, **_kwargs: object) -> None:
        entered.set()
        await asyncio.Event().wait()

    for name in (
        "_render_loop",
        "_tenant_data_loop",
        "_outbox_loop",
        "_notifier_loop",
        "_comms_poll_loop",
        "_workos_sync_loop",
        "_complycube_events_loop",
        "_firm_webhooks_loop",
        "_automations_loop",
        "_timers_loop",
        "_sei_receipts_loop",
        "_notifications_loop",
        "_email_notifications_loop",
        "_render_service_task",
    ):
        monkeypatch.setattr(worker, name, idle)

    task = asyncio.create_task(worker.run_forever())
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    await task
    assert disposed.is_set()


def test_main_treats_a_second_ctrl_c_as_a_clean_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(coro: object, **_kwargs: object) -> None:
        close = getattr(coro, "close", None)
        if close is not None:
            close()
        raise KeyboardInterrupt

    monkeypatch.setattr(worker.asyncio, "run", interrupt)
    assert worker.main([]) == 0
