"""Create a run, hold it for approval, and execute its steps.

A run is the job and, when the firm has named an approver role, the held command.
A book action keeps its justification and its RV-{n} case number on that row.
Approval is a yes from one of the roles the firm named. An empty list is off.
The provider is not part of that policy: the planner reads it off the book.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.catalogue import (
    CATEGORIES,
    get,
    onboarding_plan,
    provider_tags,
    reads,
    settings_rows,
)

# Book mutations register here when reviews moves. Empty until that swap: a book
# action with no handler records the run and does not touch the book.
BOOK_APPLIES: dict[str, Any] = {}

logger = logging.getLogger(__name__)

OPEN_STATUSES = ("awaiting_approval", "queued", "running")
TERMINAL_STATUSES = (
    "succeeded",
    "failed",
    "rejected",
    "cancelled",
    "conflicted",
    "needs_attention",
)


class ActionError(Exception):
    def __init__(self, message: str, *, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass(slots=True)
class Held:
    body: dict[str, Any]


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {})


async def _role_id(session: AsyncSession, *, tenant_id: int, slug: str | None) -> int | None:
    if not slug:
        return None
    found = (
        await session.execute(
            text(
                "SELECT id FROM auth.roles "
                "WHERE tenant_id = :t AND slug = :s AND portal = 'platform'"
            ),
            {"t": tenant_id, "s": slug},
        )
    ).scalar_one_or_none()
    if found is None:
        raise ActionError(f"No platform role named {slug}.")
    return int(found)


async def list_settings(session: AsyncSession, *, tenant_id: int) -> dict[str, Any]:
    policies = (
        (
            await session.execute(
                text(
                    "SELECT p.action_key, p.enabled, p.approval_enabled, "
                    "       p.require_justification, p.manual, "
                    "       p.notify_in_app, p.notify_email, "
                    "       COALESCE(array_agg(role.slug) FILTER ("
                    "         WHERE g.kind = 'invoke' AND role.slug IS NOT NULL"
                    "       ), '{}') AS role_slugs, "
                    "       COALESCE(array_agg(role.slug) FILTER ("
                    "         WHERE g.kind = 'approve' AND role.slug IS NOT NULL"
                    "       ), '{}') AS approval_role_slugs "
                    "FROM actions.policies p "
                    "LEFT JOIN actions.policy_roles g ON g.policy_id = p.id "
                    "LEFT JOIN auth.roles role ON role.id = g.role_id "
                    "WHERE p.tenant_id = :t AND p.provider IS NULL "
                    "GROUP BY p.id"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    by_key = {row["action_key"]: row for row in policies}
    actions = []
    for action in settings_rows():
        policy = by_key.get(action.key)
        actions.append(
            {
                "key": action.key,
                "name": action.name,
                "description": action.description,
                "category": action.category,
                "subject_kinds": list(action.subject_kinds),
                "providers": provider_tags(action),
                "reads": reads(action),
                "plan": onboarding_plan() if action.key == "client.setup" else None,
                "kind": action.kind,
                "holds_record": action.holds_record,
                "enabled": bool(policy["enabled"]) if policy else True,
                "approval_enabled": bool(policy["approval_enabled"]) if policy else False,
                "require_justification": (
                    bool(policy["require_justification"]) if policy else False
                ),
                "manual": bool(policy["manual"]) if policy else False,
                # How approvers of a held run are notified. In-app on, email off by default.
                "notify_in_app": bool(policy["notify_in_app"]) if policy else True,
                "notify_email": bool(policy["notify_email"]) if policy else False,
                "approval_role_slugs": list(policy["approval_role_slugs"]) if policy else [],
                "role_slugs": list(policy["role_slugs"]) if policy else [],
            }
        )
    return {
        "categories": [{"id": code, "label": label} for code, label in CATEGORIES],
        "actions": actions,
    }


async def _book_approvers_exist(
    session: AsyncSession, *, tenant_id: int, role_ids: list[int]
) -> None:
    """The old peer-review preflight: someone else must be able to decide."""
    from app.reviews.service import eligible_reviewer_count

    caller = (
        await session.execute(text("SELECT NULLIF(auth.jwt() ->> 'user_uuid', '')"))
    ).scalar_one_or_none()
    others = 0
    for role_id in role_ids:
        others += await eligible_reviewer_count(
            session,
            tenant_id=tenant_id,
            role_id=role_id,
            exclude_user_id=str(caller) if caller else None,
        )
    if others < 1:
        raise ActionError(
            "No one else in the selected roles can approve this. "
            "Give another person that role, or pick a different role.",
            status=409,
        )


async def set_policy(
    session: AsyncSession,
    *,
    tenant_id: int,
    action_key: str,
    provider: str | None,
    enabled: bool,
    approval_enabled: bool,
    approval_role_slug: str | None,
    role_slugs: list[str],
    approval_role_slugs: list[str] | None = None,
    require_justification: bool = False,
    manual: bool = False,
    notify_in_app: bool = True,
    notify_email: bool = False,
) -> dict[str, Any]:
    # A posted source is not stored. The firm enables the action; the planner
    # chooses the provider from the account when someone runs it.
    del provider
    action = get(action_key)
    if action is None:
        raise ActionError("That action is not in the catalogue.")
    if action.kind == "book":
        # The door always exists. Approver roles are the only switch.
        enabled = True
        role_slugs = []
    approvers = [slug for slug in (approval_role_slugs or []) if slug]
    if not approvers and approval_role_slug:
        approvers = [approval_role_slug]
    approver_ids: list[int] = []
    for slug in approvers:
        found = await _role_id(session, tenant_id=tenant_id, slug=slug)
        if found is not None:
            approver_ids.append(found)
    # A posted list is the switch: roles selected means a second person must approve.
    if approval_role_slugs is not None:
        approval_enabled = bool(approver_ids)
    elif approval_enabled and not approvers:
        raise ActionError("Choose at least one role that approves this action.")
    role_id = approver_ids[0] if approver_ids else None
    if approval_enabled and (action.kind == "book" or action.holds_record):
        await _book_approvers_exist(session, tenant_id=tenant_id, role_ids=approver_ids)
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO actions.policies "
                    "  (tenant_id, action_key, provider, enabled, approval_enabled, "
                    "   approval_role_id, require_justification, manual, "
                    "   notify_in_app, notify_email) "
                    "VALUES (:t, :k, :p, :e, :a, :r, :j, :m, :nia, :nem) "
                    "ON CONFLICT (tenant_id, action_key, provider) DO UPDATE SET "
                    "  enabled = EXCLUDED.enabled, "
                    "  approval_enabled = EXCLUDED.approval_enabled, "
                    "  approval_role_id = EXCLUDED.approval_role_id, "
                    "  require_justification = EXCLUDED.require_justification, "
                    "  manual = EXCLUDED.manual, "
                    "  notify_in_app = EXCLUDED.notify_in_app, "
                    "  notify_email = EXCLUDED.notify_email "
                    "RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "k": action_key,
                    "p": None,
                    "e": enabled,
                    "a": approval_enabled,
                    "r": role_id,
                    "j": require_justification,
                    "m": manual,
                    "nia": notify_in_app,
                    "nem": notify_email,
                },
            )
        )
        .mappings()
        .one()
    )
    policy_id = int(row["id"])
    # The editor's row is the one source once it is saved. The book key a door holds on
    # (`account.model_changed` for "Change model", `contact.bank_details_changed` for "Update
    # client bank account") is read as its pair, so a stale book row would keep an approval, a
    # reason switch or a notify channel alive that the editor shows as off. Migration 0270 left
    # those rows in place for the rollout window; the first save here retires them.
    from app.reviews.service import _SAME_ACT

    paired = [book for book, settings_key in _SAME_ACT.items() if settings_key == action_key]
    if paired:
        await session.execute(
            text(
                "DELETE FROM actions.policies WHERE tenant_id = :t AND provider IS NULL "
                "  AND action_key = ANY(CAST(:keys AS text[]))"
            ),
            {"t": tenant_id, "keys": paired},
        )
    await session.execute(
        text("DELETE FROM actions.policy_roles WHERE policy_id = :p"), {"p": policy_id}
    )
    for slug in role_slugs:
        granted = await _role_id(session, tenant_id=tenant_id, slug=slug)
        await session.execute(
            text(
                "INSERT INTO actions.policy_roles (policy_id, role_id, kind) "
                "VALUES (:p, :r, 'invoke') ON CONFLICT DO NOTHING"
            ),
            {"p": policy_id, "r": granted},
        )
    for granted in approver_ids:
        await session.execute(
            text(
                "INSERT INTO actions.policy_roles (policy_id, role_id, kind) "
                "VALUES (:p, :r, 'approve') ON CONFLICT DO NOTHING"
            ),
            {"p": policy_id, "r": granted},
        )
    listed = await list_settings(session, tenant_id=tenant_id)
    actions = listed["actions"]
    rows = actions if isinstance(actions, list) else []
    match = next(
        (item for item in rows if isinstance(item, dict) and item.get("key") == action_key),
        None,
    )
    if not isinstance(match, dict):
        return {
            "key": action_key,
            "enabled": enabled,
            "approval_enabled": approval_enabled,
            "require_justification": require_justification,
            "manual": manual,
            "notify_in_app": notify_in_app,
            "notify_email": notify_email,
            "approval_role_slugs": approvers,
            "role_slugs": role_slugs,
        }
    shaped: dict[str, Any] = {}
    for key, value in match.items():
        shaped[str(key)] = value
    return shaped


async def _policy(
    session: AsyncSession, *, tenant_id: int, action_key: str, provider: str | None
) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, enabled, approval_enabled, approval_role_id, "
                    "       require_justification, manual, notify_in_app, notify_email "
                    "FROM actions.policies "
                    "WHERE tenant_id = :t AND action_key = :k "
                    "  AND provider IS NOT DISTINCT FROM :p"
                ),
                {"t": tenant_id, "k": action_key, "p": provider},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


async def _caller_holds(
    session: AsyncSession, *, policy_id: int, role_ids: list[int] | None
) -> bool:
    granted = (
        (
            await session.execute(
                text(
                    "SELECT role_id FROM actions.policy_roles "
                    "WHERE policy_id = :p AND kind = 'invoke'"
                ),
                {"p": policy_id},
            )
        )
        .scalars()
        .all()
    )
    if not granted:
        return True
    if not role_ids:
        return False
    return any(int(role) in set(role_ids) for role in granted)


async def _invoke_allowed(
    session: AsyncSession, *, tenant_id: int, policy_id: int, role_ids: list[int]
) -> bool:
    """Whether this caller may start the action.

    A member of this firm must be on the list when the firm has set one. No roles
    in this firm means the door's own grant stands: an operations proxy carries
    another tenant's roles, or none, and the approver releasing a held booking
    does not pass roles at all.
    """
    if not role_ids:
        return True
    own = (
        await session.execute(
            text("SELECT id FROM auth.roles WHERE tenant_id = :t AND id = ANY(:ids)"),
            {"t": tenant_id, "ids": role_ids},
        )
    ).scalars()
    own_ids = [int(role) for role in own.all()]
    if not own_ids:
        return True
    return await _caller_holds(session, policy_id=policy_id, role_ids=own_ids)


async def _approval(
    session: AsyncSession, *, tenant_id: int, action_key: str
) -> tuple[bool, list[int]]:
    """The firm's action policy, not a per-provider row. Any named role may approve."""
    policy = await _policy(session, tenant_id=tenant_id, action_key=action_key, provider=None)
    if policy is None or not policy["approval_enabled"]:
        return False, []
    granted = (
        await session.execute(
            text(
                "SELECT role_id FROM actions.policy_roles WHERE policy_id = :p AND kind = 'approve'"
            ),
            {"p": int(policy["id"])},
        )
    ).scalars()
    roles = [int(role) for role in granted.all()]
    if not roles and policy["approval_role_id"] is not None:
        roles = [int(policy["approval_role_id"])]
    return True, roles


