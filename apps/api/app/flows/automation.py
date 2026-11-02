"""The automation consumer — what turns an event into a journey.

One loop (``app/worker.py::_automations_loop``) drives three passes over this module:

* ``consume_once`` — tail ``audit.actions`` by cursor (``id > position``), match armed
  automations, evaluate entry conditions, apply the safety guards, dispatch. 0041 was designed
  for exactly this consumer; this is the subscriber its header promised.
* ``run_deferred_once`` — re-attempt firings that hit a rate or concurrency limit. Overflow is
  **deferred, never dropped** (contract ``safety.$comment_rates``): a rate limit that loses a
  client's chaser task is worse than one that runs it a minute late.
* ``schedule_once`` — fire ``source="schedule"`` automations in the firm's own timezone.

**The precedence rule.** Crisp's first-satisfiable-wins: for one event, in one tenant, exactly one
automation fires. Candidates are ordered ``(priority, id)`` across BOTH kinds — flow-starts and
rule actions share one table and therefore one order, which is the only way "which one won" has an
answer. A candidate that merely *skips* (conditions unmet, inside its re-arm window) passes the
turn to the next; a candidate that dispatches — or defers, which is a promise to dispatch — ends
the event.

**The safety spine, in the order the guards run** (defaults from the contract's ``safety`` block,
mirrored in ``app/flows/triggers.py``, overridable per tenant and clamped by ``CEILINGS``):

1. **Paused** — ``settings.automations_enabled`` (deployment) then
   ``flows.automation_settings.paused`` (tenant). Nothing dispatches.
2. **Self-trigger** — an event an automation produced does not fire automations unless that
   automation set ``allow_automation_source``. Our stream emits an event for everything an
   automation does, so this is the DEFAULT failure mode, not an edge case.
3. **Chain depth** — the automation marker on the event carries ``chain_depth``; one more than
   that is this firing's depth, capped at ``maxChainDepth``. Over the cap raises an operations
   task rather than failing silently.
4. **Entry conditions** — A2's evaluator, against the flat dotted-path context built here.
5. **Re-arm window** — the same automation does not re-fire for the same subject inside
   ``rearm_minutes`` (per-automation, else ``defaultRearmMinutes``).
6. **Per-case trigger budget** — how many automations have fired inside one client's live journey.
   Exhaustion raises an operations task.
7. **Tenant rate limit** and **flow concurrency** — overflow defers.

**Idempotency.** Every considered candidate lands a ``flows.automation_deliveries`` row keyed
``(automation_id, event_key)``. Redelivery of the same event therefore cannot start a second case
— and the row set is also the answer to "why didn't my automation fire?", which is why skips are
recorded too, not just dispatches.

Runs as ``service_role`` inside the worker (no HTTP principal), so every write names its tenant
explicitly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import cron, environments, providers, tags
from app.config import settings
from app.flows import conditions, timers, triggers
from app.flows.triggers import AutomationContext, DispatchResult, TriggerSpec
from app.logging import report_error
from app.tasks import service as tasks_service

logger = logging.getLogger("automations")

#: The cursor row this consumer owns (``flows.automation_cursor.consumer``).
CONSUMER = "action_stream"

#: How many stream rows one ``consume_once`` pass reads. Small on purpose: the whole batch settles
#: in ONE transaction (so the cursor advance and the deliveries commit together), and a dispatch
#: can be a whole ``start_execution``.
BATCH_LIMIT = 25

#: How far behind the head we read. ``audit.actions`` ids are assigned at INSERT but become
#: visible at COMMIT, so ids commit slightly out of order — 0041's header says a strict tailer
#: must read with a small lag or re-scan. Every writer of that table is a single-statement
#: post-commit transaction, so a few seconds is generous.
#:
#: KNOWN HOLE, ACCEPTED FOR NOW (Aug 2026) — recorded here rather than left to be rediscovered.
#: This is a BET on commit duration, not a guarantee, and when it loses it loses SILENTLY and
#: permanently. If a write takes longer than this between taking its sequence value and committing
#: (a saturated pool, a lock wait, a paused container), a later id commits first, the cursor
#: advances past it, and `id > position` never returns the earlier row again. No error, no retry:
#: the automation simply never fires for that event, and nothing detects it.
#:
#: The real fix is to stop asking "how old is this row" and ask "has it settled": take
#: ``pg_current_snapshot()``, and only advance the cursor past a batch once every transaction that
#: was in flight when the batch was read has FINISHED. Exact rather than probabilistic, and
#: self-adjusting — near-zero wait when commits are fast, as long as needed when one is slow.
#:
#: Do NOT reach for the one-line version ("process rows whose ``xmin`` is below the snapshot's
#: ``pg_snapshot_xmin``"): it silently reintroduces the same bug, because transaction-id order and
#: sequence order can disagree. The worked counter-example, and the ``(commit_ts, id)`` alternative
#: that cannot skip by construction, are written up once on
#: ``app/notifications/consumer.py::LAG_SECONDS`` — read that before implementing either here.
#:
#: The identical hole exists in ``app/notifications/consumer.py`` (same shape, same constant). A fix
#: belongs in BOTH or the surviving one keeps the hole. Deliberately not fixed in the same change as
#: the events-rework work: this is the reliability-critical tail and wants its own tests.
LAG_SECONDS = 5

#: How long a rate/concurrency-deferred firing waits before its next attempt.
DEFER_SECONDS = 30
#: After this many deferrals (~15 minutes at the interval above) the deferral itself becomes worth
#: a human's attention: an operations task is raised ONCE and the firing keeps deferring. It is
#: never dropped — a permanently over-limit tenant is a capacity problem, not a reason to lose a
#: client's chaser.
DEFER_ESCALATE_AT = 30
#: How many deferred rows one pass re-attempts.
DEFER_BATCH = 10

#: Scheduled firings only fire NEAR their slot. Without this a worker that was down all morning
#: would fire the 09:00 review sweep at 23:00, which is worse than not firing it. It is also the
#: width of the catch-up window: slots inside it collapse to the latest one (see schedule_slot).
SCHEDULE_GRACE_MINUTES = 30

#: The role, in Graphene's OPERATIONS tenant, that safety-cap escalations are assigned to. The
#: task belongs to operations, not to the firm (the ``app/aml/screening.py`` precedent): a firm
#: cannot diagnose its own runaway automation chain, and the cap existing is our control.
OPS_ROLE_SLUG = "compliance"

#: Execution statuses that count as "live" for the flow-concurrency cap. Expressed as the
#: TERMINAL set so a new intermediate state is counted by default — the safe direction for a cap.
_TERMINAL_EXECUTION_STATES = ("completed", "cancelled", "failed")


# ---------------------------------------------------------------------------------------------
# Per-tenant limits
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Limits:
    """The safety numbers in force for one tenant: contract defaults, per-tenant overrides,
    clamped to ``triggers.CEILINGS``. An override may tune a guard; it can never disarm one."""

    paused: bool
    timezone: str
    executions_per_minute: int
    flow_concurrency: int
    case_trigger_budget: int
    max_chain_depth: int
    default_rearm_minutes: int


def _clamp(value: Any, default: int, ceiling: int | None) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return default
    return min(value, ceiling) if ceiling is not None else value


async def limits_for(session: AsyncSession, tenant_id: int) -> Limits:
    """Resolve one tenant's effective limits. No settings row ⇒ the contract defaults."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT paused, timezone, executions_per_minute, flow_concurrency, "
                    "       case_trigger_budget, max_chain_depth, default_rearm_minutes "
                    "FROM flows.automation_settings WHERE tenant_id = :t"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    safety = triggers.SAFETY
    ceilings = triggers.CEILINGS
    if row is None:
        return Limits(
            paused=False,
            timezone="Europe/London",
            executions_per_minute=int(safety["tenantExecutionsPerMinute"]),
            flow_concurrency=int(safety["flowConcurrency"]),
            case_trigger_budget=int(safety["caseTriggerBudget"]),
            max_chain_depth=int(safety["maxChainDepth"]),
            default_rearm_minutes=int(safety["defaultRearmMinutes"]),
        )
    return Limits(
        paused=bool(row["paused"]),
        timezone=str(row["timezone"] or "Europe/London"),
        executions_per_minute=_clamp(
            row["executions_per_minute"],
            int(safety["tenantExecutionsPerMinute"]),
            ceilings["tenantExecutionsPerMinute"],
        ),
        flow_concurrency=_clamp(
            row["flow_concurrency"], int(safety["flowConcurrency"]), ceilings["flowConcurrency"]
        ),
        case_trigger_budget=_clamp(
            row["case_trigger_budget"],
            int(safety["caseTriggerBudget"]),
            ceilings["caseTriggerBudget"],
        ),
        max_chain_depth=_clamp(
            row["max_chain_depth"], int(safety["maxChainDepth"]), ceilings["maxChainDepth"]
        ),
        # No ceiling: a longer re-arm window only makes automations fire LESS.
        default_rearm_minutes=_clamp(
            row["default_rearm_minutes"], int(safety["defaultRearmMinutes"]), None
        ),
    )


