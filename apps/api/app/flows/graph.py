"""The flow **graph** — v3's node model, and the pure functions the runtime asks of it.

Before v3 a flow definition was an ordered list of steps whose only edges were ``depends_on``
(backwards-pointing prerequisites), and the nearest thing to a branch was a step's ``visibility``
condition rendered by the editor to *look* like one. v3 makes the branch a first-class authored
node with numbered outgoing edges and a mandatory fallback.

**The graph does not replace the execution machinery — it compiles into it.**
``planning.plan_execution`` still fans a tier-scoped step list into cases + steps carrying
``depends_on``, and the orchestrator still instantiates a step when its prerequisites settle. So:

* an authored edge ``a → b`` compiles to "``b`` depends on ``a``" at plan time
  (``effective_depends_on``), which is why the tiered fan-out and the cross-case KYC gate keep
  working untouched;
* a **branch node is not a step**. Nothing is planned for it. It is a decision the engine takes at
  runtime when its predecessor settles, by calling ``branch_choice`` — one of this module's two
  runtime-facing functions (``next_nodes`` is the other), and deliberately pure so both are
  exhaustively testable without a DB.

The wire shape
--------------
Two shapes load, and both are first-class:

**Legacy** — ``{steps: [...], milestones: [...], intake: {...}}``. Every definition in the wild
today is one of these. It compiles to a linear chain of ``kind="step"`` nodes with ids preserved
and ``is_legacy_linear=True``.

.. warning::
   For a legacy graph the chain is **presentation order, not execution order**. Its execution
   order is, and remains, ``depends_on``. Two structural guards keep that honest, because
   sequentialising a live onboarding journey by accident would be a very quiet disaster:
   ``next_nodes`` returns ``()`` for a legacy graph (it declines to make a runtime decision it
   was never authored to make), and ``effective_depends_on`` returns the authored ``depends_on``
   verbatim with no edge-derived additions.

**v3** — ``{version: 3, entry: <node id>, nodes: [...], milestones: [...], intake: {...}}``, where
a node is a legacy step object plus two keys:

.. code-block:: json

   { "id": "start", "kind": "start", "next": ["core", "terms"] }
   { "id": "core", "kind": "step", "type": "fill_form", "title": "Applicant details",
     "scope": "participant", "assignee": "staff", "visibility": {...}, "config": {...},
     "next": "offshore?" }
   { "id": "offshore?", "kind": "branch", "title": "Offshore?",
     "config": { "branches": [
       { "id": "b1", "label": "Offshore", "conditions": {...}, "next": "edd" },
       { "id": "b2", "label": "Otherwise", "conditions": null,  "next": "setup" } ] } }

Four decisions behind that shape, each taken to keep the builder's diff small and the document
honest:

1. **A step node IS a legacy step**, with ``kind`` (defaulting to ``"step"``, so it may be omitted)
   and ``next`` added. Nothing about an authored step changes, so the editor's inspector, the
   handlers and the planner all keep reading the same keys. ``kind: "action"`` is the same shape
   for unattended engine work, and the kind is what decides whether ``type`` resolves against the
   step vocabulary or the action one — ``generate_document`` is in both (see ``Node.is_action``).
2. **Edges live on the node, never in a separate ``edges`` array.** The legacy shape already
   carried its edges on the step (``depends_on``); a second place for edges would be a second
   class of dangling reference to police, and would make copy-pasting a node in the builder lossy.
   So: ``next`` (top level, because an edge is not a parameter) and ``branches[].next``.
3. **Authored parameters live in ``config``, for control nodes exactly as for steps** — which is
   what the contract's ``controlNodes.<kind>.config`` says. Hence ``config.branches``, and hence
   ``wait_for_event``'s ``timeout_branch`` sitting in config even though it is an edge: the
   contract puts it there, and one contract beats one tidy rule.
4. **``next`` may be a LIST, and a list means parallel** (the contract's
   ``nodeEnvelope.$comment_nextArity``). ``{next: ["a", "b"]}`` releases both at once; a single
   edge stays a bare string so no existing document changes shape. Everything here reads
   ``Node.next_ids``, which is the normalised tuple — the field is deliberately not called ``next``
   so that no call site written against the old single-valued key can survive the change by
   accident. A **branch arm** stays single-valued: an arm that must open several things points at a
   node that fans out, rather than giving the document two spellings for one idea.

The ``start`` node
------------------
``kind: "start"`` is the explicit entry, at most one per definition, no config, nothing pointing at
it. It exists ONLY because a fan-out needs something to fan out *from*: "the first node nothing
points at" cannot name two roots, so without it a journey that opens on five terms declarations —
which is what our live onboarding does — has no v3 spelling. When a definition has one it IS
``Graph.entry``; when it has none the entry is derived exactly as it always was, so every v3
definition authored before the arity rule loads unchanged.

The ``condition`` node
----------------------
``kind: "condition"`` is a **branch with a fixed two-arm shape** — control flow, evaluated when a
case walks into it, exactly like ``branch``. It carries its arms in ``config.branches`` in the same
representation a branch uses, fixed at two: ``true`` (the author's condition) and ``false`` (the
unconditional else). Both arms may point at the SAME node, which is how a journey splits and comes
back together.

It is implemented ON the branch machinery rather than beside it, and that is the whole design:
``next_nodes``, ``branch_choice``, arm evaluation, untaken-subtree elision, the cycle and
reachability checks, ``planning.control_gated_steps`` and ``orchestrator._enter_node`` all treat
the two kinds alike (``BRANCHING_KINDS``). What differs is the authoring surface and the picture —
two arms, a nicer editor, its own look — and ``branch`` remains the node for three or more paths.

.. warning::
   This was a plan-time GATE before Aug 2026 — a node that compiled into the gated step's
   ``visibility`` and that nothing walked through — and the reasoning for that is worth recording
   because it was correct for the shape it was written against. A branch is only evaluated when
   control reaches it, and ``planning.control_gated_steps`` holds any step behind a control node
   until that walk arrives; the seeded onboarding had no control spine at all (three tiers offering
   their work in parallel, ordered only by ``depends_on``), so branch-gating its conditions left 14
   steps held behind nodes nothing reached. **Wiring every node into the next removes that**: the
   journey now has a spine, control genuinely flows through every node, so a condition can decide a
   path and the steps behind it are released by the walk that reaches them. The seed was rebuilt as
   that spine in the same change (``onboarding_seed._build_definition``), and the accepted
   consequence is that its work is reached one node at a time rather than a tier at a time.

``visibility`` stays in the engine and ``planning`` keeps evaluating it: legacy definitions in
production still carry one on most of their steps. What is gone is the *compile* — a condition node
decides a path, it does not rewrite a step's ``visibility``.

``validate_definition`` emits a **derived** ``steps`` projection alongside ``nodes`` for a v3
definition — the step-kind nodes, in order, in the legacy shape, with their compiled
``depends_on``. It is regenerated from ``nodes`` on every save and never authored, so the whole
existing engine (planning, projection, the router's reference/handler preflights) reads a v3
definition unchanged. ``parse`` ignores ``steps`` whenever ``nodes`` is present, so the projection
can never become a competing source of truth.

The contract mirror
-------------------
``pkgs/ui/.../book/journeys/flow-contract.json`` is the cross-language source of truth, and the
API container ships **without** pkgs/ui — so the node kinds, their config keys, the action config
keys and the caps are mirrored here as Python literals with a pin test
(``tests/test_flows_graph.py``) that fails the moment the two drift. Same precedent as
``app/branding.py::default_pages`` and ``conditions.OPERATORS``. Change the JSON and this file
together, never one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from app.flows import conditions, triggers

_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")


class DefinitionError(ValueError):
    """A flow definition failed validation (surfaced as a 422 by the router).

    Lives here rather than in ``definitions.py`` for one boring reason: ``definitions`` imports
    this module, so the exception has to sit at the bottom of the import graph. It is re-exported
    as ``definitions.DefinitionError``, which is what every existing call site catches — the class
    object is the same one, so nothing changes for them.
    """


# ---------------------------------------------------------------------------------------------
# The contract mirror (pinned against flow-contract.json → controlNodes / actionTypes / caps)
# ---------------------------------------------------------------------------------------------

#: The node kind that carries work a human does.
STEP_KIND = "step"

#: The node kind that carries work the ENGINE does, unattended. ``type`` resolves against
#: ``ACTION_CONFIG_KEYS`` rather than ``definitions.STEP_TYPES``, and THIS FIELD is the
#: discriminator between the two vocabularies — ``generate_document`` is in both, and a precedence
#: rule ("step types win") made one of them silently unauthorable. See the contract's
#: ``$comment_actionTypes``.
ACTION_KIND = "action"

#: The graph's explicit entry. Not planned, carries no config, and its list-valued ``next`` is the
#: whole point — see the module docstring's "The ``start`` node".
START_KIND = "start"

#: The two-armed branch — see "The ``condition`` node" in the module docstring.
CONDITION_KIND = "condition"

#: The arm ids a ``condition`` node's two branches carry. Fixed, because the pair IS the node: the
#: inspector edits "if it holds, go to…" and "otherwise go to…", and an arm named anything else
#: would be a branch wearing a condition's chrome.
CONDITION_TRUE = "true"
CONDITION_FALSE = "false"

#: Node kinds that become a planned step row. Everything else is control flow — nothing is planned
#: for it, it is a decision or a hop the engine takes at runtime.
PLANNED_KINDS: frozenset[str] = frozenset({STEP_KIND, ACTION_KIND})

#: The kinds whose outgoing edges are ARMS (``config.branches``) rather than a ``next``, and which
#: the runtime resolves by evaluating those arms in order. A ``condition`` is a ``branch`` with a
#: fixed two-arm shape, so every traversal, decision and elision rule reads this rather than naming
#: one kind — forking the machinery is what would let the two drift.
BRANCHING_KINDS: frozenset[str] = frozenset({"branch", CONDITION_KIND})

#: Every node kind the builder may author (the contract's ``nodeKinds``).
NODE_KINDS: frozenset[str] = (
    PLANNED_KINDS
    | BRANCHING_KINDS
    | frozenset({START_KIND, "wait", "wait_for_event", "start_flow", "exit"})
)

#: ``controlNodes.start.maxPerFlow``. A second starting point is not a publish issue but a parse
#: refusal: two entries is not a half-finished graph, it is a document with no answer to "where
#: does a case begin", and the builder must never be able to author one.
MAX_START_NODES = 1

#: Allowed ``config`` keys per control node (mirror of ``controlNodes.<kind>.config``). A step's
#: config keys are ``definitions.STEP_TYPES``; an action node's are ``ACTION_CONFIG_KEYS`` below.
CONTROL_CONFIG_KEYS: dict[str, frozenset[str]] = {
    START_KIND: frozenset(),
    CONDITION_KIND: frozenset({"branches"}),
    "branch": frozenset({"branches"}),
    "wait": frozenset({"duration_days", "duration_hours", "until_date_field"}),
    "wait_for_event": frozenset({"trigger", "conditions", "timeout_days", "timeout_branch"}),
    "start_flow": frozenset({"flow_code", "carry_memory"}),
    "exit": frozenset({"outcome", "reason"}),
}

#: Human labels for the control kinds (mirror of ``controlNodes.<kind>.label``) — used in issue
#: messages so an author reads "Branch “Offshore?”" rather than a bare id.
CONTROL_LABELS: dict[str, str] = {
    START_KIND: "Start",
    CONDITION_KIND: "Condition",
    "branch": "Branch",
    "wait": "Wait",
    "wait_for_event": "Wait for event",
    "start_flow": "Start another flow",
    "exit": "End",
}

#: The stem of a minted condition node's title (``visibility_to_conditions``), mirrored verbatim in
#: ``convert.ts``. A constant because the two languages have to mint the same string for a converted
#: document to be comparable byte-for-byte.
_ONLY_WHEN = "Only when"

#: Allowed ``config`` keys per action type (mirror of ``actionTypes.<type>.config``). The
#: *permission* half of each entry stays in ``effects.ACTIONS`` — that module owns running
#: the action, and one mirror of the permission is enough. We mirror only the keys because this
#: module must stay import-light (it is called from the worker's hot loop) and pure.
ACTION_CONFIG_KEYS: dict[str, frozenset[str]] = {
    "apply_tag": frozenset({"tag", "target"}),
    "remove_tag": frozenset({"tag", "target"}),
    "raise_task": frozenset(
        {
            "title",
            "description",
            "assign_to",
            "assignee_role_slug",
            "category",
            "priority",
            "due_in_days",
        }
    ),
    "send_email": frozenset({"template_code", "to", "variables"}),
    "assign_adviser": frozenset({"adviser_id", "role"}),
    "set_case_status": frozenset({"status"}),
    "request_document": frozenset({"category", "title", "target_contact"}),
    "generate_document": frozenset({"template_uid", "dataset"}),
    "send_document": frozenset({"document_uid"}),
    "webhook_out": frozenset({"endpoint_key", "payload_keys"}),
    "lookup": frozenset({"endpoint_key", "request_mapping", "response_mapping"}),
    "classify": frozenset({"input_fields", "question", "labels", "remember_as"}),
    "draft_message": frozenset(
        {"input_fields", "brief", "title", "assignee_role_slug", "due_in_days"}
    ),
}

#: Kinds that end the walk. ``exit`` is the explicit terminal; ``start_flow`` is a **tail call**,
#: not a subroutine call — control moves to the other flow and does not come back — so authoring a
#: ``next`` on either is a misunderstanding worth refusing rather than silently dropping.
TERMINAL_KINDS: frozenset[str] = frozenset({"exit", "start_flow"})

#: ``controlNodes.branch.minBranches`` / ``requiresElse``.
MIN_BRANCHES = 2
REQUIRES_ELSE = True

#: ``exit.outcome`` — the terminal states of ``flows.states.State``.
EXIT_OUTCOMES: tuple[str, ...] = ("completed", "cancelled", "failed")

#: caps.* — publish refuses a definition breaching any of them.
MAX_NODES_PER_FLOW = 200
MAX_BRANCHES_PER_NODE = 8
MAX_WAIT_DAYS = 90

#: A structural DoS guard, NOT the contract cap. The contract cap (200) is an authoring limit
#: reported as a publish issue, so a draft that grew too large can still be saved and pruned; this
#: one is the point past which we refuse to walk the document at all, because validating a
#: hundred-thousand-node blob on every autosave is a denial of service with a nice UI on it.
_HARD_NODE_LIMIT = 2_000


# ---------------------------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Branch:
    """One numbered outgoing edge of a branch node.

    ``conditions is None`` marks **the else** — the unconditional fallback. An authored empty
    object (``{}``) normalises to ``None`` too: an empty condition is unconditional, and reading it
    any other way would let a branch that looks like a fallback quietly swallow every case from
    wherever it happens to sit in the array.
    """

    id: str
    label: str
    conditions: Mapping[str, Any] | None
    next: str | None

    @property
    def is_else(self) -> bool:
        return self.conditions is None


@dataclass(frozen=True)
class Node:
    """One node of the graph, normalised.

    The first eight fields are the frozen public shape the rest of the B wave builds against. The
    trailing three are additive and carry what a step node needs to stay lossless: ``raw`` is the
    authored object verbatim, so a legacy definition round-trips byte-for-byte through the model.
    """

    id: str
    kind: str
    #: The step type (``kind == "step"``) or action type (``kind == "action"``); ``None`` for a
    #: control node, whose kind is its whole identity.
    type: str | None
    config: Mapping[str, Any]
    #: The outgoing edges of the linear kinds, normalised: a bare ``next`` string is a one-tuple, a
    #: list is preserved in order with duplicates removed, and absent is ``()``. Empty on ``branch``
    #: (its edges are its arms) and on the terminals. **Several ids mean parallel** — every target
    #: becomes available at once. Named ``next_ids`` rather than ``next`` on purpose: the field went
    #: from one value to many, and a call site that still thinks it is reading one should fail to
    #: compile rather than silently read a tuple as truthy.
    next_ids: tuple[str, ...]
    #: ``kind in BRANCHING_KINDS`` only; empty otherwise.
    branches: tuple[Branch, ...]
    #: participant | account | application (steps) — plan-time, unchanged semantics.
    scope: str | None
    #: Plan-time step visibility — unchanged semantics (``planning._visible``).
    visibility: Mapping[str, Any] | None
    #: Authored prerequisites. Still meaningful in v3: an edge says "after", ``depends_on`` says
    #: "not until ALL of these", which is the join a single edge cannot express.
    depends_on: tuple[str, ...] = ()
    title: str = ""
    #: The authored node object, verbatim. The lossless-round-trip guarantee lives here.
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_step(self) -> bool:
        """Does this node become a planned step row? True for human work AND engine work.

        Both kinds are planned, instantiated and given a status, because a status is where a
        failure lives and what an operator retries; what differs is which handler runs and whether
        anyone is asked for anything (``is_action``). Control nodes plan nothing.
        """
        return self.kind in PLANNED_KINDS

    @property
    def is_action(self) -> bool:
        """Unattended engine work — ``kind: "action"``, ``type`` from the contract's actionTypes.

        The kind IS the answer. It used to be inferred (a step-kind node whose type was not a step
        type), which meant the automated ``generate_document`` could not be authored at all: the
        name resolved to the staff step first, and nothing anywhere said so. The contract settled
        the ambiguity with an explicit field, so this reads the field.
        """
        return self.kind == ACTION_KIND

    @property
    def label(self) -> str:
        """What to call this node in a message to an author."""
        if self.title.strip():
            return self.title.strip()
        if self.kind in CONTROL_LABELS:
            return CONTROL_LABELS[self.kind]
        return self.id


@dataclass(frozen=True)
class Graph:
    """A parsed flow definition. ``nodes`` preserves authored order (dicts do)."""

    nodes: Mapping[str, Node]
    #: Where a case starts. ``None`` only for an empty graph.
    entry: str | None
    #: True when the definition was authored in the pre-v3 ``{steps: [...]}`` shape. Consumers MUST
    #: check it before treating edges as execution order — see the module docstring's warning.
    is_legacy_linear: bool

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(self.nodes)

    @property
    def start_id(self) -> str | None:
        """The authored ``start`` node, if the definition has one. At most one exists (``parse``
        refuses a second), and when it does it is also ``entry``."""
        return next((nid for nid, n in self.nodes.items() if n.kind == START_KIND), None)

    def steps(self) -> tuple[Node, ...]:
        return tuple(n for n in self.nodes.values() if n.is_step)


# ---------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------


def _require_mapping(value: Any, message: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DefinitionError(message)
    return value


def _slug_id(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _SLUG.match(value):
        raise DefinitionError(f"{what} needs a slug id (a-z0-9-_)")
    return value


def _opt_str(value: Any, message: str) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise DefinitionError(message)
    return value


def _next_ids(node_id: str, raw: Any) -> tuple[str, ...]:
    """Normalise the ``next`` key to a tuple of node ids (the contract's ``$comment_nextArity``).

    Three authored spellings, one reading: ``null``/absent ⇒ ``()``, a string ⇒ a one-tuple, a list
    ⇒ that list in order with blanks dropped and duplicates removed. A duplicate is de-duplicated
    rather than refused because "go to X, and also go to X" is a redundant wire, not a
    contradiction; anything that is not a string IS refused, because a number or an object in an
    edge list is a document the author cannot have meant.
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        target = raw.strip()
        return (target,) if target else ()
    if isinstance(raw, list):
        out: list[str] = []
        for entry in raw:
            if not isinstance(entry, str):
                raise DefinitionError(f"node '{node_id}' next must be node ids")
            target = entry.strip()
            if target:
                out.append(target)
        return _dedupe(out)
    raise DefinitionError(f"node '{node_id}' next must be a node id, a list of node ids, or null")


#: The default label for each of a condition's arms. A branch numbers its arms ("Branch 2"); a
#: condition asks one question, so its arms are the two answers to it.
_CONDITION_ARM_LABELS: dict[str, str] = {CONDITION_TRUE: "Yes", CONDITION_FALSE: "No"}


def _parse_branches(node_id: str, raw_branches: Any, *, kind: str = "branch") -> tuple[Branch, ...]:
    what = CONTROL_LABELS[kind].lower()
    if not isinstance(raw_branches, list):
        raise DefinitionError(f"{what} '{node_id}' needs a list of branches in its config")
    out: list[Branch] = []
    seen: set[str] = set()
    for i, b in enumerate(raw_branches):
        if not isinstance(b, Mapping):
            raise DefinitionError(f"{what} '{node_id}' branch {i} must be an object")
        bid = _slug_id(b.get("id"), f"{what} '{node_id}' branch {i}")
        if bid in seen:
            raise DefinitionError(f"{what} '{node_id}' has two branches called '{bid}'")
        seen.add(bid)
        raw_conditions = b.get("conditions")
        if raw_conditions is not None and not isinstance(raw_conditions, Mapping):
            raise DefinitionError(
                f"{what} '{node_id}' branch '{bid}' conditions must be an object or null"
            )
        # An empty condition object is unconditional, so it IS the else (see Branch's docstring).
        cond: Mapping[str, Any] | None = (
            dict(raw_conditions) if isinstance(raw_conditions, Mapping) and raw_conditions else None
        )
        label = b.get("label")
        if label is not None and not isinstance(label, str):
            raise DefinitionError(f"{what} '{node_id}' branch '{bid}' label must be text")
        default_label = (
            _CONDITION_ARM_LABELS.get(bid, "Otherwise" if cond is None else bid)
            if kind == CONDITION_KIND
            else ("Otherwise" if cond is None else f"Branch {i + 1}")
        )
        out.append(
            Branch(
                id=bid,
                label=(label or "").strip() or default_label,
                conditions=cond,
                next=_opt_str(
                    b.get("next"), f"{what} '{node_id}' branch '{bid}' next must be a node id"
                ),
            )
        )
    return tuple(out)


def _parse_node(index: int, raw_node: Any) -> Node:
    node = _require_mapping(raw_node, f"node {index} must be an object")
    nid = _slug_id(node.get("id"), f"node {index}")

    kind = node.get("kind") or STEP_KIND
    if not isinstance(kind, str) or kind not in NODE_KINDS:
        raise DefinitionError(
            f"node '{nid}' has an unknown kind '{node.get('kind')}' — "
            f"one of {', '.join(sorted(NODE_KINDS))}"
        )

    config_raw = node.get("config", {})
    config = _require_mapping(config_raw, f"node '{nid}' config must be an object")

    if kind == ACTION_KIND:
        atype = node.get("type")
        if not isinstance(atype, str) or atype not in ACTION_CONFIG_KEYS:
            raise DefinitionError(
                f"action node '{nid}' has an unknown action '{atype}' — "
                f"one of {', '.join(sorted(ACTION_CONFIG_KEYS))}"
            )
        unknown = set(config) - ACTION_CONFIG_KEYS[atype]
        if unknown:
            raise DefinitionError(
                f"action node '{nid}' ({atype}) has unknown config keys: "
                f"{', '.join(sorted(unknown))}"
            )
    elif kind != STEP_KIND:
        unknown = set(config) - CONTROL_CONFIG_KEYS[kind]
        if unknown:
            raise DefinitionError(
                f"{CONTROL_LABELS[kind].lower()} node '{nid}' has unknown config keys: "
                f"{', '.join(sorted(unknown))}"
            )

    branches: tuple[Branch, ...] = ()
    if kind in BRANCHING_KINDS:
        if "branches" in node:
            # Caught rather than merged: two places for the same list is how one of them goes stale.
            raise DefinitionError(
                f"{CONTROL_LABELS[kind].lower()} '{nid}' must carry its branches in its config, "
                "not on the node"
            )
        branches = _parse_branches(nid, config.get("branches", []), kind=kind)

    nxt = _next_ids(nid, node.get("next"))
    if nxt and (kind in TERMINAL_KINDS or kind in BRANCHING_KINDS):
        why = (
            "its branches carry its outgoing edges"
            if kind in BRANCHING_KINDS
            else "control does not come back from it"
        )
        raise DefinitionError(
            f"{CONTROL_LABELS[kind].lower()} node '{nid}' cannot have a next — {why}"
        )

    visibility = node.get("visibility")
    if visibility is not None and not isinstance(visibility, Mapping):
        raise DefinitionError(f"node '{nid}' visibility must be an object or null")
    scope = node.get("scope")
    if scope is not None and not isinstance(scope, str):
        raise DefinitionError(f"node '{nid}' scope must be text or null")
    depends = node.get("depends_on", [])
    if not isinstance(depends, list) or not all(isinstance(d, str) for d in depends):
        raise DefinitionError(f"node '{nid}' depends_on must be a list of node ids")
    if kind == START_KIND and depends:
        # A start node has no prerequisites by construction — it IS what everything else waits on.
        # Refused rather than dropped: an author who wrote one has misunderstood the node, and a
        # silently ignored key is how that misunderstanding survives into a live journey.
        raise DefinitionError(f"start node '{nid}' cannot wait for anything")
    title = node.get("title")
    if title is not None and not isinstance(title, str):
        raise DefinitionError(f"node '{nid}' title must be text")

    # A control node's `type` (if the builder echoes its kind there) is meaningless — the kind is
    # the discriminator. Dropped rather than refused: it costs nothing and refusing it would make
    # a harmless editor habit a 422.
    ntype = node.get("type") if kind in PLANNED_KINDS else None
    if kind == STEP_KIND and ntype is not None and not isinstance(ntype, str):
        raise DefinitionError(f"node '{nid}' type must be text")

    return Node(
        id=nid,
        kind=kind,
        type=ntype,
        config=dict(config),
        next_ids=nxt,
        branches=branches,
        scope=scope,
        visibility=dict(visibility) if isinstance(visibility, Mapping) else None,
        depends_on=tuple(d for d in depends if isinstance(d, str)),
        title=title or "",
        raw=dict(node),
    )


def _entry_for(nodes: Mapping[str, Node], authored: Any) -> str | None:
    """The node a case starts at.

    Three rules, in this order, and the order is the whole back-compatibility story:

    1. **a ``start`` node wins.** It is the only explicit statement of where a case begins, and it
       is the one an author drew. ``entry`` is a *derived* key — ``validate_definition`` writes it
       into canonical output on every save and reads it back on the next one — so an authored
       ``entry`` naming something else is a stale echo of the rule below, not a competing decision.
       It is still validated (a name that is not a node is a broken document either way).
    2. the authored ``entry``, for a v3 definition with no start node;
    3. else the first node nothing points at, else simply the first node (a fully cyclic graph has
       no such node, and reporting the cycle is ``graph_issues``' job, not a reason to refuse to
       parse).
    """
    if not nodes:
        return None
    if authored is not None and (not isinstance(authored, str) or authored not in nodes):
        raise DefinitionError(f"entry '{authored}' is not a node in this flow")
    start = next((nid for nid, n in nodes.items() if n.kind == START_KIND), None)
    if start is not None:
        return start
    if authored is not None:
        return str(authored)
    pointed_at = {t for nid in nodes for t in _outgoing(nodes[nid])}
    return next((nid for nid in nodes if nid not in pointed_at), next(iter(nodes)))


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    """Order-preserving de-duplication (two branches may re-converge on one node)."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return tuple(out)


def _outgoing(node: Node) -> tuple[str, ...]:
    """Every node id this node names as a successor, in authored order, duplicates removed.

    Dangling ids are returned as authored — ``graph_issues`` is what reports them; a traversal that
    silently dropped them would make a broken edge invisible.
    """
    ids: list[str] = []
    if node.kind in BRANCHING_KINDS:
        ids.extend(b.next for b in node.branches if b.next)
    else:
        ids.extend(node.next_ids)
        if node.kind == "wait_for_event":
            timeout = node.config.get("timeout_branch")
            if isinstance(timeout, str) and timeout.strip():
                ids.append(timeout.strip())
    return _dedupe(ids)


def _parse(raw: Mapping[str, Any]) -> Graph:
    definition = _require_mapping(raw, "definition must be an object")
    raw_nodes = definition.get("nodes")
    legacy = raw_nodes is None
    listed = definition.get("steps", []) if legacy else raw_nodes
    if not isinstance(listed, list):
        raise DefinitionError("steps must be a list" if legacy else "nodes must be a list")
    if len(listed) > _HARD_NODE_LIMIT:
        raise DefinitionError(
            f"this flow has {len(listed)} nodes, which is more than we will read "
            f"({_HARD_NODE_LIMIT}). The publishable limit is {MAX_NODES_PER_FLOW}."
        )

    nodes: dict[str, Node] = {}
    for i, raw_node in enumerate(listed):
        node = _parse_node(i, raw_node)
        if node.id in nodes:
            raise DefinitionError(f"duplicate node id '{node.id}'")
        if legacy and node.kind != STEP_KIND:
            # `steps` is the legacy key and it means steps. A control node there is a v3 document
            # that forgot to rename its array, and reading it as legacy would silently give it
            # legacy semantics (no branching at all).
            raise DefinitionError(
                f"'{node.id}' is a {node.kind} node — only human steps belong in “steps”; "
                "action and control nodes belong in “nodes”"
            )
        nodes[node.id] = node

    if legacy:
        # The linear chain: presentation order only. See the module docstring's warning — nothing
        # derives execution order from these edges for a legacy graph.
        ordered = list(nodes)
        nodes = {
            nid: (node if i + 1 >= len(ordered) else replace(node, next_ids=(ordered[i + 1],)))
            for i, (nid, node) in enumerate(nodes.items())
        }
        entry = ordered[0] if ordered else None
    else:
        starts = [nid for nid, node in nodes.items() if node.kind == START_KIND]
        if len(starts) > MAX_START_NODES:
            raise DefinitionError(
                "this flow has "
                + " and ".join(f"'{nid}'" for nid in starts)
                + " as starting points — a journey begins in one place. "
                "Delete all but one and wire the rest of the graph to it."
            )
        entry = _entry_for(nodes, definition.get("entry"))

    return Graph(nodes=nodes, entry=entry, is_legacy_linear=legacy)


def parse(raw: Mapping[str, Any]) -> Graph:
    """Normalise + structurally validate an authored definition into a ``Graph``.

    Accepts both wire shapes (see the module docstring). Raises ``DefinitionError`` — and **only**
    ``DefinitionError`` — for anything that is not a readable graph document: a bad id, an unknown
    kind, a duplicate, branches in the wrong place, an edge on a terminal node. Softer problems
    (a dangling edge, an orphan, a missing else, a breached cap) are *publish* issues rather than
    save-time refusals, because the editor autosaves and a half-built canvas has all of them.

    The blanket catch is the point: this is called from the router on every save and from the
    worker on stored JSON, and the caller's contract is "one exception type, always".
    """
    try:
        return _parse(raw)
    except DefinitionError:
        raise
    except RecursionError as exc:  # a pathologically nested document
        raise DefinitionError("this flow is nested too deeply to read") from exc
    except Exception as exc:
        raise DefinitionError(f"this flow could not be read ({type(exc).__name__})") from exc


# ---------------------------------------------------------------------------------------------
# Traversal (pure — the runtime half)
# ---------------------------------------------------------------------------------------------


def successors(graph: Graph, node_id: str) -> tuple[str, ...]:
    """Every node id reachable in one hop from ``node_id`` — branch targets and a
    ``wait_for_event``'s timeout target included.

    Structural, not a decision: this is what reachability, cycle detection and the plan-time
    dependency compiler walk. To ask which ONE successor a case actually takes, call ``next_node``.
    """
    node = graph.nodes.get(node_id)
    return _outgoing(node) if node is not None else ()


def next_nodes(graph: Graph, node_id: str, ctx: Mapping[str, Any]) -> tuple[str, ...]:
    """Which successors this case takes now, given the context. Empty for "nowhere" (a terminal
    node, an unknown node, or a branch with no takeable edge).

    **Several ids mean parallel** — the runtime is expected to enter all of them (the contract's
    ``$comment_nextArity``). Only a fan-out ``next`` produces more than one; a branch is exclusive
    and returns at most one, which is why ``branch_choice`` exists alongside this for the callers
    that need to know *which arm* rather than *where next*.

    A **branch** — and a **condition**, which is one — is resolved by evaluating its arms in array
    order through ``conditions.evaluate``, first satisfiable wins, falling back to the else. The
    else is skipped during the ordered pass and only consulted at the end, so it behaves as a
    fallback wherever the author happened to leave it in the list — the alternative (an else in
    the middle swallowing every case, because an empty condition evaluates True) would be a
    silent, position-dependent surprise.

    A **legacy** graph always returns ``()``: it authored no control flow, and its ordering is
    ``depends_on``, resolved by ``dependencies.unmet`` exactly as before. This is the structural
    guard, not a convention someone has to remember.
    """
    if graph.is_legacy_linear:
        return ()
    node = graph.nodes.get(node_id)
    if node is None or node.kind in TERMINAL_KINDS:
        return ()
    if node.kind in BRANCHING_KINDS:
        chosen = branch_choice(graph, node_id, ctx)
        return (chosen,) if chosen else ()
    return node.next_ids


def branch_choice(graph: Graph, node_id: str, ctx: Mapping[str, Any]) -> str | None:
    """The one arm a branch (or condition) sends this case down, as a target node id — ``None`` for
    none takeable.

    Separate from ``next_nodes`` because the callers that resolve a branch need the *decision* —
    the runtime records it on the case (``cases.data.graph.visited``, which is what the arm
    distribution in ``analytics`` reads) and the dry run reports which arm was taken. Folding it
    into a one-element tuple and unpacking it again at four call sites would be the same value
    dressed as a collection.
    """
    if graph.is_legacy_linear:
        return None
    node = graph.nodes.get(node_id)
    if node is None or node.kind not in BRANCHING_KINDS:
        return None
    fallback: Branch | None = None
    for branch in node.branches:
        if branch.is_else:
            fallback = fallback or branch
            continue
        if conditions.evaluate(branch.conditions, ctx):
            return branch.next
    return fallback.next if fallback is not None else None


def _predecessors(graph: Graph) -> dict[str, list[str]]:
    """Who points at each node, in authored order. Built from ``_outgoing`` — so a branch's arms and
    a ``wait_for_event``'s timeout count, and a fan-out's several targets each name it."""
    predecessors: dict[str, list[str]] = {nid: [] for nid in graph.nodes}
    for nid, node in graph.nodes.items():
        for target in _outgoing(node):
            if target in predecessors:
                predecessors[target].append(nid)
    return predecessors


def effective_depends_on(graph: Graph) -> dict[str, tuple[str, ...]]:
    """The plan-time dependency map: for each **step** node, the step ids that must settle first.

    THIS is what compiles a graph into the existing execution machinery, and it is the function to
    reach for — never ``successors`` — when deriving ordering, because it is legacy-safe by
    construction:

    * **legacy** ⇒ the authored ``depends_on``, verbatim. No edge-derived additions, ever.
    * **v3** ⇒ the authored ``depends_on`` union the nearest step *ancestors* reached by walking
      predecessor edges back through control nodes. A branch node is not a step, so both of its
      arms depend on whatever fed the branch; which arm is actually released is ``branch_choice``'s
      decision at runtime, not a dependency.

    Control nodes get no entry — nothing is planned for them.

    **Fan-out needed no change here**, which is worth saying out loud because it is the part of the
    arity rule that looks like it should have. The predecessor map is built from ``_outgoing``,
    which was already a list, and a node with several predecessors was already handled — so
    ``next: [a, b]`` gives *both* targets the fan-out node as their step ancestor and both are
    released when it settles, which is exactly parallel. The corollary is the one to remember: an
    edge means "after", never "instead of", so ordering *between* fanned arms is still
    ``depends_on`` and nothing else. And ``start`` is not a step, so a root fanned out from it
    walks back through it to no ancestor at all and keeps the empty prerequisite list it had as a
    legacy root.
    """
    if graph.is_legacy_linear:
        return {nid: node.depends_on for nid, node in graph.nodes.items()}

    predecessors = _predecessors(graph)

    def step_ancestors(nid: str) -> tuple[str, ...]:
        """Walk back to the first step node on every incoming path (cycle-safe)."""
        found: list[str] = []
        seen: set[str] = {nid}
        queue: list[str] = list(predecessors.get(nid, ()))
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            node = graph.nodes.get(current)
            if node is None:
                continue
            if node.is_step:
                found.append(current)
            else:
                queue.extend(predecessors.get(current, ()))
        return tuple(found)

    out: dict[str, tuple[str, ...]] = {}
    for nid, node in graph.nodes.items():
        if not node.is_step:
            continue
        merged = list(node.depends_on)
        merged.extend(d for d in step_ancestors(nid) if d not in merged)
        out[nid] = tuple(merged)
    return out


# ---------------------------------------------------------------------------------------------
# The legacy upgrade
# ---------------------------------------------------------------------------------------------


def _free_id(taken: Iterable[str], base: str) -> str:
    """A slug for a minted node that no authored node has already claimed. Mirrors
    ``nodes.ts::mintNodeId``: ids are authored keys (they appear in ``depends_on``, in a milestone's
    ``reached_when`` and in ``case_steps.step_key`` for every case ever run), so a readable one
    beats
    a surrogate the moment anybody has to read a database row or a publish issue."""
    used = set(taken)
    if base not in used:
        return base
    n = 2
    while f"{base}_{n}" in used:
        n += 1
    return f"{base}_{n}"


def canonical_next(ids: Sequence[str]) -> str | list[str] | None:
    """The wire spelling of a set of outgoing edges: ``null`` for none, a bare string for one, a
    list for several.

    Canonical because a one-element list and a bare string mean the same thing and a document that
    can say it two ways is a document that churns: every v3 definition authored before the arity
    rule carries ``next: "b"``, and re-writing them all as ``["b"]`` on the next save would be a
    diff across every stored flow for no change in meaning. ``nodes.ts`` serialises by the same
    rule, which is what lets the two sides be compared byte-for-byte.
    """
    if not ids:
        return None
    if len(ids) == 1:
        return ids[0]
    return list(ids)


def upgrade_legacy(definition: Mapping[str, Any]) -> dict[str, Any]:
    """A pre-v3 ``{steps}`` definition's PREREQUISITES, said as wires behind a ``start`` node.

    THE RULE (the contract's ``$comment_legacyUpgrade``; ``convert.ts::wireFromPrerequisites``
    mirrors it):

    * mint one ``start`` node, fanning out to every step with no ``depends_on``, in document order;
    * wire every other step FROM each of its ``depends_on`` entries, so a step with two
      prerequisites is a target of both;
    * keep each step's authored ``depends_on`` verbatim.

    The result is *behaviour-identical*, not merely lossless: ``effective_depends_on`` walks those
    predecessor edges back to the nearest step ancestors and reproduces exactly the authored
    prerequisites, so planning fans the same cases and the same instances. That is what makes it a
    faithful statement of the wiring rather than an invention.

    What it deliberately does NOT do is wire the steps in array order. That order was the pre-v3
    editor's *presentation*, inferred at render time and stored nowhere; compiling it into edges
    would turn five terms declarations a client was offered together into five they are offered one
    at a time, and it would look like a lossless upgrade the whole way. The prerequisites are the
    only ordering the document actually states, so they are the only ordering that becomes wires.

    It is the first half of ``convert_legacy`` (``visibility_to_conditions`` is the second) and NOT
    what merely READING a legacy document does — ``nodes.ts::toNodes`` invents no edges and no start
    node, because a legacy document has neither. Reading is not converting.
    """
    read = _nodes_from_steps(definition)
    raw_steps = [
        n
        for n in read.get("nodes") or []
        if isinstance(n, Mapping) and n.get("kind", STEP_KIND) == STEP_KIND
    ]
    if not raw_steps:
        return {"nodes": [], "milestones": list(definition.get("milestones") or [])}

    steps = [dict(s) for s in raw_steps]
    known = {str(s.get("id")) for s in steps}
    followers: dict[str, list[str]] = {sid: [] for sid in known}
    roots: list[str] = []
    for step in steps:
        sid = str(step.get("id"))
        deps = [d for d in (step.get("depends_on") or []) if isinstance(d, str) and d in known]
        if not deps:
            roots.append(sid)
        for dep in deps:
            followers[dep].append(sid)

    start_id = _free_id(known, START_KIND)
    nodes: list[dict[str, Any]] = [
        {
            "id": start_id,
            "kind": START_KIND,
            "title": CONTROL_LABELS[START_KIND],
            "config": {},
            "position": None,
            "depends_on": [],
            "next": canonical_next(roots),
        }
    ]
    for step in steps:
        sid = str(step.get("id"))
        nodes.append({**step, "kind": STEP_KIND, "next": canonical_next(_dedupe(followers[sid]))})
    out: dict[str, Any] = {"nodes": nodes, "milestones": list(definition.get("milestones") or [])}
    if definition.get("intake") is not None:
        out["intake"] = definition["intake"]
    return out


def condition_arms(
    conditions: Mapping[str, Any], *, when: str | None, otherwise: str | None
) -> list[dict[str, Any]]:
    """A condition node's two arms, in the canonical order and spelling.

    One minting point in each language (``convert.ts::conditionArms`` is the other) so a converted
    document is byte-identical across them, and so the seed, the converter and the builder cannot
    disagree about what a condition looks like on the wire.
    """
    return [
        {
            "id": CONDITION_TRUE,
            "label": _CONDITION_ARM_LABELS[CONDITION_TRUE],
            "conditions": dict(conditions),
            "next": when,
        },
        {
            "id": CONDITION_FALSE,
            "label": _CONDITION_ARM_LABELS[CONDITION_FALSE],
            "conditions": None,
            "next": otherwise,
        },
    ]


def _repoint(node: Mapping[str, Any], moved: Mapping[str, str]) -> dict[str, Any]:
    """One node's outgoing edges, with every target that has gained a gate aimed at the gate."""
    out = dict(node)
    if "next" in out:
        out["next"] = canonical_next(
            _dedupe(moved.get(t, t) for t in _next_ids(str(node.get("id")), out.get("next")))
        )
    config = out.get("config")
    if not isinstance(config, Mapping):
        return out
    branches = config.get("branches")
    if isinstance(branches, list):
        out["config"] = {
            **config,
            "branches": [
                {**b, "next": moved.get(str(b.get("next")), b.get("next"))}
                if isinstance(b, Mapping)
                else b
                for b in branches
            ],
        }
    return out


def visibility_to_conditions(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Turn every work node's plan-time ``visibility`` into a real ``condition`` node that TAKES ITS
    PLACE in the graph.

    THE RULE (the contract's ``$comment_conditionUpgrade``;
    ``convert.ts::conditionsFromVisibility`` mirrors it): for each work node carrying a condition,
    mint a ``condition`` node whose ``true`` arm holds that condition verbatim and points at the
    node, whose ``false`` arm points at wherever the node itself led (its single successor, or
    nowhere when it has none or several), and re-aim every edge that pointed at the node so it
    points at the condition instead. Then clear the node's own ``visibility``.

    **Taking its place is the point.** A condition is control flow now: it is evaluated when a case
    walks into it, so it has to sit ON the path rather than beside it, and the ``false`` arm is what
    the case does when the work does not apply — which in a wired journey is "carry on to whatever
    came next". A skip that named nothing would silently end the journey there.

    The false arm is left unwired when the node fans out to several successors: an arm is
    single-valued by contract, and guessing one of several would drop the others. Publish says so
    and the author draws the wire — a refusal an author can act on beats a silent choice.

    A dropped condition would silently change who a step applies to, and in onboarding that means
    asking a client for things they should never have been asked for: an offshore KYC form for an
    onshore account, a SIPP declaration for an ISA. So it is TRANSFORMED, never copied — leaving it
    on the step as well would gate the same work twice, in two places, one of them unauthorable.
    """
    nodes_in = definition.get("nodes")
    if not isinstance(nodes_in, list):
        return dict(definition)

    nodes = [dict(n) for n in nodes_in if isinstance(n, Mapping)]
    taken = {str(n.get("id")) for n in nodes}
    # Every gate id first, so re-aiming an incoming edge knows the name of a gate minted later in
    # the document. Document order, so both languages mint the same ids.
    gates: dict[str, str] = {}
    for node in nodes:
        condition = node.get("visibility")
        if node.get("kind", STEP_KIND) in PLANNED_KINDS and isinstance(condition, Mapping):
            gate_id = _free_id(taken, f"{str(node.get('id'))}_when")
            taken.add(gate_id)
            gates[str(node.get("id"))] = gate_id
    if not gates:
        return dict(definition)

    out: list[dict[str, Any]] = []
    for raw in nodes:
        nid = str(raw.get("id"))
        node = _repoint(raw, gates)
        minted = gates.get(nid)
        if minted is None:
            out.append(node)
            continue
        onward = _next_ids(nid, node.get("next"))
        out.append(
            {
                "id": minted,
                "kind": CONDITION_KIND,
                # Minted identically in both languages so a converted document can be compared
                # byte-for-byte across them. The condition's own SENTENCE is presentation and each
                # surface writes its own (the canvas runs it through the automations summariser).
                "title": f"{_ONLY_WHEN} · {raw.get('title') or nid}",
                "config": {
                    "branches": condition_arms(
                        raw["visibility"],
                        when=nid,
                        otherwise=onward[0] if len(onward) == 1 else None,
                    )
                },
                "position": None,
                "depends_on": [],
            }
        )
        out.append({**node, "visibility": None})
    return {**definition, "nodes": out}


def _nodes_from_steps(definition: Mapping[str, Any]) -> dict[str, Any]:
    """A legacy ``{steps}`` document read as ``{nodes}``, inventing nothing — the Python twin of
    ``nodes.ts::toNodes``. Step nodes, ``next: null``, ``depends_on`` verbatim: a legacy definition
    has no edges of its own, and its ordering is the tier plus the prerequisites."""
    if definition.get("nodes") is not None:
        return dict(definition)
    raw_steps = definition.get("steps") or []
    steps = [dict(s) for s in raw_steps if isinstance(s, Mapping)]
    out: dict[str, Any] = {
        "nodes": [{**s, "kind": STEP_KIND, "next": None} for s in steps],
        "milestones": list(definition.get("milestones") or []),
    }
    if definition.get("intake") is not None:
        out["intake"] = definition["intake"]
    return out


def convert_legacy(definition: Mapping[str, Any]) -> dict[str, Any]:
    """A pre-v3 ``{steps}`` document, converted to a wired ``{nodes}`` graph.

    Two rules, in order, and each is stated (and pinned) on its own: the prerequisites become wires
    behind a ``start`` node (``upgrade_legacy``), and then each step's ``visibility`` becomes a
    two-armed ``condition`` on the path in front of it (``visibility_to_conditions``).

    The order matters — the gate's ``false`` arm is "carry on to whatever came next", so the wires
    have to exist before the gates are minted. And the wiring has to happen at all: a condition is
    control flow, and control cannot reach it in a document with no edges.

    The canonical statement of what the builder does when an author reaches for something the old
    shape cannot express (``convert.ts::convertLegacy``), pinned across the two languages by the
    contract's ``legacyUpgradeExample`` and ``conditionUpgradeExample``.
    """
    read = _nodes_from_steps(definition)
    # Wired already ⇒ leave the wiring alone, so a second pass changes nothing (``convert.ts``
    # mirrors this). Re-running the prerequisite rule over a converted document would add edges
    # BESIDE the ones it just minted, including ones that bypass its conditions.
    wired = (
        read
        if any(_outgoing(n) for n in _parse(read).nodes.values())
        else upgrade_legacy(definition)
    )
    return visibility_to_conditions(wired)


# ---------------------------------------------------------------------------------------------
# Publish preflight
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Issue:
    """One publish refusal, and the node it is about.

    The MESSAGE is the thing an author reads and has always been the whole payload; ``node_id`` is
    the addition v3's builder needs. A sentence names a node by its ``label`` (its title, falling
    back to its kind) because that is what the author called it — which is good copy and a hopeless
    key: two nodes may share a title and an untitled one is just "Branch". So the id rides
    alongside, and the builder badges on the id rather than matching the text.

    ``node_id is None`` means the issue is about the flow as a whole (no entry node, a breached
    cap, an unreadable document). An issue spanning several nodes — an orphan set, a cycle — is
    emitted ONCE PER NODE with the same sentence, so every node in it badges; the string-list view
    de-duplicates by message, which is what keeps ``graph_issues`` byte-identical to what it always
    returned.
    """

    node_id: str | None
    message: str


def _dedupe_issues(issues: Iterable[Issue]) -> list[Issue]:
    """De-duplicate on (node, message) in document order. The same sentence may legitimately land on
    two different nodes (a cycle); the same sentence twice on one node never should."""
    seen: set[tuple[str | None, str]] = set()
    out: list[Issue] = []
    for issue in issues:
        key = (issue.node_id, issue.message)
        if key not in seen:
            seen.add(key)
            out.append(issue)
    return out


def messages(issues: Iterable[Issue]) -> list[str]:
    """The string view: messages in document order, de-duplicated. This is the shape every existing
    caller has always received, and the pin test compares it verbatim."""
    return list(_dedupe(issue.message for issue in issues))


def _prefixed(issues: Sequence[str], label: str) -> list[str]:
    return [f"{label}: {issue}" for issue in issues]


def _adder(node: Node, issues: list[Issue]) -> Any:
    """An ``append`` bound to one node — the per-kind helpers say what is wrong, never who it is
    about, so a message can never be filed against the wrong node by a copy-paste."""

    def add(message: str) -> None:
        issues.append(Issue(node.id, message))

    return add


def _number(value: Any) -> float | None:
    """A non-negative finite number, or None. Booleans are not numbers (``True`` days is a typo)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _branch_issues(node: Node, known: frozenset[str], issues: list[Issue]) -> None:
    label = f"Branch “{node.label}”"
    add = _adder(node, issues)
    if len(node.branches) < MIN_BRANCHES:
        add(f"{label} needs at least {MIN_BRANCHES} branches.")
    if len(node.branches) > MAX_BRANCHES_PER_NODE:
        add(
            f"{label} has {len(node.branches)} branches — the most allowed is "
            f"{MAX_BRANCHES_PER_NODE}. Split it into two branches."
        )
    elses = [b for b in node.branches if b.is_else]
    if REQUIRES_ELSE and not elses:
        # The rule the whole node exists to make safe: a fan-out with no fallback is how a client
        # silently falls off the end of a process, with nothing anywhere saying so.
        add(
            f"{label} has no “otherwise” branch. Every branch needs one unconditional fallback, "
            "or a case that matches nothing stops here."
        )
    if len(elses) > 1:
        add(
            f"{label} has {len(elses)} “otherwise” branches — only the first could ever be taken. "
            "Give the others conditions, or remove them."
        )
    # A branch that routes NOWHERE decides nothing, and is almost always half-finished. But an
    # individual arm leading nowhere is legitimate, and common: gate a tier-driven step with a
    # condition and its "otherwise" arm means "skip it", which in a tier has nothing to continue
    # to — the tier just gets on with its other work. That is exactly the shape converting a
    # legacy flow's `visibility` produces for every step nothing depends on, so refusing it
    # per-arm made eleven of the onboarding flow's twelve converted conditions unpublishable.
    # The check that keeps its teeth is the dangling one below: an arm naming a node that is not
    # here is always a mistake.
    if node.branches and not any(branch.next for branch in node.branches):
        add(f"{label} leads nowhere at all — give at least one of its paths somewhere to go.")
    for branch in node.branches:
        if branch.next and branch.next not in known:
            add(
                f"{label}: the “{branch.label}” branch leads to “{branch.next}”, "
                "which is not in this flow."
            )
        for issue in _prefixed(
            conditions.condition_issues(branch.conditions), f"{label} → {branch.label}"
        ):
            add(issue)


def _condition_issues(node: Node, known: frozenset[str], issues: list[Issue]) -> None:
    """A ``condition`` is a two-armed branch, so what publish asks of it is a branch's questions in
    the shape this node fixes: exactly two arms, ``true`` carrying the test and ``false`` the
    fallback.

    Three refusals, and each one is a case that would otherwise go wrong silently:

    * **no test** — a condition that asks nothing always takes its ``true`` arm, so a step an author
      believes is restricted to individuals is offered to everybody;
    * **the ``true`` arm leads nowhere** — the condition decides nothing at all;
    * **an arm names a node that is not here** — the walk reaches it and the path stops dead.

    The ``false`` arm may legitimately lead nowhere: that is "and otherwise this path is finished",
    which is exactly what a condition on the last piece of work in a journey means. It is the one
    thing here that is not a refusal, and the inspector says so in words rather than leaving the
    author to infer it.
    """
    label = f"Condition “{node.label}”"
    add = _adder(node, issues)
    arms = {b.id: b for b in node.branches}
    if set(arms) != {CONDITION_TRUE, CONDITION_FALSE}:
        # Reachable only from hand-written JSON — the builder, the seed and both converters mint the
        # pair through `condition_arms`. Refused rather than repaired: a third arm is a branch, and
        # silently dropping it would delete an authored path.
        add(
            f"{label} must have exactly two paths, “{CONDITION_TRUE}” and “{CONDITION_FALSE}”. "
            "Use a branch for anything else."
        )
        return
    when, otherwise = arms[CONDITION_TRUE], arms[CONDITION_FALSE]
    if when.conditions is None:
        add(f"{label} has no conditions, so it decides nothing. Write the condition, or remove it.")
    else:
        for issue in _prefixed(conditions.condition_issues(when.conditions), label):
            add(issue)
    if otherwise.conditions is not None:
        add(f"{label}: the “{otherwise.label}” path is the fallback and cannot have conditions.")
    if not when.next:
        add(f"{label} does not say where to go when it holds, so it decides nothing.")
    for arm in node.branches:
        if arm.next and arm.next not in known:
            add(
                f"{label}: the “{arm.label}” path leads to “{arm.next}”, which is not in this flow."
            )


def _start_issues(node: Node, issues: list[Issue]) -> None:
    """A start node that leads nowhere is a journey with no first step — the same failure as a flow
    with no entry at all, said against the node the author can click on."""
    if not node.next_ids:
        _adder(node, issues)(
            f"Start “{node.label}” does not lead anywhere, so nothing would ever begin."
        )


def _wait_issues(node: Node, issues: list[Issue]) -> None:
    label = f"Wait “{node.label}”"
    add = _adder(node, issues)
    days = _number(node.config.get("duration_days"))
    hours = _number(node.config.get("duration_hours"))
    until = node.config.get("until_date_field")
    if node.config.get("duration_days") is not None and days is None:
        add(f"{label} needs a number of days.")
    if node.config.get("duration_hours") is not None and hours is None:
        add(f"{label} needs a number of hours.")
    if until is not None and (not isinstance(until, str) or not until.strip()):
        add(f"{label} needs a field to wait until.")
    elif isinstance(until, str) and until.strip():
        for issue in _prefixed(
            conditions.condition_issues({"field": until.strip(), "operator": "isNotEmpty"}), label
        ):
            add(issue)
    if days is None and hours is None and not isinstance(until, str):
        add(f"{label} does not say how long to wait.")
        return
    if (days or 0) < 0 or (hours or 0) < 0:
        add(f"{label} cannot wait for a negative time.")
    total_days = (days or 0) + (hours or 0) / 24
    if total_days > MAX_WAIT_DAYS:
        add(f"{label} waits longer than {MAX_WAIT_DAYS} days. Use a scheduled automation instead.")


def _wait_for_event_issues(node: Node, known: frozenset[str], issues: list[Issue]) -> None:
    label = f"Wait for event “{node.label}”"
    add = _adder(node, issues)
    trigger = node.config.get("trigger")
    spec = triggers.resolve(trigger.strip()) if isinstance(trigger, str) else None
    if not isinstance(trigger, str) or not trigger.strip():
        add(f"{label} does not say which event to wait for.")
    elif spec is None:
        # A stale key is the failure with no symptom: the case waits forever for an event nobody
        # will ever send. Refuse it where the author can still see why.
        add(f"{label} waits for “{trigger}”, which is not an event the engine knows.")
    else:
        # KNOWN is not the same as DELIVERABLE. A trigger the rule tier can arm may still be one
        # no listener can ever be released by — a schedule is not an event, a domain event has no
        # consumer, and a task-subject trigger has no id a case could be scoped to. The registry
        # owns that answer (triggers.listener_refusal) so publish, timers.arm_listener and the
        # builder's picker cannot drift; without it the author gets no refusal and the case is
        # wedged for ever the first time a client reaches this node.
        refusal = triggers.listener_refusal(spec)
        if refusal:
            add(f"{label} waits for “{spec.label}”, which {refusal}.")
    for issue in _prefixed(conditions.condition_issues(node.config.get("conditions")), label):
        add(issue)
    timeout_days = _number(node.config.get("timeout_days"))
    if node.config.get("timeout_days") is not None and (timeout_days is None or timeout_days <= 0):
        add(f"{label} needs a positive number of days before it times out.")
    elif timeout_days is not None and timeout_days > MAX_WAIT_DAYS:
        add(f"{label} waits longer than {MAX_WAIT_DAYS} days before timing out.")
    timeout_branch = node.config.get("timeout_branch")
    if timeout_branch is not None:
        if not isinstance(timeout_branch, str) or timeout_branch.strip() not in known:
            add(f"{label}: the timeout leads to “{timeout_branch}”, which is not in this flow.")
        elif timeout_days is None:
            add(
                f"{label} has a timeout branch but no deadline, so the branch could never be taken."
            )
    if not node.next_ids and timeout_branch is None:
        add(f"{label} does not lead anywhere.")


def _start_flow_issues(node: Node, issues: list[Issue]) -> None:
    label = f"Start another flow “{node.label}”"
    add = _adder(node, issues)
    code = node.config.get("flow_code")
    if not isinstance(code, str) or not _SLUG.match(code):
        add(f"{label} does not name a flow to start.")
    carry = node.config.get("carry_memory")
    if carry is not None and not isinstance(carry, bool):
        add(f"{label}: “carry memory” must be yes or no.")


def _exit_issues(node: Node, issues: list[Issue]) -> None:
    label = f"End “{node.label}”"
    add = _adder(node, issues)
    outcome = node.config.get("outcome", "completed")
    if outcome not in EXIT_OUTCOMES:
        add(f"{label} has an outcome of “{outcome}” — use {', '.join(EXIT_OUTCOMES)}.")
    reason = node.config.get("reason")
    if reason is not None and not isinstance(reason, str):
        add(f"{label}: the reason must be text.")


# ---------------------------------------------------------------------------------------------
# classify — every label must lead somewhere
# ---------------------------------------------------------------------------------------------
#
# THE RULE, and it is the mandatory-else rule one level up. A `branch` must have an unconditional
# fallback because a case that matches nothing stops there, silently. A `classify` has the same
# failure in a subtler shape: the model returns one of N labels, the author writes arms for three
# of them, and the fourth quietly takes the "otherwise" path alongside every unrelated case. The
# author never sees it, because nothing is wrong — a case went somewhere.
#
# So the else does NOT count as coverage here. Every declared label, INCLUDING the reserved
# `unknown`, needs an arm that names it. `unknown` most of all: "the model could not answer" and
# "the model answered `low_risk`" must not be the same path, and the whole reason the reserved
# label exists is to make the first one visible.
#
# Two refusals, not one: a classify no branch decides on is also refused. A classification that
# nothing routes on is a model call a firm is paying for whose only effect is a memory key — which
# is either a mistake or the beginnings of an AI decision made somewhere the graph cannot show.
#
# The scan is deliberately SYNTACTIC — it collects the values each arm tests the classify's memory
# key against with `equals`/`in`, and asks whether the union covers the set. It does not evaluate
# anything, and it cannot see coverage expressed some other way (a `matches` pattern, a nested `or`
# whose arms are each partially about the key). Those read as "not covered" and are refused; the
# author writes the explicit arm. That is the right way round — an author told to be explicit about
# where a label goes has lost nothing, whereas a clever check that occasionally concludes "covered"
# when it is not would put the failure back exactly where this exists to stop it.


def _classify_labels(node: Node) -> tuple[tuple[str, ...], str | None]:
    """The label set and memory key of a classify node, read tolerantly.

    Imported from ``app/flows/ai.py`` rather than re-parsed here so that "what counts as a label"
    has one implementation — a second reader that disagreed with the executor's would produce a
    graph
    the builder passes and the runtime cannot use. Deferred rather than a module-level import
    because this module is otherwise pure and import-light (it is read on the worker's hot loop),
    and ``ai`` reaches settings and the memory module.

    Returns ``((), None)`` for anything malformed: the authoritative complaint about a broken label
    set is ``effects.preflight`` (which has the wording and the whole config), and two
    modules shouting different sentences about the same mistake is worse than one.
    """
    from app.flows import ai as ai_mod
    from app.flows import memory

    try:
        labels = ai_mod.parse_labels(node.config)
        key = ai_mod.parse_remember_as(node.config)
    except ai_mod.AIRefused:
        return (), None
    # The dotted path a branch condition names it by. ``memory.NAMESPACE`` rather than the literal,
    # so the one place that decides what memory is called stays the one place.
    return labels, f"{memory.NAMESPACE}.{key}"


def _covered_labels(condition: Any, field: str, out: set[str], depth: int = 0) -> None:
    """Values this condition tests ``field`` against with ``equals``/``in``, collected.

    Walks groups of either logic. An ``or`` group's members each genuinely cover their own value;
    an ``and`` group's do too as far as this check cares — ``memory.band equals 'high' AND
    contact.type equals 'individual'`` is still an arm the author has written for ``high``, just a
    narrower one. Over-counting in the ``and`` case is the deliberate direction: this check exists
    to catch a label with NO arm at all, not to prove an arm is always taken.
    """
    if depth > conditions.MAX_GROUP_DEPTH or not isinstance(condition, Mapping):
        return
    members = condition.get("conditions")
    if isinstance(members, list):
        for member in members:
            _covered_labels(member, field, out, depth + 1)
        return
    if str(condition.get("field") or "").strip() != field:
        return
    operator = str(condition.get("operator") or "").strip()
    value = condition.get("value")
    if operator == "equals" and isinstance(value, str):
        out.add(value.strip().lower())
    elif operator == "in":
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list):
            out.update(v.strip().lower() for v in value if isinstance(v, str))


