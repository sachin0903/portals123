"""The outbox dispatcher — drains ``flows.outbox``, routed by ``event_type``.

The outbox is the engine's transactional side-effect queue: rows are enqueued in the SAME
transaction as a state transition (``notifications.enqueue``), and this module delivers them out
of band with the worker idiom (claim via ``FOR UPDATE SKIP LOCKED``, exponential backoff,
dead-letter). Routing is a small handler table so new event types bolt on:

- ``task.requested``  → create a standalone task (``app.tasks.service.create_task``); the payload
  carries the authored spec + flow context, and the deterministic ``source_ref`` collapses
  at-least-once redelivery on the tasks table's unique key.
- ``identity.check.requested`` → send a portal client's held identity photos to ComplyCube and
  start the check (``app.complycube.identity.submit``).
- ``aml.screen.run`` → run a portal client's AML screen once identity passes
  (``app.complycube.screen.handle``); a screen that cannot run raises an operations task.
- ``aml.rescreen.requested`` → screen a client again whose screened details changed, or ask
  operations to (``app.complycube.rescreen.rescreen``; the row is written by a trigger
  on ``crm.contacts``).
- anything else with an addressable email payload (``notify.*``) → ``notifications._send``.
- unknown event types → dead-letter immediately (a retry can't fix "no handler").

Failure discipline: a ``PermanentDispatchError`` (bad spec — unknown role slug, inactive user,
missing participant) dead-letters immediately with the reason; any other exception is transient
and retries on backoff. There is a THIRD state for the email rows — **held**: when the deployment
mails nobody at all (``email.sending_disabled()``) the row is pushed forward with its attempt count
untouched, because a suppressed send is not a delivery failure and must not walk a row towards a
dead letter. See the branch itself for why that check is here rather than on the worker's loop.

**Dead-lettering goes through ``app.logging.report_error``**, not a
WARNING: the row is a task that will never be raised or an email that will never be sent, with
nothing left to retry it, so it has to reach the error tracker and ``graphene_errors_total``
rather than only container stdout. Runs on a ``service_role`` session inside the background worker
process (``app/worker.py``).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.email import transport as email_mod
from app.flows import effects, lookup, notifications
from app.logging import report_error
from app.tasks import service as tasks_service

logger = logging.getLogger("dispatcher")


class PermanentDispatchError(Exception):
    """The row can never succeed (bad authoring/reference) — dead-letter it, don't retry."""


async def _journey_assignee(
    session: AsyncSession, tenant_id: int, execution_uid: object
) -> str | None:
    """The platform member this journey's staff work belongs to, when one has been chosen.

    A client task is never redirected here. With nobody assigned, the spec's own assignee
    (a role queue, a named user) stands."""
    if not isinstance(execution_uid, str) or not execution_uid:
        return None
    owner = (
        await session.execute(
            text(
                "SELECT assignee_user_id::text FROM flows.executions "
                "WHERE uid = :u AND tenant_id = :t"
            ),
            {"u": execution_uid, "t": tenant_id},
        )
    ).scalar_one_or_none()
    return str(owner) if owner else None


