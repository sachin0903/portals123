"""Persist outgoing provider calls to `audit.api_log`.

The vendor clients emit; this is the persist half (`pkgs/api-log` explains the split, and
`docs/provider-routing.md` §5 explains why). A client is handed a recorder built here and calls
it once per request; nothing in `pkgs/*` knows a database exists.

Two properties matter more than throughput:

* **Recording must never break the caller.** An adviser's transfer failing because an audit
  INSERT deadlocked would be a worse outcome than a missing row. `api_log.record` already
  swallows recorder exceptions; this module also refuses to raise on its own account.
* **Recording must not block the call it describes.** The clients' hook is synchronous by
  contract (they cannot await inside it), so rows are queued and drained by a background task.
  A sync run makes thousands of calls; a round trip to Postgres inside each one would roughly
  double its wall time for evidence nobody reads synchronously.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from api_log import ApiCall, Recorder, Source
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import system_transaction

logger = logging.getLogger(__name__)

_INSERT = text(
    """
    INSERT INTO audit.api_log
        (tenant_id, user_id, source, provider, service, transport, method, url, operation,
         request, response, status_code, ok, error, duration_ms, entity_type, entity_id)
    VALUES
        (:tenant_id, :user_id, :source, :provider, :service, :transport, :method, :url,
         :operation, cast(:request AS jsonb), cast(:response AS jsonb), :status_code, :ok,
         :error, :duration_ms, :entity_type, :entity_id)
    """
)

# A queue rather than an unbounded list: if the drain falls behind (Postgres slow, a sync
# hammering an API), the bound is what stops the process growing without limit. Full means the
# oldest evidence is preserved and the newest dropped WITH A LOG LINE -- silently losing rows
# would make the table's completeness unknowable, which is worse than a known gap.
_MAX_QUEUE = 10_000


@dataclass(slots=True)
class _Envelope:
    """A call plus the request context it happened in, which the client cannot know."""

    call: ApiCall
    tenant_id: int | None
    user_id: str | None
    source: Source = "app"
    entity_type: str | None = None
    entity_id: int | None = None
    """What the call was about, when the CALLER knows it and the vendor client cannot.

    The delivery queue is the case this exists for: an attempt made for
    `platform.provider_instructions` row 42 stamps `('provider_instruction', 42)`, which is the
    whole join between one intent and the several attempts made at it
    (docs/provider-routing.md section 6a). Overrides whatever the client set, because a client
    that names its own subject is describing the vendor's world, not ours.
    """


class ApiLog:
    """Owns the queue and its drain task. One per process, held on app state."""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
    ) -> None:
        """`session_factory` defaults to `app.db.system_transaction`.

        Injectable because the default binds to the module-level engine built from settings at
        import time, which a test cannot point at its own database and a worker with a different
        DSN cannot reuse. Passing it in also makes the queue-and-drain testable without
        monkeypatching another module's internals.
        """
        self._session_factory = session_factory or system_transaction
        self._queue: asyncio.Queue[_Envelope] = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._task: asyncio.Task[None] | None = None
        self._dropped = 0

    def recorder(
        self,
        *,
        tenant_id: int | None = None,
        user_id: str | None = None,
        source: Source = "app",
        entity_type: str | None = None,
        entity_id: int | None = None,
    ) -> Recorder:
        """A recorder bound to one request's context.

        Built per call site rather than read from a context variable: a sync worker has no
        request context at all, and a cross-tenant admin action's tenant is the one being acted
        ON, not the actor's. Making the caller state it keeps both honest.

        `source` sits here rather than on `ApiCall` for the same reason `tenant_id` does: the
        vendor client cannot know it. Everything this process records is `app` -- the sync
        daemons write their own rows through `book_sink.api_log` -- so the default is the answer
        at every call site in the API, including the one the lifespan installs process-wide.
        """

        def record(call: ApiCall) -> None:
            envelope = _Envelope(
                call=call,
                tenant_id=tenant_id,
                user_id=user_id,
                source=source,
                entity_type=entity_type,
                entity_id=entity_id,
            )
            try:
                self._queue.put_nowait(envelope)
            except asyncio.QueueFull:
                self._dropped += 1
                # Once per 100, so a sustained overflow does not become the log's whole content.
                if self._dropped % 100 == 1:
                    logger.error(
                        "api_log queue full; dropped %d row(s). "
                        "The table is incomplete for this period.",
                        self._dropped,
                    )

        return record

    async def _drain(self) -> None:
        while True:
            envelope = await self._queue.get()
            try:
                await self._write(envelope)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Deliberately broad and deliberately non-fatal: the drain task must survive a
                # bad row, or one unwritable call would silence the audit trail permanently.
                logger.exception(
                    "could not write an api_log row (provider=%s)", envelope.call.provider
                )
            finally:
                self._queue.task_done()

    async def _write(self, envelope: _Envelope) -> None:
        # service_role, no request principal -- the same primitive the WorkOS startup sync uses.
        # The INSERT policy is WITH CHECK (true) precisely so this cannot be blocked by tenant
        # scoping, but the drain runs outside any request and so has no claims to be scoped by.
        async with self._session_factory() as session:
            await _insert(session, envelope, envelope.call)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._drain(), name="api-calls-audit")

    async def stop(self) -> None:
        """Drain what is queued, then stop. Called from the app's lifespan on shutdown.

        Bounded: a shutdown that hangs waiting on Postgres is worse than losing the tail of the
        queue, and the tail is already logged as dropped if it is lost.
        """
        if self._task is None:
            return
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(5):
                await self._queue.join()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None


# The process's ApiLog, installed by whichever entry point owns the lifespan: the API's
# (`app/main.py`) or the worker's (`app/worker.py`). BOTH, because they are different processes:
# the worker never constructed one, so every vendor call it made was invisible.
#
# A module-level slot rather than `fastapi_app.state`, which is where it used to live. Reaching it
# meant `from app.main import app` INSIDE a function -- a service locator that makes any caller
# construct the entire FastAPI application (every router, every dependency) to read one attribute,
# and which in the worker returned None because nothing there had set it.
_current: ApiLog | None = None


def install(api_log: ApiLog | None) -> None:
    """Install (or clear) the process's ApiLog. Idempotent; the last call wins."""
    global _current  # noqa: PLW0603 - one process-wide slot, mirroring api_log.set_default_recorder
    _current = api_log


