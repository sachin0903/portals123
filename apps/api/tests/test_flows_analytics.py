"""The step funnel — the pure shaping, and the aggregates against seeded rows.

Two halves, split the way the module is: ``funnel_nodes``/``funnel_edges`` are pure and get the
awkward cases (a re-convergent branch, a node nobody reached, a legacy definition), while
``funnel``/``versions`` need a real Postgres because they are the SQL and the SQL is the claim —
``percentile_cont`` over a FILTER, the ``jsonb_object_keys`` read of the graph walk's own visited
map, and the version cohort boundary that keeps two definitions out of one funnel.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.flows import analytics
from app.flows import graph as graph_mod
from app.flows.definitions import validate_definition


def _step(node_id: str, **kw: Any) -> dict[str, Any]:
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


#: intake → risk? ─┬─ offshore ─→ edd ─→ notify
#: ················└─ else ──────────→ notify
BRANCHING = validate_definition(
    {
        "version": 3,
        "entry": "intake",
        "nodes": [
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
        ],
    }
)


# ---------------------------------------------------------------------------------------------
# Shaping (pure)
# ---------------------------------------------------------------------------------------------


def test_nodes_keep_the_definitions_order_not_the_volume_order() -> None:
    """A funnel is read top to bottom. Sorting by volume — the obvious alternative — would reorder
    the journey and make the drop-off column meaningless."""
    parsed = graph_mod.parse(BRANCHING)
    nodes = analytics.funnel_nodes(
        parsed,
        steps={
            "notify": {
                "reached": 99,
                "completed": 99,
                "by_status": {},
                "instances": 99,
                "median_seconds": None,
                "p90_seconds": None,
                "step_type": "task",
            }
        },
        visits={},
        branch_choices={},
    )
    assert [n["node_id"] for n in nodes] == ["intake", "risk", "edd", "notify"]


def test_a_node_nobody_reached_is_kept_with_zeroes() -> None:
    """'Nobody has ever got to the offshore review' is the single most useful row a funnel can
    show, and dropping empty rows hides exactly that."""
    parsed = graph_mod.parse(BRANCHING)
    nodes = {
        n["node_id"]: n
        for n in analytics.funnel_nodes(parsed, steps={}, visits={}, branch_choices={})
    }
    assert nodes["edd"]["reached"] == 0
    assert nodes["edd"]["completed"] == 0


def test_a_control_node_is_counted_from_the_graph_walks_visited_map() -> None:
    """A branch never becomes a case_steps row, so a funnel built only from step rows has holes
    exactly where the decisions are."""
    parsed = graph_mod.parse(BRANCHING)
    nodes = {
        n["node_id"]: n
        for n in analytics.funnel_nodes(
            parsed, steps={}, visits={"risk": 40}, branch_choices={"risk": {"edd": 9, "notify": 31}}
        )
    }
    assert nodes["risk"]["reached"] == 40
    assert nodes["risk"]["is_work"] is False
    arms = {a["id"]: a for a in nodes["risk"]["arms"]}
    assert arms["b1"]["count"] == 9
    assert arms["b2"]["count"] == 31
    assert arms["b2"]["is_else"] is True


def test_a_step_prefers_its_own_rollup_over_the_visit_count() -> None:
    """The step rows know status and duration; the visited map only knows the node was entered."""
    parsed = graph_mod.parse(BRANCHING)
    nodes = {
        n["node_id"]: n
        for n in analytics.funnel_nodes(
            parsed,
            steps={
                "intake": {
                    "reached": 30,
                    "completed": 25,
                    "instances": 33,
                    "by_status": {"fulfilled": 25, "pending": 8},
                    "median_seconds": 3600.0,
                    "p90_seconds": 7200.0,
                    "step_type": "task",
                }
            },
            visits={"intake": 999},
            branch_choices={},
        )
    }
    assert nodes["intake"]["reached"] == 30
    assert nodes["intake"]["median_seconds"] == 3600.0
    assert nodes["intake"]["by_status"] == {"fulfilled": 25, "pending": 8}


def _nodes(**reach: tuple[int, int]) -> list[dict[str, Any]]:
    """`node_id -> (reached, completed)`, in the definition's order."""
    parsed = graph_mod.parse(BRANCHING)
    return analytics.funnel_nodes(
        parsed,
        steps={
            nid: {
                "reached": r,
                "completed": c,
                "instances": r,
                "by_status": {},
                "median_seconds": None,
                "p90_seconds": None,
                "step_type": "task",
            }
            for nid, (r, c) in reach.items()
        },
        visits={nid: r for nid, (r, _) in reach.items()},
        branch_choices={},
    )


