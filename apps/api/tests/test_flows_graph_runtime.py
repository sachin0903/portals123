"""The v3 graph at RUNTIME — branches taken, waits armed and resumed, chains capped, exits latched.

The pure half (``planning.reachable`` / ``control_gated_steps`` / ``elided_by_branch``) needs
nothing. The rest drives ``orchestrator`` against a real Postgres (Docker; ``service_session`` →
PostgresContainer), because every claim here is about rows: a step held ``pending`` with a reason,
a ``flows.timers`` row that outlives the process, a case latched terminal.

The regression that matters most is the one that asserts NOTHING changed: the seeded onboarding
flow is legacy-shaped and live in production, and ``test_the_seeded_onboarding_flow_is_untouched``
plus ``tests/test_flows_onboarding_walkthrough.py`` are what keep the graph runtime from touching
it. Every guard is structural (``graph.is_legacy_linear``), but a structural guard nobody exercises
is a comment.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.flows import graph as graph_mod
from app.flows import memory as memory_mod
from app.flows import onboarding_seed, onboarding_terms, orchestrator, planning, timers
from app.flows.definitions import validate_definition
from app.flows.domain_events import StepEvent, StepSignal
from app.flows.planning import ApplicationGraph, Participant
from tests.conftest import SECCL_PLATFORM_SQL

# ---------------------------------------------------------------------------------------------
# Definition helpers
# ---------------------------------------------------------------------------------------------


def _step(node_id: str, **kw: Any) -> dict[str, Any]:
    """A participant-scoped staff `task` node: the least machinery a graph test can be made of.

    `task` has no artifact, so the graph's decisions are the only thing under test — a request
    item or a KYC check would put another module's behaviour in the same assertion.
    """
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


def _seed_args() -> tuple[dict[str, str], dict[str, str]]:
    return (
        {code: f"frm_{code}" for code in onboarding_seed.FORMS},
        {name: f"doc_{i}" for i, name in enumerate(onboarding_terms.TERMS)},
    )


def _shipped_seed() -> dict[str, Any]:
    """The onboarding journey as it ships — a v3 graph."""
    return onboarding_seed._build_definition(*_seed_args())


def _legacy_seed() -> dict[str, Any]:
    """The same journey in the pre-v3 shape. Not what a tenant is seeded with any more, but what
    every definition authored before the rebuild looks like — which is what the legacy guards are
    about."""
    return onboarding_seed._legacy_definition(*_seed_args())


#: intake → risk? ─┬─ high ─→ edd ─→ notify
#:                 └─ else ────────→ notify      (the arms re-converge on purpose)
BRANCH_FLOW = _definition(
    [
        _step("intake", next="risk"),
        {
            "id": "risk",
            "kind": "branch",
            "title": "High risk?",
            "config": {
                "branches": [
                    {
                        "id": "b1",
                        "label": "High risk",
                        "conditions": {
                            "field": "memory.risk_band",
                            "operator": "equals",
                            "value": "high",
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

WAIT_FLOW = _definition(
    [
        _step("intake", next="pause"),
        {
            "id": "pause",
            "kind": "wait",
            "title": "Cooling off",
            "config": {"duration_days": 7},
            "next": "after",
        },
        _step("after"),
    ]
)

EVENT_FLOW = _definition(
    [
        _step("intake", next="park"),
        {
            "id": "park",
            "kind": "wait_for_event",
            "title": "Await the upload",
            "config": {
                "trigger": "client.document_uploaded",
                "timeout_days": 3,
                "timeout_branch": "chase",
            },
            "next": "received",
        },
        _step("received"),
        _step("chase"),
    ]
)

EXIT_FLOW = _definition(
    [
        _step("intake", next="stop"),
        {
            "id": "stop",
            "kind": "exit",
            "title": "Declined",
            "config": {"outcome": "cancelled", "reason": "the client withdrew"},
        },
    ]
)


def _chain_flow(child_code: str, *, carry: bool) -> dict[str, Any]:
    return _definition(
        [
            _step("intake", next="handover"),
            {
                "id": "handover",
                "kind": "start_flow",
                "title": "Hand over",
                "config": {"flow_code": child_code, "carry_memory": carry},
            },
        ]
    )


# ---------------------------------------------------------------------------------------------
# Pure — the decisions a control node forces
# ---------------------------------------------------------------------------------------------


def test_reachable_can_be_asked_to_avoid_a_node() -> None:
    g = graph_mod.parse(BRANCH_FLOW)
    assert planning.reachable(g, "risk") == {"risk", "edd", "notify"}
    # Nothing is reachable from the entry without going through the branch.
    assert planning.reachable(g, "intake", blocked=frozenset({"risk"})) == {"intake"}


def test_control_gated_steps_names_the_gate() -> None:
    g = graph_mod.parse(BRANCH_FLOW)
    gated = planning.control_gated_steps(g)
    assert gated == {"edd": "risk", "notify": "risk"}
    # `intake` is the entry and has no control predecessor, so the dependency pass still owns it.
    assert "intake" not in gated


def test_a_legacy_graph_has_no_control_gates_at_all() -> None:
    """THE structural guarantee. A legacy definition's edges are presentation order; if anything
    here reported a gate, every live onboarding step would be held waiting for a walk that never
    comes.

    Driven off ``_legacy_definition`` on purpose: the seed itself ships as a graph now, but every
    journey authored before that is one of these and they are still running.
    """
    g = graph_mod.parse(validate_definition(_legacy_seed()))
    assert g.is_legacy_linear
    assert planning.control_gated_steps(g) == {}
    assert planning.elided_by_branch(g, g.order[0], None) == ()


def test_the_shipped_seed_holds_initial_forms_for_the_start_walk() -> None:
    """The start releases the applicable initial form; ordinary dependencies release the parallel
    adviser fact-find after it."""
    g = graph_mod.parse(validate_definition(_shipped_seed()))
    assert not g.is_legacy_linear
    gated = planning.control_gated_steps(g)
    assert gated["core"] == "start"
    assert gated["org"] == "start"
    assert planning.reachable(g, g.entry) >= set(gated)


def test_elided_by_branch_keeps_the_re_convergence() -> None:
    g = graph_mod.parse(BRANCH_FLOW)
    # Taking the else elides the EDD arm, but `notify` is shared and must survive.
    assert planning.elided_by_branch(g, "risk", "notify") == ("edd",)
    # Taking the EDD arm elides nothing: everything the else led to is downstream of it.
    assert planning.elided_by_branch(g, "risk", "edd") == ()


def test_elided_by_branch_ignores_a_node_that_is_not_a_branch() -> None:
    g = graph_mod.parse(BRANCH_FLOW)
    assert planning.elided_by_branch(g, "intake", None) == ()


# ---------------------------------------------------------------------------------------------
# The bed
# ---------------------------------------------------------------------------------------------


class _Bed:
    def __init__(self, tenant: int, contact: int, platform: int, application: int) -> None:
        self.tenant = tenant
        self.contact = contact
        self.platform = platform
        self.application = application


async def _bed(sess: AsyncSession) -> _Bed:
    s = uuid.uuid4().hex[:8]
    tenant = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"gr_{s}", "a": f"gr-{s}", "n": f"Graph {s}"},
            )
        ).scalar_one()
    )
    platform = str(
        (
            await sess.execute(
                text(SECCL_PLATFORM_SQL),
            )
        ).scalar_one()
    )
    application = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO crm.applications (tenant_id, provider, uid, status) "
                    "VALUES (:t, :p, :u, 'draft') RETURNING id"
                ),
                {"t": tenant, "p": platform, "u": f"app_{s}"},
            )
        ).scalar_one()
    )
    contact = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, email, first_name, surname) "
                    "VALUES (:t, :u, 'individual', :e, 'Harriet', 'Vale') RETURNING id"
                ),
                {"t": tenant, "u": f"c_{s}", "e": f"h_{s}@t.test"},
            )
        ).scalar_one()
    )
    return _Bed(tenant, contact, platform, application)


async def _publish(sess: AsyncSession, bed: _Bed, definition: dict[str, Any]) -> tuple[int, int]:
    s = uuid.uuid4().hex[:8]
    flow = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
                    "VALUES (:u, :t, :c, 'Graph flow', 'published') RETURNING id"
                ),
                {"u": f"flw_{s}", "t": bed.tenant, "c": f"code_{s}"},
            )
        ).scalar_one()
    )
    version = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO flows.flow_versions "
                    "  (uid, tenant_id, flow_id, version, status, definition, published_at) "
                    "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id"
                ),
                {"u": f"flv_{s}", "t": bed.tenant, "f": flow, "d": json.dumps(definition)},
            )
        ).scalar_one()
    )
    await sess.execute(
        text("UPDATE flows.flows SET current_published_version_id = :v WHERE id = :f"),
        {"v": version, "f": flow},
    )
    return flow, version


async def _start(
    sess: AsyncSession,
    bed: _Bed,
    definition: dict[str, Any],
    *,
    memory: dict[str, Any] | None = None,
    application_id: int | None = None,
    contact_id: int | None = None,
) -> tuple[int, int]:
    """Publish + start. Returns (execution_id, the single profile case id)."""
    flow, version = await _publish(sess, bed, definition)
    created = await orchestrator.start_execution(
        sess,
        tenant_id=bed.tenant,
        flow_id=flow,
        flow_version_id=version,
        definition=definition,
        graph=ApplicationGraph(
            participants=(
                Participant(contact_id or bed.contact, "individual", frozenset({"owner"})),
            ),
            accounts=(),
        ),
        application_id=application_id or bed.application,
        memory=memory,
    )
    execution_id = int(created["id"])
    case_id = int(
        (
            await sess.execute(
                text("SELECT id FROM flows.cases WHERE execution_id = :e ORDER BY id LIMIT 1"),
                {"e": execution_id},
            )
        ).scalar_one()
    )
    return execution_id, case_id


async def _steps(sess: AsyncSession, case_id: int) -> dict[str, dict[str, Any]]:
    rows = (
        (
            await sess.execute(
                text("SELECT step_key, status, data FROM flows.case_steps WHERE case_id = :c"),
                {"c": case_id},
            )
        )
        .mappings()
        .all()
    )
    return {str(r["step_key"]): dict(r) for r in rows}


async def _complete(sess: AsyncSession, bed: _Bed, case_id: int, step_key: str) -> None:
    await orchestrator.apply_step_event(
        sess,
        bed.tenant,
        StepEvent(StepSignal.COMPLETED, case_id, step_key, participant_subject_id=bed.contact),
    )


async def _declare(sess: AsyncSession, bed: _Bed, case_id: int) -> None:
    """Send the seeded journey's declaration, which lives in the execution's application case. A
    no-op for a definition without one."""
    declared_in = (
        await sess.execute(
            text(
                "SELECT c.id FROM flows.cases c JOIN flows.case_steps cs ON cs.case_id = c.id "
                "WHERE c.execution_id = (SELECT execution_id FROM flows.cases WHERE id = :c) "
                "  AND cs.step_key = 'declaration'"
            ),
            {"c": case_id},
        )
    ).scalar_one_or_none()
    if declared_in is not None:
        await orchestrator.apply_step_event(
            sess, bed.tenant, StepEvent(StepSignal.COMPLETED, int(declared_in), "declaration")
        )


async def _case_status(sess: AsyncSession, case_id: int) -> str:
    return str(
        (
            await sess.execute(text("SELECT status FROM flows.cases WHERE id = :c"), {"c": case_id})
        ).scalar_one()
    )


# ---------------------------------------------------------------------------------------------
# The live-onboarding regression
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_legacy_onboarding_definition_is_untouched(service_session: AsyncSession) -> None:
    """A legacy definition plans, releases and completes exactly as it did before v3.

    ``tests/test_flows_onboarding_walkthrough.py`` already drives the real thing through the real
    endpoints; this one asserts the specific properties the graph runtime could have broken —
    nothing is held on a control gate, nothing is elided, and no case acquires graph state at all.

    The SEED is a graph now, so this runs on ``_legacy_definition``: the guard is about the shape
    every pre-rebuild journey is stored in, and pointing it at a document that is no longer legacy
    would have quietly retired it.
    """
    sess = service_session
    bed = await _bed(sess)
    seed = validate_definition(_legacy_seed())
    execution_id, case_id = await _start(sess, bed, seed)

    held_on_a_gate = (
        await sess.execute(
            text(
                "SELECT count(*) FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                "WHERE c.execution_id = :e AND cs.data ? 'gated_on'"
            ),
            {"e": execution_id},
        )
    ).scalar_one()
    assert held_on_a_gate == 0
    elided = (
        await sess.execute(
            text(
                "SELECT count(*) FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                "WHERE c.execution_id = :e AND cs.data ? 'skipped_reason'"
            ),
            {"e": execution_id},
        )
    ).scalar_one()
    assert elided == 0
    graph_state = (
        await sess.execute(
            text("SELECT data ? 'graph' FROM flows.cases WHERE id = :c"), {"c": case_id}
        )
    ).scalar_one()
    assert graph_state is False
    # And no timer was armed by walking a chain that is presentation order, not execution order.
    armed = (
        await sess.execute(
            text("SELECT count(*) FROM flows.timers WHERE case_id = :c"), {"c": case_id}
        )
    ).scalar_one()
    assert armed == 0


@pytest.mark.asyncio
async def test_the_seeded_flow_plans_and_releases_exactly_what_the_planner_says(
    service_session: AsyncSession,
) -> None:
    """The two-way comparison, at EXECUTION level: what the engine persisted is what the pure
    planner planned, and what it instantiated is exactly the steps with no unmet prerequisite.

    ``tests/test_flows_graph.py`` proves the seed PLANS identically through the graph; this proves
    it RUNS identically — the half a plan-time comparison cannot see.
    """
    sess = service_session
    bed = await _bed(sess)
    seed = validate_definition(_shipped_seed())
    application_graph = ApplicationGraph(
        participants=(Participant(bed.contact, "individual", frozenset({"owner"})),),
        accounts=(),
    )
    expected = planning.plan_execution(list(seed["steps"]), application_graph)
    execution_id, _ = await _start(sess, bed, seed)

    persisted = (
        (
            await sess.execute(
                text(
                    "SELECT c.kind, cs.step_key, cs.status, cs.depends_on, cs.data "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "WHERE c.execution_id = :e ORDER BY cs.id"
                ),
                {"e": execution_id},
            )
        )
        .mappings()
        .all()
    )
    planned = [(case.kind, step.step_key) for case in expected for step in case.steps]
    assert [(str(r["kind"]), str(r["step_key"])) for r in persisted] == planned

    # Ordering is honoured, not decorative: a step is handed out only once nothing holds it — no
    # unsettled prerequisite AND no fork it is waiting behind. In a spine that is one step at a
    # time, which is the point of `test_the_seeded_spine_releases_one_node_at_a_time`. A step the
    # opening fork has already elided is neither held nor handed out: it is not happening.
    for row, (_, step_key) in zip(persisted, planned, strict=True):
        if str(row["status"]) == "skipped":
            continue
        # "Has an OUTSTANDING prerequisite", not "declares one". Ordering crosses tiers now,
        # so a step can declare a prerequisite this execution plans nowhere — `idv` names
        # `terms_platform`, and this journey has no accounts, hence no account tier and no
        # terms. A key planned nowhere is satisfied (the rule that stops a conditioned-out
        # step wedging everything behind it), so the step is released while still declaring
        # the dependency. `awaiting` is what records a real hold.
        held = bool(row["data"].get("awaiting")) or "gated_on" in row["data"]
        instantiated = row["data"].get("instantiated") is True
        assert instantiated is not held, step_key


# ---------------------------------------------------------------------------------------------
# branch
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_branch_releases_only_the_arm_it_took(service_session: AsyncSession) -> None:
    sess = service_session
    bed = await _bed(sess)
    execution_id, case_id = await _start(sess, bed, BRANCH_FLOW, memory={"risk_band": "high"})

    # Before the branch is reached, neither arm exists as work. (Their recorded reason is the
    # dependency on `intake`, which is the more specific of the two truths and the one a surface
    # should show; the gate becomes the reason once that dependency clears.)
    steps = await _steps(sess, case_id)
    assert steps["edd"]["data"].get("instantiated") is not True
    assert steps["notify"]["data"].get("instantiated") is not True

    await _complete(sess, bed, case_id, "intake")
    steps = await _steps(sess, case_id)
    assert steps["edd"]["data"].get("graph_released") is True
    assert steps["edd"]["data"].get("instantiated") is True
    # `notify` sits behind BOTH arms and re-converges after `edd`, so the compiled dependency on
    # `edd` holds it and is the reason recorded — the honest one to show, since the branch has
    # already decided and the outstanding work is what is left.
    assert steps["notify"]["status"] == "pending"
    assert steps["notify"]["data"].get("awaiting") == ["edd"]
    assert steps["notify"]["data"].get("graph_released") is not True
    assert steps["notify"]["data"].get("instantiated") is not True

    await _complete(sess, bed, case_id, "edd")
    steps = await _steps(sess, case_id)
    assert steps["notify"]["data"].get("instantiated") is True

    await _complete(sess, bed, case_id, "notify")
    assert await _case_status(sess, case_id) == "completed"


@pytest.mark.asyncio
async def test_the_else_arm_elides_the_other_and_does_not_block_completion(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, BRANCH_FLOW, memory={"risk_band": "low"})

    await _complete(sess, bed, case_id, "intake")
    steps = await _steps(sess, case_id)
    assert steps["edd"]["status"] == "skipped"
    assert steps["edd"]["data"].get("skipped_reason") == "branch:risk"
    assert steps["notify"]["data"].get("graph_released") is True

    # The elided arm must not hold the case open — that is the whole reason it is `skipped` and
    # not, say, `cancelled` or left pending.
    await _complete(sess, bed, case_id, "notify")
    assert await _case_status(sess, case_id) == "completed"


@pytest.mark.asyncio
async def test_a_branch_whose_field_is_missing_falls_to_the_else(
    service_session: AsyncSession,
) -> None:
    """No memory at all. An unresolved field is ``None``, which the ``equals`` cannot match, so the
    fallback takes it — the case moves on rather than wedging on a decision nobody can make."""
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, BRANCH_FLOW)

    await _complete(sess, bed, case_id, "intake")
    steps = await _steps(sess, case_id)
    assert steps["edd"]["status"] == "skipped"
    assert steps["notify"]["data"].get("graph_released") is True


@pytest.mark.asyncio
async def test_a_branch_is_taken_once_and_recorded(service_session: AsyncSession) -> None:
    """The resume path is at-least-once; re-taking a branch would re-elide an arm the case may
    already have completed."""
    sess = service_session
    bed = await _bed(sess)
    execution_id, case_id = await _start(sess, bed, BRANCH_FLOW, memory={"risk_band": "high"})
    await _complete(sess, bed, case_id, "intake")

    visited = (
        await sess.execute(
            text("SELECT data -> 'graph' -> 'visited' FROM flows.cases WHERE id = :c"),
            {"c": case_id},
        )
    ).scalar_one()
    assert visited["risk"] == "edd"

    # A second recompute is a no-op on the graph.
    await orchestrator.recompute(sess, execution_id, tenant_id=bed.tenant)
    again = (
        await sess.execute(
            text("SELECT data -> 'graph' -> 'visited' FROM flows.cases WHERE id = :c"),
            {"c": case_id},
        )
    ).scalar_one()
    assert again == visited


# ---------------------------------------------------------------------------------------------
# condition — a two-armed branch the runtime decides at
# ---------------------------------------------------------------------------------------------
#
# The pure half is in tests/test_flows_graph.py. What needs a database is that the walk actually
# arrives: a condition holds the work behind it (`control_gated_steps`), so if the walk did not
# reach it the journey would wedge — which is precisely why this node could not be a branch until
# the journey was wired into a spine.


def _forked_flow() -> dict[str, Any]:
    """intake ──→ fork ─┬─ individual ─→ personal ─┐
    ·                   └─ otherwise ──────────────┴─→ after   (the arms re-converge)
    """
    return _definition(
        [
            _step("intake", next="fork"),
            {
                "id": "fork",
                "kind": "condition",
                "title": "An individual?",
                "config": {
                    "branches": graph_mod.condition_arms(
                        {"field": "contact.type", "operator": "equals", "value": "individual"},
                        when="personal",
                        otherwise="after",
                    )
                },
            },
            _step("personal", next="after"),
            _step("after"),
        ],
        entry="intake",
    )


@pytest.mark.asyncio
async def test_a_condition_takes_its_true_arm_and_releases_the_work_behind_it(
    service_session: AsyncSession,
) -> None:
    """The bed's participant is an individual, so the fork takes the "yes" arm: the work is released
    when the walk arrives, and not a moment before."""
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, _forked_flow())

    # Held, not handed out: the fork has not been reached, so nothing behind it may run.
    held = await _steps(sess, case_id)
    assert held["personal"]["data"].get("instantiated") is not True

    await _complete(sess, bed, case_id, "intake")
    steps = await _steps(sess, case_id)
    assert steps["personal"]["data"].get("graph_released") is True
    assert steps["personal"]["data"].get("instantiated") is True
    # The far side of the re-convergence waits for the arm that was taken, not for the fork.
    assert steps["after"]["data"].get("instantiated") is not True


@pytest.mark.asyncio
async def test_a_condition_takes_its_false_arm_and_the_paths_re_converge(
    service_session: AsyncSession,
) -> None:
    """THE shape the whole spine is drawn around. The fork excludes this participant, so the work on
    the "yes" arm is elided and the journey carries on down the "no" arm — to the same node the
    other arm would have reached.
    """
    sess = service_session
    bed = await _bed(sess)
    organisation = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, email, first_name, surname) "
                    "VALUES (:t, :u, 'organisation', :e, 'Vale', 'Trust') RETURNING id"
                ),
                {
                    "t": bed.tenant,
                    "u": f"o_{uuid.uuid4().hex[:8]}",
                    "e": f"o_{uuid.uuid4().hex[:8]}@t.test",
                },
            )
        ).scalar_one()
    )
    _, case_id = await _start(sess, bed, _forked_flow(), contact_id=organisation)

    await orchestrator.apply_step_event(
        sess,
        bed.tenant,
        StepEvent(StepSignal.COMPLETED, case_id, "intake", participant_subject_id=organisation),
    )
    steps = await _steps(sess, case_id)
    assert steps["personal"]["status"] == "skipped"
    assert steps["after"]["data"].get("graph_released") is True
    assert steps["after"]["data"].get("instantiated") is True


def _transfer_fork() -> dict[str, Any]:
    """ask ──→ transfer? ─┬─ yes ─→ extra ─┐
    ·                     └─ no ───────────┴─→ finish

    ``extra`` is visible only once the account has a transfer. That fact is collected after
    planning, which is the transfer declaration on the seeded onboarding spine.
    """
    party = {
        "logic": "AND",
        "conditions": [
            {"field": "applicant.type", "operator": "equals", "value": "individual"},
            {"field": "account.has_transfer", "operator": "equals", "value": True},
        ],
    }
    return _definition(
        [
            _step("ask", scope="account", next="transfer"),
            {
                "id": "transfer",
                "kind": "condition",
                "title": "A transfer in?",
                "config": {
                    "branches": graph_mod.condition_arms(
                        {"field": "account.has_transfer", "operator": "equals", "value": True},
                        when="extra",
                        otherwise="finish",
                    )
                },
            },
            _step(
                "extra",
                scope="account",
                per_participant=True,
                assignee="client",
                visibility=party,
                next="finish",
            ),
            _step("finish", scope="account"),
        ],
        entry="ask",
    )


async def _account_case(
    sess: AsyncSession,
    bed: _Bed,
    *,
    contact_id: int,
    contact_type: str,
) -> tuple[int, int, int]:
    """An account with no transfer yet, and the journey started over it.

    Returns ``(execution_id, account_case_id, account_id)``.
    """
    account = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO platform.accounts "
                    "  (tenant_id, contact_id, application_id, status, uid) "
                    "VALUES (:t, :c, :a, 'draft', :u) RETURNING id"
                ),
                {
                    "t": bed.tenant,
                    "c": contact_id,
                    "a": bed.application,
                    "u": f"acc_{uuid.uuid4().hex[:8]}",
                },
            )
        ).scalar_one()
    )
    await sess.execute(
        text(
            "INSERT INTO crm.account_contacts "
            "  (tenant_id, application_id, account_id, contact_id, role) "
            "VALUES (:t, :a, :acc, :c, CAST('owner' AS crm.account_role))"
        ),
        {"t": bed.tenant, "a": bed.application, "acc": account, "c": contact_id},
    )
    flow, version = await _publish(sess, bed, _transfer_fork())
    created = await orchestrator.start_execution(
        sess,
        tenant_id=bed.tenant,
        flow_id=flow,
        flow_version_id=version,
        definition=_transfer_fork(),
        graph=planning.ApplicationGraph(
            participants=(planning.Participant(contact_id, contact_type, frozenset({"owner"})),),
            accounts=(
                planning.AccountSpec(account, (contact_id,), product="GIA", has_transfer=False),
            ),
        ),
        application_id=bed.application,
    )
    case_id = int(
        (
            await sess.execute(
                text("SELECT id FROM flows.cases WHERE execution_id = :e AND kind = 'account'"),
                {"e": int(created["id"])},
            )
        ).scalar_one()
    )
    return int(created["id"]), case_id, account


@pytest.mark.asyncio
async def test_a_transfer_chosen_later_creates_the_declaration(
    service_session: AsyncSession,
) -> None:
    """Account details writes the transfer after planning. The fork must create the declaration
    it then requires, rather than walking past a step that was never planned."""
    sess = service_session
    bed = await _bed(sess)
    execution_id, case_id, account = await _account_case(
        sess, bed, contact_id=bed.contact, contact_type="individual"
    )
    assert "extra" not in await _steps(sess, case_id)

    await sess.execute(
        text("UPDATE platform.accounts SET transfer = CAST(:t AS jsonb) WHERE id = :a"),
        {"t": '{"intent": true}', "a": account},
    )
    await orchestrator.apply_step_event(
        sess, bed.tenant, StepEvent(StepSignal.COMPLETED, case_id, "ask")
    )

    steps = await _steps(sess, case_id)
    assert steps["extra"]["status"] == "pending"
    assert steps["extra"]["data"].get("graph_released") is True
    assert steps["finish"]["data"].get("graph_released") is not True
    # A second pass does not insert another copy.
    await orchestrator.recompute(sess, execution_id, tenant_id=bed.tenant)
    rows = (
        await sess.execute(
            text(
                "SELECT count(*) FROM flows.case_steps cs "
                "JOIN flows.cases c ON c.id = cs.case_id "
                "WHERE c.execution_id = :e AND cs.step_key = 'extra'"
            ),
            {"e": execution_id},
        )
    ).scalar_one()
    assert int(rows) == 1


@pytest.mark.asyncio
async def test_a_transfer_the_party_cannot_accept_is_walked_past(
    service_session: AsyncSession,
) -> None:
    """The fork can choose the declaration while visibility still excludes the party. The journey
    continues; it does not wedge on a step it must not create."""
    sess = service_session
    bed = await _bed(sess)
    organisation = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name) "
                    "VALUES (:t, :u, 'organisation', 'Vale Trust') RETURNING id"
                ),
                {"t": bed.tenant, "u": f"o_{uuid.uuid4().hex[:8]}"},
            )
        ).scalar_one()
    )
    _, case_id, account = await _account_case(
        sess, bed, contact_id=organisation, contact_type="organisation"
    )
    await sess.execute(
        text("UPDATE platform.accounts SET transfer = CAST(:t AS jsonb) WHERE id = :a"),
        {"t": '{"intent": true}', "a": account},
    )
    await orchestrator.apply_step_event(
        sess, bed.tenant, StepEvent(StepSignal.COMPLETED, case_id, "ask")
    )
    steps = await _steps(sess, case_id)
    assert "extra" not in steps
    assert steps["finish"]["data"].get("graph_released") is True


# ---------------------------------------------------------------------------------------------
# The seeded onboarding, walked
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_seeded_fact_find_releases_relevant_forms_after_initial_details(
    service_session: AsyncSession,
) -> None:
    """An individual sees only their applicable forms, and all remaining adviser forms become
    ready together after Applicant details."""
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, validate_definition(_shipped_seed()))

    async def live() -> set[str]:
        return {
            key
            for key, row in (await _steps(sess, case_id)).items()
            if row["data"].get("instantiated") is True
        }

    # `idv` too: it sits after the client's terms now (the legacy order) and this journey has no
    # accounts, so the tier that would carry those terms is never planned and nothing holds
    # identity. With a real account present the walkthrough test proves the full ordering.
    assert await live() == {"core", "idv"}
    held = await _steps(sess, case_id)
    assert "org" not in held
    # `idv` IS live here, for the same reason as above: no accounts ⇒ no terms ⇒ nothing holding
    # it. What this test is really about is that the SPINE releases one node at a time, which the
    # remaining assertions still prove.
    assert held["idv"]["data"].get("instantiated") is True

    await _complete(sess, bed, case_id, "core")
    assert await live() == {"core", "idv", "additional_info"}
    after_core = await _steps(sess, case_id)
    assert after_core["additional_info"]["status"] != "skipped"
    assert "pension" not in after_core
    assert "employer" not in after_core

    await _complete(sess, bed, case_id, "additional_info")
    await _complete(sess, bed, case_id, "idv")
    assert await live() == {"core", "additional_info", "idv", "aml"}

    await _complete(sess, bed, case_id, "aml")
    # The signed W-9 / W-8 waits on the declaration's fork. No answer was remembered, so sending
    # the declaration skips it and the client's case completes.
    assert await _case_status(sess, case_id) != "completed"
    await _declare(sess, bed, case_id)
    assert (await _steps(sess, case_id))["fatca_form"]["status"] == "skipped"
    assert await _case_status(sess, case_id) == "completed"


@pytest.mark.asyncio
async def test_the_seeded_spine_asks_this_client_for_the_same_work_as_the_shape_it_replaced(
    service_session: AsyncSession,
) -> None:
    """The rebuild changed the ORDER, deliberately — so the release order is NOT compared. What is:
    the work this client is actually asked for, which must be what the tier-parallel shape asked.

    A spine plans every step and elides the ones its forks skip, where the old shape simply never
    planned them; so what is compared is the steps that are still live at the end of the journey.
    """
    sess = service_session

    async def _asked(definition: dict[str, Any], keys: list[str]) -> set[str]:
        bed = await _bed(sess)
        _, case_id = await _start(sess, bed, validate_definition(definition))
        for key in keys:
            await _complete(sess, bed, case_id, key)
        # The spine's handoff. With no W-9 / W-8 remembered, its fork skips the upload.
        await _declare(sess, bed, case_id)
        return {
            key for key, row in (await _steps(sess, case_id)).items() if row["status"] != "skipped"
        }

    order = ["core", "additional_info", "idv", "aml"]
    assert await _asked(_shipped_seed(), order) == await _asked(_legacy_seed(), order)


# ---------------------------------------------------------------------------------------------
# fan-out — the walk is a frontier, not a cursor
# ---------------------------------------------------------------------------------------------
#
# `next` may name several nodes and they all become available at once. The cursor these tests
# replaced had one way of saying "stop": where a path ran out, the whole walk ended — so a sibling
# arm of a fan-out would silently never be entered.


#: start ─┬─→ forms ──┐
#:        └─→ terms ──┴─→ review        (two arms, re-converging on a shared step)
FAN_OUT_FLOW = _definition(
    [
        {"id": "start", "kind": "start", "next": ["forms", "terms"]},
        _step("forms", next="review"),
        _step("terms", next="review"),
        _step("review"),
    ]
)


@pytest.mark.asyncio
async def test_a_start_node_releases_every_root_at_once(service_session: AsyncSession) -> None:
    """THE case the whole change exists for. Both arms are live from kickoff; neither waits for the
    other, and nothing in either one's recorded prerequisites names the other."""
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, FAN_OUT_FLOW)

    steps = await _steps(sess, case_id)
    for arm in ("forms", "terms"):
        assert steps[arm]["data"].get("graph_released") is True, arm
        assert steps[arm]["data"].get("instantiated") is True, arm
    prerequisites = {
        str(r["step_key"]): list(r["depends_on"])
        for r in (
            await sess.execute(
                text("SELECT step_key, depends_on FROM flows.case_steps WHERE case_id = :c"),
                {"c": case_id},
            )
        )
        .mappings()
        .all()
    }
    assert prerequisites["forms"] == [] and prerequisites["terms"] == []
    # The re-convergence is a barrier expressed as prerequisites, and it holds until BOTH settle.
    assert steps["review"]["data"].get("instantiated") is not True
    assert sorted(prerequisites["review"]) == ["forms", "terms"]


