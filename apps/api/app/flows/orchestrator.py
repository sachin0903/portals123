"""The orchestrator — the DB spine that runs an execution: plan → persist → consume events →
re-derive → project. Generic (no onboarding vocabulary); onboarding is one configuration.

Runs on a ``service_role`` session (BYPASSRLS) like the rest of the engine — it writes ``flows.*``
+ the ``crm.applications`` projection. Concurrency discipline (the plan, §3): every recompute takes
``pg_advisory_xact_lock(execution_id)`` FIRST, so concurrent step events on sibling cases of the
same execution serialise on the group and can't lose the rollup or deadlock.

NOTE: exercised by ``tests/test_flows_orchestration_db.py`` under Docker (testcontainers); the pure
planning/derivation/projection logic it composes is covered by the no-DB suites.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import terms as terms_svc
from app.flows import (
    dependencies,
    form_validation,
    handlers,
    interpolation,
    notifications,
    planning,
    projection,
)
from app.flows import graph as graph_mod
from app.flows import kyc as kyc_mod
from app.flows import memory as memory_mod
from app.flows import scoring as scoring_mod
from app.flows import timers as timers_mod
from app.flows.derivation import CaseView, StepView, derive_case_state, derive_execution_state
from app.flows.domain_events import SIGNAL_TO_STATUS, StepEvent, StepSignal, StepStatus
from app.flows.errors import StepTransitionRefused
from app.flows.planning import (
    AccountSpec,
    ApplicationGraph,
    Participant,
    PlannedCase,
    plan_execution,
)
from app.flows.states import TERMINAL, State, can_transition
from app.ids import new_id
from app.idv import manual as idv_manual

logger = logging.getLogger("flow_orchestrator")


#: Step types whose artifact is a documents.request_item the client fulfils through the existing
#: document endpoints (which then trip on_item_fulfilled).
_REQUEST_BACKED = frozenset({"fill_form", "accept_terms", "upload_document"})

#: Step statuses a *fulfilment claim* must never move. ``fulfilled`` would re-run the module's
#: side-effects (re-apply a CRM patch, overwrite a recorded KYC outcome) on a step that is already
#: done; ``cancelled``/``skipped`` belong to a journey that was stopped or elided the step.
_SETTLED_STEP_STATUSES = frozenset(
    {StepStatus.FULFILLED.value, StepStatus.CANCELLED.value, StepStatus.SKIPPED.value}
)

#: What each request-backed step type is waiting for, phrased for the person who gets the refusal.
_AWAITED_ARTIFACT = {
    "fill_form": "the form to be filled in",
    "accept_terms": "the terms to be accepted",
    "upload_document": "the document to be uploaded",
}

#: Keys from a StepEvent's ``detail`` that are persisted onto the step (an allowlist — a handler's
#: working payload isn't step state). ``note``/``decided_by`` make a compliance decision auditable
#: on the step itself, not just in the audit spine. ``local_activated``/``custodian_open`` are the
#: setup_account hand-off seam (the account also carries the richer marker on
#: ``platform.accounts.data``).
_PERSISTED_DETAIL = frozenset(
    {"note", "decided_by", "outcome", "local_activated", "custodian_open"}
)

#: How many times one recompute will loop the dependency release pass. A pass releases at least one
#: instance (or stops), and a released instance is marked, so this only bounds pathological chains.
_MAX_RELEASE_PASSES = 25

# ---------------------------------------------------------------------------------------------
# The v3 graph runtime — where a case's control flow lives
# ---------------------------------------------------------------------------------------------
#
# A v3 flow is a graph, but the EXECUTION machinery underneath it is unchanged: planning still
# fans steps into tiered cases, and a step still becomes real when its prerequisites settle. What
# a control node adds is a decision the dependency pass cannot express, so the engine walks the
# graph itself and hands out releases. The walk is:
#
#   * **per case**, because everything a control node does is case-shaped — a timer row is keyed
#     (case, node), an ``exit`` latches one case, a branch resolves against one case's facts. In a
#     tiered onboarding that means each participant's profile case branches on its OWN answers,
#     which is what an author drawing one graph means by "if this client is offshore".
#   * **paused at a step another tier owns.** A case that does not hold an instance of the step it
#     has walked to waits for that step to settle somewhere in the execution rather than walking
#     past it, so a graph drawn as "forms, then check, then set up the account" keeps that order
#     across tiers. A step planned NOWHERE (conditioned out at every tier) is walked past, the
#     same way ``dependencies.unmet`` treats a prerequisite that was never created as satisfied.
#
#: Where the walk's state lives on ``flows.cases.data``: which nodes this case has entered and,
#: for a branch, which arm it took. Written under the execution's advisory lock, like every other
#: recompute product.
_GRAPH_STATE_KEY = "graph"
_VISITED_KEY = "visited"

#: Written on ``case_steps.data`` by the walk. ``graph_released`` = the engine has let this step
#: through its control node; ``gated_on`` = it is still held, and by which node (the UI's reason).
_GRAPH_RELEASED_KEY = "graph_released"
_GATED_ON_KEY = "gated_on"

#: Nodes one ``_enter_node`` walk will process before giving up — a TOTAL budget across the whole
#: frontier, not a path length. The distinction is the fan-out change: a walk is now a work-list, a
#: wide graph legitimately visits many nodes in a single pass, and a per-path limit would have
#: turned "this journey opens on five things" into a silently truncated walk. Sized above
#: ``caps.maxNodesPerFlow`` so a publishable graph can never reach it (the walk also de-duplicates,
#: so it cannot process more nodes than the graph has); it only bites on stored JSON from before
#: the acyclicity rule, where the alternative is a worker spinning on a cycle for ever.
_MAX_GRAPH_HOPS = 250

#: Step statuses that let the graph move on from a node. For the case as a whole, all instances
#: must be settled AND at least one must actually have been done: a step whose every instance was
#: ELIDED by a branch has not happened, and advancing past it would let an untaken arm drive the
#: journey. A fanned step also moves on for ONE owner once their own instance is fulfilled
#: (``_advance_graph``).
_SETTLED_FOR_ADVANCE = ("fulfilled", "skipped", "cancelled")


class ActiveExecutionExists(Exception):
    """A live case already exists for this (flow, subject) — ``cases_one_active_per_subject``.

    Raised instead of letting the unique index surface as a 500, so the router can tell the adviser
    the client is already on a journey of this flow (and that cancelling it frees the subject).

    ``execution_uid`` names the live journey when the pre-check found it, so the refusal can
    offer a way forward (open it, or stop it) rather than only the sentence. ``None`` when the
    unique index caught a race the pre-check did not see.
    """

    def __init__(self, message: str, *, execution_uid: str | None = None) -> None:
        super().__init__(message)
        self.execution_uid = execution_uid


async def _ensure_request(
    session: AsyncSession,
    tenant_id: int,
    execution_id: int,
    contact_id: int,
    cache: dict[int, int],
) -> int:
    """One ``documents.requests`` per contact per execution, holding all their request-backed items
    (their profile forms + any joint-account terms). Cached within a start_execution call."""
    if contact_id in cache:
        return cache[contact_id]
    request_id = int(
        (
            await session.execute(
                text(
                    "INSERT INTO documents.requests "
                    "  (uid, tenant_id, contact_id, requested_by, priority, message) "
                    "VALUES (:u, :t, :c, NULL, 'normal', :msg) RETURNING id"
                ),
                {"u": new_id(), "t": tenant_id, "c": contact_id, "msg": "Onboarding"},
            )
        ).scalar_one()
    )
    cache[contact_id] = request_id
    return request_id


async def on_item_fulfilled(session: AsyncSession, request_item_id: int) -> StepEvent | None:
    """Hook the document-fulfilment path calls: if a request item backs a **v2 orchestrated** case
    step, route its completion through the handler (submit_step) so the module reacts and the
    execution recomputes. A no-op for v1 cases (execution_id IS NULL) and ordinary requests.

    Returns the applied event so a caller can see that the handler REFUSED — the fulfilment itself
    succeeds whatever the handler decides, so a route that ignores this answers 200 for a step that
    is now blocked, and the person who submitted the form is told it was saved.

    ``blocked`` is in the status list on purpose. A handler that refuses its write-back (a fee
    package that does not belong to the account, a mapping that will not apply) reports BLOCKED
    *after* the artifact has been fulfilled — so the step's own answers are what need correcting,
    and re-submitting them must re-run the handler. Without it the only escape from a refused
    write-back was ``retry_step``, which discards the artifact and the answers with it."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT cs.case_id, cs.step_key, cs.step_type, cs.participant_subject_id, "
                    "       c.tenant_id "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE cs.request_item_id = :i AND c.execution_id IS NOT NULL "
                    "  AND cs.status IN ('pending', 'waiting', 'blocked') LIMIT 1"
                ),
                {"i": request_item_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        # Not a step's own artifact. It may still be one of the two pieces of manual identity
        # evidence, which are recorded on the step rather than linked to it.
        return await _settle_identity_from_evidence(session, request_item_id)
    detail: dict[str, Any] = {}
    if row["step_type"] == "fill_form":
        # Score before the transition so the stored row already carries the computed band,
        # and so `remember` below reads that band rather than a value the browser posted.
        answers, outputs = await scoring_mod.score_stored_response(session, request_item_id)
        if answers is not None:
            detail["answers"] = answers
            detail["outputs"] = outputs
    return await submit_step(
        session,
        tenant_id=int(row["tenant_id"]),
        case_id=int(row["case_id"]),
        step_key=str(row["step_key"]),
        detail=detail,
        participant_subject_id=row["participant_subject_id"],
    )


async def _settle_identity_from_evidence(
    session: AsyncSession, request_item_id: int
) -> StepEvent | None:
    """Complete a manual identity check once both of its documents have landed.

    The manual path IS the two documents ``app/idv/manual.py`` asks for (a photo ID and a proof
    of address). They are recorded on the step (``case_steps.data.identity_documents``) rather
    than linked as its ``request_item_id``, so the generic hook above cannot see them — this is the
    counterpart that can. A no-op unless the fulfilled item is one a live ``verify_identity`` step
    recorded; and while any recorded item is still ``pending`` the step keeps waiting — one file
    of two is the state a chase is about, not a verified identity.

    Settling here, from the evidence, replaces the reviewer's Pass / Refer: the check is
    mandatory and is satisfied by the documents being on the client's record (or by the digital
    provider's result on the other route), not by a verdict typed at the step. ``kyc_checks``
    records the pass with the documents behind it, so the client record says what the identity
    was verified against.
    """
    item_uid = (
        await session.execute(
            text("SELECT uid FROM documents.request_items WHERE id = :i"), {"i": request_item_id}
        )
    ).scalar_one_or_none()
    if item_uid is None:
        return None
    row = (
        (
            await session.execute(
                text(
                    "SELECT cs.case_id, cs.step_key, cs.participant_subject_id, cs.data, "
                    "       c.tenant_id "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE c.execution_id IS NOT NULL AND cs.step_type = 'verify_identity' "
                    "  AND cs.status IN ('pending', 'waiting') "
                    # Keyed `{spec: uid}` today; a bare list on steps recorded before it was keyed.
                    # A text containment test covers both without caring which.
                    "  AND (cs.data -> 'identity_documents')::text LIKE '%' || :u || '%' "
                    "LIMIT 1"
                ),
                {"u": str(item_uid)},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    tenant_id = int(row["tenant_id"])
    documents = await idv_manual.documents_for(
        session,
        tenant_id=tenant_id,
        recorded=_as_dict(row["data"]).get("identity_documents"),
    )
    if any(str(d["status"]) == "pending" for d in documents):
        return None
    filed = [
        {"key": d["key"], "title": d["title"], "document_uid": d["document_uid"]}
        for d in documents
        if str(d["status"]) == "fulfilled"
    ]
    return await submit_step(
        session,
        tenant_id=tenant_id,
        case_id=int(row["case_id"]),
        step_key=str(row["step_key"]),
        detail={
            "outcome": "passed",
            _EVIDENCE_COMPLETE: True,
            "note": "Identity documents received: " + ", ".join(str(d["title"]) for d in filed),
            "raw": {"method": "documents", "evidence": filed},
        },
        participant_subject_id=row["participant_subject_id"],
    )


async def _advisory_lock(session: AsyncSession, execution_id: int) -> None:
    """Serialise recompute for one execution on the group (fixed lock order — see module doc)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": execution_id})


async def start_execution(
    session: AsyncSession,
    *,
    tenant_id: int,
    flow_id: int,
    flow_version_id: int,
    definition: dict[str, Any],
    graph: ApplicationGraph,
    application_id: int,
    created_by: str | None = None,
    initiation: str = "adviser",
    data: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
    assignee_user_id: str | None = None,
) -> dict[str, object]:
    """Run a version-pinned execution over an already-built application (the adviser assembles the
    ``crm.applications`` + its accounts + participants first; this orchestrates the flow over them):
    plan the tiered graph and persist all cases + steps (instantiating each step's artifact via its
    handler). Returns the new execution's id + uid + the crm.applications id.

    ``memory`` seeds the journey's starting variables — the automation's authored context, the
    adviser's intake answers, or a parent journey's memory carried over by ``start_flow``. Written
    into EVERY case, because at kickoff there is no smaller scope that is true (see
    ``app/flows/memory.py``). ``data['memory']`` is read as well, so a caller that already carries
    a bag of execution data does not need a second argument.

    ``assignee_user_id`` is the staff member the journey's work starts with (0188) — the client's
    adviser at kickoff, so a journey an administrator starts on an adviser's behalf lands on the
    adviser's desk rather than the administrator's. Written before the first step is
    instantiated, because the dispatcher reads it when the kickoff steps raise their tasks.
    """
    execution_uid = new_id()
    seeds: dict[str, Any] = {}
    for source in (data.get("memory") if isinstance(data, dict) else None, memory):
        if isinstance(source, dict):
            seeds.update(source)
    execution_id = (
        await session.execute(
            text(
                "INSERT INTO flows.executions "
                "  (uid, tenant_id, flow_id, flow_version_id, status, crm_application_id, "
                "   created_by, assignee_user_id, data) "
                "VALUES (:uid, :t, :fid, :vid, 'draft', :app, CAST(:by AS uuid), "
                "  CAST(:assignee AS uuid), CAST(:d AS jsonb)) RETURNING id"
            ),
            {
                "uid": execution_uid,
                "t": tenant_id,
                "fid": flow_id,
                "vid": flow_version_id,
                "app": application_id,
                "by": created_by,
                "assignee": assignee_user_id,
                "d": json.dumps(data or {}),
            },
        )
    ).scalar_one()

    # The definition as a GRAPH: the source of the control gates below and of every runtime
    # decision after this. Legacy definitions parse to a legacy graph whose gates are empty, so
    # everything below is a no-op for them by construction rather than by an `if`.
    try:
        flow_graph: graph_mod.Graph | None = graph_mod.parse(definition)
    except graph_mod.DefinitionError:
        flow_graph = None
    gated = planning.control_gated_steps(flow_graph) if flow_graph is not None else {}

    plan = plan_execution(list(definition.get("steps", [])), graph)
    requests_cache: dict[
        int, int
    ] = {}  # contact_id -> its documents.requests id (one per execution)
    case_ids: list[int] = []
    for pcase in plan:
        case_id = await _persist_case(
            session,
            tenant_id=tenant_id,
            execution_id=int(execution_id),
            flow_id=flow_id,
            flow_version_id=flow_version_id,
            pcase=pcase,
            created_by=created_by,
            initiation=initiation,
        )
        case_ids.append(case_id)
        if seeds:
            await memory_mod.write_many(
                session, tenant_id=tenant_id, case_id=case_id, values=seeds, source="start"
            )
        for pstep in pcase.steps:
            await _persist_step(session, tenant_id=tenant_id, case_id=case_id, pstep=pstep)
        # `depends_on` is real ordering, not decoration: only steps whose prerequisites are already
        # settled get an artifact now. At plan time nothing has run, so that's every step with no
        # planned prerequisite; the rest are held `pending` with their outstanding keys recorded and
        # released by recompute's dependency pass as those prerequisites clear.
        facts: list[dependencies.StepFact] = [
            (s.step_key, s.participant_subject_id, StepStatus.PENDING.value, s.depends_on)
            for s in pcase.steps
        ]
        # The other cases in the same PLAN: at kickoff none of them is persisted yet, so the
        # cross-tier prerequisites have to come from the plan rather than from the table.
        foreign: list[dependencies.StepFact] = [
            (s.step_key, s.participant_subject_id, StepStatus.PENDING.value, s.depends_on)
            for other in plan
            if other is not pcase
            for s in other.steps
        ]
        for pstep in pcase.steps:
            awaiting = dependencies.unmet(
                pstep.step_key, pstep.participant_subject_id, pstep.depends_on, facts, foreign
            )
            if awaiting:
                await _mark_awaiting(
                    session, case_id, pstep.step_key, pstep.participant_subject_id, awaiting
                )
                continue
            if pstep.step_key in gated:
                # Behind a control node: the engine hands this one out when its walk reaches it,
                # not at kickoff. Without this a step sitting after the graph's first branch — which
                # has no compiled prerequisite at all — would be instantiated for every arm.
                await _mark_gated(session, case_id, pstep.step_key, gated[pstep.step_key])
                continue
            await _instantiate_step(
                session,
                tenant_id=tenant_id,
                execution_id=int(execution_id),
                case_id=case_id,
                step_key=pstep.step_key,
                step_type=pstep.step_type,
                config=pstep.config,
                subject_type=pcase.subject_type,
                subject_id=pcase.subject_id,
                participant_subject_id=pstep.participant_subject_id,
                requests_cache=requests_cache,
            )

    if flow_graph is not None and not flow_graph.is_legacy_linear and flow_graph.entry:
        # Walk each case from the graph's entry, so a journey that OPENS on a branch (or a wait, or
        # a start node's fan-out) resolves it now rather than only after some step happens to
        # settle.
        for case_id in case_ids:
            await _enter_node(
                session,
                tenant_id=tenant_id,
                execution_id=int(execution_id),
                case_id=case_id,
                node_ids=(flow_graph.entry,),
                graph=flow_graph,
            )

    await _append_event(
        session,
        tenant_id=tenant_id,
        execution_id=int(execution_id),
        case_id=None,
        event_type="execution.started",
        payload={"flow_version_id": flow_version_id, "cases": len(plan)},
        idempotency_key=f"start:{int(execution_id)}",
    )
    await recompute(session, int(execution_id), tenant_id=tenant_id)
    return {"id": int(execution_id), "uid": execution_uid, "application_id": int(application_id)}


async def _instantiate_step(
    session: AsyncSession,
    *,
    tenant_id: int,
    execution_id: int,
    case_id: int,
    step_key: str,
    step_type: str,
    config: dict[str, Any],
    subject_type: str | None,
    subject_id: int | None,
    participant_subject_id: int | None,
    requests_cache: dict[int, int],
) -> StepEvent | None:
    """Create one step instance's artifact through its handler and record the initial signal.

    Shared by ``start_execution`` (steps with no prerequisites) and the dependency release pass
    (steps whose prerequisites have since settled), so a released step goes through exactly the same
    path as one instantiated at kickoff.
    """
    # Request-backed steps need a documents.requests bound to the contact who fulfils them: the
    # participant for a fanned step, the profile case's contact subject, or — for an account-scoped
    # step with no per-owner fan-out — the account's primary owner. Without that last fallback an
    # account-level form (the seeded "Account activity") has no artifact and no actor at all.
    target_contact = participant_subject_id or (subject_id if subject_type == "contact" else None)
    if target_contact is None and subject_type == "account" and subject_id is not None:
        target_contact = (
            await session.execute(
                text("SELECT contact_id FROM platform.accounts WHERE id = :a"), {"a": subject_id}
            )
        ).scalar_one_or_none()
    request_id = None
    if step_type in _REQUEST_BACKED and target_contact is not None:
        request_id = await _ensure_request(
            session, tenant_id, execution_id, int(target_contact), requests_cache
        )
    ctx = handlers.StepContext(
        session=session,
        tenant_id=tenant_id,
        execution_id=execution_id,
        case_id=case_id,
        step_key=step_key,
        config=config,
        subject_type=subject_type,
        subject_id=subject_id,
        participant_subject_id=participant_subject_id,
        request_id=request_id,
    )
    handler = handlers.get_handler(step_type)
    event = await handler.instantiate(ctx) if handler is not None else None
    # Mark the instance instantiated (and clear any dependency note) BEFORE the status write, so a
    # release pass can never hand the same instance to its handler twice.
    await _mark_instantiated(session, case_id, step_key, participant_subject_id)
    if event is not None:
        await _write_step_status(session, case_id, step_key, event)
        # A step whose INITIAL signal is already terminal-ish (a dedup'd KYC pass, a render kicked
        # off) must still fire its raise-a-task specs — otherwise "on completed / on waiting"
        # follow-ups silently never happen for exactly those steps.
        await _emit_task_requests(
            session, tenant_id=tenant_id, execution_id=execution_id, event=event
        )
    return event


async def _mark_awaiting(
    session: AsyncSession,
    case_id: int,
    step_key: str,
    participant_subject_id: int | None,
    awaiting: tuple[str, ...],
) -> None:
    """Record which prerequisites a held step is still waiting on (surfaced by the read models as
    "waiting on X", so a step with no action has a visible reason)."""
    await session.execute(
        text(
            "UPDATE flows.case_steps SET data = data || CAST(:d AS jsonb) "
            "WHERE case_id = :c AND step_key = :k "
            "  AND (CAST(:psid AS int) IS NULL OR participant_subject_id = CAST(:psid AS int))"
        ),
        {
            "d": json.dumps({dependencies.AWAITING_KEY: list(awaiting)}),
            "c": case_id,
            "k": step_key,
            "psid": participant_subject_id,
        },
    )


async def _mark_instantiated(
    session: AsyncSession, case_id: int, step_key: str, participant_subject_id: int | None
) -> None:
    await session.execute(
        text(
            "UPDATE flows.case_steps "
            "SET data = (data - CAST(:awaiting AS text)) || CAST(:d AS jsonb) "
            "WHERE case_id = :c AND step_key = :k "
            "  AND (CAST(:psid AS int) IS NULL OR participant_subject_id = CAST(:psid AS int))"
        ),
        {
            "awaiting": dependencies.AWAITING_KEY,
            "d": json.dumps({dependencies.INSTANTIATED_KEY: True}),
            "c": case_id,
            "k": step_key,
            "psid": participant_subject_id,
        },
    )


async def _persist_case(
    session: AsyncSession,
    *,
    tenant_id: int,
    execution_id: int,
    flow_id: int,
    flow_version_id: int,
    pcase: PlannedCase,
    created_by: str | None,
    initiation: str,
) -> int:
    # subject_contact_id is kept populated when the subject is a contact so the existing client-RLS
    # policy (subject_contact_id = ANY(contact_scope())) covers profile cases too.
    subject_contact_id = pcase.subject_id if pcase.subject_type == "contact" else None
    if subject_contact_id is not None:
        live = await live_execution_uid(
            session, flow_id=flow_id, subject_contact_id=int(subject_contact_id)
        )
        if live is not None:
            raise ActiveExecutionExists(_ALREADY_LIVE, execution_uid=live or None)
    try:
        return int(
            (
                await session.execute(
                    text(
                        "INSERT INTO flows.cases "
                        "  (uid, tenant_id, flow_id, flow_version_id, execution_id, kind, "
                        "   subject_type, subject_id, subject_contact_id, status, initiation, "
                        "   created_by) "
                        "VALUES (:uid, :t, :fid, :vid, :eid, :kind, :stype, :sid, :scid, 'draft', "
                        "  :init, CAST(:by AS uuid)) RETURNING id"
                    ),
                    {
                        "uid": new_id(),
                        "t": tenant_id,
                        "fid": flow_id,
                        "vid": flow_version_id,
                        "eid": execution_id,
                        "kind": pcase.kind,
                        "stype": pcase.subject_type,
                        "sid": pcase.subject_id,
                        "scid": subject_contact_id,
                        "init": initiation,
                        "by": created_by,
                    },
                )
            ).scalar_one()
        )
    except IntegrityError as exc:
        # The pre-check above is not a lock: two advisers clicking "start" at the same instant both
        # see no live case (neither transaction has committed), and the second INSERT loses on
        # ``cases_one_active_per_subject``. That is the *right* outcome — one journey — but it used
        # to surface as a 500. The index is the real guard; this maps its verdict onto the same 409
        # the pre-check gives, so the loser is told what happened.
        if "cases_one_active_per_subject" not in str(getattr(exc, "orig", exc)):
            raise
        raise ActiveExecutionExists(_ALREADY_LIVE) from exc


_ALREADY_LIVE = (
    "This client is already on a live journey of this flow. Cancel it before starting another."
)


async def live_execution_uid(
    session: AsyncSession, *, flow_id: int, subject_contact_id: int
) -> str | None:
    """The uid of the LIVE journey already holding this (flow, subject), or None — the readable
    half of ``cases_one_active_per_subject`` (0024), matching its predicate exactly, so the common
    case is a clear 409 instead of an integrity error halfway through building the plan.

    The uid rather than a bare yes: the refusal has to point at the journey it is talking about,
    or the only way out of it is to go and find that journey by hand. A case with no execution
    (legacy rows) still blocks, and reports no uid."""
    row = (
        await session.execute(
            text(
                "SELECT e.uid FROM flows.cases c "
                "LEFT JOIN flows.executions e ON e.id = c.execution_id "
                "WHERE c.flow_id = :f AND c.subject_contact_id = :c "
                "  AND c.completed_at IS NULL AND c.cancelled_at IS NULL "
                "ORDER BY c.created_at DESC LIMIT 1"
            ),
            {"f": flow_id, "c": subject_contact_id},
        )
    ).first()
    if row is None:
        return None
    return str(row[0]) if row[0] else ""


async def _persist_step(session: AsyncSession, *, tenant_id: int, case_id: int, pstep: Any) -> None:
    await session.execute(
        text(
            "INSERT INTO flows.case_steps "
            "  (tenant_id, case_id, step_id, step_key, step_type, title, assignee, required, "
            "   scope, depends_on, participant_subject_id, status) "
            "VALUES (:t, :cid, :sid, :key, :stype, :title, :assignee, :req, :scope, "
            "  CAST(:deps AS text[]), :psid, 'pending')"
        ),
        {
            "t": tenant_id,
            "cid": case_id,
            "sid": _fanned_step_id(pstep),
            "key": pstep.step_key,
            "stype": pstep.step_type,
            "title": pstep.title or pstep.config.get("title") or pstep.step_key,
            "assignee": pstep.assignee,
            "req": pstep.required,
            "scope": pstep.scope,
            "deps": list(pstep.depends_on),
            "psid": pstep.participant_subject_id,
        },
    )


def _fanned_step_id(pstep: Any) -> str:
    """The unique ``(case_id, step_id)`` key — a per-participant fan-out needs the participant in
    the id so two instances of the same step key in one case don't collide."""
    if pstep.participant_subject_id is not None:
        return f"{pstep.step_key}:{pstep.participant_subject_id}"
    return str(pstep.step_key)


async def _write_step_status(
    session: AsyncSession, case_id: int, step_key: str, event: StepEvent
) -> None:
    status = SIGNAL_TO_STATUS[event.signal]
    completed = status == StepStatus.FULFILLED
    # The event's own detail rides onto the step (a compliance decision's note + decider, a render
    # error) so an outcome is never a bare flag with no recorded reason.
    patch: dict[str, Any] = {"last_event": event.reason} if event.reason else {}
    patch.update({k: v for k, v in event.detail.items() if k in _PERSISTED_DETAIL})
    await session.execute(
        text(
            "UPDATE flows.case_steps SET status = :s, "
            "  completed_at = CASE WHEN :done THEN now() ELSE completed_at END, "
            "  data = data || CAST(:d AS jsonb) "
            "WHERE case_id = :c AND step_key = :k "
            # CAST the nullable participant id so Postgres can type the bound NULL (a bare
            # `:psid IS NULL` is an AmbiguousParameter). NULL psid ⇒ match the single (non-fanned)
            # instance; a set psid ⇒ match that participant's fan-out instance.
            "  AND (CAST(:psid AS int) IS NULL OR participant_subject_id = CAST(:psid AS int))"
        ),
        {
            "s": status.value,
            "done": completed,
            "d": json.dumps(patch),
            "c": case_id,
            "k": step_key,
            "psid": event.participant_subject_id,
        },
    )


async def apply_step_event(session: AsyncSession, tenant_id: int, event: StepEvent) -> None:
    """Consume a handler's event: write the step status, append the immutable fact, then recompute
    the whole execution under the group lock."""
    execution_id = (
        await session.execute(
            text("SELECT execution_id FROM flows.cases WHERE id = :c"), {"c": event.case_id}
        )
    ).scalar_one_or_none()
    if execution_id is None:
        return  # not an orchestrated case (v1 engine path) — nothing to do
    await _write_step_status(session, event.case_id, event.step_key, event)
    await _append_event(
        session,
        tenant_id=tenant_id,
        execution_id=int(execution_id),
        case_id=event.case_id,
        event_type=event.signal.value,
        payload={
            "step_key": event.step_key,
            "participant": event.participant_subject_id,
            "reason": event.reason,
            # WHO acted — without it the timeline can't say who passed an AML check.
            "actor": event.detail.get("decided_by"),
            "note": event.detail.get("note"),
        },
        # Derived, not random: replaying the same transition appends ONE fact, not two (the unique
        # (tenant, event_type, idempotency_key) index can finally fire).
        idempotency_key=(f"{event.case_id}:{event.step_key}:{event.participant_subject_id or '-'}"),
    )
    await _emit_task_requests(
        session, tenant_id=tenant_id, execution_id=int(execution_id), event=event
    )
    await _complete_equivalent_catalogue_terms(
        session,
        tenant_id=tenant_id,
        execution_id=int(execution_id),
        source=event,
    )
    await recompute(session, int(execution_id), tenant_id=tenant_id)


async def _complete_equivalent_catalogue_terms(
    session: AsyncSession,
    *,
    tenant_id: int,
    execution_id: int,
    source: StepEvent,
) -> None:
    """Settle duplicate account rows covered by the catalogue acceptance just recorded.

    New plans collapse these before they are created. This is the compatibility path for running
    journeys planned under the old account-scoped rule: the acceptance register says the contact
    accepted one published version, so making them click identical request items cannot add legal
    evidence. Each settled row still gets its own domain event, and its request is closed so neither
    the client checklist nor the document queue keeps presenting stale work.
    """
    if (
        source.signal != StepSignal.COMPLETED
        or source.participant_subject_id is None
        or not source.detail.get("catalogue_term_uid")
    ):
        return
    rows = (
        (
            await session.execute(
                text(
                    "SELECT cs.case_id, cs.request_item_id, ri.request_id "
                    "FROM flows.case_steps cs "
                    "JOIN flows.cases c ON c.id = cs.case_id "
                    "LEFT JOIN documents.request_items ri ON ri.id = cs.request_item_id "
                    "WHERE c.execution_id = :e AND cs.step_type = 'accept_terms' "
                    "  AND cs.step_key = :k AND cs.participant_subject_id = :p "
                    "  AND cs.case_id <> :source_case "
                    "  AND cs.status NOT IN ('fulfilled', 'skipped', 'cancelled')"
                ),
                {
                    "e": execution_id,
                    "k": source.step_key,
                    "p": source.participant_subject_id,
                    "source_case": source.case_id,
                },
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        item_id = row["request_item_id"]
        request_id = row["request_id"]
        if item_id is not None:
            await session.execute(
                text(
                    "UPDATE documents.request_items SET status = 'fulfilled', fulfilled_at = now() "
                    "WHERE id = :id AND status = 'pending'"
                ),
                {"id": item_id},
            )
        if request_id is not None:
            await session.execute(
                text(
                    "UPDATE documents.requests SET status = 'fulfilled', completed_at = now() "
                    "WHERE id = :id AND NOT EXISTS ("
                    "  SELECT 1 FROM documents.request_items "
                    "  WHERE request_id = :id AND status = 'pending'"
                    ")"
                ),
                {"id": request_id},
            )
        duplicate = StepEvent(
            signal=StepSignal.COMPLETED,
            case_id=int(row["case_id"]),
            step_key=source.step_key,
            participant_subject_id=source.participant_subject_id,
            reason="already_accepted",
        )
        await _write_step_status(session, duplicate.case_id, duplicate.step_key, duplicate)
        await _append_event(
            session,
            tenant_id=tenant_id,
            execution_id=execution_id,
            case_id=duplicate.case_id,
            event_type=duplicate.signal.value,
            payload={
                "step_key": duplicate.step_key,
                "participant": duplicate.participant_subject_id,
                "reason": duplicate.reason,
                "actor": None,
                "note": None,
            },
            idempotency_key=(
                f"{duplicate.case_id}:{duplicate.step_key}:"
                f"{duplicate.participant_subject_id or '-'}"
            ),
        )
        await _emit_task_requests(
            session, tenant_id=tenant_id, execution_id=execution_id, event=duplicate
        )


async def _emit_task_requests(
    session: AsyncSession, *, tenant_id: int, execution_id: int, event: StepEvent
) -> None:
    """Enqueue a ``task.requested`` outbox row (same tx as the transition — transactional outbox)
    for each raise-a-task spec on the step whose trigger matches this signal (docs/tasks.md).

    Delivery is the dispatcher's job; failures there retry/dead-letter without ever touching the
    flow. source_ref is deterministic, so redelivery (and a re-fired transition) collapses on the
    tasks table's ``tasks_source_ref_key`` — no dedupe needed here.

    The spec's title and description are **interpolated** against the case's own context before
    they are enqueued (``app/flows/interpolation.py``'s ``{{ key || fallback }}``), so a journey
    can raise "Review {{ memory.risk_band || 'unrated' }} client {{ contact.name }}" and an
    operator gets a task that says what it is about. Rendered HERE rather than at delivery for the
    reason the outbox exists: the values are true at the moment of the transition, and a
    dispatcher retrying tomorrow must not quietly print tomorrow's facts.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT e.uid, fv.definition, f.name AS flow_name "
                    "FROM flows.executions e "
                    "JOIN flows.flow_versions fv ON fv.id = e.flow_version_id "
                    "JOIN flows.flows f ON f.id = e.flow_id "
                    "WHERE e.id = :e"
                ),
                {"e": execution_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return
    definition = row["definition"]
    if isinstance(definition, str):
        definition = json.loads(definition)
    steps = definition.get("steps") or [] if isinstance(definition, dict) else []
    step = next((s for s in steps if isinstance(s, dict) and s.get("id") == event.step_key), None)
    if step is None:
        return
    trigger = event.signal.value.removeprefix("step.")
    # The index is the spec's position in the AUTHORED list, not in the filtered one: it is part of
    # `source_ref`, which is what collapses redelivery on the tasks table's unique key. Re-basing it
    # would make every already-raised task look new.
    specs = [
        (i, spec)
        for i, spec in enumerate(step.get("tasks") or [])
        if isinstance(spec, dict) and spec.get("on", "completed") == trigger
    ]
    if not specs:
        return
    # Built once, and only when a spec actually fires — the context costs a handful of queries and
    # the overwhelming majority of transitions raise no task at all.
    context = await case_context(session, tenant_id=tenant_id, case_id=event.case_id)
    for i, spec in specs:
        participant = event.participant_subject_id
        source_ref = f"{row['uid']}:{event.step_key}:{participant or '-'}:{trigger}:{i}"
        await notifications.enqueue(
            session,
            tenant_id=tenant_id,
            event_type="task.requested",
            payload={
                "spec": _rendered_task_spec(spec, context),
                "execution_uid": row["uid"],
                "flow_name": row["flow_name"],
                "step_key": event.step_key,
                "step_title": step.get("title"),
                "participant_subject_id": participant,
                "source_ref": source_ref,
            },
        )


def _rendered_task_spec(spec: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """A raise-a-task spec with its human-facing copy interpolated. Everything else is untouched.

    Only ``title`` and ``description``: the rest of the spec is structure (a role slug, a category,
    a due offset) that the dispatcher resolves against real tables, and interpolating an id from
    author-supplied text is how a task gets assigned to the wrong person.
    """
    rendered = dict(spec)
    for key in ("title", "description"):
        value = spec.get(key)
        if isinstance(value, str) and value:
            rendered[key] = interpolation.render(value, context)
    return rendered


def _as_dict(value: Any) -> dict[str, Any]:
    """A jsonb column as a dict, whatever the driver handed back (dict or text)."""
    if isinstance(value, str):
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    return dict(value) if isinstance(value, dict) else {}


async def _assert_fulfilment_supported(
    session: AsyncSession,
    *,
    tenant_id: int,
    row: Any,
    detail: dict[str, Any],
    participant_subject_id: int | None = None,
) -> None:
    """Refuse a fulfilment claim the step's own evidence does not support.

    Two invariants, enforced HERE so no route can miss them (the staff ``/action`` endpoint used to
    reach exactly these transitions with neither):

    1. **A settled step does not re-transition.** Replaying a fulfilment onto a ``fulfilled``,
       ``cancelled`` or ``skipped`` instance would re-run the module's side effects.
    2. **A request-backed step needs its artifact.** ``fill_form``/``accept_terms``/
       ``upload_document`` are completed *by producing something* — answers, an acknowledgement, a
       file. Until the backing ``documents.request_items`` row is ``fulfilled`` there is nothing to
       complete, and for a form the stored answers must cover the form's required questions. This is
       what stops a ``cases:write`` holder ticking off the client's own confirmation and destroying
       the only evidence the client ever agreed — whichever endpoint they use.

    Deliberately evidence-based rather than actor-based: an "only staff may do this" rule has to be
    re-derived at every door and says nothing about *task*-type steps that legitimately have no
    artifact, whereas "the artifact must exist" is a property of the step itself.
    """
    step_type = str(row["step_type"])
    if str(row["status"]) in _SETTLED_STEP_STATUSES:
        raise StepTransitionRefused(
            f"This step is already {row['status']} — it cannot be completed again."
        )
    if step_type not in _REQUEST_BACKED:
        return
    if row["request_item_id"] is None or str(row["item_status"]) != "fulfilled":
        awaited = _AWAITED_ARTIFACT.get(step_type, "its artifact")
        raise StepTransitionRefused(
            f"This step is waiting for {awaited}. It cannot be marked done without that — "
            "the record of what was provided is the point of the step."
        )
    if step_type != "fill_form":
        return
    answers = detail.get("answers")
    if not isinstance(answers, dict):
        answers = await _stored_answers(session, int(row["request_item_id"]))
    if answers is None:
        raise StepTransitionRefused(
            "No answers are recorded against this form, so it cannot be completed."
        )
    missing = await form_validation.missing_required_answers(
        session,
        tenant_id=tenant_id,
        # documents.py falls back to the item's category as the legacy form code — mirror it, so the
        # same submission is judged against the same form on both paths.
        form_uid=str(row["form_uid"] or row["item_category"] or ""),
        answers=answers,
    )
    if missing:
        raise StepTransitionRefused("These answers are required: " + ", ".join(missing[:8]) + ".")
    contact_id = participant_subject_id
    if (
        contact_id is None
        and str(row["subject_type"]) == "contact"
        and row["subject_id"] is not None
    ):
        contact_id = int(row["subject_id"])
    refused = await form_validation.national_insurance_refusal(
        session,
        tenant_id=tenant_id,
        form_uid=str(row["form_uid"] or row["item_category"] or ""),
        answers=answers,
        contact_id=int(contact_id) if contact_id is not None else None,
    )
    if refused:
        raise StepTransitionRefused(refused)


async def _assert_identity_evidence_present(
    session: AsyncSession, *, tenant_id: int, row: Any
) -> None:
    """Refuse to complete an identity check while the evidence it rests on is still outstanding.

    A manual identity check IS the two documents ``app/idv/manual.py`` asks for. With the typed
    Pass / Refer gone (``_assert_check_not_decided_by_hand``) the only completion that reaches
    here is the engine's own evidence hook, which already waits for both — this is the belt to
    that brace, so no future caller can record an identity check with neither the photo ID nor
    the proof of address ever supplied.

    The test is the recorded evidence, not the configured provider: ``identity_documents`` is
    written whenever the manual path is taken from EITHER door (the client choosing it, or the
    adviser requiring it at kickoff), and a digital check records none, so it is untouched. A step
    holding no recorded items is also untouched — the evidence was never asked for, and blocking
    it would leave a journey nobody could finish.
    """
    if str(row["step_type"]) != "verify_identity":
        return
    recorded = _as_dict(row["data"]).get("identity_documents")
    if not recorded:
        return
    documents = await idv_manual.documents_for(session, tenant_id=tenant_id, recorded=recorded)
    # ``pending``, not "anything but fulfilled": a CANCELLED item is one the firm stopped asking
    # for, and treating it as outstanding would hold the check with no way to satisfy it.
    outstanding = [str(d["title"]) for d in documents if str(d["status"]) == "pending"]
    if outstanding:
        raise StepTransitionRefused(
            "This identity check is still waiting for: "
            + ", ".join(outstanding)
            + ". The decision is a judgement on that evidence, so it has to be filed first."
        )


#: Step types whose outcome is never a person's verdict. Identity is settled by the digital
#: provider's result or by the manual evidence landing; AML is settled by the screen. See
#: ``_assert_check_not_decided_by_hand``.
_AUTOMATED_CHECK_TYPES = frozenset({"verify_identity", "aml_check"})

#: The marker the engine's own evidence hook sets on the detail it submits for an identity check.
#: Routes build their detail from a fixed set of fields, so nothing outside this module can set it.
_EVIDENCE_COMPLETE = "evidence_complete"


def _assert_check_not_decided_by_hand(row: Any, detail: dict[str, Any]) -> None:
    """Refuse a Pass / Refer typed at an identity or AML step.

    Both are mandatory checks, and neither is a judgement a staff member records: an identity
    check is settled by the digital provider's result (``on_identity_result``) or by the manual
    path's two documents landing (``_settle_identity_from_evidence``), and an AML screen by the
    screening pipeline (``on_aml_result``). Those paths apply their event directly and never reach
    here. What reaches here is a person with ``cases:write`` submitting an outcome — the control
    the journey page used to offer, and the door this closes on the API side so removing the
    buttons is not the only thing standing between a click and an unverified pass.

    ``review`` (the compliance gate) is deliberately NOT in the set: that step exists to hold a
    person's decision.
    """
    if str(row["step_type"]) not in _AUTOMATED_CHECK_TYPES:
        return
    if detail.get(_EVIDENCE_COMPLETE) is True:
        return
    what = "identity check" if str(row["step_type"]) == "verify_identity" else "AML screen"
    raise StepTransitionRefused(
        f"An {what} is not passed or referred by hand. It completes when the check itself does — "
        "the provider's result, or the client's identity documents landing."
    )


async def _stored_answers(session: AsyncSession, request_item_id: int) -> dict[str, Any] | None:
    raw = (
        await session.execute(
            text(
                "SELECT answers FROM documents.form_responses WHERE request_item_id = :i "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"i": request_item_id},
        )
    ).scalar_one_or_none()
    if raw is None:
        return None
    return _as_dict(raw)


async def _assert_gate_open_for_setup(
    session: AsyncSession, *, case_id: int, tenant_id: int, step_type: str
) -> None:
    """Refuse to open an account while its owners' KYC is still outstanding.

    The account tier's entry gate ("every gating owner's profile case must be complete" — their
    fact-find, identity and AML, exactly as ``_recompute`` computes ``gate_open``) was derived onto
    the CASE (``derivation.derive_case_state``) and shown in the portal, but never asserted at a
    transition. The ``setup_account`` STEP stays ``pending`` while its case is ``blocked``, so a
    ``cases:write`` holder could record the platform hand-off — the step whose whole job is opening
    the account with the custodian — before identity or AML had been recorded at all.

    Scoped deliberately to ``setup_account`` rather than "nothing may happen in a blocked case": the
    terms declarations live in that same account case and MUST be acceptable first (the legacy state
    machine gates identity *behind* the terms — ``computeDerivedApplicationStatus`` only looks at
    identity once ``termsGateSatisfied``). A blanket case-level refusal would deadlock the tier.
    """
    if step_type != "setup_account":
        return
    outstanding = (
        await session.execute(
            text(
                "SELECT count(*) FROM flows.cases acct "
                "JOIN crm.account_contacts ac ON ac.account_id = acct.subject_id "
                "JOIN crm.contacts ct ON ct.id = ac.contact_id "
                "JOIN flows.cases prof ON prof.execution_id = acct.execution_id "
                "  AND prof.kind = 'profile' AND prof.subject_id = ac.contact_id "
                "WHERE acct.id = :c AND acct.kind = 'account' AND ct.type = 'individual' "
                "  AND ac.role IN ('owner', 'decision_maker', 'trustee') "
                "  AND prof.status <> 'completed'"
            ),
            {"c": case_id},
        )
    ).scalar_one()
    if int(outstanding):
        raise StepTransitionRefused(
            "The account cannot be opened until every owner's onboarding steps are complete."
        )
    owners = [
        int(v)
        for v in (
            await session.execute(
                text(
                    "SELECT DISTINCT ac.contact_id FROM crm.account_contacts ac "
                    "JOIN crm.contacts ct ON ct.id = ac.contact_id "
                    "JOIN flows.cases acct ON acct.subject_id = ac.account_id "
                    "WHERE acct.id = :c AND acct.kind = 'account' "
                    "  AND ct.type = 'individual' "
                    "  AND ac.role IN ('owner', 'decision_maker', 'trustee')"
                ),
                {"c": case_id},
            )
        ).scalars()
    ]
    account = (
        (
            await session.execute(
                text(
                    "SELECT p.product_type::text AS product, "
                    "       (a.transfer IS NOT NULL) AS has_transfer "
                    "FROM flows.cases c "
                    "JOIN platform.accounts a ON a.id = c.subject_id "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "WHERE c.id = :c AND c.kind = 'account'"
                ),
                {"c": case_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if not owners:
        return
    products = [account["product"] if account is not None else None]
    has_transfer = bool(account["has_transfer"]) if account is not None else False
    titles = await _outstanding_onboarding_titles(
        session,
        tenant_id=tenant_id,
        contact_ids=owners,
        products=products,
        has_transfer=has_transfer,
    )
    if not titles:
        return
    # A transfer chosen on Account details is not on the plan. The walk creates that
    # declaration when the fork is taken, but a journey that already walked past it still
    # has no step — opening then refuses a term the page does not show. Catch up in its own
    # transaction so the refusal does not roll the new step back, then ask again.
    execution_id = (
        await session.execute(
            text("SELECT execution_id FROM flows.cases WHERE id = :c AND tenant_id = :t"),
            {"c": case_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if execution_id is not None:
        await catch_up_omitted_steps(session, execution_id=int(execution_id), tenant_id=tenant_id)
        titles = await _outstanding_onboarding_titles(
            session,
            tenant_id=tenant_id,
            contact_ids=owners,
            products=products,
            has_transfer=has_transfer,
        )
    if titles:
        raise StepTransitionRefused(_terms_refusal(titles))


async def submit_step(
    session: AsyncSession,
    *,
    tenant_id: int,
    case_id: int,
    step_key: str,
    detail: dict[str, Any] | None = None,
    participant_subject_id: int | None = None,
) -> StepEvent | None:
    """The external entry point when a step's artifact is fulfilled (a form submitted, a review
    outcome recorded, a provider webhook). Routes through the step's HANDLER — so the module runs
    its side-effects (record the KYC pass, write the profile back to crm.contacts, …) — then applies
    the returned event. Callers (routers/webhooks) use this, not ``apply_step_event`` directly.

    Returns the applied ``StepEvent`` (or ``None`` when the step/handler is missing) so a staff
    action response can surface handler detail — e.g. setup_account's ``local_activated`` /
    ``custodian_open`` — without a second round trip.

    **Every route inherits ``_assert_fulfilment_supported`` from here.** The step invariants (a
    request-backed step needs its artifact actually fulfilled; a form needs its required questions
    answered) used to live in the staff form endpoint alone, which left
    ``POST /applications/{uid}/action`` — same ``cases:write``, same transition — able to complete a
    client's fact-find with no answers at all. The invariant belongs to the transition, not a door.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT cs.step_type, cs.status, cs.assignee, cs.data, cs.request_item_id, "
                    "       ri.status AS item_status, ri.form_uid, ri.category AS item_category, "
                    "       c.execution_id, c.subject_type, c.subject_id, fv.definition "
                    "FROM flows.case_steps cs "
                    "JOIN flows.cases c ON c.id = cs.case_id "
                    "JOIN flows.executions e ON e.id = c.execution_id "
                    "JOIN flows.flow_versions fv ON fv.id = e.flow_version_id "
                    "LEFT JOIN documents.request_items ri ON ri.id = cs.request_item_id "
                    "WHERE cs.case_id = :c AND cs.step_key = :k "
                    "  AND (CAST(:psid AS int) IS NULL "
                    "       OR cs.participant_subject_id = CAST(:psid AS int)) LIMIT 1"
                ),
                {"c": case_id, "k": step_key, "psid": participant_subject_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    handler = handlers.get_handler(str(row["step_type"]))
    if handler is None:
        return None
    await _assert_fulfilment_supported(
        session,
        tenant_id=tenant_id,
        row=row,
        detail=detail or {},
        participant_subject_id=participant_subject_id,
    )
    await _assert_gate_open_for_setup(
        session, case_id=case_id, tenant_id=tenant_id, step_type=str(row["step_type"])
    )
    _assert_check_not_decided_by_hand(row, detail or {})
    await _assert_identity_evidence_present(session, tenant_id=tenant_id, row=row)
    definition = row["definition"]
    if isinstance(definition, str):
        definition = json.loads(definition)
    config: dict[str, Any] = next(
        (s.get("config", {}) for s in definition.get("steps", []) if s.get("id") == step_key), {}
    )
    ctx = handlers.StepContext(
        session=session,
        tenant_id=tenant_id,
        execution_id=int(row["execution_id"]),
        case_id=case_id,
        step_key=step_key,
        config=config,
        subject_type=row["subject_type"],
        subject_id=row["subject_id"],
        participant_subject_id=participant_subject_id,
        request_id=None,
        # The instance's own recorded state (the KYC check this step opened, the document it queued)
        # — without it a handler can only find "the latest row that looks like mine", which crosses
        # wires between two concurrent journeys for the same person.
        data=_as_dict(row["data"]),
    )
    event = await handler.handle(ctx, detail or {})
    await _promote_memory(
        session,
        tenant_id=tenant_id,
        case_id=case_id,
        step_key=step_key,
        config=config,
        detail=detail or {},
        request_item_id=row["request_item_id"],
    )
    # Carry the caller's rationale + identity onto the event so the step records WHO decided and WHY
    # (a review outcome with no recorded reason is not an auditable compliance decision).
    supplied = detail or {}
    decided_by = (await session.execute(text("SELECT auth.uid()"))).scalar_one_or_none()
    extra = {
        k: v
        for k, v in (
            ("note", supplied.get("note")),
            ("outcome", supplied.get("outcome")),
            ("decided_by", str(decided_by) if decided_by else None),
        )
        if v is not None
    }
    if extra:
        event = replace(event, detail={**event.detail, **extra})
    await apply_step_event(session, tenant_id, event)
    return event


async def amend_completed_form(
    session: AsyncSession,
    *,
    tenant_id: int,
    case_id: int,
    step_key: str,
    answers: dict[str, Any],
    participant_subject_id: int | None = None,
) -> None:
    """Reapply a completed staff form without replaying its graph transition.

    Onboarding keeps the submitted responses as an append-only history, but until the adviser
    declares and hands the journey to the client the latest answer set may replace the facts in the
    book. Calling ``submit_step`` here would emit a second completion and advance the graph twice;
    this deliberately invokes only the form handler's idempotent write-back half.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT cs.step_type, cs.status, cs.request_item_id, c.execution_id, "
                    "       c.subject_type, c.subject_id, fv.definition "
                    "FROM flows.case_steps cs "
                    "JOIN flows.cases c ON c.id = cs.case_id "
                    "JOIN flows.executions e ON e.id = c.execution_id "
                    "JOIN flows.flow_versions fv ON fv.id = e.flow_version_id "
                    "WHERE cs.case_id = :c AND cs.step_key = :k AND cs.tenant_id = :t "
                    "  AND (CAST(:psid AS int) IS NULL "
                    "       OR cs.participant_subject_id = CAST(:psid AS int)) LIMIT 1"
                ),
                {"c": case_id, "k": step_key, "t": tenant_id, "psid": participant_subject_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or str(row["step_type"]) != "fill_form" or str(row["status"]) != "fulfilled":
        raise StepTransitionRefused("Only a completed form can be amended.")
    definition = row["definition"]
    if isinstance(definition, str):
        definition = json.loads(definition)
    config: dict[str, Any] = next(
        (s.get("config", {}) for s in definition.get("steps", []) if s.get("id") == step_key), {}
    )
    handler = handlers.get_handler("fill_form")
    if handler is None:  # pragma: no cover - registry invariant
        raise StepTransitionRefused("The form handler is unavailable.")
    event = await handler.handle(
        handlers.StepContext(
            session=session,
            tenant_id=tenant_id,
            execution_id=int(row["execution_id"]),
            case_id=case_id,
            step_key=step_key,
            config=config,
            subject_type=row["subject_type"],
            subject_id=row["subject_id"],
            participant_subject_id=participant_subject_id,
            request_id=None,
            data={},
        ),
        {"answers": answers},
    )
    if event.signal != StepSignal.COMPLETED:
        raise StepTransitionRefused(event.reason or "The amended answers could not be applied.")
    await _promote_memory(
        session,
        tenant_id=tenant_id,
        case_id=case_id,
        step_key=step_key,
        config=config,
        detail={"answers": answers},
        request_item_id=row["request_item_id"],
    )


async def _promote_memory(
    session: AsyncSession,
    *,
    tenant_id: int,
    case_id: int,
    step_key: str,
    config: dict[str, Any],
    detail: dict[str, Any],
    request_item_id: Any,
) -> None:
    """Promote the handful of answers the author asked the journey to remember.

    Driven by ``fill_form.config.remember`` — ``{memory_key: answer_field}`` — and by nothing
    else. The alternative design (copy every answer into memory when a form completes) is the one
    this exists instead of: a fact-find carries hundreds of the client's most sensitive fields, and
    duplicating them into a second store that branches test and emails print would be a
    data-protection decision nobody made. See ``app/flows/memory.py``.

    Reads the stored answers when the caller did not hand any over (a staff ``/action`` completing
    a form the client filled in days ago), so the promotion does not depend on which door the
    fulfilment came through.
    """
    mapping = config.get("remember")
    if not isinstance(mapping, dict) or not mapping:
        return
    answers = detail.get("answers")
    outputs = detail.get("outputs")
    # A door that stored the response and did not score it (an amendment, or a caller that
    # handed answers over without the computed outputs) still has to remember the band the
    # server computed, not a `band` key sitting in the posted answers.
    if not isinstance(outputs, dict) and request_item_id is not None:
        stored_answers, outputs = await scoring_mod.score_stored_response(
            session, int(request_item_id)
        )
        if isinstance(stored_answers, dict):
            answers = stored_answers
    if not isinstance(answers, dict) and request_item_id is not None:
        answers = await _stored_answers(session, int(request_item_id))
    if not isinstance(answers, dict):
        return
    await memory_mod.promote_from_answers(
        session,
        tenant_id=tenant_id,
        case_id=case_id,
        mapping=mapping,
        answers=answers,
        outputs=outputs if isinstance(outputs, dict) else None,
        node_id=step_key,
    )


async def _pinned_definition(session: AsyncSession, execution_id: int) -> dict[str, Any]:
    """The execution's PINNED definition — a running journey never picks up a newer version's
    authoring, which is why every consumer reads it through the execution rather than the flow."""
    raw = (
        await session.execute(
            text(
                "SELECT fv.definition FROM flows.executions e "
                "JOIN flows.flow_versions fv ON fv.id = e.flow_version_id WHERE e.id = :e"
            ),
            {"e": execution_id},
        )
    ).scalar_one_or_none()
    if raw is None:
        return {}
    definition = json.loads(raw) if isinstance(raw, str) else raw
    return definition if isinstance(definition, dict) else {}


async def _definition_steps(session: AsyncSession, execution_id: int) -> dict[str, dict[str, Any]]:
    """The execution's pinned definition steps, keyed by step id — the config a released step needs.

    A v3 definition carries the derived ``steps`` projection alongside its ``nodes`` (see
    ``definitions.validate_definition``), so this reads a graph flow unchanged.
    """
    definition = await _pinned_definition(session, execution_id)
    steps = definition.get("steps") or []
    return {str(s["id"]): s for s in steps if isinstance(s, dict) and s.get("id")}


async def _definition_graph(session: AsyncSession, execution_id: int) -> graph_mod.Graph | None:
    """The execution's pinned definition as a ``Graph``, or ``None`` if it cannot be read.

    ``None`` rather than an exception: a definition that no longer parses is a stored document
    from an older validator, and the honest response is "no graph runtime for this execution" —
    the dependency machinery underneath still runs it, exactly as it did before v3.
    """
    definition = await _pinned_definition(session, execution_id)
    if not definition:
        return None
    try:
        return graph_mod.parse(definition)
    except graph_mod.DefinitionError as exc:
        logger.warning(
            "a running execution's definition no longer parses as a graph; "
            "falling back to dependency-only ordering",
            extra={
                "event": "flow_graph_unreadable",
                "execution_id": execution_id,
                "reason": str(exc)[:200],
            },
        )
        return None


# ---------------------------------------------------------------------------------------------
# The condition context a running case is judged against
# ---------------------------------------------------------------------------------------------


async def case_context(session: AsyncSession, *, tenant_id: int, case_id: int) -> dict[str, Any]:
    """The facts a branch node evaluates against, and the values its copy interpolates.

    The vocabulary is ``conditions.FIELD_SCOPES`` — the SAME registry the builder renders as a
    picker and the same one an automation's entry conditions read (``automation.build_facts``),
    because an author who learned the field names once should not have to learn them again inside
    a journey. This one is the runtime half: it can fill the scopes an entry condition cannot see
    (``application.*``, ``case.*``, and ``memory.*``, which is the whole reason case memory exists).

    ``memory`` arrives NESTED (``{"memory": {...}}``) rather than as flat ``memory.x`` keys, so one
    object serves both resolvers — see ``memory.context``.

    Two deliberate absences. ``account.value`` is lake-derived and is not fetched: a branch on a
    live valuation would put a DuckDB read on the step-transition path, and an unresolved field
    reads as ``None``, which only ``isEmpty``/``notEquals`` can match — visible, and never a silent
    true. ``applicant.*`` is not built either: that is the PLANNER's per-participant vocabulary
    (``planning._participant_context``) and it is evaluated at plan time, where the participant is
    unambiguous; a case-level branch has no single applicant to speak for.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT c.status, c.subject_type, c.subject_id, c.subject_contact_id, "
                    "       c.created_at, f.code AS flow_code, e.crm_application_id "
                    "FROM flows.cases c "
                    "JOIN flows.flows f ON f.id = c.flow_id "
                    "LEFT JOIN flows.executions e ON e.id = c.execution_id "
                    "WHERE c.id = :c AND c.tenant_id = :t"
                ),
                {"c": case_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return await memory_mod.context(session, case_id)

    # Imported here rather than at module scope: this module already sits at the centre of the
    # engine's import graph, and `app.tags` reaches back into it through the flows outbox.
    from app import tags as tags_mod

    now = datetime.now(UTC)
    facts: dict[str, Any] = {
        "case.status": row["status"],
        "case.flow_code": row["flow_code"],
        "case.started_at": row["created_at"],
        "case.tags": await tags_mod.list_for(
            session, target="case", target_id=case_id, tenant_id=tenant_id
        ),
        # UTC, not the firm's zone. `time.*` in a BRANCH is asking "what time is it as this case
        # moves", which is a machine instant; the firm-local reading belongs to the scheduled
        # trigger pass, which already has the tenant's timezone in hand.
        "time.hour": now.hour,
        "time.weekday": now.weekday(),
    }
    facts.update(await memory_mod.context(session, case_id))

    contact_id = row["subject_contact_id"] or (
        row["subject_id"] if row["subject_type"] == "contact" else None
    )
    if contact_id is not None:
        contact = (
            (
                await session.execute(
                    text(
                        "SELECT c.type::text AS type, c.service_type::text AS service_type, "
                        "       c.sub_type::text AS sub_type, c.enabled, c.nationality, "
                        "       EXISTS (SELECT 1 FROM crm.contact_users cu "
                        "               WHERE cu.contact_id = c.id) AS has_login "
                        "FROM crm.contacts c WHERE c.id = :id AND c.tenant_id = :t"
                    ),
                    {"id": int(contact_id), "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if contact is not None:
            # Inside the `is not None` branch for the reason automation.build_facts documents: a
            # deleted client must leave every `contact.*` key ABSENT, not present-and-empty.
            facts.update({f"contact.{k}": v for k, v in contact.items()})
            facts["contact.tags"] = await tags_mod.list_for(
                session, target="contact", target_id=int(contact_id), tenant_id=tenant_id
            )
    if row["subject_type"] == "account" and row["subject_id"] is not None:
        account = (
            (
                await session.execute(
                    text(
                        "SELECT p.product_type::text AS product, "
                        "       p.product_type::text AS product_type, "
                        "       a.custody::text AS custody, "
                        "       a.status, (a.transfer IS NOT NULL) AS has_transfer, "
                        "       (a.model_id IS NOT NULL) AS discretionary "
                        "FROM platform.accounts a "
                        "LEFT JOIN platform.products p ON p.id = a.product_id "
                        "WHERE a.id = :id AND a.tenant_id = :t"
                    ),
                    {"id": int(row["subject_id"]), "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if account is not None:
            facts.update({f"account.{k}": v for k, v in account.items()})
    if row["crm_application_id"] is not None:
        application = (
            (
                await session.execute(
                    text(
                        "SELECT a.custody::text AS custody, a.status, "
                        "  EXISTS (SELECT 1 FROM platform.accounts ac "
                        "          JOIN platform.products p ON p.id = ac.product_id "
                        "          WHERE ac.application_id = a.id "
                        "            AND (upper(p.product_type::text) IN ('SIPP', 'TPPENSION') "
                        "                 OR upper(p.product_type::text) LIKE '%PENSION%')"
                        "  ) AS has_pension_product, "
                        "  EXISTS (SELECT 1 FROM platform.accounts ac "
                        "          JOIN platform.products p ON p.id = ac.product_id "
                        "          WHERE ac.application_id = a.id "
                        "            AND upper(p.product_type::text) = 'SIPP'"
                        "  ) AS has_sipp_product, "
                        "  EXISTS (SELECT 1 FROM platform.accounts ac "
                        "          WHERE ac.application_id = a.id AND ac.transfer IS NOT NULL"
                        "  ) AS has_transfer "
                        "FROM crm.applications a WHERE a.id = :id AND a.tenant_id = :t"
                    ),
                    {"id": int(row["crm_application_id"]), "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if application is not None:
            facts.update({f"application.{k}": v for k, v in application.items()})
    return facts


# ---------------------------------------------------------------------------------------------
# The graph walk
# ---------------------------------------------------------------------------------------------


async def _graph_state(session: AsyncSession, case_id: int) -> dict[str, Any]:
    data = _as_dict(
        (
            await session.execute(
                text("SELECT data FROM flows.cases WHERE id = :c"), {"c": case_id}
            )
        ).scalar_one_or_none()
    )
    state = data.get(_GRAPH_STATE_KEY)
    state = dict(state) if isinstance(state, dict) else {}
    visited = state.get(_VISITED_KEY)
    state[_VISITED_KEY] = dict(visited) if isinstance(visited, dict) else {}
    return state


async def _save_graph_state(session: AsyncSession, case_id: int, state: dict[str, Any]) -> None:
    await session.execute(
        text("UPDATE flows.cases SET data = data || CAST(:d AS jsonb) WHERE id = :c"),
        {"d": json.dumps({_GRAPH_STATE_KEY: state}, default=str), "c": case_id},
    )


async def _planned_anywhere(session: AsyncSession, execution_id: int, step_key: str) -> bool:
    return (
        await session.execute(
            text(
                "SELECT 1 FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                "WHERE c.execution_id = :e AND cs.step_key = :k LIMIT 1"
            ),
            {"e": execution_id, "k": step_key},
        )
    ).first() is not None


async def _mark_graph_released(session: AsyncSession, case_id: int, step_key: str) -> int:
    """Let a control-gated step through. Returns how many instances were released.

    Every instance in the case, not one: a control node is case-scoped (see the section header),
    so releasing "the terms declaration" after a branch releases it for both joint owners. The
    dependency pass still decides WHEN each instance is instantiated — this only removes the gate.
    """
    released = (
        await session.execute(
            text(
                "UPDATE flows.case_steps SET data = "
                "  (data - CAST(:gated AS text)) || CAST(:d AS jsonb) "
                "WHERE case_id = :c AND step_key = :k RETURNING id"
            ),
            {
                "gated": _GATED_ON_KEY,
                "d": json.dumps({_GRAPH_RELEASED_KEY: True}),
                "c": case_id,
                "k": step_key,
            },
        )
    ).all()
    return len(released)


async def _mark_gated(session: AsyncSession, case_id: int, step_key: str, gate: str) -> None:
    """Record WHY a step is held — the control node it is waiting behind."""
    await session.execute(
        text(
            "UPDATE flows.case_steps SET data = data || CAST(:d AS jsonb) "
            "WHERE case_id = :c AND step_key = :k"
        ),
        {"d": json.dumps({_GATED_ON_KEY: gate}), "c": case_id, "k": step_key},
    )


async def _elide_step(session: AsyncSession, case_id: int, step_key: str, reason: str) -> int:
    """Mark an untaken arm's step ``skipped`` — the status plan-time visibility already uses.

    ``status = 'pending'`` only, and that predicate is load-bearing: a step that has already been
    fulfilled cannot be un-done by a later branch, and one that failed must stay visible for
    remediation rather than being tidied away by a decision taken downstream of it.
    """
    elided = (
        await session.execute(
            text(
                "UPDATE flows.case_steps SET status = 'skipped', "
                "  data = (data - CAST(:gated AS text)) || CAST(:d AS jsonb) "
                "WHERE case_id = :c AND step_key = :k AND status = 'pending' RETURNING id"
            ),
            {
                "gated": _GATED_ON_KEY,
                "d": json.dumps({"skipped_reason": reason}),
                "c": case_id,
                "k": step_key,
            },
        )
    ).all()
    return len(elided)


def _terms_refusal(titles: list[str]) -> str:
    """The opening-gate sentence, naming the declarations still outstanding."""
    base = "The account cannot be opened until every owner's required terms are accepted"
    if len(titles) == 1:
        return f"{base}: {titles[0]}."
    if len(titles) == 2:
        return f"{base}: {titles[0]} and {titles[1]}."
    return f"{base}: {', '.join(titles[:-1])} and {titles[-1]}."


async def _outstanding_onboarding_titles(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_ids: list[int],
    products: list[str | None],
    has_transfer: bool,
) -> list[str]:
    """Titles of the firm's onboarding terms these owners have not accepted, in catalogue order."""
    titles: list[str] = []
    seen: set[str] = set()
    axes = products or [None]
    for contact_id in contact_ids:
        for product in axes:
            leftover = await terms_svc.outstanding_terms(
                session,
                terms_svc.OutstandingContext(
                    required_at="onboarding",
                    subject="contact",
                    contact_id=contact_id,
                    product=product,
                    has_transfer=has_transfer,
                    tenant_id=tenant_id,
                    owner="tenant",
                ),
            )
            for row in leftover:
                title = str(row.get("title") or row.get("code") or "Terms")
                if title in seen:
                    continue
                seen.add(title)
                titles.append(title)
    return titles


def _as_date(value: object) -> date | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


async def _application_graph(
    session: AsyncSession, tenant_id: int, application_id: int
) -> ApplicationGraph:
    """The application's parties and accounts, as they stand now.

    The same shape ``routers.applications._graph`` builds at kickoff. Planning ran against that
    snapshot; this one is read again when a step the plan omitted might now apply, because a
    transfer is written by Account details rather than at the start.
    """
    rows = (
        (
            await session.execute(
                text(
                    "SELECT ac.account_id, ac.contact_id, ac.role::text AS role, ct.type, ct.dob, "
                    "       p.product_type::text AS product, acc.custody::text AS acc_custody, "
                    "       (acc.transfer IS NOT NULL) AS has_transfer "
                    "FROM crm.account_contacts ac "
                    "JOIN crm.contacts ct ON ct.id = ac.contact_id "
                    "LEFT JOIN platform.accounts acc ON acc.id = ac.account_id "
                    "LEFT JOIN platform.products p ON p.id = acc.product_id "
                    "WHERE ac.application_id = :a AND ac.tenant_id = :t "
                    "ORDER BY ac.account_id, ac.contact_id, ac.role::text"
                ),
                {"a": application_id, "t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    custody = (
        await session.execute(
            text("SELECT custody::text FROM crm.applications WHERE id = :a AND tenant_id = :t"),
            {"a": application_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    roles: dict[int, set[str]] = {}
    types: dict[int, str] = {}
    dobs: dict[int, date | None] = {}
    accounts: dict[int, list[int]] = {}
    registered: dict[int, list[int]] = {}
    meta: dict[int, dict[str, Any]] = {}
    for row in rows:
        contact_id = int(row["contact_id"])
        role = str(row["role"] or "")
        if role:
            roles.setdefault(contact_id, set()).add(role)
        types[contact_id] = str(row["type"] or "individual")
        dobs[contact_id] = _as_date(row["dob"])
        if row["account_id"] is None:
            continue
        account_id = int(row["account_id"])
        parties = accounts.setdefault(account_id, [])
        if contact_id not in parties:
            parties.append(contact_id)
        if role == "registered_contact":
            named = registered.setdefault(account_id, [])
            if contact_id not in named:
                named.append(contact_id)
        meta[account_id] = {
            "product": row["product"],
            "custody": row["acc_custody"] or custody,
            "has_transfer": bool(row["has_transfer"]),
        }
    return ApplicationGraph(
        participants=tuple(
            Participant(
                contact_id=contact_id,
                type=types[contact_id],
                roles=frozenset(roles.get(contact_id, set())),
                dob=dobs[contact_id],
            )
            for contact_id in types
        ),
        accounts=tuple(
            AccountSpec(
                account_id=account_id,
                participant_contact_ids=tuple(parties),
                product=meta[account_id]["product"],
                custody=meta[account_id]["custody"],
                has_transfer=meta[account_id]["has_transfer"],
                registered_contact_ids=tuple(registered.get(account_id, ())),
            )
            for account_id, parties in accounts.items()
        ),
        custody=custody,
    )


async def _materialize_omitted_step(
    session: AsyncSession,
    *,
    tenant_id: int,
    execution_id: int,
    step_key: str,
) -> int:
    """Create the instances of one step the plan left out, if it applies now. Returns how many.

    Planning evaluates ``visibility`` once, at the start. Account details then writes
    ``platform.accounts.transfer``, the transfer fork takes the declaration, and walking past
    a step that was never created leaves opening the account waiting on a term the journey
    does not show. Re-planning this one step against the application as it stands inserts it
    on the case that owns it (catalogue terms still once per person). Zero means it still
    does not apply, and the caller walks past it.
    """
    spec = (await _definition_steps(session, execution_id)).get(step_key)
    if spec is None:
        return 0
    application_id = (
        await session.execute(
            text(
                "SELECT crm_application_id FROM flows.executions WHERE id = :e AND tenant_id = :t"
            ),
            {"e": execution_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if application_id is None:
        return 0
    plan = plan_execution([spec], await _application_graph(session, tenant_id, int(application_id)))
    case_ids = {
        (str(row["subject_type"]), row["subject_id"]): int(row["id"])
        for row in (
            await session.execute(
                text(
                    "SELECT id, subject_type, subject_id FROM flows.cases "
                    "WHERE execution_id = :e AND tenant_id = :t"
                ),
                {"e": execution_id, "t": tenant_id},
            )
        ).mappings()
    }
    inserted = 0
    for planned in plan:
        case_id = case_ids.get((planned.subject_type, planned.subject_id))
        if case_id is None:
            continue
        for step in planned.steps:
            if step.step_key != step_key:
                continue
            step_id = _fanned_step_id(step)
            exists = (
                await session.execute(
                    text("SELECT 1 FROM flows.case_steps WHERE case_id = :c AND step_id = :s"),
                    {"c": case_id, "s": step_id},
                )
            ).first()
            if exists is not None:
                continue
            await _persist_step(session, tenant_id=tenant_id, case_id=case_id, pstep=step)
            inserted += 1
    if inserted:
        logger.info(
            "a journey step the plan omitted was created once it applied",
            extra={
                "event": "flow_step_materialized",
                "execution_id": execution_id,
                "step_key": step_key,
                "instances": inserted,
            },
        )
    return inserted


async def _has_omitted_chosen_step(session: AsyncSession, execution_id: int) -> bool:
    """Whether a branch has already chosen a step this execution never planned."""
    definition = await _pinned_definition(session, execution_id)
    step_ids = {
        str(step["id"])
        for step in (definition.get("steps") or [])
        if isinstance(step, dict) and isinstance(step.get("id"), str)
    }
    if not step_ids:
        return False
    chosen: set[str] = set()
    for visited in (
        await session.execute(
            text("SELECT data -> 'graph' -> 'visited' FROM flows.cases WHERE execution_id = :e"),
            {"e": execution_id},
        )
    ).scalars():
        if isinstance(visited, str):
            visited = json.loads(visited)
        if not isinstance(visited, dict):
            continue
        for target in visited.values():
            if isinstance(target, str) and target in step_ids:
                chosen.add(target)
    if not chosen:
        return False
    existing = {
        str(key)
        for key in (
            await session.execute(
                text(
                    "SELECT DISTINCT cs.step_key FROM flows.case_steps cs "
                    "JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE c.execution_id = :e AND cs.step_key = ANY(:keys)"
                ),
                {"e": execution_id, "keys": list(chosen)},
            )
        ).scalars()
    }
    return any(key not in existing for key in chosen)


async def catch_up_omitted_steps(
    session: AsyncSession, *, execution_id: int, tenant_id: int
) -> None:
    """Create a step a running journey's fork has chosen but planning left out.

    Commits in its own transaction. The caller that notices the gap is often about to refuse
    (opening the account) or is a read (the journey page), and either of those would roll the
    new step back if it were written on the request transaction. A no-op when every chosen
    step already exists, and when another transaction already holds the execution's lock —
    the next read or the next open tries again.

    Uses this session's engine, not the process-wide one: tests bind a container the process
    DSN does not point at.
    """
    try:
        if not await _has_omitted_chosen_step(session, execution_id):
            return
        status = (
            await session.execute(
                text("SELECT status FROM flows.executions WHERE id = :e AND tenant_id = :t"),
                {"e": execution_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
        if status is None or _as_state(status, default=State.ACTIVE) in TERMINAL:
            return
        await _catch_up_in_new_transaction(session, execution_id=execution_id, tenant_id=tenant_id)
    except Exception:
        logger.exception(
            "could not create a journey step the plan had omitted",
            extra={"event": "flow_step_catch_up_failed", "execution_id": execution_id},
        )


async def _catch_up_in_new_transaction(
    session: AsyncSession, *, execution_id: int, tenant_id: int
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db import ApiSession

    bind = session.bind
    if bind is None:
        return
    maker = async_sessionmaker(
        bind, class_=AsyncSession, sync_session_class=ApiSession, expire_on_commit=False
    )
    async with maker() as heal, heal.begin():
        await heal.execute(text("SET LOCAL ROLE service_role"))
        locked = (
            await heal.execute(
                text("SELECT pg_try_advisory_xact_lock(:k)"),
                {"k": execution_id},
            )
        ).scalar()
        if not locked:
            return
        await recompute(heal, execution_id, tenant_id=tenant_id)


async def _enter_node(
    session: AsyncSession,
    *,
    tenant_id: int,
    execution_id: int,
    case_id: int,
    node_ids: Sequence[str],
    graph: graph_mod.Graph,
    partial: bool = False,
) -> bool:
    """Walk one case's control flow forward from ``node_ids``. Returns whether anything changed.

    ``partial`` is the walk one owner of several earns by settling their own instance of a fanned
    step (``_advance_graph``). It releases the steps ahead — the dependency pass still holds every
    other owner's instance on their own prerequisite — but stops, without entering, at any node
    whose meaning is the CASE's rather than one person's: a wait, an event listener, a hand-off
    to another flow, an exit. Those are entered by the ordinary walk once every instance has
    settled.

    This is the runtime half of "the graph compiles into the existing machinery", and it is a
    **frontier**, not a cursor: ``next`` may name several nodes and they all become available at
    once (the contract's ``$comment_nextArity``). So the walk is a work-list — each node either
    contributes its successors to the frontier or stops contributing, and "this path stops" is not
    "the walk stops". Two kinds are the exception and end the *whole* walk, because what they decide
    is about the case rather than about a path:

    * **step** — release it here if this case owns an instance; STOP THIS PATH if another tier owns
      it (the case resumes when it settles, ``_advance_graph``). If nothing anywhere planned it,
      re-plan it against the application as it stands now: a fact collected after kickoff (a
      transfer on Account details) can make a declaration apply. Still nothing, walk past it —
      a step that was conditioned out cannot be allowed to wedge the journey.
    * **start** — the explicit entry: fan out to everything it names, all at once. This is the node
      the whole arity rule exists for.
    * **branch** / **condition** — ``graph.branch_choice`` against this case's facts: arms in order,
      first satisfiable wins, else the fallback. A condition is a branch with two arms, so it is the
      same code path and not a second one. The arms not taken have their steps marked ``skipped``,
      so they can neither block a dependent step nor stop the case completing.
    * **wait** / **wait_for_event** — arm the durable timer/listener and stop THIS path; a sibling
      arm of a fan-out keeps walking, which is the point of parallel. Nothing armable (no resolvable
      deadline, no listener AND no timeout) means "do not wait at all" and the path carries straight
      on — hanging for ever is invisible, moving on early is not.
    * **start_flow** — a tail call into another published flow, depth-capped. It ends the walk:
      control has left this case, and releasing more of its steps behind it would be work nobody
      will ever come back for.
    * **exit** — latch this case's terminal outcome. It ends the walk for the harder version of the
      same reason: ``_latch_case_exit`` compensates the outstanding work, so a sibling arm releasing
      a step afterwards would put a live to-do inside a finished case.

    Idempotent two ways. ``visited`` is persisted and survives the walk — the resume path is
    at-least-once, and entering a node twice would re-arm a wait or re-take a branch. ``seen`` is
    per-walk and is what makes a re-convergence cheap: two arms of a fan-out that meet again enter
    the shared node once, not twice.
    """
    if graph.is_legacy_linear:
        # THE structural guard, not a convention: a legacy definition's edges are presentation
        # order and its execution order is `depends_on`. Walking them would sequentialise a live
        # onboarding journey, silently. (``next_nodes`` refuses for the same reason.)
        return False
    state = await _graph_state(session, case_id)
    visited: dict[str, Any] = state[_VISITED_KEY]
    facts: dict[str, Any] | None = None
    changed = False

    frontier: deque[str] = deque(node_ids)
    seen: set[str] = set()
    hops = 0

    while frontier:
        current = frontier.popleft()
        if current in seen:
            continue
        seen.add(current)
        if current in visited:
            # Entered before. A branch is not re-taken — its decision is recorded — but the walk
            # carries on down the arm it chose, because a partial walk (one owner of several) may
            # have stopped at a case-level node behind it that this walk is now allowed to enter.
            node = graph.nodes.get(current)
            recorded = visited[current]
            if (
                node is not None
                and node.kind in graph_mod.BRANCHING_KINDS
                and isinstance(recorded, str)
            ):
                frontier.append(recorded)
            continue
        hops += 1
        if hops > _MAX_GRAPH_HOPS:
            logger.warning(
                "a flow graph walk hit its node budget; the case stops here",
                extra={
                    "event": "flow_graph_hop_limit",
                    "case_id": case_id,
                    "node_id": current,
                },
            )
            break
        node = graph.nodes.get(current)
        if node is None:
            # An edge into a node this pinned version does not have. Drop this path rather than
            # guess — publish reports the dangling edge, and a sibling arm is still valid.
            continue

        if node.is_step:
            if await _mark_graph_released(session, case_id, current):
                visited[current] = current
                changed = True
                continue
            if await _planned_anywhere(session, execution_id, current):
                continue  # another tier owns it; this case waits for it to settle
            # Planned nowhere at kickoff. A later fact may have made it apply; create it
            # before giving up, or the opening gate will demand a term the journey never shows.
            await _materialize_omitted_step(
                session,
                tenant_id=tenant_id,
                execution_id=execution_id,
                step_key=current,
            )
            if await _mark_graph_released(session, case_id, current):
                visited[current] = current
                changed = True
                continue
            if await _planned_anywhere(session, execution_id, current):
                continue  # created on another case; this one waits for that acceptance
            frontier.extend(node.next_ids)  # still does not apply — walk past, do not wedge
            continue

        if node.kind == graph_mod.START_KIND:
            visited[current] = None
            changed = True
            frontier.extend(node.next_ids)
            continue

        if node.kind in graph_mod.BRANCHING_KINDS:
            if facts is None:
                facts = await case_context(session, tenant_id=tenant_id, case_id=case_id)
            chosen = graph_mod.branch_choice(graph, current, facts)
            # The CHOSEN TARGET, not None: `analytics` reads this map for the arm distribution, and
            # it is the only record of which way a case actually went.
            visited[current] = chosen
            for elided in planning.elided_by_branch(graph, current, chosen):
                await _elide_step(session, case_id, elided, f"branch:{current}")
            changed = True
            if chosen:
                frontier.append(chosen)
            continue

        if partial:
            # Everything below decides something about the case as a whole. One owner's progress
            # must not start the cooling-off, park the case, hand it to another flow or end it
            # while the other owners still have work here; the full walk enters these.
            continue

        if node.kind == "wait":
            if facts is None:
                facts = await case_context(session, tenant_id=tenant_id, case_id=case_id)
            fire_at = timers_mod.resolve_fire_at(node.config, now=datetime.now(UTC), ctx=facts)
            # None on a non-branch node: the successors are in the definition, and storing them
            # here as well would put a list where `analytics._BRANCH_CHOICES` expects a decision.
            visited[current] = None
            changed = True
            if fire_at is None:
                frontier.extend(node.next_ids)
                continue
            await timers_mod.schedule(
                session,
                tenant_id=tenant_id,
                case_id=case_id,
                node_id=current,
                kind=timers_mod.KIND_WAIT,
                fire_at=fire_at,
                payload={"successors": list(node.next_ids)},
            )
            continue

        if node.kind == "wait_for_event":
            listener_uid, timer_uid = await timers_mod.enter_wait_for_event(
                session,
                tenant_id=tenant_id,
                case_id=case_id,
                node_id=current,
                config=node.config,
            )
            visited[current] = None
            changed = True
            if listener_uid is None and timer_uid is None:
                # Nothing could ever leave this node — no listener could be armed and no deadline
                # was authored. Carrying on is `resolve_fire_at`'s discipline applied to the other
                # waiting node: a parked case nobody can release is the failure with no symptom.
                logger.warning(
                    "a flow case reached a wait-for-event it could never leave; carrying on",
                    extra={
                        "event": "flow_wait_for_event_unarmable",
                        "case_id": case_id,
                        "node_id": current,
                    },
                )
                frontier.extend(node.next_ids)
            continue

        if node.kind == "start_flow":
            visited[current] = None
            changed = True
            await _start_child_flow(
                session,
                tenant_id=tenant_id,
                execution_id=execution_id,
                case_id=case_id,
                node=node,
            )
            break  # a tail call: control does not come back, for any arm

        if node.kind == "exit":
            visited[current] = None
            changed = True
            await _latch_case_exit(
                session,
                tenant_id=tenant_id,
                execution_id=execution_id,
                case_id=case_id,
                node=node,
            )
            break  # the case is over; a sibling arm has nothing left to release into

        # An unknown kind — parse would have refused it, so this is belt and braces. Drop the path.

    if changed:
        await _save_graph_state(session, case_id, state)
    return changed


async def _advance_graph(
    session: AsyncSession, execution_id: int, *, tenant_id: int, graph: graph_mod.Graph | None
) -> bool:
    """Drive every case forward from the steps that have now settled. Returns whether it moved.

    Runs before the dependency release pass on each recompute, so a node the graph has just let
    through is instantiated in the same pass rather than the next event.

    A step advances the graph when EVERY instance of it in the execution has settled and at least
    one was actually fulfilled — an all-elided step has not happened, and letting it advance would
    put an untaken arm in charge of the journey. The successor is then entered in every case
    EXCEPT one that elided the step itself: that case decided not to go this way.

    **A fanned step also advances per owner.** The moment one participant's own instance is
    fulfilled, the graph is walked ahead for them (``_enter_node(partial=True)``): the steps behind
    the next gate are let through, and the dependency pass — which resolves a fanned dependent
    against its OWN participant's prerequisite — hands each owner their next step as they earn it.
    Waiting for every owner here meant a joint applicant who had accepted the general declaration
    could not be shown the platform terms until the other applicant had signed in and accepted
    theirs: the fork between the two declarations was case-scoped, so nobody passed it until
    everybody had. The branch decision is still taken once per case (it reads account facts, not
    a person's); only the release timing is per person. Case-level nodes behind a fork — a wait,
    an exit — stay with the every-instance rule.
    """
    if graph is None or graph.is_legacy_linear:
        return False
    rows = (
        (
            await session.execute(
                text(
                    "SELECT cs.step_key, cs.participant_subject_id AS participant, "
                    "       bool_and(cs.status = ANY(:settled)) AS settled, "
                    "       bool_or(cs.status = 'fulfilled') AS done "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE c.execution_id = :e "
                    "GROUP BY cs.step_key, cs.participant_subject_id"
                ),
                {"e": execution_id, "settled": list(_SETTLED_FOR_ADVANCE)},
            )
        )
        .mappings()
        .all()
    )
    by_key: dict[str, list[Any]] = {}
    for r in rows:
        by_key.setdefault(str(r["step_key"]), []).append(r)
    # key → whether the walk is the whole case's (every instance settled) or one owner's.
    advances: dict[str, bool] = {}
    for step_key, lanes in by_key.items():
        all_settled = all(bool(lane["settled"]) for lane in lanes)
        any_done = any(bool(lane["done"]) for lane in lanes)
        if all_settled and any_done:
            advances[step_key] = False
        elif any(
            lane["participant"] is not None and bool(lane["settled"]) and bool(lane["done"])
            for lane in lanes
        ):
            advances[step_key] = True
    if not advances:
        return False
    case_ids = (
        (
            await session.execute(
                text("SELECT id FROM flows.cases WHERE execution_id = :e ORDER BY id"),
                {"e": execution_id},
            )
        )
        .scalars()
        .all()
    )
    moved = False
    for step_key, partial in advances.items():
        node = graph.nodes.get(step_key)
        if node is None or not node.next_ids:
            continue
        for case_id in case_ids:
            if await _case_elided(session, int(case_id), step_key):
                continue
            moved |= await _enter_node(
                session,
                tenant_id=tenant_id,
                execution_id=execution_id,
                case_id=int(case_id),
                # Every successor, in one walk: a step that fans out releases all of its targets
                # together, and doing them in separate walks would re-read the case's graph state
                # once per arm for no benefit.
                node_ids=node.next_ids,
                graph=graph,
                partial=partial,
            )
    return moved


# ---------------------------------------------------------------------------------------------
# exit — latching one case's outcome
# ---------------------------------------------------------------------------------------------


async def _latch_case_exit(
    session: AsyncSession,
    *,
    tenant_id: int,
    execution_id: int,
    case_id: int,
    node: graph_mod.Node,
) -> None:
    """End this case here, with the outcome the author named.

    ``exit`` is the node that says "for this client, the journey stops" — approved and finished,
    or declined, or failed. Three things have to happen together or the case is only half-ended:

    1. **the outstanding work is compensated**, through each step's own handler and by withdrawing
       the client-facing request items. Without this the client's portal keeps asking for a form
       nobody will ever read. This is ``cancel_execution``'s discipline at case scope — deliberately
       NOT a call into it, because ending one case must not tear down its siblings;
    2. **the case is latched**, which ``derivation.derive_case_state`` then honours for ever
       (terminal ⇒ unchanged) — so the outcome survives every later recompute;
    3. **its wake-ups are retired**, because a cancelled journey must not resume next Tuesday.
    """
    outcome = node.config.get("outcome")
    target = _as_state(
        outcome if isinstance(outcome, str) else "completed", default=State.COMPLETED
    )
    if target not in TERMINAL:
        # graph_issues refuses a non-terminal outcome at publish; on stored JSON from before that
        # rule, "completed" is the reading the contract's default gives.
        target = State.COMPLETED
    reason = node.config.get("reason")
    reason_text = reason.strip()[:500] if isinstance(reason, str) and reason.strip() else None

    current = (
        await session.execute(text("SELECT status FROM flows.cases WHERE id = :c"), {"c": case_id})
    ).scalar_one_or_none()
    if current is not None and _as_state(current, default=State.ACTIVE) in TERMINAL:
        return  # already latched — an at-least-once resume must be harmless

    await _compensate_case(
        session,
        tenant_id=tenant_id,
        execution_id=execution_id,
        case_id=case_id,
        reason=reason_text or f"ended by “{node.label}”",
    )
    await _save_case_state(session, case_id, target)
    await timers_mod.cancel_for_case(session, case_id=case_id)
    await _append_event(
        session,
        tenant_id=tenant_id,
        execution_id=execution_id,
        case_id=case_id,
        event_type=f"case.{target.value}",
        payload={"reason": reason_text, "node": node.id, "by": "flow"},
        idempotency_key=f"exit:{case_id}:{node.id}",
    )
    await _latch_execution_if_spent(session, execution_id)


async def _latch_execution_if_spent(session: AsyncSession, execution_id: int) -> None:
    """Latch the execution when every one of its cases has ended and none of them completed.

    ``derivation.derive_execution_state`` deliberately excludes cancelled cases from the "required"
    set, so an execution whose cases ALL cancelled has nothing left to roll up and falls through to
    ``draft`` — a journey that is visibly over reported as never started. That is invisible until
    an ``exit`` node makes it reachable without ``cancel_execution``, which latches explicitly.
    Fixed here rather than in the pure derivation because the rule needs the whole case set and
    the derivation is given only what is required.
    """
    states = (
        (
            await session.execute(
                text("SELECT status FROM flows.cases WHERE execution_id = :e"),
                {"e": execution_id},
            )
        )
        .scalars()
        .all()
    )
    parsed = [_as_state(s, default=State.ACTIVE) for s in states]
    if not parsed or any(s not in TERMINAL for s in parsed):
        return
    if State.COMPLETED in parsed:
        return  # recompute's ordinary rollup already has an answer for this
    outcome = State.FAILED if State.FAILED in parsed else State.CANCELLED
    await _save_execution_state(session, execution_id, outcome)


async def _compensate_case(
    session: AsyncSession, *, tenant_id: int, execution_id: int, case_id: int, reason: str
) -> None:
    """Undo one case's outstanding work: handler compensation, then withdraw and latch.

    The case-scoped twin of ``cancel_execution``'s middle. Kept separate rather than generalised
    because the two differ in the part that matters — cancelling an execution also cancels the
    tasks the whole journey raised and latches the execution, and doing either of those from an
    ``exit`` node in one participant's case would be a surprise a no-code tool must never spring.
    """
    outstanding = (
        (
            await session.execute(
                text(
                    "SELECT cs.step_key, cs.step_type, cs.participant_subject_id, cs.data, "
                    "       c.subject_type, c.subject_id "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE cs.case_id = :c "
                    "  AND cs.status NOT IN ('fulfilled', 'skipped', 'cancelled')"
                ),
                {"c": case_id},
            )
        )
        .mappings()
        .all()
    )
    definition = await _definition_steps(session, execution_id)
    for row in outstanding:
        handler = handlers.get_handler(str(row["step_type"]))
        if handler is None:
            continue
        cfg = (definition.get(str(row["step_key"])) or {}).get("config")
        await handler.cancel(
            handlers.StepContext(
                session=session,
                tenant_id=tenant_id,
                execution_id=execution_id,
                case_id=case_id,
                step_key=str(row["step_key"]),
                config=cfg if isinstance(cfg, dict) else {},
                subject_type=row["subject_type"],
                subject_id=row["subject_id"],
                participant_subject_id=row["participant_subject_id"],
                data=_as_dict(row["data"]),
            )
        )
    await session.execute(
        text(
            "UPDATE documents.request_items SET status = 'cancelled' "
            "WHERE status = 'pending' AND id IN ("
            "  SELECT cs.request_item_id FROM flows.case_steps cs "
            "  WHERE cs.case_id = :c AND cs.request_item_id IS NOT NULL)"
        ),
        {"c": case_id},
    )
    await session.execute(
        text(
            "UPDATE flows.case_steps SET status = 'skipped', data = data || CAST(:d AS jsonb) "
            "WHERE case_id = :c AND status NOT IN ('fulfilled', 'skipped', 'cancelled')"
        ),
        {"c": case_id, "d": json.dumps({"skipped_reason": reason})},
    )


# ---------------------------------------------------------------------------------------------
# start_flow — the tail call
# ---------------------------------------------------------------------------------------------


async def _start_child_flow(
    session: AsyncSession,
    *,
    tenant_id: int,
    execution_id: int,
    case_id: int,
    node: graph_mod.Node,
) -> None:
    """Hand this client over to another published flow. Never raises — a chain that cannot start
    must not roll back the transition that reached it.

    **Depth is A1's counter, not a second one.** ``flows.executions.data.chain_depth`` carries the
    same number ``AutomationContext.chain_depth`` does and is compared against the same per-tenant
    ceiling (``automation.limits_for`` → ``triggers.SAFETY.maxChainDepth``, clamped by
    ``CEILINGS``). A journey that starts a journey that starts a journey is exactly the runaway the
    automation consumer already guards, and the guard is only a guard if there is one of it.

    ``carry_memory`` copies this case's memory into the child's cases, so "we already know their
    risk band" survives the hand-over. Off by default: memory can hold anything an author promoted
    out of a fact-find, and carrying it into a different process should be a decision.
    """
    from app.flows import automation as automation_mod  # cycle: automation imports us lazily too

    code = node.config.get("flow_code")
    if not isinstance(code, str) or not code.strip():
        return

    parent = (
        (
            await session.execute(
                text(
                    "SELECT e.uid, e.data, e.crm_application_id, a.provider, "
                    "       c.subject_contact_id, c.uid AS case_uid "
                    "FROM flows.executions e "
                    "JOIN flows.cases c ON c.id = :c "
                    "LEFT JOIN crm.applications a ON a.id = e.crm_application_id "
                    "WHERE e.id = :e"
                ),
                {"e": execution_id, "c": case_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if parent is None:
        return
    contact_id = parent["subject_contact_id"]
    if contact_id is None:
        await _refuse_child_flow(
            session,
            tenant_id=tenant_id,
            execution_id=execution_id,
            case_id=case_id,
            node=node,
            reason="this case is not about a client, so there is nobody to start a journey for",
        )
        return

    raw_depth = _as_dict(parent["data"]).get("chain_depth")
    depth = (
        int(raw_depth) if isinstance(raw_depth, int) and not isinstance(raw_depth, bool) else 0
    ) + 1
    limits = await automation_mod.limits_for(session, tenant_id)
    if depth > limits.max_chain_depth:
        await _refuse_child_flow(
            session,
            tenant_id=tenant_id,
            execution_id=execution_id,
            case_id=case_id,
            node=node,
            reason=(
                f"chain depth {depth} exceeds the cap of {limits.max_chain_depth} — a journey is "
                "very likely starting itself"
            ),
        )
        return

    flow = (
        (
            await session.execute(
                text(
                    "SELECT f.id, f.current_published_version_id AS vid, v.definition "
                    "FROM flows.flows f "
                    "JOIN flows.flow_versions v ON v.id = f.current_published_version_id "
                    "WHERE f.tenant_id = :t AND f.code = :code AND f.status = 'published'"
                ),
                {"t": tenant_id, "code": code.strip()},
            )
        )
        .mappings()
        .one_or_none()
    )
    if flow is None:
        await _refuse_child_flow(
            session,
            tenant_id=tenant_id,
            execution_id=execution_id,
            case_id=case_id,
            node=node,
            reason=f"no published flow called “{code}” in this firm",
        )
        return
    if parent["provider"] is None:
        await _refuse_child_flow(
            session,
            tenant_id=tenant_id,
            execution_id=execution_id,
            case_id=case_id,
            node=node,
            reason="the parent journey has no application to inherit a platform from",
        )
        return

    contact_type = (
        await session.execute(
            text("SELECT type::text FROM crm.contacts WHERE id = :c AND tenant_id = :t"),
            {"c": int(contact_id), "t": tenant_id},
        )
    ).scalar_one_or_none()
    application_id = int(
        (
            await session.execute(
                text(
                    "INSERT INTO crm.applications (tenant_id, provider, uid, status) "
                    "VALUES (:t, CAST(:p AS platform.provider_code), :u, 'draft') RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "p": str(parent["provider"]),
                    # Deterministic in the parts that identify it and unique by construction: one
                    # execution's one start_flow node starts one child journey.
                    "u": f"app_{parent['uid']}_{node.id}"[:64],
                },
            )
        ).scalar_one()
    )
    definition = flow["definition"]
    if isinstance(definition, str):
        definition = json.loads(definition)
    child_graph = ApplicationGraph(
        participants=(
            planning.Participant(
                contact_id=int(contact_id),
                type=str(contact_type or "individual"),
                # 'owner' so the participant is KYC-relevant, for the reason FlowTarget documents:
                # a journey nobody named a role for must gate identity, not quietly skip it.
                roles=frozenset({"owner"}),
            ),
        ),
        accounts=(),
    )
    carry = bool(node.config.get("carry_memory"))
    try:
        created = await start_execution(
            session,
            tenant_id=tenant_id,
            flow_id=int(flow["id"]),
            flow_version_id=int(flow["vid"]),
            definition=definition,
            graph=child_graph,
            application_id=application_id,
            initiation="flow",
            data={
                "chain_depth": depth,
                "started_by_execution": parent["uid"],
                "started_by_node": node.id,
            },
            memory=await memory_mod.read(session, case_id) if carry else None,
        )
    except ActiveExecutionExists:
        # Not an error: one live journey per (flow, client) is the engine's own guard, and a flow
        # handing over to one the client is already on is exactly what it protects.
        await _refuse_child_flow(
            session,
            tenant_id=tenant_id,
            execution_id=execution_id,
            case_id=case_id,
            node=node,
            reason="the client is already on a live journey of that flow",
        )
        return
    await _append_event(
        session,
        tenant_id=tenant_id,
        execution_id=execution_id,
        case_id=case_id,
        event_type="flow.started",
        payload={
            "node": node.id,
            "flow_code": code.strip(),
            "execution_uid": created["uid"],
            "chain_depth": depth,
            "carry_memory": carry,
        },
        idempotency_key=f"startflow:{case_id}:{node.id}",
    )


async def _refuse_child_flow(
    session: AsyncSession,
    *,
    tenant_id: int,
    execution_id: int,
    case_id: int,
    node: graph_mod.Node,
    reason: str,
) -> None:
    """Record that a ``start_flow`` did not start anything, and why.

    A fact rather than a log line: "the review journey never started" is a question a firm will
    ask months later, and an answer that only exists in container stdout is not an answer.
    """
    logger.warning(
        "a flow's start_flow node did not start a journey",
        extra={
            "event": "flow_start_flow_refused",
            "tenant_id": tenant_id,
            "case_id": case_id,
            "node_id": node.id,
            "reason": reason,
        },
    )
    await _append_event(
        session,
        tenant_id=tenant_id,
        execution_id=execution_id,
        case_id=case_id,
        event_type="flow.start_refused",
        payload={"node": node.id, "reason": reason},
        idempotency_key=f"startflow-refused:{case_id}:{node.id}",
    )


# ---------------------------------------------------------------------------------------------
# The resume seam (frozen contract with app/flows/timers.py)
# ---------------------------------------------------------------------------------------------


async def resume_from_timer(
    session: AsyncSession, *, case_id: int, node_id: str, kind: str
) -> None:
    """A case's wait is over — take the edge the node was holding. THE seam ``timers`` calls.

    ``kind`` is one of ``timers.RESUME_KINDS``:

    * ``'wait'`` / ``'wait_for_event'`` — the node's normal successor (the pause elapsed, or the
      event the case was listening for happened);
    * ``'wait_for_event_timeout'`` — the deadline passed, so the node's ``timeout_branch``, read
      **from the timer's own payload**. Snapshotted at arm time by ``enter_wait_for_event``
      because the flow may have been re-published since; the pinned definition's config is only
      the fallback for a timer armed before the snapshot existed.

    Runs inside the caller's transaction (the timer loop's, or the automation consumer's savepoint)
    and takes the execution's advisory lock like every other recompute, so a resume and a
    concurrent step event on a sibling case serialise rather than racing the rollup.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT c.tenant_id, c.execution_id, c.status, c.completed_at, c.cancelled_at "
                    "FROM flows.cases c WHERE c.id = :c"
                ),
                {"c": case_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["execution_id"] is None:
        return  # a v1 case, or one that has been deleted: there is no graph to resume
    if row["completed_at"] is not None or row["cancelled_at"] is not None:
        return  # latched; the loop cancels these too, but the check is cheap and this is the door
    tenant_id, execution_id = int(row["tenant_id"]), int(row["execution_id"])
    await _advisory_lock(session, execution_id)

    graph = await _definition_graph(session, execution_id)
    node = graph.nodes.get(node_id) if graph is not None else None
    if graph is None or node is None:
        logger.warning(
            "a flow timer fired for a node the pinned definition does not have",
            extra={"event": "flow_resume_unknown_node", "case_id": case_id, "node_id": node_id},
        )
        return

    targets: tuple[str, ...]
    if kind == timers_mod.RESUME_EVENT_TIMEOUT:
        # A deadline escape is one place by contract (`config.timeout_branch` is a single id), so
        # this stays single-valued while the ordinary successor below does not.
        branch = await _timeout_branch(session, case_id=case_id, node=node)
        targets = (branch,) if branch else ()
        # Retire the listener this node was ALSO waiting on — the event can no longer release a
        # node the case has left. Deliberately not `timers.cancel_for_node`: that would cancel the
        # very timer the loop is in the middle of firing, and the loop's own `state = 'fired'`
        # write would then no-op, erasing the record that the deadline was honoured.
        await session.execute(
            text(
                "UPDATE flows.case_listeners SET state = 'cancelled', cancelled_at = now() "
                "WHERE case_id = :c AND node_id = :n AND state = 'active'"
            ),
            {"c": case_id, "n": node_id},
        )
    else:
        targets = node.next_ids

    # The node itself is already `visited` (it was recorded when the wait was armed), so entering
    # the TARGETS is what moves the case on.
    await _enter_node(
        session,
        tenant_id=tenant_id,
        execution_id=execution_id,
        case_id=case_id,
        node_ids=targets,
        graph=graph,
    )
    await recompute(session, execution_id, tenant_id=tenant_id)


async def _timeout_branch(
    session: AsyncSession, *, case_id: int, node: graph_mod.Node
) -> str | None:
    snapshot = (
        await session.execute(
            text(
                "SELECT payload ->> 'timeout_branch' FROM flows.timers "
                "WHERE case_id = :c AND node_id = :n AND kind = :k "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"c": case_id, "n": node.id, "k": timers_mod.KIND_EVENT_TIMEOUT},
        )
    ).scalar_one_or_none()
    if isinstance(snapshot, str) and snapshot.strip():
        return snapshot.strip()
    fallback = node.config.get("timeout_branch")
    return fallback.strip() if isinstance(fallback, str) and fallback.strip() else None


async def _case_elided(session: AsyncSession, case_id: int, step_key: str) -> bool:
    """Did THIS case decline this step? (It holds instances, and none of them was fulfilled.)"""
    row = (
        (
            await session.execute(
                text(
                    "SELECT count(*) AS instances, "
                    "       count(*) FILTER (WHERE status = 'fulfilled') AS done "
                    "FROM flows.case_steps WHERE case_id = :c AND step_key = :k"
                ),
                {"c": case_id, "k": step_key},
            )
        )
        .mappings()
        .one()
    )
    return int(row["instances"]) > 0 and int(row["done"]) == 0


async def _release_dependent_steps(
    session: AsyncSession,
    execution_id: int,
    *,
    tenant_id: int,
    graph: graph_mod.Graph | None = None,
) -> bool:
    """Instantiate any held step whose prerequisites have now settled.

    This is what makes authored ordering real: the artifact (request item, KYC check, render job) is
    created only when the step actually becomes the next thing to do. Runs before derivation on
    every recompute, and loops because releasing one step can settle another immediately (a
    dedup'd KYC check completes at instantiate). Returns whether anything was released.

    **Two gates now, not one.** ``depends_on`` is the original — "not until all of these have
    settled" — and it is unchanged. A v3 graph adds the control gate: a step sitting behind a
    branch, a wait or a wait-for-event is held until the ENGINE walks the graph to it
    (``_enter_node``), because its compiled ``depends_on`` names the step BEFORE the control node
    and would otherwise release it the moment that settled — running both arms of a branch, and
    skipping every wait. ``planning.control_gated_steps`` is empty for a legacy graph, so nothing
    about an existing journey changes.
    """
    gated = planning.control_gated_steps(graph) if graph is not None else {}
    held = (
        (
            await session.execute(
                text(
                    "SELECT cs.case_id, cs.step_key, cs.step_type, cs.participant_subject_id, "
                    "       cs.depends_on, cs.data, c.subject_type, c.subject_id "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE c.execution_id = :e AND cs.status = 'pending' "
                    # A control-gated step may have NO dependencies at all (it sits straight after
                    # the graph's entry branch), so the marker keys widen the claim — otherwise the
                    # engine would release it and this pass would never look at it again.
                    "  AND (cardinality(cs.depends_on) > 0 "
                    "       OR cs.data ?| ARRAY[CAST(:gate AS text), CAST(:rel AS text)]) "
                    "  AND COALESCE((cs.data ->> CAST(:inst AS text))::boolean, false) "
                    "      IS NOT TRUE "
                    "ORDER BY cs.id"
                ),
                {
                    "e": execution_id,
                    "inst": dependencies.INSTANTIATED_KEY,
                    "gate": _GATED_ON_KEY,
                    "rel": _GRAPH_RELEASED_KEY,
                },
            )
        )
        .mappings()
        .all()
    )
    if not held:
        return False
    facts_by_case = await _dependency_facts(session, execution_id)
    definition = await _definition_steps(session, execution_id)
    requests_cache: dict[int, int] = {}
    released = False
    for row in held:
        case_id = int(row["case_id"])
        step_key = str(row["step_key"])
        participant = row["participant_subject_id"]
        spec = definition.get(step_key) or {}
        # The PERSISTED prerequisites, not the authored ones. Planning writes what
        # `graph.effective_depends_on` compiled — the authored `depends_on` UNION the step
        # ancestors the wires imply — and the definition carries only the authored half. Reading
        # the definition here meant a step whose ordering came from an edge (which, in a v3 spine,
        # is nearly all of them) resolved against an empty list and was released on the first
        # recompute. Invisible while every edge stayed inside one tier, because planning had
        # already held those; it surfaced the moment ordering could cross tiers.
        depends_on = [d for d in (row["depends_on"] or []) if isinstance(d, str)]
        awaiting = dependencies.unmet(
            step_key,
            participant,
            depends_on,
            facts_by_case.get(case_id, []),
            # Ordering crosses tiers: a participant-tier step may wait on account-tier work (the
            # legacy terms-before-checks order). Consulted only for keys this case does not plan.
            _foreign_facts(facts_by_case, case_id),
        )
        if awaiting:
            await _mark_awaiting(session, case_id, step_key, participant, awaiting)
            continue
        if step_key in gated and not _as_dict(row["data"]).get(_GRAPH_RELEASED_KEY):
            await _mark_gated(session, case_id, step_key, gated[step_key])
            continue
        cfg = spec.get("config")
        await _instantiate_step(
            session,
            tenant_id=tenant_id,
            execution_id=execution_id,
            case_id=case_id,
            step_key=step_key,
            step_type=str(row["step_type"]),
            config=cfg if isinstance(cfg, dict) else {},
            subject_type=row["subject_type"],
            subject_id=row["subject_id"],
            participant_subject_id=participant,
            requests_cache=requests_cache,
        )
        released = True
    return released


def _foreign_facts(
    facts_by_case: dict[int, list[dependencies.StepFact]], case_id: int
) -> list[dependencies.StepFact]:
    """Every step instance of the execution EXCEPT this case's — the cross-tier half of the
    dependency scope. Flattened per call rather than cached: an execution's step count is small
    (tens), and a stale copy would release a step whose prerequisite had just moved."""
    return [f for cid, rows in facts_by_case.items() if cid != case_id for f in rows]


async def _dependency_facts(
    session: AsyncSession, execution_id: int
) -> dict[int, list[dependencies.StepFact]]:
    """Every step instance of the execution, grouped by case — the dependency resolver's input."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT cs.case_id, cs.step_key, cs.participant_subject_id, cs.status, "
                    "       cs.depends_on FROM flows.case_steps cs "
                    "JOIN flows.cases c ON c.id = cs.case_id WHERE c.execution_id = :e"
                ),
                {"e": execution_id},
            )
        )
        .mappings()
        .all()
    )
    out: dict[int, list[dependencies.StepFact]] = {}
    for r in rows:
        out.setdefault(int(r["case_id"]), []).append(
            (
                str(r["step_key"]),
                r["participant_subject_id"],
                str(r["status"]),
                tuple(r["depends_on"] or ()),
            )
        )
    return out


async def recompute(session: AsyncSession, execution_id: int, *, tenant_id: int) -> None:
    """Re-derive every case's state (account cases gated on their participants' profile completion),
    the execution rollup, and the crm.applications projection — all under the group lock.

    Two things happen before derivation, in this order, because state must be derived from an
    up-to-date board rather than the one the last event left:

    1. **the graph advances** — every case walks forward from the steps that have just settled,
       taking branches, arming waits and eliding the arms not taken (``_advance_graph``). A no-op
       for a legacy definition, whose ordering is ``depends_on`` and nothing else;
    2. **held steps are released** — whatever the dependency rules (and now the control gates)
       have unblocked gets its artifact.

    They loop together because each feeds the other: entering a branch releases a step, which may
    complete at instantiate (a dedup'd KYC check), which advances the graph again.
    """
    await _advisory_lock(session, execution_id)

    graph = await _definition_graph(session, execution_id)
    # Bounded: each pass either releases an instance or moves a case to a node it has not visited,
    # and both are recorded, so this only caps pathological chains.
    for _ in range(_MAX_RELEASE_PASSES):
        moved = await _advance_graph(session, execution_id, tenant_id=tenant_id, graph=graph)
        released = await _release_dependent_steps(
            session, execution_id, tenant_id=tenant_id, graph=graph
        )
        if not moved and not released:
            break

    cases = (
        (
            await session.execute(
                text(
                    "SELECT id, kind, subject_id, status FROM flows.cases WHERE execution_id = :e"
                ),
                {"e": execution_id},
            )
        )
        .mappings()
        .all()
    )
    steps_by_case = await _steps_by_case(session, [int(c["id"]) for c in cases])

    # First pass: non-account cases (no cross-case gate) → learn which profile cases completed.
    new_state: dict[int, State] = {}
    completed_profiles: set[int] = set()
    for c in cases:
        if c["kind"] == "account":
            continue
        st = derive_case_state(State(c["status"]), steps_by_case.get(int(c["id"]), []))
        new_state[int(c["id"])] = st
        if c["kind"] == "profile" and st == State.COMPLETED and c["subject_id"] is not None:
            completed_profiles.add(int(c["subject_id"]))

    # Second pass: account cases, gated on their participants' profile completion.
    gates = await _account_gate_contacts(session, execution_id)
    for c in cases:
        if c["kind"] != "account":
            continue
        gate_open = all(cid in completed_profiles for cid in gates.get(int(c["id"]), ()))
        new_state[int(c["id"])] = derive_case_state(
            State(c["status"]), steps_by_case.get(int(c["id"]), []), gate_open=gate_open
        )

    for cid, st in new_state.items():
        await _save_case_state(session, cid, st)

    exec_state = derive_execution_state(
        State(await _execution_status(session, execution_id)),
        [CaseView(state=st) for st in new_state.values()],
    )
    await _save_execution_state(session, execution_id, exec_state)
    await _project(session, execution_id, tenant_id=tenant_id, exec_state=exec_state)


async def _steps_by_case(session: AsyncSession, case_ids: list[int]) -> dict[int, list[StepView]]:
    if not case_ids:
        return {}
    rows = (
        (
            await session.execute(
                text(
                    "SELECT case_id, step_id, required, status FROM flows.case_steps "
                    "WHERE case_id = ANY(:ids)"
                ),
                {"ids": case_ids},
            )
        )
        .mappings()
        .all()
    )
    out: dict[int, list[StepView]] = {cid: [] for cid in case_ids}
    for r in rows:
        out[int(r["case_id"])].append(
            StepView(
                key=str(r["step_id"]), required=bool(r["required"]), status=StepStatus(r["status"])
            )
        )
    return out


async def _account_gate_contacts(
    session: AsyncSession, execution_id: int
) -> dict[int, tuple[int, ...]]:
    """The KYC-relevant participant contact ids each account case waits on, recovered from the
    account's ``crm.account_contacts`` (individuals in a gating role)."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT c.id AS case_id, ac.contact_id "
                    "FROM flows.cases c "
                    "JOIN crm.account_contacts ac ON ac.account_id = c.subject_id "
                    "JOIN crm.contacts ct ON ct.id = ac.contact_id "
                    "WHERE c.execution_id = :e AND c.kind = 'account' "
                    "  AND ct.type = 'individual' "
                    "  AND ac.role IN ('owner', 'decision_maker', 'trustee')"
                ),
                {"e": execution_id},
            )
        )
        .mappings()
        .all()
    )
    out: dict[int, list[int]] = {}
    for r in rows:
        out.setdefault(int(r["case_id"]), []).append(int(r["contact_id"]))
    return {k: tuple(v) for k, v in out.items()}


async def _save_case_state(session: AsyncSession, case_id: int, state: State) -> None:
    changed = (
        await session.execute(
            text(
                "UPDATE flows.cases SET status = :s, "
                "  completed_at = CASE WHEN :done THEN COALESCE(completed_at, now()) "
                "                      ELSE completed_at END, "
                "  cancelled_at = CASE WHEN :canc THEN COALESCE(cancelled_at, now()) "
                "                      ELSE cancelled_at END "
                "WHERE id = :c AND status <> :s RETURNING id"
            ),
            {
                "s": state.value,
                "done": state == State.COMPLETED,
                "canc": state == State.CANCELLED,
                "c": case_id,
            },
        )
    ).all()
    if changed and state in TERMINAL:
        # A finished journey must not wake up next Tuesday. Here rather than at each call site so
        # every route into a terminal state — derivation, an `exit` node, `set_case_status`, an
        # operator cancelling — retires the case's wake-ups, and none of them has to remember to.
        # Idempotent, and only on the transition, so a recompute that re-derives the same terminal
        # state does not re-run it.
        await timers_mod.cancel_for_case(session, case_id=case_id)


async def _execution_status(session: AsyncSession, execution_id: int) -> str:
    return str(
        (
            await session.execute(
                text("SELECT status FROM flows.executions WHERE id = :e"), {"e": execution_id}
            )
        ).scalar_one()
    )


async def _save_execution_state(session: AsyncSession, execution_id: int, state: State) -> None:
    await session.execute(
        text(
            "UPDATE flows.executions SET status = :s, "
            "  completed_at = CASE WHEN :done THEN COALESCE(completed_at, now()) "
            "                      ELSE completed_at END, "
            "  cancelled_at = CASE WHEN :canc THEN COALESCE(cancelled_at, now()) "
            "                      ELSE cancelled_at END "
            "WHERE id = :e AND status <> :s"
        ),
        {
            "s": state.value,
            "done": state == State.COMPLETED,
            "canc": state == State.CANCELLED,
            "e": execution_id,
        },
    )


async def _project(
    session: AsyncSession, execution_id: int, *, tenant_id: int, exec_state: State
) -> None:
    """Write the legacy onboarding label onto crm.applications (the domain projection)."""
    app_row = (
        (
            await session.execute(
                text(
                    "SELECT e.crm_application_id AS app_id, a.custody, a.offshore_approved, "
                    "       (e.data ->> 'adviser_declared')::boolean AS declared "
                    "FROM flows.executions e "
                    "LEFT JOIN crm.applications a ON a.id = e.crm_application_id "
                    "WHERE e.id = :e"
                ),
                {"e": execution_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if app_row is None or app_row["app_id"] is None:
        return
    step_rows = (
        (
            await session.execute(
                text(
                    "SELECT cs.step_type, cs.status, cs.scope FROM flows.case_steps cs "
                    "JOIN flows.cases c ON c.id = cs.case_id WHERE c.execution_id = :e"
                ),
                {"e": execution_id},
            )
        )
        .mappings()
        .all()
    )
    has_offshore = str(app_row["custody"] or "") == "offshore"
    contact_ids = [
        int(v)
        for v in (
            await session.execute(
                text(
                    "SELECT DISTINCT c.subject_id FROM flows.cases c "
                    "WHERE c.execution_id = :e AND c.kind = 'profile' "
                    "  AND c.subject_id IS NOT NULL"
                ),
                {"e": execution_id},
            )
        ).scalars()
    ]
    account_rows = (
        (
            await session.execute(
                text(
                    "SELECT p.product_type::text AS product, "
                    "       (a.transfer IS NOT NULL) AS has_transfer "
                    "FROM platform.accounts a "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "WHERE a.application_id = :a"
                ),
                {"a": app_row["app_id"]},
            )
        )
        .mappings()
        .all()
    )
    outstanding_empty = await terms_svc.onboarding_outstanding_empty(
        session,
        tenant_id=tenant_id,
        contact_ids=contact_ids,
        products=[r["product"] for r in account_rows],
        has_transfer=any(bool(r["has_transfer"]) for r in account_rows),
    )
    gates = projection.gates_from_steps(
        [(str(r["step_type"]), str(r["status"]), str(r["scope"] or "")) for r in step_rows],
        declared=bool(app_row["declared"]),
        has_offshore=has_offshore,
        offshore_approved=bool(app_row["offshore_approved"]),
        outstanding_empty=outstanding_empty,
    )
    label = projection.project_onboarding_status(exec_state, gates)
    await session.execute(
        text(
            "UPDATE crm.applications SET status = :s WHERE id = :a AND status IS DISTINCT FROM :s"
        ),
        {"s": label, "a": app_row["app_id"]},
    )


async def cancel_execution(
    session: AsyncSession,
    *,
    tenant_id: int,
    execution_id: int,
    reason: str | None = None,
    cancelled_by: str | None = None,
) -> bool:
    """Abandon a journey started in error: compensate every outstanding step through its handler,
    withdraw the client's outstanding request items, cancel the tasks raised against it, and
    latch the cases + execution ``cancelled``.

    Cancelling is what releases ``cases_one_active_per_subject``, so the subject can restart.
    Idempotent — a second call on an already-cancelled execution returns False and changes nothing.
    """
    await _advisory_lock(session, execution_id)
    current = await _execution_status(session, execution_id)
    if State(current) in TERMINAL:
        return False

    outstanding = (
        (
            await session.execute(
                text(
                    "SELECT cs.case_id, cs.step_key, cs.step_type, cs.participant_subject_id, "
                    "       cs.request_item_id, cs.data, c.subject_type, c.subject_id "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE c.execution_id = :e "
                    "  AND cs.status NOT IN ('fulfilled', 'skipped', 'cancelled')"
                ),
                {"e": execution_id},
            )
        )
        .mappings()
        .all()
    )
    definition = await _definition_steps(session, execution_id)
    for row in outstanding:
        spec = definition.get(str(row["step_key"])) or {}
        cfg = spec.get("config")
        handler = handlers.get_handler(str(row["step_type"]))
        if handler is not None:
            # The handler's compensation hook: withdraw the invite, close the draft account,
            # void the pending KYC check. A handler with nothing to undo inherits the no-op base.
            await handler.cancel(
                handlers.StepContext(
                    session=session,
                    tenant_id=tenant_id,
                    execution_id=execution_id,
                    case_id=int(row["case_id"]),
                    step_key=str(row["step_key"]),
                    config=cfg if isinstance(cfg, dict) else {},
                    subject_type=row["subject_type"],
                    subject_id=row["subject_id"],
                    participant_subject_id=row["participant_subject_id"],
                    # Compensation must undo THIS instance's artifact (the KYC check it opened), not
                    # the newest row that looks like it — see ReviewHandler.cancel.
                    data=_as_dict(row["data"]),
                )
            )
    # Withdraw the client-facing artifacts so the portal checklist clears, then latch the steps.
    withdrawn = (
        (
            await session.execute(
                text(
                    "UPDATE documents.request_items SET status = 'cancelled' "
                    "WHERE status = 'pending' AND id IN ("
                    "  SELECT cs.request_item_id FROM flows.case_steps cs "
                    "  JOIN flows.cases c ON c.id = cs.case_id "
                    "  WHERE c.execution_id = :e AND cs.request_item_id IS NOT NULL) "
                    "RETURNING request_id"
                ),
                {"e": execution_id},
            )
        )
        .scalars()
        .all()
    )
    # ONLY the requests this cancel actually withdrew something from. Sweeping every request the
    # journey touched relabelled a contact who had already completed all of their items
    # ('fulfilled') as 'cancelled', because an unrelated staff step elsewhere was still outstanding.
    affected = sorted({int(r) for r in withdrawn if r is not None})
    if affected:
        await session.execute(
            text(
                "UPDATE documents.requests SET status = 'cancelled' "
                "WHERE id = ANY(:ids) AND status <> 'cancelled' AND NOT EXISTS ("
                "  SELECT 1 FROM documents.request_items ri "
                "  WHERE ri.request_id = documents.requests.id AND ri.status = 'pending')"
            ),
            {"ids": affected},
        )
    await session.execute(
        text(
            "UPDATE flows.case_steps cs SET status = 'cancelled', "
            "  data = cs.data || CAST(:d AS jsonb) "
            "FROM flows.cases c "
            "WHERE c.id = cs.case_id AND c.execution_id = :e "
            "  AND cs.status NOT IN ('fulfilled', 'skipped', 'cancelled')"
        ),
        {"e": execution_id, "d": json.dumps({"cancelled_reason": reason} if reason else {})},
    )
    # Cancel the outstanding tasks the journey raised (they reference the execution as subject).
    await session.execute(
        text(
            "UPDATE tasks.tasks SET status = 'cancelled' "
            "WHERE tenant_id = :t AND subject_type = 'execution' AND status IN "
            "  ('open', 'in_progress') AND subject_id = "
            "  (SELECT uid FROM flows.executions WHERE id = :e)"
        ),
        {"t": tenant_id, "e": execution_id},
    )
    cancelled_cases = (
        (
            await session.execute(
                text(
                    "UPDATE flows.cases SET status = 'cancelled', "
                    "  cancelled_at = COALESCE(cancelled_at, now()) "
                    "WHERE execution_id = :e AND completed_at IS NULL AND cancelled_at IS NULL "
                    "RETURNING id"
                ),
                {"e": execution_id},
            )
        )
        .scalars()
        .all()
    )
    # Retire the wake-ups an abandoned journey armed. The timer loop cancels a due timer whose case
    # is already terminal anyway, but that check only runs when the timer comes due — which for a
    # ninety-day wait is a quarter of a year of rows claiming a cancelled client will be chased.
    for cancelled_case in cancelled_cases:
        await timers_mod.cancel_for_case(session, case_id=int(cancelled_case))
    await _save_execution_state(session, execution_id, State.CANCELLED)
    await _append_event(
        session,
        tenant_id=tenant_id,
        execution_id=execution_id,
        case_id=None,
        event_type="execution.cancelled",
        payload={"reason": reason, "by": cancelled_by},
        idempotency_key=f"cancel:{execution_id}",
    )
    await _project(session, execution_id, tenant_id=tenant_id, exec_state=State.CANCELLED)
    return True


class CaseStatusRefused(ValueError):
    """A ``set_case_status`` the engine will not perform, with a reason fit to show an author."""


async def set_case_status(
    session: AsyncSession,
    *,
    tenant_id: int,
    case_uid: str,
    status: str,
    reason: str | None = None,
    changed_by: str | None = None,
) -> bool:
    """Latch ONE case into a terminal state. Returns False if it was already terminal.

    The service behind the contract's ``set_case_status`` action, written here rather than in the
    executor so that the day a human route wants it, both go through one implementation with one
    set of rules.

    **Terminal states only, and this is not a limitation to be lifted later.** Case state is
    DERIVED, not stored-and-trusted: every step event runs ``recompute``, which re-derives each
    case's state from its steps via ``derivation.derive_case_state``. That function's first rule is
    "terminal ⇒ unchanged (latched)" — so ``completed``/``cancelled``/``failed`` set here survive,
    and a working state (``active``/``waiting``/``blocked``/``draft``) set here would be silently
    overwritten by the very next step event. An action whose effect evaporates on the next event is
    worse than one that refuses: the author would see it work in a test and not in production.
    ``app/flows/effects.py`` refuses the non-terminal ones at publish preflight for the same
    reason, so an author learns this while writing the rule, not at 3am.

    Cancelling one case does NOT run ``cancel_execution``'s compensation (withdrawing the client's
    request items, cancelling the journey's tasks). That is deliberate: this is a case-level status
    change, and quietly performing a whole execution's teardown from a one-line rule action would
    be the sort of surprise a no-code tool must never spring. A rule that wants the full abandon
    should be a flow action step (Phase B) with that behaviour named.

    Tolerant of two shapes the orchestrated path never produces but the table permits, because
    this function can be pointed at ANY case by uid and must not 500 on one:
    ``flows.cases.status`` is free-form text (0021) so a v1 case may hold ``in_progress`` or a
    firm-authored milestone name, and ``execution_id`` is nullable so a case may not belong to an
    execution at all.
    """
    try:
        target = State(status)
    except ValueError as exc:
        raise CaseStatusRefused(f"'{status}' is not a case status") from exc
    if target not in TERMINAL:
        raise CaseStatusRefused(
            f"'{status}' is a working state the engine re-derives from the case's steps, so "
            "setting it would not survive the next event — only "
            f"{', '.join(sorted(s.value for s in TERMINAL))} can be set directly"
        )

    row = (
        (
            await session.execute(
                text(
                    "SELECT id, execution_id, status FROM flows.cases "
                    "WHERE uid = :u AND tenant_id = :t"
                ),
                {"u": case_uid, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise CaseStatusRefused(f"case {case_uid} does not exist in this firm")
    # A status the state machine does not know (a v1 'in_progress', a firm-authored milestone) is
    # read as a WORKING state: it is certainly not one of the three terminal words, and treating
    # "we cannot parse this" as "already finished" would silently refuse to close real cases.
    current = _as_state(row["status"], default=State.ACTIVE)
    if current in TERMINAL:
        return False  # latched; idempotent rather than an error (a redelivery must be harmless)
    if not can_transition(current, target):
        raise CaseStatusRefused(f"a case cannot move from {current.value} to {target.value}")

    execution_id = int(row["execution_id"]) if row["execution_id"] is not None else None
    if execution_id is not None:
        await _advisory_lock(session, execution_id)
    await _save_case_state(session, int(row["id"]), target)
    await _append_event(
        session,
        tenant_id=tenant_id,
        execution_id=execution_id,
        case_id=int(row["id"]),
        event_type=f"case.{target.value}",
        payload={"reason": reason, "by": changed_by},
        idempotency_key=f"status:{row['id']}:{target.value}",
    )
    if execution_id is None:
        return True  # a standalone case: there is no rollup or projection to re-roll

    # Re-roll the execution and its projection from the cases as they now stand. Not `recompute`:
    # that would re-derive every case from its steps, which is right after a STEP event and wrong
    # here — this case's new state is the input, not something to be recalculated away.
    states = (
        (
            await session.execute(
                text("SELECT status FROM flows.cases WHERE execution_id = :e"),
                {"e": execution_id},
            )
        )
        .scalars()
        .all()
    )
    exec_state = derive_execution_state(
        _as_state(await _execution_status(session, execution_id), default=State.ACTIVE),
        [CaseView(state=_as_state(s, default=State.ACTIVE)) for s in states],
    )
    await _save_execution_state(session, execution_id, exec_state)
    await _project(session, execution_id, tenant_id=tenant_id, exec_state=exec_state)
    return True


def _as_state(value: object, *, default: State) -> State:
    """One stored status as a ``State``, falling back rather than raising.

    ``flows.cases.status``/``flows.executions.status`` are free-form text by design (0021: "the
    vocabulary lives in the flow definition"), so anything reading them as the seven-state machine
    has to cope with a value from outside it."""
    try:
        return State(str(value))
    except ValueError:
        return default


async def retry_step(
    session: AsyncSession,
    *,
    tenant_id: int,
    case_id: int,
    step_key: str,
    participant_subject_id: int | None = None,
) -> bool:
    """Re-run a failed step's ``instantiate`` (a render that errored, a check that never started).

    The step's artifact is created afresh through the handler, so a transient failure has an
    operator escape hatch instead of pinning the case. False when there is nothing to retry.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT cs.id, cs.step_type, cs.status, cs.participant_subject_id, "
                    "       c.execution_id, c.subject_type, c.subject_id "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE cs.case_id = :c AND cs.step_key = :k "
                    "  AND (CAST(:psid AS int) IS NULL "
                    "       OR cs.participant_subject_id = CAST(:psid AS int)) "
                    "ORDER BY cs.id LIMIT 1"
                ),
                {"c": case_id, "k": step_key, "psid": participant_subject_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["execution_id"] is None:
        return False
    if str(row["status"]) not in ("failed", "blocked"):
        return False
    execution_id = int(row["execution_id"])
    definition = await _definition_steps(session, execution_id)
    cfg = (definition.get(step_key) or {}).get("config")
    # Reset by ROW ID, not by (case, step_key): with participant omitted the key-wide UPDATE reset
    # every participant's instance of a fanned step and then re-instantiated only one, quietly
    # wiping the others' request items. The instance re-instantiated below is exactly this row.
    target_participant = row["participant_subject_id"]
    await session.execute(
        text(
            "UPDATE flows.case_steps SET status = 'pending', request_item_id = NULL WHERE id = :i"
        ),
        {"i": int(row["id"])},
    )
    await _instantiate_step(
        session,
        tenant_id=tenant_id,
        execution_id=execution_id,
        case_id=case_id,
        step_key=step_key,
        step_type=str(row["step_type"]),
        config=cfg if isinstance(cfg, dict) else {},
        subject_type=row["subject_type"],
        subject_id=row["subject_id"],
        participant_subject_id=target_participant,
        requests_cache={},
    )
    await recompute(session, execution_id, tenant_id=tenant_id)
    return True


async def withdraw_step(
    session: AsyncSession,
    *,
    tenant_id: int,
    case_id: int,
    step_key: str,
    participant_subject_id: int | None,
    reason: str,
) -> bool:
    """Take one open step off a running journey because the fact it was raised for has changed.

    A branch is decided once, when the walk enters it, and is not re-read. So a fact that changes
    after the branch released a step (a W-9 answer cleared on the client record) leaves that step
    asking for work that no longer applies. This is the step-scoped twin of ``_compensate_case``:
    the handler's ``cancel`` runs, the pending request item is cancelled so the client portal stops
    asking, and the step becomes ``skipped`` with ``reason``, which is settled and never blocks the
    case. Then the execution recomputes.

    Only an open instance (``pending``/``waiting``/``blocked``) is withdrawn. Work already done is
    never undone. Returns whether this instance was withdrawn."""
    execution_id = (
        await session.execute(
            text("SELECT execution_id FROM flows.cases WHERE id = :c AND tenant_id = :t"),
            {"c": case_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if execution_id is None:
        return False
    await _advisory_lock(session, int(execution_id))
    row = (
        (
            await session.execute(
                text(
                    "SELECT cs.id, cs.step_type, cs.request_item_id, cs.data, "
                    "       c.subject_type, c.subject_id "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE cs.case_id = :c AND cs.step_key = :k "
                    "  AND (CAST(:psid AS int) IS NULL "
                    "       OR cs.participant_subject_id = CAST(:psid AS int)) "
                    "  AND cs.status IN ('pending', 'waiting', 'blocked') "
                    "ORDER BY cs.id LIMIT 1 FOR UPDATE OF cs"
                ),
                {"c": case_id, "k": step_key, "psid": participant_subject_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return False
    handler = handlers.get_handler(str(row["step_type"]))
    if handler is not None:
        cfg = ((await _definition_steps(session, int(execution_id))).get(step_key) or {}).get(
            "config"
        )
        await handler.cancel(
            handlers.StepContext(
                session=session,
                tenant_id=tenant_id,
                execution_id=int(execution_id),
                case_id=case_id,
                step_key=step_key,
                config=cfg if isinstance(cfg, dict) else {},
                subject_type=row["subject_type"],
                subject_id=row["subject_id"],
                participant_subject_id=participant_subject_id,
                data=_as_dict(row["data"]),
            )
        )
    if row["request_item_id"] is not None:
        request_id = (
            await session.execute(
                text(
                    "UPDATE documents.request_items SET status = 'cancelled' "
                    "WHERE id = :i AND status = 'pending' RETURNING request_id"
                ),
                {"i": int(row["request_item_id"])},
            )
        ).scalar_one_or_none()
        if request_id is not None:
            # Nothing left to ask for: the request is closed as answered when the client did
            # answer something on it, and as cancelled when this was all it asked.
            await session.execute(
                text(
                    "UPDATE documents.requests SET completed_at = now(), status = CASE "
                    "  WHEN EXISTS (SELECT 1 FROM documents.request_items ri "
                    "               WHERE ri.request_id = documents.requests.id "
                    "                 AND ri.status = 'fulfilled') THEN 'fulfilled' "
                    "  ELSE 'cancelled' END "
                    "WHERE id = :r AND status = 'pending' AND NOT EXISTS ("
                    "  SELECT 1 FROM documents.request_items ri "
                    "  WHERE ri.request_id = documents.requests.id AND ri.status = 'pending')"
                ),
                {"r": int(request_id)},
            )
    await session.execute(
        text(
            "UPDATE flows.case_steps SET status = 'skipped', "
            "  data = (data - CAST(:gated AS text)) || CAST(:d AS jsonb) WHERE id = :i"
        ),
        {"i": int(row["id"]), "gated": _GATED_ON_KEY, "d": json.dumps({"skipped_reason": reason})},
    )
    await _append_event(
        session,
        tenant_id=tenant_id,
        execution_id=int(execution_id),
        case_id=case_id,
        event_type="step.withdrawn",
        payload={"step_key": step_key, "participant": participant_subject_id, "reason": reason},
        idempotency_key=f"{case_id}:{step_key}:{participant_subject_id or '-'}",
    )
    await recompute(session, int(execution_id), tenant_id=tenant_id)
    return True


async def on_document_rendered(
    session: AsyncSession, document_id: str, *, error: str | None = None
) -> None:
    """Hook the render worker calls when a ``generate_document`` step's document finishes.

    A ``generate_document`` step records the document it enqueued on ``case_steps.data.document_id``
    and reports WAITING; this closes the loop — a successful render completes the step, a failed one
    fails it with the render error so the case blocks visibly (and can be retried) rather than
    waiting forever. A no-op for documents no case step owns (ordinary staff generation)."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT cs.case_id, cs.step_key, cs.participant_subject_id, c.tenant_id "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE c.execution_id IS NOT NULL "
                    "  AND cs.data ->> 'document_id' = CAST(:d AS text) "
                    "  AND cs.status IN ('pending', 'waiting') LIMIT 1"
                ),
                {"d": str(document_id)},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return
    event = StepEvent(
        signal=StepSignal.FAILED if error else StepSignal.COMPLETED,
        case_id=int(row["case_id"]),
        step_key=str(row["step_key"]),
        participant_subject_id=row["participant_subject_id"],
        reason=f"render_failed: {error}"[:500] if error else "rendered",
    )
    await apply_step_event(session, int(row["tenant_id"]), event)


async def on_action_run_settled(
    session: AsyncSession, run_uid: str, *, error: str | None = None
) -> None:
    """Close a ``setup_account`` step that is waiting on an action plan.

    The step records ``case_steps.data.action_run_uid`` and reports WAITING. A
    no-op when the journey already finished the step inline, or when no step owns
    the run.
    """
    # One plan opens every account on the application. Each setup step records the
    # same run, so settling it has to release all of them. Closing only the first
    # left the other account on Record hand-off.
    rows = (
        (
            await session.execute(
                text(
                    "SELECT cs.case_id, cs.step_key, cs.participant_subject_id, c.tenant_id "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE c.execution_id IS NOT NULL "
                    "  AND cs.data ->> 'action_run_uid' = CAST(:u AS text) "
                    "  AND cs.status IN ('pending', 'waiting')"
                ),
                {"u": str(run_uid)},
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        event = StepEvent(
            signal=StepSignal.FAILED if error else StepSignal.COMPLETED,
            case_id=int(row["case_id"]),
            step_key=str(row["step_key"]),
            participant_subject_id=row["participant_subject_id"],
            reason=f"action_failed: {error}"[:500] if error else "action_settled",
        )
        await apply_step_event(session, int(row["tenant_id"]), event)


async def on_kyc_result(
    session: AsyncSession,
    kyc_check_id: int | None,
    *,
    kind: str = kyc_mod.KIND_AML,
    outcome: str = "passed",
    external_ref: str | None = None,
    raw: dict[str, Any] | None = None,
) -> bool:
    """Settle a ``crm.kyc_checks`` row and release the step waiting on it.

    The counterpart of ``on_document_rendered`` for identity/AML: a ``verify_identity`` or
    ``aml_check`` step records the check row it opened on ``case_steps.data.kyc_check_id`` and
    reports WAITING, and this closes the loop. ``passed`` completes the step;
    ``failed``/``referred`` fails it, so a case involving a confirmed sanctions match or a rejected
    identity blocks visibly instead of proceeding.

    ``kind`` is what the outcome is a statement ABOUT, and it only decides the validity horizon and
    the reason string — the two things that legitimately differ between an identity check and an AML
    screen. Callers use the two wrappers below rather than passing it.

    A no-op for checks no case step owns — a screen an operator ran ad hoc from the console, or a
    verification against a check some other journey has already settled, is not releasing anybody's
    onboarding and must not be mistaken for one. Returns whether a step was released.
    """
    if kyc_check_id is None:
        return False
    row = (
        (
            await session.execute(
                text(
                    "SELECT cs.case_id, cs.step_key, cs.participant_subject_id, c.tenant_id "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE c.execution_id IS NOT NULL "
                    "  AND cs.data ->> 'kyc_check_id' = CAST(:k AS text) "
                    "  AND cs.status IN ('pending', 'waiting') LIMIT 1"
                ),
                {"k": str(int(kyc_check_id))},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return False

    # Record the outcome on the check itself before releasing the step, so the client record and the
    # engine cannot disagree about whether the person passed. The horizon comes from the unit-tested
    # rule (`kyc.compute_valid_until`): none for an identity or AML pass, which does not lapse.
    passed = outcome == "passed"
    verified_at = (await session.execute(text("SELECT now()"))).scalar_one()
    valid_until = kyc_mod.compute_valid_until(verified_at, kind) if passed else None
    await session.execute(
        text(
            "UPDATE crm.kyc_checks SET status = :st, "
            "  verified_at = COALESCE(:verified, verified_at), "
            "  valid_until = COALESCE(:vu, valid_until), "
            "  external_ref = COALESCE(:ref, external_ref), "
            "  raw = raw || CAST(:raw AS jsonb), updated_at = now() "
            "WHERE id = :id"
        ),
        {
            "st": "passed" if passed else outcome,
            "verified": verified_at if passed else None,
            "vu": valid_until,
            "ref": external_ref,
            "raw": json.dumps(raw or {}),
            "id": int(kyc_check_id),
        },
    )
    identity = kind == kyc_mod.KIND_IDENTITY
    if passed:
        reason = "identity_verified" if identity else "screened_clear"
    else:
        reason = f"{'identity' if identity else 'aml'}_{outcome}"
    event = StepEvent(
        signal=StepSignal.COMPLETED if passed else StepSignal.FAILED,
        case_id=int(row["case_id"]),
        step_key=str(row["step_key"]),
        participant_subject_id=row["participant_subject_id"],
        reason=reason,
    )
    await apply_step_event(session, int(row["tenant_id"]), event)
    return True


async def on_aml_result(
    session: AsyncSession,
    kyc_check_id: int | None,
    *,
    outcome: str = "passed",
    external_ref: str | None = None,
) -> bool:
    """What the AML pipeline calls when a screen (and any adjudication) concludes."""
    return await on_kyc_result(
        session,
        kyc_check_id,
        kind=kyc_mod.KIND_AML,
        outcome=outcome,
        external_ref=external_ref,
    )


async def on_identity_result(
    session: AsyncSession,
    kyc_check_id: int | None,
    *,
    outcome: str = "passed",
    external_ref: str | None = None,
    raw: dict[str, Any] | None = None,
) -> bool:
    """What an identity verification's result calls when it concludes.

    ``raw`` is the vendor's own payload, merged onto the check so the evidence for the decision is
    on the record rather than only in a log line.
    """
    return await on_kyc_result(
        session,
        kyc_check_id,
        kind=kyc_mod.KIND_IDENTITY,
        outcome=outcome,
        external_ref=external_ref,
        raw=raw,
    )


async def _append_event(
    session: AsyncSession,
    *,
    tenant_id: int,
    # Nullable to match the column (0025): a fact can belong to a case that is not part of an
    # execution — flows.cases.execution_id is itself nullable, and set_case_status can be pointed
    # at such a case.
    execution_id: int | None,
    case_id: int | None,
    event_type: str,
    payload: dict[str, object],
    idempotency_key: str,
) -> None:
    await session.execute(
        text(
            "INSERT INTO flows.domain_events "
            "  (tenant_id, execution_id, case_id, event_type, payload, idempotency_key) "
            "VALUES (:t, :e, :c, :et, CAST(:p AS jsonb), :k) "
            "ON CONFLICT (tenant_id, event_type, idempotency_key) DO NOTHING"
        ),
        {
            "t": tenant_id,
            "e": execution_id,
            "c": case_id,
            "et": event_type,
            "p": json.dumps(payload),
            "k": idempotency_key,
        },
    )