def zone(name: str) -> ZoneInfo:
    """The firm's timezone, or UTC if the stored name is not a real zone. Never raises: a typo in
    a settings row must not stop every OTHER tenant's schedule pass."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("unknown automation timezone %r — falling back to UTC", name)
        return UTC  # type: ignore[return-value]


# ---------------------------------------------------------------------------------------------
# Subject resolution — what one event is ABOUT
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SubjectSource:
    """How to get an event's subject id out of the event row.

    Four strategies, in the order they are tried. Everything is best-effort: an event whose
    subject cannot be resolved still fires an automation whose target does not need one (a rule
    that emails the firm), and fails cleanly for one that does.
    """

    #: A matched path param carrying the id directly (``/firm/contacts/{contact_id}/...``).
    path_param: str | None = None
    #: A field on ``metadata.subject`` (the action stream's allowlisted subject capture). A list
    #: value takes its FIRST element — a send to several clients names one subject, and
    #: per-recipient fan-out (one event, N firings) is deliberately not smuggled in here.
    subject_field: tuple[str, ...] = ()
    #: A lookup keyed by a path param that carries a uid rather than an id. Binds ``:v`` (the
    #: param) and ``:t`` (the tenant), returns the subject id.
    sql: str | None = None
    sql_param: str | None = None
    #: For CREATE routes, whose events genuinely carry no id: the ``audit.log`` action written by
    #: the DML trigger in the same request. Matched on (tenant, actor, a window ending at the
    #: event) and required to be UNAMBIGUOUS — exactly one row, or no subject. The house rule
    #: (``aml.match_contact``, ``actions.pick_previous``): degrade, never guess.
    created_audit_action: str | None = None


#: How long before the action event the DML row that created the subject may have been written.
#: The action event is written post-commit in the same request, so this is generous.
CREATED_LOOKBACK_SECONDS = 30

#: The five routes below are uid-addressed now (docs/api.md -> "Addressing"), so their events
#: record `{contact_uid}` / `{account_uid}` and the serial that used to sit on the path is gone.
#: `audit.actions` is HISTORY and is never rewritten, so the stream holds both shapes: rows written
#: before the rename carry the id, rows written since carry the uid. Both are declared on every one
#: of these -- `resolve_subject` tries `path_param` first and only falls through to the lookup when
#: it finds nothing, so an old row still resolves by id and a new one by uid.
#:
#: THE COST OF GETTING THIS WRONG IS SILENT. A subject that will not resolve is not an error: the
#: trigger simply reports that the event named no client, so the notification finds no recipients
#: and is never raised, and an automation waiting on that client is never released. The symptom is
#: an event visible in the log with nothing downstream of it -- which is how these five went
#: unnoticed from the rename until a firm asked why assigning an adviser notified nobody.
_CONTACT_BY_UID = "SELECT id FROM crm.contacts WHERE uid = :v AND tenant_id = :t"
_ACCOUNT_BY_UID = "SELECT id FROM platform.accounts WHERE uid = :v AND tenant_id = :t"

_SUBJECTS: dict[str, SubjectSource] = {
    # -- the id off the path (pre-rename rows), else the uid resolved (everything since) --------
    "client.adviser_assigned": SubjectSource(
        path_param="contact_id", sql=_CONTACT_BY_UID, sql_param="contact_uid"
    ),
    "client.portal_user_linked": SubjectSource(
        path_param="contact_id", sql=_CONTACT_BY_UID, sql_param="contact_uid"
    ),
    "instruction.created": SubjectSource(
        path_param="contact_id", sql=_CONTACT_BY_UID, sql_param="contact_uid"
    ),
    "client.bank_account_updated": SubjectSource(
        path_param="contact_id", sql=_CONTACT_BY_UID, sql_param="contact_uid"
    ),
    "account.model_updated": SubjectSource(
        path_param="account_id", sql=_ACCOUNT_BY_UID, sql_param="account_uid"
    ),
    # -- ids the stream already captures as allowlisted subjects ------------------------------
    "document.generated": SubjectSource(subject_field=("client_contact_id",)),
    "document_request.created": SubjectSource(subject_field=("contact_id",)),
    "document.sent": SubjectSource(subject_field=("contact_ids",)),
    # -- uid on the path, id one join away ----------------------------------------------------
    "document_request.item_fulfilled": SubjectSource(
        sql=(
            "SELECT r.contact_id FROM documents.request_items i "
            "JOIN documents.requests r ON r.id = i.request_id "
            "WHERE i.uid = :v AND i.tenant_id = :t"
        ),
        sql_param="item_uid",
    ),
    "document_request.form_submitted": SubjectSource(
        sql=(
            "SELECT r.contact_id FROM documents.request_items i "
            "JOIN documents.requests r ON r.id = i.request_id "
            "WHERE i.uid = :v AND i.tenant_id = :t"
        ),
        sql_param="item_uid",
    ),
    "instruction.cancelled": SubjectSource(
        sql="SELECT contact_id FROM platform.instructions WHERE uid = :v AND tenant_id = :t",
        sql_param="uid",
    ),
    "journey.started": SubjectSource(
        sql="SELECT id FROM crm.applications WHERE uid = :v AND tenant_id = :t",
        sql_param="app_uid",
    ),
    "task.completed": SubjectSource(
        sql="SELECT id FROM tasks.tasks WHERE uid = :v AND tenant_id = :t",
        sql_param="uid",
    ),
    # -- a creation: the id exists only in the response, which the stream never captures -------
    "client.created": SubjectSource(created_audit_action="crm.contacts.insert"),
    # DELIBERATE NON-ENTRY: task.created. Same shape as the contact create above, but
    # tasks.tasks is not on audit.attach, so there is no DML row to match against and no honest
    # way to name the task that was raised. The trigger still fires (a rule that pings a channel
    # on any new task is useful); a target that needs the task itself gets no subject and says so.
}


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def events_with_subject_source() -> frozenset[str]:
    """Event types a SUBJECT can be resolved from — i.e. the ones that can name a client or account.

    The public read of ``_SUBJECTS``, because it is the real gate on whether a notification can
    reach anyone but the actor and two other modules need to ask. Everything downstream of a
    client/adviser audience (`notifications/catalogue.py`'s generic resolvers, and the event
    registry's classification) funnels through ``resolve_subject``, so an event absent from here
    resolves to nobody however much its route or metadata appears to name something.

    Not the same set as ``event_schema.REGISTRY``'s subject-bearing events: that records what an
    event CAPTURES in metadata, and a capture with no source declared here is unreachable. The
    registry surfaces that difference as its own state, because closing it is a declaration rather
    than new capture.
    """
    return frozenset(_SUBJECTS)


async def resolve_subject(
    session: AsyncSession, spec: TriggerSpec, event: Mapping[str, Any], *, tenant_id: int
) -> int | None:
    """The subject id one event names, or ``None``. Never raises.

    ``tenant_id`` is the firm the event ACTED ON — ``handle_event``'s
    ``target_tenant_id or tenant_id`` — and it is passed in rather than read off the event on
    purpose. When a Graphene operator works a firm's book with ``?tenant=``, the row lives in the
    TARGET firm; binding the event's own ``tenant_id`` (the operator's operations tenant) made the
    six lookups below find nothing. Not a leak — the wrong tenant returns no row, never another
    firm's — but a silent one: the trigger reported "this did not name a client", action
    automations refused, the re-arm window collapsed to per-automation, and
    ``timers.release_listeners`` short-circuited on a null subject, so a case parked on
    ``wait_for_event`` was never released by an operator fulfilling that client's request.
    """
    source = _SUBJECTS.get(str(event["event_type"]))
    if source is None:
        return None
    path_params = event.get("path_params") or {}
    metadata = event.get("metadata") or {}
    subject = metadata.get("subject") or {}

    raw: Any = None
    if source.path_param:
        raw = path_params.get(source.path_param)
    for field_name in source.subject_field:
        if raw is None:
            raw = _first(subject.get(field_name))
    if raw is None and source.sql and source.sql_param:
        value = path_params.get(source.sql_param)
        if value is not None:
            raw = (
                await session.execute(text(source.sql), {"v": str(value), "t": tenant_id})
            ).scalar_one_or_none()
    if raw is None and source.created_audit_action:
        raw = await _created_subject(
            session, source.created_audit_action, event, tenant_id=tenant_id
        )
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


async def _created_subject(
    session: AsyncSession, audit_action: str, event: Mapping[str, Any], *, tenant_id: int
) -> int | None:
    """The id of the row this create-shaped event created, via the DML audit trail.

    Exactly-one-or-nothing: two clients created by the same adviser inside the lookback window
    are ambiguous, and an automation that starts an onboarding journey for *probably* the right
    client is worse than one that does not start. A bulk import therefore fires nothing, which is
    the safe direction.

    ``tenant_id`` is the acted-on firm, not ``event["tenant_id"]`` — see ``resolve_subject``. The
    ``audit.log`` row a proxied create writes is stamped with the TARGET firm, so the actor's own
    tenant matches nothing. The actor predicate is unchanged and still does the disambiguating.
    """
    since = event["created_at"] - timedelta(seconds=CREATED_LOOKBACK_SECONDS)
    rows = (
        (
            await session.execute(
                text(
                    "SELECT entity_id FROM audit.log "
                    "WHERE action = :a AND tenant_id = :t "
                    "  AND user_id IS NOT DISTINCT FROM CAST(:actor AS uuid) "
                    "  AND created_at > :since AND created_at <= :until "
                    "  AND entity_id IS NOT NULL LIMIT 2"
                ),
                {
                    "a": audit_action,
                    "t": tenant_id,
                    "actor": str(event["actor_id"]) if event["actor_id"] else None,
                    "since": since,
                    "until": event["created_at"],
                },
            )
        )
        .scalars()
        .all()
    )
    return int(rows[0]) if len(rows) == 1 else None


# ---------------------------------------------------------------------------------------------
# The condition context
# ---------------------------------------------------------------------------------------------


async def build_facts(
    session: AsyncSession,
    *,
    spec: TriggerSpec,
    event: Mapping[str, Any] | None,
    tenant_id: int,
    subject_id: int | None,
    tz: ZoneInfo,
    now: datetime,
    case_uid: str | None = None,
) -> dict[str, Any]:
    """The flat dotted-path context A2's evaluator reads (``app/flows/conditions.py``).

    ``event.*`` and ``time.*`` are always present; ``contact.*``/``account.*`` are filled when the
    trigger's subject resolves to one. ``application.*``, the rest of ``case.*`` and ``memory.*``
    are NOT populated here and that is correct rather than missing: they describe the inside of a
    running case, which is what a runtime branch node evaluates against (Phase B), not what an
    entry condition can see at the moment an event arrives. An unknown field yields ``None``, so
    only ``isEmpty``/``notEquals`` can match it — never an exception, never a silent true.

    ``case.tags`` is the ONE exception to that boundary, added with the rule tier. Tags are the
    substrate automations compose through — one rule tags a case, another triggers on it — so a
    tag that could not be READ at entry would make the composition one-directional and the
    feature half a feature. It is also cheap and true at this instant, which the derived
    ``case.status`` is not.
    """
    local = now.astimezone(tz)
    facts: dict[str, Any] = {
        "event.type": event["event_type"] if event else None,
        "event.actor_id": str(event["actor_id"]) if event and event["actor_id"] else None,
        "time.hour": local.hour,
        # Python's Monday=0..Sunday=6, the same convention the schedule config uses.
        "time.weekday": local.weekday(),
    }
    if event:
        for key, value in ((event.get("metadata") or {}).get("subject") or {}).items():
            facts[f"event.subject.{key}"] = value
        for key, value in (event.get("path_params") or {}).items():
            facts.setdefault(f"event.subject.{key}", value)

    if case_uid:
        case_id = (
            await session.execute(
                text("SELECT id FROM flows.cases WHERE uid = :u AND tenant_id = :t"),
                {"u": case_uid, "t": tenant_id},
            )
        ).scalar_one_or_none()
        if case_id is not None:
            facts["case.tags"] = await tags.list_for(
                session, target="case", target_id=int(case_id), tenant_id=tenant_id
            )

    if subject_id is None:
        return facts
    if spec.subject == "contact":
        row = (
            (
                await session.execute(
                    text(
                        "SELECT c.type::text AS type, c.service_type::text AS service_type, "
                        "       c.sub_type::text AS sub_type, c.enabled, c.nationality, "
                        "       EXISTS (SELECT 1 FROM crm.contact_users cu "
                        "               WHERE cu.contact_id = c.id) AS has_login "
                        "FROM crm.contacts c WHERE c.id = :id AND c.tenant_id = :t"
                    ),
                    {"id": subject_id, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            facts.update({f"contact.{k}": v for k, v in row.items()})
            # contact.tags — a list, so `contains`/`in`/`matches` all mean what an author expects
            # (conditions.matches walks a list and matches if any member does). A tagless client
            # gets `[]` rather than a missing key, so `isEmpty`/`notContains` mean the same thing
            # for "no tags" as they would for any other empty value.
            #
            # Inside the `row is not None` branch on purpose: when the contact does NOT exist,
            # every `contact.*` key must stay absent. That is a load-bearing invariant elsewhere —
            # the rules API's dry-run reports "no client found" by testing whether any key in the
            # subject's scope is present, and a tags key written unconditionally would make a
            # deleted client look like a matched one.
            facts["contact.tags"] = await tags.list_for(
                session, target="contact", target_id=subject_id, tenant_id=tenant_id
            )
    elif spec.subject == "account":
        row = (
            (
                await session.execute(
                    text(
                        "SELECT p.product_type::text AS product, a.custody::text AS custody, "
                        "       a.status, (a.transfer IS NOT NULL) AS has_transfer, "
                        "       (a.model_id IS NOT NULL) AS discretionary "
                        "FROM platform.accounts a "
                        "LEFT JOIN platform.products p ON p.id = a.product_id "
                        "WHERE a.id = :id AND a.tenant_id = :t"
                    ),
                    {"id": subject_id, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            facts.update({f"account.{k}": v for k, v in row.items()})
    return facts


async def _case_uid_for(
    session: AsyncSession,
    *,
    event: Mapping[str, Any] | None,
    spec: TriggerSpec,
    subject_id: int | None,
    tenant_id: int,
) -> str | None:
    """The case the per-case trigger budget is counted over.

    An explicit ``case_uid`` path param when the event carries one; otherwise, for a contact
    subject, that client's live case. That is the identity the budget is actually about — "how
    many automations have fired inside this client's journey" — and it is what makes a chain that
    churns on one client hit a ceiling rather than run all night.

    The tenant predicate is not decoration. This runs on a ``service_role`` session with RLS
    bypassed, so it is the only thing scoping the query at all; every sibling query here forces the
    tenant and this one used to lean on contact ids happening to be globally unique. A case naming
    a contact outside its own tenant would have had its budget counted against the wrong firm.
    """
    if event:
        explicit = (event.get("path_params") or {}).get("case_uid")
        if explicit:
            return str(explicit)
    if subject_id is None or spec.subject != "contact":
        return None
    return (
        await session.execute(
            text(
                "SELECT uid FROM flows.cases WHERE subject_contact_id = :c AND tenant_id = :t "
                "  AND completed_at IS NULL AND cancelled_at IS NULL ORDER BY id DESC LIMIT 1"
            ),
            {"c": subject_id, "t": tenant_id},
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------------------------
# Deliveries
# ---------------------------------------------------------------------------------------------


async def _already_delivered(session: AsyncSession, automation_id: int, event_key: str) -> bool:
    return (
        await session.execute(
            text(
                "SELECT 1 FROM flows.automation_deliveries "
                "WHERE automation_id = :a AND event_key = :k"
            ),
            {"a": automation_id, "k": event_key},
        )
    ).first() is not None


async def _record(
    session: AsyncSession,
    *,
    automation_id: int,
    tenant_id: int,
    event_key: str,
    event_id: int | None,
    outcome: str,
    subject_kind: str | None = None,
    subject_id: int | None = None,
    case_uid: str | None = None,
    chain_depth: int = 0,
    detail: str | None = None,
    error: str | None = None,
    retry_after: datetime | None = None,
    attempts: int = 0,
    context: Mapping[str, Any] | None = None,
) -> None:
    """Write (or re-settle) one delivery. Upsert rather than insert: a deferred row is re-settled
    in place on its next attempt, which is what keeps ``(automation_id, event_key)`` a genuine
    idempotency key across the deferral path as well as the redelivery one."""
    await session.execute(
        text(
            "INSERT INTO flows.automation_deliveries "
            "  (automation_id, tenant_id, event_id, event_key, subject_kind, subject_id, "
            "   case_uid, outcome, detail, error, chain_depth, attempts, retry_after, context) "
            "VALUES (:a, :t, :eid, :k, :sk, :sid, :cu, :o, :d, :e, :cd, :att, :ra, "
            "        CAST(:ctx AS jsonb)) "
            "ON CONFLICT (automation_id, event_key) DO UPDATE SET "
            "  outcome = EXCLUDED.outcome, detail = EXCLUDED.detail, error = EXCLUDED.error, "
            "  case_uid = COALESCE(EXCLUDED.case_uid, flows.automation_deliveries.case_uid), "
            "  attempts = EXCLUDED.attempts, retry_after = EXCLUDED.retry_after"
        ),
        {
            "a": automation_id,
            "t": tenant_id,
            "eid": event_id,
            "k": event_key,
            "sk": subject_kind,
            "sid": str(subject_id) if subject_id is not None else None,
            "cu": case_uid,
            "o": outcome,
            "d": detail,
            "e": error,
            "cd": chain_depth,
            "att": attempts,
            "ra": retry_after,
            "ctx": json.dumps(dict(context or {}), default=str),
        },
    )


# ---------------------------------------------------------------------------------------------
# Operations escalation
# ---------------------------------------------------------------------------------------------


async def raise_ops_task(
    session: AsyncSession, *, title: str, description: str, source_ref: str
) -> None:
    """Put a safety-cap breach in front of a Graphene operator.

    Exhausting a budget or the depth cap must not fail silently — that is the whole difference
    between a guard and a shrug. The task lands in the OPERATIONS tenant's inbox (the
    ``app/aml/screening.py`` precedent): the firm cannot diagnose a runaway chain, and the cap is
    our control, not theirs. Failing to raise it is itself reported rather than swallowed — but it
    never propagates, because an escalation that cannot be filed must not also stop the loop.
    """
    try:
        ops_tenant = await environments.operations_tenant_id(session)
    except LookupError:
        ops_tenant = None
    role_id = None
    if ops_tenant is not None:
        role_id = (
            await session.execute(
                text("SELECT id FROM auth.roles WHERE tenant_id = :t AND slug = :s"),
                {"t": ops_tenant, "s": OPS_ROLE_SLUG},
            )
        ).scalar_one_or_none()
    if ops_tenant is None or role_id is None:
        report_error(
            "an automation safety cap fired but no operations task could be raised",
            log=logger,
            event="automation_escalation_undeliverable",
            reason=("no operations tenant" if ops_tenant is None else f"no {OPS_ROLE_SLUG!r} role"),
            title=title,
        )
        return
    try:
        # A SAVEPOINT of its own: this runs inside the consumer's batch transaction, so a rejected
        # INSERT (a constraint we did not anticipate, a role that vanished mid-pass) would
        # otherwise poison the whole batch — losing the delivery bookkeeping of every event in it
        # for the sake of a notification. The escalation is important; it is not more important
        # than the guard's own record of what it refused.
        async with session.begin_nested():
            await tasks_service.create_task(
                session,
                tenant_id=int(ops_tenant),
                assignee_kind="role",
                assignee_role_id=int(role_id),
                title=title,
                description=description,
                category="generic",
                priority="high",
                source_kind="automation",
                source_ref=source_ref,
            )
    except Exception as exc:  # noqa: BLE001 — never let an escalation break the pass it reports
        report_error(
            "an automation safety cap fired but the operations task was rejected",
            exc=exc,
            log=logger,
            event="automation_escalation_rejected",
            title=title,
        )


# ---------------------------------------------------------------------------------------------
# The guards + dispatch for one candidate
# ---------------------------------------------------------------------------------------------

#: Outcomes that END the event: the turn does not pass to a lower-priority automation.
_TERMINAL_OUTCOMES = frozenset(
    {"dispatched", "deferred", "budget_exhausted", "depth_exceeded", "failed"}
)


@dataclass(frozen=True)
class Candidate:
    """One armed automation being considered for one firing."""

    id: int
    uid: str
    name: str
    tenant_id: int
    trigger_key: str
    conditions: Any
    flow_id: int | None
    action: Mapping[str, Any] | None
    config: Mapping[str, Any]
    rearm_minutes: int | None
    allow_automation_source: bool

    @property
    def kind(self) -> str:
        """Which dispatch target runs it. The CHECK constraint guarantees exactly one is set."""
        return "flow" if self.flow_id is not None else "action"

    @property
    def spec(self) -> Mapping[str, Any]:
        """The target's own payload."""
        return {"flow_id": self.flow_id} if self.flow_id is not None else dict(self.action or {})


