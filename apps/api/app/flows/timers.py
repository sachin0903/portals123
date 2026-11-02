"""Time, for the flow engine — durable wake-ups and the listener half of ``wait_for_event``.

Phase B gives a flow two control nodes that are about waiting, and this module owns both halves
of the machinery underneath them (schema: ``0045_flow_timers``):

* **``wait``** — pause the case for a duration, or until a date carried in case memory / a subject
  field, then resume at the node's successor. One ``flows.timers`` row of kind ``wait``.
* **``wait_for_event``** — park the case until one of the trigger registry's events happens FOR
  THIS CASE'S SUBJECT, with an optional deadline that takes the node's ``timeout_branch``. One
  ``flows.case_listeners`` row, plus (when the author set ``timeout_days``) a ``flows.timers`` row
  of kind ``wait_for_event_timeout``.

**Why a table and not an ``asyncio`` sleep.** "Resume this case in seven days" is a promise made
to a firm about a client. A worker is redeployed, OOM-killed and scaled to zero routinely; an
in-process timer is lost every one of those times, silently, and the symptom is a client who never
gets chased. So a wait is a committed row, claimed by whichever worker gets to it
(``FOR UPDATE SKIP LOCKED`` — several may run), and surviving a restart is the whole point.

**The two seams this module is defined by.**

1. *Resumption* belongs to the orchestrator, not here. When a timer fires, this module calls
   **``orchestrator.resume_from_timer(session, *, case_id, node_id, kind)``** — the frozen
   contract with B2 — and knows nothing about successors, branches or step planning. ``kind`` is
   one of ``RESUME_KINDS``: ``'wait'`` and ``'wait_for_event'`` both mean *take the node's normal
   successor*; ``'wait_for_event_timeout'`` means *take the node's timeout branch*. The import is
   deliberately lazy and guarded (the orchestrator imports half the engine, and this module is
   imported BY it), so a deployment where the seam does not exist logs a warning and leaves the
   timer pending rather than crashing the loop.
2. *Noticing an event* belongs to the automation consumer, which already tails ``audit.actions``.
   Rather than building a second consumer, ``release_listeners`` is a single indexed lookup that
   ``app/flows/automation.py::handle_event`` calls for the (small) slice of the stream that is on
   the curated trigger menu.

**Subject scoping is the correctness-critical part** and is made structural rather than careful:
``flows.case_listeners.subject_kind``/``subject_id`` are both NOT NULL, both compared on release,
and a case whose subject cannot be resolved *in the trigger's own subject space* is refused a
listener at arm time. A document uploaded for client A cannot release client B's case, and an
account event cannot release a contact-scoped listener because account 7 and contact 7 are
different id spaces — a comparison on the id alone would conflate them.

Everything here runs on a ``service_role`` session: both tables are engine state with no
``authenticated`` grant and no policy at all.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.flows import conditions, triggers
from app.flows.states import TERMINAL
from app.ids import new_id
from app.logging import report_error

logger = logging.getLogger("flow_timers")

#: Timer kinds — what firing one MEANS. Mirrors the CHECK on ``flows.timers.kind``.
KIND_WAIT = "wait"
KIND_EVENT_TIMEOUT = "wait_for_event_timeout"
KINDS: frozenset[str] = frozenset({KIND_WAIT, KIND_EVENT_TIMEOUT})

#: The ``kind`` values handed to ``orchestrator.resume_from_timer``. A superset of the timer kinds:
#: a ``wait_for_event`` node released by its EVENT resumes at the normal successor and has no timer
#: row at all, so the seam needs a third word for it.
RESUME_WAIT = KIND_WAIT
RESUME_EVENT = "wait_for_event"
RESUME_EVENT_TIMEOUT = KIND_EVENT_TIMEOUT
RESUME_KINDS: frozenset[str] = frozenset({RESUME_WAIT, RESUME_EVENT, RESUME_EVENT_TIMEOUT})

#: The contract's structural ceiling on a wait (``caps.maxWaitDays``). Publish refuses a definition
#: that authors a longer duration; this is the runtime backstop for ``until_date_field``, which
#: publish cannot check because the date is not known until the case runs.
MAX_WAIT_DAYS: int = int(triggers.CAPS["maxWaitDays"])

#: How many due timers one worker pass fires, each in its own transaction.
BATCH_LIMIT = 20

#: Retry backoff for a resume that raised: 60s, 120s, 240s … capped at an hour. The same shape as
#: the outbox's (``app/flows/notifications.py::next_retry_seconds``), deliberately — a wait that
#: cannot resume is the same class of problem as a notification that cannot send.
_BASE_BACKOFF_S = 60
_MAX_BACKOFF_S = 3600


def backoff_seconds(attempts: int) -> int:
    """Delay before the Nth failed resume is retried."""
    return int(min(_BASE_BACKOFF_S * (2 ** max(0, attempts - 1)), _MAX_BACKOFF_S))


# ---------------------------------------------------------------------------------------------
# resolve_fire_at — pure
# ---------------------------------------------------------------------------------------------


def _aware(value: datetime) -> datetime:
    """A naive datetime is read as UTC. Authored dates and lake/OLTP columns arrive both ways, and
    guessing local time would silently shift a deadline by an hour twice a year."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _as_number(value: Any) -> float | None:
    """A finite non-boolean number, or ``None``. Numeric strings are accepted (a builder field is
    a text input); ``True`` is not a 1 — a boolean duration is authoring junk, not a one-hour wait.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    # NaN/±inf would make every comparison below meaningless (and `now + timedelta(days=nan)`
    # raises outright, which would fail the transition instead of degrading to "fire now").
    return number if math.isfinite(number) else None


def _as_datetime(value: Any) -> datetime | None:
    """Coerce a resolved ``until_date_field`` value to an aware datetime, or ``None``.

    Total: junk of any shape yields ``None``, which the caller turns into "fire immediately"
    rather than an exception that would fail the whole transition.
    """
    if isinstance(value, datetime):
        return _aware(value)
    if isinstance(value, date):
        # A bare date means the START of that day. Anything else (end of day, close of business)
        # would be an invented convention the author never wrote down.
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        # fromisoformat on 3.13 handles offsets but not a trailing 'Z'.
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            return _aware(datetime.fromisoformat(raw))
        except ValueError:
            return None
    return None


def resolve_fire_at(
    config: Mapping[str, Any], *, now: datetime, ctx: Mapping[str, Any]
) -> datetime | None:
    """When a ``wait`` node's pause ends — or ``None`` for "do not wait at all".

    Pure, and total. Reads the contract's three ``controlNodes.wait`` config keys:

    * ``duration_days`` / ``duration_hours`` — offsets from ``now``; both may be set and they sum.
    * ``until_date_field`` — a dotted field resolved out of the case context by the ONE shared
      resolver (``conditions.resolve_field``), which reads both the planner's flat dotted-key
      contexts and the nested trigger/branch ones.

    A duration wins over a date field when both are authored: it is the more specific instruction,
    and the alternative (erroring) would be a publish-time concern, not a runtime one.

    **``None`` means fire immediately, never "wait for ever".** An absent config, an unresolvable
    or junk date, a date already in the past, or a zero/negative duration all return ``None``, and
    every caller's contract is to take the node's successor there and then. That choice is
    deliberate and it is the safe direction: a `wait until {memory.review_date}` whose field never
    got populated would otherwise park a client's journey silently and permanently, and nobody
    finds that until the client complains. Moving on early is visible in the case history; hanging
    for ever is not.

    Clamped to ``caps.maxWaitDays``. Publish already refuses an over-cap authored duration, so in
    practice this bites on a date field that resolves years out — clamped rather than refused,
    because a 400-day date is a data problem and 90 days from now is a defensible reading of it,
    whereas refusing would drop the wait entirely.
    """
    at = _aware(now)
    if not isinstance(config, Mapping):
        return None

    days = _as_number(config.get("duration_days"))
    hours = _as_number(config.get("duration_hours"))
    target: datetime | None
    if days is not None or hours is not None:
        target = at + timedelta(days=days or 0.0, hours=hours or 0.0)
    else:
        target = _as_datetime(conditions.resolve_field(config.get("until_date_field"), ctx))
    if target is None:
        return None

    ceiling = at + timedelta(days=MAX_WAIT_DAYS)
    if target > ceiling:
        logger.info(
            "clamping a flow wait to the contract cap",
            extra={"event": "flow_timer_clamped", "max_wait_days": MAX_WAIT_DAYS},
        )
        target = ceiling
    return target if target > at else None


# ---------------------------------------------------------------------------------------------
# Scheduling + cancellation (the frozen interface B2 calls)
# ---------------------------------------------------------------------------------------------


async def schedule(
    session: AsyncSession,
    *,
    tenant_id: int,
    case_id: int,
    node_id: str,
    kind: str,
    fire_at: datetime,
    payload: Mapping[str, Any] | None = None,
) -> str:
    """Arm a durable wake-up for ``case_id`` at ``node_id``. Returns the timer uid.

    **Idempotent by construction.** The partial unique index on ``(case_id, node_id) WHERE state =
    'pending'`` makes this an upsert: re-entering a node whose wait is already armed re-arms it
    instead of stacking a second wake-up. That matters because the engine's own resume path is
    at-least-once — two pending timers on one node would resume the same case twice, and on a
    ``wait_for_event`` node the second one would take the timeout branch after the event had
    already released it.

    Called inside the caller's transaction, deliberately: a case that has been told to wait and a
    timer that will wake it must commit together or not at all.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown timer kind {kind!r}; expected one of {sorted(KINDS)}")
    uid = new_id()
    return str(
        (
            await session.execute(
                text(
                    "INSERT INTO flows.timers "
                    "  (uid, tenant_id, case_id, execution_id, node_id, kind, fire_at, payload) "
                    "VALUES (:uid, :t, :c, "
                    "  (SELECT execution_id FROM flows.cases WHERE id = :c), "
                    "  :n, :k, :fa, CAST(:p AS jsonb)) "
                    "ON CONFLICT (case_id, node_id) WHERE state = 'pending' DO UPDATE SET "
                    "  kind = EXCLUDED.kind, fire_at = EXCLUDED.fire_at, "
                    "  payload = EXCLUDED.payload, attempts = 0, last_error = NULL "
                    "RETURNING uid"
                ),
                {
                    "uid": uid,
                    "t": tenant_id,
                    "c": case_id,
                    "n": node_id,
                    "k": kind,
                    "fa": _aware(fire_at),
                    "p": json.dumps(dict(payload or {}), default=str),
                },
            )
        ).scalar_one()
    )


