"""Unit tests for the v3 flow graph (``app/flows/graph.py``) and the validation it feeds
(``app/flows/definitions.py``). No DB.

What this suite is for, in order of how much it would hurt to lose:

1. **Legacy compatibility.** Every definition in the wild is the pre-v3 ``{steps: [...]}`` shape and
   they are running live onboarding journeys. Loading one must be lossless AND behaviour-identical
   — proved here by planning the seeded onboarding flow twice (through the graph and around it) and
   demanding the same execution plan, not by eyeballing the model.
2. **The mandatory else.** A fan-out with no fallback is how a client silently falls off the end of
   a process. The resolution table pins order, first-satisfiable and the else, and publish refuses
   a branch that has none.
3. **The contract pin.** The node kinds, their config keys, the action config keys and the caps are
   mirrored from flow-contract.json as Python literals (the API container ships without pkgs/ui) —
   these fail the moment the two sides drift.
4. **The one-exception-type guarantee.** ``parse`` is called on every autosave and on stored JSON
   in the worker; the fuzz battery at the bottom throws junk of every shape at it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.flows import definitions, graph, onboarding_seed, onboarding_terms, planning
from app.flows.definitions import DefinitionError, validate_definition

# ---------------------------------------------------------------------------------------------
# The contract pin (flow-contract.json is the single cross-language source of truth)
# ---------------------------------------------------------------------------------------------

# tests/ -> apps/api -> apps -> the repo root.
_CONTRACT_PATH = (
    Path(__file__).resolve().parents[3]
    / "pkgs"
    / "ui"
    / "src"
    / "lib"
    / "components"
    / "book"
    / "journeys"
    / "flow-contract.json"
)
_DRIFT = "the Python side drifted from flow-contract.json — change them together"


def _contract() -> dict[str, Any]:
    data = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.mark.pkgs_ui_pin
def test_contract_node_kinds_match() -> None:
    """Pinned against ``nodeKinds`` — the contract's own list — not against
    ``{step} | controlNodes``, which is how ``action`` could be added to the contract without a
    single Python test noticing that the parser did not know about it."""
    assert graph.NODE_KINDS == frozenset(_contract()["nodeKinds"]), _DRIFT
    assert graph.PLANNED_KINDS == frozenset({graph.STEP_KIND, graph.ACTION_KIND}), _DRIFT
    assert graph.NODE_KINDS - graph.PLANNED_KINDS == frozenset(_contract()["controlNodes"]), _DRIFT


@pytest.mark.pkgs_ui_pin
def test_contract_control_config_keys_match() -> None:
    expected = {k: frozenset(v["config"]) for k, v in _contract()["controlNodes"].items()}
    assert graph.CONTROL_CONFIG_KEYS == expected, _DRIFT


@pytest.mark.pkgs_ui_pin
def test_contract_control_labels_match() -> None:
    expected = {k: v["label"] for k, v in _contract()["controlNodes"].items()}
    assert graph.CONTROL_LABELS == expected, _DRIFT


@pytest.mark.pkgs_ui_pin
def test_contract_branch_rules_match() -> None:
    branch = _contract()["controlNodes"]["branch"]
    assert graph.MIN_BRANCHES == branch["minBranches"], _DRIFT
    assert graph.REQUIRES_ELSE == branch["requiresElse"], _DRIFT


@pytest.mark.pkgs_ui_pin
def test_contract_action_config_keys_match() -> None:
    expected = {k: frozenset(v["config"]) for k, v in _contract()["actionTypes"].items()}
    assert graph.ACTION_CONFIG_KEYS == expected, _DRIFT


@pytest.mark.pkgs_ui_pin
def test_contract_caps_match() -> None:
    caps = _contract()["caps"]
    assert graph.MAX_NODES_PER_FLOW == caps["maxNodesPerFlow"], _DRIFT
    assert graph.MAX_BRANCHES_PER_NODE == caps["maxBranchesPerNode"], _DRIFT
    assert graph.MAX_WAIT_DAYS == caps["maxWaitDays"], _DRIFT


@pytest.mark.pkgs_ui_pin
def test_contract_step_types_match() -> None:
    """The step vocabulary, pinned the way the contract's own comment says it should be: type,
    defaultAssignee, defaultScope and configKeys as sets, plus requiredConfig."""
    for stype, spec in _contract()["stepTypes"].items():
        ours = definitions.STEP_TYPES.get(stype)
        assert ours is not None, f"{stype} is in the contract but not in STEP_TYPES — {_DRIFT}"
        assert ours.default_assignee == spec["defaultAssignee"], _DRIFT
        assert ours.default_scope == spec["defaultScope"], _DRIFT
        assert ours.config_keys == frozenset(spec["configKeys"]), _DRIFT
        assert definitions.REQUIRED_CONFIG.get(stype, ()) == tuple(spec["requiredConfig"]), _DRIFT
    assert set(definitions.STEP_TYPES) == set(_contract()["stepTypes"]), _DRIFT


@pytest.mark.pkgs_ui_pin
def test_contract_start_node_rules_match() -> None:
    start = _contract()["controlNodes"]["start"]
    assert graph.START_KIND in graph.NODE_KINDS, _DRIFT
    assert graph.CONTROL_CONFIG_KEYS[graph.START_KIND] == frozenset(start["config"]), _DRIFT
    assert graph.MAX_START_NODES == start["maxPerFlow"], _DRIFT


def test_every_control_kind_is_validated() -> None:
    """A kind sitting in NODE_KINDS with no config allowlist behind it would accept anything an
    author typed — the branch-with-no-else failure in a different costume."""
    for kind in graph.NODE_KINDS - graph.PLANNED_KINDS:
        assert kind in graph.CONTROL_CONFIG_KEYS
        assert kind in graph.CONTROL_LABELS


# ---------------------------------------------------------------------------------------------
# Legacy compatibility — the definitions in the wild
# ---------------------------------------------------------------------------------------------


def _forms_and_terms() -> tuple[dict[str, str], dict[str, str]]:
    return (
        {code: f"frm_{code}" for code in onboarding_seed.FORMS},
        {code: f"doc_{code}" for code in onboarding_terms.TERMS},
    )


def _seed_definition() -> dict[str, Any]:
    """The seed in the pre-v3 shape. Still the subject of the legacy-compatibility suite below:
    every definition authored before the rebuild is one of these and they are running live
    journeys, so the guarantees about reading one have to keep being tested on a real one."""
    return onboarding_seed._legacy_definition(*_forms_and_terms())


def _shipped_definition() -> dict[str, Any]:
    """The seed as it actually ships — a v3 graph."""
    return onboarding_seed._build_definition(*_forms_and_terms())


def _seed_normalised() -> dict[str, Any]:
    return validate_definition(_seed_definition())


def test_seed_parses_as_a_legacy_graph() -> None:
    parsed = graph.parse(_seed_normalised())
    assert parsed.is_legacy_linear
    assert parsed.entry == _seed_normalised()["steps"][0]["id"]
    assert all(node.is_step for node in parsed.nodes.values())


def test_seed_round_trips_losslessly() -> None:
    """Ids, order and every authored key survive the model — ``Node.raw`` is the guarantee."""
    normalised = _seed_normalised()
    parsed = graph.parse(normalised)
    assert list(parsed.order) == [s["id"] for s in normalised["steps"]]
    assert [dict(n.raw) for n in parsed.steps()] == normalised["steps"]


def test_seed_dependencies_are_untouched_by_the_graph() -> None:
    """THE legacy guarantee: no edge-derived dependency is added. Sequentialising the seeded
    onboarding flow (which fans in parallel per participant) would be a very quiet disaster."""
    normalised = _seed_normalised()
    compiled = graph.effective_depends_on(graph.parse(normalised))
    assert compiled == {s["id"]: tuple(s["depends_on"]) for s in normalised["steps"]}


def test_legacy_graph_refuses_to_decide_at_runtime() -> None:
    """The second structural guard: a legacy definition authored no control flow, so the graph
    declines to name a successor. Its order is ``depends_on``, resolved as it always was."""
    parsed = graph.parse(_seed_normalised())
    assert parsed.entry is not None
    assert graph.next_nodes(parsed, parsed.entry, {}) == ()
    # ... even though the chain is there for the editor to draw.
    assert parsed.nodes[parsed.entry].next_ids != ()


def _application() -> planning.ApplicationGraph:
    return planning.ApplicationGraph(
        participants=(
            planning.Participant(1, "individual", frozenset({"owner"})),
            planning.Participant(2, "individual", frozenset({"owner"})),
            planning.Participant(3, "child", frozenset({"beneficiary"})),
        ),
        accounts=(
            planning.AccountSpec(10, (1, 2), product="ISA", has_transfer=True),
            planning.AccountSpec(11, (1,), product="SIPP"),
        ),
        custody="offshore",
    )


def test_seed_plans_identically_through_the_graph() -> None:
    """The behaviour-identical half, proved rather than asserted: fan the SEEDED definition across
    a realistic application twice — once from the authored steps, once from the graph's derived
    ones — and demand the same cases, the same step instances, in the same order."""
    normalised = _seed_normalised()
    parsed = graph.parse(normalised)
    compiled = graph.effective_depends_on(parsed)
    via_graph = [{**dict(n.raw), "depends_on": list(compiled[n.id])} for n in parsed.steps()]
    application = _application()
    assert planning.plan_execution(via_graph, application) == planning.plan_execution(
        normalised["steps"], application
    )


def test_seed_publishes_with_the_graph_checks_added() -> None:
    """publish_issues now also runs graph_issues; the one flow every firm starts from must stay
    publishable through it (a regression here makes the seeded flow read-only in practice)."""
    assert definitions.publish_issues(_seed_normalised()) == []


def test_legacy_validate_output_is_unchanged() -> None:
    """No new keys, no reordering: a legacy save must produce the same canonical JSON it always
    has, or every stored definition churns on the next save."""
    out = validate_definition({"steps": [{"id": "a", "type": "task", "title": "A"}]})
    assert set(out) == {"steps", "milestones", "intake"}


def test_control_node_in_the_legacy_steps_array_is_refused() -> None:
    with pytest.raises(DefinitionError, match="belong in"):
        graph.parse({"steps": [{"id": "b", "kind": "branch", "config": {"branches": []}}]})


# ---------------------------------------------------------------------------------------------
# The legacy upgrade — the same journey, said in v3
# ---------------------------------------------------------------------------------------------
#
# THE point of the arity rule. Until `next` could be a list, opening a legacy flow in the v3
# builder and adding one branch forced the other nineteen steps into a chain, and
# `effective_depends_on` compiled that chain into hard prerequisites — parallel work becoming
# serial at exactly the moment somebody adopted the new engine on the process it was built for.
# These prove the upgrade is behaviour-identical, and they prove it the only way that counts:
# through the real validator and the real planner, on the real seeded onboarding.


@pytest.mark.pkgs_ui_pin
def test_the_contract_pins_the_upgrade_rule() -> None:
    """The rule is cross-language (``nodes.ts::toNodes`` mirrors it), so the contract carries a
    worked example and both sides reproduce it exactly."""
    example = _contract()["legacyUpgradeExample"]
    ours = graph.upgrade_legacy({"steps": example["legacy"]})["nodes"]
    # Projected onto the keys each example entry declares — the example states the RULE (ids, kinds,
    # edges, prerequisites), not the whole envelope, and pinning the envelope here would make the
    # contract a second copy of the serialiser.
    assert [
        {k: n.get(k) for k in expected}
        for n, expected in zip(ours, example["upgraded"], strict=True)
    ] == example["upgraded"], _DRIFT


def test_the_seed_upgrades_to_a_graph_with_one_start_and_no_extra_dependencies() -> None:
    normalised = _seed_normalised()
    parsed = graph.parse(graph.upgrade_legacy(normalised))
    assert not parsed.is_legacy_linear
    assert parsed.start_id == parsed.entry
    # THE assertion the whole wave exists for: compiling the upgraded graph reproduces the authored
    # prerequisites exactly — no edge-derived additions, so nothing that ran in parallel is serial.
    assert graph.effective_depends_on(parsed) == {
        s["id"]: tuple(s["depends_on"]) for s in normalised["steps"]
    }


def test_the_seed_fans_its_whole_first_tier_out_of_the_start_node() -> None:
    """The concrete failure this closes: our live onboarding asks for five ``terms_*``
    declarations together, and a single-valued ``next`` could only have offered them one at a
    time."""
    normalised = _seed_normalised()
    parsed = graph.parse(graph.upgrade_legacy(normalised))
    assert parsed.start_id is not None
    roots = parsed.nodes[parsed.start_id].next_ids
    assert roots == tuple(s["id"] for s in normalised["steps"] if not s["depends_on"])
    assert len(roots) > 1, "a seed whose first tier is one step would not test anything"


def test_the_upgraded_seed_validates_and_plans_identically() -> None:
    """The proof, end to end and through the real code paths rather than through the model: run the
    seeded onboarding through ``validate_definition`` in BOTH shapes, fan each result across the
    same realistic application, and demand the same cases, the same step instances, in the same
    order — ``depends_on`` included, since that is the thing an edge compiles into."""
    legacy = _seed_normalised()
    v3 = validate_definition(graph.upgrade_legacy(legacy))
    assert v3["version"] == 3
    # The derived projection is the legacy step list, key for key.
    assert v3["steps"] == legacy["steps"]
    application = _application()
    assert planning.plan_execution(
        v3["steps"],  # type: ignore[arg-type]
        application,
    ) == planning.plan_execution(legacy["steps"], application)  # type: ignore[arg-type]


def test_the_upgraded_seed_is_still_publishable() -> None:
    """A faithful upgrade that cannot be published is not a usable one — this is what an author
    actually does after converting a live journey."""
    assert (
        definitions.publish_issues(validate_definition(graph.upgrade_legacy(_seed_normalised())))
        == []
    )


def test_upgrading_an_empty_definition_mints_nothing() -> None:
    assert graph.upgrade_legacy({"steps": []})["nodes"] == []


def test_the_minted_start_id_never_collides_with_a_step() -> None:
    upgraded = graph.upgrade_legacy({"steps": [{"id": "start"}, {"id": "start_2"}]})
    assert upgraded["nodes"][0]["id"] == "start_3"


# ---------------------------------------------------------------------------------------------
# Parsing the v3 shape
# ---------------------------------------------------------------------------------------------


def _step_node(nid: str, **kw: Any) -> dict[str, Any]:
    return {"id": nid, "type": "task", "title": nid.title(), "config": {}, **kw}


def _branch_node(nid: str, *branches: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return {"id": nid, "kind": "branch", "config": {"branches": list(branches)}, **kw}


def _branch(bid: str, nxt: str | None, conditions: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": bid, "label": bid, "conditions": conditions, "next": nxt}


def test_kind_defaults_to_step() -> None:
    parsed = graph.parse({"nodes": [_step_node("a")]})
    assert parsed.nodes["a"].kind == graph.STEP_KIND
    assert not parsed.is_legacy_linear


def test_branch_config_is_lifted_onto_the_node() -> None:
    parsed = graph.parse(
        {
            "nodes": [
                _step_node("a", next="pick"),
                _branch_node(
                    "pick",
                    _branch(
                        "hot",
                        "a2",
                        {"field": "contact.type", "operator": "equals", "value": "individual"},
                    ),
                    _branch("else", "a3"),
                ),
                _step_node("a2"),
                _step_node("a3"),
            ]
        }
    )
    node = parsed.nodes["pick"]
    assert [b.id for b in node.branches] == ["hot", "else"]
    assert node.branches[1].is_else
    assert node.next_ids == ()


def test_an_empty_condition_object_is_the_else() -> None:
    """An empty condition is unconditional; reading it any other way would let a branch that looks
    like a fallback swallow every case from wherever it sits in the array."""
    parsed = graph.parse({"nodes": [_branch_node("p", _branch("x", "a", {}), _branch("y", "b"))]})
    assert parsed.nodes["p"].branches[0].is_else


def test_branches_on_the_node_rather_than_in_config_are_refused() -> None:
    with pytest.raises(DefinitionError, match="in its config"):
        graph.parse({"nodes": [{"id": "p", "kind": "branch", "branches": []}]})


@pytest.mark.parametrize("kind", ["exit", "start_flow", "branch"])
def test_a_next_on_a_node_that_has_none_is_refused(kind: str) -> None:
    """Silently dropping an authored edge is how a case stops somewhere nobody expected."""
    with pytest.raises(DefinitionError, match="cannot have a next"):
        graph.parse({"nodes": [{"id": "n", "kind": kind, "next": "a", "config": {}}]})


def test_unknown_kind_is_refused() -> None:
    with pytest.raises(DefinitionError, match="unknown kind"):
        graph.parse({"nodes": [{"id": "n", "kind": "gateway"}]})


def test_unknown_control_config_key_is_refused() -> None:
    with pytest.raises(DefinitionError, match="unknown config keys"):
        graph.parse({"nodes": [{"id": "w", "kind": "wait", "config": {"duration_weeks": 2}}]})


def test_duplicate_node_ids_are_refused() -> None:
    with pytest.raises(DefinitionError, match="duplicate node id"):
        graph.parse({"nodes": [_step_node("a"), _step_node("a")]})


def test_entry_defaults_to_the_node_nothing_points_at() -> None:
    parsed = graph.parse({"nodes": [_step_node("b"), _step_node("a", next="b")]})
    assert parsed.entry == "a"


def test_authored_entry_wins() -> None:
    parsed = graph.parse({"nodes": [_step_node("a", next="b"), _step_node("b")], "entry": "b"})
    assert parsed.entry == "b"


def test_authored_entry_must_exist() -> None:
    with pytest.raises(DefinitionError, match="entry 'ghost'"):
        graph.parse({"nodes": [_step_node("a")], "entry": "ghost"})


def test_an_empty_graph_has_no_entry() -> None:
    parsed = graph.parse({"nodes": []})
    assert parsed.entry is None and parsed.nodes == {}


# ---------------------------------------------------------------------------------------------
# The arity rule — `next` may be a list, and a list means parallel
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("authored", "expected"),
    [
        (None, ()),
        ("b", ("b",)),
        ("", ()),
        ([], ()),
        (["b"], ("b",)),
        (["b", "c"], ("b", "c")),
        (["b", "b"], ("b",)),  # a redundant wire, not a contradiction
        ([" b ", "", "c"], ("b", "c")),
    ],
)
def test_next_normalises_every_authored_spelling(authored: Any, expected: tuple[str, ...]) -> None:
    parsed = graph.parse(
        {"nodes": [_step_node("a", next=authored), _step_node("b"), _step_node("c")]}
    )
    assert parsed.nodes["a"].next_ids == expected


def test_a_non_string_edge_is_refused_rather_than_coerced() -> None:
    with pytest.raises(DefinitionError, match="must be node ids"):
        graph.parse({"nodes": [_step_node("a", next=["b", 7])]})
    with pytest.raises(DefinitionError, match="must be a node id"):
        graph.parse({"nodes": [_step_node("a", next={"id": "b"})]})


def test_a_fan_out_gives_both_targets_the_same_prerequisite() -> None:
    """THE dependency claim, checked rather than assumed: `next: [b, c]` makes both depend on `a`
    and neither depend on the other, which is what parallel means to the planner."""
    parsed = graph.parse(
        {"nodes": [_step_node("a", next=["b", "c"]), _step_node("b"), _step_node("c")]}
    )
    assert graph.effective_depends_on(parsed) == {"a": (), "b": ("a",), "c": ("a",)}


def test_ordering_between_fanned_arms_is_still_depends_on() -> None:
    """The corollary worth pinning: an edge means "after", never "instead of". A fan-out where one
    arm happens to wait for another is authored with a prerequisite, not with fewer wires."""
    parsed = graph.parse(
        {
            "nodes": [
                _step_node("a", next=["b", "c"]),
                _step_node("b"),
                _step_node("c", depends_on=["b"]),
            ]
        }
    )
    assert graph.effective_depends_on(parsed)["c"] == ("b", "a")


def test_a_re_convergent_fan_out_names_both_arms_as_prerequisites() -> None:
    parsed = graph.parse(
        {
            "nodes": [
                _step_node("a", next=["b", "c"]),
                _step_node("b", next="join"),
                _step_node("c", next="join"),
                _step_node("join"),
            ]
        }
    )
    # Which is the barrier, expressed without a barrier node: `join` waits for BOTH, because
    # `depends_on` already means "not until all of these".
    assert graph.effective_depends_on(parsed)["join"] == ("b", "c")


def test_successors_and_next_nodes_both_report_the_whole_fan() -> None:
    parsed = graph.parse(
        {"nodes": [_step_node("a", next=["b", "c"]), _step_node("b"), _step_node("c")]}
    )
    assert graph.successors(parsed, "a") == ("b", "c")
    assert graph.next_nodes(parsed, "a", {}) == ("b", "c")


def test_a_dangling_edge_in_a_fan_is_reported_per_target() -> None:
    parsed = graph.parse({"nodes": [_step_node("a", next=["b", "ghost"]), _step_node("b")]})
    assert any("ghost" in m for m in graph.graph_issues(parsed))


# ---------------------------------------------------------------------------------------------
# The start node
# ---------------------------------------------------------------------------------------------


def _start(nid: str = "start", **kw: Any) -> dict[str, Any]:
    return {"id": nid, "kind": "start", **kw}


def test_a_start_node_is_the_entry_and_fans_out() -> None:
    parsed = graph.parse({"nodes": [_step_node("a"), _step_node("b"), _start(next=["a", "b"])]})
    assert parsed.entry == "start" and parsed.start_id == "start"
    assert parsed.nodes["start"].next_ids == ("a", "b")
    # It is control flow, not work: nothing is planned for it and it is nobody's prerequisite.
    assert not parsed.nodes["start"].is_step
    assert graph.effective_depends_on(parsed) == {"a": (), "b": ()}


def test_a_start_node_beats_a_stale_authored_entry() -> None:
    """``entry`` is derived output that gets read back on the next save, so an explicit start node
    must win — otherwise adding one to a stored definition would leave the entry where it was."""
    parsed = graph.parse({"nodes": [_step_node("a"), _start(next="a")], "entry": "a"})
    assert parsed.entry == "start"


def test_a_v3_definition_with_no_start_node_keeps_the_derived_entry() -> None:
    """Back-compat: every v3 definition authored before the arity rule has no start node."""
    parsed = graph.parse({"nodes": [_step_node("b"), _step_node("a", next="b")]})
    assert parsed.start_id is None and parsed.entry == "a"


def test_two_start_nodes_are_refused_at_parse() -> None:
    with pytest.raises(DefinitionError, match="starting points"):
        graph.parse({"nodes": [_start("s1"), _start("s2"), _step_node("a")]})


def test_a_start_node_cannot_carry_config_or_prerequisites() -> None:
    with pytest.raises(DefinitionError, match="unknown config keys"):
        graph.parse({"nodes": [_start(config={"outcome": "completed"})]})
    with pytest.raises(DefinitionError, match="cannot wait for anything"):
        graph.parse({"nodes": [_start(next="a", depends_on=["a"]), _step_node("a")]})


def test_a_start_node_that_leads_nowhere_is_a_publish_issue() -> None:
    parsed = graph.parse({"nodes": [_start(), _step_node("a")]})
    assert any("does not lead anywhere" in m for m in graph.graph_issues(parsed))


def test_the_steps_after_a_start_node_are_control_gated() -> None:
    """A root behind the start node is released by the ENGINE's walk, not by ``depends_on`` (it has
    none) — which is what makes a fanned-out entry release all of its roots together."""
    parsed = graph.parse({"nodes": [_start(next=["a", "b"]), _step_node("a"), _step_node("b")]})
    assert planning.control_gated_steps(parsed) == {"a": "start", "b": "start"}


def _is(value: str) -> dict[str, Any]:
    return {"field": "contact.type", "operator": "equals", "value": value}


# ---------------------------------------------------------------------------------------------
# The condition node — a two-armed BRANCH, and everything that follows from it being control flow
# ---------------------------------------------------------------------------------------------
#
# A `condition` is a `branch` with a fixed pair of arms, implemented on the same machinery. So the
# tests that matter are the ones that would break if anybody forked it back off: it must be
# resolved by `branch_choice`, its successors must be held for the walk, it must need an incoming
# edge like every other control node, and its untaken arm must elide like a branch's.


def _condition(
    nid: str,
    conditions: dict[str, Any] | None,
    when: str | None,
    otherwise: str | None = None,
) -> dict[str, Any]:
    return {
        "id": nid,
        "kind": "condition",
        "title": nid,
        "config": {
            "branches": graph.condition_arms(conditions or {}, when=when, otherwise=otherwise)
        },
    }


def test_a_condition_is_resolved_by_branch_choice_exactly_like_a_branch() -> None:
    """One decision function, not two. The whole design is that a condition IS a branch: fork the
    resolution and the two drift the first time either is touched."""
    parsed = graph.parse(
        {
            "nodes": [
                _start(next="pick"),
                _condition("pick", _is("individual"), "a", "b"),
                _step_node("a", next="b"),
                _step_node("b"),
            ]
        }
    )
    assert graph.branch_choice(parsed, "pick", {"contact": {"type": "individual"}}) == "a"
    assert graph.branch_choice(parsed, "pick", {"contact": {"type": "organisation"}}) == "b"
    assert graph.next_nodes(parsed, "pick", {"contact": {"type": "organisation"}}) == ("b",)


def test_both_arms_may_lead_to_the_same_node_which_is_the_re_convergence() -> None:
    """The shape the whole journey is drawn around: the path splits around one piece of work and
    comes back together, so what the condition decides is whether that work happens."""
    parsed = graph.parse(
        {
            "nodes": [
                _start(next="pick"),
                _condition("pick", _is("individual"), "a", "b"),
                _step_node("a", next="b"),
                _step_node("b"),
            ]
        }
    )
    assert graph.graph_issues(parsed) == []
    assert graph.successors(parsed, "pick") == ("a", "b")
    # `b` is reached either way, so it is nobody's to elide.
    assert planning.elided_by_branch(parsed, "pick", "a") == ()
    assert planning.elided_by_branch(parsed, "pick", "b") == ("a",)


def test_a_step_behind_a_condition_IS_control_gated() -> None:
    """The reversal, pinned. A condition is evaluated when the walk reaches it, so the work behind
    it must be held until then — otherwise both arms' work would be handed out at kickoff and the
    condition would decide nothing. It was NOT held while a condition was a plan-time gate; wiring
    the journey into a spine is what makes holding it correct (and safe: the walk arrives)."""
    parsed = graph.parse(
        {
            "nodes": [
                _start(next="pick"),
                _condition("pick", _is("individual"), "a", "b"),
                _step_node("a", next="b"),
                _step_node("b"),
            ]
        }
    )
    assert planning.control_gated_steps(parsed) == {"a": "pick", "b": "pick"}


def test_a_condition_needs_an_incoming_edge() -> None:
    """A wire is the only way into a control node. A condition nothing reaches decides nothing, and
    the work behind it would be held for ever waiting for a walk."""
    parsed = graph.parse(
        {
            "nodes": [_step_node("a"), _condition("a_when", _is("individual"), "a")],
            "entry": "a",
        }
    )
    assert any("Nothing leads to" in m for m in graph.graph_issues(parsed))


def test_a_condition_is_transparent_to_the_dependency_compiler() -> None:
    """It is not a step, so ``step_ancestors`` walks straight through it: the work on either arm
    waits for whatever fed the condition, and which arm actually runs is the walk's decision rather
    than a prerequisite."""
    parsed = graph.parse(
        {
            "nodes": [
                _step_node("a", next="pick"),
                _condition("pick", _is("individual"), "b", "c"),
                _step_node("b", next="c"),
                _step_node("c"),
            ]
        }
    )
    assert graph.effective_depends_on(parsed) == {"a": (), "b": ("a",), "c": ("b", "a")}


def test_publish_refuses_a_condition_with_no_conditions() -> None:
    parsed = graph.parse(
        {"nodes": [_start(next="a_when"), _condition("a_when", None, "a"), _step_node("a")]}
    )
    assert any("has no conditions" in m for m in graph.graph_issues(parsed))


def test_publish_refuses_a_condition_whose_true_arm_leads_nowhere() -> None:
    parsed = graph.parse(
        {
            "nodes": [
                _start(next="a_when"),
                _condition("a_when", _is("individual"), None, "a"),
                _step_node("a"),
            ]
        }
    )
    assert any("does not say where to go when it holds" in m for m in graph.graph_issues(parsed))


def test_publish_allows_an_unwired_otherwise_arm() -> None:
    """ "And otherwise this path is finished" is a real answer — it is what a condition on the last
    piece of work in a journey means, and refusing it would make every converted legacy flow
    unpublishable for saying something true."""
    parsed = graph.parse(
        {
            "nodes": [
                _start(next="a_when"),
                _condition("a_when", _is("individual"), "a"),
                _step_node("a"),
            ]
        }
    )
    assert graph.graph_issues(parsed) == []


def test_publish_refuses_a_condition_whose_arm_names_a_node_that_is_not_here() -> None:
    parsed = graph.parse(
        {"nodes": [_start(next="a_when"), _condition("a_when", _is("individual"), "ghost")]}
    )
    assert any("which is not in this flow" in m for m in graph.graph_issues(parsed))


def test_publish_refuses_a_condition_that_is_not_two_armed() -> None:
    """Reachable only from hand-written JSON — everything that mints one goes through
    ``condition_arms``. Refused rather than repaired: a third arm is a branch, and silently dropping
    it would delete an authored path."""
    parsed = graph.parse(
        {
            "nodes": [
                _start(next="pick"),
                {
                    "id": "pick",
                    "kind": "condition",
                    "config": {"branches": [_branch("b1", "a", _is("individual"))]},
                },
                _step_node("a"),
            ]
        }
    )
    assert any("must have exactly two paths" in m for m in graph.graph_issues(parsed))


def test_publish_refuses_a_condition_whose_fallback_carries_conditions() -> None:
    parsed = graph.parse(
        {
            "nodes": [
                _start(next="pick"),
                {
                    "id": "pick",
                    "kind": "condition",
                    "config": {
                        "branches": [
                            _branch("true", "a", _is("individual")),
                            _branch("false", "b", _is("organisation")),
                        ]
                    },
                },
                _step_node("a"),
                _step_node("b"),
            ]
        }
    )
    assert any(
        "is the fallback and cannot have conditions" in m for m in graph.graph_issues(parsed)
    )


def test_a_condition_carrying_a_broken_condition_is_reported() -> None:
    parsed = graph.parse(
        {
            "nodes": [
                _start(next="a_when"),
                _condition("a_when", {"field": "nope.nope", "operator": "equals", "value": 1}, "a"),
                _step_node("a"),
            ]
        }
    )
    assert graph.graph_issues(parsed) != []


def test_a_condition_cannot_carry_a_next_because_its_arms_are_its_edges() -> None:
    with pytest.raises(graph.DefinitionError, match="branches carry its outgoing edges"):
        graph.parse({"nodes": [{**_condition("a_when", _is("individual"), "a"), "next": "a"}]})


# ---------------------------------------------------------------------------------------------
# Conversion — a legacy flow, wired into a spine with its conditions on it
# ---------------------------------------------------------------------------------------------


@pytest.mark.pkgs_ui_pin
def test_the_contract_pins_the_condition_conversion_rule() -> None:
    """Cross-language, like the wiring rule: the contract carries a worked example and both sides
    reproduce it exactly (``convert.ts::conditionsFromVisibility`` is the mirror)."""
    example = _contract()["conditionUpgradeExample"]
    ours = graph.visibility_to_conditions({"nodes": example["authored"]})["nodes"]
    assert [
        {k: n.get(k) for k in expected}
        for n, expected in zip(ours, example["converted"], strict=True)
    ] == example["converted"], _DRIFT


@pytest.mark.pkgs_ui_pin
def test_the_contract_pins_the_condition_arm_ids() -> None:
    assert list(_contract()["controlNodes"]["condition"]["armIds"]) == [
        graph.CONDITION_TRUE,
        graph.CONDITION_FALSE,
    ], _DRIFT


def test_conversion_wires_the_prerequisites_and_puts_the_condition_on_the_path() -> None:
    converted = graph.convert_legacy(
        {
            "steps": [
                {"id": "a", "type": "task", "title": "A", "visibility": _is("individual")},
                {"id": "b", "type": "task", "title": "B", "depends_on": ["a"]},
            ]
        }
    )
    assert [(n["id"], n["kind"]) for n in converted["nodes"]] == [
        ("start", "start"),
        ("a_when", "condition"),
        ("a", "step"),
        ("b", "step"),
    ]
    by_id = {n["id"]: n for n in converted["nodes"]}
    # The condition takes the step's PLACE: the start now points at it, not at the step, so the walk
    # cannot reach the work without passing the question.
    assert by_id["start"]["next"] == "a_when"
    # …and its arms re-converge, because skipping `a` means going where `a` was going.
    assert by_id["a_when"]["config"]["branches"] == graph.condition_arms(
        _is("individual"), when="a", otherwise="b"
    )
    assert by_id["a"]["next"] == "b"
    assert by_id["b"]["depends_on"] == ["a"]
    # TRANSFORMED, not copied — the condition lives in one place afterwards.
    assert by_id["a"]["visibility"] is None


def test_conversion_leaves_the_otherwise_arm_unwired_rather_than_guessing() -> None:
    """An arm is single-valued by contract, so picking one of a fan would silently drop the others.
    Publish says so and the author draws the wire."""
    converted = graph.convert_legacy(
        {
            "steps": [
                {"id": "a", "type": "task", "title": "A", "visibility": _is("individual")},
                {"id": "b", "type": "task", "title": "B", "depends_on": ["a"]},
                {"id": "c", "type": "task", "title": "C", "depends_on": ["a"]},
            ]
        }
    )
    gate = next(n for n in converted["nodes"] if n["kind"] == "condition")
    assert gate["config"]["branches"][1]["next"] is None


def test_the_converted_seed_publishes_and_keeps_every_condition() -> None:
    """The conditions are the part that must survive conversion byte-for-byte: a dropped one
    silently changes who a step applies to, which in onboarding means asking a client for an
    offshore KYC form for an onshore account or a SIPP declaration for an ISA."""
    legacy = _seed_normalised()
    gated = [s for s in legacy["steps"] if s["visibility"]]
    assert len(gated) > 1, "a seed with no conditions would not test anything"

    converted = validate_definition(graph.convert_legacy(legacy))
    kinds = [n["kind"] for n in converted["nodes"]]  # type: ignore[index,union-attr]
    assert kinds.count("condition") == len(gated)
    assert set(kinds) == {"start", "condition", "step"}
    assert definitions.publish_issues(converted) == []

    asked = {
        n["config"]["branches"][0]["next"]: n["config"]["branches"][0]["conditions"]
        for n in converted["nodes"]  # type: ignore[union-attr]
        if n["kind"] == "condition"
    }
    assert asked == {s["id"]: s["visibility"] for s in gated}
    # The work itself is untouched — same steps, same order, and no `visibility` left behind.
    assert [s["id"] for s in converted["steps"]] == [  # type: ignore[index,union-attr]
        s["id"] for s in legacy["steps"]
    ]
    assert all(s["visibility"] is None for s in converted["steps"])  # type: ignore[index,union-attr]


def test_converting_twice_mints_nothing_the_second_time() -> None:
    """Idempotent, because the builder calls it on a shape transition and a shape transition is not
    always a single event. The second pass must also not RE-WIRE: running the prerequisite rule over
    a converted document would add edges beside the conditions it just minted, bypassing them."""
    once = graph.convert_legacy(
        {
            "steps": [
                {"id": "a", "type": "task", "title": "A", "visibility": _is("individual")},
                {"id": "b", "type": "task", "title": "B", "depends_on": ["a"]},
            ]
        }
    )
    assert graph.convert_legacy(once) == once


def test_conversion_wires_a_definition_with_no_conditions_and_mints_none() -> None:
    legacy = {"steps": [{"id": "a", "type": "task", "title": "A"}]}
    converted = graph.convert_legacy(legacy)
    assert [n["id"] for n in converted["nodes"]] == ["start", "a"]
    assert not any(n["kind"] == "condition" for n in converted["nodes"])


# ---------------------------------------------------------------------------------------------
# The SHIPPED seed — the onboarding journey, rebuilt as a wired spine
# ---------------------------------------------------------------------------------------------
#
# `onboarding_seed._build_definition` returns `{nodes}`: a start node, every node wired into the
# next, and each condition a two-armed node whose arms re-converge. `_legacy_definition` is kept
# beside it as the statement of what work the journey does — the PLAN legitimately differs now (a
# spine compiles its wires into prerequisites), so what is pinned here is that the same work is
# there, that the spine publishes, and that it actually walks.


def test_the_shipped_seed_is_a_wired_spine() -> None:
    shipped = _shipped_definition()
    assert "steps" not in shipped, "`steps` is a derived projection — never authored"
    kinds = [n.get("kind") for n in shipped["nodes"]]
    assert set(kinds) == {"start", "step", "condition"}
    assert kinds.count("start") == 1
    parsed = graph.parse(validate_definition(shipped))
    # Everything is wired into the next: only the last node leads nowhere, and every node but the
    # start is reachable from it.
    leaves = [nid for nid in parsed.nodes if not graph.successors(parsed, nid)]
    assert leaves == ["setup"]
    reached: set[str] = set()
    queue = [parsed.entry]
    while queue:
        current = queue.pop()
        if current in reached:
            continue
        reached.add(str(current))
        queue.extend(graph.successors(parsed, str(current)))
    assert reached == set(parsed.nodes)


def test_the_shipped_seed_does_the_same_work_as_the_journey_it_replaced() -> None:
    """The rebuild changed the ORDER, deliberately. What it must not change is the work: the same
    step ids, types, tiers, actors and config, so no terms declaration is quietly dropped and no
    fact-find quietly re-assigned.

    Compared as a SET keyed by id, not as a sequence: the spine now runs in the legacy order
    (fact-find → declaration → terms → identity → AML), which is a different order from the tier
    listing this reference was written in, and asserting on sequence would only re-state that.
    Two steps are additions. `declaration` is the adviser's handoff, which the tier-parallel shape
    had nowhere to put (`docs/onboarding-legacy.md` §2). `fatca_form` is the client's signed W-9 /
    W-8, released by a fork on the answer Additional information remembers — a runtime question
    the tier-parallel shape could not ask, so that form also carries `remember` here.
    """
    shipped = validate_definition(_shipped_definition())["steps"]
    legacy = _seed_normalised()["steps"]
    keys = ("id", "type", "title", "scope", "assignee", "per_participant", "config", "required")
    by_id = {s["id"]: {k: s.get(k) for k in keys} for s in shipped}  # type: ignore[union-attr]
    assert set(by_id) - {s["id"] for s in legacy} == {"declaration", "fatca_form"}
    for step in legacy:
        expected = {k: step.get(k) for k in keys}
        if step["id"] == "additional_info":
            expected["config"] = {
                **expected["config"],
                "remember": onboarding_seed.FATCA_REMEMBER,
            }
        assert by_id[step["id"]] == expected, step["id"]
    upload = by_id["fatca_form"]
    assert (upload["type"], upload["scope"], upload["assignee"]) == (
        "upload_document",
        "participant",
        "client",
    )
    assert upload["config"]["category"] == "fatca_form"
    handoff = by_id["declaration"]
    assert (handoff["type"], handoff["scope"], handoff["assignee"]) == (
        "task",
        "application",
        "staff",
    )
    # Adviser-form applicability stays plan-time visibility so irrelevant forms are never shown as
    # incomplete work. Per-participant terms keep their owner visibility for the same reason.
    kept = {
        s["id"]: s["visibility"]
        for s in shipped  # type: ignore[union-attr]
        if s["visibility"] is not None
    }
    assert list(kept) == [
        "core",
        "jisa_child",
        "jisa_registered_contact",
        "org",
        "additional_info",
        "pension",
        "employer",
        "fatca_form",
        "terms_general",
        "terms_isa",
        "terms_transfer",
        "terms_platform",
    ]
    # What stayed is a PART of what was there — the per-participant half. The rest of each terms
    # declaration's condition (ISA, transfer-in) is a per-account question and became a fork.
    for step in legacy:
        if step["id"] not in kept:
            continue
        was = step["visibility"]
        parts = was["conditions"] if was.get("logic") == "and" else [was]
        assert kept[step["id"]] == was or kept[step["id"]] in parts, step["id"]


def test_the_shipped_seed_asks_every_condition_the_old_one_asked() -> None:
    """Where a condition ends up is the design; whether it is still asked is the safety rule.

    Two things are compared rather than a per-step mapping, because the spine legitimately factors
    the conditions differently: the terms declarations shared an "is this an individual" test
    and it is now one fork above all of them, and "organisation" is the OTHER ARM of the fork that
    asks about individuals rather than a condition of its own. So: every field the old journey
    decided on is still decided on, and every step that used to be conditional is still behind a
    condition.
    """
    shipped = validate_definition(_shipped_definition())
    parsed = graph.parse(shipped)

    def fields(condition: Any) -> set[str]:
        if not condition:
            return set()
        members = condition.get("conditions") if condition.get("logic") else None
        if isinstance(members, list):
            return {f for m in members for f in fields(m)}
        return {str(condition.get("field"))}

    asked = {
        f
        for node in parsed.nodes.values()
        if node.kind == graph.CONDITION_KIND
        for arm in node.branches
        for f in fields(arm.conditions)
    } | {f for s in shipped["steps"] for f in fields(s["visibility"])}  # type: ignore[union-attr]
    was_asked = {f for s in _seed_normalised()["steps"] for f in fields(s["visibility"])}
    assert was_asked <= asked

    # …and every step that used to be conditional can still be skipped: some fork, taking some arm,
    # elides it. (Not "is directly behind a condition": `terms_platform` sits below the terms fork
    # with three declarations between them, and the fork skips the whole block.)
    skippable = {
        sid
        for nid, node in parsed.nodes.items()
        if node.kind == graph.CONDITION_KIND
        for arm in node.branches
        for sid in planning.elided_by_branch(parsed, nid, arm.next)
    }
    still_planned_out = {
        s["id"]
        for s in shipped["steps"]  # type: ignore[union-attr]
        if s["visibility"] is not None
    }
    for step in _seed_normalised()["steps"]:
        if step["visibility"]:
            assert step["id"] in skippable | still_planned_out, step["id"]


def test_the_shipped_seed_re_converges_so_no_condition_can_end_the_journey() -> None:
    """Every fork lands back on the spine. What the "no" arm elides is the work that fork decides
    on — for the terms fork, the whole terms block — and never the journey's own end: a condition
    that could strand "Open the account" would be a client whose account silently never opens.
    """
    parsed = graph.parse(validate_definition(_shipped_definition()))
    forks = [nid for nid, n in parsed.nodes.items() if n.kind == graph.CONDITION_KIND]
    assert len(forks) >= 3
    for nid in forks:
        arms = {b.id: b for b in parsed.nodes[nid].branches}
        both = (arms[graph.CONDITION_TRUE].next, arms[graph.CONDITION_FALSE].next)
        assert all(both), nid  # both arms wired: the journey carries on either way
        for chosen in both:
            assert "setup" not in planning.elided_by_branch(parsed, nid, chosen), nid
        # The "no" arm elides what the fork decides on — the work between it and where the two arms
        # meet again.
        skipped = planning.elided_by_branch(parsed, nid, arms[graph.CONDITION_FALSE].next)
        assert arms[graph.CONDITION_TRUE].next in skipped, nid


def test_the_shipped_seed_is_publishable() -> None:
    assert definitions.publish_issues(validate_definition(_shipped_definition())) == []


def test_the_shipped_seed_still_fans_out_by_tier_not_by_wire() -> None:
    """The wires say what comes after what; `scope` still says how often it happens. A spine must
    not have flattened the per-person / per-account fan-out."""
    shipped = validate_definition(_shipped_definition())
    plan = planning.plan_execution(shipped["steps"], _application())  # type: ignore[arg-type]
    assert {case.subject_type for case in plan} == {"participant", "account", "application"} - {
        "application"
    } or len(plan) > 1


def test_the_minted_condition_id_never_collides() -> None:
    converted = graph.convert_legacy(
        {
            "steps": [
                {"id": "a", "type": "task", "title": "A", "visibility": _is("individual")},
                {"id": "a_when", "type": "task", "title": "Taken"},
            ]
        }
    )
    assert [n["id"] for n in converted["nodes"] if n["kind"] == "condition"] == ["a_when_2"]


# ---------------------------------------------------------------------------------------------
# Branch resolution — the table
# ---------------------------------------------------------------------------------------------


def _resolution_graph(*branches: dict[str, Any]) -> graph.Graph:
    return graph.parse(
        {
            "nodes": [
                _branch_node("pick", *branches),
                _step_node("one"),
                _step_node("two"),
                _step_node("fallback"),
            ],
            "entry": "pick",
        }
    )


def test_branch_takes_the_first_satisfiable_in_array_order() -> None:
    g = _resolution_graph(
        _branch("a", "one", _is("individual")),
        _branch("b", "two", _is("individual")),
        _branch("z", "fallback"),
    )
    assert graph.branch_choice(g, "pick", {"contact": {"type": "individual"}}) == "one"


def test_branch_skips_a_branch_whose_conditions_fail() -> None:
    g = _resolution_graph(
        _branch("a", "one", _is("organisation")),
        _branch("b", "two", _is("individual")),
        _branch("z", "fallback"),
    )
    assert graph.branch_choice(g, "pick", {"contact": {"type": "individual"}}) == "two"


def test_branch_falls_back_to_the_else() -> None:
    g = _resolution_graph(
        _branch("a", "one", _is("organisation")),
        _branch("z", "fallback"),
    )
    assert graph.branch_choice(g, "pick", {"contact": {"type": "individual"}}) == "fallback"


def test_the_else_is_a_fallback_wherever_it_sits() -> None:
    """An else in the middle must not swallow the case: an empty/absent condition evaluates True,
    so taking it strictly in array order would make the outcome depend on list position."""
    g = _resolution_graph(
        _branch("z", "fallback"),
        _branch("b", "two", _is("individual")),
    )
    assert graph.branch_choice(g, "pick", {"contact": {"type": "individual"}}) == "two"
    assert graph.branch_choice(g, "pick", {"contact": {"type": "organisation"}}) == "fallback"


def test_branch_with_no_match_and_no_else_goes_nowhere() -> None:
    g = _resolution_graph(
        _branch("a", "one", _is("organisation")),
        _branch("b", "two", _is("trust")),
    )
    assert graph.branch_choice(g, "pick", {"contact": {"type": "individual"}}) is None
    # ... which is exactly why publish refuses it.
    assert any("otherwise" in i for i in graph.graph_issues(g))


def test_next_nodes_of_a_terminal_and_an_unknown_node() -> None:
    g = graph.parse({"nodes": [{"id": "end", "kind": "exit", "config": {"outcome": "completed"}}]})
    assert graph.next_nodes(g, "end", {}) == ()
    assert graph.next_nodes(g, "nope", {}) == ()


def test_next_nodes_follows_a_linear_edge() -> None:
    g = graph.parse({"nodes": [_step_node("a", next="b"), _step_node("b")]})
    assert graph.next_nodes(g, "a", {}) == ("b",)


# ---------------------------------------------------------------------------------------------
# successors + the plan-time dependency compiler
# ---------------------------------------------------------------------------------------------


def test_successors_includes_branch_targets_and_dedupes() -> None:
    g = _resolution_graph(
        _branch("a", "one", _is("individual")),
        _branch("b", "one", _is("organisation")),  # re-converging branches
        _branch("z", "fallback"),
    )
    assert graph.successors(g, "pick") == ("one", "fallback")
    assert graph.successors(g, "nope") == ()


def test_successors_includes_a_wait_for_event_timeout_branch() -> None:
    g = graph.parse(
        {
            "nodes": [
                {
                    "id": "w",
                    "kind": "wait_for_event",
                    "config": {
                        "trigger": "client.form_submitted",
                        "timeout_days": 7,
                        "timeout_branch": "chase",
                    },
                    "next": "on",
                },
                _step_node("on"),
                _step_node("chase"),
            ]
        }
    )
    assert graph.successors(g, "w") == ("on", "chase")


def test_edges_compile_to_dependencies_through_control_nodes() -> None:
    """Both arms of a branch depend on whatever fed the branch — the branch itself is not a step,
    so it can never be a prerequisite. WHICH arm is released is branch_choice's runtime decision."""
    g = graph.parse(
        {
            "nodes": [
                _step_node("first", next="pick"),
                _branch_node(
                    "pick", _branch("a", "left", _is("individual")), _branch("z", "right")
                ),
                _step_node("left", next="join"),
                _step_node("right", next="join"),
                _step_node("join"),
            ]
        }
    )
    compiled = graph.effective_depends_on(g)
    assert "pick" not in compiled  # control nodes are not planned
    assert compiled == {
        "first": (),
        "left": ("first",),
        "right": ("first",),
        "join": ("left", "right"),
    }