def _row_to_candidate(row: Mapping[str, Any]) -> Candidate:
    return Candidate(
        id=int(row["id"]),
        uid=str(row["uid"]),
        name=str(row["name"]),
        tenant_id=int(row["tenant_id"]),
        trigger_key=str(row["trigger_key"]),
        conditions=row["conditions"],
        flow_id=int(row["flow_id"]) if row["flow_id"] is not None else None,
        action=row["action"],
        config=row["config"] or {},
        rearm_minutes=row["rearm_minutes"],
        allow_automation_source=bool(row["allow_automation_source"]),
    )


async def _rearmed(
    session: AsyncSession,
    candidate: Candidate,
    *,
    spec: TriggerSpec,
    subject_id: int | None,
    window: int,
    now: datetime,
) -> bool:
    """Is this automation still inside its re-arm window for THIS subject?

    A firing with no resolvable subject is windowed on the automation alone (``subject_id IS
    NULL``), which is the conservative reading: without an identity we cannot tell two clients
    apart, so we throttle rather than risk a storm.

    ``updated_at`` rather than ``created_at``, for the reason ``_dispatched_in_window`` sets out:
    a deferred firing's row is older than the firing.
    """
    if window <= 0:
        return False
    return (
        await session.execute(
            text(
                "SELECT 1 FROM flows.automation_deliveries "
                "WHERE automation_id = :a AND outcome = 'dispatched' "
                "  AND subject_kind IS NOT DISTINCT FROM :sk "
                "  AND subject_id IS NOT DISTINCT FROM :sid "
                "  AND updated_at > :since LIMIT 1"
            ),
            {
                "a": candidate.id,
                "sk": spec.subject if subject_id is not None else None,
                "sid": str(subject_id) if subject_id is not None else None,
                "since": now - timedelta(minutes=window),
            },
        )
    ).first() is not None