@pytest.mark.asyncio
async def test_a_re_convergence_waits_for_every_arm(service_session: AsyncSession) -> None:
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, FAN_OUT_FLOW)

    await _complete(sess, bed, case_id, "forms")
    assert (await _steps(sess, case_id))["review"]["data"].get("instantiated") is not True
    await _complete(sess, bed, case_id, "terms")
    steps = await _steps(sess, case_id)
    assert steps["review"]["data"].get("instantiated") is True
    # Entered once, not once per arm: the walk de-duplicates a re-convergence within the frontier
    # and `visited` de-duplicates it across walks.
    assert steps["review"]["data"].get("graph_released") is True


@pytest.mark.asyncio
async def test_one_arm_owned_by_another_tier_does_not_stop_the_others(
    service_session: AsyncSession,
) -> None:
    """The failure mode the cursor had. `photo_id` is participant-scoped and this application has a
    second participant, so THIS case does not own it — the old walk `break`s there. Its sibling
    `terms` must still be released, because a path stopping is not the walk stopping."""
    sess = service_session
    bed = await _bed(sess)
    other = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, email, first_name, surname) "
                    "VALUES (:t, :u, 'individual', :e, 'Otto', 'Vale') RETURNING id"
                ),
                {"t": bed.tenant, "u": f"c2_{uuid.uuid4().hex[:8]}", "e": f"o{bed.contact}@t.test"},
            )
        ).scalar_one()
    )
    definition = _definition(
        [
            {"id": "start", "kind": "start", "next": ["photo_id", "terms"]},
            # Only the second participant is asked for the photo id.
            _step(
                "photo_id",
                visibility={"field": "applicant.roles", "operator": "contains", "value": "trustee"},
            ),
            _step("terms"),
        ]
    )
    flow, version = await _publish(sess, bed, definition)
    created = await orchestrator.start_execution(
        sess,
        tenant_id=bed.tenant,
        flow_id=flow,
        flow_version_id=version,
        definition=definition,
        graph=ApplicationGraph(
            participants=(
                Participant(bed.contact, "individual", frozenset({"owner"})),
                Participant(other, "individual", frozenset({"trustee"})),
            ),
            accounts=(),
        ),
        application_id=bed.application,
    )
    cases = (
        (
            await sess.execute(
                text("SELECT id, subject_id FROM flows.cases WHERE execution_id = :e ORDER BY id"),
                {"e": int(created["id"])},
            )
        )
        .mappings()
        .all()
    )
    owner_case = next(int(r["id"]) for r in cases if int(r["subject_id"]) == bed.contact)
    trustee_case = next(int(r["id"]) for r in cases if int(r["subject_id"]) == other)

    owner_steps = await _steps(sess, owner_case)
    assert "photo_id" not in owner_steps  # planned for the other participant only
    assert owner_steps["terms"]["data"].get("instantiated") is True
    assert (await _steps(sess, trustee_case))["photo_id"]["data"].get("instantiated") is True