def _custodian_codes() -> tuple[str, ...]:
    """Providers that custody assets. A scheme-administrator link is not a cash rail."""
    from app.providers import custodians

    return custodians()


async def _load_account(
    session: AsyncSession, *, tenant_id: int, account_id: int
) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.uid, a.contact_id, a.product_id, a.model_id, "
                    # The column is what the firm stamped. A synced account often has only the
                    # primary link, which is the same custodian the instruction path already uses.
                    "       COALESCE(a.custodian::text, ( "
                    "         SELECT min(l.provider::text) FROM platform.links l "
                    "          WHERE l.entity_type = 'account' AND l.entity_id = a.id "
                    "            AND l.tenant_id = a.tenant_id AND l.link_type = 'primary' "
                    "            AND l.provider::text = ANY(:custodians) "
                    "         HAVING count(DISTINCT l.provider) = 1 "
                    "       )) AS custodian, a.data, a.payments, "
                    "       a.withdrawals, a.transfer, "
                    "       c.uid AS contact_uid, p.product_type::text AS product_type "
                    "FROM platform.accounts a "
                    "LEFT JOIN crm.contacts c ON c.id = a.contact_id "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "WHERE a.id = :a AND a.tenant_id = :t"
                ),
                {"a": account_id, "t": tenant_id, "custodians": list(_custodian_codes())},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