def test_drop_off_is_measured_along_edges_not_between_adjacent_rows() -> None:
    parsed = graph_mod.parse(BRANCHING)
    nodes = _nodes(intake=(100, 80), risk=(72, 72), edd=(20, 15), notify=(52, 50))
    edges = {(e["from"], e["to"]): e for e in analytics.funnel_edges(parsed, nodes)}
    # 80 finished the intake; only 72 got as far as the decision.
    assert edges[("intake", "risk")]["lost"] == 8
    assert edges[("intake", "risk")]["rate"] == pytest.approx(0.9)
    # A branch fans out: both arms are edges from the same node, and neither is "the next row".
    assert set(edges) >= {("risk", "edd"), ("risk", "notify"), ("edd", "notify")}


def test_a_nodes_own_leak_is_its_outstanding_count_not_an_edge() -> None:
    """100 reached the intake and 80 completed it: the 20 are stuck IN the node, not lost along an
    edge. An author reading only edges would see a clean journey with twenty clients in limbo."""
    parsed = graph_mod.parse(BRANCHING)
    nodes = {n["node_id"]: n for n in _nodes(intake=(100, 80), risk=(80, 80))}
    assert nodes["intake"]["reached"] - nodes["intake"]["completed"] == 20
    edges = {(e["from"], e["to"]): e for e in analytics.funnel_edges(parsed, list(nodes.values()))}
    assert edges[("intake", "risk")]["lost"] == 0


def test_a_reconvergent_target_reports_a_gain_rather_than_a_negative_drop_off() -> None:
    """`notify` has two predecessors, so it reaches more cases than either one completed. A funnel
    that renders that as '-40% drop-off' is a bug report waiting to happen."""
    parsed = graph_mod.parse(BRANCHING)
    nodes = _nodes(intake=(100, 100), risk=(100, 100), edd=(20, 20), notify=(100, 95))
    edges = {(e["from"], e["to"]): e for e in analytics.funnel_edges(parsed, nodes)}
    assert edges[("edd", "notify")]["lost"] == 0
    assert edges[("edd", "notify")]["gained"] == 80


def test_an_edge_out_of_a_node_nobody_completed_has_no_rate() -> None:
    """Zero completions is not a zero rate — it is no rate at all, and dividing would be a lie
    dressed as a percentage."""
    parsed = graph_mod.parse(BRANCHING)
    edges = {(e["from"], e["to"]): e for e in analytics.funnel_edges(parsed, _nodes())}
    assert edges[("intake", "risk")]["rate"] is None


# ---------------------------------------------------------------------------------------------
# The aggregates (real Postgres)
# ---------------------------------------------------------------------------------------------


async def _scalar(sess: AsyncSession, sql: str, **params: Any) -> Any:
    return (await sess.execute(text(sql), params)).scalar_one_or_none()


class _Seed:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


async def _seed_flow(sess: AsyncSession, definition: dict[str, Any]) -> _Seed:
    s = uuid.uuid4().hex[:8]
    tenant = int(
        await _scalar(
            sess,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
            u=f"fn_{s}",
            a=f"fn-{s}",
            n=f"FN {s}",
        )
    )
    flow = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
            "VALUES (:u, :t, :c, 'Onboarding', 'published') RETURNING id",
            u=f"flw_{s}",
            t=tenant,
            c=f"onb_{s}",
        )
    )
    version = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status, "
            "  definition, published_at) "
            "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id",
            u=f"fv_{s}",
            t=tenant,
            f=flow,
            d=json.dumps(definition),
        )
    )
    await sess.execute(
        text("UPDATE flows.flows SET current_published_version_id = :v WHERE id = :f"),
        {"v": version, "f": flow},
    )
    return _Seed(slug=s, tenant=tenant, flow=flow, flow_uid=f"flw_{s}", version=version)


