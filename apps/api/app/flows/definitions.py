"""Flow-definition validation — the authored ``{steps, milestones}`` / ``{nodes, milestones}`` JSON.

The step-type *vocabulary* is code (bounded, safe); composing steps into a flow is *data*. This
module is the allowlist gate on that data (mirrors the discipline of ``app/branding.py``): a
firm can compose any steps/milestones, but only from known step types and known config keys, and
milestones may only reference declared step ids. Values inside ``config``/``visibility`` are kept
deliberately open (interpreted by the step handlers / the frontend), like form field ``options``.

Adding a step type = one entry in ``STEP_TYPES`` here + a handler in ``engine`` — no schema change.

**v3 (the graph).** A definition may now arrive in either shape, and ``app/flows/graph.py`` owns
the difference (read its docstring for the wire shape and the legacy-compatibility guarantees):

* legacy ``{steps: [...]}`` — validated exactly as it always was, byte-for-byte, because every
  definition in the wild is one and they are running live onboarding journeys;
* v3 ``{nodes: [...]}`` — the same step objects plus ``kind``/``next``, alongside the control
  nodes (branch / wait / wait_for_event / start_flow / exit). Canonical output carries a
  **derived** ``steps`` projection so the rest of the engine reads a v3 definition unchanged.

The division of labour with ``graph.py``: this module owns the *vocabulary* (which step types,
which config keys, which assignees and scopes) and ``graph.py`` owns the *shape* (ids, kinds,
edges, branches). ``validate_definition`` normalises, then hands the result to ``graph.parse`` for
the structural refusals; ``publish_issues`` adds ``graph.graph_issues``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.flows import dependencies
from app.flows import graph as graph_mod
from app.flows import handlers as handlers_mod

# Re-exported: ``graph`` has to own the class (this module imports it, not the other way round),
# but ``definitions.DefinitionError`` is what every existing call site catches and it is the same
# class object, so nothing changes for them.
DefinitionError = graph_mod.DefinitionError

_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")
ASSIGNEES = ("client", "staff")


# The three tiers a step can be scoped to (docs/flows.md §2). The engine fans participant-scoped
# steps per KYC-relevant person, account-scoped steps per account (per_participant → per owner), and
# application-scoped steps once.
SCOPES = ("participant", "account", "application")


@dataclass(frozen=True)
class StepType:
    type: str
    default_assignee: str
    # Allowed keys in a step's `config` (values are opaque, interpreted by the handler/UI).
    config_keys: frozenset[str] = field(default_factory=frozenset)
    # Where this step naturally sits when the author hasn't chosen a scope explicitly.
    default_scope: str = "application"


#: Config keys this estate RENAMED or RETIRED, still present in stored definitions. Accepted and
#: ignored, so a flow authored before the change stays validatable, savable and publishable — the
#: alternative is a firm locked out of its own flow with no route back but SQL on a jsonb column.
#:
#: `platform_id` -> `provider` on `setup_account` (0138: the registry serial became the provider
#: code). `provider` on `aml_check`: an AML step always screens on ComplyCube. `validity_days` on
#: `verify_identity` and `aml_check`: an identity or AML pass does not lapse. Not a compatibility
#: layer to grow indefinitely: an entry here is a debt, and the way to clear one is a one-off jsonb
#: rewrite, after which the entry goes.
_RENAMED_CONFIG_KEYS: dict[str, frozenset[str]] = {
    "setup_account": frozenset({"platform_id"}),
    "verify_identity": frozenset({"validity_days"}),
    "aml_check": frozenset({"provider", "validity_days"}),
}

STEP_TYPES: dict[str, StepType] = {
    # `remember` is the authored promotion into CASE MEMORY ({memory_key: answer_field}), applied
    # by the orchestrator when the form completes — not by the handler, which is why it is declared
    # here and absent from FillFormHandler._config_keys. Deliberately not "copy every answer": see
    # app/flows/memory.py.
    "fill_form": StepType(
        "fill_form",
        "client",
        frozenset({"form_uid", "category", "mapping", "mode", "title", "remember"}),
        "participant",
    ),
    "accept_terms": StepType(
        "accept_terms",
        "client",
        frozenset({"document_uid", "content", "category", "title", "term_uid"}),
        "account",
    ),
    "upload_document": StepType(
        "upload_document", "client", frozenset({"category", "format_hint", "title"}), "participant"
    ),
    "setup_account": StepType(
        "setup_account",
        "staff",
        frozenset({"provider", "product_id", "model_id", "fee_package_id", "role"}),
        "account",
    ),
    # Identity is a manual staff-review gate by default (a staff member records the outcome). When
    # an external provider (ComplyCube) is configured, its adapter (app/flows/providers.py)
    # drives the same case step automatically — no engine or schema change. `provider` selects the
    # adapter ('manual' by default).
    "verify_identity": StepType(
        "verify_identity",
        "staff",
        frozenset({"provider", "category"}),
        "participant",
    ),
    # Always screened on ComplyCube (`AmlCheckHandler`), so it has no `provider` to choose.
    "aml_check": StepType("aml_check", "staff", frozenset({"category"}), "participant"),
    # Render a template to a document at a point in the flow (reuses the render pipeline).
    "generate_document": StepType(
        "generate_document",
        "staff",
        # `title` names the generated document — the handler reads it, so the validator must accept
        # it (declared-but-unread and read-but-undeclared are both drift; see
        # tests/test_flows_handlers.py).
        frozenset({"template_uid", "dataset", "category", "title"}),
        "application",
    ),
    # A staff compliance decision (e.g. offshore enhanced due diligence). On a `passed` outcome it
    # can raise a whitelisted application gate flag (`gate`, e.g. offshore_approved) that drives the
    # onboarding status projection; otherwise it holds the case.
    "review": StepType("review", "staff", frozenset({"title", "category", "gate"}), "account"),
    # A generic staff/client to-do (adviser declaration, offshore approval, "open in provider").
    # ``invite_clients`` — on complete, best-effort portal invites for KYC-relevant owners
    # (legacy declare → email; see TaskHandler).
    "task": StepType("task", "staff", frozenset({"description", "invite_clients"}), "application"),
}


# Config keys that MUST be present for a step type to be publishable (they reference another
# entity the client/staff step depends on). Reference *existence* (form/doc/platform is real +
# published) is checked by the router against the DB — this is the structural half.
REQUIRED_CONFIG: dict[str, tuple[str, ...]] = {
    "fill_form": ("form_uid",),
    # Written markdown (`content`) or a library PDF (`document_uid`) — one of the two, checked
    # in `_step_publish_issues` rather than as a single required key.
    "accept_terms": (),
    # setup_account needs NOTHING: the platform/product/model are properties of the application the
    # adviser assembled (crm.applications.provider, platform.accounts.product_id), and the step's
    # config is only an optional override. Requiring the provider made the seeded onboarding flow
    # unpublishable — the one flow every firm starts from.
    "setup_account": (),
    "upload_document": (),
    "generate_document": ("template_uid",),
    "task": (),
}


# Fire-and-forget task side-effects a step may declare (docs/tasks.md — journey authoring).
# `on` maps 1:1 to the engine's StepSignals; `assign_to` travels portably (role SLUG, not id;
# 'participant' = the step's participant contact). Values mirror tasks.tasks CHECKs.
_TASK_TRIGGERS = ("completed", "failed", "blocked", "waiting")
_TASK_ASSIGN_KINDS = ("role", "user", "participant")
_TASK_CATEGORIES = ("review", "acknowledge", "upload", "follow_up", "generic")
_TASK_PRIORITIES = ("low", "normal", "high")


def _validate_step_tasks(sid: str, raw: object) -> list[dict[str, object]]:
    """Validate + normalise a step's ``tasks`` list (raise-a-task side-effects)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DefinitionError(f"step '{sid}' tasks must be a list")
    specs: list[dict[str, object]] = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            raise DefinitionError(f"step '{sid}' task {i} must be an object")
        on = t.get("on", "completed")
        if on not in _TASK_TRIGGERS:
            raise DefinitionError(f"step '{sid}' task {i} has an invalid trigger '{on}'")
        assign = t.get("assign_to")
        if not isinstance(assign, dict) or assign.get("kind") not in _TASK_ASSIGN_KINDS:
            raise DefinitionError(f"step '{sid}' task {i} assign_to must be role/user/participant")
        kind = assign["kind"]
        norm_assign: dict[str, object] = {"kind": kind}
        if kind == "role":
            role = assign.get("role")
            if not isinstance(role, str) or not _SLUG.match(role):
                raise DefinitionError(f"step '{sid}' task {i} needs a role slug")
            norm_assign["role"] = role
        elif kind == "user":
            user_id = assign.get("user_id")
            if not isinstance(user_id, str) or not user_id:
                raise DefinitionError(f"step '{sid}' task {i} needs a user_id")
            norm_assign["user_id"] = user_id
        title = t.get("title")
        if not isinstance(title, str) or not title.strip():
            raise DefinitionError(f"step '{sid}' task {i} needs a title")
        category = t.get("category", "generic")
        if category not in _TASK_CATEGORIES:
            raise DefinitionError(f"step '{sid}' task {i} has an invalid category '{category}'")
        priority = t.get("priority", "normal")
        if priority not in _TASK_PRIORITIES:
            raise DefinitionError(f"step '{sid}' task {i} has an invalid priority '{priority}'")
        due_days = t.get("due_days")
        if due_days is not None and (not isinstance(due_days, int) or due_days < 0):
            raise DefinitionError(f"step '{sid}' task {i} due_days must be a non-negative int")
        description = t.get("description")
        if description is not None and not isinstance(description, str):
            raise DefinitionError(f"step '{sid}' task {i} description must be a string")
        specs.append(
            {
                "on": on,
                "assign_to": norm_assign,
                "title": title.strip(),
                "category": category,
                "priority": priority,
                "due_days": due_days,
                "description": description,
            }
        )
    return specs


