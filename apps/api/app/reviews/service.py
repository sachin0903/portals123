"""Policy, propose, and decide — the one primitive every door and the review API call.

Propose never applies. Approve applies once (``applied_at``), then a second approve is a
no-op. Self-approval is refused even when the proposer holds the reviewer role.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.catalogue import get as domain_action
from app.actions.redact import firm_visible
from app.reviews import catalogue
from app.reviews.apply import handler

# Changing the bank details on the record and sending that bank account to the custodian
# are one act. Approvers on either action hold the save. The settings row people see is
# "Update client bank account" (`client.bank`); the door still records the book key.
_SAME_ACT: dict[str, str] = {
    "contact.bank_details_changed": "client.bank",
    "account.bank_details_changed": "account.bank",
    # The review key is the one the door holds on; the paired key is the one the Actions Policies
    # tab (the baton catalogue) writes, so a firm setting approval there drives the review.
    "client.vulnerability_updated": "client.vulnerability",
    "client.details_changed": "client.details",
    # Changing a model / the fee packages is one act with pushing it to the custodian: the
    # settings row is "Change model" (`model.attach`) / "Change fee packages" (`fee.attach`),
    # approvers there hold the record save, and the push follows the approval.
    "account.model_changed": "model.attach",
    "account.fee_packages_changed": "fee.attach",
}

MODES = ("off", "four_eye")
OPEN_STATUSES = ("pending",)
DECISION_STATUSES = ("approved", "rejected", "cancelled", "conflicted")
# The queue speaks pending/approved. The run stores awaiting_approval/succeeded.


def _lifecycle_subject(
    *,
    action_key: str,
    review_uid: str,
    subject_type: str,
    subject_id: str,
    contact_id: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    subject: dict[str, Any] = {
        "action_key": action_key,
        "review_uid": review_uid,
        "subject_type": subject_type,
        "subject_id": subject_id,
    }
    if contact_id is not None:
        subject["contact_id"] = int(contact_id)
    if extra:
        subject.update({k: v for k, v in extra.items() if v is not None})
    return subject


def _lifecycle_subject_from_row(
    row: dict[str, Any], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    return _lifecycle_subject(
        action_key=str(row["action_key"]),
        review_uid=str(row["uid"]),
        subject_type=str(row["subject_type"]),
        subject_id=str(row["subject_id"]),
        contact_id=int(row["contact_id"]) if row.get("contact_id") is not None else None,
        extra=extra,
    )


class ReviewError(ValueError):
    """A validation failure the caller should surface as 4xx."""

    def __init__(self, detail: str, *, status: int = 422, field: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status
        self.field = field


@dataclass(frozen=True)
class HoldResult:
    held: bool
    review_uid: str | None = None
    body: dict[str, object] | None = None


async def _actor_uuid(session: AsyncSession, workos_user_id: str) -> str | None:
    row = (
        await session.execute(
            text(
                "SELECT id::text FROM auth.users WHERE workos_user_id = :w AND deleted_at IS NULL"
            ),
            {"w": workos_user_id},
        )
    ).scalar_one_or_none()
    return str(row) if row else None


async def _role_in_tenant(
    session: AsyncSession, tenant_id: int, role_id: int
) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text("SELECT id, slug, name FROM auth.roles WHERE id = :r AND tenant_id = :t"),
                {"r": role_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def _role_by_slug(session: AsyncSession, tenant_id: int, slug: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text("SELECT id, slug, name FROM auth.roles WHERE slug = :s AND tenant_id = :t"),
                {"s": slug, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def _holds_role(session: AsyncSession, *, tenant_id: int, user_id: str, role_id: int) -> bool:
    ok = (
        await session.execute(
            text(
                "SELECT 1 FROM auth.organisation_memberships m "
                "JOIN auth.membership_roles mr ON mr.membership_id = m.id "
                "WHERE m.user_id = CAST(:u AS uuid) AND m.tenant_id = :t "
                "  AND m.portal = 'platform' AND m.status = 'active' "
                "  AND mr.role_id = :r"
            ),
            {"u": user_id, "t": tenant_id, "r": role_id},
        )
    ).scalar_one_or_none()
    return ok is not None


async def eligible_reviewer_count(
    session: AsyncSession, *, tenant_id: int, role_id: int, exclude_user_id: str | None = None
) -> int:
    """Active platform members who hold the given role, other than the caller.

    Used by the enable preflight: there must be someone else who can actually decide.
    The role on the action is the grant. There is no separate approve permission.
    """
    row = (
        await session.execute(
            text(
                "SELECT count(DISTINCT u.id) FROM auth.users u "
                "JOIN auth.organisation_memberships m ON m.user_id = u.id "
                "JOIN auth.membership_roles mr ON mr.membership_id = m.id "
                "WHERE m.tenant_id = :t AND m.portal = 'platform' AND m.status = 'active' "
                "  AND u.enabled AND mr.role_id = :r "
                "  AND (CAST(:x AS uuid) IS NULL OR u.id <> CAST(:x AS uuid))"
            ),
            {"t": tenant_id, "r": role_id, "x": exclude_user_id},
        )
    ).scalar_one()
    return int(row or 0)


_POLICY_SQL = """
SELECT p.action_key,
       CASE WHEN p.approval_enabled AND (
              p.approval_role_id IS NOT NULL
              OR EXISTS (
                SELECT 1 FROM actions.policy_roles g
                WHERE g.policy_id = p.id AND g.kind = 'approve'
              )
            ) THEN 'four_eye' ELSE 'off' END AS mode,
       p.approval_role_id AS reviewer_role_id,
       p.require_justification,
       r.slug AS reviewer_role_slug, r.name AS reviewer_role_name