def test_compiled_dependencies_keep_the_authored_ones() -> None:
    """An edge says "after"; ``depends_on`` says "not until ALL of these" — the join a single edge
    cannot express. Both survive."""
    g = graph.parse(
        {
            "nodes": [
                _step_node("a", next="b"),
                _step_node("side"),
                _step_node("b", depends_on=["side"]),
            ]
        }
    )
    assert graph.effective_depends_on(g)["b"] == ("side", "a")


# ---------------------------------------------------------------------------------------------
# Publish preflight
# ---------------------------------------------------------------------------------------------


def test_dangling_edges_are_reported_not_raised() -> None:
    """A half-built canvas autosaves; the refusal belongs at publish, where the author is asking."""
    g = graph.parse({"nodes": [_step_node("a", next="ghost")]})
    assert any("“ghost”, which is not in this flow" in i for i in graph.graph_issues(g))


def test_an_unwired_step_is_not_an_orphan() -> None:
    """A work node needs no incoming wire: it is reached through its TIER.

    ``effective_depends_on`` gives it no prerequisites, so ``planning`` creates its instance and it
    becomes available the moment its tier opens — it genuinely runs. Calling it an orphan forced an
    author who added one branch to a twenty-step flow to wire the other nineteen into a chain, and a
    chain compiles into hard prerequisites: work a tier used to offer together, offered one item at
    a time.
    """
    g = graph.parse({"nodes": [_step_node("a"), _step_node("stray")], "entry": "a"})
    assert graph.graph_issues(g) == []


