"""The step funnel — where a published journey actually loses people.

A dry run tells an author what *should* happen. This tells them what *did*: per node, how many
cases reached it, how many completed / are waiting / blocked / skipped / failed, how long the ones
that completed took (median and p90), and the drop-off from each node to the next.

Three decisions worth knowing about
-----------------------------------
**Scoped by flow VERSION, never by flow.** A definition changes — a step is added, a branch is
rewired, a form is swapped — and mixing cases from before and after a change into one funnel is
how a firm concludes their onboarding got worse when what actually happened is that they added a
step. ``flows.cases.flow_version_id`` is pinned at start, so the version IS the cohort. The caller
picks one; the router defaults to the currently published version because that is the one an
author is looking at.

**Control nodes count too, from ``cases.data.graph.visited``.** A branch, a wait and an exit never
become a ``case_steps`` row, so a funnel built only from step rows has holes exactly where the
interesting decisions are ("how many clients went down the offshore arm?"). The graph walk already
records every node it enters on the case (``orchestrator._enter_node``), keyed by node id with the
branch's chosen successor as the value, so one ``jsonb_object_keys`` aggregate fills those holes
and a second reads the arm distribution. Nothing new is written to make this work.

**A small number of set-based queries, never a per-case fan-out.** Four aggregates run under RLS
against the firm's own rows: the version list, the case totals, the per-step roll-up (with the
percentiles computed in Postgres by ``percentile_cont``, not by pulling durations into Python) and
the per-node graph-visit counts. ``0047_flow_analytics`` adds the composite index the cohort
filter needs; before it, the version+date filter fell back to the single-column
``cases_flow_version_id_idx`` and then filtered by date row by row.

The drop-off arithmetic is pure and unit-tested (``funnel_edges``): the graph's own successor
relation, ``reached(successor) / completed(node)``, so a re-convergent branch does not double-count
and a node with two predecessors is not reported as leaking from one of them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.flows import graph as graph_mod

#: The ``flows.case_steps.status`` vocabulary (0021 + 0025's widening). Named here so the roll-up's
#: FILTER clauses and the response shape cannot drift from the CHECK constraint; a status the DB
#: allows and this module forgets would silently vanish out of the totals.
STEP_STATUSES: tuple[str, ...] = (
    "pending",
    "fulfilled",
    "skipped",
    "cancelled",
    "blocked",
    "waiting",
    "failed",
)

#: Statuses that mean "this step is not going to happen, and that is fine" — an untaken branch arm
#: or a step plan-time visibility excluded. They are counted and shown, but they are subtracted
#: from the completion rate: scoring a firm down for steps their own conditions correctly skipped
#: would make the number meaningless.
_IRRELEVANT: frozenset[str] = frozenset({"skipped", "cancelled"})


@dataclass(frozen=True)
class NodeStats:
    node_id: str
    reached: int
    completed: int
    by_status: Mapping[str, int]
    median_seconds: float | None
    p90_seconds: float | None

    @property
    def outstanding(self) -> int:
        return (
            sum(count for status, count in self.by_status.items() if status not in _IRRELEVANT)
            - self.completed
        )


def _percentiles(alias: str) -> str:
    """The two duration percentiles, over fulfilled steps only.

    ``created_at`` is when the step row was made and ``completed_at`` when it settled, so this is
    time-in-step, not time-since-case-start — which is the number an author can act on ("the ID
    upload sits for nine days"). Steps with no ``completed_at`` are excluded rather than counted as
    zero: a step still open has no duration, and treating it as instant would make a stalled node
    look like the fastest one on the board.
    """
    duration = f"EXTRACT(EPOCH FROM ({alias}.completed_at - {alias}.created_at))"
    return (
        f"percentile_cont(0.5) WITHIN GROUP (ORDER BY {duration}) "
        f"  FILTER (WHERE {alias}.status = 'fulfilled' AND {alias}.completed_at IS NOT NULL) "
        f"  AS median_seconds, "
        f"percentile_cont(0.9) WITHIN GROUP (ORDER BY {duration}) "
        f"  FILTER (WHERE {alias}.status = 'fulfilled' AND {alias}.completed_at IS NOT NULL) "
        f"  AS p90_seconds"
    )


_STATUS_FILTERS = ", ".join(
    f"count(*) FILTER (WHERE s.status = '{status}') AS n_{status}" for status in STEP_STATUSES
)

#: The per-step roll-up. One pass over the cohort's step rows, grouped by the definition's step id.
#: ``count(DISTINCT s.case_id)`` rather than ``count(*)`` for "reached", because a step fans out per
#: participant or per owner — three joint owners each getting a terms step is one case reaching the
#: node, not three, and conflating them would make every fan-out node look like a funnel expansion.
# noqa: S608 on the statement below — the only interpolations are `_STATUS_FILTERS` and
# `_percentiles`, both built at import from module-level literals (`STEP_STATUSES`, a fixed alias).
# Nothing from a request reaches the string; every request value is a bound parameter.
_STEP_ROLLUP = text(
    # COALESCE(step_key, step_id) is THE grouping key, and getting it wrong is the whole feature
    # wrong: a per-participant fan-out writes a UNIQUE-satisfying `step_id` of `<key>:<contact>`
    # (`orchestrator._fanned_step_id`) while `step_key` keeps the definition's own id, so grouping
    # by `step_id` would scatter one authored node across a row per client. `step_key` is nullable
    # because 0025 added it to a table that already had rows, and on those the id IS the key.
    "SELECT COALESCE(s.step_key, s.step_id) AS step_id, "  # noqa: S608
    "       min(s.step_type) AS step_type, "
    "       count(DISTINCT s.case_id) AS reached, "
    "       count(*) AS instances, "
    "       count(DISTINCT s.case_id) FILTER (WHERE s.status = 'fulfilled') AS completed, "
    f"      {_STATUS_FILTERS}, "
    f"      {_percentiles('s')} "
    "FROM flows.case_steps s "
    "JOIN flows.cases c ON c.id = s.case_id "
    "WHERE c.flow_version_id = :version_id "
    "  AND c.created_at >= :since AND c.created_at < :until "
    "GROUP BY COALESCE(s.step_key, s.step_id)"
)

#: Case totals for the cohort — the denominator every rate is against, and the case-level outcome
#: split. ``status`` is the engine's derived state (``states.State``), free-form by column type but
#: closed by the derivation, so it is bucketed rather than enumerated in SQL.
_CASE_TOTALS = text(
    "SELECT c.status, count(*) AS n, "
    "       percentile_cont(0.5) WITHIN GROUP ( "
    "         ORDER BY EXTRACT(EPOCH FROM (c.completed_at - c.created_at))) "
    "         FILTER (WHERE c.completed_at IS NOT NULL) AS median_seconds "
    "FROM flows.cases c "
    "WHERE c.flow_version_id = :version_id "
    "  AND c.created_at >= :since AND c.created_at < :until "
    "GROUP BY c.status"
)

#: Every node the graph walk entered, counted. The key is the node id; the value a branch stores is
#: the successor it chose, which is what makes the arm distribution readable from the same column.
#: ``jsonb_object_keys`` on a LATERAL is a per-case expansion of a small object (a case visits at
#: most ``caps.maxNodesPerFlow`` nodes), not a per-case query.
_GRAPH_VISITS = text(
    "SELECT k.node_id, count(*) AS reached "
    "FROM flows.cases c "
    "CROSS JOIN LATERAL jsonb_object_keys( "
    "  COALESCE(c.data -> 'graph' -> 'visited', '{}'::jsonb)) AS k(node_id) "
    "WHERE c.flow_version_id = :version_id "
    "  AND c.created_at >= :since AND c.created_at < :until "
    "GROUP BY k.node_id"
)

#: Which arm each branch sent cases down. The visited map stores ``{branch_id: chosen_node_id}``,
#: so the value is the arm's TARGET rather than the arm's own id — the surface resolves it back to
#: an arm through the definition, which is the only place that mapping is authored.
_BRANCH_CHOICES = text(
    "SELECT k.key AS node_id, k.value #>> '{}' AS chose, count(*) AS n "
    "FROM flows.cases c "
    "CROSS JOIN LATERAL jsonb_each( "
    "  COALESCE(c.data -> 'graph' -> 'visited', '{}'::jsonb)) AS k "
    "WHERE c.flow_version_id = :version_id "
    "  AND c.created_at >= :since AND c.created_at < :until "
    "  AND k.value #>> '{}' IS DISTINCT FROM k.key "
    "GROUP BY k.key, k.value"
)


async def versions(session: AsyncSession, *, flow_uid: str) -> list[dict[str, Any]]:
    """The version picker's options, newest first, each with the size of its cohort.

    The case count is what stops a version picker being a guessing game: a version published for
    two hours before a fix has three cases behind it, and an author comparing funnels needs to see
    that before drawing a conclusion from it.
    """
    rows = (
        await session.execute(
            text(
                "SELECT v.uid, v.version, v.status, v.published_at, "
                "       (SELECT count(*) FROM flows.cases c WHERE c.flow_version_id = v.id) "
                "         AS case_count, "
                "       (f.current_published_version_id = v.id) AS is_current "
                "FROM flows.flow_versions v "
                "JOIN flows.flows f ON f.id = v.flow_id "
                "WHERE f.uid = :u "
                "ORDER BY v.version DESC"
            ),
            {"u": flow_uid},
        )
    ).mappings()
    return [dict(r) for r in rows]


async def funnel(
    session: AsyncSession,
    *,
    version_id: int,
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    """The raw aggregates for one version's cohort. Four queries, all set-based.

    RLS is the tenant boundary here, as it is for every other read on this surface: the session is
    an ``authenticated`` one and ``flows.cases``/``case_steps`` are readable only within the
    caller's tenant lineage, so no ``tenant_id`` predicate is smuggled in from the wire.
    """
    params = {"version_id": version_id, "since": since, "until": until}
    step_rows = (await session.execute(_STEP_ROLLUP, params)).mappings().all()
    case_rows = (await session.execute(_CASE_TOTALS, params)).mappings().all()
    visit_rows = (await session.execute(_GRAPH_VISITS, params)).mappings().all()
    choice_rows = (await session.execute(_BRANCH_CHOICES, params)).mappings().all()

    steps = {
        str(r["step_id"]): {
            "step_type": r["step_type"],
            "reached": int(r["reached"]),
            "instances": int(r["instances"]),
            "completed": int(r["completed"]),
            "by_status": {s: int(r[f"n_{s}"]) for s in STEP_STATUSES if int(r[f"n_{s}"])},
            "median_seconds": _as_float(r["median_seconds"]),
            "p90_seconds": _as_float(r["p90_seconds"]),
        }
        for r in step_rows
    }
    visits = {str(r["node_id"]): int(r["reached"]) for r in visit_rows}
    choices: dict[str, dict[str, int]] = {}
    for r in choice_rows:
        if r["chose"] is not None:
            choices.setdefault(str(r["node_id"]), {})[str(r["chose"])] = int(r["n"])

    cases = {str(r["status"]): int(r["n"]) for r in case_rows}
    completed_median = next(
        (_as_float(r["median_seconds"]) for r in case_rows if r["status"] == "completed"), None
    )
    return {
        "steps": steps,
        "visits": visits,
        "branch_choices": choices,
        "cases": {
            "total": sum(cases.values()),
            "by_status": cases,
            "median_seconds": completed_median,
        },
    }


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):  # pragma: no cover — percentile_cont returns numeric or NULL
        return None


# ---------------------------------------------------------------------------------------------
# Shaping (pure — unit-tested without a database)
# ---------------------------------------------------------------------------------------------


def funnel_nodes(
    parsed: graph_mod.Graph,
    *,
    steps: Mapping[str, Mapping[str, Any]],
    visits: Mapping[str, int],
    branch_choices: Mapping[str, Mapping[str, int]],
) -> list[dict[str, Any]]:
    """Merge the two aggregates onto the definition's nodes, in the definition's own order.

    Order matters more than it looks: a funnel is read top to bottom, and sorting by volume (the
    obvious alternative) would reorder the journey and make the drop-off column meaningless. Nodes
    the definition has but nobody reached are kept with zeroes rather than dropped — "nobody has
    ever got to the offshore review" is the single most useful row a funnel can show, and dropping
    empty rows hides exactly that.

    A step node prefers its ``case_steps`` roll-up (which knows status and duration) and falls back
    to the graph-visit count; a control node has only the visit count, which is why the walk's
    ``visited`` map is read at all.
    """
    out: list[dict[str, Any]] = []
    for node_id, node in parsed.nodes.items():
        stat = steps.get(node_id)
        reached = int(stat["reached"]) if stat else visits.get(node_id, 0)
        entry: dict[str, Any] = {
            "node_id": node_id,
            "kind": node.kind,
            "type": node.type,
            "title": node.label,
            "scope": node.scope,
            "reached": reached,
            "completed": int(stat["completed"]) if stat else reached,
            "by_status": dict(stat["by_status"]) if stat else {},
            "instances": int(stat["instances"]) if stat else reached,
            "median_seconds": stat["median_seconds"] if stat else None,
            "p90_seconds": stat["p90_seconds"] if stat else None,
            "is_work": node.is_step,
        }
        if node.kind in graph_mod.BRANCHING_KINDS:
            taken = branch_choices.get(node_id, {})
            entry["arms"] = [
                {
                    "id": arm.id,
                    "label": arm.label,
                    "is_else": arm.is_else,
                    "next": arm.next,
                    "count": taken.get(arm.next or "", 0),
                }
                for arm in node.branches
            ]
        out.append(entry)
    return out


def funnel_edges(
    parsed: graph_mod.Graph, nodes: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Drop-off along every authored edge: how many cases completed the source and how many
    reached the target.

    Along the EDGE rather than between adjacent rows in the list, because a graph is not a list: a
    branch has several successors and two arms may re-converge, so "the next row down" is not the
    next thing that happens. ``lost`` can legitimately be zero on every arm of a branch and still
    sum to a real loss at the node — which is why the node's own outstanding count is reported
    separately rather than inferred from its edges.

    Negative loss is clamped to zero and reported as ``gained``: a target with two predecessors
    reaches more cases than either one completed, and a funnel that renders that as "-40% drop-off"
    is a bug report waiting to happen.
    """
    by_id = {str(n["node_id"]): n for n in nodes}
    edges: list[dict[str, Any]] = []
    for node_id in parsed.nodes:
        source = by_id.get(node_id)
        if source is None:
            continue
        completed = int(source["completed"])
        for target_id in graph_mod.successors(parsed, node_id):
            target = by_id.get(target_id)
            if target is None:
                continue
            reached = int(target["reached"])
            edges.append(
                {
                    "from": node_id,
                    "to": target_id,
                    "completed": completed,
                    "reached": reached,
                    "lost": max(completed - reached, 0),
                    "gained": max(reached - completed, 0),
                    "rate": (reached / completed) if completed else None,
                }
            )
    return edges