@pytest.mark.asyncio
async def test_a_branch_arm_may_itself_fan_out(service_session: AsyncSession) -> None:
    """Exclusive and parallel compose: the branch picks ONE arm, and that arm opens two things."""
    sess = service_session
    bed = await _bed(sess)
    definition = _definition(
        [
            _step("intake", next="risk"),
            {
                "id": "risk",
                "kind": "branch",
                "title": "High risk?",
                "config": {
                    "branches": [
                        {
                            "id": "b1",
                            "label": "High risk",
                            "conditions": {
                                "field": "memory.risk_band",
                                "operator": "equals",
                                "value": "high",
                            },
                            "next": "escalate",
                        },
                        {"id": "b2", "label": "Otherwise", "conditions": None, "next": "proceed"},
                    ]
                },
            },
            # The taken arm fans out; the untaken one is a single step that must be elided.
            _step("escalate", next=["edd", "notify_compliance"]),
            _step("edd"),
            _step("notify_compliance"),
            _step("proceed"),
        ]
    )
    _, case_id = await _start(sess, bed, definition, memory={"risk_band": "high"})
    await _complete(sess, bed, case_id, "intake")
    await _complete(sess, bed, case_id, "escalate")

    steps = await _steps(sess, case_id)
    for both in ("edd", "notify_compliance"):
        assert steps[both]["data"].get("graph_released") is True, both
        assert steps[both]["data"].get("instantiated") is True, both
    assert steps["proceed"]["status"] == "skipped"