FROM actions.policies p
LEFT JOIN auth.roles r ON r.id = p.approval_role_id
"""


def _policy_keys(action_key: str) -> tuple[str, ...]:
    paired = _SAME_ACT.get(action_key)
    if paired is None:
        return (action_key,)
    return (action_key, paired)


async def _policy_row(
    session: AsyncSession, *, tenant_id: int, action_key: str
) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    _POLICY_SQL
                    + " WHERE p.tenant_id = :t AND p.action_key = :k AND p.provider IS NULL"
                ),
                {"t": tenant_id, "k": action_key},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def get_policy(
    session: AsyncSession, *, tenant_id: int, action_key: str
) -> dict[str, Any] | None:
    found: dict[str, Any] | None = None
    require = False
    for key in _policy_keys(action_key):
        row = await _policy_row(session, tenant_id=tenant_id, action_key=key)
        if row is None:
            continue
        require = require or bool(row.get("require_justification"))
        holding = row["mode"] == "four_eye" and row["reviewer_role_id"] is not None
        if found is None:
            found = row
        elif holding and (found["mode"] != "four_eye" or found["reviewer_role_id"] is None):
            found = row
    if found is None:
        return None
    out = dict(found)
    out["require_justification"] = require
    return out


async def list_policies(session: AsyncSession, *, tenant_id: int) -> list[dict[str, Any]]:
    """Wired catalogue + the firm's policy row (missing = off)."""
    stored = {
        r["action_key"]: dict(r)
        for r in (
            await session.execute(
                text(_POLICY_SQL + " WHERE p.tenant_id = :t AND p.provider IS NULL"),
                {"t": tenant_id},
            )
        ).mappings()
    }
    out: list[dict[str, Any]] = []
    for spec in catalogue.WIRED:
        row = stored.get(spec.key) or {}
        require = bool(row.get("require_justification"))
        paired = _SAME_ACT.get(spec.key)
        other = stored.get(paired) if paired else None
        if other:
            require = require or bool(other.get("require_justification"))
            if row.get("mode") != "four_eye" and other.get("mode") == "four_eye":
                row = other
        out.append(
            {
                "action_key": spec.key,
                "label": spec.label,
                "description": spec.description,
                "family": spec.family,
                "event_type": spec.event_type,
                "mode": row.get("mode") or "off",
                "require_justification": require,
                "reviewer_role_id": row.get("reviewer_role_id"),
                "reviewer_role_slug": row.get("reviewer_role_slug"),
                "reviewer_role_name": row.get("reviewer_role_name"),
            }
        )
    return out