#: Step types whose artifact is a per-person request item. They need a CONTACT to fulfil them, so
#: they only work at participant scope, or account scope with ``per_participant`` (per owner).
#: Anything else plans a step with no actor — a permanent dead end — so publish refuses it.
_NEEDS_ACTOR: frozenset[str] = frozenset({"fill_form", "accept_terms", "upload_document"})


def _steps_of(definition: Mapping[str, object]) -> list[object]:
    """The step objects of either shape. A v3 definition carries the derived ``steps`` projection,
    but fall back to the step-kind nodes so this works on a hand-built v3 document too."""
    steps = definition.get("steps")
    if isinstance(steps, list):
        return steps
    nodes = definition.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [
        n
        for n in nodes
        if isinstance(n, dict) and (n.get("kind") or graph_mod.STEP_KIND) in graph_mod.PLANNED_KINDS
    ]


def _step_publish_issues(definition: dict[str, object]) -> list[graph_mod.Issue]:
    """The step-shape half of ``publish_issues`` — no graph walk, no de-duplication (the string
    view has never de-duplicated these, and two same-titled steps each missing a form are two
    problems even though they read alike)."""
    issues: list[graph_mod.Issue] = []
    steps_list = _steps_of(definition)
    if not steps_list:
        issues.append(graph_mod.Issue(None, "Add at least one step before publishing."))
    for s in steps_list:
        if not isinstance(s, dict):
            continue
        label = s.get("title") or s.get("id") or "a step"
        node_id = s.get("id") if isinstance(s.get("id"), str) else None
        stype = str(s.get("type"))
        cfg = s.get("config") if isinstance(s.get("config"), dict) else {}
        for key in REQUIRED_CONFIG.get(stype, ()):
            if not cfg.get(key):  # type: ignore[union-attr]
                issues.append(
                    graph_mod.Issue(node_id, f"Step “{label}” needs a {key.replace('_', ' ')}.")
                )
        if stype == "accept_terms":
            has_pdf = bool(cfg.get("document_uid"))  # type: ignore[union-attr]
            has_copy = bool(str(cfg.get("content") or "").strip())  # type: ignore[union-attr]
            has_term = bool(cfg.get("term_uid"))  # type: ignore[union-attr]
            if not has_pdf and not has_copy and not has_term:
                issues.append(
                    graph_mod.Issue(
                        node_id,
                        f"Step “{label}” needs a library term, a written declaration, or a PDF.",
                    )
                )
        if stype in _NEEDS_ACTOR:
            scope = s.get("scope") or STEP_TYPES[stype].default_scope
            assignee = s.get("assignee") or STEP_TYPES[stype].default_assignee
            if scope == "account" and assignee == "client" and not s.get("per_participant"):
                # One account, several owners: without the fan-out the engine cannot say WHICH
                # client is being asked, so it would ask nobody. (A staff-assigned account step is
                # fine — the engine binds it to the account's primary owner for staff to complete.)
                issues.append(
                    graph_mod.Issue(
                        node_id,
                        f"Step “{label}” runs once per account but a client has to complete it — "
                        "tick “One per owner”, or move it to “Per client”.",
                    )
                )
            elif scope == "application":
                # An application case has no contact subject at all, so there is no one to ask.
                issues.append(
                    graph_mod.Issue(
                        node_id,
                        f"Step “{label}” runs once for the application, so there is nobody to "
                        "complete it. Move it to “Per client” or “Per account”.",
                    )
                )
    return issues


