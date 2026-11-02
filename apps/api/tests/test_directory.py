"""Cross-tenant user directory queries (read-only, over the mirror)."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.directory import list_users, member_profile, user_with_memberships


@pytest.mark.asyncio
async def test_user_directory(service_session: AsyncSession) -> None:
    uid = (
        await service_session.execute(
            text(
                "INSERT INTO auth.users (uid, email, workos_user_id, enabled) "
                "VALUES ('usr_dir', 'dir@x.test', 'wos_dir', true) RETURNING id"
            )
        )
    ).scalar_one()
    tid = (
        await service_session.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES ('ten_dir', 'firm-dir', 'Firm Dir') RETURNING id"
            )
        )
    ).scalar_one()
    await service_session.execute(
        text("INSERT INTO auth.organisation_memberships (user_id, tenant_id) VALUES (:u, :t)"),
        {"u": uid, "t": tid},
    )

    users = await list_users(service_session)
    me = next(u for u in users if u["email"] == "dir@x.test")
    assert me["memberships"] == 1

    detail = await user_with_memberships(service_session, str(uid))
    assert detail is not None
    assert detail["user"]["email"] == "dir@x.test"
    assert [m["tenant_name"] for m in detail["memberships"]] == ["Firm Dir"]
    # Each membership carries the firm's UID as well as its serial id: the admin portal links a
    # person's firms as `/tenants/{uid}`, and with only the id those links 404'd. The id stays
    # because the page also fetches each firm's roles, which take the API's integer contract.
    membership = detail["memberships"][0]
    assert isinstance(membership["tenant_uid"], str)
    assert membership["tenant_uid"]
    assert isinstance(membership["tenant_id"], int)

    # unknown (but uuid-shaped) id -> None
    assert (
        await user_with_memberships(service_session, "00000000-0000-0000-0000-000000000000") is None
    )


@pytest.mark.asyncio
async def test_member_profile_reports_effective_access(service_session: AsyncSession) -> None:
    tenant_id = (
        await service_session.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES ('ten_profile', 'profile-firm', 'Profile Firm') RETURNING id"
            )
        )
    ).scalar_one()
    user_id = (
        await service_session.execute(
            text(
                "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled, type) "
                "VALUES ('authprofile1', :t, 'member@profile.test', 'wos_profile_member', true, "
                "ARRAY['staff']::text[]) RETURNING id"
            ),
            {"t": tenant_id},
        )
    ).scalar_one()
    role_id = (
        await service_session.execute(
            text(
                "INSERT INTO auth.roles (tenant_id, slug, name, portal, type) "
                "VALUES (:t, 'profile-reader', 'Profile reader', 'platform', 'organisation') "
                "RETURNING id"
            ),
            {"t": tenant_id},
        )
    ).scalar_one()
    await service_session.execute(
        text(
            "INSERT INTO auth.role_permissions (role_id, permission_id) "
            "SELECT :r, id FROM auth.permissions WHERE slug = 'members:read'"
        ),
        {"r": role_id},
    )
    await service_session.execute(
        text(
            "INSERT INTO auth.organisation_memberships "
            "(user_id, tenant_id, role_id, portal, status) "
            "VALUES (:u, :t, :r, 'platform', 'active')"
        ),
        {"u": user_id, "t": tenant_id, "r": role_id},
    )

    profile = await member_profile(service_session, tenant_id, "authprofile1")

    assert profile is not None
    assert profile["user"]["email"] == "member@profile.test"
    assert profile["user"]["is_adviser"] is False
    assert profile["user"]["job_title"] is None
    assert profile["effective_permissions"] == ["members:read"]
    assert profile["memberships"][0]["role_slugs"] == ["profile-reader"]
    assert profile["client_access"] == []
    assert profile["staff_access"] == []


@pytest.mark.asyncio
async def test_member_profile_omits_retired_ticket_mints(service_session: AsyncSession) -> None:
    """A ticket mint already in the stream is not Recorded activity. A sign-in is."""
    tenant_id = (
        await service_session.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES ('ten_activity', 'activity-firm', 'Activity Firm') RETURNING id"
            )
        )
    ).scalar_one()
    user_id = (
        await service_session.execute(
            text(
                "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled, type) "
                "VALUES ('authactivity1', :t, 'activity@profile.test', 'wos_profile_activity', "
                "true, ARRAY['staff']::text[]) RETURNING id"
            ),
            {"t": tenant_id},
        )
    ).scalar_one()
    await service_session.execute(
        text(
            "INSERT INTO audit.actions "
            "  (event_type, tenant_id, actor_id, actor_sub, method, route, status) "
            "VALUES "
            "  ('notification.stream_opened', :t, CAST(:u AS uuid), 'wos_profile_activity', "
            "   'POST', '/notifications/ws-ticket', 200), "
            "  ('message.stream_opened', :t, CAST(:u AS uuid), 'wos_profile_activity', "
            "   'POST', '/messaging/ws-ticket', 200), "
            "  ('authentication.succeeded', :t, CAST(:u AS uuid), 'wos_profile_activity', "
            "   'POST', '/auth/login', 200)"
        ),
        {"t": tenant_id, "u": user_id},
    )
    await service_session.execute(
        text(
            "INSERT INTO audit.page_views (tenant_id, actor_id, portal, path) "
            "VALUES (:t, CAST(:u AS uuid), 'platform', '/team/authactivity1')"
        ),
        {"t": tenant_id, "u": user_id},
    )

    profile = await member_profile(service_session, tenant_id, "authactivity1")

    assert profile is not None
    assert profile["activity"]["recorded_actions"] == 1
    assert profile["activity"]["successful_sign_ins"] == 1
    assert [event["event_type"] for event in profile["recent_activity"]] == [
        "authentication.succeeded"
    ]
    assert [(view["portal"], view["path"]) for view in profile["recent_page_views"]] == [
        ("platform", "/team/authactivity1")
    ]
    days = profile["activity_days"]
    assert isinstance(days, list)
    assert 365 <= len(days) <= 371
    assert sum(day["actions"] for day in days) == 1
    assert sum(day["page_views"] for day in days) == 1
    assert any(day["actions"] == 0 for day in days)