def account_subjects(account: dict[str, Any], contact_id: int | None) -> list[dict[str, Any]]:
    """The account, and its contact when this run is about both.

    The activity starter and the money door name the same records. One list, so a payment
    run and a run started from the account page attach to the same subjects.
    """
    subjects: list[dict[str, Any]] = [
        {
            "entity_type": "account",
            "entity_uid": str(account["uid"]),
            "entity_id": int(account["id"]),
        }
    ]
    if contact_id is not None and account.get("contact_uid"):
        subjects.append(
            {
                "entity_type": "contact",
                "entity_uid": str(account["contact_uid"]),
                "entity_id": contact_id,
            }
        )
    return subjects


async def caller_ids(session: AsyncSession) -> tuple[str | None, list[int]]:
    """The acting user and their roles. A session with no request claims yields neither."""
    try:
        async with session.begin_nested():
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT auth.jwt() ->> 'user_uuid' AS user_uuid, "
                            "       auth.role_ids() AS role_ids"
                        )
                    )
                )
                .mappings()
                .one()
            )
    except Exception:
        logger.debug("action caller has no request claims", exc_info=True)
        return None, []
    raw = row["role_ids"] or []
    return (str(row["user_uuid"]) if row["user_uuid"] else None, [int(role) for role in raw])