def _graph_publish_issues(definition: dict[str, object]) -> list[graph_mod.Issue]:
    """The graph half — already de-duplicated by ``graph_issues_detailed``."""
    try:
        return graph_mod.graph_issues_detailed(graph_mod.parse(definition))
    except DefinitionError as exc:
        # Unreachable via the router (it validates on save), but publish_issues is also called on
        # stored JSON — and "we could not read this" is a publish issue, not a 500.
        return [graph_mod.Issue(None, str(exc))]


def publish_issues_detailed(definition: dict[str, object]) -> list[graph_mod.Issue]:
    """``publish_issues``, with each sentence carrying the id of the node it is about.

    THE authoritative producer — ``publish_issues`` is its string projection, so publish's refusal
    copy and the builder's live badges can never say different things. ``node_id is None`` marks a
    whole-flow problem ("Add at least one step", an unreadable document).
    """
    return _step_publish_issues(definition) + _graph_publish_issues(definition)


def publish_issues(definition: dict[str, object]) -> list[str]:
    """Human-readable reasons a (validated) definition is not ready to publish: no steps, a step
    missing its required config, a client-fulfilled step with nobody to fulfil it, plus every
    graph-shape refusal (``graph.graph_issues`` — the mandatory else, orphans, dangling edges,
    loops, the caps, each control node's config). Empty ⇒ structurally publishable.

    The router adds what needs the database: reference checks (does the form/document/platform
    exist), each handler's own ``preflight``, and — for an action node —
    ``effects.preflight``, which asks whether any role in the firm holds the permission the
    action needs. That one is async and session-bound, which is why it cannot live here.
    """
    # The two halves keep their historical string behaviour exactly: step issues verbatim, graph
    # issues de-duplicated by message (which is what turns one orphan sentence filed against three
    # nodes back into the single line publish has always printed).
    return [issue.message for issue in _step_publish_issues(definition)] + graph_mod.messages(
        _graph_publish_issues(definition)
    )


