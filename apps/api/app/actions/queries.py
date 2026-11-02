"""Project baton runs for the actions screens: one run as its tree, the runs listing, and the
approvals queue.

A baton run is a tree — composites call composites call leaves — and the detail returns it as
one: every node with its state, its calls, its result and its children, plus the facts the
engine keeps beside the tree (the lifecycle events, the decision on a held run, the scheduling
that says when a wait polls again). The screens draw the tree and say what happens next; nothing
is flattened here any more, so a book read is a node with a result rather than a "step" with no
address, and a gate is a node a person can decide.

Two words travel with every state. ``state`` is baton's own (type + name + message), which the
UI colours by type and labels by name. ``status`` is the one word the listing filters and the
stat tiles count on, folded by :func:`status_word` — ONE fold, used by the Python side and
mirrored by the SQL CASE the listing runs, so a cancelled run cannot be "failed" on one screen
and "cancelled" on the next.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from baton import AttemptView, RunDetail
from baton.registry import Registry, default_registry
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.redact import firm_visible
from app.listing import DEFAULT_LIMIT, PAGE_LIMIT_MAX, clean, clean_many

# ---- the status fold ---------------------------------------------------------------------

#: The words the UI knows. A WAITING run is one resting on a provider (a durable NotReady wait or
#: a scheduled retry) — its own, or, for a composite paused on its children, one of theirs.
STATUS_WORDS: tuple[str, ...] = (
    "running",
    "waiting",
    "awaiting_approval",
    "succeeded",
    "partial",
    "skipped",
    "needs_attention",
    "conflicted",
    "timed_out",
    "failed",
    "rejected",
    "cancelled",
)

_WAITING_NAMES = ("Waiting", "AwaitingRetry")


def status_word(
    state_type: str, state_name: str, *, waiting_below: bool = False, held_below: bool = False
) -> str:
    """Fold a baton (state_type, state_name) onto the one status word the UI filters on.

    ``waiting_below`` says a descendant is resting on a provider; a composite paused on such a
    child reads as waiting too, since that is what the firm is waiting for. ``held_below`` says a
    descendant is resting at an approval gate (a composite's child the firm gates), and wins: a
    person has to act before anything else can happen, so the root reads awaiting approval."""
    if state_type == "completed":
        if state_name == "Skipped":
            return "skipped"
        if state_name == "Partial":
            return "partial"
        return "succeeded"
    if state_type == "failed":
        if state_name == "NeedsAttention":
            return "needs_attention"
        if state_name == "Conflicted":
            return "conflicted"
        if state_name == "TimedOut":
            return "timed_out"
        return "failed"
    if state_type == "cancelled":
        return "rejected" if state_name == "Rejected" else "cancelled"
    if state_type == "paused":
        if state_name == "AwaitingApproval" or held_below:
            return "awaiting_approval"
        return "waiting" if waiting_below else "running"
    if state_type == "scheduled" and state_name in _WAITING_NAMES:
        return "waiting"
    # scheduled (due), pending, running, crashed (a crash resumes) — in progress
    return "running"


# A descendant resting on a provider. The listing's CASE and the 'waiting' filter both use it.
_WAITING_BELOW_SQL = (
    "EXISTS (SELECT 1 FROM baton.runs d WHERE d.root_run_id = r.id "
    "  AND d.state_type = 'scheduled' AND d.state_name IN ('Waiting', 'AwaitingRetry'))"
)

# A descendant resting at an approval gate: a composite's child the firm gates (`before_child`).
# The gate row itself is excluded — it is the held CHILD a person decides, and the one the
# approvals screens name.
_HELD_BELOW_SQL = (
    "EXISTS (SELECT 1 FROM baton.runs d WHERE d.root_run_id = r.id AND d.kind <> 'gate' "
    "  AND d.state_type = 'paused' AND d.state_name = 'AwaitingApproval')"
)

# The SQL twin of status_word, over the root rows the listing reads. Kept beside the function so
# a new state name is added to both or neither (test_baton_queries pins their agreement).
_STATUS_SQL = f"""
CASE
  WHEN r.state_type = 'completed' AND r.state_name = 'Skipped' THEN 'skipped'
  WHEN r.state_type = 'completed' AND r.state_name = 'Partial' THEN 'partial'
  WHEN r.state_type = 'completed' THEN 'succeeded'
  WHEN r.state_type = 'failed' AND r.state_name = 'NeedsAttention' THEN 'needs_attention'
  WHEN r.state_type = 'failed' AND r.state_name = 'Conflicted' THEN 'conflicted'
  WHEN r.state_type = 'failed' AND r.state_name = 'TimedOut' THEN 'timed_out'
  WHEN r.state_type = 'failed' THEN 'failed'
  WHEN r.state_type = 'cancelled' AND r.state_name = 'Rejected' THEN 'rejected'
  WHEN r.state_type = 'cancelled' THEN 'cancelled'
  WHEN r.state_type = 'paused' AND r.state_name = 'AwaitingApproval' THEN 'awaiting_approval'
  WHEN r.state_type = 'paused' AND {_HELD_BELOW_SQL} THEN 'awaiting_approval'
  WHEN r.state_type = 'paused' AND {_WAITING_BELOW_SQL} THEN 'waiting'
  WHEN r.state_type = 'scheduled' AND r.state_name IN ('Waiting', 'AwaitingRetry') THEN 'waiting'
  ELSE 'running'
END
"""

# status word → the predicate selecting it. `failed` is the whole failed TYPE minus the parked
# runs, which is what the "Failed" tile counts: a run that stopped, as against one waiting for a
# person. `succeeded` is the whole completed type, so a Partial or Skipped root is found under it.
_STATE_FILTER: dict[str, str] = {
    "running": (
        "(r.state_type IN ('pending', 'running', 'crashed') "
        " OR (r.state_type = 'scheduled' AND r.state_name NOT IN ('Waiting', 'AwaitingRetry')) "
        " OR (r.state_type = 'paused' AND r.state_name <> 'AwaitingApproval' "
        f"     AND NOT {_HELD_BELOW_SQL} AND NOT {_WAITING_BELOW_SQL}))"
    ),
    "waiting": (
        "((r.state_type = 'scheduled' AND r.state_name IN ('Waiting', 'AwaitingRetry')) "
        " OR (r.state_type = 'paused' AND r.state_name <> 'AwaitingApproval' "
        f"     AND NOT {_HELD_BELOW_SQL} AND {_WAITING_BELOW_SQL}))"
    ),
    "awaiting_approval": (
        f"(r.state_type = 'paused'  AND (r.state_name = 'AwaitingApproval' OR {_HELD_BELOW_SQL}))"
    ),
    "succeeded": "(r.state_type = 'completed')",
    "partial": "(r.state_type = 'completed' AND r.state_name = 'Partial')",
    "skipped": "(r.state_type = 'completed' AND r.state_name = 'Skipped')",
    "needs_attention": "(r.state_type = 'failed' AND r.state_name = 'NeedsAttention')",
    "conflicted": "(r.state_type = 'failed' AND r.state_name = 'Conflicted')",
    "timed_out": "(r.state_type = 'failed' AND r.state_name = 'TimedOut')",
    "failed": "(r.state_type = 'failed' AND r.state_name <> 'NeedsAttention')",
    "rejected": "(r.state_type = 'cancelled' AND r.state_name = 'Rejected')",
    "cancelled": "(r.state_type = 'cancelled')",
}


# ---- names and providers -----------------------------------------------------------------


def _provider(action_key: str) -> str | None:
    """The provider is the leaf key's prefix when that prefix is a real provider: ``seccl.payment``
    → ``seccl``. An internal resolver (``load.plan``, ``resolve.contact_provider``) has none."""
    from app.providers import PROVIDERS

    head = action_key.partition(".")[0]
    return head if head in PROVIDERS else None


def _provider_labels() -> dict[str, str]:
    from app.actions.catalogue import SOURCES

    return dict(SOURCES)


def action_name(action_key: str, registry: Registry = default_registry) -> str:
    """The human name of any action, leaf or composite: the actions catalogue's where it lists
    one, else the reviews catalogue's (a held book change — "Edit client details", not
    ``client.details_changed``), else the registered definition's, else the key itself. A key a
    firm could once hold and no longer can is still named from the retired list, so settled
    history reads."""
    from app.actions.catalogue import BY_KEY
    from app.reviews.catalogue import RETIRED
    from app.reviews.catalogue import get as review_get

    spec = BY_KEY.get(action_key)
    if spec is not None:
        return spec.name
    review = review_get(action_key)
    if review is not None:
        return review.label
    defn = registry.get(action_key)
    if defn is not None:
        return defn.name
    return RETIRED.get(action_key, action_key)


def _stamp(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


# ---- the provider's reason ---------------------------------------------------------------

_JSON_TAIL = re.compile(r"^(?P<head>.*?)[\s:,-]*(?P<body>[\[{].*[\]}])\s*$", re.DOTALL)


def _split_json_tail(message: str | None) -> tuple[str | None, Any]:
    """An engine message often ends in the vendor's whole JSON body ("... failed: 422 {…}"). The
    words before it are the sentence a person reads; the body is the vendor's answer, which is
    worth reading as such when the attempt recorded no response of its own."""
    if not message:
        return None, None
    match = _JSON_TAIL.match(message.strip())
    if match is None:
        return message.strip(), None
    head = match.group("head").strip().rstrip(":,-").strip()
    try:
        body = json.loads(match.group("body"))
    except ValueError:
        return message.strip(), None
    return head or None, body


def _trim_json_tail(message: str | None) -> str | None:
    return _split_json_tail(message)[0]


def _truthy(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0"):
            return False
    return None


def summarise_failure(
    message: str | None, *, response: Any = None, status_code: int | None = None
) -> dict[str, Any]:
    """The provider's refusal as a headline a person reads, with the raw body left to the call.

    SEI answers ``{"error": {"code", "message", "userMessage", "trackingId", "errors": [{"field",
    "reason"}], "transient"}}`` — the ``reason`` is the sentence that explains the refusal, the
    ``message`` is "Business Exception". Seccl and Dynamics answer ``{"message"}`` /
    ``{"error": {"code", "message"}}``. Anything else falls back to the engine's own message with
    its JSON tail cut off. ``transient`` is whatever the body said (SEI says), else None."""
    headline: str | None = None
    detail: str | None = None
    code: str | None = None
    tracking: str | None = None
    transient: bool | None = None
    trimmed, tail = _split_json_tail(message)
    # The attempt's own response first; else the body the message carried in its tail — a leaf
    # that raised Permanent(str(exc)) without recording evidence still said what the vendor said.
    body = response if isinstance(response, dict) else tail if isinstance(tail, dict) else None
    if body is not None:
        err = body.get("error")
        if isinstance(err, dict):
            reasons = [
                str(item.get("reason") or item.get("message") or "")
                for item in (err.get("errors") or [])
                if isinstance(item, dict)
            ]
            reasons = [reason for reason in reasons if reason]
            generic = err.get("userMessage") or err.get("message")
            headline = reasons[0] if reasons else (str(generic) if generic else None)
            if reasons and generic and str(generic) != headline:
                detail = str(generic)
            code = str(err["code"]) if err.get("code") else None
            tracking = str(err["trackingId"]) if err.get("trackingId") else None
            transient = _truthy(err.get("transient"))
        elif isinstance(err, str) and err.strip():
            headline = err.strip()
        if headline is None:
            for key in ("userMessage", "detail", "message", "reason", "error_description"):
                value = body.get(key)
                if isinstance(value, str) and value.strip():
                    headline = value.strip()
                    break
    if headline is None:
        headline = trimmed or (message.strip() if message else None)
    elif trimmed and trimmed != headline and detail is None:
        detail = trimmed
    return {
        "headline": headline,
        "detail": detail,
        "code": code,
        "tracking_id": tracking,
        "transient": transient,
        "status_code": status_code,
    }


# ---- the tree ----------------------------------------------------------------------------


_CALL_IN_MESSAGE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(\S+)\s+failed:\s*(\d{3})\b")


def _recover_call(attempt: AttemptView) -> tuple[str | None, str | None, int | None, Any]:
    """The call an attempt made, when the leaf recorded none. Before the leaves captured their
    vendor calls on failure, a refused call left only its message — "POST cam_createcontact
    failed: 400 {…}" — and the attempt row showed the leaf's params as the request. The method,
    the path, the code and the body are all in that sentence, so read them back out of it."""
    if attempt.method or attempt.url:
        return attempt.method, attempt.url, attempt.status_code, attempt.response
    if not attempt.error:
        return None, None, attempt.status_code, attempt.response
    match = _CALL_IN_MESSAGE.search(attempt.error)
    if match is None:
        return None, None, attempt.status_code, attempt.response
    _head, tail = _split_json_tail(attempt.error)
    response = attempt.response if attempt.response is not None else tail
    return match.group(1), match.group(2), attempt.status_code or int(match.group(3)), response


def _attempt_row(attempt: AttemptView) -> dict[str, Any]:
    method, url, status_code, response = _recover_call(attempt)
    # A CALL made a request to a provider; a FUNCTION leaf (a book read, a resolver) has an input
    # and an output, which the engine stores in the same two columns. The screen labels them so.
    kind = "call" if (method or url) else "function"
    return {
        "attempt_no": attempt.attempt_no,
        # The engine's own word: ok · skipped · wait · retry · failed · timed_out · crashed. A wait
        # is not a failure, and the UI badges each word for what it is.
        "outcome": attempt.outcome,
        "kind": kind,
        "recovered": kind == "call" and not (attempt.method or attempt.url),
        "method": method,
        "url": url,
        "status_code": status_code,
        "error": attempt.error,
        "error_type": attempt.error_type,
        "duration_ms": attempt.duration_ms,
        "started_at": _stamp(attempt.started_at),
        "ended_at": _stamp(attempt.ended_at),
        "request": firm_visible(attempt.request),
        "response": firm_visible(response),
    }


def _options(action_key: str, registry: Registry) -> dict[str, Any]:
    """The leaf policy a screen states beside a node: how many retries, how long a wait may last,
    and what the parent does with its failure."""
    defn = registry.get(action_key)
    if defn is None:
        return {"retries": None, "wait_budget_seconds": None, "idempotent": None}
    opts = defn.options
    return {
        "retries": opts.retries,
        "wait_budget_seconds": opts.wait_timeout_seconds,
        "idempotent": opts.idempotent,
    }


def _waiting_below(detail: RunDetail) -> bool:
    for child in detail.children:
        state = child.run.state
        if state.type.value == "scheduled" and state.name in _WAITING_NAMES:
            return True
        if _waiting_below(child):
            return True
    return False


def _held_below(detail: RunDetail) -> bool:
    """A descendant ACTION (not the gate row) resting at an approval gate."""
    for child in detail.children:
        run = child.run
        if (
            run.kind != "gate"
            and run.state.type.value == "paused"
            and run.state.name == "AwaitingApproval"
        ):
            return True
        if _held_below(child):
            return True
    return False


def project_node(
    detail: RunDetail, *, depth: int = 0, registry: Registry = default_registry
) -> dict[str, Any]:
    """One node of the tree, recursively: the run, its calls, its result and its children, in call
    order. Names, roles and people are resolved afterwards by :func:`run_detail`, which has the
    session; this is pure so it can be tested on an in-memory engine with its own registry."""
    run = detail.run
    state = run.state
    is_gate = run.kind == "gate"
    children = [
        project_node(child, depth=depth + 1, registry=registry) for child in detail.children
    ]
    attempts = [_attempt_row(attempt) for attempt in detail.attempts]
    failed = [a for a in attempts if a["outcome"] in ("failed", "timed_out", "crashed")]
    last_failed = failed[-1] if failed else None
    failure = None
    if state.type.value in ("failed", "crashed"):
        failure = summarise_failure(
            state.message or run.error,
            response=last_failed["response"] if last_failed else None,
            status_code=last_failed["status_code"] if last_failed else None,
        )
    return {
        "uid": run.uid,
        "action_key": run.action_key,
        "name": "Approval" if is_gate else action_name(run.action_key, registry),
        "kind": run.kind,
        "provider": None if is_gate else _provider(run.action_key),
        "key": run.key,
        "position": run.position,
        "depth": depth,
        "after_keys": list(detail.after_keys),
        "leaf": run.leaf,
        "state": {"type": state.type.value, "name": state.name, "message": state.message},
        "status": status_word(
            state.type.value,
            state.name,
            waiting_below=_waiting_below(detail),
            held_below=_held_below(detail),
        ),
        "on_failure": run.on_failure,
        **_options(run.action_key, registry),
        "retries_used": sum(1 for a in attempts if a["outcome"] == "retry"),
        "params": firm_visible(run.params) if run.params else {},
        "result": firm_visible(run.result) if run.result is not None else None,
        "requested_at": _stamp(run.requested_at),
        "started_at": _stamp(run.started_at),
        "ended_at": _stamp(run.ended_at),
        "scheduled_at": _stamp(run.scheduled_at),
        "wait_started_at": _stamp(run.wait_started_at),
        "passes": run.passes,
        "attempt_count": run.attempt_count,
        # Who may decide, and what was decided. Gates carry gate_roles; a gated run carries
        # approver_role_ids. Both are ids here; run_detail swaps in the names.
        "case_no": run.case_no,
        "justification": run.justification,
        "approver_role_ids": list(run.gate_roles or []) if is_gate else list(run.approver_role_ids),
        "gate_message": run.gate_message,
        "decision": run.decision,
        "decided_by": run.decided_by,
        "decided_at": _stamp(run.decided_at),
        "decision_note": run.decision_note,
        "failure": failure,
        "attempts": attempts,
        "children": children,
    }


def _walk(node: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yield node
    for child in node["children"]:
        yield from _walk(child)


def first_problem(node: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The node to point a person at: the deepest failed node on the first failed path — the leaf
    whose call was refused, not the composite that merely reports it."""
    if node["state"]["type"] not in ("failed", "crashed"):
        return None
    for child in node["children"]:
        found = first_problem(child)
        if found is not None:
            return found
    return node