async def _handle_task_requested(
    session: AsyncSession, tenant_id: int, payload: dict[str, Any]
) -> None:
    spec = payload.get("spec") or {}
    assign = spec.get("assign_to") or {}
    kind = assign.get("kind")
    participant = payload.get("participant_subject_id")

    assignee: dict[str, Any]
    if kind == "role":
        role_id = (
            await session.execute(
                text("SELECT id FROM auth.roles WHERE tenant_id = :t AND slug = :s"),
                {"t": tenant_id, "s": assign.get("role")},
            )
        ).scalar_one_or_none()
        if role_id is None:
            raise PermanentDispatchError(f"no role '{assign.get('role')}' in tenant {tenant_id}")
        assignee = {"assignee_kind": "role", "assignee_role_id": int(role_id)}
    elif kind == "user":
        assignee = {"assignee_kind": "user", "assignee_user_id": assign.get("user_id")}
    elif kind == "participant":
        if participant is None:
            raise PermanentDispatchError("participant-assigned task on a step with no participant")
        assignee = {"assignee_kind": "contact", "assignee_contact_id": int(participant)}
    else:
        raise PermanentDispatchError(f"unknown assign_to kind '{kind}'")

    # Staff work follows the journey's assignee. A participant spec is the client's own
    # task and is left alone; with no assignee, the authored role or user stands.
    if kind != "participant":
        owner = await _journey_assignee(session, tenant_id, payload.get("execution_uid"))
        if owner:
            assignee = {"assignee_kind": "user", "assignee_user_id": owner}

    # Placeholder set: {participant_name}, {flow_name}, {step_title}. Kept tiny on purpose.
    participant_name = None
    if participant is not None:
        participant_name = (
            await session.execute(
                text(
                    "SELECT COALESCE(name, NULLIF(TRIM(CONCAT_WS(' ', first_name, surname)), ''),"
                    "  email) FROM crm.contacts WHERE id = :c"
                ),
                {"c": int(participant)},
            )
        ).scalar_one_or_none()
    fills = {
        "participant_name": participant_name or "the client",
        "flow_name": payload.get("flow_name") or "the journey",
        "step_title": payload.get("step_title") or payload.get("step_key") or "a step",
    }

    def _fill(s: str | None) -> str | None:
        if s is None:
            return None
        for key, val in fills.items():
            s = s.replace("{" + key + "}", str(val))
        return s

    due_days = spec.get("due_days")
    due_date = None
    if isinstance(due_days, int):
        due_date = (
            await session.execute(
                text("SELECT (CURRENT_DATE + make_interval(days => :d))::date::text"),
                {"d": due_days},
            )
        ).scalar_one()

    try:
        await tasks_service.create_task(
            session,
            tenant_id=tenant_id,
            title=_fill(spec.get("title")) or "Follow up",
            description=_fill(spec.get("description")),
            category=spec.get("category", "generic"),
            priority=spec.get("priority", "normal"),
            due_date=due_date,
            subject_type="execution",
            subject_id=str(payload.get("execution_uid")),
            source_kind="flow",
            source_ref=str(payload.get("source_ref")),
            **assignee,
        )
    except tasks_service.TaskError as exc:  # bad reference — retrying can't fix authoring
        raise PermanentDispatchError(str(exc)) from exc


#: How long one outbound webhook may take. Short on purpose: the row is retried with backoff, so a
#: slow endpoint costs a retry, whereas a long timeout costs the whole queue's head-of-line.
WEBHOOK_TIMEOUT_S = 10.0

#: How much of a webhook's REPLY we will read. A webhook's answer is a status code — nothing here
#: parses the body, and nothing downstream ever sees it — so any of it is already more than we
#: need. The cap exists because ``client.post`` buffers the whole response into memory before this
#: code sees a single header, so without it an allowlisted endpoint answering with a gigabyte (or a
#: small compressed bomb that decodes to one) is an unbounded allocation in the worker on a third
#: party's say-so. ``lookup`` streams under the same discipline and for the same reason; this side
#: had no bound at all. Generous, because exceeding it is a misconfiguration to report, not a
#: threshold to tune.
WEBHOOK_MAX_RESPONSE_BYTES = 64 * 1024


async def _handle_webhook(tenant_id: int, payload: dict[str, Any]) -> None:
    """Deliver a ``webhook_out`` action's POST (``app/flows/effects.py``).

    The URL was resolved from the operator's allowlist when the row was enqueued and is re-checked
    here against the LIVE configuration: a row can sit in the queue across a deploy, and an
    endpoint an operator has since removed must stop being called rather than keep firing from a
    payload written before they removed it. That makes a withdrawn endpoint a permanent failure
    (dead-letter, reported) — correct, because nothing about retrying it can succeed.

    Non-2xx is a transient failure: it goes back on backoff and eventually dead-letters, which is
    the whole reason this rides the outbox instead of an inline call.

    The client comes from ``lookup._build_client`` — the same house policy as the inbound half,
    which is the point: ``follow_redirects=False`` is a CONTROL on both, since a 302 is exactly how
    an allowlisted endpoint becomes an unallowlisted one, and it was being inherited from an httpx
    default here while being argued for explicitly over there. The reply is streamed under a byte
    cap for the same reason a lookup's is; nothing reads it beyond the status code.
    """
    key = str(payload.get("endpoint_key") or "")
    live = settings.automation_webhook_endpoints.get(key)
    if live is None:
        raise PermanentDispatchError(
            f"webhook endpoint '{key}' is no longer configured in this deployment"
        )
    async with lookup._build_client(timeout=WEBHOOK_TIMEOUT_S) as client:
        async with client.stream(
            "POST",
            live,
            json={
                "endpoint_key": key,
                "tenant_id": tenant_id,
                "automation": {
                    "uid": payload.get("automation_uid"),
                    "name": payload.get("automation_name"),
                    "trigger": payload.get("trigger_key"),
                },
                "case_uid": payload.get("case_uid"),
                "data": payload.get("body") or {},
            },
        ) as response:
            status = response.status_code
            # A redirect is not followed, so it is not a delivery either — say so rather than let
            # a 3xx pass as success on the "< 400" test. Transient: an operator repointing the
            # endpoint at its final address fixes it, and the row is still on backoff until then.
            if response.is_redirect:
                raise RuntimeError(
                    f"webhook endpoint '{key}' redirected ({status}), which is not followed"
                )
            if status >= 400:
                raise RuntimeError(f"webhook endpoint '{key}' returned {status}")
            # Drained, not read: closing an httpx stream without consuming it can leave the
            # connection unusable, and the cap has to bite BEFORE the bytes are in memory — which
            # `client.post`, buffering the whole body, could never do.
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > WEBHOOK_MAX_RESPONSE_BYTES:
                    raise RuntimeError(
                        f"webhook endpoint '{key}' answered with more than "
                        f"{WEBHOOK_MAX_RESPONSE_BYTES} bytes"
                    )