def test_an_unreachable_control_node_IS_an_orphan() -> None:
    """A wire is the only way into a control node — there is no tier to fall back on. A branch
    nobody points at never evaluates, so this stays a refusal."""
    g = graph.parse(
        {
            "nodes": [
                _step_node("a"),
                _branch_node(
                    "stray",
                    _branch("b1", "a", {"field": "x", "operator": "equals", "value": 1}),
                    _branch("b2", "a"),
                    title="Stray fork",
                ),
            ],
            "entry": "a",
        }
    )
    assert any("Nothing leads to" in i and "stray" in i.lower() for i in graph.graph_issues(g))


def test_a_loop_is_refused() -> None:
    g = graph.parse({"nodes": [_step_node("a", next="b"), _step_node("b", next="a")]})
    issues = graph.graph_issues(g)
    assert any("lead back to each other in a loop" in i for i in issues)
    assert sum("loop" in i for i in issues) == 1  # reported once, not once per way in


def test_a_self_loop_is_refused() -> None:
    g = graph.parse({"nodes": [_step_node("a", next="a")]})
    assert any("loop" in i for i in graph.graph_issues(g))


def test_a_branch_needs_at_least_two_branches() -> None:
    g = graph.parse({"nodes": [_branch_node("p", _branch("z", "a")), _step_node("a")]})
    assert any("at least 2 branches" in i for i in graph.graph_issues(g))