async def _dispatched_in_window(session: AsyncSession, tenant_id: int, now: datetime) -> int:
    """How many of this firm's automations have actually RUN in the last minute.

    Windowed on ``updated_at``, not ``created_at``, and the difference is the whole guard. A
    delivery row is written when the firing is first considered and re-settled in place when a
    deferral finally runs (``_record``'s upsert), so ``created_at`` is when the event ARRIVED. A
    firing deferred for ten minutes and then dispatched kept a ten-minute-old ``created_at`` and
    never counted against the window — which meant the rate limit leaked hardest under sustained
    overflow, the exact condition it exists for: ``run_deferred_once`` drains ``DEFER_BATCH`` rows
    every couple of seconds, and none of them counted.

    ``updated_at`` is maintained by the table's ``set_updated_at`` trigger, and ``dispatched`` is
    terminal for a delivery (nothing re-settles a row that ran), so for the rows this counts it is
    exactly the moment the automation fired.
    """
    return int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM flows.automation_deliveries "
                    "WHERE tenant_id = :t AND outcome = 'dispatched' AND updated_at > :since"
                ),
                {"t": tenant_id, "since": now - timedelta(minutes=1)},
            )
        ).scalar_one()
    )


async def _case_firings(session: AsyncSession, case_uid: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM flows.automation_deliveries "
                    "WHERE case_uid = :c AND outcome = 'dispatched'"
                ),
                {"c": case_uid},
            )
        ).scalar_one()
    )


async def _live_executions(session: AsyncSession, tenant_id: int) -> int:
    return int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM flows.executions "
                    "WHERE tenant_id = :t AND status <> ALL(:done)"
                ),
                {"t": tenant_id, "done": list(_TERMINAL_EXECUTION_STATES)},
            )
        ).scalar_one()
    )


