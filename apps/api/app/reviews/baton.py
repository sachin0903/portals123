"""The four-eye reviews on baton (Phase 3, part 2) — the sole engine for held reviews.

When the flag is on, a held review is a baton run: ``hold`` starts it with a run-level approval
gate (the reviewer roles), ``approve``/``reject``/``cancel`` go through the engine, and the run is
projected back into the shape the ``/reviews`` surface already reads, so the book UI is unchanged.
The capture/apply/conflict logic and the audit lifecycle events are the live ones
(``app/reviews/apply.py``, ``app/reviews/events.py``), so a held change and an immediate write do
not drift. The legacy engine (``actions.runs``) stays the path when the flag is off, and old
held rows there are still listed and decidable alongside new baton ones.

The Reviews queue (``list_requests`` / ``get_request``) is the firm's WHOLE approval queue, not
only the book reviews: every root run that was held for a second person — a bank-details change,
a payment, a transfer, an account opening — has a case number, and the reviewer sees them in one
place. A book review keeps its now-vs-proposed body; a provider action carries the input the
door recorded (``meta.input``) so the reviewer can read what was asked. The record-level pending
strips (``pending_for_subject`` / ``pending_for_contact``) stay book-review only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from baton import Approval, ApprovalError, NotFound, Subject, SubjectBusy
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.reviews.service import HoldResult, ReviewError

from app.actions.baton_engine import get_engine
from app.reviews.catalogue import WIRED_BY_KEY
from app.reviews.events import record_review_action

_WIRED_KEYS = tuple(WIRED_BY_KEY)


def enabled() -> bool:
    """The reviews fold is live — baton is the only engine; the book actions are registered."""
    engine = get_engine()
    return all(engine.registry.get(key) is not None for key in _WIRED_KEYS)


async def owns(session: AsyncSession, *, tenant_id: int, uid: str) -> bool:
    """Whether this review uid is a baton review run (vs a legacy ``actions.runs`` row)."""
    found = (
        await session.execute(
            text(
                "SELECT 1 FROM baton.runs WHERE uid = :u AND tenant_id = :t "
                "  AND parent_run_id IS NULL AND action_key = ANY(:keys)"
            ),
            {"u": uid, "t": tenant_id, "keys": list(_WIRED_KEYS)},
        )
    ).first()
    return found is not None


# The review-row projection: baton.runs mapped onto the columns app/reviews/service.py::_out reads.
# It mirrors the legacy _SELECT join-for-join, with the baton state model (state_type/state_name →
# status, the approver_role_ids array column, params->payload/before). A book review's command
# sits under params.payload / params.before; a provider action's params are the call itself
# (account_id, instruction_uid, …), so the account joins read both shapes, and `input` is the
# readable snapshot the door recorded (what the run detail's Input panel shows).
# The review's status word, from baton's state. Only a run resting AT ITS GATE is pending: once
# approved, a composite provider action runs its children (Scheduled, Running, paused
# AwaitingChildren) and a leaf book review is applied by the worker — all "approved" here; the
# run's own progress is Actions' concern. A failed APPLY on a book review is a conflict (the
# snapshot went stale); a provider action the custodian refused was still approved by a person,
# and its refusal is parked NeedsAttention in Actions, not a review state.
_STATUS_CASE = """CASE
         WHEN r.state_type = 'paused' AND r.state_name = 'AwaitingApproval' THEN 'pending'
         WHEN r.state_type = 'cancelled' AND r.state_name = 'Rejected' THEN 'rejected'
         WHEN r.state_type = 'cancelled' THEN 'cancelled'
         WHEN r.decision = 'approved'
              AND (r.state_type NOT IN ('failed', 'crashed')
                   OR (r.meta->>'review') IS DISTINCT FROM 'true') THEN 'approved'
         ELSE 'conflicted'
       END"""

# Resting at the gate: what the inbox, the record strips and the clash check mean by "waiting".
_WAITING = "(r.state_type = 'paused' AND r.state_name = 'AwaitingApproval')"


def _eligible() -> str:
    """How many people other than the proposer could decide this case — NULL when the gate names
    no role. The same count the approvals listing reads, so the two seats agree on "nobody"."""
    from app.actions.approvals import eligible_sql

    return eligible_sql("r.approver_role_ids", "r.requested_by")


_ELIGIBLE_SQL = _eligible()


def _select_sql() -> str:
    """The review-row projection, built once at import from this module's own constants."""
    return (
        """
SELECT r.id, r.uid, r.case_no, r.action_key, r.meta->>'event_type' AS event_type,
       r.subject_kind AS subject_type, r.subject_uid AS subject_id, r.contact_id,
       r.params->'payload' AS payload, r.params->'before' AS before, r.justification,
       r.meta->'input' AS input,
       r.approver_role_ids[1] AS reviewer_role_id,
       rr.slug AS reviewer_role_slug, rr.name AS reviewer_role_name,
       r.requested_by::text AS proposed_by,
       COALESCE(pu.name, pu.email) AS proposed_by_name,
       r.requested_at AS proposed_at,
       """  # noqa: S608 - the interpolations are this module's own SQL constants
        + _STATUS_CASE
        + """ AS status,
       r.decided_by,
       COALESCE(du.name, du.email) AS decided_by_name,
       r.decided_at, r.decision_note,
       CASE WHEN r.state_type = 'completed' THEN r.ended_at END AS applied_at,
       r.created_at, r.updated_at,
       COALESCE(r.approver_role_ids, ARRAY[]::int[]) AS approver_role_ids,
       COALESCE(
         (SELECT array_agg(ro.name ORDER BY ro.name) FROM auth.roles ro
           WHERE ro.id = ANY(r.approver_role_ids)), ARRAY[]::text[]) AS approver_roles,
       ("""
        + _ELIGIBLE_SQL
        + """) AS eligible_approvers,
       r.parent_run_id IS NOT NULL AS is_child,
       c.uid AS contact_uid,
       COALESCE(c.name, NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), c.email)
         AS contact_name,
       COALESCE(acc.uid, acc_i.uid) AS account_uid,
       COALESCE(
         NULLIF(acc.name, ''), p.name, p.product_type::text, acc.uid,
         NULLIF(acc_i.name, ''), p_i.name, p_i.product_type::text, acc_i.uid
       ) AS account_name
FROM baton.runs r
  LEFT JOIN auth.roles rr ON rr.id = r.approver_role_ids[1]
  LEFT JOIN auth.users pu ON pu.id = r.requested_by
  LEFT JOIN auth.users du ON du.id = r.decided_by
  LEFT JOIN crm.contacts c ON c.id = r.contact_id
  LEFT JOIN platform.accounts acc ON acc.tenant_id = r.tenant_id AND (
    (r.subject_kind = 'account' AND acc.uid = r.subject_uid)
    OR (
      COALESCE(r.params->'payload'->>'account_id', r.params->>'account_id') ~ '^[0-9]+$'
      AND acc.id = COALESCE(r.params->'payload'->>'account_id', r.params->>'account_id')::int
    )
  )
  LEFT JOIN platform.products p ON p.id = acc.product_id
  LEFT JOIN platform.instructions inst ON inst.tenant_id = r.tenant_id AND (
    inst.uid = r.subject_uid
    OR inst.uid = COALESCE(r.params->'payload'->>'instruction_uid', r.params->>'instruction_uid')
  )
  LEFT JOIN platform.accounts acc_i
    ON acc_i.id = inst.account_id AND acc_i.tenant_id = r.tenant_id
  LEFT JOIN platform.products p_i ON p_i.id = acc_i.product_id
WHERE (r.parent_run_id IS NULL OR r.case_no IS NOT NULL)
  -- Roots, and the gated CHILD of a composite (a firm that gates account opening holds
  -- onboarding's `account.create` child, which has its own case number and is what a person
  -- decides). A gate row itself has no case number and never lists.
  AND r.kind <> 'gate'
  -- A manual hold belongs only in the firm's Actions → Approvals area, not the Reviews inbox:
  -- it is an operational "do this by hand then accept", not a record-change review.
  AND (r.meta->>'manual') IS DISTINCT FROM 'true'
"""
    )