@pytest.mark.asyncio
async def test_the_node_budget_is_total_and_a_wide_graph_stays_under_it(
    service_session: AsyncSession,
) -> None:
    """A wide graph is exactly what a per-path hop limit could not see: sixty roots is sixty nodes
    in ONE walk. The budget is sized above the publishable node cap for this reason, and the walk
    de-duplicates, so a legal graph can never reach it."""
    sess = service_session
    bed = await _bed(sess)
    roots = [f"r{i}" for i in range(60)]
    definition = _definition(
        [
            {"id": "start", "kind": "start", "next": roots},
            *[_step(r) for r in roots],
        ]
    )
    assert len(roots) > orchestrator._MAX_GRAPH_HOPS // 10  # the old per-path cap was 50
    _, case_id = await _start(sess, bed, definition)
    steps = await _steps(sess, case_id)
    assert all(steps[r]["data"].get("instantiated") is True for r in roots)


@pytest.mark.asyncio
async def test_a_v3_flow_with_no_start_node_still_walks_from_its_derived_entry(
    service_session: AsyncSession,
) -> None:
    """Back-compat: every v3 definition authored before the arity rule has no start node, so the
    entry stays "the node nothing points at" and a bare `next: "b"` still means one successor."""
    sess = service_session
    bed = await _bed(sess)
    definition = _definition([_step("intake", next="pause"), *WAIT_FLOW["nodes"][1:]])
    parsed = graph_mod.parse(definition)
    assert parsed.start_id is None and parsed.entry == "intake"
    _, case_id = await _start(sess, bed, definition)
    await _complete(sess, bed, case_id, "intake")
    armed = (
        await sess.execute(
            text("SELECT count(*) FROM flows.timers WHERE case_id = :c"), {"c": case_id}
        )
    ).scalar_one()
    assert armed == 1