async def consider(
    session: AsyncSession,
    candidate: Candidate,
    *,
    spec: TriggerSpec,
    limits: Limits,
    event_key: str,
    event: Mapping[str, Any] | None,
    event_id: int | None,
    subject_id: int | None,
    case_uid: str | None,
    chain_depth: int,
    facts: Mapping[str, Any],
    automation_sourced: bool,
    now: datetime,
    attempts: int = 0,
) -> str:
    """Run the guards for ONE candidate and dispatch if they all pass. Returns the outcome.

    The caller stops walking the priority order when the outcome is terminal (see
    ``_TERMINAL_OUTCOMES``) — that is first-satisfiable-wins.
    """
    subject_kind = spec.subject if subject_id is not None else None

    def _rec(outcome: str, **kw: Any) -> Any:
        """Record this candidate's decision. The per-firing constants are bound once here so each
        guard below reads as the decision it is making, not as ten lines of bookkeeping."""
        fields: dict[str, Any] = {
            "subject_kind": subject_kind,
            "subject_id": subject_id,
            "case_uid": case_uid,
            "chain_depth": chain_depth,
            **kw,  # a caller may override any of them (a dispatch names the case it created)
        }
        return _record(
            session,
            automation_id=candidate.id,
            tenant_id=candidate.tenant_id,
            event_key=event_key,
            event_id=event_id,
            outcome=outcome,
            **fields,
        )

    # (2) The self-trigger guard. THE default failure mode: our stream emits an event for
    # everything an automation does, so without this a rule that tags a client on tag-applied
    # would tag them for ever.
    if automation_sourced and not candidate.allow_automation_source:
        await _rec("skipped_automation", detail="the event was produced by an automation")
        return "skipped_automation"

    # (3) Chain depth, which applies even to an automation that opted in above.
    if chain_depth > limits.max_chain_depth:
        await _rec(
            "depth_exceeded",
            detail=f"chain depth {chain_depth} exceeds the cap of {limits.max_chain_depth}",
        )
        await raise_ops_task(
            session,
            title=f"Automation chain depth cap hit: {candidate.name}",
            description=(
                f"“{candidate.name}” ({candidate.uid}) was reached at chain depth {chain_depth}, "
                f"past the cap of {limits.max_chain_depth}. An automation is very likely feeding "
                "itself. It has NOT run. Review the automation's trigger and its "
                "'allow automation-sourced events' setting."
            ),
            # Keyed on the AUTOMATION, not the event. `tasks_source_ref_key` collapses repeat
            # escalations onto one row, and folding `event_key` in made the key unique per event —
            # so the guard that exists to contain a runaway chain filed one high-priority ops task
            # per over-depth event, into the very inbox it is protecting. A bulk operation
            # spawning many chains was a mailbox flood raised by the flood control. Both sibling
            # guards already collapse correctly (`budget:{case_uid}`, `defer:{uid}`); the fact an
            # automation is over the cap is one fact, however many events hit it.
            source_ref=f"depth:{candidate.uid}",
        )
        return "depth_exceeded"

    # (4) Entry conditions (A2's evaluator; absent ⇒ met).
    if not conditions.evaluate(candidate.conditions, facts):
        await _rec("skipped_conditions", detail="entry conditions were not met")
        return "skipped_conditions"

    # (5) The re-arm window.
    window = (
        candidate.rearm_minutes
        if isinstance(candidate.rearm_minutes, int)
        else limits.default_rearm_minutes
    )
    if await _rearmed(session, candidate, spec=spec, subject_id=subject_id, window=window, now=now):
        await _rec("skipped_rearm", detail=f"already fired for this subject within {window}m")
        return "skipped_rearm"

    # (6) The per-case trigger budget.
    if case_uid is not None:
        fired = await _case_firings(session, case_uid)
        if fired >= limits.case_trigger_budget:
            await _rec(
                "budget_exhausted",
                detail=f"{fired} automations have already fired on case {case_uid}",
            )
            await raise_ops_task(
                session,
                title=f"Automation budget exhausted on case {case_uid}",
                description=(
                    f"{fired} automations have fired inside case {case_uid}, at or past the "
                    f"budget of {limits.case_trigger_budget}. “{candidate.name}” "
                    f"({candidate.uid}) has NOT run. This usually means two automations are "
                    "triggering each other inside one client's journey."
                ),
                source_ref=f"budget:{case_uid}",
            )
            return "budget_exhausted"

    # (7) Rate + concurrency: DEFER, never drop.
    if (
        await _dispatched_in_window(session, candidate.tenant_id, now)
        >= limits.executions_per_minute
    ):
        return await _defer(
            session,
            candidate,
            _rec,
            reason=f"tenant rate limit ({limits.executions_per_minute}/min) reached",
            attempts=attempts,
            now=now,
            context=_defer_context(spec, event_id, subject_id, case_uid, chain_depth, facts),
        )
    if (
        candidate.kind == "flow"
        and await _live_executions(session, candidate.tenant_id) >= limits.flow_concurrency
    ):
        return await _defer(
            session,
            candidate,
            _rec,
            reason=f"flow concurrency cap ({limits.flow_concurrency}) reached",
            attempts=attempts,
            now=now,
            context=_defer_context(spec, event_id, subject_id, case_uid, chain_depth, facts),
        )

    # (8) Dispatch. A SAVEPOINT so a target that raises rolls back only its own work — one bad
    # automation must never wedge the loop or lose the rest of the batch's bookkeeping.
    target = triggers.target_for(candidate.kind)
    if target is None:
        await _rec("failed", error=f"no dispatch target registered for kind '{candidate.kind}'")
        return "failed"
    ctx = AutomationContext(
        tenant_id=candidate.tenant_id,
        automation_id=candidate.id,
        automation_uid=candidate.uid,
        automation_name=candidate.name,
        trigger_key=candidate.trigger_key,
        event_id=event_id,
        event_type=str(event["event_type"]) if event else None,
        subject_kind=subject_kind,
        subject_id=subject_id,
        case_uid=case_uid,
        chain_depth=chain_depth,
        actor_id=str(event["actor_id"]) if event and event["actor_id"] else None,
        config=candidate.config,
        facts=facts,
    )
    try:
        async with session.begin_nested():
            result = await target.run(session, ctx, candidate.spec)
    except Exception as exc:  # noqa: BLE001 — one bad automation must not stop the loop
        report_error(
            "automation dispatch failed; dead-lettered on the delivery row",
            exc=exc,
            log=logger,
            event="automation_dispatch_failed",
            automation_uid=candidate.uid,
            tenant_id=candidate.tenant_id,
            trigger_key=candidate.trigger_key,
        )
        await _rec("failed", error=f"{type(exc).__name__}: {exc}"[:500])
        return "failed"
    if not result.ok:
        await _rec("failed", error=(result.detail or "the automation target refused")[:500])
        return "failed"
    await _rec("dispatched", detail=result.detail, case_uid=result.case_uid or case_uid)
    logger.info(
        "automation fired",
        extra={
            "event": "automation_dispatched",
            "automation_uid": candidate.uid,
            "tenant_id": candidate.tenant_id,
            "trigger_key": candidate.trigger_key,
            "chain_depth": chain_depth,
        },
    )
    return "dispatched"


def _defer_context(
    spec: TriggerSpec,
    event_id: int | None,
    subject_id: int | None,
    case_uid: str | None,
    chain_depth: int,
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    """The snapshot a deferred firing is re-attempted from. Self-contained on purpose: by the time
    the retry runs, the originating event is behind the cursor and may have been pruned."""
    return {
        "trigger_key": spec.key,
        "event_id": event_id,
        "subject_id": subject_id,
        "case_uid": case_uid,
        "chain_depth": chain_depth,
        "facts": dict(facts),
    }


async def _defer(
    session: AsyncSession,
    candidate: Candidate,
    rec: Any,
    *,
    reason: str,
    attempts: int,
    now: datetime,
    context: Mapping[str, Any],
) -> str:
    attempts += 1
    await rec(
        "deferred",
        detail=reason,
        attempts=attempts,
        retry_after=now + timedelta(seconds=DEFER_SECONDS),
        context=context,
    )
    if attempts == DEFER_ESCALATE_AT:
        await raise_ops_task(
            session,
            title=f"Automation stuck behind a limit: {candidate.name}",
            description=(
                f"“{candidate.name}” ({candidate.uid}) has been deferred {attempts} times — "
                f"{reason}. It is still queued and will run, but the tenant has been over its "
                "limit for a sustained period. Check for a runaway automation or raise the limit."
            ),
            source_ref=f"defer:{candidate.uid}",
        )
    return "deferred"


# ---------------------------------------------------------------------------------------------
# First satisfiable wins — the walk, and what happens when a deferred firing breaks its promise
# ---------------------------------------------------------------------------------------------


async def _walk_candidates(
    session: AsyncSession,
    candidates: Sequence[Candidate],
    *,
    spec: TriggerSpec,
    limits: Limits,
    event_key: str,
    event: Mapping[str, Any] | None,
    event_id: int | None,
    subject_id: int | None,
    case_uid: str | None,
    chain_depth: int,
    facts: Mapping[str, Any],
    automation_sourced: bool,
    now: datetime,
) -> str | None:
    """Offer one event to each armed automation in priority order until one takes it.

    A candidate that already holds a delivery for this ``event_key`` is skipped rather than
    reconsidered — that is what makes at-least-once redelivery of the same stream row a no-op, and
    it is also what lets ``_pass_the_turn`` re-run this walk over the whole candidate list without
    double-firing anything that already had its turn.
    """
    for candidate in candidates:
        if await _already_delivered(session, candidate.id, event_key):
            continue  # at-least-once redelivery: this candidate already had its turn
        outcome = await consider(
            session,
            candidate,
            spec=spec,
            limits=limits,
            event_key=event_key,
            event=event,
            event_id=event_id,
            subject_id=subject_id,
            case_uid=case_uid,
            chain_depth=chain_depth,
            facts=facts,
            automation_sourced=automation_sourced,
            now=now,
        )
        if outcome in _TERMINAL_OUTCOMES:
            return outcome  # first satisfiable wins
    return None


async def _pass_the_turn(
    session: AsyncSession,
    *,
    tenant_id: int,
    spec: TriggerSpec,
    limits: Limits,
    event_key: str,
    event_id: int | None,
    context: Mapping[str, Any],
    now: datetime,
) -> None:
    """Give a deferred event's turn to the automations that were skipped behind it.

    ``deferred`` is terminal (``_TERMINAL_OUTCOMES``) on a PROMISE: the firing will run, so a
    lower-priority rule must not run alongside one that is merely queued. When the promise breaks,
    the turn has to move on — and nothing else will ever move it, because by the time the deferral
    drains, the originating event is far behind the consumer's cursor.

    It breaks more often than the deferral path admits. The automation is disabled while its
    firing sits in the queue; the re-arm window has closed over it; its conditions read differently
    against the snapshotted facts on the retry. In every one of those the firing settles as a skip,
    and until now every other automation armed on that trigger silently lost the event.

    Only for STREAM firings (``action:``). A scheduled firing holds nobody's turn —
    ``schedule_once`` offers each due schedule independently, with no priority order between them —
    so handing one on would invent a race that tier does not have.
    """
    if not event_key.startswith("action:"):
        return
    facts = context.get("facts") or {}
    event_type = facts.get("event.type") if isinstance(facts, Mapping) else None
    # The same projection handle_event used. Falling back to the automation's own key keeps a
    # firing whose snapshot predates this field from losing its turn outright.
    keys = triggers.keys_for_event(str(event_type)) if event_type else ()
    candidates = await _candidates(session, tenant_id, keys or (spec.key,))
    if not candidates:
        return
    subject_id = context.get("subject_id")
    await _walk_candidates(
        session,
        candidates,
        spec=spec,
        limits=limits,
        event_key=event_key,
        event=None,
        event_id=event_id,
        subject_id=int(subject_id) if isinstance(subject_id, int) else None,
        case_uid=context.get("case_uid"),
        chain_depth=int(context.get("chain_depth") or 0),
        facts=facts if isinstance(facts, Mapping) else {},
        # As on the deferral's own retry: the self-trigger decision was taken when the event was
        # first read, and the marker is not in the snapshot to re-ask it with.
        automation_sourced=False,
        now=now,
    )


# ---------------------------------------------------------------------------------------------
# Pass 1 — tail the action stream
# ---------------------------------------------------------------------------------------------


def _automation_marker(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The chain marker an automation stamps on the events it produces:
    ``metadata.automation = {"source": "rule"|"flow", "id": <uid>, "chain_depth": int}`` — the
    shape agreed with the action executor. Anything else shaped wrong is treated as ABSENT rather
    than as trusted, which fails towards "this is a human action" — the conservative direction is
    the other one, so a malformed marker is logged."""
    marker = (event.get("metadata") or {}).get("automation")
    if marker is None:
        return None
    if not isinstance(marker, dict):
        logger.warning("ignoring a malformed automation marker on action event %s", event["id"])
        return None
    return marker


async def _candidates(
    session: AsyncSession, tenant_id: int, trigger_keys: tuple[str, ...]
) -> list[Candidate]:
    """Armed automations for this tenant on these trigger keys, in the ONE total order (priority,
    then id — the tiebreak that makes the order total without forcing distinct priorities)."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, name, tenant_id, trigger_key, conditions, flow_id, action, "
                    "       config, rearm_minutes, allow_automation_source "
                    "FROM flows.automations "
                    "WHERE tenant_id = :t AND enabled AND trigger_key = ANY(:keys) "
                    "ORDER BY priority, id"
                ),
                {"t": tenant_id, "keys": list(trigger_keys)},
            )
        )
        .mappings()
        .all()
    )
    return [_row_to_candidate(dict(r)) for r in rows]


