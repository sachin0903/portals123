"""The sync side's persist half for `audit.api_log` — what a provider daemon said to a custodian.

`pkgs/api-log` is the contract and stays free of any database; `apps/api/app/api_log.py` is the
API's persist half. This is the third piece, and it exists because the sync does not run in the
API's process: each provider is its own interlace daemon, so it cannot reach that queue, and its
calls — the overwhelming majority of the traffic, thousands a night — were going unrecorded.

It writes as **`book_writer`** (0062, `BOOK_WRITER_DSN`), the narrow login that already reaches
the merged relations. Not `service_role`: giving a daemon the run of `auth.users`, `documents.*`
and `aml.*` so that it can record that it called Seccl would be a bad trade. `0073` grants it
INSERT and nothing else, matching the table's append-only posture.

Three properties, in the order they matter:

* **Recording never breaks a run.** A sync that failed because an audit INSERT deadlocked would
  be a far worse outcome than a missing row. Every failure here is swallowed and logged.
* **Recording never blocks a call.** The recorder contract is synchronous, so rows go onto a
  bounded queue drained by one background thread. A Postgres round trip inside each request
  would roughly double a run's wall time.
* **A lost row is loud.** A full queue drops the NEWEST row and says so, because a silently
  incomplete audit trail is worse than a known gap — its holes are invisible.
"""

from __future__ import annotations

import atexit
import json
import logging
import queue
import threading
from typing import Any

from api_log import ApiCall, Source, set_default_recorder

from book_sink.config import SinkConfig, from_env

logger = logging.getLogger("book_sink.api_log")

# No RETURNING, and that is a constraint rather than a preference: book_writer holds INSERT and
# NOT SELECT, and RETURNING needs SELECT on the columns it names. Adding one would fail every
# insert in this module -- silently, since the drain swallows. Pinned in apps/api's test_api_log.
_INSERT = """
INSERT INTO audit.api_log
    (tenant_id, user_id, source, provider, service, transport, method, url, operation,
     request, response, status_code, ok, error, duration_ms, entity_type, entity_id)
VALUES
    (%(tenant_id)s, %(user_id)s, %(source)s, %(provider)s, %(service)s, %(transport)s,
     %(method)s, %(url)s, %(operation)s, %(request)s, %(response)s, %(status_code)s, %(ok)s,
     %(error)s, %(duration_ms)s, %(entity_type)s, %(entity_id)s)
"""

# Same bound and the same reasoning as the API's drain: if Postgres is slow while a sync hammers
# a vendor, this is what stops the process growing without limit.
_MAX_QUEUE = 10_000
_BATCH = 100
_SENTINEL = object()


def _as_json(value: Any) -> str | None:
    """Serialise for a jsonb column, tolerating anything a vendor body can hold.

    `default=str` because a body can carry a Decimal or a datetime that json refuses, and losing
    the evidence to a TypeError is the wrong trade. A non-object body is wrapped: jsonb accepts
    a bare scalar, but querying one is awkward.
    """
    if value is None:
        return None
    try:
        if not isinstance(value, dict | list):
            value = {"value": value}
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return json.dumps({"unserialisable": repr(value)[:2000]})


def _params(call: ApiCall, source: Source, tenant_id: int | None) -> dict[str, Any]:
    return {
        # No user: a scheduled pull has no principal, which is exactly what a null says here.
        "tenant_id": tenant_id,
        "user_id": None,
        "source": source,
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
        "entity_type": call.entity_type,
        "entity_id": call.entity_id,
    }


class SyncApiLog:
    """A queue and one drain thread. One per process, installed by `install()`."""

    def __init__(self, dsn: str, *, source: Source = "sync") -> None:
        self._dsn = dsn
        self._source: Source = source
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=_MAX_QUEUE)
        self._thread: threading.Thread | None = None
        self._dropped = 0

    # --- the Recorder contract ------------------------------------------------

    def __call__(self, call: ApiCall, /) -> None:
        """Positional-only, so this instance satisfies `api_log.Recorder` directly."""
        try:
            self._queue.put_nowait(call)
        except queue.Full:
            self._dropped += 1
            # Once per hundred, so a sustained overflow does not become the log's whole content.
            if self._dropped % 100 == 1:
                logger.error(
                    "api_log queue full; dropped %d row(s). The table is incomplete for "
                    "this period.",
                    self._dropped,
                )

    # --- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        # A daemon thread: a run that has finished its work must not be held open by an audit
        # drain, and `stop()` (registered with atexit) is what flushes the tail cleanly.
        self._thread = threading.Thread(target=self._drain, name="api-log-sync", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Flush what is queued, then stop. Bounded: a shutdown hanging on Postgres is worse
        than losing the tail, and the tail's loss is already logged."""
        if self._thread is None:
            return
        self._queue.put(_SENTINEL)
        self._thread.join(timeout=timeout)
        self._thread = None

    # --- the drain ------------------------------------------------------------

    def _drain(self) -> None:
        while True:
            batch, stopping = self._take()
            if batch:
                try:
                    self._write(batch)
                except Exception:
                    # Deliberately broad and deliberately non-fatal, and HERE rather than inside
                    # `_write`: the guarantee is that the LOOP survives, which must not depend on
                    # every present and future write path remembering to catch for itself. A
                    # thread that dies here takes the rest of the run's audit trail with it and
                    # reports nothing, since nobody is waiting on it.
                    logger.warning("could not write %d api_log row(s)", len(batch), exc_info=True)
            if stopping:
                return

    def _take(self) -> tuple[list[ApiCall], bool]:
        """One blocking read, then whatever else is already waiting.

        Batched because a sync run makes thousands of calls and one INSERT per row would make
        the drain the slowest thing in the process; blocking on the first keeps an idle daemon
        from spinning.
        """
        batch: list[ApiCall] = []
        item = self._queue.get()
        if item is _SENTINEL:
            return batch, True
        batch.append(item)
        stopping = False
        while len(batch) < _BATCH:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is _SENTINEL:
                stopping = True
                break
            batch.append(item)
        return batch, stopping

    def _write(self, batch: list[ApiCall]) -> None:
        """One connection per batch. Free to raise -- `_drain` owns the guard."""
        import psycopg

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.executemany(_INSERT, [_params(c, self._source, None) for c in batch])


def install_api_log(
    config: SinkConfig | None = None, *, source: Source = "sync"
) -> SyncApiLog | None:
    """Start the drain and make it this process's default recorder. Returns None when unconfigured.

    Called once at daemon startup. Every vendor client falls back to the process default when it
    is handed no recorder of its own, so this one call is what records a whole sync run — rather
    than a `recorder=` argument threaded through the thirty-odd places the models construct a
    client, which is the wiring that never got done and left the table empty.

    None when there is no book DSN, which is a real state and not a failure: `interlace plan` on
    a laptop has no database, exactly as `tenants.enabled` tolerates.
    """
    settings = config or from_env()
    if not settings.book_dsn:
        logger.warning("book_sink: BOOK_WRITER_DSN unset, so outgoing calls are not recorded")
        return None
    sink = SyncApiLog(settings.book_dsn, source=source)
    sink.start()
    set_default_recorder(sink)
    atexit.register(sink.stop)
    return sink
