"""The emit side of `audit.api_log` — the contract between a vendor HTTP client and whatever
wants to record its calls.

NOT `audit.log`, which is a different table recording what a USER did to our own data. This
one is outbound: what WE said to a third party, and what it said back.

Three pieces, and this is only the first:

  * **this package** — the contract. `ApiCall`, `Recorder`, `record`, `redact`. No database.
  * `app/api_log.py` — the persist half, in the API: a queue and a background drain.
  * `audit.api_log` — the table (migration 0047).

Deliberately tiny, and deliberately free of any database, HTTP or settings dependency. The
vendor clients (`pkgs/morgan-lloyd`, `pkgs/seccl`, `pkgs/sei`) are SDKs — giving them a
Postgres dependency so they could write their own audit rows would make them unusable from a
script, a test or a sync worker that has no session, and would couple three packages to one
application's schema.

So the clients emit; they do not persist. Each accepts an optional `recorder` and calls it once
per request at its single HTTP choke point. The API supplies one that writes `audit.api_log`
(`app/api_log.py`); a sync service can supply its own, or none at all.

See `docs/provider-routing.md`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

__all__ = [
    "ApiCall",
    "body_of",
    "Recorder",
    "Source",
    "clear_default_recorder",
    "default_recorder",
    "operation_of",
    "path_segments",
    "record",
    "recording_into",
    "redact",
    "set_default_recorder",
]

Source = Literal["app", "sync"]
"""Which half of the platform made the call.

`app` is a request path -- an adviser created a client, an operator fired an action. `sync` is a
provider daemon's nightly pull. The distinction is the first question anyone asks of this log
("was that us or the cron?"), and it cannot be derived after the fact from the operation alone:
both halves call `get_all_contacts`.

It is a property of the PROCESS, not of the call, so it is stated once where persistence is
configured rather than at every call site -- the same reasoning that keeps `tenant_id` and
`user_id` off `ApiCall`. Kept in lockstep with the CHECK on `audit.api_log.source`."""

# Anything whose name matches is replaced before the call leaves this module. Matched on a
# lower-cased substring of the key with separators REMOVED, so every spelling of the same
# header collapses to one hint: `X-Api-Key`, `api_key`, `api-key` and `apikey` all become
# `apikey`. Substring matching alone was not enough — it caught `apikey` and `api_key` but let
# the hyphenated `X-Api-Key` through, which is the commonest way the header is actually
# written, and this table is append-only.
_SECRET_HINTS = ("authorization", "auth", "token", "secret", "password", "apikey", "credential")
_REDACTED = "[redacted]"


def _is_secret(key: Any) -> bool:
    normalised = str(key).lower().replace("-", "").replace("_", "").replace(" ", "")
    return any(hint in normalised for hint in _SECRET_HINTS)


def redact(value: Any, *, depth: int = 0) -> Any:
    """Strip credentials from a header or body, recursively.

    Applied by `record` rather than by callers: a token reaching an audit table because one of
    a dozen call sites forgot is not a hypothetical, and the table is append-only, so it could
    not be cleaned up afterwards.

    Bodies themselves are KEPT. A redacted audit trail answers no questions, and the point of
    the table is being able to say what we actually sent.
    """
    if depth > 6:
        return value
    if isinstance(value, dict):
        return {
            key: (_REDACTED if _is_secret(key) else redact(inner, depth=depth + 1))
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [redact(item, depth=depth + 1) for item in value[:50]]
    return value


@dataclass(slots=True)
class ApiCall:
    """One outgoing call, as the recorder receives it."""

    provider: str
    """The counterparty a firm would name: 'seccl', 'sei', 'morgan_lloyd'."""
    service: str | None = None
    """A sub-system reached through it — CTC is provider 'morgan_lloyd', service 'ctc'."""
    transport: str = "http"
    method: str | None = None
    url: str | None = None
    operation: str | None = None
    """The domain verb, when the caller knows it ('create_transfer'). Makes the log read as a
    business trail rather than only as HTTP."""
    request: Any = None
    response: Any = None
    status_code: int | None = None
    ok: bool = False
    """Whether the call achieved its purpose — NOT merely `status < 400`. Morgan Lloyd answers
    200 with responseKey=1 on failure and CTC answers 200 with an HTML error page, so the
    client decides this, not the transport."""
    error: str | None = None
    duration_ms: int | None = None
    entity_type: str | None = None
    entity_id: int | None = None
    keep_response: bool = False
    """Set when the response body carries something that exists NOWHERE ELSE if it is discarded.

    A successful call's body is normally dropped (see `keeps_response`). The exception is a call
    that MINTED an identifier: `cam_createcontact` answers with a new `contactid` and
    `cam_createscheme` with a `schemeReference`, and once the `platform.links` row is written
    those live in one place. If the link write then fails, a discarded body makes the id
    unrecoverable and the retry creates the record a second time -- the duplication
    `sync-to-morgan-lloyd` was rebuilt to stop.

    An explicit flag rather than an inference. Keying off `entity_type` was tried and is wrong:
    it means "what this call was ABOUT", which a read sets too, and the clients that most need
    the exception do not set it at all. Keying off the operation NAME would be string-matching
    `cam_create*` across three vendors' naming conventions.
    """
    extra: dict[str, Any] = field(default_factory=dict)

    def keeps_response(self) -> bool:
        """Whether this call's response body is worth the row it costs.

        A FAILURE's body is the whole point of the log: the vendor's message is what someone
        reconstructs an incident from, and Morgan Lloyd's is often the only statement of what
        was wrong with a payload. A SUCCESS's body is usually an echo of what we sent, and at
        sync volumes -- thousands of calls a night, some of them 457-column contacts -- keeping
        them all costs a great deal of storage to answer nothing.

        One exception, and it is not hypothetical: a call that minted an identifier keeps its
        body whatever the outcome. See `keep_response`, which the client sets.
        """
        return not self.ok or self.keep_response

    def redacted(self) -> ApiCall:
        """A copy safe to persist: credentials stripped, and a bare success's response dropped."""
        return ApiCall(
            provider=self.provider,
            service=self.service,
            transport=self.transport,
            method=self.method,
            url=self.url,
            operation=self.operation,
            request=redact(self.request),
            response=redact(self.response) if self.keeps_response() else None,
            status_code=self.status_code,
            ok=self.ok,
            error=self.error,
            duration_ms=self.duration_ms,
            entity_type=self.entity_type,
            entity_id=self.entity_id,
            keep_response=self.keep_response,
            extra=redact(self.extra),
        )