def _position(sid: str, raw: object, *, what: str) -> dict[str, float] | None:
    """The node's canvas coordinate for the visual editor — UI-only, opaque to the engine,
    preserved verbatim ({x, y} numbers) or null (auto-layout on first open)."""
    if raw is None:
        return None
    if not (
        isinstance(raw, dict)
        and isinstance(raw.get("x"), int | float)
        and isinstance(raw.get("y"), int | float)
    ):
        raise DefinitionError(f"{what} '{sid}' position must be {{x, y}} numbers or null")
    return {"x": float(raw["x"]), "y": float(raw["y"])}


def _validate_step(index: int, s: object, *, what: str = "step") -> dict[str, object]:
    """Validate + normalise ONE authored step object (the legacy shape, unchanged).

    ``what`` only changes the noun in the messages — a v3 document calls these nodes. Extracted
    verbatim from the old inline loop so the legacy path is byte-identical; v3 reuses it, which is
    the point: a step node IS a legacy step.
    """
    if not isinstance(s, dict):
        raise DefinitionError(f"{what} {index} must be an object")
    sid = s.get("id")
    if not isinstance(sid, str) or not _SLUG.match(sid):
        raise DefinitionError(f"{what} {index} needs a slug id (a-z0-9-_)")
    stype = s.get("type")
    if not isinstance(stype, str) or stype not in STEP_TYPES:
        raise DefinitionError(f"{what} '{sid}' has an unknown type '{stype}'")
    spec = STEP_TYPES[stype]
    title = s.get("title")
    if not isinstance(title, str) or not title.strip():
        raise DefinitionError(f"{what} '{sid}' needs a title")
    assignee = s.get("assignee", spec.default_assignee)
    if assignee not in ASSIGNEES:
        raise DefinitionError(f"{what} '{sid}' has an invalid assignee '{assignee}'")
    config = s.get("config", {})
    if not isinstance(config, dict):
        raise DefinitionError(f"{what} '{sid}' config must be an object")
    # A RENAMED KEY IS ACCEPTED AND IGNORED, not refused. `setup_account`'s allowlist swapped
    # `platform_id` for `provider` when the registry serial became the provider code (0138), and
    # nothing rewrote `flows.flow_versions.definition` — so a firm that had authored a custodian
    # override could no longer VALIDATE, save or republish its own flow, and the only fix was hand
    # editing a jsonb column. The already-published version keeps executing either way, because
    # `SetupAccountHandler` simply stops reading the key.
    #
    # Refusing an unknown key is right and stays: it catches a typo that would otherwise be a
    # silent no-op. What must not be refused is a key this estate itself renamed.
    unknown = set(config) - spec.config_keys - _RENAMED_CONFIG_KEYS.get(stype, frozenset())
    if unknown:
        raise DefinitionError(
            f"{what} '{sid}' ({stype}) has unknown config keys: {', '.join(sorted(unknown))}"
        )
    visibility = s.get("visibility")
    if visibility is not None and not isinstance(visibility, dict):
        raise DefinitionError(f"{what} '{sid}' visibility must be an object or null")
    scope = s.get("scope", spec.default_scope)
    if scope not in SCOPES:
        raise DefinitionError(f"{what} '{sid}' has an invalid scope '{scope}'")
    depends = s.get("depends_on", [])
    if not isinstance(depends, list) or not all(isinstance(x, str) for x in depends):
        raise DefinitionError(f"{what} '{sid}' depends_on must be a list of step ids")
    return {
        "id": sid,
        "type": stype,
        "title": title.strip(),
        "required": bool(s.get("required", True)),
        "assignee": assignee,
        "scope": scope,
        "per_participant": bool(s.get("per_participant", False)),
        "depends_on": list(depends),
        "visibility": visibility,
        "position": _position(sid, s.get("position"), what=what),
        "config": config,
        "tasks": _validate_step_tasks(sid, s.get("tasks")),
    }