# ---------------------------------------------------------------------------------------------
# wait / wait_for_event / the resume seam
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_wait_arms_a_durable_timer_and_holds_the_next_step(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, WAIT_FLOW)
    await _complete(sess, bed, case_id, "intake")

    timer = (
        (
            await sess.execute(
                text(
                    "SELECT node_id, kind, state, fire_at > now() + interval '6 days' AS far_off "
                    "FROM flows.timers WHERE case_id = :c"
                ),
                {"c": case_id},
            )
        )
        .mappings()
        .one()
    )
    assert (timer["node_id"], timer["kind"], timer["state"]) == ("pause", "wait", "pending")
    assert timer["far_off"] is True
    # The step behind the wait is NOT instantiated — the failure the control gate exists for.
    steps = await _steps(sess, case_id)
    assert steps["after"]["data"].get("instantiated") is not True
    assert steps["after"]["data"].get("gated_on") == "pause"


@pytest.mark.asyncio
async def test_resume_from_a_wait_releases_the_successor(service_session: AsyncSession) -> None:
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, WAIT_FLOW)
    await _complete(sess, bed, case_id, "intake")

    await orchestrator.resume_from_timer(
        sess, case_id=case_id, node_id="pause", kind=timers.RESUME_WAIT
    )
    steps = await _steps(sess, case_id)
    assert steps["after"]["data"].get("instantiated") is True


@pytest.mark.asyncio
async def test_a_wait_with_no_resolvable_deadline_carries_straight_on(
    service_session: AsyncSession,
) -> None:
    """``resolve_fire_at`` returns ``None`` for an unresolvable date, and its contract is "take the
    successor now". A journey parked for ever is invisible; moving on early is not."""
    sess = service_session
    bed = await _bed(sess)
    definition = _definition(
        [
            _step("intake", next="pause"),
            {
                "id": "pause",
                "kind": "wait",
                "title": "Until",
                "config": {"until_date_field": "memory.never_set"},
                "next": "after",
            },
            _step("after"),
        ]
    )
    _, case_id = await _start(sess, bed, definition)
    await _complete(sess, bed, case_id, "intake")

    armed = (
        await sess.execute(
            text("SELECT count(*) FROM flows.timers WHERE case_id = :c"), {"c": case_id}
        )
    ).scalar_one()
    assert armed == 0
    assert (await _steps(sess, case_id))["after"]["data"].get("instantiated") is True


@pytest.mark.asyncio
async def test_wait_for_event_arms_a_listener_and_a_deadline(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, EVENT_FLOW)
    await _complete(sess, bed, case_id, "intake")

    listener = (
        (
            await sess.execute(
                text(
                    "SELECT trigger_key, subject_kind, subject_id, state "
                    "FROM flows.case_listeners WHERE case_id = :c"
                ),
                {"c": case_id},
            )
        )
        .mappings()
        .one()
    )
    assert listener["trigger_key"] == "client.document_uploaded"
    assert (listener["subject_kind"], listener["subject_id"]) == ("contact", bed.contact)
    assert listener["state"] == "active"
    timer = (
        (
            await sess.execute(
                text("SELECT kind, payload FROM flows.timers WHERE case_id = :c"), {"c": case_id}
            )
        )
        .mappings()
        .one()
    )
    assert timer["kind"] == timers.KIND_EVENT_TIMEOUT
    assert timer["payload"]["timeout_branch"] == "chase"


@pytest.mark.asyncio
async def test_the_event_resume_takes_the_normal_successor(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, EVENT_FLOW)
    await _complete(sess, bed, case_id, "intake")

    await orchestrator.resume_from_timer(
        sess, case_id=case_id, node_id="park", kind=timers.RESUME_EVENT
    )
    steps = await _steps(sess, case_id)
    assert steps["received"]["data"].get("instantiated") is True
    assert steps["chase"]["data"].get("instantiated") is not True


@pytest.mark.asyncio
async def test_the_timeout_resume_takes_the_snapshotted_branch(
    service_session: AsyncSession,
) -> None:
    """The deadline's branch comes from the TIMER'S PAYLOAD, snapshotted when the wait was armed —
    not from the definition as it reads now. Simulated by rewriting the payload to a different
    (valid) node: if the pinned config won, the case would take ``chase``."""
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, EVENT_FLOW)
    await _complete(sess, bed, case_id, "intake")

    await sess.execute(
        text(
            'UPDATE flows.timers SET payload = payload || \'{"timeout_branch": "received"}\' '
            "WHERE case_id = :c AND node_id = 'park'"
        ),
        {"c": case_id},
    )
    await orchestrator.resume_from_timer(
        sess, case_id=case_id, node_id="park", kind=timers.RESUME_EVENT_TIMEOUT
    )
    steps = await _steps(sess, case_id)
    assert steps["received"]["data"].get("instantiated") is True
    assert steps["chase"]["data"].get("instantiated") is not True
    # And the listener is retired: the event can no longer release a node the case has left.
    state = (
        await sess.execute(
            text("SELECT state FROM flows.case_listeners WHERE case_id = :c"), {"c": case_id}
        )
    ).scalar_one()
    assert state == "cancelled"