def _classify_issues(graph: Graph, node: Node, issues: list[Issue]) -> None:
    label = f"Classification “{node.label}”"
    add = _adder(node, issues)
    labels, field = _classify_labels(node)
    if not labels or field is None:
        return  # effects.preflight owns the wording for a broken label set

    # A `branch`, deliberately, and not the two-armed `condition`: a classify's label set always
    # includes the reserved `unknown`, so it has three answers at the least and two arms cannot
    # cover them. "Add a branch" is a refusal the author can act on; "this condition has no arm for
    # …" would be one they cannot.
    deciders = [
        other
        for other in _downstream(graph, node.id)
        if graph.nodes[other].kind == "branch"
        and any(_tests_field(branch.conditions, field) for branch in graph.nodes[other].branches)
    ]
    if not deciders:
        add(
            f"{label} writes “{field}”, but no branch after it decides on that value. A "
            "classification exists so a branch can route on it — add one, or remove the "
            "classification."
        )
        return

    for decider_id in deciders:
        decider = graph.nodes[decider_id]
        covered: set[str] = set()
        for branch in decider.branches:
            _covered_labels(branch.conditions, field, covered)
        missing = [name for name in labels if name not in covered]
        if missing:
            add(
                f"{label} can answer “{'”, “'.join(missing)}”, and branch “{decider.label}” has no "
                f"arm for {'that' if len(missing) == 1 else 'those'}. Every answer needs its own "
                "arm — “otherwise” is the fallback for a case nobody expected, not the place a "
                "known answer quietly ends up."
            )