def _validate_action_node(index: int, n: dict[str, Any]) -> dict[str, object]:
    """A ``kind: "action"`` node: work the engine does itself (tag, raise a task, send an approved
    email, render a document) sitting inline in a journey, with nobody assigned to it.

    Only the structural half lives here — the config-key allowlist from the contract. Whether the
    firm has granted anyone the permission the action needs is ``effects.preflight``'s
    question, and it needs a session, so the router's publish path asks it (see ``publish_issues``).
    """
    sid = n.get("id")
    if not isinstance(sid, str) or not _SLUG.match(sid):
        raise DefinitionError(f"node {index} needs a slug id (a-z0-9-_)")
    atype = n.get("type")
    if not isinstance(atype, str) or atype not in graph_mod.ACTION_CONFIG_KEYS:
        raise DefinitionError(
            f"node '{sid}' has an unknown action '{atype}' — "
            f"one of {', '.join(sorted(graph_mod.ACTION_CONFIG_KEYS))}"
        )
    config = n.get("config", {})
    if not isinstance(config, dict):
        raise DefinitionError(f"node '{sid}' config must be an object")
    unknown = set(config) - graph_mod.ACTION_CONFIG_KEYS[atype]
    if unknown:
        raise DefinitionError(
            f"node '{sid}' ({atype}) has unknown config keys: {', '.join(sorted(unknown))}"
        )
    visibility = n.get("visibility")
    if visibility is not None and not isinstance(visibility, dict):
        raise DefinitionError(f"node '{sid}' visibility must be an object or null")
    scope = n.get("scope", "application")
    if scope not in SCOPES:
        raise DefinitionError(f"node '{sid}' has an invalid scope '{scope}'")
    depends = n.get("depends_on", [])
    if not isinstance(depends, list) or not all(isinstance(x, str) for x in depends):
        raise DefinitionError(f"node '{sid}' depends_on must be a list of node ids")
    title = n.get("title")
    if title is not None and not isinstance(title, str):
        raise DefinitionError(f"node '{sid}' needs a title")
    return {
        "id": sid,
        "kind": graph_mod.ACTION_KIND,
        "type": atype,
        "title": (title or "").strip() or atype.replace("_", " ").capitalize(),
        "required": bool(n.get("required", True)),
        # NOT 'client'. The persisted column's vocabulary is client|staff and an action is neither,
        # but the default for a missing key is 'client' — which would put engine work in a client's
        # portal. 'staff' is the one that keeps it out of everyone's way; it completes at
        # instantiate, so it never lands in a queue either (step_handlers.ActionStepHandler).
        "assignee": "staff",
        "scope": scope,
        "depends_on": list(depends),
        "visibility": visibility,
        "position": _position(sid, n.get("position"), what="node"),
        "config": config,
        "next": n.get("next"),
    }