_SELECT = _select_sql()

# The queue is every run that was HELD for a second person. The engine numbers a run only when it
# starts behind an approval gate, so `case_no` is that fact — a payment that went straight out has
# none and is not a review; the same payment held by an approver role is RV-n like a bank change.
_HELD = " AND r.case_no IS NOT NULL"

# The record strips show the book reviews only — the held commands whose body the record card
# already knows how to draw (bank, model, instruction, schedule).
_BOOK_ONLY = " AND r.action_key = ANY(:keys)"


def _out(row: Any, *, caller_id: str | None, caller_role_ids: list[int] | None) -> dict[str, Any]:
    from app.reviews.service import _out as project

    return project(row, caller_id=caller_id, caller_role_ids=caller_role_ids)


async def _approvers(session: AsyncSession, *, tenant_id: int, action_key: str) -> list[int]:
    """The reviewer roles the firm set on this action — the same query the legacy hold uses."""
    from app.reviews.service import _policy_keys

    roles = [
        int(role)
        for role in (
            await session.execute(
                text(
                    "SELECT g.role_id FROM actions.policies p "
                    "JOIN actions.policy_roles g ON g.policy_id = p.id AND g.kind = 'approve' "
                    "WHERE p.tenant_id = :t AND p.action_key = ANY(CAST(:keys AS text[])) "
                    "  AND p.provider IS NULL AND p.approval_enabled"
                ),
                {"t": tenant_id, "keys": list(_policy_keys(action_key))},
            )
        ).scalars()
    ]
    return roles