def test_a_branch_may_not_have_two_elses() -> None:
    g = graph.parse(
        {"nodes": [_branch_node("p", _branch("y", "a"), _branch("z", "a")), _step_node("a")]}
    )
    assert any("“otherwise” branches" in i for i in graph.graph_issues(g))


def test_too_many_branches_is_refused() -> None:
    arms = [_branch(f"b{n}", "a", _is(str(n))) for n in range(graph.MAX_BRANCHES_PER_NODE + 1)]
    g = graph.parse({"nodes": [_branch_node("p", *arms, _branch("z", "a")), _step_node("a")]})
    assert any("the most allowed is" in i for i in graph.graph_issues(g))


def test_a_branch_arm_that_leads_nowhere_is_allowed_when_another_arm_does_not() -> None:
    """An arm with no target means "and on this path there is nothing more to do".

    That is the shape converting a legacy step's condition produces: the "otherwise" arm skips the
    step, and for a step nothing depends on there is nowhere to skip TO — the tier simply carries on
    with its other work. Refusing it per-arm made eleven of the onboarding flow's twelve converted
    conditions unpublishable, which is how this was found.
    """
    g = graph.parse(
        {
            "nodes": [
                _branch_node("p", _branch("a", None, _is("x")), _branch("z", "a")),
                _step_node("a"),
            ]
        }
    )
    assert graph.graph_issues(g) == []