@pytest.mark.asyncio
async def test_the_timeout_resume_falls_back_to_the_pinned_config(
    service_session: AsyncSession,
) -> None:
    """A timer armed before the snapshot existed still has to resume somewhere."""
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, EVENT_FLOW)
    await _complete(sess, bed, case_id, "intake")

    await sess.execute(
        text(
            "UPDATE flows.timers SET payload = payload - 'timeout_branch' "
            "WHERE case_id = :c AND node_id = 'park'"
        ),
        {"c": case_id},
    )
    await orchestrator.resume_from_timer(
        sess, case_id=case_id, node_id="park", kind=timers.RESUME_EVENT_TIMEOUT
    )
    assert (await _steps(sess, case_id))["chase"]["data"].get("instantiated") is True


@pytest.mark.asyncio
async def test_resuming_a_node_the_definition_no_longer_has_is_a_no_op(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, WAIT_FLOW)
    await orchestrator.resume_from_timer(
        sess, case_id=case_id, node_id="a-node-that-never-existed", kind=timers.RESUME_WAIT
    )
    assert await _case_status(sess, case_id) in {"draft", "active"}


@pytest.mark.asyncio
async def test_a_case_waits_at_a_step_another_tier_owns(service_session: AsyncSession) -> None:
    """The tier rule: an account case walking the graph does NOT stride past a participant step it
    holds no instance of. Its wait is armed only once that step has settled somewhere, so a graph
    drawn as "forms, then pause, then open the account" keeps that order across tiers."""
    sess = service_session
    bed = await _bed(sess)
    s = uuid.uuid4().hex[:8]
    account = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO platform.accounts "
                    "  (tenant_id, contact_id, application_id, status, uid) "
                    "VALUES (:t, :c, :a, 'draft', :u) RETURNING id"
                ),
                {"t": bed.tenant, "c": bed.contact, "a": bed.application, "u": f"acc_{s}"},
            )
        ).scalar_one()
    )
    await sess.execute(
        text(
            "INSERT INTO crm.account_contacts "
            "  (tenant_id, application_id, account_id, contact_id, role) "
            "VALUES (:t, :a, :acc, :c, CAST('owner' AS crm.account_role))"
        ),
        {"t": bed.tenant, "a": bed.application, "acc": account, "c": bed.contact},
    )
    definition = _definition(
        [
            _step("intake", next="pause"),
            {
                "id": "pause",
                "kind": "wait",
                "title": "Cooling off",
                "config": {"duration_hours": 12},
                "next": "open",
            },
            _step("open", scope="account"),
        ]
    )
    flow, version = await _publish(sess, bed, definition)
    created = await orchestrator.start_execution(
        sess,
        tenant_id=bed.tenant,
        flow_id=flow,
        flow_version_id=version,
        definition=definition,
        graph=ApplicationGraph(
            participants=(Participant(bed.contact, "individual", frozenset({"owner"})),),
            accounts=(planning.AccountSpec(account, (bed.contact,)),),
        ),
        application_id=bed.application,
    )
    execution_id = int(created["id"])
    profile_case, account_case = (
        int(r)
        for r in (
            await sess.execute(
                text(
                    "SELECT id FROM flows.cases WHERE execution_id = :e ORDER BY "
                    "  CASE kind WHEN 'profile' THEN 0 ELSE 1 END"
                ),
                {"e": execution_id},
            )
        )
        .scalars()
        .all()
    )

    # Nothing is armed yet: the account case is parked at `intake`, which it does not own.
    armed = (
        await sess.execute(
            text("SELECT count(*) FROM flows.timers WHERE case_id = ANY(:c)"),
            {"c": [profile_case, account_case]},
        )
    ).scalar_one()
    assert armed == 0
    # WHY it is held changed when ordering learned to cross tiers, though the hold itself did not.
    # `open`'s compiled prerequisite is `intake`, which lives in the profile case; that used to
    # count as satisfied (a prerequisite "at another tier" was skipped), so the only thing left
    # holding the step was the control gate. The dependency is now seen, and it is checked first —
    # so the step names the earlier of its two reasons. Once `intake` settles the gate takes over,
    # which is what the rest of this test asserts.
    assert (await _steps(sess, account_case))["open"]["data"].get("awaiting") == ["intake"]

    await _complete(sess, bed, profile_case, "intake")
    # The account case has now reached the wait and carries its OWN wake-up (flows.timers is keyed
    # by case + node, and a case is what a resume or an `exit` acts on). The profile case reached
    # it too, but it has nothing left in its own tier — so it completed, and completing retired the
    # wake-up it had just armed. A finished case must not wake up next Tuesday.
    armed_nodes = (
        (
            await sess.execute(
                text(
                    "SELECT case_id, node_id, state FROM flows.timers "
                    "WHERE case_id = ANY(:c) ORDER BY case_id"
                ),
                {"c": [profile_case, account_case]},
            )
        )
        .mappings()
        .all()
    )
    assert {(int(r["case_id"]), str(r["node_id"]), str(r["state"])) for r in armed_nodes} == {
        (profile_case, "pause", "cancelled"),
        (account_case, "pause", "pending"),
    }
    assert await _case_status(sess, profile_case) == "completed"
    assert (await _steps(sess, account_case))["open"]["data"].get("instantiated") is not True

    await orchestrator.resume_from_timer(
        sess, case_id=account_case, node_id="pause", kind=timers.RESUME_WAIT
    )
    assert (await _steps(sess, account_case))["open"]["data"].get("instantiated") is True


# ---------------------------------------------------------------------------------------------
# Joint owners — a fanned step advances the graph for EACH owner as they settle their own copy
# ---------------------------------------------------------------------------------------------


def _owner_step(node_id: str, **kw: Any) -> dict[str, Any]:
    """An account-scoped task fanned to every owner — the shape of every terms step in the seed."""
    return _step(node_id, scope="account", per_participant=True, **kw)


async def _joint_account(sess: AsyncSession, bed: _Bed) -> tuple[int, int]:
    """A second owner and an account shared with the bed's contact → (second contact, account)."""
    s = uuid.uuid4().hex[:8]
    second = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, email, first_name, surname) "
                    "VALUES (:t, :u, 'individual', :e, 'Jane', 'Vale') RETURNING id"
                ),
                {"t": bed.tenant, "u": f"c2_{s}", "e": f"j_{s}@t.test"},
            )
        ).scalar_one()
    )
    account = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO platform.accounts "
                    "  (tenant_id, contact_id, application_id, status, uid) "
                    "VALUES (:t, :c, :a, 'draft', :u) RETURNING id"
                ),
                {"t": bed.tenant, "c": bed.contact, "a": bed.application, "u": f"acc_{s}"},
            )
        ).scalar_one()
    )
    for contact in (bed.contact, second):
        await sess.execute(
            text(
                "INSERT INTO crm.account_contacts "
                "  (tenant_id, application_id, account_id, contact_id, role) "
                "VALUES (:t, :a, :acc, :c, CAST('owner' AS crm.account_role))"
            ),
            {"t": bed.tenant, "a": bed.application, "acc": account, "c": contact},
        )
    return second, account


async def _start_joint(
    sess: AsyncSession,
    bed: _Bed,
    definition: dict[str, Any],
    *,
    second: int,
    account: int,
    memory: dict[str, Any] | None = None,
) -> tuple[int, int]:
    """Publish + start for two owners of one account. Returns (execution_id, the account case)."""
    flow, version = await _publish(sess, bed, definition)
    created = await orchestrator.start_execution(
        sess,
        tenant_id=bed.tenant,
        flow_id=flow,
        flow_version_id=version,
        definition=definition,
        graph=ApplicationGraph(
            participants=(
                Participant(bed.contact, "individual", frozenset({"owner"})),
                Participant(second, "individual", frozenset({"owner"})),
            ),
            accounts=(planning.AccountSpec(account, (bed.contact, second)),),
        ),
        application_id=bed.application,
        memory=memory,
    )
    execution_id = int(created["id"])
    account_case = int(
        (
            await sess.execute(
                text(
                    "SELECT id FROM flows.cases WHERE execution_id = :e AND kind = 'account' "
                    "ORDER BY id LIMIT 1"
                ),
                {"e": execution_id},
            )
        ).scalar_one()
    )
    return execution_id, account_case


async def _lane(sess: AsyncSession, case_id: int, key: str, participant: int) -> dict[str, Any]:
    """One owner's instance of a fanned step."""
    return dict(
        (
            await sess.execute(
                text(
                    "SELECT status, data FROM flows.case_steps "
                    "WHERE case_id = :c AND step_key = :k AND participant_subject_id = :p"
                ),
                {"c": case_id, "k": key, "p": participant},
            )
        )
        .mappings()
        .one()
    )


async def _complete_as(
    sess: AsyncSession, bed: _Bed, case_id: int, step_key: str, participant: int
) -> None:
    await orchestrator.apply_step_event(
        sess,
        bed.tenant,
        StepEvent(StepSignal.COMPLETED, case_id, step_key, participant_subject_id=participant),
    )