async def _case(
    sess: AsyncSession,
    seed: _Seed,
    *,
    version: int | None = None,
    status: str = "active",
    created_at: datetime | None = None,
    visited: dict[str, Any] | None = None,
    steps: list[tuple[str, str, int | None]] = (),
) -> int:
    """One case with its step rows. ``steps`` is (step_id, status, minutes_to_complete)."""
    started = created_at or datetime.now(UTC) - timedelta(days=1)
    case_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.cases (uid, tenant_id, flow_id, flow_version_id, status, "
            "  created_at, completed_at, data) "
            "VALUES (:u, :t, :f, :v, :s, :ca, :co, CAST(:d AS jsonb)) RETURNING id",
            u=f"cas_{uuid.uuid4().hex[:12]}",
            t=seed.tenant,
            f=seed.flow,
            v=version or seed.version,
            s=status,
            ca=started,
            co=started + timedelta(days=2) if status == "completed" else None,
            d=json.dumps({"graph": {"visited": visited}} if visited else {}),
        )
    )
    for step_id, step_status, minutes in steps:
        await sess.execute(
            text(
                "INSERT INTO flows.case_steps (tenant_id, case_id, step_id, step_key, step_type, "
                "  status, created_at, completed_at) "
                "VALUES (:t, :c, :sid, :sid, 'task', :st, :ca, :co)"
            ),
            {
                "t": seed.tenant,
                "c": case_id,
                "sid": step_id,
                "st": step_status,
                "ca": started,
                "co": started + timedelta(minutes=minutes) if minutes is not None else None,
            },
        )
    return case_id


@pytest.mark.asyncio
async def test_the_rollup_counts_reach_completion_and_the_status_split(
    service_session: AsyncSession,
) -> None:
    seed = await _seed_flow(service_session, BRANCHING)
    await _case(service_session, seed, steps=[("intake", "fulfilled", 60)])
    await _case(service_session, seed, steps=[("intake", "fulfilled", 180)])
    await _case(service_session, seed, steps=[("intake", "pending", None)])
    await _case(service_session, seed, steps=[("intake", "skipped", None)])

    result = await analytics.funnel(
        service_session,
        version_id=seed.version,
        since=datetime.now(UTC) - timedelta(days=7),
        until=datetime.now(UTC) + timedelta(days=1),
    )
    intake = result["steps"]["intake"]
    assert intake["reached"] == 4
    assert intake["completed"] == 2
    assert intake["by_status"] == {"fulfilled": 2, "pending": 1, "skipped": 1}
    # percentile_cont over the two fulfilled durations only — a step still open has no duration,
    # and counting it as zero would make a stalled node look like the fastest on the board.
    assert intake["median_seconds"] == pytest.approx(7200.0)
    assert intake["p90_seconds"] == pytest.approx(10080.0)


@pytest.mark.asyncio
async def test_a_fanned_out_step_counts_cases_not_instances(
    service_session: AsyncSession,
) -> None:
    """Three joint owners each getting a terms step is ONE case reaching the node, not three —
    conflating them would make every fan-out node look like a funnel expansion."""
    seed = await _seed_flow(service_session, BRANCHING)
    case_id = await _case(service_session, seed, steps=[("intake", "fulfilled", 10)])
    # A fan-out writes `<key>:<contact>` as the step_id (the UNIQUE(case_id, step_id) key) and
    # keeps the definition's own id in step_key — which is why the roll-up groups on step_key.
    for participant in (2, 3):
        await service_session.execute(
            text(
                "INSERT INTO flows.case_steps (tenant_id, case_id, step_id, step_key, step_type, "
                "  status, participant_subject_id, completed_at) "
                "VALUES (:t, :c, :sid, 'intake', 'task', 'fulfilled', :p, now())"
            ),
            {"t": seed.tenant, "c": case_id, "sid": f"intake:{participant}", "p": participant},
        )
    result = await analytics.funnel(
        service_session,
        version_id=seed.version,
        since=datetime.now(UTC) - timedelta(days=7),
        until=datetime.now(UTC) + timedelta(days=1),
    )
    assert result["steps"]["intake"]["reached"] == 1
    assert result["steps"]["intake"]["instances"] == 3


@pytest.mark.asyncio
async def test_control_nodes_and_branch_arms_come_out_of_the_visited_map(
    service_session: AsyncSession,
) -> None:
    seed = await _seed_flow(service_session, BRANCHING)
    for _ in range(3):
        await _case(service_session, seed, visited={"intake": "intake", "risk": "notify"})
    await _case(service_session, seed, visited={"intake": "intake", "risk": "edd"})

    result = await analytics.funnel(
        service_session,
        version_id=seed.version,
        since=datetime.now(UTC) - timedelta(days=7),
        until=datetime.now(UTC) + timedelta(days=1),
    )
    assert result["visits"]["risk"] == 4
    assert result["branch_choices"]["risk"] == {"notify": 3, "edd": 1}
    # A step's own entry (`{node: node}`) is not an arm choice and must not appear as one.
    assert "intake" not in result["branch_choices"]