class Recorder(Protocol):
    """Anything that can persist a call. Synchronous and fire-and-forget by contract.

    The parameter is positional-only. A named one would make the argument's NAME part of the
    contract, so a recorder had to spell it `call` — which rules out a bound method, a
    ``partial``, a queue's ``put`` and anything else with a differently-named first argument,
    for no benefit. `record` invokes it positionally regardless.
    """

    def __call__(self, call: ApiCall, /) -> None: ...


# The process-wide recorder, installed once by whatever owns persistence: the API's lifespan
# (`source='app'`), a provider daemon's startup (`source='sync'`).
#
# WHY A DEFAULT EXISTS AT ALL, rather than an explicit `recorder=` at every construction. There
# are around thirty-five client construction sites -- five in the API, thirty across the three
# sync services' interlace models -- and threading the argument through each is not merely
# tedious: it is silently incomplete the moment someone adds the thirty-sixth, and a partially
# recorded audit trail is worse than an absent one because its gaps are invisible. This is how
# `audit.api_log` came to be a finished table, drain, endpoint set and admin page with no
# producer at all: every client accepted a recorder and not one caller passed one.
#
# Whether a process records its outgoing calls is a property of the DEPLOYMENT, not of the call
# site, so it is configured once where the deployment is. An explicit `recorder=` still wins,
# because the sites that hold a request's tenant and user must be able to say so and the default
# cannot know them.
_default: Recorder | None = None

# A recorder scoped to the current async task, layered OVER the process one. A single action run
# (or any caller) can collect its OWN calls for the duration of a block without disturbing the
# audit recorder or other concurrent runs — it is a `ContextVar`, so the collection does not leak
# across tasks the way the global slot would. `recording_into` installs it; `default_recorder`
# (which every client falls back to) prefers it.
_scoped: ContextVar[Recorder | None] = ContextVar("api_log_scoped_recorder", default=None)


def set_default_recorder(recorder: Recorder | None) -> None:
    """Install the process-wide recorder. Idempotent; the last call wins."""
    global _default  # noqa: PLW0603 - one process-wide slot, by design
    _default = recorder


def default_recorder() -> Recorder | None:
    """The recorder for the next call: the task-scoped one (`recording_into`) if a block installed
    it, else the process-wide one, else None.

    None is a legitimate answer, not a misconfiguration: a script, a test or an SDK consumer
    outside this monorepo has no database to write to, which is the reason these packages take
    a recorder rather than reaching for one.
    """
    return _scoped.get() or _default