async def cancel_for_node(session: AsyncSession, *, case_id: int, node_id: str) -> int:
    """Retire everything this case is waiting on AT THIS NODE — the pending timer and the active
    listener alike, because on a ``wait_for_event`` node the two are one logical wait.

    Returns the number of rows retired across both tables (a timer-less listener therefore still
    reports 1, which is what "did I actually cancel a wait?" needs to mean).
    """
    # RETURNING + a count rather than `rowcount`: on an async driver the attribute is not part of
    # the typed Result surface, and counting the returned ids is exact under any driver.
    retired = (
        await session.execute(
            text(
                "UPDATE flows.timers SET state = 'cancelled', cancelled_at = now() "
                "WHERE case_id = :c AND node_id = :n AND state = 'pending' RETURNING id"
            ),
            {"c": case_id, "n": node_id},
        )
    ).all()
    listeners = (
        await session.execute(
            text(
                "UPDATE flows.case_listeners SET state = 'cancelled', cancelled_at = now() "
                "WHERE case_id = :c AND node_id = :n AND state = 'active' RETURNING id"
            ),
            {"c": case_id, "n": node_id},
        )
    ).all()
    return len(retired) + len(listeners)


async def cancel_for_case(session: AsyncSession, *, case_id: int) -> int:
    """Retire every pending wake-up and active listener on a case. Call this when a case reaches a
    terminal state: a cancelled journey must not wake up next Tuesday.

    Returns the number of rows retired across both tables. The timer loop cancels a due timer whose
    case is already terminal anyway — belt and braces, because the loop's check only runs when the
    timer comes due, which may be months away.
    """
    retired = (
        await session.execute(
            text(
                "UPDATE flows.timers SET state = 'cancelled', cancelled_at = now() "
                "WHERE case_id = :c AND state = 'pending' RETURNING id"
            ),
            {"c": case_id},
        )
    ).all()
    listeners = (
        await session.execute(
            text(
                "UPDATE flows.case_listeners SET state = 'cancelled', cancelled_at = now() "
                "WHERE case_id = :c AND state = 'active' RETURNING id"
            ),
            {"c": case_id},
        )
    ).all()
    return len(retired) + len(listeners)