#: general ─→ isa? ─┬─ isa ─→ isa_terms ─→ platform ─→ pause ─→ open
#:                  └─ else ─────────────→ platform
#: The seed's terms spine in miniature: every terms step fanned to each owner, a shared step at
#: the end behind a cooling-off wait.
JOINT_TERMS_FLOW = _definition(
    [
        _owner_step("general", next="is_isa"),
        {
            "id": "is_isa",
            "kind": "branch",
            "title": "Opening an ISA?",
            "config": {
                "branches": [
                    {
                        "id": "b1",
                        "label": "ISA",
                        "conditions": {
                            "field": "memory.product",
                            "operator": "equals",
                            "value": "isa",
                        },
                        "next": "isa_terms",
                    },
                    {"id": "b2", "label": "Otherwise", "conditions": None, "next": "platform"},
                ]
            },
        },
        _owner_step("isa_terms", next="platform"),
        _owner_step("platform", next="pause"),
        {
            "id": "pause",
            "kind": "wait",
            "title": "Cooling off",
            "config": {"duration_days": 7},
            "next": "open",
        },
        _step("open", scope="account"),
    ]
)


@pytest.mark.asyncio
async def test_each_owner_passes_a_fork_as_soon_as_their_own_step_is_done(
    service_session: AsyncSession,
) -> None:
    """Joe and Jane share an account. Joe signs in and accepts the general declaration; the
    platform terms must be his to accept NOW, not once Jane has signed in and accepted hers.

    The fork after the declaration was walked for the case only once EVERY owner's instance had
    settled, so the steps behind it stayed ``gated_on`` the fork for everyone until the slowest
    owner caught up. Each owner's later step still waits on their OWN earlier one — that is the
    dependency pass, which was right all along.
    """
    sess = service_session
    bed = await _bed(sess)
    joe = bed.contact
    jane, account = await _joint_account(sess, bed)
    execution_id, case_id = await _start_joint(
        sess, bed, JOINT_TERMS_FLOW, second=jane, account=account, memory={"product": "gia"}
    )

    for owner in (joe, jane):
        assert (await _lane(sess, case_id, "general", owner))["data"].get("instantiated") is True
        # Held — the dependency pass names its reason first, the fork's gate takes over after.
        held = await _lane(sess, case_id, "platform", owner)
        assert held["data"].get("instantiated") is not True
        assert "general" in held["data"].get("awaiting", [])

    # Joe accepts the declaration → HIS platform terms are released; Jane's are held on hers.
    await _complete_as(sess, bed, case_id, "general", joe)
    joes = await _lane(sess, case_id, "platform", joe)
    assert joes["data"].get("instantiated") is True, joes
    assert "gated_on" not in joes["data"] and "awaiting" not in joes["data"]
    janes = await _lane(sess, case_id, "platform", jane)
    assert janes["data"].get("instantiated") is not True
    assert janes["data"].get("awaiting") == ["general"], janes
    # The fork was decided once, for the case: the ISA arm is elided for BOTH owners.
    for owner in (joe, jane):
        assert (await _lane(sess, case_id, "isa_terms", owner))["status"] == "skipped"

    # Jane catches up → hers is released too.
    await _complete_as(sess, bed, case_id, "general", jane)
    janes = await _lane(sess, case_id, "platform", jane)
    assert janes["data"].get("instantiated") is True, janes
    assert "awaiting" not in janes["data"]

    # The cooling-off is the CASE's: it does not start until both have accepted the platform terms.
    await _complete_as(sess, bed, case_id, "platform", joe)
    armed = (
        await sess.execute(
            text("SELECT count(*) FROM flows.timers WHERE case_id = :c"), {"c": case_id}
        )
    ).scalar_one()
    assert armed == 0, "one owner's acceptance must not start the cooling-off for the account"
    assert (await _steps(sess, case_id))["open"]["data"].get("instantiated") is not True

    await _complete_as(sess, bed, case_id, "platform", jane)
    armed = (
        await sess.execute(
            text("SELECT count(*) FROM flows.timers WHERE case_id = :c AND state = 'pending'"),
            {"c": case_id},
        )
    ).scalar_one()
    assert armed == 1
    execution_status = (
        await sess.execute(
            text("SELECT status FROM flows.executions WHERE id = :e"), {"e": execution_id}
        )
    ).scalar_one()
    assert execution_status != "completed"


#: general ─→ isa? ─┬─ isa ─→ isa_terms
#:                  └─ else ─→ pause ─→ platform
#: A case-level node DIRECTLY behind the fork: the first owner's walk must stop short of it, and
#: the case's own walk must still reach it through the fork it already decided.
JOINT_FORK_TO_WAIT_FLOW = _definition(
    [
        _owner_step("general", next="is_isa"),
        {
            "id": "is_isa",
            "kind": "branch",
            "title": "Opening an ISA?",
            "config": {
                "branches": [
                    {
                        "id": "b1",
                        "label": "ISA",
                        "conditions": {
                            "field": "memory.product",
                            "operator": "equals",
                            "value": "isa",
                        },
                        "next": "isa_terms",
                    },
                    {"id": "b2", "label": "Otherwise", "conditions": None, "next": "pause"},
                ]
            },
        },
        _owner_step("isa_terms"),
        {
            "id": "pause",
            "kind": "wait",
            "title": "Cooling off",
            "config": {"duration_days": 7},
            "next": "platform",
        },
        _owner_step("platform"),
    ]
)


@pytest.mark.asyncio
async def test_a_wait_behind_a_fork_is_reached_once_every_owner_has_passed_it(
    service_session: AsyncSession,
) -> None:
    """The first owner's walk decides the fork but stops short of the wait behind it. When the
    last owner settles, the case's walk finds the fork already decided — and must carry on down
    the recorded arm to arm the wait, rather than treat the visited fork as a dead end."""
    sess = service_session
    bed = await _bed(sess)
    joe = bed.contact
    jane, account = await _joint_account(sess, bed)
    _, case_id = await _start_joint(
        sess, bed, JOINT_FORK_TO_WAIT_FLOW, second=jane, account=account, memory={"product": "gia"}
    )

    await _complete_as(sess, bed, case_id, "general", joe)
    armed = (
        await sess.execute(
            text("SELECT count(*) FROM flows.timers WHERE case_id = :c"), {"c": case_id}
        )
    ).scalar_one()
    assert armed == 0
    # The fork WAS decided by Joe's walk — the ISA arm is already elided.
    assert (await _lane(sess, case_id, "isa_terms", joe))["status"] == "skipped"

    await _complete_as(sess, bed, case_id, "general", jane)
    armed = (
        await sess.execute(
            text("SELECT count(*) FROM flows.timers WHERE case_id = :c AND state = 'pending'"),
            {"c": case_id},
        )
    ).scalar_one()
    assert armed == 1
    for owner in (joe, jane):
        lane = await _lane(sess, case_id, "platform", owner)
        assert lane["data"].get("instantiated") is not True

    await orchestrator.resume_from_timer(
        sess, case_id=case_id, node_id="pause", kind=timers.RESUME_WAIT
    )
    for owner in (joe, jane):
        assert (await _lane(sess, case_id, "platform", owner))["data"].get("instantiated") is True


