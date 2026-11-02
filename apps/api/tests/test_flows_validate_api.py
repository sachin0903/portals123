"""Live publish validation — ``POST /flows/{uid}/validate`` and the structured issue list behind it.

Two guarantees are pinned here, and they pull in opposite directions:

* **The words never changed.** ``graph_issues`` still returns exactly the strings it always did.
  The structured accessor is a second VIEW of one producer, not a second producer — a builder that
  badges one sentence while publish refuses with another is worse than no badge at all.
* **Every refusal now names its node.** Including the DB-backed ones the router owns, and
  including the multi-node ones (an orphan set, a loop), which are filed against every node
  involved so each of them badges.

Plus the thing that makes the endpoint safe to call on a debounce: it writes nothing.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.audit_actions import EXEMPT_MUTATING_ROUTES
from app.auth import Principal, get_current_principal
from app.flows import definitions
from app.flows import graph as graph_mod
from app.main import app
from app.routers import flows as flows_router
from tests.conftest import api_routes

# ---------------------------------------------------------------------------------------------
# Fixtures: definitions with known problems
# ---------------------------------------------------------------------------------------------


def _step(sid: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": sid,
        "kind": "step",
        "type": "review",
        "title": sid.title(),
        "config": {},
        "scope": "account",
        "assignee": "staff",
    }
    base.update(kw)
    return base


def _branchless() -> dict[str, Any]:
    """A branch with no “otherwise”, a dangling arm, and an orphaned end."""
    return {
        "nodes": [
            _step("intro", next="fork"),
            {
                "id": "fork",
                "kind": "branch",
                "title": "Advised?",
                "config": {
                    "branches": [
                        {
                            "id": "b1",
                            "label": "Advised",
                            "conditions": {
                                "field": "contact.service_type",
                                "operator": "equals",
                                "value": "advised",
                            },
                            "next": "gone",
                        }
                    ]
                },
            },
            {"id": "stranded", "kind": "exit", "title": "Nowhere", "config": {}},
        ],
        "milestones": [],
    }


def _looped() -> dict[str, Any]:
    return {
        "nodes": [_step("a", next="b"), _step("b", next="a")],
        "milestones": [],
    }


# ---------------------------------------------------------------------------------------------
# The string view is unchanged
# ---------------------------------------------------------------------------------------------


def test_graph_issues_still_returns_the_exact_sentences_it_always_did() -> None:
    """The pin. These strings are the ones publish has shown since v3 landed; the structured
    refactor must not have reworded, reordered or duplicated a single one."""
    issues = graph_mod.graph_issues(graph_mod.parse(_branchless()))
    assert issues == [
        "Branch “Advised?” needs at least 2 branches.",
        "Branch “Advised?” has no “otherwise” branch. Every branch needs one unconditional "
        "fallback, or a case that matches nothing stops here.",
        "Branch “Advised?”: the “Advised” branch leads to “gone”, which is not in this flow.",
        "Nothing leads to “Nowhere”. Connect them, or remove them.",
    ]


def test_a_multi_node_sentence_is_said_once_in_the_string_view() -> None:
    """A loop names every node on it in ONE sentence. The structured view files that sentence
    against each of them (so each badges); the string view must still print it once."""
    graph = graph_mod.parse(_looped())
    detailed = graph_mod.graph_issues_detailed(graph)
    loops = [i for i in detailed if "loop" in i.message]
    assert {i.node_id for i in loops} == {"a", "b"}
    assert len({i.message for i in loops}) == 1
    assert graph_mod.graph_issues(graph).count(loops[0].message) == 1


def test_publish_issues_string_view_matches_the_structured_one() -> None:
    """Message-for-message, ignoring the de-duplication the string view has always applied."""
    definition = definitions.validate_definition(_branchless())
    strings = definitions.publish_issues(definition)
    detailed = definitions.publish_issues_detailed(definition)
    assert set(strings) == {i.message for i in detailed}


# ---------------------------------------------------------------------------------------------
# The structured view names the right node
# ---------------------------------------------------------------------------------------------


def test_every_issue_names_the_node_it_is_about() -> None:
    detailed = definitions.publish_issues_detailed(definitions.validate_definition(_branchless()))
    by_node: dict[str | None, list[str]] = {}
    for issue in detailed:
        by_node.setdefault(issue.node_id, []).append(issue.message)
    assert all("Branch “Advised?”" in m for m in by_node["fork"])
    assert by_node["stranded"] == ["Nothing leads to “Nowhere”. Connect them, or remove them."]
    assert "intro" not in by_node  # a fine step collects nothing


def test_a_whole_flow_problem_has_no_node() -> None:
    detailed = definitions.publish_issues_detailed({"steps": []})
    assert [(i.node_id, i.message) for i in detailed] == [
        (None, "Add at least one step before publishing.")
    ]


def test_a_step_config_refusal_is_filed_against_that_step() -> None:
    definition = definitions.validate_definition(
        {
            "nodes": [
                _step("pack", type="generate_document", scope="application", config={}),
            ],
            "milestones": [],
        }
    )
    detailed = definitions.publish_issues_detailed(definition)
    missing = [i for i in detailed if "template uid" in i.message]
    assert missing and missing[0].node_id == "pack"


# ---------------------------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------------------------


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one(self) -> Any:
        return self._value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def first(self) -> Any:
        return (self._value,) if self._value is not None else None

    def mappings(self) -> Any:  # pragma: no cover — not reached by these paths
        raise AssertionError("validate must not read rows")


class _ReadOnlySession:
    """A session that answers the handful of lookups validation makes and REFUSES to write.

    The refusal is the test: the endpoint is on ``EXEMPT_MUTATING_ROUTES`` precisely because it has
    no effect, so "it happens not to write today" is not good enough — a write here fails loudly.

    ``firm_flows`` is the rest of the firm's flow shelf as the ``start_flow`` checks see it:
    ``code -> published definition``. A code that is present is a published flow whose definition
    the cycle walk can follow; a code that is absent is not a flow in this firm at all.
    """

    def __init__(
        self,
        *,
        flow_code: str | None = "this-flow",
        firm_flows: dict[str, dict[str, Any]] | None = None,
        drafts: set[str] | None = None,
    ) -> None:
        self.statements: list[str] = []
        self.flow_code = flow_code
        self.firm_flows = firm_flows or {}
        self.drafts = drafts or set()
        self.committed = False

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        verb = sql.split(" ", 1)[0].upper()
        assert verb == "SELECT", f"validate issued a non-SELECT statement: {sql}"
        if "auth.tenant_id()" in sql and sql.startswith("SELECT auth.tenant_id()"):
            return _Result(7)
        if "FROM flows.flows WHERE uid" in sql:
            return _Result(self.flow_code)
        code = (params or {}).get("c")
        if "SELECT f.status FROM flows.flows f" in sql:
            if code in self.drafts:
                return _Result("draft")
            return _Result("published" if code in self.firm_flows else None)
        if "SELECT v.definition FROM flows.flows f" in sql:
            if code in self.drafts:
                return _Result(None)
            return _Result(self.firm_flows.get(str(code)))
        return _Result(None)

    async def commit(self) -> None:  # pragma: no cover
        raise AssertionError("validate must not commit")


_PRINCIPAL = Principal(
    user_id="wos_user",
    organisation_id="org",
    email="author@firm.test",
    permissions=["flows:write", "flows:read"],
    role="admin",
)


async def _validate(definition: dict[str, Any], **kw: Any) -> Any:
    session = _ReadOnlySession(**kw)
    out = await flows_router.validate_flow_definition(
        uid="flw_abc",
        body=flows_router.ValidateDefinition(definition=definition),
        principal=_PRINCIPAL,
        session=session,  # type: ignore[arg-type]
    )
    return out, session


@pytest.mark.asyncio
async def test_validate_reports_the_same_issues_publish_would() -> None:
    out, _session = await _validate(_branchless())
    messages = [i.message for i in out.issues]
    assert "Branch “Advised?” needs at least 2 branches." in messages
    assert {i.node_id for i in out.issues if "Advised?" in i.message} == {"fork"}


@pytest.mark.asyncio
async def test_validate_writes_nothing() -> None:
    # A definition with a start_flow node, so the cycle walk runs too: it READS flow_versions (it
    # has to — following a tail call means parsing the target's published definition), which is
    # exactly why "never mentions flow_versions" is the wrong assertion and "every statement is a
    # SELECT, and nothing commits" is the right one.
    _out, session = await _validate(
        _calls("review"), firm_flows={"review": _calls("chase"), "chase": _calls()}
    )
    assert session.statements  # it did look things up
    assert all(s.startswith("SELECT") for s in session.statements)
    assert not session.committed
    assert any("flow_versions" in s for s in session.statements)


@pytest.mark.asyncio
async def test_a_clean_definition_validates_with_no_issues() -> None:
    definition = {
        "nodes": [
            _step("review", next="done"),
            {"id": "done", "kind": "exit", "title": "Done", "config": {"outcome": "completed"}},
        ],
        "milestones": [],
    }
    out, _session = await _validate(definition)
    assert out.issues == []


@pytest.mark.asyncio
async def test_an_unreadable_draft_is_an_answer_not_a_422() -> None:
    """Mid-edit the author's document is often nonsense. The endpoint exists to SAY so."""
    out, _session = await _validate({"nodes": [{"id": "a", "kind": "nonsense"}]})
    assert len(out.issues) == 1
    assert out.issues[0].node_id is None
    assert "unknown kind" in out.issues[0].message