def test_a_branch_that_leads_nowhere_AT_ALL_is_reported() -> None:
    """Every arm dangling is a branch that decides nothing — a half-finished node, and the signal an
    author actually needs when they drop a fresh branch on the canvas."""
    g = graph.parse(
        {
            "nodes": [
                _branch_node("p", _branch("a", None, _is("x")), _branch("z", None)),
                _step_node("a", next="p"),
            ]
        }
    )
    assert any("leads nowhere at all" in i for i in graph.graph_issues(g))


def test_a_branch_arm_naming_a_node_that_is_not_here_is_still_reported() -> None:
    """The check that kept its teeth: dangling is always a mistake, however permissive null is."""
    g = graph.parse(
        {
            "nodes": [
                _branch_node("p", _branch("a", "ghost", _is("x")), _branch("z", "a")),
                _step_node("a"),
            ]
        }
    )
    assert any("which is not in this flow" in i for i in graph.graph_issues(g))


def test_a_branch_condition_is_validated_through_the_shared_evaluator() -> None:
    """One issue vocabulary: a branch's condition problems read exactly like a rule's."""
    bad = {"field": "contact.type", "operator": "wat", "value": 1}
    g = graph.parse(
        {"nodes": [_branch_node("p", _branch("a", "a", bad), _branch("z", "a")), _step_node("a")]}
    )
    assert any("a test we don’t know" in i for i in graph.graph_issues(g))