def _validate_control_node(index: int, n: dict[str, Any], kind: str) -> dict[str, object]:
    """A control node: its id, its label, its config and its edge. The *contents* of the config
    (which branches, which trigger, how long to wait) are ``graph.parse``'s and
    ``graph.graph_issues``' business — this only pins the envelope."""
    sid = n.get("id")
    if not isinstance(sid, str) or not _SLUG.match(sid):
        raise DefinitionError(f"node {index} needs a slug id (a-z0-9-_)")
    config = n.get("config", {})
    if not isinstance(config, dict):
        raise DefinitionError(f"node '{sid}' config must be an object")
    title = n.get("title")
    if title is not None and not isinstance(title, str):
        raise DefinitionError(f"node '{sid}' title must be text")
    depends = n.get("depends_on", [])
    if not isinstance(depends, list) or not all(isinstance(x, str) for x in depends):
        raise DefinitionError(f"node '{sid}' depends_on must be a list of node ids")
    out: dict[str, object] = {
        "id": sid,
        "kind": kind,
        "title": (title or "").strip() or graph_mod.CONTROL_LABELS[kind],
        "depends_on": list(depends),
        "position": _position(sid, n.get("position"), what="node"),
        "config": config,
    }
    # A branch's (and a condition's) edges live on its arms, and a tail call / an end has none at
    # all: emitting a `next` key for them would invite the builder to set one, which graph.parse
    # then refuses.
    if kind not in graph_mod.TERMINAL_KINDS and kind not in graph_mod.BRANCHING_KINDS:
        out["next"] = n.get("next")
    return out


def _validate_milestones(raw: object, known_step_ids: set[str]) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        raise DefinitionError("milestones must be a list")
    milestones: list[dict[str, object]] = []
    for i, m in enumerate(raw):
        if not isinstance(m, dict):
            raise DefinitionError(f"milestone {i} must be an object")
        mstatus = m.get("status")
        if not isinstance(mstatus, str) or not _SLUG.match(mstatus):
            raise DefinitionError(f"milestone {i} needs a slug status")
        reached = m.get("reached_when", [])
        if not isinstance(reached, list) or not all(isinstance(x, str) for x in reached):
            raise DefinitionError(f"milestone '{mstatus}' reached_when must be a list of step ids")
        missing = [x for x in reached if x not in known_step_ids]
        if missing:
            raise DefinitionError(
                f"milestone '{mstatus}' references unknown steps: {', '.join(missing)}"
            )
        stored: dict[str, object] = {"status": mstatus, "reached_when": list(reached)}
        # A client reads the label and the summary. The status slug stays the key.
        label = m.get("label")
        if isinstance(label, str) and label.strip():
            stored["label"] = label.strip()
        elif "label" in m and m.get("label") is not None:
            raise DefinitionError(f"milestone '{mstatus}' label must be text")
        summary = m.get("summary")
        if isinstance(summary, str) and summary.strip():
            stored["summary"] = summary.strip()
        elif "summary" in m and m.get("summary") is not None:
            raise DefinitionError(f"milestone '{mstatus}' summary must be text")
        milestones.append(stored)
    return milestones