@pytest.mark.asyncio
async def test_validate_404s_on_a_flow_this_firm_cannot_see() -> None:
    with pytest.raises(HTTPException) as exc:
        await _validate(_branchless(), flow_code=None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_validate_needs_flows_write() -> None:
    reader = Principal(
        user_id="wos_reader",
        organisation_id="org",
        email="reader@firm.test",
        permissions=["flows:read"],
        role="viewer",
    )
    with pytest.raises(HTTPException) as exc:
        await flows_router.validate_flow_definition(
            uid="flw_abc",
            body=flows_router.ValidateDefinition(definition=_branchless()),
            principal=reader,
            session=_ReadOnlySession(),  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------------------------
# start_flow — the cross-flow cycle check
# ---------------------------------------------------------------------------------------------
#
# `graph_issues` refuses a loop drawn inside ONE definition, but `start_flow` is a terminal kind:
# there is no edge to follow, so a loop drawn ACROSS flows was invisible to every validator and
# A -> B -> A published clean. The contract had claimed "cycle-checked at publish" since v3 landed.


def _tail_call(code: str, nid: str = "go", title: str = "Off we go") -> dict[str, Any]:
    return {"id": nid, "kind": "start_flow", "title": title, "config": {"flow_code": code}}


def _calls(*codes: str) -> dict[str, Any]:
    """A one-step flow that tail-calls each of ``codes`` — the shape a firm's other flows take in
    these tests, and the shape of the draft under validation."""
    if not codes:
        return {"nodes": [_step("only")], "milestones": []}
    nodes: list[dict[str, Any]] = [_step("only", next=[f"go{i}" for i, _ in enumerate(codes)])]
    nodes += [_tail_call(code, nid=f"go{i}") for i, code in enumerate(codes)]
    return {"nodes": nodes, "milestones": []}


@pytest.mark.asyncio
async def test_a_flow_that_starts_itself_is_refused() -> None:
    """The degenerate cycle, and the one the runtime punishes hardest: every hop is a fresh case
    until the chain cap turns it into an operations task."""
    out, _s = await _validate(_calls("this-flow"), firm_flows={"this-flow": _calls("this-flow")})
    assert [i.node_id for i in out.issues] == ["go0"]
    assert "this same journey" in out.issues[0].message


@pytest.mark.asyncio
async def test_a_two_flow_cycle_is_refused() -> None:
    """A -> B -> A. The reported bug, verbatim."""
    out, _s = await _validate(
        _calls("review"),
        firm_flows={"review": _calls("this-flow")},
    )
    assert [i.node_id for i in out.issues] == ["go0"]
    assert "comes back round" in out.issues[0].message
    assert "this-flow → review → this-flow" in out.issues[0].message


@pytest.mark.asyncio
async def test_a_three_flow_cycle_is_refused_and_names_the_chain() -> None:
    """Longer than two, because a check that only compares the immediate target would pass this
    one — and the author needs the whole chain to know which link to cut."""
    out, _s = await _validate(
        _calls("review"),
        firm_flows={"review": _calls("chase"), "chase": _calls("this-flow")},
    )
    assert [i.node_id for i in out.issues] == ["go0"]
    assert "this-flow → review → chase → this-flow" in out.issues[0].message


@pytest.mark.asyncio
async def test_a_chain_that_ends_publishes() -> None:
    """The check must refuse loops, not tail calls. A three-deep chain that terminates is exactly
    what start_flow is FOR."""
    out, _s = await _validate(
        _calls("review"),
        firm_flows={"review": _calls("chase"), "chase": _calls()},
    )
    assert out.issues == []


@pytest.mark.asyncio
async def test_a_diamond_is_not_a_cycle() -> None:
    """Two tail calls converging on a shared downstream flow revisit a code without looping. A
    walk that confused "seen before" with "back round" would refuse this."""
    out, _s = await _validate(
        _calls("review", "chase"),
        firm_flows={"review": _calls("wrap"), "chase": _calls("wrap"), "wrap": _calls()},
    )
    assert out.issues == []


@pytest.mark.asyncio
async def test_only_the_offending_node_is_badged() -> None:
    """Two tail calls, one of them looping. The builder badges nodes, so a refusal filed against
    the flow (or against both) would send the author to the wrong one."""
    out, _s = await _validate(
        _calls("wrap", "review"),
        firm_flows={"wrap": _calls(), "review": _calls("this-flow")},
    )
    assert [i.node_id for i in out.issues] == ["go1"]


@pytest.mark.asyncio
async def test_an_unpublished_target_is_still_the_refusal_it_always_was() -> None:
    """The cycle walk follows PUBLISHED definitions only, so a draft target must not silently
    become "no cycle here, carry on" — the older, more specific refusal still wins."""
    out, _s = await _validate(_calls("review"), drafts={"review"})
    assert [i.node_id for i in out.issues] == ["go0"]
    assert "not published" in out.issues[0].message


@pytest.mark.asyncio
async def test_the_walk_loads_each_flow_once() -> None:
    """The bound. Following tail calls is a graph walk against the database, and a firm's flows can
    fan out; the definition cache is what keeps it one SELECT per distinct flow rather than one per
    path, and it is shared across every start_flow node in the definition."""
    out, session = await _validate(
        _calls("review", "chase"),
        firm_flows={"review": _calls("wrap"), "chase": _calls("wrap"), "wrap": _calls()},
    )
    assert out.issues == []
    definition_reads = [s for s in session.statements if "SELECT v.definition" in s]
    assert len(definition_reads) == 3, "review, chase and wrap — each fetched exactly once"


@pytest.mark.asyncio
async def test_a_cycle_lying_entirely_downstream_is_not_this_flows_refusal() -> None:
    """Deliberate scope. B and C calling each other is B's or C's own publish to refuse; blaming
    the flow that merely reaches them would badge a node whose author cannot fix it from here — and
    the runtime chain cap still bounds it. Pinned so the choice is a decision, not an oversight."""
    out, _s = await _validate(
        _calls("review"),
        firm_flows={"review": _calls("chase"), "chase": _calls("review")},
    )
    assert out.issues == []


# ---------------------------------------------------------------------------------------------
# The action-event exemption
# ---------------------------------------------------------------------------------------------


def _validate_route() -> Any:
    return next(
        route
        for route in api_routes(app)
        if getattr(route, "name", None) == "validate-flow-definition"
    )


def test_validate_is_exempt_from_the_action_stream_but_still_authenticates() -> None:
    """Exempt because it has no effect to record and the builder calls it on a debounce — NOT
    because it is unauthenticated. Both halves matter: drop the authentication and the exemption
    becomes an open endpoint; drop the exemption and a firm's activity log fills with keystrokes.
    """
    route = _validate_route()
    assert ("POST", route.path) in EXEMPT_MUTATING_ROUTES

    def authenticates(dependant: Any) -> bool:
        return any(
            dep.call is get_current_principal or authenticates(dep)
            for dep in dependant.dependencies
        )

    assert authenticates(route.dependant)
