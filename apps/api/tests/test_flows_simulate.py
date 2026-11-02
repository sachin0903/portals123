"""The flow dry run — every node kind, every branch outcome, and the guarantee that it performs
nothing.

All pure: ``simulate`` takes a validated definition, a fact context and an ``ApplicationGraph``,
and returns a description. No database, no session, no clock unless the test pins one — which is
the point of the module and also what makes it exhaustively testable.

The first test in the file is the one that matters most. Side-effect freedom is claimed
structurally (``app/flows/simulate.py`` imports nothing that can write), and a claim about an
import graph is worthless unless something checks the import graph.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.flows import onboarding_seed, onboarding_terms
from app.flows import simulate as simulate_mod
from app.flows.definitions import validate_definition
from app.flows.planning import AccountSpec, ApplicationGraph, Participant
from app.flows.simulate import simulate, synthetic_facts, synthetic_graph

NOW = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------------------------
# The no-side-effects guarantee
# ---------------------------------------------------------------------------------------------

#: Every module that can write a case, arm a timer, enqueue an outbox row, send an email or call a
#: provider. The simulator importing any of them would put a live code path between a dry run and a
#: real effect, and no amount of care at the call sites substitutes for the import simply not
#: existing. (``timers`` is absent from this list on purpose: the simulator imports exactly one
#: name from it, ``resolve_fire_at``, which is pure and total — the assertion below pins that it is
#: the only one.)
_FORBIDDEN_MODULES = {
    "app.flows.orchestrator",
    "app.flows.engine",
    "app.flows.step_handlers",
    "app.flows.effects",
    "app.flows.dispatcher",
    "app.flows.automation",
    "app.flows.memory",
    "app.flows.notifications",
    "app.flows.providers",
    "app.complycube.provider",
    "app.documents.service",
    "app.tasks.service",
    "app.email",
    "app.tags",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_the_simulator_imports_no_executing_module() -> None:
    path = Path(simulate_mod.__file__)
    offenders = sorted(_imports(path) & _FORBIDDEN_MODULES)
    assert not offenders, (
        "app/flows/simulate.py imports modules that can perform real work: "
        f"{offenders}. A dry run's side-effect freedom is STRUCTURAL — there must be no code path "
        "from the simulator to an executor. Describe the action from its authored config instead."
    )


def test_the_simulator_takes_only_the_pure_half_of_timers() -> None:
    """``timers`` writes rows; ``timers.resolve_fire_at`` is pure and total. Importing the one
    function keeps the wait's resume time identical to the engine's without importing the module's
    writing half into the dry run's reach."""
    source = Path(simulate_mod.__file__).read_text(encoding="utf-8")
    assert "from app.flows.timers import resolve_fire_at" in source
    assert "from app.flows import timers" not in source