#: How long a ``notify.*`` row is HELD when this deployment cannot mail anybody at all. Long enough
#: that a dev box is not re-reading the same rows every poll, short enough that a deploy which turns
#: sending on delivers the backlog within the hour.
HELD_RETRY_SECONDS = 300


async def _drain(session: AsyncSession, *, now: datetime, limit: int) -> tuple[int, int]:
    """The drain itself. Returns ``(claimed, delivered)``.

    ``claimed`` is what tells a caller whether the queue still has work — ``delivered`` alone
    can't, since a row that failed and went back on backoff is progress too.
    """
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, tenant_id, event_type, payload, attempts, max_attempts "
                    "FROM flows.outbox "
                    "WHERE status = 'queued' AND next_retry <= :now "
                    "ORDER BY next_retry FOR UPDATE SKIP LOCKED LIMIT :lim"
                ),
                {"now": now, "lim": limit},
            )
        )
        .mappings()
        .all()
    )
    delivered = 0
    for r in rows:
        payload = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
        error: str | None = None
        permanent = False
        try:
            if r["event_type"] == "task.requested":
                await _handle_task_requested(session, int(r["tenant_id"]), payload)
            elif r["event_type"] == "identity.check.requested":
                # Imported here rather than at module scope: app.complycube imports this module for
                # PermanentDispatchError, so a top-level import would be circular.
                from app.complycube import identity as complycube_identity

                await complycube_identity.submit(session, int(r["tenant_id"]), payload)
            elif r["event_type"] == "aml.screen.run":
                from app.complycube import screen as complycube_screen

                await complycube_screen.handle(session, int(r["tenant_id"]), payload)
            elif r["event_type"] == "aml.rescreen.requested":
                from app.complycube import rescreen as complycube_rescreen

                await complycube_rescreen.rescreen(session, int(r["tenant_id"]), payload)
            elif r["event_type"] == effects.WEBHOOK_EVENT_TYPE:
                await _handle_webhook(int(r["tenant_id"]), payload)
            elif "to" in payload or str(r["event_type"]).startswith("notify."):
                # HELD is not FAILED. When this deployment mails nobody at all (no Resend
                # credential, or a non-production stage with an empty allowlist) `_send` answers
                # False for a reason that has nothing to do with delivery, and treating that as a
                # failure burns an attempt each pass until the row DEAD-LETTERS. A dead letter says
                # "this email will never be sent" and is reported to the error tracker, so a dev box
                # manufactured a stream of incidents indistinguishable from a real Resend outage.
                #
                # `_notifier_loop` already skips its scan for exactly this reason; this is the same
                # rule for the outbox. It is enforced HERE, at the row, and deliberately NOT by
                # gating the worker's outbox loop: that loop also drains `task.requested`,
                # `identity.check.requested` and `webhook_out`, none of which sends mail, so
                # skipping it would stop a dev box raising flow tasks at all.
                #
                # The row is pushed forward without incrementing `attempts` — so it cannot
                # dead-letter while it is only held, and it cannot hold the queue's head either
                # (its `next_retry` moves, which a bare `continue` would not, and the rows behind
                # it are tasks that have nothing to do with email).
                await email_mod.refresh_allowlist()
                held = email_mod.sending_disabled()
                if held is not None:
                    logger.info(
                        "outbox %s HELD — %s; no mail can leave this deployment, so the row is "
                        "kept queued rather than retried towards a dead letter",
                        r["id"],
                        held,
                        extra={
                            "event": "outbox_held_sending_disabled",
                            "outbox_id": int(r["id"]),
                            "attempts": int(r["attempts"]),
                        },
                    )
                    await session.execute(
                        text(
                            "UPDATE flows.outbox SET next_retry = :now + "
                            "  make_interval(secs => :delay), last_error = :err WHERE id = :id"
                        ),
                        {
                            "now": now,
                            "delay": HELD_RETRY_SECONDS,
                            "err": f"held (not failed): {held}"[:500],
                            "id": r["id"],
                        },
                    )
                    continue
                if not await notifications._send(int(r["tenant_id"]), payload):
                    error = "delivery failed"
            else:
                error, permanent = f"no handler for '{r['event_type']}'", True
        except PermanentDispatchError as exc:
            error, permanent = str(exc), True
        except Exception as exc:  # noqa: BLE001 — transient (DB blip, provider down): retry
            error = f"{type(exc).__name__}: {exc}"

        if error is None:
            await session.execute(
                text(
                    "UPDATE flows.outbox SET status = 'delivered', delivered_at = :now, "
                    "  attempts = attempts + 1, last_attempt = :now WHERE id = :id"
                ),
                {"now": now, "id": r["id"]},
            )
            delivered += 1
        else:
            attempts = int(r["attempts"]) + 1
            dead = permanent or notifications.should_dead_letter(attempts, int(r["max_attempts"]))
            if dead:
                # A dead-lettered row is a task that will never be raised or a notification that
                # will never be sent — permanently, silently, with nothing left to retry it. A
                # WARNING on container stdout is not a witness to that: it goes through
                # report_error so it reaches the error tracker and graphene_errors_total, like
                # every other swallowed failure (docs/audit/06-core.md).
                report_error(
                    f"outbox row {r['id']} dead-lettered after {attempts} attempt(s) — this "
                    f"{r['event_type']} will NEVER be delivered",
                    log=logger,
                    event="outbox_dead_lettered",
                    outbox_id=int(r["id"]),
                    event_type=str(r["event_type"]),
                    tenant_id=int(r["tenant_id"]),
                    attempts=attempts,
                    permanent=permanent,
                    reason=error[:500],
                )
                if r["event_type"] == "identity.check.requested":
                    from app.complycube import identity as complycube_identity

                    # The client is asked to send the photos again: the step would otherwise
                    # wait, and their copies be held, for a send that will never happen.
                    await complycube_identity.gave_up(session, payload)
            else:
                logger.info(
                    "outbox %s retrying: %s",
                    r["id"],
                    error,
                    extra={
                        "event": "outbox_retry",
                        "outbox_id": int(r["id"]),
                        "attempts": attempts,
                    },
                )
            await session.execute(
                text(
                    "UPDATE flows.outbox SET attempts = :a, last_attempt = :now, "
                    "  status = CASE WHEN :dead THEN 'dead_letter' ELSE 'queued' END, "
                    "  next_retry = :now + make_interval(secs => :delay), "
                    "  last_error = :err WHERE id = :id"
                ),
                {
                    "a": attempts,
                    "now": now,
                    "dead": dead,
                    "delay": notifications.next_retry_seconds(attempts),
                    "err": error[:500],
                    "id": r["id"],
                },
            )
    return len(rows), delivered