def _check_dependencies(steps: list[dict[str, object]], known: set[str]) -> None:
    """``depends_on`` may only reference declared steps, and the dependency graph must be acyclic —
    the orchestrator holds a step until its prerequisites settle, so a cycle would wedge every step
    in it forever."""
    for s in steps:
        deps = s["depends_on"]
        missing = [d for d in deps if d not in known] if isinstance(deps, list) else []
        if missing:
            raise DefinitionError(
                f"step '{s['id']}' depends on unknown steps: {', '.join(missing)}"
            )
    cyclic = dependencies.find_cycles(steps)
    if cyclic:
        raise DefinitionError(
            f"steps depend on each other in a loop: {', '.join(cyclic)}. "
            "Remove one of the dependencies."
        )


def validate_definition(raw: object) -> dict[str, object]:
    """Validate + normalise an authored flow definition. Raises ``DefinitionError`` on any
    violation.

    Returns the canonical ``{"steps": [...], "milestones": [...], "intake": {...}}`` for a legacy
    definition, and for a v3 one ``{"version": 3, "entry": …, "nodes": [...], "steps": [...],
    "milestones": [...], "intake": {...}}`` — where ``steps`` is a **derived projection** of the
    step-kind nodes, regenerated here on every save and never authored, so every existing consumer
    (planning, projection, the router's reference + handler preflights) reads a v3 definition with
    no change at all. ``graph.parse`` ignores it whenever ``nodes`` is present, so it can never
    become a competing source of truth.

    The projection carries **compiled** ``depends_on`` (an authored edge becomes a prerequisite) and
    **compiled** ``visibility`` (a ``condition`` node becomes part of the gated step's plan-time
    condition). Both are the same trick: v3's authored vocabulary is turned back into the two things
    the pre-v3 engine already understood.
    """
    if not isinstance(raw, dict):
        raise DefinitionError("definition must be an object")
    if raw.get("nodes") is not None:
        return _validate_graph_definition(raw)

    steps_in = raw.get("steps", [])
    if not isinstance(steps_in, list):
        raise DefinitionError("steps must be a list")
    steps: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for i, s in enumerate(steps_in):
        step = _validate_step(i, s)
        sid = str(step["id"])
        if sid in seen_ids:
            raise DefinitionError(f"duplicate step id '{sid}'")
        seen_ids.add(sid)
        steps.append(step)
    _check_dependencies(steps, seen_ids)
    return {
        "steps": steps,
        "milestones": _validate_milestones(raw.get("milestones", []), seen_ids),
        "intake": _validate_intake(raw.get("intake")),
    }


def _validate_graph_definition(raw: dict[str, Any]) -> dict[str, object]:
    """The v3 half of ``validate_definition``: normalise every node by kind, then hand the result
    to ``graph.parse`` for the structural refusals, then derive the ``steps`` projection that the
    whole pre-v3 engine reads."""
    nodes_in = raw.get("nodes")
    if not isinstance(nodes_in, list):
        raise DefinitionError("nodes must be a list")

    nodes: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    step_ids: set[str] = set()
    for i, n in enumerate(nodes_in):
        if not isinstance(n, dict):
            raise DefinitionError(f"node {i} must be an object")
        kind = n.get("kind") or graph_mod.STEP_KIND
        if not isinstance(kind, str) or kind not in graph_mod.NODE_KINDS:
            raise DefinitionError(f"node {i} has an unknown kind '{n.get('kind')}'")
        if kind == graph_mod.ACTION_KIND:
            # THE DISCRIMINATOR. `kind` decides which vocabulary `type` resolves against — not a
            # precedence rule, which is what made the automated `generate_document` unauthorable
            # (the staff step of the same name always won, silently). See the contract's
            # $comment_actionTypes and ``graph.Node.is_action``.
            node = _validate_action_node(i, n)
        elif kind != graph_mod.STEP_KIND:
            node = _validate_control_node(i, n, kind)
        else:
            node = {**_validate_step(i, n, what="node"), "kind": kind, "next": n.get("next")}
        nid = str(node["id"])
        if nid in seen_ids:
            raise DefinitionError(f"duplicate node id '{nid}'")
        seen_ids.add(nid)
        if kind in graph_mod.PLANNED_KINDS:
            step_ids.add(nid)
        nodes.append(node)

    entry = raw.get("entry")
    parsed = graph_mod.parse({"nodes": nodes, "entry": entry})
    compiled = graph_mod.effective_depends_on(parsed)
    # `depends_on` still only names steps — a control node is never a prerequisite, it is a hop.
    _check_dependencies(
        [{**n, "depends_on": list(compiled.get(str(n["id"]), ()))} for n in nodes if "type" in n],
        step_ids,
    )

    # The derived projection. An ACTION node's `type` is stamped with the action prefix
    # (``handlers.action_step_type``) because this projection is what planning fans out and what
    # ends up in ``flows.case_steps.step_type`` — where `generate_document` alone would be
    # ambiguous all over again, this time with no `kind` left to disambiguate it. The prefixed name
    # is the discriminator made durable: one string, unambiguous in the registry and legible in the
    # database. The authored `nodes` array keeps the plain type, so the builder round-trips.
    #
    # `visibility` passes through verbatim. A `condition` node does NOT compile into it — it is a
    # two-armed branch, decided by the walk (`graph`'s "The ``condition`` node") — and the two are
    # different questions the engine keeps asking separately: `visibility` is "does this step apply
    # to this participant/account at all", a condition is "which way does this case go".
    derived_steps = [
        {k: v for k, v in n.items() if k not in ("kind", "next")}
        | {"depends_on": list(compiled.get(str(n["id"]), ()))}
        | (
            {"type": handlers_mod.action_step_type(str(n["type"]))}
            if n.get("kind") == graph_mod.ACTION_KIND
            else {}
        )
        for n in nodes
        if str(n["id"]) in step_ids
    ]
    return {
        "version": 3,
        "entry": parsed.entry,
        "nodes": nodes,
        "steps": derived_steps,
        "milestones": _validate_milestones(raw.get("milestones", []), step_ids),
        "intake": _validate_intake(raw.get("intake")),
    }