def test_the_simulator_issues_no_sql() -> None:
    """A belt for the same claim from the other direction: the module contains no statement text
    and no session handling at all, so it cannot read or write the database even by accident."""
    source = Path(simulate_mod.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("session.execute", "sqlalchemy", "insert into", "update ", "delete from"):
        assert forbidden not in source, f"simulate.py mentions {forbidden!r}"


# ---------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------


def _step(node_id: str, **kw: Any) -> dict[str, Any]:
    """A participant-scoped staff `task` node — the least machinery a graph test needs."""
    return {
        "id": node_id,
        "kind": "step",
        "type": "task",
        "title": node_id,
        "scope": "participant",
        "assignee": "staff",
        "config": {},
        **kw,
    }


def _definition(nodes: list[dict[str, Any]], entry: str | None = None) -> dict[str, Any]:
    return validate_definition({"nodes": nodes, "entry": entry or nodes[0]["id"]})


def _person(**kw: Any) -> ApplicationGraph:
    return ApplicationGraph(
        participants=(
            Participant(
                contact_id=1, type=kw.pop("type", "individual"), roles=frozenset({"owner"})
            ),
        ),
        accounts=kw.pop("accounts", ()),
        custody=kw.pop("custody", None),
    )


def _run(definition: dict[str, Any], facts: dict[str, Any], graph: ApplicationGraph | None = None):
    return simulate(definition, facts=facts, app_graph=graph or _person(), now=NOW)


def _path(result: Any) -> list[str]:
    return [hop.node_id for hop in result.path]


# ---------------------------------------------------------------------------------------------
# The linear kinds
# ---------------------------------------------------------------------------------------------


def test_a_straight_line_is_walked_in_order_and_completes() -> None:
    definition = _definition([_step("a", next="b"), _step("b", next="c"), _step("c")])
    result = _run(definition, {})
    assert _path(result) == ["a", "b", "c"]
    assert result.outcome["kind"] == "completed"
    assert result.shape == "graph"


def test_a_step_the_journey_waits_at_is_marked_blocking() -> None:
    """The simulation walks past a step the engine would stop at (a dry run answers 'what would
    happen', not 'where is it now'), so it has to SAY where the engine would stop."""
    definition = _definition([_step("a", next="b"), _step("b")])
    result = _run(definition, {})
    assert [hop.blocking for hop in result.path] == [True, True]


def test_an_action_node_is_described_and_never_blocks() -> None:
    definition = _definition(
        [
            {
                "id": "tag",
                "kind": "action",
                "type": "apply_tag",
                "title": "Tag them",
                "scope": "application",
                "config": {"tag": "onboarding", "target": "contact"},
                "next": "done",
            },
            _step("done"),
        ]
    )
    result = _run(definition, {})
    action = result.path[0]
    assert action.kind == "action"
    # `handlers.action_step_type` prefixes the derived projection's type; the authored node keeps
    # the plain one, which is what the surface renders against the contract's actionTypes.
    assert action.type == "apply_tag"
    assert action.blocking is False
    assert action.config["tag"] == "onboarding"


def test_an_exit_node_latches_its_authored_outcome() -> None:
    definition = _definition(
        [
            _step("a", next="stop"),
            {
                "id": "stop",
                "kind": "exit",
                "title": "Not eligible",
                "config": {"outcome": "cancelled", "reason": "Below the minimum"},
            },
        ]
    )
    result = _run(definition, {})
    assert result.outcome["kind"] == "exit"
    assert result.outcome["outcome"] == "cancelled"
    assert result.outcome["reason"] == "Below the minimum"


def test_start_flow_ends_the_walk_as_a_tail_call() -> None:
    definition = _definition(
        [
            _step("a", next="hand"),
            {
                "id": "hand",
                "kind": "start_flow",
                "title": "Into the review",
                "config": {"flow_code": "annual-review"},
            },
        ]
    )
    result = _run(definition, {})
    assert result.outcome["kind"] == "handed_off"
    assert result.outcome["flow_code"] == "annual-review"


def test_a_non_terminal_node_with_no_successor_completes_rather_than_hanging() -> None:
    definition = _definition([_step("a")])
    result = _run(definition, {})
    assert result.outcome["kind"] == "completed"


def test_a_fan_out_reports_every_arm_rather_than_picking_one() -> None:
    """The dry run walks a frontier like the engine does. A cursor would have reported one arm and
    listed the other as excluded — the report saying the journey does something it does not."""
    definition = _definition(
        [
            {"id": "start", "kind": "start", "next": ["forms", "terms"]},
            _step("forms", next="review"),
            _step("terms", next="review"),
            _step("review"),
        ]
    )
    result = _run(definition, {})
    # The start node itself is not a hop — it is where the journey begins, not something it does.
    assert _path(result) == ["forms", "terms", "review"]
    assert result.excluded == ()
    assert result.outcome["kind"] == "completed"


def test_a_re_convergence_is_not_reported_as_a_loop() -> None:
    """Two arms meeting again is the graph working. Only a node that reaches ITSELF is a loop, and
    conflating the two would make every well-formed fan-out look broken in the dry run."""
    definition = _definition(
        [
            {"id": "start", "kind": "start", "next": ["a", "b"]},
            _step("a", next="join"),
            _step("b", next="join"),
            _step("join"),
        ]
    )
    result = _run(definition, {})
    assert result.outcome["kind"] == "completed"
    assert _path(result).count("join") == 1


def test_a_wait_delays_only_the_arm_behind_it() -> None:
    """Each frontier entry carries its own clock: a seven-day pause on one arm must not report the
    other arm's work as happening a week late."""
    definition = _definition(
        [
            {"id": "start", "kind": "start", "next": ["pause", "sibling"]},
            {
                "id": "pause",
                "kind": "wait",
                "title": "Cooling off",
                "config": {"duration_days": 7},
                "next": "after",
            },
            _step("after"),
            _step("sibling"),
        ]
    )
    at_day = {hop.node_id: hop.at_day for hop in _run(definition, {}).path}
    assert at_day["sibling"] == 0
    assert at_day["after"] == 7


def test_a_cycle_is_reported_rather_than_spun() -> None:
    """``validate_definition`` refuses a cycle outright, so the endpoint 422s before the simulator
    is reached. This builds one by hand anyway: the walk runs over STORED definitions too, and one
    saved before a rule was added must be described rather than spun on."""
    definition = {
        "version": 3,
        "entry": "a",
        "nodes": [_step("a", next="b"), _step("b", next="a")],
        "steps": [{**_step("a"), "depends_on": []}, {**_step("b"), "depends_on": []}],
        "milestones": [],
    }
    result = _run(definition, {})
    assert result.outcome["kind"] == "looped"
    assert result.outcome["node_id"] == "a"


def test_an_edge_into_a_missing_node_stops_and_says_so() -> None:
    # Publish validation refuses this, so it is built by hand: a saved DRAFT may carry it, and the
    # dry run is what an author uses before publishing.
    definition = {
        "version": 3,
        "entry": "a",
        "nodes": [_step("a", next="ghost")],
        "steps": [{**_step("a"), "depends_on": []}],
        "milestones": [],
    }
    result = _run(definition, {})
    assert result.outcome["kind"] == "dangling"
    assert result.outcome["node_id"] == "ghost"


# ---------------------------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------------------------


def _branch_flow() -> dict[str, Any]:
    """intake → risk? ─┬─ offshore ─→ edd ─→ notify
    ····················└─ else ──────────→ notify   (the arms re-converge on purpose)"""
    return _definition(
        [
            _step("intake", next="risk"),
            {
                "id": "risk",
                "kind": "branch",
                "title": "Offshore?",
                "config": {
                    "branches": [
                        {
                            "id": "b1",
                            "label": "Offshore",
                            "conditions": {
                                "field": "application.custody",
                                "operator": "equals",
                                "value": "offshore",
                            },
                            "next": "edd",
                        },
                        {"id": "b2", "label": "Otherwise", "conditions": None, "next": "notify"},
                    ]
                },
            },
            _step("edd", next="notify"),
            _step("notify"),
        ]
    )


def test_a_branch_reports_which_arm_and_why_with_the_value_it_read() -> None:
    result = _run(_branch_flow(), {"application.custody": "offshore"})
    assert _path(result) == ["intake", "risk", "edd", "notify"]
    branch = result.path[1]
    assert branch.taken_arm == "b1"
    passing = branch.arms[0]
    assert passing["passed"] is True
    assert passing["explanation"]["actual"] == "offshore"
    assert passing["explanation"]["field"] == "application.custody"


def test_the_else_arm_is_taken_when_nothing_matches_and_carries_no_explanation() -> None:
    result = _run(_branch_flow(), {"application.custody": "onshore"})
    assert _path(result) == ["intake", "risk", "notify"]
    branch = result.path[1]
    assert branch.taken_arm == "b2"
    assert branch.arms[0]["passed"] is False
    assert branch.arms[0]["explanation"]["actual"] == "onshore"
    assert branch.arms[1]["is_else"] is True
    assert branch.arms[1]["explanation"] is None


def test_a_missing_field_takes_the_else_rather_than_matching_everything() -> None:
    """The evaluator's rule (an unknown field resolves to None and is then tested normally) has to
    survive into the dry run, or an author would be told their branch fires when it will not."""
    result = _run(_branch_flow(), {})
    assert result.path[1].taken_arm == "b2"
    assert result.path[1].arms[0]["explanation"]["actual"] is None


def test_an_arm_after_the_taken_one_is_reported_as_never_evaluated() -> None:
    """First-satisfiable-wins means a later arm never ran. Showing it a verdict would invite an
    author to 'fix' a condition that was never consulted."""
    definition = _definition(
        [
            {
                "id": "risk",
                "kind": "branch",
                "title": "Which",
                "config": {
                    "branches": [
                        {
                            "id": "b1",
                            "label": "A",
                            "conditions": {
                                "field": "contact.type",
                                "operator": "equals",
                                "value": "individual",
                            },
                            "next": "a",
                        },
                        {
                            "id": "b2",
                            "label": "B",
                            "conditions": {"field": "contact.type", "operator": "isNotEmpty"},
                            "next": "b",
                        },
                        {"id": "b3", "label": "Else", "conditions": None, "next": "b"},
                    ]
                },
            },
            _step("a"),
            _step("b"),
        ],
        entry="risk",
    )
    result = _run(definition, {"contact.type": "individual"})
    arms = result.path[0].arms
    assert arms[0]["taken"] is True
    assert arms[1]["reached"] is False and arms[1]["passed"] is None


def test_the_untaken_arm_is_excluded_with_the_branch_that_diverted_it() -> None:
    result = _run(_branch_flow(), {"application.custody": "onshore"})
    excluded = {e.node_id: e for e in result.excluded}
    assert excluded["edd"].reason == "branch_not_taken"
    assert excluded["edd"].because_of == "risk"
    # A re-convergent node is NOT excluded just because one way into it was not taken.
    assert "notify" not in excluded


def test_a_branch_reports_the_steps_its_untaken_arms_elide() -> None:
    result = _run(_branch_flow(), {"application.custody": "onshore"})
    assert result.path[1].elided == ("edd",)


def test_a_branch_with_no_takeable_arm_stops_the_walk() -> None:
    definition = {
        "version": 3,
        "entry": "risk",
        "nodes": [
            {
                "id": "risk",
                "kind": "branch",
                "title": "Which",
                "config": {
                    "branches": [
                        {
                            "id": "b1",
                            "label": "A",
                            "conditions": {
                                "field": "contact.type",
                                "operator": "equals",
                                "value": "nobody",
                            },
                            "next": "a",
                        }
                    ]
                },
            },
            _step("a"),
        ],
        "steps": [{**_step("a"), "depends_on": []}],
        "milestones": [],
    }
    result = _run(definition, {"contact.type": "individual"})
    assert result.outcome["kind"] == "completed"
    assert result.path[0].taken_arm is None
    assert result.path[0].note is not None


# ---------------------------------------------------------------------------------------------
# Waits
# ---------------------------------------------------------------------------------------------


def test_a_wait_reports_when_the_case_resumes_and_the_clock_carries_forward() -> None:
    """Two waits in a row must accumulate: a journey with a seven-day chase and then a thirty-day
    review resumes the second at day 37, not day 30."""
    definition = _definition(
        [
            {
                "id": "w1",
                "kind": "wait",
                "title": "Chase",
                "config": {"duration_days": 7},
                "next": "w2",
            },
            {
                "id": "w2",
                "kind": "wait",
                "title": "Review",
                "config": {"duration_days": 30},
                "next": "done",
            },
            _step("done"),
        ],
        entry="w1",
    )
    result = _run(definition, {})
    assert result.path[0].resumes_at == datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    assert result.path[1].resumes_at == datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    assert result.path[2].at_day == pytest.approx(37.0)
    assert result.outcome["elapsed_days"] == pytest.approx(37.0)


def test_a_wait_with_no_resolvable_deadline_does_not_wait_and_says_so() -> None:
    """``resolve_fire_at``'s rule — an unpopulated date field means 'do not wait at all', never
    'wait for ever'. Surfacing it here is the only way an author learns it before a client does."""
    definition = _definition(
        [
            {
                "id": "w",
                "kind": "wait",
                "title": "Until review",
                "config": {"until_date_field": "memory.review_date"},
                "next": "done",
            },
            _step("done"),
        ],
        entry="w",
    )
    result = _run(definition, {})
    assert result.path[0].resumes_at is None
    assert result.path[0].blocking is False
    assert "does not wait" in (result.path[0].note or "")
    assert result.outcome["elapsed_days"] == 0.0


def test_a_wait_reads_a_date_out_of_case_memory() -> None:
    definition = _definition(
        [
            {
                "id": "w",
                "kind": "wait",
                "title": "Until review",
                "config": {"until_date_field": "memory.review_date"},
                "next": "done",
            },
            _step("done"),
        ],
        entry="w",
    )
    result = _run(definition, {"memory": {"review_date": "2026-08-20T09:00:00Z"}})
    assert result.path[0].resumes_at == datetime(2026, 8, 20, 9, 0, tzinfo=UTC)


def test_wait_for_event_follows_the_event_and_reports_the_timeout_as_an_alternative() -> None:
    definition = _definition(
        [
            {
                "id": "listen",
                "kind": "wait_for_event",
                "title": "Signed?",
                "config": {
                    "trigger": "client.document_uploaded",
                    "timeout_days": 7,
                    "timeout_branch": "chase",
                },
                "next": "done",
            },
            _step("chase"),
            _step("done"),
        ],
        entry="listen",
    )
    result = _run(definition, {})
    assert _path(result) == ["listen", "done"]
    assert result.path[0].listens_for == "client.document_uploaded"
    assert result.path[0].timeout_branch == "chase"
    excluded = {e.node_id: e for e in result.excluded}
    assert excluded["chase"].reason == "timeout_only"
    assert excluded["chase"].because_of == "listen"


def test_a_listener_with_no_deadline_is_flagged() -> None:
    definition = _definition(
        [
            {
                "id": "listen",
                "kind": "wait_for_event",
                "title": "Signed?",
                "config": {"trigger": "client.document_uploaded"},
                "next": "done",
            },
            _step("done"),
        ],
        entry="listen",
    )
    result = _run(definition, {})
    assert "indefinitely" in (result.path[0].note or "")


# ---------------------------------------------------------------------------------------------
# The plan: tiers, fan-out and visibility
# ---------------------------------------------------------------------------------------------


def test_a_step_reports_the_tier_and_the_instances_the_plan_would_create() -> None:
    definition = _definition(
        [
            {**_step("terms"), "scope": "account", "per_participant": True, "next": "done"},
            _step("done"),
        ],
        entry="terms",
    )
    graph = ApplicationGraph(
        participants=(
            Participant(contact_id=1, type="individual", roles=frozenset({"owner"})),
            Participant(contact_id=2, type="individual", roles=frozenset({"owner"})),
        ),
        accounts=(AccountSpec(account_id=10, participant_contact_ids=(1, 2), product="ISA"),),
    )
    result = _run(definition, {}, graph)
    terms = result.path[0]
    assert len(terms.instances) == 2  # one per owner of the account
    assert {i.case_kind for i in terms.instances} == {"account"}
    assert result.tiers["participants"] == 2
    assert result.tiers["account_cases"] == 1


def test_a_step_conditioned_out_everywhere_is_walked_past_not_blocked() -> None:
    """The engine walks past a step planned nowhere rather than wedging on it
    (``orchestrator._enter_node``); so does the dry run, and it says which condition did it."""
    definition = _definition(
        [
            {
                **_step("offshore-form"),
                "visibility": {
                    "field": "application.custody",
                    "operator": "equals",
                    "value": "offshore",
                },
                "next": "done",
            },
            _step("done"),
        ],
        entry="offshore-form",
    )
    result = _run(definition, {}, _person(custody="onshore"))
    skipped = result.path[0]
    assert skipped.instances == ()
    assert skipped.blocking is False
    assert "walks straight past" in (skipped.note or "")
    assert skipped.visibility is not None and skipped.visibility["passed"] is False
    assert skipped.visibility["actual"] == "onshore"
    assert _path(result) == ["offshore-form", "done"]


def test_a_kyc_step_is_not_planned_for_a_child_or_an_organisation() -> None:
    """KYC relevance is the planner's rule (legacy ``isIndividualForClientGates``) and the dry run
    inherits it rather than re-deciding: an organisation is gathered but never identity-checked.

    Wired to a second node deliberately, so the report under test is the WALK. A single unwired node
    is a tier-driven graph (``graph.is_tier_driven``) and reports the plan instead, where an
    unplanned step is an ``excluded`` entry rather than a hop with no instances — a different, and
    for that shape more honest, answer.
    """
    definition = _definition(
        [{**_step("aml"), "type": "aml_check", "config": {}, "next": "after"}, _step("after")],
        entry="aml",
    )
    org = ApplicationGraph(
        participants=(Participant(contact_id=1, type="organisation", roles=frozenset({"owner"})),),
        accounts=(),
    )
    assert _run(definition, {}, org).path[0].instances == ()
    assert _run(definition, {}, _person()).path[0].instances != ()


def test_a_subject_that_matches_nothing_still_returns_a_readable_answer() -> None:
    """Every step conditioned out, no branch taken anywhere: an empty journey is a legitimate
    result and must read as one rather than as a failure."""
    definition = _definition(
        [
            {
                **_step("a"),
                "visibility": {"field": "contact.type", "operator": "equals", "value": "trust"},
                "next": "b",
            },
            {
                **_step("b"),
                "visibility": {"field": "contact.type", "operator": "equals", "value": "trust"},
            },
        ]
    )
    result = _run(definition, {"contact.type": "individual"})
    assert all(hop.instances == () for hop in result.path)
    assert result.outcome["kind"] == "completed"
    assert result.tiers["step_instances"] == 0


def test_an_orphan_node_is_named_apart_from_an_untaken_arm() -> None:
    # `a → b` makes this a graph with a path, which is the only shape where "orphan" is a meaningful
    # verdict: in a tier-driven graph nothing is an orphan, because a work node is released by its
    # tier and needs no incoming wire (`graph_issues` has always said so).
    definition = {
        "version": 3,
        "entry": "a",
        "nodes": [{**_step("a"), "next": "b"}, _step("b"), _step("orphan")],
        "steps": [
            {**_step("a"), "depends_on": []},
            {**_step("b"), "depends_on": ["a"]},
            {**_step("orphan"), "depends_on": []},
        ],
        "milestones": [],
    }
    result = _run(definition, {})
    excluded = {e.node_id: e for e in result.excluded}
    assert excluded["orphan"].reason == "orphan"
    assert excluded["orphan"].because_of is None


# ---------------------------------------------------------------------------------------------
# Legacy definitions
# ---------------------------------------------------------------------------------------------


def test_a_legacy_definition_reports_the_plan_and_makes_no_ordering_claim() -> None:
    """A legacy ``{steps}`` definition's edges are presentation order; its execution order is
    ``depends_on``. ``next_node`` refuses to decide for one, and so does the dry run — it reports
    which steps apply and explicitly says there is no path."""
    definition = validate_definition(
        {
            "steps": [
                {
                    "id": "a",
                    "type": "task",
                    "title": "A",
                    "scope": "participant",
                    "assignee": "staff",
                    "config": {},
                },
                {
                    "id": "b",
                    "type": "task",
                    "title": "B",
                    "scope": "participant",
                    "assignee": "staff",
                    "config": {},
                    "visibility": {"field": "contact.type", "operator": "equals", "value": "trust"},
                },
            ],
            "milestones": [],
        }
    )
    result = simulate(
        definition, facts={"contact.type": "individual"}, app_graph=_person(), now=NOW
    )
    assert result.shape == "legacy"
    assert _path(result) == ["a"]
    assert [e.node_id for e in result.excluded] == ["b"]
    assert result.excluded[0].reason == "not_planned"
    assert "prerequisites settle" in result.outcome["detail"]


def test_the_seeded_graph_is_walked_end_to_end_for_a_real_subject() -> None:
    """The dry run of the journey every firm starts from — a WALK, because the seed is wired.

    Every node in the offshore ISA/SIPP subject's fan-out appears in the walk. A plan-time-
    inapplicable node remains visible with no instances so an author can see their whole graph;
    runtime branches still report the diverted arm. The plan-shaped report is for a legacy
    definition, which has no path to walk.
    """
    forms = {code: f"frm_{code}" for code in onboarding_seed.FORMS}
    terms = {code: f"doc_{code}" for code in onboarding_terms.TERMS}
    app_graph = synthetic_graph(custody="offshore", products=["ISA", "SIPP"], has_transfer=True)
    facts = synthetic_facts(app_graph, now=NOW)

    shipped = simulate(
        validate_definition(onboarding_seed._build_definition(forms, terms)),
        facts=facts,
        app_graph=app_graph,
        now=NOW,
    )
    assert shipped.shape == "graph"
    walked = _path(shipped)
    assert walked[:8] == [
        "core",
        "jisa_child",
        "jisa_registered_contact",
        "org",
        "additional_info",
        "pension",
        "employer",
        "account_preferences",
    ]
    assert walked[-1] == "setup"
    # An offshore individual with a SIPP and a transfer gets all of it. The organisation form is
    # still drawn, but the real planner gives it no instances for this subject.
    for reached in (
        "additional_info",
        "pension",
        "employer",
        "account_preferences",
        "terms_isa",
        "setup",
    ):
        assert reached in walked, reached
    assert next(hop for hop in shipped.path if hop.node_id == "org").instances == ()

    legacy = simulate(
        validate_definition(onboarding_seed._legacy_definition(forms, terms)),
        facts=facts,
        app_graph=app_graph,
        now=NOW,
    )
    assert legacy.shape == "legacy"
    # The same WORK, whatever the order: a legacy definition reports the plan and the spine reports
    # the walk, so the comparison is which steps this subject gets, not the sequence.
    assert {nid for nid in walked if nid in {s.node_id for s in legacy.path}} == {
        s.node_id for s in legacy.path
    }


# ---------------------------------------------------------------------------------------------
# Synthetic subjects
# ---------------------------------------------------------------------------------------------


def test_a_synthetic_subject_drives_the_same_branch_the_real_one_would() -> None:
    graph = synthetic_graph(custody="offshore", products=["SIPP"])
    facts = synthetic_facts(graph, now=NOW)
    result = simulate(_branch_flow(), facts=facts, app_graph=graph, now=NOW)
    assert _path(result) == ["intake", "risk", "edd", "notify"]
    assert facts["application.has_sipp_product"] is True
    assert facts["application.has_pension_product"] is True


def test_a_synthetic_subject_leaves_unset_fields_absent_rather_than_null_filled() -> None:
    """An absent field resolves to None and only isEmpty/notEquals can match it — which is the
    truth. Null-filling would make an author's untested field look tested."""
    facts = synthetic_facts(synthetic_graph(), now=NOW)
    assert "contact.nationality" not in facts
    assert "contact.service_type" not in facts


def test_synthetic_memory_lands_nested_so_one_object_serves_both_resolvers() -> None:
    facts = synthetic_facts(synthetic_graph(), memory={"risk_band": "high"}, now=NOW)
    assert facts["memory"] == {"risk_band": "high"}


# ---------------------------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------------------------


def test_the_result_serialises_with_performed_false() -> None:
    payload = _run(_branch_flow(), {"application.custody": "offshore"}).as_dict()
    assert payload["performed"] is False
    assert payload["shape"] == "graph"
    assert [h["node_id"] for h in payload["path"]] == ["intake", "risk", "edd", "notify"]
    assert payload["outcome"]["kind"] == "completed"