@pytest.mark.asyncio
async def test_cancelling_an_execution_retires_its_wake_ups(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    bed = await _bed(sess)
    execution_id, case_id = await _start(sess, bed, WAIT_FLOW)
    await _complete(sess, bed, case_id, "intake")
    assert (
        await sess.execute(
            text("SELECT count(*) FROM flows.timers WHERE case_id = :c AND state = 'pending'"),
            {"c": case_id},
        )
    ).scalar_one() == 1

    await orchestrator.cancel_execution(
        sess, tenant_id=bed.tenant, execution_id=execution_id, reason="started in error"
    )
    states = (
        (
            await sess.execute(
                text("SELECT state FROM flows.timers WHERE case_id = :c"), {"c": case_id}
            )
        )
        .scalars()
        .all()
    )
    assert set(states) == {"cancelled"}


# ---------------------------------------------------------------------------------------------
# exit
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_latches_the_case_and_retires_its_wake_ups(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    bed = await _bed(sess)
    execution_id, case_id = await _start(sess, bed, EXIT_FLOW)
    # A wake-up armed elsewhere in the case: a cancelled journey must not wake up next Tuesday.
    from datetime import UTC, datetime, timedelta

    await timers.schedule(
        sess,
        tenant_id=bed.tenant,
        case_id=case_id,
        node_id="some-other-node",
        kind=timers.KIND_WAIT,
        fire_at=datetime.now(UTC) + timedelta(days=1),
    )

    await _complete(sess, bed, case_id, "intake")

    assert await _case_status(sess, case_id) == "cancelled"
    timer_state = (
        await sess.execute(
            text("SELECT state FROM flows.timers WHERE case_id = :c"), {"c": case_id}
        )
    ).scalar_one()
    assert timer_state == "cancelled"
    fact = (
        await sess.execute(
            text(
                "SELECT payload FROM flows.domain_events "
                "WHERE case_id = :c AND event_type = 'case.cancelled'"
            ),
            {"c": case_id},
        )
    ).scalar_one()
    assert fact["reason"] == "the client withdrew"
    assert fact["node"] == "stop"
    # The execution rolls up behind it.
    exec_status = (
        await sess.execute(
            text("SELECT status FROM flows.executions WHERE id = :e"), {"e": execution_id}
        )
    ).scalar_one()
    assert exec_status == "cancelled"


@pytest.mark.asyncio
async def test_exit_skips_the_work_that_will_never_happen(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    bed = await _bed(sess)
    definition = _definition(
        [
            _step("intake", next="stop"),
            {"id": "stop", "kind": "exit", "title": "Done", "config": {"outcome": "completed"}},
            # Reachable only via depends_on, so it is planned and outstanding when the exit fires.
            _step("leftover", depends_on=["intake"]),
        ],
        entry="intake",
    )
    _, case_id = await _start(sess, bed, definition)
    await _complete(sess, bed, case_id, "intake")

    steps = await _steps(sess, case_id)
    assert steps["leftover"]["status"] == "skipped"
    assert await _case_status(sess, case_id) == "completed"


# ---------------------------------------------------------------------------------------------
# start_flow
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_chains_into_the_named_flow(service_session: AsyncSession) -> None:
    sess = service_session
    bed = await _bed(sess)
    child_definition = _definition([_step("review")])
    child_flow, _ = await _publish(sess, bed, child_definition)
    child_code = str(
        (
            await sess.execute(
                text("SELECT code FROM flows.flows WHERE id = :f"), {"f": child_flow}
            )
        ).scalar_one()
    )

    parent_execution, case_id = await _start(
        sess, bed, _chain_flow(child_code, carry=True), memory={"risk_band": "high"}
    )
    await _complete(sess, bed, case_id, "intake")

    child = (
        (
            await sess.execute(
                text(
                    "SELECT e.id, e.data, c.id AS case_id, c.initiation "
                    "FROM flows.executions e JOIN flows.cases c ON c.execution_id = e.id "
                    "WHERE e.flow_id = :f"
                ),
                {"f": child_flow},
            )
        )
        .mappings()
        .one()
    )
    assert child["data"]["chain_depth"] == 1
    assert child["initiation"] == "flow"
    # carry_memory: the child knows what the parent learned.
    assert await memory_mod.read(sess, int(child["case_id"])) == {"risk_band": "high"}
    fact = (
        await sess.execute(
            text(
                "SELECT payload FROM flows.domain_events "
                "WHERE execution_id = :e AND event_type = 'flow.started'"
            ),
            {"e": parent_execution},
        )
    ).scalar_one()
    assert fact["flow_code"] == child_code


@pytest.mark.asyncio
async def test_start_flow_is_refused_past_the_chain_depth_cap(
    service_session: AsyncSession,
) -> None:
    """A1's counter and A1's ceiling — a journey that starts a journey is the same runaway the
    automation consumer already guards, so there is one guard, not two."""
    sess = service_session
    bed = await _bed(sess)
    await sess.execute(
        text("INSERT INTO flows.automation_settings (tenant_id, max_chain_depth) VALUES (:t, 1)"),
        {"t": bed.tenant},
    )
    child_definition = _definition([_step("review")])
    child_flow, _ = await _publish(sess, bed, child_definition)
    child_code = str(
        (
            await sess.execute(
                text("SELECT code FROM flows.flows WHERE id = :f"), {"f": child_flow}
            )
        ).scalar_one()
    )

    parent_execution, case_id = await _start(sess, bed, _chain_flow(child_code, carry=False))
    # The parent is itself already one link deep.
    await sess.execute(
        text("UPDATE flows.executions SET data = data || '{\"chain_depth\": 1}' WHERE id = :e"),
        {"e": parent_execution},
    )
    await _complete(sess, bed, case_id, "intake")

    started = (
        await sess.execute(
            text("SELECT count(*) FROM flows.executions WHERE flow_id = :f"), {"f": child_flow}
        )
    ).scalar_one()
    assert started == 0
    fact = (
        await sess.execute(
            text(
                "SELECT payload FROM flows.domain_events "
                "WHERE execution_id = :e AND event_type = 'flow.start_refused'"
            ),
            {"e": parent_execution},
        )
    ).scalar_one()
    assert "chain depth 2" in fact["reason"]


@pytest.mark.asyncio
async def test_start_flow_records_an_unpublished_target_rather_than_failing_silently(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    bed = await _bed(sess)
    parent_execution, case_id = await _start(sess, bed, _chain_flow("no-such-flow", carry=False))
    await _complete(sess, bed, case_id, "intake")

    fact = (
        await sess.execute(
            text(
                "SELECT payload FROM flows.domain_events "
                "WHERE execution_id = :e AND event_type = 'flow.start_refused'"
            ),
            {"e": parent_execution},
        )
    ).scalar_one()
    assert "no-such-flow" in fact["reason"]
    # The transition that reached it still stands.
    assert (await _steps(sess, case_id))["intake"]["status"] == "fulfilled"


# ---------------------------------------------------------------------------------------------
# memory in the running engine
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_injected_at_start_reaches_the_condition_context(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, BRANCH_FLOW, memory={"risk_band": "high", "quote": 12.5})

    ctx = await orchestrator.case_context(sess, tenant_id=bed.tenant, case_id=case_id)
    assert ctx["memory"] == {"risk_band": "high", "quote": 12.5}
    # And the rest of the registry's runtime scopes are there too.
    assert ctx["contact.type"] == "individual"
    assert ctx["case.status"] in {"draft", "active"}
    assert ctx["application.custody"] is None
    assert "time.hour" in ctx


@pytest.mark.asyncio
async def test_an_authored_mapping_promotes_answers_into_memory(
    service_session: AsyncSession,
) -> None:
    """The orchestrator's promotion seam, driven by ``fill_form.config.remember``."""
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, BRANCH_FLOW)

    await orchestrator._promote_memory(
        sess,
        tenant_id=bed.tenant,
        case_id=case_id,
        step_key="intake",
        config={"remember": {"risk_band": "attitude_to_risk"}},
        detail={"answers": {"attitude_to_risk": "high", "national_insurance": "QQ123456C"}},
        request_item_id=None,
    )
    # Only the authored key crossed over.
    assert await memory_mod.read(sess, case_id) == {"risk_band": "high"}


@pytest.mark.asyncio
async def test_a_step_with_no_remember_mapping_promotes_nothing(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    bed = await _bed(sess)
    _, case_id = await _start(sess, bed, BRANCH_FLOW)
    await orchestrator._promote_memory(
        sess,
        tenant_id=bed.tenant,
        case_id=case_id,
        step_key="intake",
        config={"form_uid": "frm_x"},
        detail={"answers": {"attitude_to_risk": "high"}},
        request_item_id=None,
    )
    assert await memory_mod.read(sess, case_id) == {}


@pytest.mark.asyncio
async def test_a_raised_task_prints_the_journey_s_memory(service_session: AsyncSession) -> None:
    """`{{ memory.key || fallback }}` in a step's raise-a-task spec, rendered at the transition."""
    sess = service_session
    bed = await _bed(sess)
    definition = _definition(
        [
            _step(
                "intake",
                tasks=[
                    {
                        "on": "completed",
                        "assign_to": {"kind": "role", "role": "compliance"},
                        "title": "Review {{ memory.risk_band || unrated }} client",
                        "description": "Client {{ contact.type }}, band {{ memory.risk_band }}",
                    }
                ],
            )
        ]
    )
    _, case_id = await _start(sess, bed, definition, memory={"risk_band": "high"})
    await _complete(sess, bed, case_id, "intake")

    payload = (
        await sess.execute(
            text(
                "SELECT payload FROM flows.outbox WHERE tenant_id = :t "
                "AND event_type = 'task.requested' ORDER BY id DESC LIMIT 1"
            ),
            {"t": bed.tenant},
        )
    ).scalar_one()
    assert payload["spec"]["title"] == "Review high client"
    assert payload["spec"]["description"] == "Client individual, band high"
    # Structure is untouched — only the human-facing copy is interpolated.
    assert payload["spec"]["assign_to"] == {"kind": "role", "role": "compliance"}


@pytest.mark.asyncio
async def test_a_missing_memory_key_prints_the_authored_fallback(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    bed = await _bed(sess)
    definition = _definition(
        [
            _step(
                "intake",
                tasks=[
                    {
                        "on": "completed",
                        "assign_to": {"kind": "role", "role": "compliance"},
                        "title": "Review {{ memory.risk_band || unrated }} client",
                    }
                ],
            )
        ]
    )
    _, case_id = await _start(sess, bed, definition)
    await _complete(sess, bed, case_id, "intake")

    payload = (
        await sess.execute(
            text(
                "SELECT payload FROM flows.outbox WHERE tenant_id = :t "
                "AND event_type = 'task.requested' ORDER BY id DESC LIMIT 1"
            ),
            {"t": bed.tenant},
        )
    ).scalar_one()
    assert payload["spec"]["title"] == "Review unrated client"