def pending_response(held: Held) -> JSONResponse:
    return JSONResponse(status_code=202, content=held.body)


async def _contact_by_uid(session: AsyncSession, *, tenant_id: int, uid: str) -> int:
    found = (
        await session.execute(
            text("SELECT id FROM crm.contacts WHERE tenant_id = :t AND uid = :u"),
            {"t": tenant_id, "u": uid},
        )
    ).scalar_one_or_none()
    if found is None:
        raise ActionError("That client was not found.", status=404)
    return int(found)


async def _account_id_by_uid(session: AsyncSession, *, tenant_id: int, uid: str) -> int:
    found = (
        await session.execute(
            text("SELECT id FROM platform.accounts WHERE tenant_id = :t AND uid = :u"),
            {"t": tenant_id, "u": uid},
        )
    ).scalar_one_or_none()
    if found is None:
        raise ActionError("That account was not found.", status=404)
    return int(found)


async def _provider_for_contact(
    session: AsyncSession, *, tenant_id: int, contact_id: int, providers: tuple[str, ...]
) -> str:
    if len(providers) == 1:
        return providers[0]
    found = (
        (
            await session.execute(
                text(
                    "SELECT DISTINCT provider::text AS provider FROM platform.links "
                    "WHERE tenant_id = :t AND entity_type = 'contact' "
                    "  AND entity_id = :id AND provider::text = ANY(:providers)"
                ),
                {"t": tenant_id, "id": contact_id, "providers": list(providers)},
            )
        )
        .scalars()
        .all()
    )
    if len(found) == 1:
        return str(found[0])
    if len(found) > 1:
        raise ActionError("This client is on more than one provider. Run it from the account.")
    # Create is what writes the link. Until then the account's custodian, or the
    # firm's only enabled custodian, is the same choice the journey already makes.
    accounts = (
        (
            await session.execute(
                text(
                    "SELECT DISTINCT COALESCE(a.custodian::text, ( "
                    "  SELECT min(l.provider::text) FROM platform.links l "
                    "   WHERE l.entity_type = 'account' AND l.entity_id = a.id "
                    "     AND l.tenant_id = a.tenant_id AND l.link_type = 'primary' "
                    "     AND l.provider::text = ANY(:providers) "
                    "  HAVING count(DISTINCT l.provider) = 1 "
                    ")) AS provider "
                    "FROM platform.accounts a "
                    "WHERE a.tenant_id = :t AND ( "
                    "  a.contact_id = :id OR EXISTS ( "
                    "    SELECT 1 FROM crm.account_contacts ac "
                    "     WHERE ac.account_id = a.id AND ac.tenant_id = a.tenant_id "
                    "       AND ac.contact_id = :id "
                    "  ) "
                    ")"
                ),
                {"t": tenant_id, "id": contact_id, "providers": list(providers)},
            )
        )
        .scalars()
        .all()
    )
    on_account = [str(item) for item in accounts if item and str(item) in providers]
    if len(on_account) == 1:
        return on_account[0]
    if len(on_account) > 1:
        raise ActionError("This client is on more than one provider. Run it from the account.")
    firms = (
        (
            await session.execute(
                text(
                    "SELECT provider::text FROM platform.tenant_platforms "
                    "WHERE tenant_id = :t AND enabled AND provider::text = ANY(:providers)"
                ),
                {"t": tenant_id, "providers": list(providers)},
            )
        )
        .scalars()
        .all()
    )
    enabled = [str(item) for item in firms if item]
    if len(enabled) == 1:
        return enabled[0]
    if not enabled:
        raise ActionError("This client has no provider link yet.")
    raise ActionError("This client is on more than one provider. Run it from the account.")