async def handle_event(session: AsyncSession, event: Mapping[str, Any], *, now: datetime) -> None:
    """Consider one ``audit.actions`` row against the armed automations of the firm it touched."""
    keys = triggers.keys_for_event(str(event["event_type"]))
    if not keys:
        return  # most of the stream is not on the curated menu — that is the point of the menu

    # WHICH firm's automations? The book target when the action was proxied (a Graphene operator
    # acting on a firm's book with `?tenant=` changed THAT firm's records, and it is that firm's
    # automations which should respond), otherwise the actor's own tenant.
    tenant_id = event["target_tenant_id"] or event["tenant_id"]
    if tenant_id is None:
        return

    # One spec per event; when two friendly keys ever project the same event, the first in
    # registry order wins the subject/context (they differ only in labelling today).
    spec = triggers.TRIGGERS[keys[0]]
    if not triggers.supported(spec):
        return  # a domain-sourced trigger nothing emits yet — see triggers.PENDING_DOMAIN_EVENTS
    subject_id = await resolve_subject(session, spec, event, tenant_id=int(tenant_id))

    # ---- the Flows v3 wait_for_event listener (B4) -------------------------------------------
    # A running journey parked on a `wait_for_event` node is released by exactly the events this
    # consumer already reads, so it hooks in here rather than growing a second tailer of the same
    # stream. Three placements are deliberate:
    #
    #  * BEFORE the candidate check — a waiting case is not an armed automation, and a firm with no
    #    rules at all can still have journeys listening.
    #  * BEFORE the `limits.paused` check — pausing AUTOMATIONS stops new rules from firing; it
    #    must not freeze journeys that are already running, which would be an unrelated (and
    #    unrecoverable-looking) outage from the firm's point of view.
    #  * With a LAZY fact loader — building the condition context costs a query or two, and only a
    #    listener that actually carries extra conditions needs one.
    #
    # Cost on the hot path is one probe of a partial index, and only for the small slice of the
    # stream that is on the curated trigger menu at all.
    async def _listener_facts() -> Mapping[str, Any]:
        listener_limits = await limits_for(session, int(tenant_id))
        return await build_facts(
            session,
            spec=spec,
            event=event,
            tenant_id=int(tenant_id),
            subject_id=subject_id,
            tz=zone(listener_limits.timezone),
            now=now,
        )

    await timers.release_listeners(
        session,
        tenant_id=int(tenant_id),
        trigger_keys=keys,
        subject_kind=spec.subject if subject_id is not None else None,
        subject_id=subject_id,
        facts=_listener_facts,
        event_id=int(event["id"]),
    )
    # ------------------------------------------------------------------------------------------

    candidates = await _candidates(session, int(tenant_id), keys)
    if not candidates:
        return

    limits = await limits_for(session, int(tenant_id))
    event_key = f"action:{int(event['id'])}"
    if limits.paused:
        # Record ONCE against the highest-priority candidate and stop. Recording every candidate
        # would be noise, but recording nothing would leave "why didn't it fire?" unanswerable.
        head = candidates[0]
        if not await _already_delivered(session, head.id, event_key):
            await _record(
                session,
                automation_id=head.id,
                tenant_id=int(tenant_id),
                event_key=event_key,
                event_id=int(event["id"]),
                outcome="skipped_paused",
                detail="automations are paused for this firm",
            )
        return

    marker = _automation_marker(event)
    raw_depth = marker.get("chain_depth") if marker else None
    chain_depth = (int(raw_depth) + 1) if isinstance(raw_depth, int) else (1 if marker else 0)

    case_uid = await _case_uid_for(
        session, event=event, spec=spec, subject_id=subject_id, tenant_id=int(tenant_id)
    )
    tz = zone(limits.timezone)
    facts = await build_facts(
        session,
        spec=spec,
        event=event,
        tenant_id=int(tenant_id),
        subject_id=subject_id,
        tz=tz,
        now=now,
        case_uid=case_uid,
    )

    await _walk_candidates(
        session,
        candidates,
        spec=spec,
        limits=limits,
        event_key=event_key,
        event=event,
        event_id=int(event["id"]),
        subject_id=subject_id,
        case_uid=case_uid,
        chain_depth=chain_depth,
        facts=facts,
        automation_sourced=marker is not None,
        now=now,
    )


async def _ensure_cursor(session: AsyncSession) -> None:
    """Create this consumer's cursor row on first run, parked at the CURRENT head.

    Lazily rather than in the migration for two reasons, and both matter. Mechanically:
    ``flows.automation_cursor`` is service_role-only with no policy, and FORCE RLS binds the table
    owner, so the migrator role cannot insert into it at all — only a BYPASSRLS role can, which is
    this one. Semantically (the ``app/events.py`` precedent): a consumer's first run defines its
    own starting position, and starting at 0 would replay the entire history of the action stream
    the moment a firm arms its first automation. Automations act on what happens next.
    """
    await session.execute(
        text(
            "INSERT INTO flows.automation_cursor (consumer, position) "
            "VALUES (:c, COALESCE((SELECT max(id) FROM audit.actions), 0)) "
            "ON CONFLICT (consumer) DO NOTHING"
        ),
        {"c": CONSUMER},
    )