# The kickoff a flow collects to START a journey — the "New journey" screen is generated from this,
# not hardcoded. The structured part (which fixed blocks to collect + whether new people may be
# created inline) plus any custom fields the adviser captures at the start.
_INTAKE_FIELD_TYPES = frozenset(
    {"text", "textarea", "select", "date", "email", "number", "checkbox"}
)


def _validate_intake(raw: object) -> dict[str, object]:
    """Validate + normalise a flow's ``intake`` block (absent ⇒ sensible onboarding defaults)."""
    i = raw if isinstance(raw, dict) else {}
    fields: list[dict[str, object]] = []
    seen: set[str] = set()
    fields_in = i.get("fields", [])
    if not isinstance(fields_in, list):
        raise DefinitionError("intake.fields must be a list")
    for n, f in enumerate(fields_in):
        if not isinstance(f, dict):
            raise DefinitionError(f"intake field {n} must be an object")
        key = f.get("key")
        if not isinstance(key, str) or not _SLUG.match(key):
            raise DefinitionError(f"intake field {n} needs a slug key")
        if key in seen:
            raise DefinitionError(f"duplicate intake field '{key}'")
        seen.add(key)
        ftype = f.get("type", "text")
        if ftype not in _INTAKE_FIELD_TYPES:
            raise DefinitionError(f"intake field '{key}' has an invalid type '{ftype}'")
        label = f.get("label")
        if not isinstance(label, str) or not label.strip():
            raise DefinitionError(f"intake field '{key}' needs a label")
        opts = f.get("options", [])
        if not isinstance(opts, list) or not all(isinstance(o, str) for o in opts):
            raise DefinitionError(f"intake field '{key}' options must be a list of strings")
        fields.append(
            {
                "key": key,
                "label": label.strip(),
                "type": ftype,
                "required": bool(f.get("required", False)),
                "options": list(opts),
            }
        )
    subject = "accounts" if i.get("subject", "accounts") != "person" else "person"
    # A person journey's kickoff is "who is this for". Restating that on a second page is opt-in.
    # An account journey still confirms, because the page is the accounts, products and custody.
    review = i.get("review")
    if not isinstance(review, bool):
        review = subject != "person"
    return {
        "subject": subject,
        "collect_platform": bool(i.get("collect_platform", True)),
        "collect_custody": bool(i.get("collect_custody", True)),
        "collect_product": bool(i.get("collect_product", True)),
        "allow_new": bool(i.get("allow_new", True)),
        "review": review,
        "fields": fields,
    }
