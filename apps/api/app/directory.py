"""Cross-tenant user directory queries (read-only, over the auth.* mirror)."""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_stream import ROUTINE_EVENT_TYPES
from app.page_views import daily_activity


async def list_users(session: AsyncSession) -> list[dict[str, object]]:
    """All logins with how many firms they belong to."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT u.id AS user_id, u.email, u.name, u.enabled, u.workos_user_id, "
                    "       count(DISTINCT m.tenant_id) AS memberships "
                    "FROM auth.users u "
                    "LEFT JOIN auth.organisation_memberships m ON m.user_id = u.id "
                    "GROUP BY u.id, u.email, u.name, u.enabled, u.workos_user_id "
                    "ORDER BY u.email"
                )
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def user_with_memberships(session: AsyncSession, user_id: str) -> dict[str, object] | None:
    """A login + the firms it belongs to (the person across tenants)."""
    user = (
        (
            await session.execute(
                text(
                    # `uid` rides along: the admin portal addresses a login by it, and its
                    # write actions then need the uuid the member endpoints are typed on.
                    "SELECT id, uid, email, name, enabled, mobile, workos_user_id, last_sign_in "
                    "FROM auth.users WHERE id = :u"
                ),
                {"u": user_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if user is None:
        return None
    memberships = (
        (
            await session.execute(
                text(
                    # `tenant_uid` because the admin portal's firm links are uid-addressed; the
                    # numeric id stays for the per-firm role fetches, which take the API's own
                    # integer contract.
                    "SELECT m.tenant_id, t.uid AS tenant_uid, t.name AS tenant_name, "
                    "       t.alias AS tenant_alias, "
                    "       m.portal, r.slug AS role_slug, m.status "
                    "FROM auth.organisation_memberships m "
                    "JOIN auth.tenants t ON t.id = m.tenant_id "
                    "LEFT JOIN auth.roles r ON r.id = m.role_id "
                    "WHERE m.user_id = :u ORDER BY t.name, m.portal"
                ),
                {"u": user_id},
            )
        )
        .mappings()
        .all()
    )
    return {"user": dict(user), "memberships": [dict(m) for m in memberships]}


async def member_profile(
    session: AsyncSession, tenant_id: int, user_uid: str
) -> dict[str, object] | None:
    """One tenant-scoped login, its effective access, and its recorded activity summary.

    This is the read model behind both the firm's Team/Profile detail and the operations view.
    It deliberately reports what the application can prove from its own authorisation mirror:
    complete role sets and their permission union, client identities the login may act for, and
    clients assigned to a staff member. Ordinary GET requests are not audited, so ``activity``
    means authentication events and successful mutations rather than an invented browsing trail.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, email, name, first_name, last_name, mobile, avatar, enabled, "
                    "       type AS types, is_adviser, job_title, fca_reference, "
                    "       provider_orgs, provider_refs, "
                    "       workos_user_id, invitation_sent_at, invitation_expires_at, "
                    "       (invitation_id IS NOT NULL AND invitation_accepted_at IS NULL "
                    "         AND invitation_expires_at > now()) AS invitation_pending, "
                    "       invitation_accepted_at, last_sign_in, created_at, updated_at "
                    "FROM auth.users "
                    "WHERE tenant_id = :t AND uid = :uid AND deleted_at IS NULL"
                ),
                {"t": tenant_id, "uid": user_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    user = dict(row)
    user["id"] = str(user["id"])
    user["is_adviser"] = bool(user.get("is_adviser"))
    orgs = user.pop("provider_orgs", None) or {}
    if isinstance(orgs, str):
        orgs = json.loads(orgs)
    if not isinstance(orgs, dict):
        orgs = {}
    refs = user.pop("provider_refs", None) or {}
    if isinstance(refs, str):
        refs = json.loads(refs)
    if not isinstance(refs, dict):
        refs = {}
    user["seccl_node"] = orgs.get("seccl")
    user["sei_dsr"] = refs.get("sei")
    user_id = user["id"]

    membership_rows = (
        (
            await session.execute(
                text(
                    "SELECT m.portal, m.status, m.created_at, "
                    "       COALESCE(array_agg(DISTINCT r.slug ORDER BY r.slug) "
                    "         FILTER (WHERE r.slug IS NOT NULL), '{}'::text[]) AS role_slugs, "
                    "       COALESCE(array_agg(DISTINCT p.slug ORDER BY p.slug) "
                    "         FILTER (WHERE p.slug IS NOT NULL), '{}'::text[]) AS permissions "
                    "FROM auth.organisation_memberships m "
                    "LEFT JOIN auth.membership_roles mr ON mr.membership_id = m.id "
                    "LEFT JOIN auth.roles r ON r.id = mr.role_id "
                    "LEFT JOIN auth.role_permissions rp ON rp.role_id = r.id "
                    "LEFT JOIN auth.permissions p ON p.id = rp.permission_id "
                    "WHERE m.tenant_id = :t AND m.user_id = :u "
                    "GROUP BY m.id, m.portal, m.status, m.created_at ORDER BY m.portal"
                ),
                {"t": tenant_id, "u": user_id},
            )
        )
        .mappings()
        .all()
    )
    memberships = [dict(row) for row in membership_rows]
    granted_permissions = (
        {
            str(permission)
            for row in membership_rows
            if row["status"] == "active"
            for permission in row["permissions"]
        }
        if user["enabled"]
        else set()
    )
    permission_catalogue = set(
        (await session.execute(text("SELECT slug FROM auth.permissions"))).scalars()
    )
    if "system" in granted_permissions:
        # `system` bypasses every Python and RLS permission check. Showing only the stored slug
        # would materially understate what an operations identity can do.
        effective_permissions = sorted(permission_catalogue)
    else:
        # Runtime `require_permission` treats write as the matching read. Reflect that implication
        # in the profile instead of labelling the raw grants "effective".
        implied_reads = {
            f"{slug.removesuffix(':write')}:read"
            for slug in granted_permissions
            if slug.endswith(":write")
        }
        effective_permissions = sorted(granted_permissions | (implied_reads & permission_catalogue))

    client_access = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    "SELECT c.uid, c.name, c.email, cu.capacity, cu.created_at, "
                    "       count(DISTINCT a.id) AS account_count "
                    "FROM crm.contact_users cu "
                    "JOIN crm.contacts c ON c.id = cu.contact_id AND c.tenant_id = cu.tenant_id "
                    "LEFT JOIN platform.accounts a "
                    "  ON a.contact_id = c.id AND a.tenant_id = c.tenant_id "
                    "WHERE cu.tenant_id = :t AND cu.user_id = :u AND c.deleted_at IS NULL "
                    "GROUP BY c.id, c.uid, c.name, c.email, cu.capacity, cu.created_at "
                    "ORDER BY c.name NULLS LAST, c.email"
                ),
                {"t": tenant_id, "u": user_id},
            )
        )
        .mappings()
        .all()
    ]
    staff_access = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    "SELECT c.uid, c.name, c.email, ca.role, ca.created_at, "
                    "       count(DISTINCT a.id) AS account_count "
                    "FROM crm.client_advisers ca "
                    "JOIN crm.contacts c ON c.id = ca.contact_id AND c.tenant_id = ca.tenant_id "
                    "LEFT JOIN platform.accounts a "
                    "  ON a.contact_id = c.id AND a.tenant_id = c.tenant_id "
                    "WHERE ca.tenant_id = :t AND ca.adviser_id = :u AND c.deleted_at IS NULL "
                    "GROUP BY c.id, c.uid, c.name, c.email, ca.role, ca.created_at "
                    "ORDER BY c.name NULLS LAST, c.email"
                ),
                {"t": tenant_id, "u": user_id},
            )
        )
        .mappings()
        .all()
    ]
    activity = (
        (
            await session.execute(
                text(
                    "SELECT count(*) AS recorded_actions, max(created_at) AS last_recorded_action, "
                    "       count(*) FILTER (WHERE event_type = 'authentication.succeeded') "
                    "         AS successful_sign_ins, "
                    "       count(*) FILTER (WHERE event_type = 'authentication.failed') "
                    "         AS failed_sign_ins "
                    "FROM audit.actions "
                    "WHERE actor_id = :u AND (tenant_id = :t OR target_tenant_id = :t) "
                    "  AND event_type NOT LIKE 'aml.%' AND event_type NOT LIKE 'action.aml-%' "
                    "  AND event_type <> ALL(CAST(:hidden AS text[]))"
                ),
                {"t": tenant_id, "u": user_id, "hidden": list(ROUTINE_EVENT_TYPES)},
            )
        )
        .mappings()
        .one()
    )
    recent_activity = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    "SELECT id, event_type, created_at, method, route, status, "
                    "       path_params, metadata "
                    "FROM audit.actions "
                    "WHERE actor_id = :u AND (tenant_id = :t OR target_tenant_id = :t) "
                    # Keep the firm audit stream's AML/tipping-off boundary here too. The same
                    # profile read model is used by firm and operations surfaces; an operator can
                    # investigate AML in its dedicated, permissioned console.
                    "  AND event_type NOT LIKE 'aml.%' AND event_type NOT LIKE 'action.aml-%' "
                    "  AND event_type <> ALL(CAST(:hidden AS text[])) "
                    "ORDER BY id DESC LIMIT 30"
                ),
                {"t": tenant_id, "u": user_id, "hidden": list(ROUTINE_EVENT_TYPES)},
            )
        )
        .mappings()
        .all()
    ]
    # The same window as the action rows, kept separate so a run of navigations cannot
    # push a sign-in out of the list. The profile merges the two by time.
    recent_page_views = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    "SELECT id, portal, path, created_at "
                    "FROM audit.page_views "
                    "WHERE actor_id = :u AND tenant_id = :t "
                    "ORDER BY id DESC LIMIT 30"
                ),
                {"t": tenant_id, "u": user_id},
            )
        )
        .mappings()
        .all()
    ]
    return {
        "user_id": str(user_id),
        "user": user,
        "memberships": memberships,
        "effective_permissions": effective_permissions,
        "client_access": client_access,
        "staff_access": staff_access,
        "activity": dict(activity),
        "recent_activity": recent_activity,
        "recent_page_views": recent_page_views,
        "activity_days": await daily_activity(session, tenant_id, user_id),
    }