async def consume_once(
    maker: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> bool:
    """One pass of the action-stream tail. Returns True when the batch was full (more to do).

    The whole pass is ONE transaction: the deliveries and the cursor advance commit together, so a
    crash mid-batch replays the batch rather than losing it — and the deliveries' unique key makes
    that replay a no-op. The cursor row is claimed ``FOR UPDATE SKIP LOCKED``, so a second worker
    finds nothing to do rather than double-consuming.

    **The head-of-line cost that follows from that, said out loud**: one transaction for the batch
    means one slow firing blocks the batch, and the batch blocks EVERY tenant's automations —
    there is one consumer for the estate. The only firing that can be slow for long is a ``lookup``
    action, which is synchronous by design (``app/flows/lookup.py`` §1) and bounded by
    ``lookup.TIMEOUT_S``. ``BATCH_LIMIT × TIMEOUT_S`` therefore has to stay inside this loop's
    ``worker.LOOP_STALL_SECONDS`` allowance, or a bad batch trips the stall watchdog, the container
    restarts, this transaction rolls back with the cursor un-advanced, and the same batch is read
    again — a permanent outage rather than a slow minute. Pinned by
    ``tests/test_flows_lookup.py::test_a_full_batch_of_lookups_stays_inside_the_watchdog``.
    """
    at = now or datetime.now(UTC)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        await _ensure_cursor(session)
        position = (
            await session.execute(
                text(
                    "SELECT position FROM flows.automation_cursor WHERE consumer = :c "
                    "FOR UPDATE SKIP LOCKED"
                ),
                {"c": CONSUMER},
            )
        ).scalar_one_or_none()
        if position is None:
            return False  # another worker holds the cursor
        events = (
            (
                await session.execute(
                    text(
                        "SELECT id, event_type, tenant_id, target_tenant_id, actor_id, "
                        "       path_params, metadata, created_at "
                        "FROM audit.actions WHERE id > :pos AND created_at < :cutoff "
                        "ORDER BY id LIMIT :lim"
                    ),
                    {
                        "pos": int(position),
                        "cutoff": at - timedelta(seconds=LAG_SECONDS),
                        "lim": BATCH_LIMIT,
                    },
                )
            )
            .mappings()
            .all()
        )
        if not events:
            return False
        if settings.automations_enabled:
            for event in events:
                await handle_event(session, dict(event), now=at)
        await session.execute(
            text(
                "UPDATE flows.automation_cursor SET position = :p, updated_at = now() "
                "WHERE consumer = :c"
            ),
            {"p": int(events[-1]["id"]), "c": CONSUMER},
        )
    return len(events) == BATCH_LIMIT


# ---------------------------------------------------------------------------------------------
# Pass 2 — the deferral queue
# ---------------------------------------------------------------------------------------------


async def run_deferred_once(
    maker: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> bool:
    """Re-attempt firings that hit a rate or concurrency limit. One transaction per row, the
    outbox dispatcher's discipline: a batch-wide rollback would lose the attempt bookkeeping of
    every row in it."""
    at = now or datetime.now(UTC)
    if not settings.automations_enabled:
        return False
    worked = False
    for _ in range(DEFER_BATCH):
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT d.id, d.automation_id, d.tenant_id, d.event_id, d.event_key, "
                            "       d.attempts, d.context, "
                            "       a.uid, a.name, a.trigger_key, a.conditions, a.flow_id, "
                            "       a.action, a.config, a.rearm_minutes, "
                            "       a.allow_automation_source, a.enabled "
                            "FROM flows.automation_deliveries d "
                            "JOIN flows.automations a ON a.id = d.automation_id "
                            "WHERE d.outcome = 'deferred' AND d.retry_after <= :now "
                            "ORDER BY d.retry_after FOR UPDATE OF d SKIP LOCKED LIMIT 1"
                        ),
                        {"now": at},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                break
            worked = True
            context = row["context"] or {}
            spec = triggers.resolve(str(context.get("trigger_key") or row["trigger_key"]))
            limits = await limits_for(session, int(row["tenant_id"]))
            if not row["enabled"]:
                # Disarmed while queued. Not a failure — an explicit human decision that this
                # should not run — so it settles as a skip rather than sitting in the queue for
                # ever waiting for a limit that no longer matters.
                await _record(
                    session,
                    automation_id=int(row["automation_id"]),
                    tenant_id=int(row["tenant_id"]),
                    event_key=str(row["event_key"]),
                    event_id=row["event_id"],
                    outcome="skipped_paused",
                    detail="the automation was disabled while this firing was deferred",
                    attempts=int(row["attempts"]),
                )
                # …and it was holding the event's turn on a promise it can no longer keep.
                if spec is not None and not limits.paused:
                    await _pass_the_turn(
                        session,
                        tenant_id=int(row["tenant_id"]),
                        spec=spec,
                        limits=limits,
                        event_key=str(row["event_key"]),
                        event_id=row["event_id"],
                        context=context,
                        now=at,
                    )
                continue
            candidate = _row_to_candidate({**row, "id": row["automation_id"]})
            if spec is None or limits.paused:
                await _record(
                    session,
                    automation_id=candidate.id,
                    tenant_id=candidate.tenant_id,
                    event_key=str(row["event_key"]),
                    event_id=row["event_id"],
                    outcome="skipped_paused",
                    detail=(
                        "automations are paused for this firm"
                        if spec is not None
                        else f"unknown trigger key {row['trigger_key']!r}"
                    ),
                    attempts=int(row["attempts"]),
                )
                # No turn is passed on here. A paused tenant's other automations must not run
                # either (handle_event stops the whole walk on `limits.paused`), and an unknown
                # trigger key leaves no spec to walk the candidates with.
                continue
            subject_id = context.get("subject_id")
            outcome = await consider(
                session,
                candidate,
                spec=spec,
                limits=limits,
                event_key=str(row["event_key"]),
                event=None,
                event_id=row["event_id"],
                subject_id=int(subject_id) if isinstance(subject_id, int) else None,
                case_uid=context.get("case_uid"),
                chain_depth=int(context.get("chain_depth") or 0),
                facts=context.get("facts") or {},
                # The self-trigger decision was already taken when the firing was first
                # considered; re-asking it here would double-count a marker we no longer hold.
                automation_sourced=False,
                now=at,
                attempts=int(row["attempts"]),
            )
            if outcome not in _TERMINAL_OUTCOMES:
                # It settled as a skip — conditions that no longer hold against the snapshot, a
                # re-arm window that closed over it — so it is not going to run after all, and the
                # event's turn moves on exactly as it would have at the head of the stream.
                await _pass_the_turn(
                    session,
                    tenant_id=int(row["tenant_id"]),
                    spec=spec,
                    limits=limits,
                    event_key=str(row["event_key"]),
                    event_id=row["event_id"],
                    context=context,
                    now=at,
                )
    return worked


# ---------------------------------------------------------------------------------------------
# Pass 3 — scheduled triggers
# ---------------------------------------------------------------------------------------------


def schedule_slot(schedule: Mapping[str, Any], *, tz: ZoneInfo, now: datetime) -> str | None:
    """The slot key this schedule is due for RIGHT NOW in the firm's timezone, or ``None``.

    Config: ``{"cron": "<5-field expression>", "timezone": "<IANA name>"}``. The expression is
    parsed by ``app/cron.py`` — the SAME 5-field subset (``*``, a number, a list, ``a-b``,
    ``*/n``, ``a-b/n``) the interlace model schedules already use, deliberately reused rather than
    inventing a second schedule dialect for the builder's picker to render. Anything outside the
    subset yields no slot rather than a guess, exactly as ``cron.next_fire`` does.

    Evaluated in LOCAL time: an annual-review sweep authored as "09:00" must land at 09:00 in the
    firm's own morning on both sides of a clock change, which it only does if the cron fields are
    matched against the local wall clock rather than UTC.

    The key is the slot's local timestamp, so ``(automation_id, 'schedule:<key>')`` fires at most
    once per slot however often the pass runs — the same idempotency mechanism an event gets. When
    several slots fall inside the grace window (a worker that was briefly down, or a per-minute
    expression) the LATEST is returned: the missed ones collapse, because firing a chaser four
    times to catch up is not catching up.
    """
    expression = schedule.get("cron")
    if not isinstance(expression, str) or not expression.strip():
        return None
    # Naive local wall-clock time: cron fields are wall-clock by definition, and app/cron.py does
    # pure arithmetic, so handing it an aware datetime would only invite a UTC comparison later.
    local = now.astimezone(tz).replace(tzinfo=None, second=0, microsecond=0)
    moment = local - timedelta(minutes=SCHEDULE_GRACE_MINUTES)
    latest: datetime | None = None
    # Bounded: the window is minutes wide and cron's granularity is the minute, so this can only
    # iterate once per minute of grace even for `* * * * *`.
    for _ in range(SCHEDULE_GRACE_MINUTES + 1):
        candidate = cron.next_fire(expression, moment)
        if candidate is None or candidate > local:
            break
        latest, moment = candidate, candidate
    return latest.strftime("%Y-%m-%dT%H:%M") if latest else None


async def schedule_once(
    maker: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> None:
    """Fire every armed schedule that is due, each in its own firm's timezone.

    A scheduled firing names NO subject: the contract's ``schedule.recurring`` describes "a subject
    query the conditions then filter", and that query is a Phase B concern (it needs the branch
    engine's iteration). Today a schedule is a firm-level firing — which is exactly what a rule
    action such as "raise the quarterly review task" wants, and what a flow target will refuse
    with a clear reason rather than starting a journey about nobody.
    """
    at = now or datetime.now(UTC)
    if not settings.automations_enabled:
        return
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, uid, name, tenant_id, trigger_key, conditions, flow_id, "
                        "       action, config, rearm_minutes, allow_automation_source, schedule "
                        "FROM flows.automations "
                        "WHERE enabled AND schedule IS NOT NULL ORDER BY tenant_id, priority, id"
                    )
                )
            )
            .mappings()
            .all()
        )
        limits_cache: dict[int, Limits] = {}
        for row in rows:
            spec = triggers.resolve(str(row["trigger_key"]))
            if spec is None or spec.source != "schedule":
                continue
            tenant_id = int(row["tenant_id"])
            if tenant_id not in limits_cache:
                limits_cache[tenant_id] = await limits_for(session, tenant_id)
            limits = limits_cache[tenant_id]
            if limits.paused:
                continue
            schedule = row["schedule"] or {}
            override = schedule.get("timezone")
            tz = zone(str(override) if isinstance(override, str) and override else limits.timezone)
            slot = schedule_slot(schedule, tz=tz, now=at)
            if slot is None:
                continue
            candidate = _row_to_candidate(dict(row))
            event_key = f"schedule:{slot}"
            if await _already_delivered(session, candidate.id, event_key):
                continue
            facts = await build_facts(
                session,
                spec=spec,
                event=None,
                tenant_id=tenant_id,
                subject_id=None,
                tz=tz,
                now=at,
            )
            await consider(
                session,
                candidate,
                spec=spec,
                limits=limits,
                event_key=event_key,
                event=None,
                event_id=None,
                subject_id=None,
                case_uid=None,
                chain_depth=0,
                facts=facts,
                automation_sourced=False,
                now=at,
            )