@pytest.mark.asyncio
async def test_a_different_version_is_a_different_cohort(
    service_session: AsyncSession,
) -> None:
    """Mixing cases from before and after a definition change into one funnel is how a firm
    concludes their onboarding got worse when what happened is that they added a step."""
    seed = await _seed_flow(service_session, BRANCHING)
    other = int(
        await _scalar(
            service_session,
            "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status, "
            "  definition) VALUES (:u, :t, :f, 2, 'draft', CAST(:d AS jsonb)) RETURNING id",
            u=f"fv2_{seed.slug}",
            t=seed.tenant,
            f=seed.flow,
            d=json.dumps(BRANCHING),
        )
    )
    await _case(service_session, seed, steps=[("intake", "fulfilled", 10)])
    await _case(service_session, seed, version=other, steps=[("intake", "failed", None)])

    v1 = await analytics.funnel(
        service_session,
        version_id=seed.version,
        since=datetime.now(UTC) - timedelta(days=7),
        until=datetime.now(UTC) + timedelta(days=1),
    )
    v2 = await analytics.funnel(
        service_session,
        version_id=other,
        since=datetime.now(UTC) - timedelta(days=7),
        until=datetime.now(UTC) + timedelta(days=1),
    )
    assert v1["steps"]["intake"]["by_status"] == {"fulfilled": 1}
    assert v2["steps"]["intake"]["by_status"] == {"failed": 1}
    assert v1["cases"]["total"] == 1 and v2["cases"]["total"] == 1


@pytest.mark.asyncio
async def test_the_date_range_bounds_the_cohort_by_when_the_case_started(
    service_session: AsyncSession,
) -> None:
    seed = await _seed_flow(service_session, BRANCHING)
    await _case(
        service_session,
        seed,
        created_at=datetime.now(UTC) - timedelta(days=200),
        steps=[("intake", "fulfilled", 5)],
    )
    await _case(service_session, seed, steps=[("intake", "fulfilled", 5)])
    recent = await analytics.funnel(
        service_session,
        version_id=seed.version,
        since=datetime.now(UTC) - timedelta(days=7),
        until=datetime.now(UTC) + timedelta(days=1),
    )
    assert recent["steps"]["intake"]["reached"] == 1
    assert recent["cases"]["total"] == 1


@pytest.mark.asyncio
async def test_case_totals_split_by_state_and_time_the_completed_ones(
    service_session: AsyncSession,
) -> None:
    seed = await _seed_flow(service_session, BRANCHING)
    await _case(service_session, seed, status="completed")
    await _case(service_session, seed, status="completed")
    await _case(service_session, seed, status="blocked")
    result = await analytics.funnel(
        service_session,
        version_id=seed.version,
        since=datetime.now(UTC) - timedelta(days=7),
        until=datetime.now(UTC) + timedelta(days=1),
    )
    assert result["cases"]["by_status"] == {"completed": 2, "blocked": 1}
    assert result["cases"]["total"] == 3
    assert result["cases"]["median_seconds"] == pytest.approx(172800.0)  # two days


@pytest.mark.asyncio
async def test_an_empty_cohort_returns_zeroes_rather_than_nothing(
    service_session: AsyncSession,
) -> None:
    """A firm that has just published a journey has no cases yet, and that has to be an empty
    state rather than a broken page."""
    seed = await _seed_flow(service_session, BRANCHING)
    result = await analytics.funnel(
        service_session,
        version_id=seed.version,
        since=datetime.now(UTC) - timedelta(days=7),
        until=datetime.now(UTC) + timedelta(days=1),
    )
    assert result["steps"] == {}
    assert result["cases"] == {"total": 0, "by_status": {}, "median_seconds": None}
    nodes = analytics.funnel_nodes(
        graph_mod.parse(BRANCHING), steps={}, visits={}, branch_choices={}
    )
    assert all(n["reached"] == 0 for n in nodes)


@pytest.mark.asyncio
async def test_the_version_picker_carries_each_cohorts_size(
    service_session: AsyncSession,
) -> None:
    """A version published for two hours before a fix has three cases behind it, and an author
    comparing funnels needs to see that before drawing a conclusion from it."""
    seed = await _seed_flow(service_session, BRANCHING)
    await _case(service_session, seed)
    await _case(service_session, seed)
    rows = await analytics.versions(service_session, flow_uid=seed.flow_uid)
    assert [r["version"] for r in rows] == [1]
    assert rows[0]["case_count"] == 2
    assert rows[0]["is_current"] is True