# ---------------------------------------------------------------------------------------------
# wait_for_event — the listener half
# ---------------------------------------------------------------------------------------------


async def _case_subject(
    session: AsyncSession, case_id: int, spec: triggers.TriggerSpec
) -> int | None:
    """The case's subject id **in the trigger's own subject space**, or ``None``.

    Not "the case's subject id": a contact-subject case listening for an account trigger has no
    account to be about, and returning its contact id would arm a listener that releases on a
    stranger's account event. Each branch below reads only a column that genuinely holds an id of
    ``spec.subject``'s kind; anything else is a refusal.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT c.subject_contact_id, c.subject_type, c.subject_id, "
                    "       e.crm_application_id "
                    "FROM flows.cases c "
                    "LEFT JOIN flows.executions e ON e.id = c.execution_id "
                    "WHERE c.id = :c"
                ),
                {"c": case_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    subject_type, subject_id = row["subject_type"], row["subject_id"]
    if spec.subject == "contact":
        if row["subject_contact_id"] is not None:
            return int(row["subject_contact_id"])
        return int(subject_id) if subject_type == "contact" and subject_id is not None else None
    if spec.subject == "account":
        return int(subject_id) if subject_type == "account" and subject_id is not None else None
    if spec.subject == "application":
        return int(row["crm_application_id"]) if row["crm_application_id"] is not None else None
    if spec.subject == "case":
        return case_id
    # 'task' — a case is never about a task, so there is nothing honest to scope to. That is why
    # 'task' is absent from ``triggers.LISTENER_SUBJECTS`` and a task trigger is refused at publish
    # rather than reaching here: this branch is the last line of defence, not the only one.
    return None


async def arm_listener(
    session: AsyncSession,
    *,
    tenant_id: int,
    case_id: int,
    node_id: str,
    trigger_key: str,
    node_conditions: Any = None,
    timer_id: int | None = None,
    subject_kind: str | None = None,
    subject_id: int | None = None,
) -> str | None:
    """Park ``case_id`` on ``node_id``, listening for ``trigger_key`` about its own subject.

    Returns the listener uid, or ``None`` when no listener could be armed — an unknown trigger key,
    a trigger nothing could ever deliver to a waiting case, or a case with no subject in that
    trigger's subject space. **``None`` is not an error the caller should swallow**: the node then
    depends entirely on its timeout, so a node with no ``timeout_days`` would hang. Publish refuses
    the first two outright (``graph._wait_for_event_issues``); at runtime, saying so and leaving
    the timeout to do its job beats arming a listener that would release on any client's event —
    or, worse, one that nothing can release at all.

    Upserts on the ``(case_id, node_id) WHERE state = 'active'`` unique index, so re-entering the
    node re-arms rather than duplicating.
    """
    spec = triggers.resolve(trigger_key)
    if spec is None:
        logger.warning(
            "cannot arm a flow listener for an unknown trigger key",
            extra={"event": "flow_listener_unknown_trigger", "trigger_key": trigger_key},
        )
        return None
    # The same answer publish and the builder read (``triggers.listener_refusal``), enforced again
    # here because publish is not retroactive: a definition published before that check existed can
    # still carry, say, a schedule trigger — whose subject IS a contact, so every branch below
    # would happily arm a listener no event can ever match, and a node with no deadline would park
    # the case permanently. Refusing is what turns "wedged for ever" into "moved on, and logged".
    refusal = triggers.listener_refusal(spec)
    if refusal is not None:
        logger.warning(
            "cannot arm a flow listener: this trigger cannot be delivered to a waiting case",
            extra={
                "event": "flow_listener_undeliverable",
                "trigger_key": trigger_key,
                "reason": refusal,
                "case_id": case_id,
            },
        )
        return None
    kind = subject_kind or spec.subject
    sid = subject_id if subject_id is not None else await _case_subject(session, case_id, spec)
    if sid is None:
        logger.warning(
            "cannot arm a flow listener: the case has no subject in the trigger's subject space",
            extra={
                "event": "flow_listener_unscoped",
                "trigger_key": trigger_key,
                "subject_kind": kind,
                "case_id": case_id,
            },
        )
        return None
    uid = new_id()
    return str(
        (
            await session.execute(
                text(
                    "INSERT INTO flows.case_listeners "
                    "  (uid, tenant_id, case_id, node_id, trigger_key, subject_kind, subject_id, "
                    "   conditions, timer_id) "
                    "VALUES (:uid, :t, :c, :n, :k, :sk, :sid, CAST(:cond AS jsonb), :tid) "
                    "ON CONFLICT (case_id, node_id) WHERE state = 'active' DO UPDATE SET "
                    "  trigger_key = EXCLUDED.trigger_key, subject_kind = EXCLUDED.subject_kind, "
                    "  subject_id = EXCLUDED.subject_id, conditions = EXCLUDED.conditions, "
                    "  timer_id = EXCLUDED.timer_id "
                    "RETURNING uid"
                ),
                {
                    "uid": uid,
                    "t": tenant_id,
                    "c": case_id,
                    "n": node_id,
                    "k": trigger_key,
                    "sk": kind,
                    "sid": sid,
                    "cond": json.dumps(node_conditions) if node_conditions else None,
                    "tid": timer_id,
                },
            )
        ).scalar_one()
    )


async def enter_wait_for_event(
    session: AsyncSession,
    *,
    tenant_id: int,
    case_id: int,
    node_id: str,
    config: Mapping[str, Any],
    now: datetime | None = None,
) -> tuple[str | None, str | None]:
    """Everything a ``wait_for_event`` node needs, in one call: arm the listener and (when the
    author set ``timeout_days``) the deadline that takes ``timeout_branch``.

    Returns ``(listener_uid, timer_uid)``, either of which may be ``None``. Both ``None`` means the
    node can never be left — the caller must treat that as "resume now" rather than park the case,
    for the same reason ``resolve_fire_at`` returns ``None`` instead of hanging.

    The timer is armed FIRST so the listener can point at it: releasing on the event then retires
    the deadline in the same statement set.
    """
    at = _aware(now or datetime.now(UTC))
    timeout_days = _as_number(config.get("timeout_days"))
    timer_uid: str | None = None
    timer_id: int | None = None
    if timeout_days is not None and timeout_days > 0:
        fire_at = resolve_fire_at({"duration_days": timeout_days}, now=at, ctx={})
        if fire_at is not None:
            timer_uid = await schedule(
                session,
                tenant_id=tenant_id,
                case_id=case_id,
                node_id=node_id,
                kind=KIND_EVENT_TIMEOUT,
                fire_at=fire_at,
                payload={
                    "trigger": config.get("trigger"),
                    # Snapshotted: by the time the deadline passes the flow may have been
                    # re-published with a different branch target, and the case is pinned to the
                    # version it started on.
                    "timeout_branch": config.get("timeout_branch"),
                },
            )
            timer_id = (
                await session.execute(
                    text("SELECT id FROM flows.timers WHERE uid = :u"), {"u": timer_uid}
                )
            ).scalar_one_or_none()
    listener_uid = await arm_listener(
        session,
        tenant_id=tenant_id,
        case_id=case_id,
        node_id=node_id,
        trigger_key=str(config.get("trigger") or ""),
        node_conditions=config.get("conditions"),
        timer_id=int(timer_id) if timer_id is not None else None,
    )
    return listener_uid, timer_uid


#: A lazily-evaluated condition context (see ``release_listeners``).
FactsLoader = Callable[[], Awaitable[Mapping[str, Any]]]


async def _load_facts(facts: Mapping[str, Any] | FactsLoader | None) -> Mapping[str, Any]:
    if facts is None:
        return {}
    if isinstance(facts, Mapping):
        return facts
    return await facts()


async def release_listeners(
    session: AsyncSession,
    *,
    tenant_id: int,
    trigger_keys: Sequence[str],
    subject_kind: str | None,
    subject_id: int | None,
    facts: Mapping[str, Any] | FactsLoader | None = None,
    event_id: int | None = None,
) -> list[str]:
    """Release every case parked on one of ``trigger_keys`` for THIS subject. Returns their uids.

    The hook ``app/flows/automation.py::handle_event`` calls once per event that is on the curated
    trigger menu — one index probe on ``case_listeners_match_idx``, matching nothing in the
    overwhelming majority of firms, which is why it is cheap enough to sit in the consumer's path.

    ``facts`` may be a mapping OR an async callable returning one, so the consumer can hand over a
    loader it never pays for: building the condition context costs a query or two, and the only
    events that need it are the vanishingly rare ones that matched a listener carrying conditions.

    **An event with no resolvable subject releases nothing.** ``subject_id IS NULL`` short-circuits
    before the query, so there is no code path on which an unscoped event can release a scoped
    listener. Combined with both columns being NOT NULL on the table, "client A's upload must not
    release client B's case" is a property of the schema, not of remembering to write the
    predicate.

    A listener whose extra ``conditions`` are not met by this event stays active: the event simply
    was not the one it was waiting for.
    """
    if subject_id is None or subject_kind is None or not trigger_keys:
        return []
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, case_id, node_id, conditions, timer_id "
                    "FROM flows.case_listeners "
                    "WHERE state = 'active' AND tenant_id = :t AND trigger_key = ANY(:keys) "
                    "  AND subject_kind = :sk AND subject_id = :sid "
                    "ORDER BY id"
                ),
                {
                    "t": tenant_id,
                    "keys": list(trigger_keys),
                    "sk": subject_kind,
                    "sid": subject_id,
                },
            )
        )
        .mappings()
        .all()
    )
    released: list[str] = []
    resolved_facts: Mapping[str, Any] | None = None
    for row in rows:
        if row["conditions"]:
            if resolved_facts is None:
                resolved_facts = await _load_facts(facts)
            if not conditions.evaluate(row["conditions"], resolved_facts):
                continue
        try:
            # A SAVEPOINT of its own: this runs inside the automation consumer's batch
            # transaction, so a resume that raises would otherwise poison the whole batch — every
            # other firm's events in it included. Rolling back to here leaves the listener ACTIVE,
            # which is self-healing: the next matching event, or the node's own timeout, tries
            # again rather than the case being stranded half-released.
            async with session.begin_nested():
                await session.execute(
                    text(
                        "UPDATE flows.case_listeners SET state = 'released', released_at = now(), "
                        "  released_event_id = :e WHERE id = :id AND state = 'active'"
                    ),
                    {"id": row["id"], "e": event_id},
                )
                if row["timer_id"] is not None:
                    await session.execute(
                        text(
                            "UPDATE flows.timers SET state = 'cancelled', cancelled_at = now() "
                            "WHERE id = :id AND state = 'pending'"
                        ),
                        {"id": row["timer_id"]},
                    )
                await _resume(
                    session,
                    case_id=int(row["case_id"]),
                    node_id=str(row["node_id"]),
                    kind=RESUME_EVENT,
                )
        except Exception as exc:  # noqa: BLE001 — one stuck case must not stop the consumer
            report_error(
                "a flow listener matched but the case could not be resumed; it stays armed",
                exc=exc,
                log=logger,
                event="flow_listener_resume_failed",
                tenant_id=tenant_id,
                case_id=int(row["case_id"]),
                node_id=str(row["node_id"]),
            )
            continue
        released.append(str(row["uid"]))
        logger.info(
            "a flow case was released by an event",
            extra={
                "event": "flow_listener_released",
                "tenant_id": tenant_id,
                "case_id": int(row["case_id"]),
                "node_id": str(row["node_id"]),
            },
        )
    return released


# ---------------------------------------------------------------------------------------------
# The resume seam
# ---------------------------------------------------------------------------------------------


class ResumeUnavailable(RuntimeError):
    """The orchestrator has no ``resume_from_timer``. Raised so the timer stays pending and is
    retried, rather than being marked fired against an engine that never woke the case."""


async def _resume(session: AsyncSession, *, case_id: int, node_id: str, kind: str) -> None:
    """Hand control back to the engine. THE frozen seam with B2.

    Imported lazily, and guarded, for two reasons that both matter. Mechanically: the orchestrator
    imports most of the engine and will import THIS module (to schedule waits), so a top-level
    import here is a cycle. Practically: this module and its loop must be testable — and
    deployable — before the seam exists, so an absent ``resume_from_timer`` is a logged warning and
    a retryable failure, not an ImportError that takes the worker's timer loop down at boot.
    """
    from app.flows import orchestrator

    hook = getattr(orchestrator, "resume_from_timer", None)
    if hook is None:
        logger.warning(
            "orchestrator.resume_from_timer is not available; the timer stays pending",
            extra={"event": "flow_resume_seam_missing", "case_id": case_id, "node_id": node_id},
        )
        raise ResumeUnavailable("orchestrator.resume_from_timer is not implemented in this build")
    await hook(session, case_id=case_id, node_id=node_id, kind=kind)


# ---------------------------------------------------------------------------------------------
# The due loop
# ---------------------------------------------------------------------------------------------


async def _case_is_terminal(session: AsyncSession, case_id: int) -> bool:
    """Has this case (or the execution it belongs to) finished? A finished journey has no node to
    resume at, so its timer is cancelled rather than fired."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT c.status, c.completed_at, c.cancelled_at, e.status AS execution_status "
                    "FROM flows.cases c "
                    "LEFT JOIN flows.executions e ON e.id = c.execution_id "
                    "WHERE c.id = :c"
                ),
                {"c": case_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return True  # the case is gone; there is nothing left to wake
    if row["completed_at"] is not None or row["cancelled_at"] is not None:
        return True
    terminal = {str(s) for s in TERMINAL}
    return str(row["status"]) in terminal or str(row["execution_status"] or "") in terminal


async def fire_due(session: AsyncSession, *, now: datetime) -> bool:
    """Claim and settle ONE due timer. Returns True if one was claimed (drained or not).

    ``FOR UPDATE SKIP LOCKED`` because several workers may run: the row is locked for the whole
    transaction that resumes the case, so a second worker skips past it rather than firing the same
    wake-up twice. ``state`` is re-checked in the UPDATE for the same reason.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, tenant_id, case_id, node_id, kind, attempts, max_attempts "
                    "FROM flows.timers WHERE state = 'pending' AND fire_at <= :now "
                    "ORDER BY fire_at, id FOR UPDATE SKIP LOCKED LIMIT 1"
                ),
                {"now": now},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return False

    if await _case_is_terminal(session, int(row["case_id"])):
        # Cancelled, NOT fired. Firing would ask the orchestrator to move a latched case, which it
        # would (rightly) refuse; recording it as fired would claim we resumed something we did
        # not. A completed journey whose seven-day chaser comes due is an ordinary outcome, not an
        # error, so it is an INFO line rather than a report_error.
        await session.execute(
            text(
                "UPDATE flows.timers SET state = 'cancelled', cancelled_at = now(), "
                "  last_error = 'the case was already terminal when the timer came due' "
                "WHERE id = :id AND state = 'pending'"
            ),
            {"id": row["id"]},
        )
        logger.info(
            "a flow timer came due on a finished case; cancelled",
            extra={
                "event": "flow_timer_case_terminal",
                "timer_uid": str(row["uid"]),
                "case_id": int(row["case_id"]),
            },
        )
        return True

    try:
        # SAVEPOINT: the resume's own writes roll back on failure, but the attempt/backoff
        # bookkeeping below is written in the OUTER transaction and commits. Without this a
        # failing resume would roll back the record of its own failure and retry instantly,
        # for ever — the hot loop a backoff exists to prevent.
        async with session.begin_nested():
            await _resume(
                session,
                case_id=int(row["case_id"]),
                node_id=str(row["node_id"]),
                kind=str(row["kind"]),
            )
    except Exception as exc:  # noqa: BLE001 — one poisonous timer must never wedge the loop
        await _fail_or_retry(session, dict(row), f"{type(exc).__name__}: {exc}")
        return True

    await session.execute(
        text(
            "UPDATE flows.timers SET state = 'fired', fired_at = now(), "
            "  attempts = attempts + 1 WHERE id = :id AND state = 'pending'"
        ),
        {"id": row["id"]},
    )
    logger.info(
        "a flow timer fired",
        extra={
            "event": "flow_timer_fired",
            "timer_uid": str(row["uid"]),
            "tenant_id": int(row["tenant_id"]),
            "case_id": int(row["case_id"]),
            "node_id": str(row["node_id"]),
            "kind": str(row["kind"]),
        },
    )
    return True


async def _fail_or_retry(session: AsyncSession, row: Mapping[str, Any], error: str) -> None:
    """Back off, or dead-letter. The outbox's discipline (``app/flows/dispatcher.py``)."""
    attempts = int(row["attempts"]) + 1
    dead = attempts >= int(row["max_attempts"])
    await session.execute(
        text(
            "UPDATE flows.timers SET attempts = :a, last_error = :e, "
            "  state = CASE WHEN :dead THEN 'dead_letter' ELSE 'pending' END, "
            "  fire_at = CASE WHEN :dead THEN fire_at "
            "                 ELSE now() + make_interval(secs => :delay) END "
            "WHERE id = :id"
        ),
        {
            "a": attempts,
            "e": error[:500],
            "dead": dead,
            "delay": backoff_seconds(attempts),
            "id": row["id"],
        },
    )
    if dead:
        # A dead-lettered timer is a client's journey that will never resume, with nothing left to
        # retry it. That has to reach the error tracker and graphene_errors_total, not just
        # container stdout — the same rule the outbox's dead letters follow.
        report_error(
            f"flow timer {row['uid']} dead-lettered after {attempts} attempt(s) — case "
            f"{row['case_id']} will NEVER resume from node {row['node_id']}",
            log=logger,
            event="flow_timer_dead_lettered",
            timer_uid=str(row["uid"]),
            tenant_id=int(row["tenant_id"]),
            case_id=int(row["case_id"]),
            node_id=str(row["node_id"]),
            attempts=attempts,
            reason=error[:500],
        )
    else:
        logger.warning(
            "a flow timer failed to resume its case; retrying",
            extra={
                "event": "flow_timer_retry",
                "timer_uid": str(row["uid"]),
                "attempts": attempts,
                "error": error[:500],
            },
        )


async def run_due_once(
    maker: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> bool:
    """One worker pass over the due timers. Returns True if any were claimed (more may remain).

    ONE TRANSACTION PER TIMER, the outbox dispatcher's discipline: resuming a case is real work
    (steps instantiated, tasks raised, notifications enqueued), so batching them would mean a
    single failure at row 19 discarding eighteen resumed journeys' bookkeeping — and the row-level
    ``SKIP LOCKED`` claim already gives the batch nothing.
    """
    worked = False
    for _ in range(BATCH_LIMIT):
        at = _aware(now or datetime.now(UTC))
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            claimed = await fire_due(session, now=at)
        if not claimed:
            break
        worked = True
    return worked