@contextmanager
def recording_into(sink: Recorder) -> Iterator[None]:
    """Also hand every ``ApiCall`` made in this block to ``sink``, alongside whatever recorder was
    already in effect (the process one, or an outer ``recording_into``).

    For a caller that needs the calls it just made — a baton action surfacing the request and
    response of the vendor call it fired as the run's evidence, success or failure. Scoped to the
    current task via a ``ContextVar``, so concurrent runs each collect only their own, and the
    audit trail keeps its row regardless. ``sink`` is called with a redacted copy (the same
    ``record`` hands the recorder), so it never sees credentials.
    """
    outer = _scoped.get() or _default

    def fan(call: ApiCall) -> None:
        try:
            sink(call)
        finally:
            if outer is not None:
                outer(call)

    token = _scoped.set(fan)
    try:
        yield
    finally:
        _scoped.reset(token)


def clear_default_recorder() -> None:
    """Forget the process-wide recorder (shutdown; and test isolation, since it is global)."""
    set_default_recorder(None)


@contextmanager
def record(recorder: Recorder | None, call: ApiCall) -> Iterator[ApiCall]:
    """Time a call, and hand the finished record to `recorder` whatever happens.

    The `finally` is the point: a call that raised — a timeout, a DNS failure, a connection
    reset — is exactly the one someone reconstructs an incident from, and it is the one a
    naive `recorder(call)` after the request would miss.

    Recording never breaks the caller. If the recorder itself raises, the exception is
    swallowed: failing an adviser's transfer because the audit insert deadlocked would be a
    worse outcome than a missing row, and the row's absence is itself visible in the log.
    """
    started = time.perf_counter()
    try:
        yield call
    except Exception as exc:
        if call.error is None:
            call.error = f"{type(exc).__name__}: {exc}"
        call.ok = False
        raise
    finally:
        call.duration_ms = int((time.perf_counter() - started) * 1000)
        if recorder is not None:
            try:
                recorder(call.redacted())
            except Exception:  # noqa: BLE001, S110 - see docstring
                pass


RecorderFactory = Callable[[], Recorder]


# ------------------------------------------------------------------------------------------------
# The operation name.
#
# `operation` is the log's domain verb — what it groups by, filters on, and what the outbound
# console joins its tabs by. It was derived three different ways, once per vendor client, and only
# Morgan Lloyd's was tested:
#
#   seccl        `path.lstrip("/").split("/")[0]`   -> "v1" for `/v1/fee-structures/{firm}`
#   morgan_lloyd strips the query AND key predicate -> the one that had been fixed, the hard way
#   sei          `segments[-1]`                     -> an identifier, for any path carrying one
#
# The RULE genuinely differs — ML names an action at the root, SEI names a service then an
# operation — so this takes the vendor's choice as a parameter rather than pretending one rule
# fits. What is shared is the CLEANING, which is where all three went wrong or would have.

_KEY_PREDICATE = "("


def path_segments(path: str) -> tuple[str, ...]:
    """A request path's segments, with everything that is not the operation removed.

    Strips the query string and any key predicate. Both matter and both were learned from a real
    log: OData reads landed one distinct "operation" per `$select` projection, which fragments
    every count over them and makes an exact-match filter miss the call entirely.
    """
    without_query = path.split("?")[0]
    return tuple(seg.split(_KEY_PREDICATE)[0] for seg in without_query.split("/") if seg)


# Path prefixes that are not the operation. A version segment names the API, not the call: Seccl's
# `/v1/fee-structures/{firm}` recorded `operation="v1"`, which is true of one endpoint in the whole
# sync and tells a reader nothing about it.
_NOT_AN_OPERATION = frozenset({"v1", "v2", "v3", "api"})


def operation_of(path: str, *, take: Literal["first", "last"] = "first") -> str | None:
    """The domain verb a path names, or None when it names nothing.

    `take` is the vendor's own shape, stated rather than inferred:

    * ``first`` — the operation is at the root. Morgan Lloyd's Dataverse actions and entity sets,
      Seccl's resources.
    * ``last``  — the path is `service/operation`. SEI's SWP, where the first segment is the
      service (`currencyreceiptservices`) and is recorded separately.

    A path whose last segment is an IDENTIFIER is the reason `last` is not the default: it would
    record one operation per record and fragment the log, which is the same failure the query
    string caused for Morgan Lloyd.
    """
    segments = [seg for seg in path_segments(path) if seg.lower() not in _NOT_AN_OPERATION]
    if not segments:
        return None
    return segments[0] if take == "first" else segments[-1]


def body_of(response: Any, *, limit: int = 4000) -> Any:
    """A response as JSON where it is JSON, else its text, truncated.

    Truncated because a rogue endpoint answering with a 5MB HTML error page should not put 5MB
    into an append-only table once per retry cycle. Duck-typed on `.json()`/`.text` rather than
    typed against httpx, so this package keeps its no-dependency rule.
    """
    try:
        return response.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is the case this exists for
        return getattr(response, "text", "")[:limit] or None