# ---------------------------------------------------------------------------------------------
# The flow dispatch target
# ---------------------------------------------------------------------------------------------


class FlowTarget:
    """Start a case of an armed flow — through the EXISTING engine, not a second implementation.

    ``orchestrator.start_execution`` is the one path that plans, persists and instantiates a
    journey, and it is deliberately reused verbatim: an automation-started onboarding must be
    indistinguishable from an adviser-started one, right down to the KYC gate. What the automation
    path has to supply that the ``/applications`` router gets from the adviser is the application
    the flow runs over — so a minimal ``crm.applications`` row is created for the subject contact
    and the participant graph is built in memory (one participant, no accounts). A flow whose
    steps are account-scoped simply plans nothing for them, which is the honest outcome: the
    account does not exist yet.
    """

    async def run(
        self, session: AsyncSession, ctx: AutomationContext, spec: Mapping[str, Any]
    ) -> DispatchResult:
        # Imported here, not at module scope: the orchestrator imports plenty of the engine, and a
        # top-level import would make this module part of that cycle.
        from app.flows import orchestrator
        from app.flows.planning import ApplicationGraph, Participant

        flow_id = spec.get("flow_id")
        if not isinstance(flow_id, int):
            return DispatchResult(ok=False, detail="the automation names no flow")
        if ctx.subject_kind != "contact" or ctx.subject_id is None:
            return DispatchResult(
                ok=False,
                detail=(
                    "this trigger did not name a client, so there is nobody to start a journey "
                    "for — use an action automation, or a trigger with a client subject"
                ),
            )
        flow = (
            (
                await session.execute(
                    text(
                        "SELECT f.id, f.current_published_version_id AS vid, v.definition "
                        "FROM flows.flows f "
                        "JOIN flows.flow_versions v ON v.id = f.current_published_version_id "
                        "WHERE f.id = :f AND f.tenant_id = :t AND f.status = 'published'"
                    ),
                    {"f": flow_id, "t": ctx.tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if flow is None:
            return DispatchResult(ok=False, detail="the flow is not published")

        contact = (
            (
                await session.execute(
                    text(
                        "SELECT type::text AS type FROM crm.contacts "
                        "WHERE id = :c AND tenant_id = :t"
                    ),
                    {"c": ctx.subject_id, "t": ctx.tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if contact is None:
            return DispatchResult(ok=False, detail="the client no longer exists")

        provider = await self._provider(session, ctx)
        if provider is None:
            return DispatchResult(
                ok=False,
                detail=(
                    "no platform could be resolved for the journey — set provider on the automation"
                ),
            )
        application_id = int(
            (
                await session.execute(
                    text(
                        "INSERT INTO crm.applications (tenant_id, provider, uid, status) "
                        "VALUES (:t, CAST(:p AS platform.provider_code), :u, 'draft') "
                        "RETURNING id"
                    ),
                    {
                        "t": ctx.tenant_id,
                        "p": provider,
                        "u": f"app_{ctx.automation_uid}_{ctx.event_id or 's'}"[:64],
                    },
                )
            ).scalar_one()
        )
        definition = flow["definition"]
        if isinstance(definition, str):
            definition = json.loads(definition)
        graph = ApplicationGraph(
            participants=(
                Participant(
                    contact_id=int(ctx.subject_id),
                    type=str(contact["type"] or "individual"),
                    # 'owner' so the participant is KYC-relevant (planning.KYC_ROLES) — an
                    # automation-started journey must gate identity exactly as an adviser-started
                    # one does, not quietly skip it because nobody named a role.
                    roles=frozenset({"owner"}),
                ),
            ),
            accounts=(),
        )
        try:
            created = await orchestrator.start_execution(
                session,
                tenant_id=ctx.tenant_id,
                flow_id=int(flow["id"]),
                flow_version_id=int(flow["vid"]),
                definition=definition,
                graph=graph,
                application_id=application_id,
                initiation="automation",
                data={
                    "automation_uid": ctx.automation_uid,
                    "trigger_key": ctx.trigger_key,
                    # Carried onto the execution so a `start_flow` node INSIDE this journey counts
                    # from the depth that reached it rather than from zero. One counter, A1's —
                    # otherwise a rule that starts a flow that starts a flow escapes the cap by
                    # changing lane (app/flows/orchestrator.py::_start_child_flow).
                    "chain_depth": ctx.chain_depth,
                },
                # An automation may hand the journey its starting variables. Nothing authors these
                # yet; the seam is here so the rule builder's "with these values" lands without
                # touching the orchestrator.
                memory=(
                    ctx.config.get("memory") if isinstance(ctx.config.get("memory"), dict) else None
                ),
            )
        except orchestrator.ActiveExecutionExists:
            # Not an error: one live journey per (flow, client) is the engine's own guard, and an
            # automation racing an adviser who already started one is exactly what it protects.
            return DispatchResult(ok=False, detail="the client is already on a live journey")
        case_uid = (
            await session.execute(
                text("SELECT uid FROM flows.cases WHERE execution_id = :e ORDER BY id LIMIT 1"),
                {"e": created["id"]},
            )
        ).scalar_one_or_none()
        return DispatchResult(
            ok=True, detail=f"started journey {created['uid']}", case_uid=case_uid
        )

    async def _provider(self, session: AsyncSession, ctx: AutomationContext) -> str | None:
        """Which custodian the synthesised application belongs to.

        In order: what the automation was authored with, then whatever custodian this client's
        last application used, then the firm's ONLY ENABLED one. No guessing beyond that — a firm
        on two custodians whose automation names neither is an authoring gap, and saying so beats
        filing a journey with the wrong one.

        The last resort changed with 0131 and is better for it. `platform.platforms` was global
        with no tenant column, so "the only platform there is" meant the only one the ESTATE
        integrates with — three, always, so the fallback never fired. `platform.tenant_platforms`
        is per firm, so "the only one this firm has enabled" is a question with a useful answer."""
        configured = ctx.config.get("provider")
        if isinstance(configured, str) and configured in providers.PROVIDERS:
            return configured
        previous = (
            await session.execute(
                text(
                    "SELECT a.provider FROM crm.applications a "
                    "JOIN crm.account_contacts ac ON ac.application_id = a.id "
                    "WHERE ac.contact_id = :c AND a.tenant_id = :t AND a.provider IS NOT NULL "
                    "ORDER BY a.id DESC LIMIT 1"
                ),
                {"c": ctx.subject_id, "t": ctx.tenant_id},
            )
        ).scalar_one_or_none()
        if previous is not None:
            return str(previous)
        enabled = (
            (
                await session.execute(
                    text(
                        "SELECT provider FROM platform.tenant_platforms "
                        "WHERE tenant_id = :t AND enabled "
                        "  AND provider = ANY(CAST(:custodians AS platform.provider_code[])) "
                        "LIMIT 2"
                    ),
                    {"t": ctx.tenant_id, "custodians": list(providers.custodians())},
                )
            )
            .scalars()
            .all()
        )
        return str(enabled[0]) if len(enabled) == 1 else None


triggers.register_target("flow", FlowTarget())