async def set_policy(
    session: AsyncSession,
    *,
    tenant_id: int,
    action_key: str,
    mode: str,
    reviewer_role_id: int | None,
    actor_user_id: str | None,
    reviewer_role_slug: str | None = None,
) -> dict[str, Any]:
    spec = catalogue.get(action_key)
    if spec is None or not spec.wired:
        raise ReviewError("That action cannot be put behind a four-eye check.", status=404)
    if mode not in MODES:
        raise ReviewError("Choose off or four-eye.")
    if mode == "four_eye":
        if reviewer_role_id is None and reviewer_role_slug:
            by_slug = await _role_by_slug(session, tenant_id, reviewer_role_slug)
            if by_slug is None:
                raise ReviewError(
                    "That role does not belong to this firm.",
                    field="reviewer_role_id",
                )
            reviewer_role_id = int(by_slug["id"])
        if reviewer_role_id is None:
            raise ReviewError("Pick which role reviews this action.", field="reviewer_role_id")
        role = await _role_in_tenant(session, tenant_id, reviewer_role_id)
        if role is None:
            raise ReviewError("That role does not belong to this firm.", field="reviewer_role_id")
        others = await eligible_reviewer_count(
            session, tenant_id=tenant_id, role_id=reviewer_role_id, exclude_user_id=actor_user_id
        )
        if others < 1:
            raise ReviewError(
                f"No one else in {role['name']} can approve this. "
                "Give another person that role and the approve permission, "
                "or pick a different role.",
                status=409,
            )
    holding = mode == "four_eye"
    policy_id = int(
        (
            await session.execute(
                text(
                    "INSERT INTO actions.policies "
                    "  (tenant_id, action_key, provider, enabled, approval_enabled, "
                    "   approval_role_id) "
                    "VALUES (:t, :k, NULL, true, :a, :r) "
                    "ON CONFLICT (tenant_id, action_key, provider) DO UPDATE SET "
                    "  enabled = true, "
                    "  approval_enabled = EXCLUDED.approval_enabled, "
                    "  approval_role_id = EXCLUDED.approval_role_id "
                    "RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "k": action_key,
                    "a": holding,
                    "r": reviewer_role_id if holding else None,
                },
            )
        ).scalar_one()
    )
    await session.execute(
        text("DELETE FROM actions.policy_roles WHERE policy_id = :p AND kind = 'approve'"),
        {"p": policy_id},
    )
    if holding and reviewer_role_id is not None:
        await session.execute(
            text(
                "INSERT INTO actions.policy_roles (policy_id, role_id, kind) "
                "VALUES (:p, :r, 'approve') ON CONFLICT DO NOTHING"
            ),
            {"p": policy_id, "r": reviewer_role_id},
        )
    row = await get_policy(session, tenant_id=tenant_id, action_key=action_key)
    if row is None:
        raise ReviewError("That action cannot be put behind a four-eye check.", status=404)
    row.pop("action_key", None)
    return {
        "action_key": spec.key,
        "label": spec.label,
        "description": spec.description,
        "family": spec.family,
        "event_type": spec.event_type,
        **row,
    }


async def maybe_hold(
    session: AsyncSession,
    *,
    tenant_id: int,
    workos_user_id: str,
    action_key: str,
    subject_type: str,
    subject_id: str,
    contact_id: int | None,
    payload: dict[str, Any],
    justification: str | None,
    idempotency_key: str,
) -> HoldResult:
    """If the firm has four-eye on for ``action_key``, persist the command and do not apply.

    Returns ``HoldResult(held=False)`` when the policy is off (or missing) so the caller
    applies immediately. A reason is required only when the action's
    ``require_justification`` switch is on.
    """
    spec = catalogue.require_wired(action_key)
    policy = await get_policy(session, tenant_id=tenant_id, action_key=action_key)
    if policy is None or policy["mode"] != "four_eye":
        return HoldResult(held=False)
    if policy["reviewer_role_id"] is None:
        return HoldResult(held=False)

    text_just = (justification or "").strip()
    if policy.get("require_justification") and not text_just:
        raise ReviewError(
            "Explain why this change is needed — a second person will review it.",
            field="justification",
        )
    stored_just = text_just or None

    actor = await _actor_uuid(session, workos_user_id)
    if actor is None:
        raise ReviewError("Your login could not be resolved.", status=401)

    wired = handler(action_key)
    before = await wired.capture_before(session, tenant_id=tenant_id, payload=payload)

    # With the flag on, a held review is a baton run (run-level approval gate). The policy checks
    # above still gate whether to hold at all; only the persistence moves.
    from app.reviews import baton as baton_reviews

    return await baton_reviews.hold(
        session,
        tenant_id=tenant_id,
        spec=spec,
        policy=policy,
        subject_type=subject_type,
        subject_id=subject_id,
        contact_id=contact_id,
        payload=payload,
        before=before,
        justification=stored_just,
        idempotency_key=idempotency_key,
        actor=actor,
        workos_user_id=workos_user_id,
    )