def _tests_field(condition: Any, field: str, depth: int = 0) -> bool:
    """Does this condition mention ``field`` anywhere? Any operator — the question here is only
    which branch is the one deciding on the classification, not how."""
    if depth > conditions.MAX_GROUP_DEPTH or not isinstance(condition, Mapping):
        return False
    members = condition.get("conditions")
    if isinstance(members, list):
        return any(_tests_field(member, field, depth + 1) for member in members)
    return str(condition.get("field") or "").strip() == field


def _downstream(graph: Graph, start: str) -> list[str]:
    """Every node reachable from ``start``, excluding itself. Bounded by the node count, so a
    re-convergence is cheap and a loop (reported separately) cannot spin."""
    seen: set[str] = set()
    queue = list(successors(graph, start))
    while queue:
        current = queue.pop()
        if current in seen or current not in graph.nodes:
            continue
        seen.add(current)
        queue.extend(successors(graph, current))
    seen.discard(start)
    return [nid for nid in graph.nodes if nid in seen]


def _reachability_issues(graph: Graph, issues: list[Issue]) -> None:
    """Refuse a graph with nodes nothing can ever enter — but only for the kinds where that is true.

    **A work node needs no incoming wire, and this is not a leniency.** The engine has always had
    two
    orderings, and a wire is only one of them: the other is the TIER (``scope`` — participant, then
    account behind the KYC gate, then application). ``effective_depends_on`` gives an unwired step
    no
    prerequisites, so ``planning`` creates its instance and it becomes available the moment its tier
    opens. It genuinely runs. Calling it an orphan was the mistake, and an expensive one: it forced
    an author who added a single branch to a twenty-step flow to then wire the other nineteen into a
    chain, and a chain compiles into hard prerequisites — turning work a tier used to offer together
    into work offered one item at a time.

    **A control node must be reached**, because a wire is the ONLY way to enter one: a branch nobody
    points at never evaluates, a condition nobody points at never decides, a wait nobody points at
    never waits, and an exit nobody points at never ends anything. Those are real authoring bugs
    with no tier to fall back on, so they stay refusals — which is also why this is a per-kind rule
    rather than dropping the check.

    A ``condition`` was exempt while it was a plan-time gate (it was read off the step, so nothing
    had to reach it). It is control flow now, so the exemption is gone: a condition nothing reaches
    decides nothing, and the step behind it would be held for ever waiting for a walk.
    """
    if graph.entry is None:
        issues.append(Issue(None, "This flow has no starting point."))
        return
    reached: set[str] = set()
    queue = [graph.entry]
    while queue:
        current = queue.pop()
        if current in reached or current not in graph.nodes:
            continue
        reached.add(current)
        queue.extend(successors(graph, current))
    exempt = PLANNED_KINDS
    orphans = [
        nid for nid, node in graph.nodes.items() if nid not in reached and node.kind not in exempt
    ]
    if orphans:
        message = (
            "Nothing leads to "
            + ", ".join(f"“{graph.nodes[nid].label}”" for nid in orphans)
            + ". Connect them, or remove them."
        )
        # One sentence, filed against EVERY orphan: the string view de-duplicates it back to the
        # single message publish has always shown, while the builder badges each stranded node.
        issues.extend(Issue(nid, message) for nid in orphans)