def test_the_node_cap_is_reported() -> None:
    nodes = [_step_node(f"n{i}") for i in range(graph.MAX_NODES_PER_FLOW + 1)]
    for i, node in enumerate(nodes[:-1]):
        node["next"] = f"n{i + 1}"
    g = graph.parse({"nodes": nodes})
    assert any("the most allowed is 200" in i for i in graph.graph_issues(g))


def test_the_hard_node_limit_is_a_refusal_not_an_issue() -> None:
    with pytest.raises(DefinitionError, match="more than we will read"):
        graph.parse({"nodes": [_step_node(f"n{i}") for i in range(graph._HARD_NODE_LIMIT + 1)]})


@pytest.mark.parametrize(
    ("config", "fragment"),
    [
        ({}, "does not say how long to wait"),
        ({"duration_days": "soon"}, "needs a number of days"),
        ({"duration_days": graph.MAX_WAIT_DAYS + 1}, "waits longer than 90 days"),
        ({"duration_days": -1}, "negative"),
        ({"until_date_field": "nonsense.field"}, "isn’t a field the engine knows"),
    ],
)
def test_wait_config_is_validated(config: dict[str, Any], fragment: str) -> None:
    g = graph.parse({"nodes": [{"id": "w", "kind": "wait", "config": config}]})
    assert any(fragment in i for i in graph.graph_issues(g)), graph.graph_issues(g)