def held_node(node: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The node a person decides: this run when it rests at its own gate, else the shallowest
    descendant action resting at one (a composite's child the firm gates). The decision is posted
    with THAT node's uid — the engine looks for the open gate under the run it is given."""
    for candidate in _walk(node):
        if (
            candidate["kind"] != "gate"
            and candidate["state"]["type"] == "paused"
            and candidate["state"]["name"] == "AwaitingApproval"
        ):
            return candidate
    return None


def open_gate(node: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The gate holding this run, when it (or a descendant the firm gates) is awaiting approval."""
    held = held_node(node)
    if held is None:
        return None
    children: list[Mapping[str, Any]] = held["children"]
    for child in children:
        if child["kind"] == "gate" and child["state"]["type"] == "paused":
            return child
    return None


def can_decide(
    node: Mapping[str, Any], *, caller_id: str | None, caller_role_ids: Sequence[int]
) -> bool:
    """Mirror of the engine's own gate check: not the requester, and holding an approver role
    (or no role named). Advisory — the engine decides again on the POST."""
    gate = open_gate(node)
    if gate is None:
        return False
    if caller_id is not None and node.get("requested_by") == caller_id:
        return False
    roles = set(gate["approver_role_ids"])
    return not roles or bool(roles & set(caller_role_ids))


# ---- the detail --------------------------------------------------------------------------


async def _names(
    session: AsyncSession, *, user_ids: set[str], role_ids: set[int]
) -> tuple[dict[str, str], dict[int, str]]:
    users: dict[str, str] = {}
    roles: dict[int, str] = {}
    if user_ids:
        rows = (
            await session.execute(
                text(
                    "SELECT id::text AS id, COALESCE(name, email) AS name FROM auth.users "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": sorted(user_ids)},
            )
        ).mappings()
        users = {str(row["id"]): str(row["name"]) for row in rows}
    if role_ids:
        rows = (
            await session.execute(
                text("SELECT id, name FROM auth.roles WHERE id = ANY(:ids)"),
                {"ids": sorted(role_ids)},
            )
        ).mappings()
        roles = {int(row["id"]): str(row["name"]) for row in rows}
    return users, roles


async def _subject_records(
    session: AsyncSession,
    *,
    tenant_id: int,
    subject_kind: str | None,
    subject_uid: str | None,
    params: Mapping[str, Any],
    contact_id: int | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The client and the account a run is about, by name, so the header can link them. The
    account is the subject when the subject is an account, else the one the params name."""
    contact: dict[str, Any] | None = None
    account: dict[str, Any] | None = None
    if contact_id:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT uid, COALESCE(name, "
                        "  NULLIF(TRIM(CONCAT_WS(' ', first_name, surname)), ''), email) AS name "
                        "FROM crm.contacts WHERE id = :c AND tenant_id = :t"
                    ),
                    {"c": contact_id, "t": tenant_id},
                )
            )
            .mappings()
            .first()
        )
        if row is not None:
            contact = {"uid": row["uid"], "name": row["name"]}
    account_uid = subject_uid if subject_kind == "account" else None
    account_id = params.get("account_id") if isinstance(params, Mapping) else None
    if account_uid or account_id:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT a.uid, a.name, a.custodian::text AS custodian, "
                        "       p.product_type::text AS product_type, c.uid AS contact_uid, "
                        "       COALESCE(c.name, "
                        "         NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), "
                        "         c.email) AS contact_name "
                        "FROM platform.accounts a "
                        "LEFT JOIN platform.products p ON p.id = a.product_id "
                        "LEFT JOIN crm.contacts c ON c.id = a.contact_id "
                        "WHERE a.tenant_id = :t AND (a.uid = :u OR a.id = :id)"
                    ),
                    {"t": tenant_id, "u": account_uid, "id": account_id},
                )
            )
            .mappings()
            .first()
        )
        if row is not None:
            account = {
                "uid": row["uid"],
                "name": row["name"],
                "product_type": row["product_type"],
                "custodian": row["custodian"],
            }
            if contact is None and row["contact_uid"]:
                contact = {"uid": row["contact_uid"], "name": row["contact_name"]}
    return contact, account