def _cycle_issues(graph: Graph, issues: list[Issue]) -> None:
    """A loop inside one flow is refused: the engine releases a node when its predecessor settles,
    so a cycle is a case that can never finish. Looping deliberately is what ``start_flow`` is for
    — a tail call whose depth the runtime caps (``safety.maxChainDepth``)."""
    VISITING, DONE = 0, 1
    state: dict[str, int] = {}
    for root in graph.nodes:
        if root in state:
            continue
        # Iterative DFS so a long chain cannot blow the stack on author-supplied JSON.
        stack: list[tuple[str, list[str]]] = [(root, list(successors(graph, root)))]
        state[root] = VISITING
        while stack:
            current, pending = stack[-1]
            if not pending:
                state[current] = DONE
                stack.pop()
                continue
            nxt = pending.pop()
            if nxt not in graph.nodes:
                continue  # a dangling edge — reported separately
            if state.get(nxt) == VISITING:
                on_stack = [n for n, _ in stack]
                loop = [*on_stack[on_stack.index(nxt) :], nxt]
                message = (
                    "These steps lead back to each other in a loop: "
                    + " → ".join(f"“{graph.nodes[n].label}”" for n in loop)
                    + ". Use “Start another flow” to repeat a journey."
                )
                # Every node on the loop is equally at fault and equally where the author will
                # click, so each one carries the sentence (de-duplicated in the string view).
                issues.extend(Issue(nid, message) for nid in _dedupe(loop))
                state[nxt] = DONE  # report each loop once, not once per way into it
            elif nxt not in state:
                state[nxt] = VISITING
                stack.append((nxt, list(successors(graph, nxt))))