def current() -> ApiLog | None:
    """The process's ApiLog, or None where nothing installed one.

    None is legitimate: a test, a script or a management command has no drain running, and a
    missing audit row must never be the reason a vendor call does not happen.
    """
    return _current


async def _insert(session: AsyncSession, envelope: _Envelope, call: ApiCall) -> None:
    await session.execute(
        _INSERT,
        {
            "tenant_id": envelope.tenant_id,
            "user_id": envelope.user_id,
            "source": envelope.source,
            "provider": call.provider,
            "service": call.service,
            "transport": call.transport,
            "method": call.method,
            "url": call.url,
            "operation": call.operation,
            "request": _as_json(call.request),
            "response": _as_json(call.response),
            "status_code": call.status_code,
            "ok": call.ok,
            "error": call.error,
            "duration_ms": call.duration_ms,
            "entity_type": envelope.entity_type or call.entity_type,
            "entity_id": envelope.entity_id if envelope.entity_id is not None else call.entity_id,
        },
    )


def _as_json(value: object) -> str | None:
    """Serialise for a jsonb column, tolerating anything.

    A body can hold a Decimal or a datetime that json cannot encode; `default=str` keeps the row
    writable rather than losing the evidence to a TypeError. A non-object body (the CTC engine
    answers XML) is wrapped, because jsonb accepts a scalar but querying one is awkward.
    """
    if value is None:
        return None
    try:
        if not isinstance(value, dict | list):
            value = {"value": value}
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return json.dumps({"unserialisable": repr(value)[:2000]})


@contextlib.asynccontextmanager
async def lifespan(audit: ApiLog) -> AsyncIterator[None]:
    await audit.start()
    try:
        yield
    finally:
        await audit.stop()