async def _journey(session: AsyncSession, *, tenant_id: int, run_uid: str) -> dict[str, Any] | None:
    """The onboarding step that raised this run, when one did: the application to link, and the
    step's title. A step records only the run uid, so this is the way back."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT ap.uid AS application_uid, cs.title AS step_title, cs.step_id "
                    "FROM flows.case_steps cs "
                    "JOIN flows.cases c ON c.id = cs.case_id "
                    "LEFT JOIN crm.applications ap "
                    "  ON c.resource_type = 'application' AND ap.id = c.resource_id "
                    "WHERE cs.tenant_id = :t AND cs.data ->> 'action_run_uid' = :u "
                    "ORDER BY cs.id LIMIT 1"
                ),
                {"t": tenant_id, "u": run_uid},
            )
        )
        .mappings()
        .first()
    )
    if row is None or not row["application_uid"]:
        return None
    return {
        "application_uid": row["application_uid"],
        "step_title": row["step_title"] or row["step_id"],
    }


async def run_detail(
    session: AsyncSession,
    *,
    tenant_id: int,
    run_uid: str,
    caller_id: str | None = None,
    caller_role_ids: Sequence[int] = (),
) -> dict[str, Any]:
    """One run for its page: the root node and its tree, the people and roles by name, the client
    and account it is about, the journey step that raised it, the lifecycle events, and the
    advice a screen needs to offer one next action (``can_retry`` / ``can_cancel`` /
    ``can_decide``). Raises ``ActionError`` (404) when the uid is not a baton run for this firm."""
    from app.actions import service
    from app.actions.catalogue import BY_KEY

    detail = await service.detail(session, run_uid=run_uid, tenant_id=tenant_id)
    root = project_node(detail, registry=default_registry)
    run = detail.run
    spec = BY_KEY.get(run.action_key)

    # Every person and role the tree or its events name, resolved in two queries.
    user_ids: set[str] = set()
    role_ids: set[int] = set()
    for node in _walk(root):
        if node["decided_by"]:
            user_ids.add(str(node["decided_by"]))
        role_ids.update(int(role) for role in node["approver_role_ids"])
    if run.requested_by:
        user_ids.add(str(run.requested_by))
    for event in detail.events:
        if event.actor_id:
            user_ids.add(str(event.actor_id))
    users, roles = await _names(session, user_ids=user_ids, role_ids=role_ids)

    def name_roles(node: dict[str, Any]) -> None:
        node["approver_roles"] = [
            roles.get(int(role), f"role {role}") for role in node["approver_role_ids"]
        ]
        node["decided_by_name"] = users.get(str(node["decided_by"])) if node["decided_by"] else None
        for child in node["children"]:
            name_roles(child)

    name_roles(root)
    root["requested_by"] = run.requested_by
    root["requested_by_name"] = users.get(str(run.requested_by)) if run.requested_by else None

    contact, account = await _subject_records(
        session,
        tenant_id=tenant_id,
        subject_kind=run.subject_kind,
        subject_uid=run.subject_uid,
        params=run.params or {},
        contact_id=run.contact_id,
    )
    journey = await _journey(session, tenant_id=tenant_id, run_uid=run_uid)

    # The events are keyed by run id; the nodes by uid. One query names which node each is about.
    ids = (
        await session.execute(
            text(
                "SELECT id, uid FROM baton.runs WHERE uid = :u "
                "OR root_run_id = (SELECT id FROM baton.runs WHERE uid = :u)"
            ),
            {"u": run_uid},
        )
    ).mappings()
    uid_by_id = {int(row["id"]): str(row["uid"]) for row in ids}
    names_by_uid = {node["uid"]: node["name"] for node in _walk(root)}
    events = [
        {
            "id": event.id,
            "type": event.event_type,
            "at": _stamp(event.created_at),
            "actor_id": event.actor_id,
            "actor_name": users.get(str(event.actor_id)) if event.actor_id else None,
            "run_uid": uid_by_id.get(event.run_id),
            "run_name": names_by_uid.get(uid_by_id.get(event.run_id, ""), None),
            "payload": event.payload or {},
        }
        for event in detail.events
    ]

    problem = first_problem(root)
    gate = open_gate(root)
    held = held_node(root)
    terminal = root["state"]["type"] in ("completed", "failed", "cancelled")
    waiting = [node for node in _walk(root) if node["status"] == "waiting" and node["scheduled_at"]]
    next_poll = min((node["scheduled_at"] for node in waiting), default=None)
    leaves = [node for node in _walk(root) if node["attempts"] or node["leaf"]]

    # Who could decide the open gate, other than the requester — so a screen can say "nobody
    # else holds Compliance" rather than silently showing no buttons — and when the approvers
    # were last reminded.
    from app.actions.approvals import REMINDED_EVENT, eligible_approvers

    eligible = (
        await eligible_approvers(
            session,
            tenant_id=tenant_id,
            role_ids=[int(role) for role in gate["approver_role_ids"]],
            exclude_user_id=str(run.requested_by) if run.requested_by else None,
        )
        if gate is not None
        else None
    )
    reminded = [event["at"] for event in events if event["type"] == REMINDED_EVENT]
    # A held book review's command rides on its params: the proposal and the snapshot it was
    # taken against, which is the now-vs-proposed body the Reviews queue draws.
    params = root["params"] if isinstance(root["params"], Mapping) else {}
    change = (
        {"payload": params.get("payload"), "before": params.get("before")}
        if run.meta.get("review") and isinstance(params.get("payload"), Mapping)
        else None
    )

    root.update(
        {
            "description": spec.description if spec else "",
            "category": spec.category if spec else None,
            "event_type": run.meta.get("event_type"),
            "subject_kind": run.subject_kind,
            "subject_uid": run.subject_uid,
            "contact": contact,
            "account": account,
            "journey": journey,
            "input": firm_visible(run.meta["input"]) if run.meta.get("input") else None,
            "change": change,
            "manual": bool(run.meta.get("manual")),
            "is_review": bool(run.meta.get("review")),
            "approval": {
                "required": bool(root["approver_role_ids"]) or gate is not None,
                "roles": gate["approver_roles"] if gate else root["approver_roles"],
                "gate_uid": gate["uid"] if gate else None,
                # The node whose uid a decision is posted with: the root at its own gate, or the
                # gated child of a composite.
                "held_uid": held["uid"] if held else None,
                "held_name": held["name"] if held else None,
                "held_since": held["requested_at"] if held else None,
                "can_decide": can_decide(
                    root, caller_id=caller_id, caller_role_ids=list(caller_role_ids)
                ),
                "eligible_approvers": eligible,
                "reminded_at": max(reminded) if reminded else None,
                "reminders": len(reminded),
            },
            "attention": (
                {
                    "node_uid": problem["uid"],
                    "node_name": problem["name"],
                    "provider": problem["provider"],
                    **(problem["failure"] or {}),
                }
                if problem is not None
                else None
            ),
            "timing": {
                "requested_at": root["requested_at"],
                "started_at": root["started_at"],
                "ended_at": root["ended_at"],
                "next_poll_at": next_poll,
                "passes": root["passes"],
                "calls": sum(len(node["attempts"]) for node in _walk(root)),
                "leaves": len(leaves),
            },
            "can_retry": problem is not None,
            "can_cancel": not terminal,
            "events": events,
        }
    )
    return root


# ---- the listing -------------------------------------------------------------------------

#: The filters the listing understands, by the key the UI sends. `held` is the approvals seat's
#: own: `over_day` (at the gate for more than a day), `undecidable` (nobody but the requester
#: holds an approver role), `money` (the recorded input carries a figure).
LIST_FILTER_KEYS: tuple[str, ...] = (
    "state",
    "action",
    "provider",
    "requested_by",
    "contact",
    "held",
)

# The node a person decides, beside each root: the root itself when it rests at its own gate,
# else the shallowest gated descendant. Everything the approvals seat says about a row — who may
# decide it, since when, whether anyone can — reads off this join.
_HELD_JOIN = """
LEFT JOIN LATERAL (
  SELECT h.uid, h.approver_role_ids, h.requested_at, h.case_no, h.action_key
  FROM baton.runs h
  WHERE (h.id = r.id OR h.root_run_id = r.id) AND h.kind <> 'gate'
    AND h.state_type = 'paused' AND h.state_name = 'AwaitingApproval'
  ORDER BY h.depth, h.position NULLS FIRST, h.id
  LIMIT 1
) held ON true
"""

# A figure on the recorded input, when the door wrote one: a payment's amount, a transfer's total.
_HELD_AMOUNT_SQL = (
    "CASE WHEN (r.meta -> 'input' ->> 'amount') ~ '^-?[0-9]+(\\.[0-9]+)?$' "
    "       THEN (r.meta -> 'input' ->> 'amount')::numeric "
    "     WHEN (r.meta -> 'input' ->> 'total_value') ~ '^-?[0-9]+(\\.[0-9]+)?$' "
    "       THEN (r.meta -> 'input' ->> 'total_value')::numeric END"
)


def _eligible_sql() -> str:
    from app.actions.approvals import eligible_sql

    return (
        "CASE WHEN held.uid IS NULL THEN NULL ELSE "
        + eligible_sql("held.approver_role_ids", "r.requested_by")
        + " END"
    )


_HELD_FILTER: dict[str, str] = {
    "over_day": "(held.uid IS NOT NULL AND held.requested_at < now() - interval '1 day')",
    "undecidable": f"(held.uid IS NOT NULL AND ({_eligible_sql()}) = 0)",
    "money": f"(held.uid IS NOT NULL AND ({_HELD_AMOUNT_SQL}) IS NOT NULL)",
}

#: Sortable columns — a CLOSED set, because `sort` arrives on a query string.
_SORTS: dict[str, str] = {
    # columns of the `base` CTE, not of `r`: the ORDER BY runs over the CTE's output
    "requested": "requested_at",
    "name": "action_key",
    "state": "status",
    "requested_by": "requested_by_name",
    "duration": "(COALESCE(ended_at, now()) - started_at)",
}

_ROW_SQL = f"""
SELECT r.id, r.uid, r.action_key, r.case_no, r.subject_kind, r.subject_uid,
       r.requested_at, r.started_at, r.ended_at, r.state_type, r.state_name, r.state_message,
       r.requested_by::text AS requested_by,
       ({_STATUS_SQL}) AS status,
       COALESCE(pu.name, pu.email) AS requested_by_name,
       c.uid AS contact_uid,
       COALESCE(c.name, NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), c.email)
         AS contact_name,
       COALESCE(
         (SELECT array_agg(DISTINCT split_part(d.action_key, '.', 1)) FROM baton.runs d
           WHERE d.root_run_id = r.id
             AND split_part(d.action_key, '.', 1) = ANY(CAST(:providers AS text[]))),
         ARRAY[]::text[]) AS providers,
       COALESCE(
         (SELECT json_agg(json_build_object(
              'uid', ch.uid, 'key', ch.action_key, 'kind', ch.kind,
              'state_type', ch.state_type, 'state_name', ch.state_name) ORDER BY ch.position)
           FROM baton.runs ch WHERE ch.parent_run_id = r.id),
         '[]'::json) AS children,
       (held.uid IS NOT NULL
         AND (r.requested_by IS NULL OR r.requested_by IS DISTINCT FROM CAST(:me AS uuid))
         AND (cardinality(held.approver_role_ids) = 0
              OR held.approver_role_ids && CAST(:roles AS int[]))) AS can_decide,
       (r.meta ->> 'review' = 'true') AS is_review,
       held.uid AS held_uid,
       held.requested_at AS held_since,
       COALESCE(held.approver_role_ids, r.approver_role_ids) AS approver_role_ids,
       COALESCE(
         (SELECT array_agg(ro.name ORDER BY ro.name) FROM auth.roles ro
           WHERE ro.id = ANY(COALESCE(held.approver_role_ids, r.approver_role_ids))),
         ARRAY[]::text[]) AS approver_roles,
       ({_eligible_sql()}) AS eligible_approvers,
       ({_HELD_AMOUNT_SQL}) AS held_amount