def graph_issues_detailed(graph: Graph) -> list[Issue]:
    """``graph_issues``, with each sentence carrying the id of the node it is about.

    THE authoritative producer — ``graph_issues`` is the string projection of this list, so there
    is one set of refusals and one set of words for them. See ``Issue`` for what ``node_id: None``
    means and why a multi-node problem is emitted once per node.
    """
    issues: list[Issue] = []
    if not graph.nodes:
        return issues  # "add at least one step" is publish_issues' message, said once
    if len(graph.nodes) > MAX_NODES_PER_FLOW:
        issues.append(
            Issue(
                None,
                f"This flow has {len(graph.nodes)} nodes — the most allowed is "
                f"{MAX_NODES_PER_FLOW}. Split it into two flows joined by “Start another flow”.",
            )
        )
    known = frozenset(graph.nodes)
    for node in graph.nodes.values():
        add = _adder(node, issues)
        if node.kind == START_KIND:
            _start_issues(node, issues)
        elif node.kind == CONDITION_KIND:
            _condition_issues(node, known, issues)
        elif node.kind == "branch":
            _branch_issues(node, known, issues)
        elif node.kind == "wait":
            _wait_issues(node, issues)
        elif node.kind == "wait_for_event":
            _wait_for_event_issues(node, known, issues)
        elif node.kind == "start_flow":
            _start_flow_issues(node, issues)
        elif node.kind == "exit":
            _exit_issues(node, issues)
        elif node.kind == ACTION_KIND and node.type == "classify":
            # An action node, so it also falls through to the visibility check below — hence the
            # explicit `_visibility_issues` call rather than sharing the `else` arm.
            _classify_issues(graph, node, issues)
            for issue in _prefixed(conditions.condition_issues(node.visibility), node.label):
                add(issue)
        else:
            for issue in _prefixed(conditions.condition_issues(node.visibility), node.label):
                add(issue)
        for target in node.next_ids:
            if target not in known:
                add(f"“{node.label}” leads to “{target}”, which is not in this flow.")
        for dep in node.depends_on:
            if dep not in known:
                add(f"“{node.label}” waits for “{dep}”, which is not in this flow.")
    _reachability_issues(graph, issues)
    _cycle_issues(graph, issues)
    return _dedupe_issues(issues)


def graph_issues(graph: Graph) -> list[str]:
    """Human-readable reasons this graph is not ready to publish. Empty ⇒ structurally publishable.

    Covers the caps, dangling edges, orphans, loops, the mandatory else, and each control node's
    own config — including a ``wait_for_event`` naming a trigger the registry does not have, and
    one naming a trigger no listener could ever be released by (``triggers.listener_refusal``).
    Deduplicated in document order, like ``conditions.condition_issues`` — one clear message beats
    twenty copies.

    Not covered, deliberately, because they need the database: whether a ``start_flow``'s
    ``flow_code`` names a real published flow **and does not lead back round to this one**, and
    whether an action node's permission is held by any role in the firm
    (``effects.preflight``). All three are async and session-bound — the router's publish path
    (``routers/flows.py::_automation_issues``) is where they belong.
    """
    return messages(graph_issues_detailed(graph))
