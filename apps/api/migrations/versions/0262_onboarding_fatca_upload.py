"""onboarding: a W-9 or W-8 answer asks the client to upload the signed form

Revision ID: 0262_onboarding_fatca_upload
Revises: 0261_contact_fatca_document
Create Date: 2026-10-17

``alembic_version.version_num`` is ``varchar(32)``; this id is 28 characters.

Additional information remembers its W-9 / W-8 answer in case memory. When the declaration is
sent, the fork ``needs_fatca_form`` reads it, and a W-9 or a W-8 releases ``fatca_form``: a
client upload of category ``fatca_form``. The fork's other arm and the step both lead to
``setup``, so opening the account waits for the form and nothing else does.

Adding a step changes the journey's structure, and a published version is never edited: a
journey keeps the version it started on. So this does what ``onboarding_seed._republish`` does.
For each firm's onboarding flow whose current version has ``additional_info``, ``declaration``
and ``setup`` as step nodes and has neither new node, it publishes ``patched()`` of that
definition as the next version and points the flow at it. Journeys already running are not
changed. A legacy (``{steps}``) definition is left alone: it has no forks.

``patched()`` is frozen here and also rebuilds the derived ``steps`` projection's compiled
``depends_on`` with a frozen copy of ``graph.effective_depends_on``, so the stored version is what
``validate_definition`` would store.

Downgrade puts each flow back on the version the upgrade replaced, when the current version is
still exactly what the upgrade published, and deletes that version when no journey started on
it. A flow published again since keeps its current version.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from typing import Any

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

revision: str = "0262_onboarding_fatca_upload"
down_revision: str | None = "0261_contact_fatca_document"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_ = (revision, down_revision, branch_labels, depends_on)  # Alembic reads these names off the module

FLOW_CODE = "onboarding"
GATE_ID = "needs_fatca_form"
STEP_ID = "fatca_form"
_REMEMBER = {"fatca_status": "fatca_status"}

_GATE: dict[str, Any] = {
    "id": GATE_ID,
    "kind": "condition",
    "title": "A signed W-9 or W-8 to collect?",
    "depends_on": [],
    "position": None,
    "config": {
        "branches": [
            {
                "id": "true",
                "label": "Yes",
                "conditions": {
                    "field": "memory.fatca_status",
                    "operator": "in",
                    "value": ["w9", "w8"],
                },
                "next": STEP_ID,
            },
            {"id": "false", "label": "No", "conditions": None, "next": "setup"},
        ]
    },
}

_STEP: dict[str, Any] = {
    "id": STEP_ID,
    "type": "upload_document",
    "title": "Upload your signed W-9 / W-8",
    "required": True,
    "assignee": "client",
    "scope": "participant",
    "per_participant": False,
    "depends_on": [],
    "visibility": None,
    "position": None,
    "config": {
        "category": "fatca_form",
        "format_hint": "PDF or a photo",
        "title": "Upload your signed W-9 / W-8",
    },
    "tasks": [],
}

# A frozen copy of the graph vocabulary `effective_depends_on` reads (app/flows/graph.py).
_PLANNED_KINDS = frozenset({"step", "action"})
_BRANCHING_KINDS = frozenset({"branch", "condition"})


def _next_ids(node: dict[str, Any]) -> list[str]:
    raw = node.get("next")
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, str) and t]
    return []


def _outgoing(node: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    kind = node.get("kind") or "step"
    if kind in _BRANCHING_KINDS:
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        branches = config.get("branches") if isinstance(config.get("branches"), list) else []
        ids.extend(b["next"] for b in branches if isinstance(b, dict) and b.get("next"))
    else:
        ids.extend(_next_ids(node))
        if kind == "wait_for_event":
            config = node.get("config") if isinstance(node.get("config"), dict) else {}
            timeout = config.get("timeout_branch")
            if isinstance(timeout, str) and timeout.strip():
                ids.append(timeout.strip())
    out: list[str] = []
    for nid in ids:
        if nid not in out:
            out.append(nid)
    return out


def _compiled_depends_on(nodes: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Each step node's authored ``depends_on`` plus its nearest step ancestors, in the order
    ``graph.effective_depends_on`` produces them."""
    by_id = {str(n["id"]): n for n in nodes}
    predecessors: dict[str, list[str]] = {nid: [] for nid in by_id}
    for nid, node in by_id.items():
        for target in _outgoing(node):
            if target in predecessors:
                predecessors[target].append(nid)

    def is_step(nid: str) -> bool:
        return (by_id[nid].get("kind") or "step") in _PLANNED_KINDS

    out: dict[str, list[str]] = {}
    for nid, node in by_id.items():
        if not is_step(nid):
            continue
        found: list[str] = []
        seen = {nid}
        queue = list(predecessors.get(nid, ()))
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            if current not in by_id:
                continue
            if is_step(current):
                found.append(current)
            else:
                queue.extend(predecessors.get(current, ()))
        authored = node.get("depends_on")
        merged = [d for d in authored if isinstance(d, str)] if isinstance(authored, list) else []
        merged.extend(d for d in found if d not in merged)
        out[nid] = merged
    return out


def _step_node(nodes: list[dict[str, Any]], nid: str) -> dict[str, Any] | None:
    for node in nodes:
        if node.get("id") == nid and (node.get("kind") or "step") == "step":
            return node
    return None