FROM baton.runs r
LEFT JOIN auth.users pu ON pu.id = r.requested_by
LEFT JOIN crm.contacts c ON c.id = r.contact_id
{_HELD_JOIN}
"""  # noqa: S608 - the only interpolations are the module's own SQL constants


def _list_clauses(
    *,
    tenant_id: int,
    q: str | None,
    filters: Mapping[str, list[str] | None],
    skip: str | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """The WHERE for the listing, optionally with one filter LIFTED (for that filter's facet)."""
    clauses = ["r.tenant_id = :t", "r.parent_run_id IS NULL"]
    params: dict[str, Any] = {"t": tenant_id}
    if q:
        clauses.append(
            "(:q_norm <% r._search OR lower(r.uid) = :q_norm OR r.case_no::text = :q_norm)"
        )
        params["q_norm"] = q.strip().lower()
    states = filters.get("state")
    if states and skip != "state":
        known = [s for s in states if s in _STATE_FILTER]
        if known:
            clauses.append("(" + " OR ".join(_STATE_FILTER[s] for s in known) + ")")
    actions = filters.get("action")
    if actions and skip != "action":
        clauses.append("r.action_key = ANY(CAST(:actions AS text[]))")
        params["actions"] = list(actions)
    providers = filters.get("provider")
    if providers and skip != "provider":
        clauses.append(
            "EXISTS (SELECT 1 FROM baton.runs d WHERE d.root_run_id = r.id "
            "  AND split_part(d.action_key, '.', 1) = ANY(CAST(:f_providers AS text[])))"
        )
        params["f_providers"] = list(providers)
    requesters = filters.get("requested_by")
    if requesters and skip != "requested_by":
        clauses.append("r.requested_by = ANY(CAST(:requesters AS uuid[]))")
        params["requesters"] = list(requesters)
    contacts = filters.get("contact")
    if contacts and skip != "contact":
        clauses.append("c.uid = ANY(CAST(:contacts AS text[]))")
        params["contacts"] = list(contacts)
    held = filters.get("held")
    if held and skip != "held":
        known = [h for h in held if h in _HELD_FILTER]
        if known:
            clauses.append("(" + " OR ".join(_HELD_FILTER[h] for h in known) + ")")
    return clauses, params


async def list_all_runs(
    session: AsyncSession,
    *,
    tenant_id: int,
    q: str | None = None,
    state: str | Sequence[str] | None = None,
    action_key: str | Sequence[str] | None = None,
    provider: str | Sequence[str] | None = None,
    requested_by: str | Sequence[str] | None = None,
    contact_uid: str | Sequence[str] | None = None,
    held: str | Sequence[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    sort: str | None = None,
    direction: str | None = None,
    caller_id: str | None = None,
    caller_role_ids: Sequence[int] = (),
) -> dict[str, Any]:
    """The runs table across every action, server-paged: one row per ROOT run with its status,
    subject, requester, timing, the providers it reached and its immediate children (the step
    bar), in the ``{items, total, facets, facet_labels, stats}`` envelope every book listing
    returns. Roots only — the children are the tree, not the listing. A root held at a gate (its
    own, or a gated child's) also carries the gate's facts: who may decide it, how many people
    could, since when, and the figure at stake."""
    from app.providers import PROVIDERS

    filters: dict[str, list[str] | None] = {
        "state": clean_many(state),
        "action": clean_many(action_key),
        "provider": clean_many(provider),
        "requested_by": clean_many(requested_by),
        "contact": clean_many(contact_uid),
        "held": clean_many(held),
    }
    q = clean(q)
    page = min(max(1, limit or DEFAULT_LIMIT), PAGE_LIMIT_MAX)
    skip_rows = max(0, offset)
    order = _SORTS.get(sort or "", "requested_at")
    ascending = (direction or "").lower() == "asc"
    direction_sql = "ASC" if ascending else "DESC"
    # A stable tiebreak so paging never shows a row twice or drops one across a boundary.
    order_by = f"{order} {direction_sql} NULLS LAST, id {direction_sql}"

    clauses, params = _list_clauses(tenant_id=tenant_id, q=q, filters=filters)
    where = " AND ".join(clauses)
    sql = (
        f"WITH base AS ({_ROW_SQL} WHERE {where}) "  # noqa: S608 - clauses are constants
        "SELECT base.*, count(*) OVER () AS total FROM base "
        f"ORDER BY {order_by} LIMIT :lim OFFSET :off"
    )
    rows = (
        await session.execute(
            text(sql),
            {
                **params,
                "lim": page,
                "off": skip_rows,
                "providers": list(PROVIDERS),
                "me": caller_id,
                "roles": list(caller_role_ids),
            },
        )
    ).mappings()
    items: list[dict[str, Any]] = []
    total = 0
    for row in rows:
        total = int(row["total"])
        raw_children = row["children"]
        children = json.loads(raw_children) if isinstance(raw_children, str) else raw_children
        items.append(
            {
                "uid": row["uid"],
                "action_key": row["action_key"],
                "name": action_name(str(row["action_key"])),
                "status": row["status"],
                "state": {
                    "type": row["state_type"],
                    "name": row["state_name"],
                    "message": row["state_message"],
                },
                "case_no": row["case_no"],
                "subject_kind": row["subject_kind"],
                "subject_uid": row["subject_uid"],
                "contact_uid": row["contact_uid"],
                "contact_name": row["contact_name"],
                "requested_by": row["requested_by"],
                "requested_by_name": row["requested_by_name"],
                "requested_at": _stamp(row["requested_at"]),
                "started_at": _stamp(row["started_at"]),
                "ended_at": _stamp(row["ended_at"]),
                "providers": list(row["providers"] or []),
                "children": [
                    {
                        "uid": child["uid"],
                        "key": child["key"],
                        "name": "Approval"
                        if child["kind"] == "gate"
                        else action_name(child["key"]),
                        "kind": child["kind"],
                        "status": status_word(child["state_type"], child["state_name"]),
                    }
                    for child in (children or [])
                ],
                "can_decide": bool(row["can_decide"]),
                "is_review": bool(row["is_review"]),
                "held_uid": row["held_uid"],
                "held_since": _stamp(row["held_since"]),
                "approver_role_ids": [int(role) for role in (row["approver_role_ids"] or [])],
                "approver_roles": list(row["approver_roles"] or []),
                "eligible_approvers": (
                    int(row["eligible_approvers"])
                    if row["eligible_approvers"] is not None
                    else None
                ),
                "held_amount": float(row["held_amount"])
                if row["held_amount"] is not None
                else None,
            }
        )
    facets, counts = await _facets(session, tenant_id=tenant_id, q=q, filters=filters)
    stats = await _run_stats(session, tenant_id=tenant_id)
    labels = _provider_labels()
    return {
        "items": items,
        "total": total,
        "limit": page,
        "offset": skip_rows,
        "facets": facets,
        "facet_counts": counts,
        "facet_labels": {
            "action": {key: action_name(key) for key in facets.get("action", [])},
            "provider": {code: labels.get(code, code) for code in facets.get("provider", [])},
        },
        "stats": stats,
    }


async def _facets(
    session: AsyncSession,
    *,
    tenant_id: int,
    q: str | None,
    filters: Mapping[str, list[str] | None],
) -> tuple[dict[str, list[str]], dict[str, dict[str, int]]]:
    """Each facet's options over the match set with its OWN filter lifted (app/listing.py's third
    rule), so picking a state never makes the other states disappear from the menu."""
    from app.providers import PROVIDERS

    options: dict[str, list[str]] = {}
    counts: dict[str, dict[str, int]] = {}
    for key, expr in (
        ("state", f"({_STATUS_SQL})"),
        ("action", "r.action_key"),
        ("provider", None),
    ):
        clauses, params = _list_clauses(tenant_id=tenant_id, q=q, filters=filters, skip=key)
        where = " AND ".join(clauses)
        if expr is not None:
            sql = (
                f"SELECT {expr} AS v, count(*) AS n FROM baton.runs r "  # noqa: S608 - constants
                "LEFT JOIN crm.contacts c ON c.id = r.contact_id "
                f"{_HELD_JOIN} "
                f"WHERE {where} GROUP BY 1 ORDER BY 1"
            )
        else:
            sql = (
                "SELECT p AS v, count(*) AS n FROM ("  # noqa: S608
                "  SELECT DISTINCT r.id, split_part(d.action_key, '.', 1) AS p "
                "  FROM baton.runs r "
                "  LEFT JOIN crm.contacts c ON c.id = r.contact_id "
                f"  {_HELD_JOIN} "
                "  JOIN baton.runs d ON d.root_run_id = r.id "
                f" WHERE {where} "  # noqa: S608 - clauses are constants, values are bound
                "   AND split_part(d.action_key, '.', 1) = ANY(CAST(:providers AS text[]))"
                ") x GROUP BY 1 ORDER BY 1"
            )
            params["providers"] = list(PROVIDERS)
        rows = (await session.execute(text(sql), params)).mappings().all()
        options[key] = [str(row["v"]) for row in rows if row["v"] is not None]
        counts[key] = {str(row["v"]): int(row["n"]) for row in rows if row["v"] is not None}
    return options, counts


async def _run_stats(session: AsyncSession, *, tenant_id: int) -> dict[str, int | float]:
    """The stat-tile counts over the firm's root runs: in flight, resting on a provider, held for
    a decision, parked for a person, stopped in the last week, and completed in the last day —
    and the approvals seat's own: held for more than a day, held with nobody able to decide, the
    money sitting at a gate, and what was decided in the last week (gated children included)."""
    awaiting = _STATE_FILTER["awaiting_approval"]
    row = (
        (
            await session.execute(
                text(
                    "SELECT "  # noqa: S608
                    f"  count(*) FILTER (WHERE {_STATE_FILTER['running']}) AS running, "
                    f"  count(*) FILTER (WHERE {_STATE_FILTER['waiting']}) AS waiting, "
                    f"  count(*) FILTER (WHERE {awaiting}) AS awaiting_approval, "
                    f"  count(*) FILTER (WHERE {_STATE_FILTER['needs_attention']}) "
                    "     AS needs_attention, "
                    f"  count(*) FILTER (WHERE {_STATE_FILTER['failed']} "
                    "     AND r.ended_at > now() - interval '7 days') AS failed_7d, "
                    "  count(*) FILTER (WHERE r.state_type = 'completed' "
                    "    AND r.ended_at > now() - interval '24 hours') AS completed_24h, "
                    f"  count(*) FILTER (WHERE {awaiting} "
                    "    AND held.requested_at < now() - interval '1 day') AS held_over_day, "
                    f"  count(*) FILTER (WHERE {awaiting} AND ({_eligible_sql()}) = 0) "
                    "     AS held_undecidable, "
                    f"  COALESCE(sum(({_HELD_AMOUNT_SQL})) FILTER (WHERE {awaiting}), 0) "
                    "     AS money_held, "
                    "  (SELECT count(*) FROM baton.runs d WHERE d.tenant_id = :t "
                    "     AND d.kind <> 'gate' AND d.decided_at > now() - interval '7 days') "
                    "     AS decided_7d, "
                    "  (SELECT count(*) FROM baton.runs d WHERE d.tenant_id = :t "
                    "     AND d.kind <> 'gate' AND d.decision = 'approved' "
                    "     AND d.decided_at > now() - interval '7 days') AS approved_7d, "
                    "  (SELECT count(*) FROM baton.runs d WHERE d.tenant_id = :t "
                    "     AND d.kind <> 'gate' AND d.decision = 'rejected' "
                    "     AND d.decided_at > now() - interval '7 days') AS rejected_7d "
                    f"FROM baton.runs r {_HELD_JOIN} "
                    "WHERE r.tenant_id = :t AND r.parent_run_id IS NULL"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one()
    )
    out: dict[str, int | float] = {k: int(v or 0) for k, v in row.items() if k != "money_held"}
    out["money_held"] = float(row["money_held"] or 0)
    return out


# ---- the approvals queue -----------------------------------------------------------------


def _approvals_eligible_sql() -> str:
    from app.actions.approvals import eligible_sql

    return eligible_sql("r.approver_role_ids", "r.requested_by")


# The held runs the approvals queue decides on, over the columns the card reads — ONE queue for
# everything awaiting approval: book reviews (meta.review, e.g. a vulnerability or bank change),
# money runs, and gated composite children (a gated sub-action holds in place). `is_review` tells
# the UI which decide path to use (a review goes through /reviews so its lifecycle events fire).
# The state_name guard excludes a composite merely paused on its children, and kind<>'gate' drops
# the gate rows. Approver roles are resolved to names, and can_decide mirrors the engine's own gate
# check (not the requester, holds an approver role) so the buttons show only to someone who could
# actually decide.
_APPROVALS_SQL = f"""
SELECT r.uid, r.action_key, r.case_no, r.subject_kind, r.subject_uid, r.requested_at,
       r.justification, (r.meta->>'review' = 'true') AS is_review,
       COALESCE(pu.name, pu.email) AS requested_by_name,
       c.uid AS contact_uid,
       COALESCE(c.name, NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), c.email)
         AS contact_name,
       acc.custodian::text AS provider,
       COALESCE(
         (SELECT array_agg(ro.name ORDER BY ro.name) FROM auth.roles ro
           WHERE ro.id = ANY(r.approver_role_ids)), ARRAY[]::text[]) AS approver_roles,
       ((r.requested_by IS NULL OR r.requested_by <> CAST(:me AS uuid))
         AND (cardinality(r.approver_role_ids) = 0
              OR r.approver_role_ids && CAST(:roles AS int[]))) AS can_decide,
       ({_approvals_eligible_sql()}) AS eligible_approvers
FROM baton.runs r"""  # noqa: S608 - the interpolation is the module's own SQL constant
_APPROVALS_SQL += """
  LEFT JOIN auth.users pu ON pu.id = r.requested_by
  LEFT JOIN crm.contacts c ON c.id = r.contact_id
  LEFT JOIN platform.accounts acc
    ON acc.tenant_id = r.tenant_id AND r.subject_kind = 'account' AND acc.uid = r.subject_uid
WHERE r.tenant_id = :t AND r.state_type = 'paused' AND r.state_name = 'AwaitingApproval'
  AND r.kind <> 'gate'
ORDER BY r.requested_at DESC
LIMIT :lim
"""


async def list_pending_approvals(
    session: AsyncSession,
    *,
    tenant_id: int,
    caller_id: str | None,
    caller_role_ids: list[int],
    limit: int = 50,
) -> list[dict[str, Any]]:
    """The approvals queue: everything held at an approval gate — book reviews and action runs
    alike — each with the action name, the client and subject, who raised it and why, the roles
    that may approve it, whether the caller may decide, and ``is_review`` (which decide path)."""
    from app.actions.catalogue import BY_KEY
    from app.reviews.catalogue import get as review_get

    rows = (
        (
            await session.execute(
                text(_APPROVALS_SQL),
                {"t": tenant_id, "me": caller_id, "roles": caller_role_ids, "lim": limit},
            )
        )
        .mappings()
        .all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        key = str(row["action_key"])
        is_review = bool(row["is_review"])
        # a review's label lives in the reviews catalogue; an action's name in the actions catalogue
        if is_review and (rspec := review_get(key)) is not None:
            name = rspec.label
        elif (spec := BY_KEY.get(key)) is not None:
            name = spec.name
        else:
            name = key
        out.append(
            {
                "uid": row["uid"],
                "action_key": key,
                "name": name,
                "is_review": is_review,
                "case_no": row["case_no"],
                "subject_kind": row["subject_kind"],
                "subject_uid": row["subject_uid"],
                "contact_uid": row["contact_uid"],
                "contact_name": row["contact_name"],
                "requested_by_name": row["requested_by_name"],
                "requested_at": _stamp(row["requested_at"]),
                "justification": row["justification"],
                "provider": row["provider"],
                "approver_roles": list(row["approver_roles"] or []),
                "can_decide": bool(row["can_decide"]),
                "eligible_approvers": (
                    int(row["eligible_approvers"])
                    if row["eligible_approvers"] is not None
                    else None
                ),
            }
        )
    return out