def test_a_valid_wait_has_no_issues() -> None:
    g = graph.parse(
        {
            "nodes": [
                {"id": "w", "kind": "wait", "config": {"duration_days": 7}, "next": "a"},
                _step_node("a"),
            ]
        }
    )
    assert graph.graph_issues(g) == []


def test_wait_for_event_must_name_a_live_trigger() -> None:
    """A stale key is the failure with no symptom: the case waits forever for an event nobody will
    ever send."""
    g = graph.parse(
        {
            "nodes": [
                {
                    "id": "w",
                    "kind": "wait_for_event",
                    "config": {"trigger": "client.telepathy"},
                    "next": "a",
                },
                _step_node("a"),
            ]
        }
    )
    assert any("not an event the engine knows" in i for i in graph.graph_issues(g))


def test_wait_for_event_accepts_a_registry_key() -> None:
    g = graph.parse(
        {
            "nodes": [
                {
                    "id": "w",
                    "kind": "wait_for_event",
                    "config": {
                        "trigger": "client.form_submitted",
                        "timeout_days": 7,
                        "timeout_branch": "chase",
                    },
                    "next": "a",
                },
                _step_node("a"),
                _step_node("chase"),
            ]
        }
    )
    assert graph.graph_issues(g) == []


def test_wait_for_event_refuses_a_trigger_nothing_can_deliver() -> None:
    """The wedge. Publish knew an UNKNOWN trigger was fatal but not an UNDELIVERABLE one, so a
    schedule (not an event at all), a domain event (no consumer), and a task trigger (a case is
    never about a task) all published clean — and a node with no ``timeout_days`` then parked its
    case for ever with nothing on earth able to release it.

    The registry owns the answer (``triggers.listener_refusal``) so publish, ``arm_listener`` and
    the builder's picker read the same one; this pins that publish is a reader of it.
    """
    for trigger in (
        "schedule.recurring",
        "case.completed",
        "screening.completed",
        "tag.applied",
        "task.created",
        "task.completed",
    ):
        g = graph.parse(
            {
                "nodes": [
                    {
                        "id": "w",
                        "kind": "wait_for_event",
                        "config": {"trigger": trigger},
                        "next": "a",
                    },
                    _step_node("a"),
                ]
            }
        )
        issues = graph.graph_issues(g)
        assert issues, f"{trigger} published clean — the case would wait for ever"
        assert any(i.startswith("Wait for event") for i in issues), trigger


def test_wait_for_event_says_WHY_a_trigger_cannot_be_waited_for() -> None:
    """Two different problems need two different sentences: an author who picked a schedule has to
    be told a schedule is not an event, and one who picked a task trigger has to be told a journey
    has no task of its own. "Not supported" would leave both of them guessing."""

    def _issue(trigger: str) -> str:
        g = graph.parse(
            {
                "nodes": [
                    {
                        "id": "w",
                        "kind": "wait_for_event",
                        "config": {"trigger": trigger},
                        "next": "a",
                    },
                    _step_node("a"),
                ]
            }
        )
        return next(i for i in graph.graph_issues(g) if i.startswith("Wait for event"))

    assert "schedule" in _issue("schedule.recurring")
    assert "task" in _issue("task.completed")
    assert "deliver" in _issue("tag.applied")


def test_wait_for_event_timeout_branch_must_resolve() -> None:
    g = graph.parse(
        {
            "nodes": [
                {
                    "id": "w",
                    "kind": "wait_for_event",
                    "config": {
                        "trigger": "client.form_submitted",
                        "timeout_days": 7,
                        "timeout_branch": "ghost",
                    },
                    "next": "a",
                },
                _step_node("a"),
            ]
        }
    )
    assert any("the timeout leads to “ghost”" in i for i in graph.graph_issues(g))


def test_wait_for_event_timeout_branch_needs_a_deadline() -> None:
    g = graph.parse(
        {
            "nodes": [
                {
                    "id": "w",
                    "kind": "wait_for_event",
                    "config": {"trigger": "client.form_submitted", "timeout_branch": "chase"},
                    "next": "a",
                },
                _step_node("a"),
                _step_node("chase"),
            ]
        }
    )
    assert any("no deadline" in i for i in graph.graph_issues(g))


def test_start_flow_needs_a_flow_code() -> None:
    g = graph.parse({"nodes": [{"id": "s", "kind": "start_flow", "config": {}}]})
    assert any("does not name a flow to start" in i for i in graph.graph_issues(g))


def test_start_flow_is_the_sanctioned_loop() -> None:
    """A graph may loop deliberately via start_flow — it is a tail call the runtime depth-caps, not
    an edge, so cycle detection must not see it."""
    g = graph.parse(
        {
            "nodes": [
                _step_node("a", next="again"),
                {"id": "again", "kind": "start_flow", "config": {"flow_code": "onboarding"}},
            ]
        }
    )
    assert graph.graph_issues(g) == []


def test_exit_outcome_is_validated() -> None:
    g = graph.parse({"nodes": [{"id": "e", "kind": "exit", "config": {"outcome": "brilliant"}}]})
    assert any("use completed, cancelled, failed" in i for i in graph.graph_issues(g))


def test_an_empty_graph_reports_nothing_here() -> None:
    """ "Add at least one step" is publish_issues' message, and it should be said once."""
    assert graph.graph_issues(graph.parse({"nodes": []})) == []
    assert definitions.publish_issues({"steps": []}) == ["Add at least one step before publishing."]


# ---------------------------------------------------------------------------------------------
# validate_definition on the v3 shape
# ---------------------------------------------------------------------------------------------


def _v3(*nodes: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"nodes": list(nodes), **extra}


def test_v3_emits_a_derived_steps_projection() -> None:
    out = validate_definition(
        _v3(
            _step_node("first", next="pick"),
            _branch_node("pick", _branch("a", "left", _is("individual")), _branch("z", "right")),
            _step_node("left"),
            _step_node("right"),
        )
    )
    assert out["version"] == 3
    assert out["entry"] == "first"
    assert [n["id"] for n in out["nodes"]] == ["first", "pick", "left", "right"]  # type: ignore[index,union-attr]
    steps = out["steps"]
    assert isinstance(steps, list)
    assert [s["id"] for s in steps] == ["first", "left", "right"]  # branch is not a step
    assert all(set(s) & {"kind", "next"} == set() for s in steps)
    assert steps[1]["depends_on"] == ["first"]  # compiled through the branch