def _out(
    row: Any,
    *,
    caller_id: str | None = None,
    caller_role_ids: list[int] | None = None,
    secrets: bool = True,
) -> dict[str, Any]:
    data = dict(row)
    raw_payload = data.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    raw_before = data.get("before")
    before: dict[str, Any] | None = raw_before if isinstance(raw_before, dict) else None
    key = str(data["action_key"])
    try:
        described = handler(key).describe(before, payload)
    except KeyError:
        # A provider action, or a retired book key still on settled history: no describer.
        described = {"summary": catalogue.RETIRED.get(key, key), "before": "", "after": ""}
    spec = catalogue.get(key)
    action = domain_action(key)
    data["label"] = (
        spec.label if spec else (action.name if action else catalogue.RETIRED.get(key, key))
    )
    data["summary"] = described.get("summary")
    data["before_label"] = described.get("before")
    data["after_label"] = described.get("after")
    if action is not None and action.kind != "book":
        # A provider action held at its gate: the queue approves/rejects it through the run's
        # own decision route, and its body is the input the door recorded rather than a
        # now-vs-proposed. `justification` stays the proposer's own words (or empty) — the
        # listing's Why column must not read a stock sentence as a reason.
        data["source"] = "action"
        if not spec:
            data["summary"] = "Waiting for approval before this is sent."
    raw_input = data.get("input")
    data["input"] = firm_visible(raw_input) if isinstance(raw_input, dict) and raw_input else None
    proposed = str(data.get("proposed_by") or "")
    data["is_mine"] = bool(caller_id) and proposed == caller_id
    roles = [int(role) for role in (data.pop("approver_role_ids", None) or [])]
    role_id = data.get("reviewer_role_id")
    if role_id is not None and int(role_id) not in roles:
        roles.append(int(role_id))
    data["can_decide"] = (
        data.get("status") == "pending"
        and bool(caller_id)
        and proposed != caller_id
        and (caller_role_ids is None or not roles or any(role in caller_role_ids for role in roles))
    )
    # id is internal (Decision 9). case_no is the spoken handle.
    data.pop("id", None)
    data.pop("contact_id", None)
    # Digits stay on the review surface (listing expand + pending card). The
    # public trail still does not store them.
    if not secrets:
        data.pop("payload", None)
        data.pop("before", None)
    return data


async def get_request(
    session: AsyncSession,
    *,
    uid: str,
    caller_id: str | None = None,
    caller_role_ids: list[int] | None = None,
) -> dict[str, Any] | None:
    from app.reviews import baton as baton_reviews

    return await baton_reviews.get_request(
        session, uid=uid, caller_id=caller_id, caller_role_ids=caller_role_ids
    )


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
    from app.reviews import baton as baton_reviews

    return await baton_reviews.list_requests(
        session,
        tenant_id=tenant_id,
        scope=scope,
        caller_id=caller_id,
        caller_role_ids=caller_role_ids,
        status=status,
        action_key=action_key,
        contact_uid=contact_uid,
        limit=limit,
    )


async def _resolve_actor(
    session: AsyncSession, *, workos_user_id: str, actor_user_id: str | None
) -> tuple[str, str]:
    if actor_user_id:
        sub = (
            workos_user_id
            or (
                await session.execute(
                    text("SELECT workos_user_id FROM auth.users WHERE id = CAST(:u AS uuid)"),
                    {"u": actor_user_id},
                )
            ).scalar_one_or_none()
        )
        return actor_user_id, str(sub or f"user:{actor_user_id}")
    actor = await _actor_uuid(session, workos_user_id)
    if actor is None:
        raise ReviewError("Your login could not be resolved.", status=401)
    return actor, workos_user_id


async def pending_for_subject(
    session: AsyncSession,
    *,
    tenant_id: int,
    subject_type: str,
    subject_id: str,
    caller_id: str | None = None,
    caller_role_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    from app.reviews import baton as baton_reviews

    return await baton_reviews.pending_for_subject(
        session,
        tenant_id=tenant_id,
        subject_type=subject_type,
        subject_id=subject_id,
        caller_id=caller_id,
        caller_role_ids=caller_role_ids,
    )


async def pending_for_contact(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    caller_id: str | None = None,
    caller_role_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    from app.reviews import baton as baton_reviews

    return await baton_reviews.pending_for_contact(
        session,
        tenant_id=tenant_id,
        contact_id=contact_id,
        caller_id=caller_id,
        caller_role_ids=caller_role_ids,
    )


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
    from app.reviews import baton as baton_reviews

    return await baton_reviews.approve(
        session,
        tenant_id=tenant_id,
        uid=uid,
        workos_user_id=workos_user_id,
        note=note,
        role_ids=role_ids,
        actor_user_id=actor_user_id,
    )


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
    reason = (note or "").strip()
    if not reason:
        raise ReviewError("Say why you are rejecting this.", field="decision_note")
    from app.reviews import baton as baton_reviews

    return await baton_reviews.reject(
        session,
        tenant_id=tenant_id,
        uid=uid,
        workos_user_id=workos_user_id,
        note=reason,
        role_ids=role_ids,
        actor_user_id=actor_user_id,
    )


async def cancel(
    session: AsyncSession,
    *,
    tenant_id: int,
    uid: str,
    workos_user_id: str,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    from app.reviews import baton as baton_reviews

    return await baton_reviews.cancel(
        session,
        tenant_id=tenant_id,
        uid=uid,
        workos_user_id=workos_user_id,
        actor_user_id=actor_user_id,
    )


def parse_uuid(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(UUID(value))
    except (TypeError, ValueError):
        return None