async def dispatch_due(session: AsyncSession, *, now: datetime, limit: int = 20) -> int:
    """Drain due outbox rows: claim (skip-locked), route by event_type, then mark delivered /
    backoff-retry / dead-letter. Returns the number delivered. service_role session.

    Every row in the batch settles in the CALLER's transaction, so a caller that batches is
    choosing to lose the whole batch's bookkeeping on a rollback — see ``dispatch_once``, which is
    why the worker calls this one row at a time.
    """
    return (await _drain(session, now=now, limit=limit))[1]


# How many rows one worker pass will drain, each in its own transaction.
BATCH_LIMIT = 20


async def dispatch_once(maker: async_sessionmaker[AsyncSession]) -> bool:
    """One drain pass — ONE ROW PER TRANSACTION (the worker-loop entry point).

    The pass used to be a single transaction around up to 20 rows, i.e. up to 20 emails handed to
    Resend with all the ``status='delivered'`` UPDATEs still uncommitted. Sending is not
    transactional: if the container went away mid-batch — and the worker's new container
    healthcheck makes a restart MORE likely, not less — those updates rolled back while the emails
    had already left, so the retry sent every one of them again. Committing per row bounds that to
    the single row in flight, which is the irreducible at-least-once window for a non-transactional
    provider. It costs one short transaction per row, on a queue whose whole point is that it is
    drained out of band.
    """
    delivered = 0
    for _ in range(BATCH_LIMIT):
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            claimed, sent = await _drain(session, now=datetime.now(UTC), limit=1)
        delivered += sent
        if claimed == 0:  # queue drained (a failed row still counts as claimed — no head-of-line)
            break
    return delivered > 0
