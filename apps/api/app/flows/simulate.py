"""Dry-run a flow **definition** against a subject — the pure simulator.

The rule tier's dry run (``POST /automations/{uid}/test``) answers "would this rule fire for this
client". This is the same question one level up: *what happens to a client like this when they go
through this journey* — which nodes they enter and in what order, which arm each branch takes and
why, what would be planned and at what tier, where the journey pauses and for how long, and where
it ends. An author gets that answer **before** publishing, which is the difference between a graph
builder you can trust and one you find out about through a client.

Side-effect freedom is STRUCTURAL, not a promise
------------------------------------------------
This module cannot perform an action even if something called it wrongly, because it has no way
to. It imports the engine's **pure** modules only — ``graph`` (shape + traversal), ``conditions``
(the one evaluator), ``planning`` (the pure tier fan-out), ``timers.resolve_fire_at`` (documented
pure and total) — and imports **none** of ``orchestrator`` / ``engine`` / ``step_handlers`` /
``effects`` / ``memory`` / ``dispatcher``, which are the only code paths that write a case,
arm a timer, enqueue an outbox row, send an email or call a provider. An action node is
*described* from its authored ``type`` and ``config``; the executor is never reached, never
imported, and could not be reached from here without adding an import that
``tests/test_flows_simulate.py::test_the_simulator_imports_no_executing_module`` fails on.

There is a second, independent guard at the HTTP layer: the endpoint runs the whole dry run inside
a savepoint it always rolls back (``routers/flow_insights.py``), so even a future read that turned
out to write could not commit. Two guarantees, neither relying on the other.

Where the simulation is faithful, and where it departs
------------------------------------------------------
Faithful, deliberately, because *a dry run that lies is worse than none*:

* the context is built by the same queries and carries the same vocabulary the runtime builds
  (``conditions.FIELD_SCOPES``; see ``build_context`` below and ``orchestrator.case_context``);
* branch resolution is ``graph.next_node`` — the engine's own function, not a re-implementation;
* plan-time step visibility and the tier fan-out are ``planning.plan_execution`` — likewise, so a
  step conditioned out here is conditioned out at instantiate;
* a step planned NOWHERE is walked past rather than blocking, exactly as ``_enter_node`` does;
* an untaken arm's elided steps come from ``planning.elided_by_branch``;
* a ``wait``'s resume time is ``timers.resolve_fire_at``, including its "``None`` means do not
  wait at all" rule.

Two departures, both stated in the output so a reader is never misled:

1. **Every step is assumed to complete.** The engine stops at a released step and waits for a
   human; the simulation carries on, because "what would happen" is the question. Each such hop is
   marked ``blocking`` so the surface can say "the journey waits here for the client".
2. **One walk, not one per case.** The engine walks the graph per case (a tiered onboarding has a
   profile case per participant and an account case per account), so a branch on a participant's
   own answers resolves per participant. The simulation walks once against the subject's context
   and reports the tier fan-out as *metadata on each step hop* (how many instances, at which
   tier). For the single-client subject this surface is built around, the two coincide; for a
   multi-participant application they do not, and ``Simulation.tiers`` says how many cases the
   plan would really produce.

Also honest about what it does not know: ``account.value`` is lake-derived and the runtime does
not fetch it either (``orchestrator.case_context`` explains why), so a branch on it resolves to
``None`` here exactly as it would there.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.flows import conditions as conditions_mod
from app.flows import graph as graph_mod
from app.flows import planning
from app.flows.timers import resolve_fire_at

#: The most hops a simulated walk takes before it gives up and reports a loop. The engine's own
#: walk is capped the same way (``orchestrator._MAX_GRAPH_HOPS``); a published graph is acyclic by
#: publish rule, but a DRAFT is exactly where a cycle lives, and the dry run is the surface that
#: should say so rather than spin.
MAX_HOPS = 500

#: How the walk ended. ``exit``/``handed_off``/``completed`` are authored endings; the rest are the
#: three ways an author's draft ends badly, and naming them apart is the whole point — "it stops
#: dead here" and "it loops here" need different fixes.
OUTCOME_KINDS = (
    "exit",  # an explicit `exit` node latched an outcome
    "handed_off",  # a `start_flow` tail call
    "completed",  # the last node's `next` was absent on a graph with no exit node
    "unfinished",  # a non-terminal node with `next: null` — publish refuses this
    "dangling",  # an edge into a node id the definition does not have
    "looped",  # the walk came back to a node it had already entered
    "too_long",  # MAX_HOPS exhausted
)

#: Why a node is not on this subject's path. Ordered from "the author meant this" to "the author
#: probably did not".
EXCLUSION_REASONS = (
    "branch_not_taken",  # an arm of a branch this subject did not take
    "timeout_only",  # only reachable if a wait_for_event's deadline passes
    "not_planned",  # a step whose visibility excluded it at every tier
    "orphan",  # not reachable from the entry at all — an authoring mistake
    "unreached",  # reachable in principle; the walk ended before getting there
)


# ---------------------------------------------------------------------------------------------
# The result model (mirrored in TypeScript by pkgs/ui .../book/journeys/simulation.ts)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedInstance:
    """One concrete step row the plan would create — the tier it runs at and who it is about."""

    case_kind: str  # profile | account | application
    subject_type: str  # contact | account | group
    subject_id: int | None
    participant_subject_id: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_kind": self.case_kind,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "participant_subject_id": self.participant_subject_id,
        }


@dataclass(frozen=True)
class Hop:
    """One node the case enters, in order."""

    node_id: str
    kind: str
    title: str
    #: The step type / action type; ``None`` for a control node, whose kind is its identity.
    type: str | None = None
    #: True when the real engine would stop here and wait for a person, a timer or an event. The
    #: simulation walks on regardless (see the module docstring), and this is what says so.
    blocking: bool = False
    #: Work nodes: the instances the plan would create. Empty ⇒ planned nowhere ⇒ walked past.
    instances: tuple[PlannedInstance, ...] = ()
    #: Work nodes: the authored plan-time visibility condition, evaluated leaf by leaf against the
    #: subject's own context. ``None`` when the node authored none.
    visibility: dict[str, Any] | None = None
    scope: str | None = None
    assignee: str | None = None
    required: bool = True
    #: Branch nodes: every arm with its verdict, and which one was taken.
    arms: tuple[dict[str, Any], ...] = ()
    taken_arm: str | None = None
    #: Steps this branch's untaken arms elide (``planning.elided_by_branch``).
    elided: tuple[str, ...] = ()
    #: Wait nodes: when the case would resume, and how far into the journey that is.
    resumes_at: datetime | None = None
    #: wait_for_event: the trigger it listens for and where a timeout would send it.
    listens_for: str | None = None
    timeout_branch: str | None = None
    #: How many days into the journey this hop happens (0 for everything before the first wait).
    at_day: float = 0.0
    #: The authored config, verbatim — the surface describes an action from this rather than the
    #: server writing prose the rule tier already knows how to write.
    config: Mapping[str, Any] = field(default_factory=dict)
    #: Free-text only where the structure cannot carry it (why a step was walked past).
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "title": self.title,
            "type": self.type,
            "blocking": self.blocking,
            "instances": [i.as_dict() for i in self.instances],
            "visibility": self.visibility,
            "scope": self.scope,
            "assignee": self.assignee,
            "required": self.required,
            "arms": list(self.arms),
            "taken_arm": self.taken_arm,
            "elided": list(self.elided),
            "resumes_at": self.resumes_at,
            "listens_for": self.listens_for,
            "timeout_branch": self.timeout_branch,
            "at_day": round(self.at_day, 2),
            "config": dict(self.config),
            "note": self.note,
        }


@dataclass(frozen=True)
class Excluded:
    node_id: str
    kind: str
    title: str
    reason: str
    #: The node whose decision excluded it (a branch, a wait_for_event), when there is one.
    because_of: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "title": self.title,
            "reason": self.reason,
            "because_of": self.because_of,
        }


@dataclass(frozen=True)
class Simulation:
    path: tuple[Hop, ...]
    excluded: tuple[Excluded, ...]
    outcome: dict[str, Any]
    tiers: dict[str, Any]
    #: How the report should be read. ``graph`` walks a path; ``legacy`` reports the PLAN, because a
    #: pre-v3 definition authored no sequence to walk (see ``_legacy_path``).
    shape: str  # "graph" | "legacy"
    facts: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            # Structural, not a claim: this module imports nothing that can perform anything.
            "performed": False,
            "shape": self.shape,
            "path": [h.as_dict() for h in self.path],
            "excluded": [e.as_dict() for e in self.excluded],
            "outcome": self.outcome,
            "tiers": self.tiers,
            "facts": dict(self.facts),
        }


# ---------------------------------------------------------------------------------------------
# Explaining a condition (shared with the rule tier's dry run)
# ---------------------------------------------------------------------------------------------
#
# ``conditions.explain`` walks a condition tree leaf by leaf and calls the SAME evaluator on each
# node, so a verdict shown to an author can never disagree with the engine's. It lives beside the
# evaluator rather than here because the rule tier's ``POST /automations/{uid}/test`` needs the
# identical shape, and two implementations of "which row is wrong" is exactly the drift the one
# evaluator exists to prevent.
explain = conditions_mod.explain


# ---------------------------------------------------------------------------------------------
# The plan preview
# ---------------------------------------------------------------------------------------------


def _instances_by_step(
    steps: Sequence[Mapping[str, Any]], app_graph: planning.ApplicationGraph
) -> tuple[dict[str, list[PlannedInstance]], dict[str, Any]]:
    """Run the REAL planner over the definition's derived step projection and index the result by
    step key.

    Nothing here re-implements visibility, KYC relevance or ``per_participant`` fan-out:
    ``planning.plan_execution`` is the function the orchestrator calls, so a step this reports as
    planned-nowhere is a step instantiate would not create either.
    """
    plan = planning.plan_execution([dict(s) for s in steps], app_graph)
    by_step: dict[str, list[PlannedInstance]] = {}
    for case in plan:
        for step in case.steps:
            by_step.setdefault(step.step_key, []).append(
                PlannedInstance(
                    case_kind=case.kind,
                    subject_type=case.subject_type,
                    subject_id=case.subject_id,
                    participant_subject_id=step.participant_subject_id,
                )
            )
    tiers = {
        "cases": len(plan),
        "profile_cases": sum(1 for c in plan if c.kind == "profile"),
        "account_cases": sum(1 for c in plan if c.kind == "account"),
        "application_cases": sum(1 for c in plan if c.kind == "application"),
        "step_instances": sum(len(c.steps) for c in plan),
        "participants": len(app_graph.participants),
        "accounts": len(app_graph.accounts),
    }
    return by_step, tiers


def _step_context(node: graph_mod.Node, app_graph: planning.ApplicationGraph) -> dict[str, Any]:
    """The context a step's ``visibility`` is judged against, for the EXPLANATION only.

    The verdict that matters is the planner's (``_instances_by_step``), which evaluates per
    participant and per account. This one exists so the surface can show an author which leaf of
    their condition failed, and it therefore mirrors the planner's own vocabulary: the
    application-level facts plus, for a participant-scoped step, the first participant's
    ``applicant.*`` keys — the same shape ``planning._participant_context`` builds.
    """
    ctx: dict[str, Any] = dict(app_graph._app_context())
    scope = node.scope or planning.default_scope(str(node.type or ""))
    if scope == "participant" and app_graph.participants:
        person = app_graph.participants[0]
        ctx["applicant.type"] = person.type
        ctx["applicant.roles"] = list(person.roles)
        ctx["applicant.onboarding_role"] = planning.onboarding_role(person, app_graph)
        ctx["applicant.has_pension_product"] = app_graph.participant_has_pension(person.contact_id)
        ctx["applicant.has_sipp_product"] = app_graph.participant_has_sipp(person.contact_id)
    elif scope == "account" and app_graph.accounts:
        account = app_graph.accounts[0]
        ctx["account.product"] = account.product
        ctx["account.custody"] = account.custody or app_graph.custody
        ctx["account.has_transfer"] = account.has_transfer
    return ctx


# ---------------------------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------------------------


def _arm_report(
    node: graph_mod.Node, facts: Mapping[str, Any], chosen: str | None
) -> tuple[dict[str, Any], ...]:
    """Every arm of a branch with its own verdict and the values it read.

    The arms are reported in authored order with the else marked, and the *taken* one is whichever
    ``graph.next_node`` picked — read from its answer rather than recomputed, so the report and the
    decision cannot disagree even if the ordering rule changes. An arm after the taken one shows
    ``reached: false``: first-satisfiable-wins means it was never evaluated at runtime, and showing
    a verdict for it would invite an author to "fix" a condition that never ran.
    """
    out: list[dict[str, Any]] = []
    decided = False
    for arm in node.branches:
        is_else = arm.is_else
        reached = not decided and not is_else
        passed = conditions_mod.evaluate(arm.conditions, facts) if (reached or is_else) else False
        out.append(
            {
                "id": arm.id,
                "label": arm.label,
                "is_else": is_else,
                "next": arm.next,
                "reached": reached or is_else,
                "passed": passed if (reached or is_else) else None,
                "taken": arm.next == chosen and not decided,
                "explanation": (None if is_else else explain(arm.conditions, facts)),
            }
        )
        if not is_else and reached and passed:
            decided = True
    return tuple(out)


def _legacy_path(
    graph: graph_mod.Graph,
    by_step: Mapping[str, list[PlannedInstance]],
    app_graph: planning.ApplicationGraph,
) -> tuple[tuple[Hop, ...], tuple[Excluded, ...], dict[str, Any]]:
    """A pre-v3 ``{steps}`` definition has no path to walk — report the PLAN instead.

    Its order is the TIER and then ``depends_on``, resolved by ``dependencies.unmet``; it authored
    no sequence at all.

    ``next_nodes`` refuses to make a decision for a legacy definition precisely so nothing
    sequentialises a live onboarding journey by accident. Reporting a linear "path" here would be
    that same mistake wearing a UI, so the dry run reports which steps apply to this subject and
    which do not, in authored order, with no ordering claim at all. That is the honest answer, and
    it is still the answer the author needs ("will this client get the offshore form?").
    """
    hops: list[Hop] = []
    excluded: list[Excluded] = []
    for node in graph.nodes.values():
        if not node.is_step:
            continue
        instances = tuple(by_step.get(node.id, ()))
        if instances:
            hops.append(
                Hop(
                    node_id=node.id,
                    kind=node.kind,
                    title=node.label,
                    type=node.type,
                    blocking=node.kind == graph_mod.STEP_KIND,
                    instances=instances,
                    visibility=(
                        explain(node.visibility, _step_context(node, app_graph))
                        if node.visibility
                        else None
                    ),
                    scope=node.scope,
                    assignee=node.raw.get("assignee") if isinstance(node.raw, Mapping) else None,
                    required=bool(node.raw.get("required", True)),
                    config=node.config,
                )
            )
        else:
            excluded.append(
                Excluded(
                    node_id=node.id,
                    kind=node.kind,
                    title=node.label,
                    reason="not_planned",
                )
            )
    outcome = {
        "kind": "completed",
        "node_id": None,
        "detail": (
            "This journey was authored before the graph builder, so its steps run when their "
            "prerequisites settle rather than along a path."
        ),
        "elapsed_days": 0.0,
    }
    return tuple(hops), tuple(excluded), outcome


def _reaches_itself(parsed: graph_mod.Graph, node_id: str) -> bool:
    """Is this node its own descendant — i.e. genuinely on a loop?

    The walk needs this because a frontier meets a node twice for two very different reasons. Two
    arms of a fan-out re-converging on "notify the adviser" is the graph working exactly as
    authored; a node that leads back to itself is the draft the dry run exists to catch. Before the
    arity rule the walk was a single cursor and "I have been here before" could only mean the
    second, so the distinction did not have to be made.

    Computed on demand rather than up front: it costs a reachability walk and only a revisit asks.
    """
    return any(
        node_id in planning.reachable(parsed, target)
        for target in graph_mod.successors(parsed, node_id)
    )


def _predecessors(parsed: graph_mod.Graph) -> dict[str, list[str]]:
    """Who points at each node, from the same edges reachability walks."""
    out: dict[str, list[str]] = {nid: [] for nid in parsed.nodes}
    for nid in parsed.nodes:
        for target in graph_mod.successors(parsed, nid):
            if target in out:
                out[target].append(nid)
    return out


def _awaits_another_arm(
    parsed: graph_mod.Graph,
    node_id: str,
    predecessors: Mapping[str, list[str]],
    visited: list[str],
    frontier: deque[tuple[str, datetime]],
) -> bool:
    """Whether a node not yet reported still has an incoming arm on its way.

    True when one of its predecessors has not been walked and is reachable from a node still on
    the frontier. An arm the walk diverted away from is not on the frontier, so it is not waited
    for."""
    pending = [p for p in predecessors.get(node_id, ()) if p not in visited]
    if not pending:
        return False
    ahead = [target for target, _clock in frontier if target != node_id]
    return any(p in planning.reachable(parsed, target) for p in pending for target in ahead)


def simulate(
    definition: Mapping[str, Any],
    *,
    facts: Mapping[str, Any],
    app_graph: planning.ApplicationGraph,
    now: datetime | None = None,
) -> Simulation:
    """Walk ``definition`` for one subject and report what would happen. Performs nothing.

    ``definition`` must be the NORMALISED definition — what ``definitions.validate_definition``
    returns — because the derived ``steps`` projection is what the planner fans out, and running a
    dry run over a shape the save path would have rewritten is the first way a dry run starts
    lying. The endpoint validates before calling.

    ``facts`` is the flat/nested dotted-path context the ONE evaluator reads
    (``conditions.FIELD_SCOPES``); ``app_graph`` is the tier shape the planner fans over. ``now``
    pins the clock so a wait's resume time is reproducible in tests — and the clock **advances**
    across waits, so a journey with a 7-day chase and then a 30-day review reports the second
    resuming at day 37 rather than day 30.
    """
    clock = (now or datetime.now(UTC)).astimezone(UTC)
    started = clock
    parsed = graph_mod.parse(definition)
    steps_projection = definition.get("steps")
    by_step, tiers = _instances_by_step(
        steps_projection if isinstance(steps_projection, list) else [], app_graph
    )

    # No authored sequence ⇒ report the plan, not a path. Only a legacy definition qualifies: a v3
    # graph is wired, so it has a path and the walk below is the honest report of it.
    if parsed.is_legacy_linear:
        plan_hops, plan_excluded, plan_outcome = _legacy_path(parsed, by_step, app_graph)
        return Simulation(
            path=plan_hops,
            excluded=plan_excluded,
            outcome=plan_outcome,
            tiers=tiers,
            shape="legacy",
            facts=facts,
        )

    hops: list[Hop] = []
    #: node id -> the node whose decision took control away from it, for the exclusion report.
    diverted: dict[str, str] = {}
    visited: list[str] = []
    outcome: dict[str, Any] = {"kind": "completed", "node_id": None, "detail": None}

    # A FRONTIER, matching the runtime (``orchestrator._enter_node``): `next` may name several nodes
    # and they all happen at once, so the dry run reports them all rather than picking one and
    # calling the rest excluded. Each entry carries its OWN clock — a wait delays the arms after it
    # and nothing else, and a shared clock would have reported a sibling arm resuming seven days
    # late because some other arm paused.
    frontier: deque[tuple[str, datetime]] = deque()
    if parsed.entry:
        frontier.append((parsed.entry, clock))
    latest = clock

    predecessors = _predecessors(parsed)
    #: Consecutive re-queues without a node being reported. A join waits only while something else
    #: on the frontier can still move; past one full turn of it, the walk reports it anyway.
    waits = 0

    while frontier:
        current, clock = frontier.popleft()
        if (
            current not in visited
            and waits <= len(frontier)
            and _awaits_another_arm(parsed, current, predecessors, visited, frontier)
        ):
            # A join reached by a short arm first. The runtime holds it until every arm that can
            # still arrive has (an edge means "after"), so the report lists it there too.
            frontier.append((current, clock))
            waits += 1
            continue
        waits = 0
        latest = max(latest, clock)
        if current in visited:
            # Two arms meeting again is re-convergence, not a loop, and reporting it as one would
            # make every well-formed fan-out look broken. A real loop is a node that reaches itself.
            if _reaches_itself(parsed, current):
                outcome = {
                    "kind": "looped",
                    "node_id": current,
                    "detail": "The journey comes back to a node it has already run. Publish "
                    "refuses a loop — break the cycle before publishing.",
                }
                break
            continue
        node = parsed.nodes.get(current)
        if node is None:
            outcome = {
                "kind": "dangling",
                "node_id": current,
                "detail": "An edge points at a node this definition does not have.",
            }
            break
        if len(visited) >= MAX_HOPS:
            outcome = {
                "kind": "too_long",
                "node_id": current,
                "detail": f"The walk did not settle within {MAX_HOPS} hops.",
            }
            break
        visited.append(current)
        at_day = (clock - started).total_seconds() / 86400.0

        if node.kind == graph_mod.START_KIND:
            # The entry fan-out. No hop is reported for it: it is where the journey begins, which
            # the report already says, and a row saying "Start" would be a step the author cannot
            # act on.
            frontier.extend((target, clock) for target in node.next_ids)
            continue

        if node.is_step:
            instances = tuple(by_step.get(node.id, ()))
            gate = node.visibility
            hops.append(
                Hop(
                    node_id=node.id,
                    kind=node.kind,
                    title=node.label,
                    type=node.type,
                    # An action settles at instantiate and blocks nobody; a step waits for a human.
                    blocking=bool(instances) and node.kind == graph_mod.STEP_KIND,
                    instances=instances,
                    visibility=(explain(gate, _step_context(node, app_graph)) if gate else None),
                    scope=node.scope,
                    assignee=node.raw.get("assignee") if isinstance(node.raw, Mapping) else None,
                    required=bool(node.raw.get("required", True)),
                    at_day=at_day,
                    config=node.config,
                    note=(
                        None
                        if instances
                        else "Nothing is planned for this node for this subject, so the journey "
                        "walks straight past it."
                    ),
                )
            )
            frontier.extend((target, clock) for target in node.next_ids)
            continue

        if node.kind in graph_mod.BRANCHING_KINDS:
            chosen = graph_mod.branch_choice(parsed, node.id, facts)
            arms = _arm_report(node, facts, chosen)
            for arm in node.branches:
                if arm.next and arm.next != chosen:
                    diverted.setdefault(arm.next, node.id)
            hops.append(
                Hop(
                    node_id=node.id,
                    kind=node.kind,
                    title=node.label,
                    arms=arms,
                    taken_arm=next((str(a["id"]) for a in arms if a["taken"]), None),
                    elided=tuple(planning.elided_by_branch(parsed, node.id, chosen)),
                    at_day=at_day,
                    config=node.config,
                    note=(
                        None
                        if chosen
                        else "No arm of this branch leads anywhere — the journey stops here."
                    ),
                )
            )
            if chosen:
                frontier.append((chosen, clock))
            continue

        if node.kind == "wait":
            fire_at = resolve_fire_at(node.config, now=clock, ctx=facts)
            if fire_at is not None:
                clock = fire_at
            hops.append(
                Hop(
                    node_id=node.id,
                    kind=node.kind,
                    title=node.label,
                    blocking=fire_at is not None,
                    resumes_at=fire_at,
                    at_day=at_day,
                    config=node.config,
                    note=(
                        None
                        if fire_at is not None
                        # resolve_fire_at's documented rule, surfaced rather than hidden: a wait
                        # with no resolvable deadline does not park the case for ever, it does not
                        # wait at all — and an author who authored a date field that never gets
                        # populated needs to be told that here, not by a client complaining.
                        else "This pause has no resolvable end, so the journey does not wait at "
                        "all and carries straight on."
                    ),
                )
            )
            frontier.extend((target, clock) for target in node.next_ids)
            continue

        if node.kind == "wait_for_event":
            timeout_days = node.config.get("timeout_days")
            timeout_branch = node.config.get("timeout_branch")
            if isinstance(timeout_branch, str) and timeout_branch:
                diverted.setdefault(timeout_branch, node.id)
            hops.append(
                Hop(
                    node_id=node.id,
                    kind=node.kind,
                    title=node.label,
                    blocking=True,
                    listens_for=(
                        str(node.config.get("trigger")) if node.config.get("trigger") else None
                    ),
                    timeout_branch=(
                        str(timeout_branch) if isinstance(timeout_branch, str) else None
                    ),
                    at_day=at_day,
                    config=node.config,
                    note=(
                        None
                        if timeout_days
                        else "There is no deadline on this listener, so a journey that never sees "
                        "the event waits indefinitely."
                    ),
                )
            )
            # The simulation follows the EVENT arriving, because that is the path the author drew
            # as the main one; the timeout target is reported as an alternative rather than walked.
            frontier.extend((target, clock) for target in node.next_ids)
            continue

        if node.kind == "start_flow":
            hops.append(
                Hop(
                    node_id=node.id,
                    kind=node.kind,
                    title=node.label,
                    at_day=at_day,
                    config=node.config,
                )
            )
            outcome = {
                "kind": "handed_off",
                "node_id": node.id,
                "detail": "The journey hands over to another flow and ends here — a tail call, "
                "so control does not come back.",
                "flow_code": node.config.get("flow_code"),
            }
            break

        if node.kind == "exit":
            hops.append(
                Hop(
                    node_id=node.id,
                    kind=node.kind,
                    title=node.label,
                    at_day=at_day,
                    config=node.config,
                )
            )
            outcome = {
                "kind": "exit",
                "node_id": node.id,
                "detail": None,
                "outcome": node.config.get("outcome") or "completed",
                "reason": node.config.get("reason"),
            }
            break

        # An unknown kind cannot happen through `validate_definition`, but a stored version predates
        # every rule we add later. Stop and say so rather than guess a successor.
        outcome = {
            "kind": "dangling",
            "node_id": node.id,
            "detail": f"This version contains a node kind the engine does not know ({node.kind}).",
        }
        break

    if outcome["kind"] == "completed" and not outcome.get("detail"):
        # Every arm ran out of successors, which is what completing looks like for a fan-out.
        outcome["node_id"] = visited[-1] if visited else None
        outcome["detail"] = "The journey runs out of steps and the case completes."
    outcome["elapsed_days"] = round((latest - started).total_seconds() / 86400.0, 2)

    return Simulation(
        path=tuple(hops),
        excluded=_exclusions(parsed, visited, diverted, by_step),
        outcome=outcome,
        tiers=tiers,
        shape="graph",
        facts=facts,
    )


def _exclusions(
    parsed: graph_mod.Graph,
    visited: Sequence[str],
    diverted: Mapping[str, str],
    by_step: Mapping[str, list[PlannedInstance]],
) -> tuple[Excluded, ...]:
    """Every node this subject does not reach, and the decision that is responsible.

    "Unreachable" is four different findings and only one of them is a bug, so they are named
    apart: an arm this client did not take is the flow working, a node reachable only through a
    deadline is a design an author should see, a step planned nowhere is visibility doing its job,
    and a node no path reaches at all is an authoring mistake ``graph_issues`` also reports.

    A node is attributed to the nearest decision that diverted control away from it (a branch's
    untaken arm, a ``wait_for_event``'s timeout target) by walking forward from that arm's target
    — so everything downstream of an untaken arm inherits its reason rather than being reported as
    a mystery orphan.
    """
    seen = set(visited)
    from_entry = planning.reachable(parsed, parsed.entry)
    attributed: dict[str, str] = {}
    for target, decider in diverted.items():
        for nid in planning.reachable(parsed, target):
            if nid not in seen:
                attributed.setdefault(nid, decider)

    out: list[Excluded] = []
    for nid, node in parsed.nodes.items():
        if nid in seen:
            continue
        decider_id = attributed.get(nid)
        if decider_id is not None:
            decider_node = parsed.nodes.get(decider_id)
            reason = (
                "timeout_only"
                if decider_node is not None and decider_node.kind == "wait_for_event"
                else "branch_not_taken"
            )
        elif nid not in from_entry:
            reason = "orphan"
        elif node.is_step and not by_step.get(nid):
            reason = "not_planned"
        else:
            reason = "unreached"
        out.append(
            Excluded(
                node_id=nid,
                kind=node.kind,
                title=node.label,
                reason=reason,
                because_of=decider_id,
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------------------------
# Synthetic subjects (pure — the author sets the fields by hand)
# ---------------------------------------------------------------------------------------------


def synthetic_graph(
    *,
    contact_type: str = "individual",
    roles: Sequence[str] = ("owner",),
    custody: str | None = None,
    products: Sequence[str] = (),
    has_transfer: bool = False,
) -> planning.ApplicationGraph:
    """An ``ApplicationGraph`` the author describes rather than one read out of the book.

    A synthetic subject is not a lesser dry run — it is the only way to ask "what happens to an
    offshore SIPP client" for a firm that has not onboarded one yet, which is precisely when an
    author most needs the answer. One participant and one account per named product, because a
    synthetic multi-participant application is a modelling exercise nobody asked for; a real
    subject covers that case with real data.
    """
    participant = planning.Participant(
        contact_id=-1,
        type=contact_type,
        roles=frozenset(r for r in roles if isinstance(r, str)),
    )
    accounts = tuple(
        planning.AccountSpec(
            account_id=-(index + 1),
            participant_contact_ids=(-1,),
            product=str(product),
            custody=custody,
            has_transfer=has_transfer,
        )
        for index, product in enumerate(products)
    )
    return planning.ApplicationGraph(
        participants=(participant,), accounts=accounts, custody=custody
    )


def synthetic_facts(
    graph_spec: planning.ApplicationGraph,
    *,
    overrides: Mapping[str, Any] | None = None,
    memory: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The condition context for a synthetic subject: the application/account facts the author
    described, plus any ``contact.*`` / ``memory.*`` values they set by hand.

    The vocabulary is ``conditions.FIELD_SCOPES`` and nothing else, so a branch that reads
    ``contact.nationality`` here reads the same key it will read at runtime. Unset fields stay
    ABSENT rather than null-filled — an absent field resolves to ``None`` and only
    ``isEmpty``/``notEquals`` can match it, which is exactly what "we have no value for this"
    should do, and null-filling would make an author's untested field look tested.
    """
    at = (now or datetime.now(UTC)).astimezone(UTC)
    person = graph_spec.participants[0] if graph_spec.participants else None
    facts: dict[str, Any] = {
        "contact.type": person.type if person else None,
        "contact.tags": [],
        "application.custody": graph_spec.custody,
        "application.has_pension_product": graph_spec.has_pension_product,
        "application.has_sipp_product": graph_spec.has_sipp_product,
        "application.has_transfer": graph_spec.has_transfer,
        "case.status": "draft",
        "case.tags": [],
        "case.started_at": at,
        "time.hour": at.hour,
        "time.weekday": at.weekday(),
        "time.now": at,
    }
    if graph_spec.accounts:
        first = graph_spec.accounts[0]
        facts["account.product"] = first.product
        facts["account.custody"] = first.custody or graph_spec.custody
        facts["account.has_transfer"] = first.has_transfer
    if memory:
        facts["memory"] = dict(memory)
    for key, value in (overrides or {}).items():
        facts[str(key)] = value
    return facts


def days_between(start: datetime, end: datetime) -> float:
    """Whole-ish days between two instants — the unit the path reports its clock in."""
    return (end - start) / timedelta(days=1)