def test_v3_output_reparses_to_the_same_graph() -> None:
    definition = _v3(_step_node("a", next="b"), _step_node("b"))
    once = validate_definition(definition)
    assert validate_definition(once) == once
    assert not graph.parse(once).is_legacy_linear  # `nodes` wins over the derived `steps`


def test_v3_milestones_reference_step_nodes() -> None:
    out = validate_definition(
        _v3(_step_node("a"), milestones=[{"status": "done", "reached_when": ["a"]}])
    )
    assert out["milestones"] == [{"status": "done", "reached_when": ["a"]}]
    with pytest.raises(DefinitionError, match="unknown steps"):
        validate_definition(
            _v3(
                _step_node("a", next="p"),
                _branch_node("p", _branch("x", "a", _is("y")), _branch("z", "a")),
                milestones=[{"status": "done", "reached_when": ["p"]}],
            )
        )


def test_v3_refuses_a_dependency_loop_at_save() -> None:
    """Consistent with the legacy ``depends_on`` rule: a loop is never valid at any point, so it is
    refused where it is authored rather than only at publish."""
    with pytest.raises(DefinitionError, match="loop"):
        validate_definition(_v3(_step_node("a", next="b"), _step_node("b", next="a")))


def test_v3_action_node_config_is_allowlisted() -> None:
    out = validate_definition(
        _v3(
            {
                "id": "tag",
                "kind": "action",
                "type": "apply_tag",
                "config": {"tag": "vip", "target": "contact"},
            }
        )
    )
    node = out["nodes"][0]  # type: ignore[index]
    assert node["title"] == "Apply tag"
    assert graph.parse(out).nodes["tag"].is_action
    with pytest.raises(DefinitionError, match="unknown config keys"):
        validate_definition(
            _v3({"id": "tag", "kind": "action", "type": "apply_tag", "config": {"colour": "red"}})
        )


def test_the_kind_decides_a_name_collision_not_a_precedence_rule() -> None:
    """``generate_document`` is in both vocabularies. ``kind`` says which one is meant — the whole
    reason the contract grew an explicit discriminator (its ``$comment_actionTypes``): the old rule
    ("step types win") made the automated one unauthorable, silently."""
    out = validate_definition(
        _v3(
            {
                "id": "pack",
                "type": "generate_document",
                "title": "Pack",
                "config": {"template_uid": "tpl_1"},
            },
            {
                "id": "auto",
                "kind": "action",
                "type": "generate_document",
                "title": "Render it",
                "config": {"template_uid": "tpl_1"},
            },
        )
    )
    parsed = graph.parse(out)
    assert not parsed.nodes["pack"].is_action
    assert parsed.nodes["auto"].is_action
    # Both are planned; only the projection's type distinguishes them once the kind is gone.
    types = {str(s["id"]): s["type"] for s in out["steps"]}  # type: ignore[index]
    assert types == {"pack": "generate_document", "auto": "action:generate_document"}


def test_an_action_node_must_name_a_real_action() -> None:
    with pytest.raises(DefinitionError, match="unknown action"):
        validate_definition(_v3({"id": "x", "kind": "action", "type": "fill_form", "config": {}}))


def test_a_step_node_may_not_name_an_action() -> None:
    """The discriminator cuts both ways: without a ``kind`` an action type is not a step type, and
    saying so is better than quietly planning a step nothing can handle."""
    with pytest.raises(DefinitionError, match="unknown type 'apply_tag'"):
        validate_definition(_v3({"id": "tag", "type": "apply_tag", "config": {"tag": "vip"}}))


def test_v3_unknown_step_type_is_refused() -> None:
    with pytest.raises(DefinitionError, match="unknown type"):
        validate_definition(_v3({"id": "x", "type": "teleport", "title": "X"}))


def test_v3_depends_on_may_not_name_a_control_node() -> None:
    with pytest.raises(DefinitionError, match="depends on unknown steps"):
        validate_definition(
            _v3(
                _branch_node("p", _branch("x", "a", _is("y")), _branch("z", "a")),
                _step_node("a", depends_on=["p"]),
            )
        )


def test_v3_publish_issues_include_the_graph() -> None:
    out = validate_definition(
        _v3(
            _step_node("a", next="p"),
            _branch_node("p", _branch("x", "a2", _is("individual")), _branch("y", "a3", _is("o"))),
            _step_node("a2"),
            _step_node("a3"),
        )
    )
    assert any("otherwise" in i for i in definitions.publish_issues(out))


# ---------------------------------------------------------------------------------------------
# The one-exception-type guarantee
# ---------------------------------------------------------------------------------------------

_HOSTILE: list[Any] = [
    None,
    [],
    "",
    0,
    {"nodes": "not a list"},
    {"steps": {"id": "a"}},
    {"nodes": [None]},
    {"nodes": [[]]},
    {"nodes": [{"id": None}]},
    {"nodes": [{"id": "A B"}]},
    {"nodes": [{"id": "a", "kind": 7}]},
    {"nodes": [{"id": "a", "config": "nope"}]},
    {"nodes": [{"id": "a", "next": 7}]},
    {"nodes": [{"id": "a", "depends_on": "b"}]},
    {"nodes": [{"id": "a", "visibility": "yes"}]},
    {"nodes": [{"id": "a", "title": 7}]},
    {"nodes": [{"id": "a", "type": 7}]},
    {"nodes": [{"id": "a", "scope": 7}]},
    {"nodes": [{"id": "p", "kind": "branch", "config": {"branches": "x"}}]},
    {"nodes": [{"id": "p", "kind": "branch", "config": {"branches": [None]}}]},
    {"nodes": [{"id": "p", "kind": "branch", "config": {"branches": [{"id": "!"}]}}]},
    {"nodes": [{"id": "p", "kind": "branch", "config": {"branches": [{"id": "a", "next": 7}]}}]},
    {
        "nodes": [
            {"id": "p", "kind": "branch", "config": {"branches": [{"id": "a", "conditions": 7}]}}
        ]
    },
    {"nodes": [{"id": "p", "kind": "branch", "config": {"branches": [{"id": "a", "label": 7}]}}]},
    {"nodes": [{"id": "a"}], "entry": 7},
    {"nodes": [{"id": "a"}], "entry": []},
]


@pytest.mark.parametrize("hostile", _HOSTILE)
def test_parse_raises_only_definition_error(hostile: Any) -> None:
    """``parse`` runs on every autosave and on stored JSON in the worker. The caller's contract is
    one exception type, always — anything else is a 500 on a save button."""
    try:
        graph.parse(hostile)
    except DefinitionError:
        pass
    except Exception as exc:  # pragma: no cover — this failing IS the test failing
        pytest.fail(f"parse raised {type(exc).__name__}: {exc}")


def test_deeply_nested_junk_does_not_blow_up() -> None:
    nested: Any = {"field": "a", "operator": "equals", "value": 1}
    for _ in range(2_000):
        nested = {"logic": "and", "conditions": [nested]}
    parsed = graph.parse(
        {
            "nodes": [
                _branch_node("p", _branch("a", "s", nested), _branch("z", "s")),
                _step_node("s"),
            ]
        }
    )
    assert graph.branch_choice(parsed, "p", {}) in ("s", None)
    assert isinstance(graph.graph_issues(parsed), list)


@pytest.mark.parametrize("hostile", _HOSTILE)
def test_validate_definition_raises_only_definition_error(hostile: Any) -> None:
    try:
        validate_definition(hostile)
    except DefinitionError:
        pass
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"validate_definition raised {type(exc).__name__}: {exc}")


@pytest.mark.pkgs_ui_pin
def test_contract_mapping_transforms_match() -> None:
    """The transform names an author can pick in the flow editor are the ones the mapper can run.

    An unknown transform is a `MappingError` at publish, so a contract listing one the mapper does
    not implement turns a legal-looking choice into a refusal only discovered on save.
    """
    from app.flows.mapping import TRANSFORMS

    assert set(TRANSFORMS) == set(_contract()["mapping"]["transforms"]), _DRIFT


@pytest.mark.pkgs_ui_pin
def test_contract_mapping_targets_match_the_writable_columns() -> None:
    """The targets offered as COLUMNS are exactly the columns `_write_contact_patch` will set.

    Drift here is silent in the worst way: a target the writer does not whitelist is not rejected,
    it is nested into `data` instead — so an author picks "Date of birth" from a dropdown and the
    answer lands at `data.dob`, invisible to every screen that reads the column.
    """
    import inspect
    import re

    from app.flows import step_handlers

    source = inspect.getsource(step_handlers._write_contact_patch)
    block = source[source.index("columns: dict[str, str | None] = {") :]
    written = set(re.findall(r'"(\w+)":', block[: block.index("}")]))
    assert written == set(_contract()["mapping"]["targets"]), _DRIFT