async def _notify_channels(
    session: AsyncSession, *, tenant_id: int, action_key: str
) -> tuple[bool, bool]:
    """The channels (in-app, email) this action's approval notice uses, from its policy. Defaults
    to in-app only when the firm has set no policy row — the whole point of the preference."""
    from app.reviews.service import _policy_keys

    row = (
        (
            await session.execute(
                text(
                    "SELECT notify_in_app, notify_email FROM actions.policies "
                    "WHERE tenant_id = :t AND action_key = ANY(CAST(:keys AS text[])) "
                    "  AND provider IS NULL "
                    "ORDER BY action_key LIMIT 1"
                ),
                {"t": tenant_id, "keys": list(_policy_keys(action_key))},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return True, False
    return bool(row["notify_in_app"]), bool(row["notify_email"])


def _case_ref(case_no: Any) -> str:
    return f"RV-{int(case_no)}" if case_no is not None else "a case"


async def _open_holds(
    session: AsyncSession,
    *,
    tenant_id: int,
    action_key: str,
    subject_type: str,
    subject_id: str,
) -> list[tuple[Any, dict[str, Any], bool, str]]:
    """The holds still IN FLIGHT on this subject for this action — waiting at the gate, or
    approved and not yet landed by the worker — as (case_no, payload, waiting, uid), oldest
    first. What a refusal names, what the per-field check reads, and what a second edit AMENDS:
    an edit that overlaps an approved one would only park Conflicted once it lands."""
    # _WAITING is a module constant; every value is a bound parameter.
    sql = (
        f"SELECT r.case_no, r.uid, r.params->'payload' AS payload, {_WAITING} AS waiting "  # noqa: S608
        "FROM baton.runs r "
        "WHERE r.tenant_id = :t AND r.parent_run_id IS NULL AND r.action_key = :k "
        "  AND r.subject_kind = :st AND r.subject_uid = :sid "
        f"  AND ({_WAITING} OR (r.decision = 'approved' "
        "       AND r.state_type IN ('scheduled', 'running', 'pending', 'paused'))) "
        "ORDER BY r.requested_at"
    )
    rows = (
        await session.execute(
            text(sql),
            {"t": tenant_id, "k": action_key, "st": subject_type, "sid": subject_id},
        )
    ).mappings()
    out: list[tuple[Any, dict[str, Any], bool, str]] = []
    for row in rows:
        raw = row["payload"]
        out.append(
            (
                row["case_no"],
                raw if isinstance(raw, dict) else {},
                bool(row["waiting"]),
                str(row["uid"]),
            )
        )
    return out


def _in_the_way(case_no: Any, waiting: bool) -> str:
    ref = _case_ref(case_no)
    if waiting:
        return f"already waiting for review ({ref}). Approve, reject or cancel that first."
    return f"already approved and being applied ({ref}). Let that land first."


def _fields_of(payload: dict[str, Any]) -> set[str]:
    raw = payload.get("fields")
    return set(raw.keys()) if isinstance(raw, dict) else set()


def _amendable(
    spec: Any, payload: dict[str, Any], in_flight: list[tuple[Any, dict[str, Any], bool, str]]
) -> tuple[Any, dict[str, Any], bool, str] | None:
    """The in-flight hold a new proposal lands on: for a one-open key the open hold on the
    subject; for client details (several holds, each on its own fields) the first one that
    touches a field this edit touches. None means the edit opens a case of its own."""
    if spec.one_open:
        return in_flight[0] if in_flight else None
    proposed = _fields_of(payload)
    for item in in_flight:
        if proposed & _fields_of(item[1]):
            return item
    return None


def _merge_payload(
    spec: Any, open_payload: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """What the case proposes once this edit lands on it. A details hold keeps every field it
    already carried and takes the new values over it (an edit to the tax card joins the case,
    the earlier edit to the contact card stays proposed); any other hold is replaced whole — a
    second bank account IS the proposal now."""
    if not spec.one_open:
        fields = {**(open_payload.get("fields") or {}), **(payload.get("fields") or {})}
        return {**open_payload, **payload, "fields": fields}
    return payload


async def _amend(
    session: AsyncSession,
    *,
    tenant_id: int,
    spec: Any,
    run_uid: str,
    case_no: Any,
    open_payload: dict[str, Any],
    payload: dict[str, Any],
    justification: str | None,
    subject_type: str,
    subject_id: str,
    contact_id: int | None,
    actor: str,
    workos_user_id: str,
) -> HoldResult:
    """A second edit while a case is still waiting UPDATES the case instead of being refused:
    the proposal is merged (or replaced), the snapshot it is compared against is taken again
    now, the approvers are told the case changed, and the door answers the same case number.
    Only a case still at its gate can take an edit; one already approved and landing cannot
    (the worker is applying what the second person agreed to), and that is still a 409."""
    import json

    from app.actions import approvals
    from app.reviews.apply import handler
    from app.reviews.service import HoldResult, ReviewError, _lifecycle_subject

    merged = _merge_payload(spec, open_payload, payload)
    before = await handler(spec.key).capture_before(session, tenant_id=tenant_id, payload=merged)
    row = (
        (
            await session.execute(
                text(
                    "UPDATE baton.runs AS r SET params = CAST(:p AS jsonb), "  # noqa: S608
                    "  justification = COALESCE(CAST(:j AS text), r.justification), "
                    "  meta = r.meta || jsonb_build_object("
                    "    'amended_at', now(), 'amended_by', CAST(:a AS text)), "
                    "  updated_at = now() "
                    f"WHERE r.uid = :u AND r.tenant_id = :t AND {_WAITING} "
                    "RETURNING r.case_no, r.approver_role_ids, "
                    "          r.requested_by::text AS requested_by"
                ),
                {
                    "p": json.dumps({"payload": merged, "before": before}, default=str),
                    "j": (justification or "").strip() or None,
                    "a": actor,
                    "u": run_uid,
                    "t": tenant_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        # Moved on (or out of this reader's reach) between the read and the write.
        raise ReviewError(f"A change like this is {_in_the_way(case_no, True)}", status=409)
    slugs = await approvals.role_slugs(session, [int(r) for r in (row["approver_role_ids"] or [])])
    notify_in_app, notify_email = await _notify_channels(
        session, tenant_id=tenant_id, action_key=spec.key
    )
    await record_review_action(
        session,
        event_type="review.requested",
        tenant_id=tenant_id,
        actor_id=actor,
        actor_sub=workos_user_id,
        subject=_lifecycle_subject(
            action_key=spec.key,
            review_uid=run_uid,
            subject_type=subject_type,
            subject_id=subject_id,
            contact_id=contact_id,
            extra={
                "reviewer_role_slug": slugs[0] if slugs else None,
                "reviewer_role_slugs": slugs or None,
                "notify_in_app": notify_in_app,
                "notify_email": notify_email,
                "proposed_by": row["requested_by"],
                "amended": True,
            },
        ),
    )
    return HoldResult(
        held=True,
        review_uid=run_uid,
        body={
            "status": "pending_review",
            "review_uid": run_uid,
            "case_no": row["case_no"],
            "amended": True,
        },
    )


async def hold(
    session: AsyncSession,
    *,
    tenant_id: int,
    spec: Any,
    policy: dict[str, Any],
    subject_type: str,
    subject_id: str,
    contact_id: int | None,
    payload: dict[str, Any],
    before: Any,
    justification: str | None,
    idempotency_key: str,
    actor: str,
    workos_user_id: str,
) -> HoldResult:
    """Start the held review as a baton run, record ``review.requested``; returns a HoldResult."""
    from app.reviews.service import HoldResult, ReviewError, _lifecycle_subject

    engine = get_engine()
    defn = engine.registry.require(spec.key)
    approvers = await _approvers(session, tenant_id=tenant_id, action_key=spec.key)
    if not approvers:
        approvers = [int(policy["reviewer_role_id"])]
    # The same proposal posted twice (a retry, a double click) is the same run — engine.start
    # replays it by idempotency key — so the checks below are for a DIFFERENT proposal only.
    unit = engine.store.bind(session)
    replay = await engine.store.run_by_key(unit, tenant_id, idempotency_key)
    in_flight = (
        []
        if replay is not None
        else await _open_holds(
            session,
            tenant_id=tenant_id,
            action_key=spec.key,
            subject_type=subject_type,
            subject_id=subject_id,
        )
    )
    if in_flight:
        # A case already open on this subject (for client details: on a field this edit touches)
        # TAKES the edit while it is still waiting — the proposal is amended and the approvers
        # told — rather than refusing it. One already approved and landing cannot, and says so.
        target = _amendable(spec, payload, in_flight)
        if target is not None:
            open_case, open_payload, waiting, open_uid = target
            if not waiting:
                clash = sorted(_fields_of(payload) & _fields_of(open_payload))
                names = ", ".join(field.replace("_", " ") for field in clash)
                lead = f"{names} " if names else "A change like this is "
                raise ReviewError(f"{lead}{_in_the_way(open_case, False)}", status=409)
            return await _amend(
                session,
                tenant_id=tenant_id,
                spec=spec,
                run_uid=open_uid,
                case_no=open_case,
                open_payload=open_payload,
                payload=payload,
                justification=justification,
                subject_type=subject_type,
                subject_id=subject_id,
                contact_id=contact_id,
                actor=actor,
                workos_user_id=workos_user_id,
            )
    try:
        result = await engine.start(
            unit,
            defn,
            tenant_id=tenant_id,
            params={"payload": payload, "before": before},
            idempotency_key=idempotency_key,
            requested_by=actor,
            contact_id=contact_id,
            subjects=[Subject(subject_type, subject_id, None)],
            approval=Approval(roles=approvers),
            justification=justification,
            meta={"event_type": spec.event_type, "review": True},
        )
    except SubjectBusy as exc:
        # One open hold per subject for this action: say which case and where it is, so the
        # adviser can find it on the record (or in Reviews) rather than guess.
        if in_flight:
            open_case, _payload, waiting, _uid = in_flight[0]
            raise ReviewError(
                f"A change like this is {_in_the_way(open_case, waiting)}", status=409
            ) from exc
        raise ReviewError(
            "A change like this is already in flight. Let it land first.", status=409
        ) from exc
    run = result.run
    if result.created:
        role_slug = policy.get("reviewer_role_slug")
        notify_in_app, notify_email = await _notify_channels(
            session, tenant_id=tenant_id, action_key=spec.key
        )
        await record_review_action(
            session,
            event_type="review.requested",
            tenant_id=tenant_id,
            actor_id=actor,
            actor_sub=workos_user_id,
            subject=_lifecycle_subject(
                action_key=spec.key,
                review_uid=run.uid,
                subject_type=subject_type,
                subject_id=subject_id,
                contact_id=contact_id,
                # The policy's channels ride the event; the consumers honour them (in-app and/or
                # email), so one static trigger covers every action with its firm's own choice.
                extra={
                    "reviewer_role_slug": role_slug,
                    "notify_in_app": notify_in_app,
                    "notify_email": notify_email,
                },
            ),
        )
    return HoldResult(
        held=True,
        review_uid=run.uid,
        body={"status": "pending_review", "review_uid": run.uid, "case_no": run.case_no},
    )


async def get_request(
    session: AsyncSession,
    *,
    uid: str,
    caller_id: str | None = None,
    caller_role_ids: list[int] | None = None,
) -> dict[str, Any] | None:
    row = (
        (await session.execute(text(_SELECT + _HELD + " AND r.uid = :u"), {"u": uid}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return _out(row, caller_id=caller_id, caller_role_ids=caller_role_ids)


async def list_requests(
    session: AsyncSession,
    *,
    tenant_id: int,
    scope: str,
    caller_id: str | None,
    caller_role_ids: list[int],
    status: str | None = None,
    action_key: str | None = None,
    contact_uid: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses = ["r.tenant_id = :t"]
    params: dict[str, Any] = {"t": tenant_id, "lim": limit}
    # The projected status word is what the caller filters on, so compare against the same fold.
    if scope == "inbox":
        clauses.append(_WAITING)
    elif status and status != "all":
        clauses.append(f"{_STATUS_CASE} = :st")
        params["st"] = status
    if action_key:
        clauses.append("r.action_key = :k")
        params["k"] = action_key
    if contact_uid:
        clauses.append("c.uid = :cu")
        params["cu"] = contact_uid
    if scope == "mine" and caller_id:
        clauses.append("r.requested_by = CAST(:me AS uuid)")
        params["me"] = caller_id
    elif scope == "inbox" and caller_id:
        clauses.append(
            "(r.requested_by IS NULL OR r.requested_by <> CAST(:me AS uuid)) "
            "AND r.approver_role_ids && CAST(:roles AS int[])"
        )
        params["me"] = caller_id
        params["roles"] = caller_role_ids
    where = " AND ".join(clauses)
    # Waiting rows first: the page is the queue, and a held provider action settled last week
    # must not push a bank change still waiting off the end of the page.
    sql = f"{_SELECT}{_HELD} AND {where} ORDER BY {_WAITING} DESC, r.requested_at DESC LIMIT :lim"
    rows = (await session.execute(text(sql), params)).mappings()
    return [_out(r, caller_id=caller_id, caller_role_ids=caller_role_ids) for r in rows]


def _translate(exc: ApprovalError) -> ReviewError:
    from app.reviews.service import ReviewError

    return ReviewError(str(exc), status=exc.status)


async def approve(
    session: AsyncSession,
    *,
    tenant_id: int,
    uid: str,
    workos_user_id: str,
    note: str | None,
    role_ids: list[int],
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Approve a held baton review: release the gate and record ``review.approved``.

    The apply runs on the worker (the engine claims the now-scheduled run), so approve returns the
    review as ``approved`` straight away and the change lands a moment later — the same split the
    money and journey doors use. A stale record parks the run; the inbox shows it ``conflicted`` on
    the next read. The applied happened-event (``client.bank_account_updated`` …) is written by the
    terminal hook when the apply actually commits, so automations do not fire before the change
    lands."""
    from app.reviews.service import (
        ReviewError,
        _lifecycle_subject_from_row,
        _resolve_actor,
    )

    # A provider action held at its gate is also listed by /reviews, but it is decided through
    # its run (`POST /actions/runs/{uid}/decision`), which the portals route to. This path is the
    # book reviews' own lifecycle (apply, happened-event, review.* events).
    if not await owns(session, tenant_id=tenant_id, uid=uid):
        raise ReviewError("Review not found.", status=404)
    engine = get_engine()
    actor, actor_sub = await _resolve_actor(
        session, workos_user_id=workos_user_id, actor_user_id=actor_user_id
    )
    found = await get_request(session, uid=uid, caller_id=actor, caller_role_ids=role_ids)
    if found is None:
        raise ReviewError("Review not found.", status=404)
    if found["status"] == "approved":
        return found
    if found["status"] != "pending":
        raise ReviewError("This review is no longer waiting.", status=409)

    try:
        await engine.decide(
            engine.store.bind(session),
            uid,
            decision="approve",
            actor_id=actor,
            role_ids=role_ids,
            note=note,
        )
    except ApprovalError as exc:
        raise _translate(exc) from exc
    except NotFound as exc:
        raise ReviewError("Review not found.", status=404) from exc

    row = await _row(session, uid=uid)
    if row is None:
        raise ReviewError("Review not found.", status=404)
    await record_review_action(
        session,
        event_type="review.approved",
        tenant_id=tenant_id,
        actor_id=actor,
        actor_sub=actor_sub,
        subject=_lifecycle_subject_from_row(row, extra={"proposed_by": str(row["proposed_by"])}),
    )
    out = await get_request(session, uid=uid, caller_id=actor, caller_role_ids=role_ids)
    if out is None:
        raise ReviewError("Review not found.", status=404)
    return out


async def reject(
    session: AsyncSession,
    *,
    tenant_id: int,
    uid: str,
    workos_user_id: str,
    note: str,
    role_ids: list[int],
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    from app.reviews.service import ReviewError, _lifecycle_subject_from_row, _resolve_actor

    # A provider action held at its gate is also listed by /reviews, but it is decided through
    # its run (`POST /actions/runs/{uid}/decision`), which the portals route to. This path is the
    # book reviews' own lifecycle (apply, happened-event, review.* events).
    if not await owns(session, tenant_id=tenant_id, uid=uid):
        raise ReviewError("Review not found.", status=404)
    engine = get_engine()
    actor, actor_sub = await _resolve_actor(
        session, workos_user_id=workos_user_id, actor_user_id=actor_user_id
    )
    row = await _row(session, uid=uid)
    if row is None:
        raise ReviewError("Review not found.", status=404)
    if row["status"] != "pending":
        raise ReviewError("This review is no longer waiting.", status=409)
    try:
        await engine.decide(
            engine.store.bind(session),
            uid,
            decision="reject",
            actor_id=actor,
            role_ids=role_ids,
            note=note,
        )
    except ApprovalError as exc:
        raise _translate(exc) from exc
    except NotFound as exc:
        raise ReviewError("Review not found.", status=404) from exc
    await record_review_action(
        session,
        event_type="review.rejected",
        tenant_id=tenant_id,
        actor_id=actor,
        actor_sub=actor_sub,
        subject=_lifecycle_subject_from_row(row, extra={"proposed_by": str(row["proposed_by"])}),
    )
    out = await get_request(session, uid=uid, caller_id=actor, caller_role_ids=role_ids)
    if out is None:
        raise ReviewError("Review not found.", status=404)
    return out


async def cancel(
    session: AsyncSession,
    *,
    tenant_id: int,
    uid: str,
    workos_user_id: str,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    from app.reviews.service import ReviewError, _lifecycle_subject_from_row, _resolve_actor

    # A provider action held at its gate is also listed by /reviews, but it is decided through
    # its run (`POST /actions/runs/{uid}/decision`), which the portals route to. This path is the
    # book reviews' own lifecycle (apply, happened-event, review.* events).
    if not await owns(session, tenant_id=tenant_id, uid=uid):
        raise ReviewError("Review not found.", status=404)
    engine = get_engine()
    actor, actor_sub = await _resolve_actor(
        session, workos_user_id=workos_user_id, actor_user_id=actor_user_id
    )
    row = await _row(session, uid=uid)
    if row is None:
        raise ReviewError("Review not found.", status=404)
    if row["status"] != "pending":
        raise ReviewError("This review is no longer waiting.", status=409)
    # Only the person who raised the change may withdraw it — a second person acts on it by
    # approving or rejecting (with a reason), never by cancelling. (The legacy service enforced
    # this; the fold dropped it, letting any book-writer cancel someone else's pending review.)
    if row["proposed_by"] is not None and str(row["proposed_by"]) != str(actor):
        raise ReviewError("Only the person who submitted this can cancel it.", status=403)
    await engine.cancel(engine.store.bind(session), uid, actor_id=actor, note=None)
    await record_review_action(
        session,
        event_type="review.cancelled",
        tenant_id=tenant_id,
        actor_id=actor,
        actor_sub=actor_sub,
        subject=_lifecycle_subject_from_row(row, extra={"proposed_by": str(row["proposed_by"])}),
    )
    out = await get_request(session, uid=uid, caller_id=actor, caller_role_ids=role_ids_of(row))
    if out is None:
        raise ReviewError("Review not found.", status=404)
    return out


def role_ids_of(row: dict[str, Any]) -> list[int]:
    return [int(r) for r in (row.get("approver_role_ids") or [])]


async def _row(session: AsyncSession, *, uid: str) -> dict[str, Any] | None:
    """The raw projected row (pre ``_out``), for the lifecycle-subject helpers and status checks."""
    found = (
        (
            await session.execute(
                text(_SELECT + _BOOK_ONLY + " AND r.uid = :u"),
                {"u": uid, "keys": list(_WIRED_KEYS)},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(found) if found is not None else None


async def pending_for_subject(
    session: AsyncSession,
    *,
    tenant_id: int,
    subject_type: str,
    subject_id: str,
    caller_id: str | None = None,
    caller_role_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Held baton reviews that target this subject (an account record also wants the raises keyed
    on its id and the cancels joined through the instruction)."""
    targeted = " OR COALESCE(acc.uid, acc_i.uid) = :sid" if subject_type == "account" else ""
    rows = (
        await session.execute(
            text(
                _SELECT + _BOOK_ONLY + " AND r.tenant_id = :t AND " + _WAITING + " AND ("
                "(r.subject_kind = :st AND r.subject_uid = :sid)" + targeted + ") "
                "ORDER BY r.requested_at DESC"
            ),
            {"t": tenant_id, "st": subject_type, "sid": subject_id, "keys": list(_WIRED_KEYS)},
        )
    ).mappings()
    return [_out(r, caller_id=caller_id, caller_role_ids=caller_role_ids) for r in rows]


async def pending_for_contact(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    caller_id: str | None = None,
    caller_role_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Held baton reviews for this client."""
    rows = (
        await session.execute(
            text(
                _SELECT + _BOOK_ONLY + " AND r.tenant_id = :t AND r.contact_id = :c "
                "AND " + _WAITING + " ORDER BY r.requested_at DESC"
            ),
            {"t": tenant_id, "c": contact_id, "keys": list(_WIRED_KEYS)},
        )
    ).mappings()
    return [_out(r, caller_id=caller_id, caller_role_ids=caller_role_ids) for r in rows]