def patched(definition: object) -> dict[str, Any] | None:
    """The definition with the W-9 / W-8 upload added, or None when it does not apply.

    It applies to a validated v3 definition (``nodes`` and the derived ``steps``) whose step nodes
    include ``additional_info`` (a form), ``declaration`` and ``setup``, and that has neither new
    node yet. The new step takes ``additional_info``'s visibility, so it is planned for the same
    people the question is asked about."""
    if not isinstance(definition, dict):
        return None
    nodes_in, steps_in = definition.get("nodes"), definition.get("steps")
    if not isinstance(nodes_in, list) or not isinstance(steps_in, list):
        return None
    if not all(isinstance(n, dict) and isinstance(n.get("id"), str) for n in nodes_in):
        return None
    if any(n["id"] in (GATE_ID, STEP_ID) for n in nodes_in):
        return None
    info = _step_node(nodes_in, "additional_info")
    if info is None or info.get("type") != "fill_form":
        return None
    if _step_node(nodes_in, "declaration") is None or _step_node(nodes_in, "setup") is None:
        return None

    out = copy.deepcopy(definition)
    nodes: list[dict[str, Any]] = out["nodes"]
    info = _step_node(nodes, "additional_info")
    declaration = _step_node(nodes, "declaration")
    if info is None or declaration is None:  # pragma: no cover - checked on the original above
        return None
    config = info.get("config") if isinstance(info.get("config"), dict) else {}
    held = config.get("remember") if isinstance(config.get("remember"), dict) else {}
    info["config"] = {**config, "remember": {**held, **_REMEMBER}}
    targets = _next_ids(declaration)
    declaration["next"] = [*targets, GATE_ID] if targets else GATE_ID
    step = {**copy.deepcopy(_STEP), "visibility": copy.deepcopy(info.get("visibility"))}
    at = nodes.index(declaration) + 1
    nodes[at:at] = [copy.deepcopy(_GATE), {**step, "kind": "step", "next": "setup"}]

    compiled = _compiled_depends_on(nodes)
    steps: list[Any] = out["steps"]
    for derived in steps:
        if not isinstance(derived, dict):
            continue
        if derived.get("id") == "additional_info":
            derived["config"] = copy.deepcopy(info["config"])
        if derived.get("id") in compiled:
            derived["depends_on"] = list(compiled[str(derived["id"])])
    position = next(
        (
            i + 1
            for i, s in enumerate(steps)
            if isinstance(s, dict) and s.get("id") == "declaration"
        ),
        len(steps),
    )
    steps[position:position] = [{**step, "depends_on": compiled[STEP_ID]}]
    return out


def _loaded(raw: object) -> object:
    return json.loads(raw) if isinstance(raw, str) else raw


def apply(conn: Connection) -> int:
    """Publish the patched definition as each eligible onboarding flow's next version. Returns how
    many flows moved. Runs again as a no-op: a flow whose current version has the step is skipped."""
    # flows.flows / flows.flow_versions are FORCE RLS and the migrator is not BYPASSRLS, so the
    # reads and writes run as service_role (0039 says why).
    conn.exec_driver_sql("SET LOCAL ROLE service_role")
    rows = conn.execute(
        text(
            "SELECT f.id AS flow_id, f.tenant_id, fv.definition "
            "FROM flows.flows f JOIN flows.flow_versions fv "
            "  ON fv.id = f.current_published_version_id "
            "WHERE f.code = :code ORDER BY f.id"
        ),
        {"code": FLOW_CODE},
    ).all()
    moved = 0
    for row in rows:
        definition = patched(_loaded(row.definition))
        if definition is None:
            continue
        version_id = conn.execute(
            text(
                "INSERT INTO flows.flow_versions "
                "  (uid, tenant_id, flow_id, version, status, definition, published_at) "
                "SELECT public.nanoid(12), :t, :f, COALESCE(MAX(version), 0) + 1, 'published', "
                "  CAST(:d AS jsonb), now() "
                "FROM flows.flow_versions WHERE flow_id = :f RETURNING id"
            ),
            {"t": row.tenant_id, "f": row.flow_id, "d": json.dumps(definition)},
        ).scalar_one()
        conn.execute(
            text("UPDATE flows.flows SET current_published_version_id = :v WHERE id = :f"),
            {"v": version_id, "f": row.flow_id},
        )
        moved += 1
    conn.exec_driver_sql("RESET ROLE")
    return moved


def revert(conn: Connection) -> int:
    """Put each flow the upgrade moved back on the version it replaced. Returns how many."""
    conn.exec_driver_sql("SET LOCAL ROLE service_role")
    rows = conn.execute(
        text(
            "SELECT f.id AS flow_id, cur.id AS current_id, cur.version, cur.definition "
            "FROM flows.flows f JOIN flows.flow_versions cur "
            "  ON cur.id = f.current_published_version_id "
            "WHERE f.code = :code ORDER BY f.id"
        ),
        {"code": FLOW_CODE},
    ).all()
    restored = 0
    for row in rows:
        previous = conn.execute(
            text(
                "SELECT id, definition FROM flows.flow_versions "
                "WHERE flow_id = :f AND version < :v AND status = 'published' "
                "ORDER BY version DESC LIMIT 1"
            ),
            {"f": row.flow_id, "v": row.version},
        ).one_or_none()
        if previous is None or patched(_loaded(previous.definition)) != _loaded(row.definition):
            continue
        conn.execute(
            text("UPDATE flows.flows SET current_published_version_id = :v WHERE id = :f"),
            {"v": previous.id, "f": row.flow_id},
        )
        conn.execute(
            text(
                "DELETE FROM flows.flow_versions v WHERE v.id = :v "
                "AND NOT EXISTS (SELECT 1 FROM flows.executions e WHERE e.flow_version_id = v.id) "
                "AND NOT EXISTS (SELECT 1 FROM flows.cases c WHERE c.flow_version_id = v.id)"
            ),
            {"v": row.current_id},
        )
        restored += 1
    conn.exec_driver_sql("RESET ROLE")
    return restored


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    revert(op.get_bind())
