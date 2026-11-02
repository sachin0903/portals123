"""The local login bootstrap is a repair, not a one-shot seed.

`pnpm db:reset` wipes the Graphene tenant + owner memberships. A later local revoke leaves them
`inactive`, and the WorkOS poller will not promote that back (it treats inactive as deliberate).
Login then fails with "no access to this portal" even though WorkOS still authenticates. This
script is what puts the mirror back: staff type, folded email, active platform + operations
memberships, every permission including `system`.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_dev_tenant.py"


def _script():
    spec = importlib.util.spec_from_file_location("bootstrap_dev_tenant", SCRIPTS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_bootstrap_reactivates_inactive_memberships_and_folds_email(
    service_session: AsyncSession,
) -> None:
    bootstrap = _script().bootstrap
    suffix = uuid.uuid4().hex[:10]
    email_raw = f"  Owner.{suffix}@GraphenePlatforms.com "
    folded = f"owner.{suffix}@grapheneplatforms.com"
    wos = f"user_boot_{suffix}"
    kwargs = {
        "tenant_alias": f"boot-{suffix}",
        "tenant_name": "Boot Firm",
        "workos_user_id": wos,
        "email": email_raw,
        "first_name": "Owner",
        "last_name": "Boot",
        "role_slug": "admin",
        "role_name": "Administrator",
    }

    first = await bootstrap(service_session, **kwargs)
    assert first["email"] == folded

    row = (
        await service_session.execute(
            text("SELECT email, enabled, type FROM auth.users WHERE id = CAST(:u AS uuid)"),
            {"u": first["user_id"]},
        )
    ).one()
    assert row.email == folded
    assert row.enabled is True
    assert list(row.type) == ["staff"]

    memberships = (
        await service_session.execute(
            text(
                "SELECT portal, status FROM auth.organisation_memberships "
                "WHERE user_id = CAST(:u AS uuid) ORDER BY portal"
            ),
            {"u": first["user_id"]},
        )
    ).all()
    assert [(m.portal, m.status) for m in memberships] == [
        ("operations", "active"),
        ("platform", "active"),
    ]

    system = (
        await service_session.execute(
            text(
                "SELECT 1 FROM auth.role_permissions rp "
                "JOIN auth.permissions p ON p.id = rp.permission_id "
                "WHERE rp.role_id = :r AND p.slug = 'system'"
            ),
            {"r": first["role_id"]},
        )
    ).scalar_one_or_none()
    assert system is not None

    attached = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM auth.membership_roles mr "
                "JOIN auth.organisation_memberships m ON m.id = mr.membership_id "
                "WHERE m.user_id = CAST(:u AS uuid)"
            ),
            {"u": first["user_id"]},
        )
    ).scalar_one()
    assert int(attached) == 2

    await service_session.execute(
        text(
            "UPDATE auth.organisation_memberships SET status = 'inactive' "
            "WHERE user_id = CAST(:u AS uuid)"
        ),
        {"u": first["user_id"]},
    )

    second = await bootstrap(service_session, **kwargs)
    assert second["user_id"] == first["user_id"]
    assert second["tenant_id"] == first["tenant_id"]

    restored = (
        await service_session.execute(
            text(
                "SELECT portal, status FROM auth.organisation_memberships "
                "WHERE user_id = CAST(:u AS uuid) ORDER BY portal"
            ),
            {"u": first["user_id"]},
        )
    ).all()
    assert [(m.portal, m.status) for m in restored] == [
        ("operations", "active"),
        ("platform", "active"),
    ]


@pytest.mark.asyncio
async def test_bootstrap_grants_operations_permissions_missing_from_the_sql_seed(
    service_session: AsyncSession,
) -> None:
    """A fresh migrate does not insert `system` or `tenants:read`. Bootstrap must."""
    bootstrap = _script().bootstrap
    suffix = uuid.uuid4().hex[:10]
    await service_session.execute(
        text(
            "DELETE FROM auth.role_permissions WHERE permission_id IN "
            "(SELECT id FROM auth.permissions WHERE slug IN ('system', 'tenants:read'))"
        )
    )
    await service_session.execute(
        text("DELETE FROM auth.permissions WHERE slug IN ('system', 'tenants:read')")
    )

    result = await bootstrap(
        service_session,
        tenant_alias=f"boot-{suffix}",
        tenant_name="Boot Firm",
        workos_user_id=f"user_boot_{suffix}",
        email=f"owner.{suffix}@grapheneplatforms.com",
        first_name="Owner",
        last_name="Boot",
        role_slug="admin",
        role_name="Administrator",
    )

    held = (
        (
            await service_session.execute(
                text(
                    "SELECT p.slug FROM auth.role_permissions rp "
                    "JOIN auth.permissions p ON p.id = rp.permission_id "
                    "WHERE rp.role_id = :r AND p.slug IN ('system', 'tenants:read') "
                    "ORDER BY p.slug"
                ),
                {"r": result["role_id"]},
            )
        )
        .scalars()
        .all()
    )
    assert list(held) == ["system", "tenants:read"]


@pytest.mark.asyncio
async def test_ensure_staff_user_adds_another_login_on_the_same_role(
    service_session: AsyncSession,
) -> None:
    module = _script()
    suffix = uuid.uuid4().hex[:10]
    owner = await module.bootstrap(
        service_session,
        tenant_alias=f"boot-{suffix}",
        tenant_name="Boot Firm",
        workos_user_id=f"user_boot_{suffix}",
        email=f"owner.{suffix}@grapheneplatforms.com",
        first_name="Owner",
        last_name="Boot",
        role_slug="admin",
        role_name="Administrator",
    )
    extra_wos = f"user_extra_{suffix}"
    extra_email = f"extra.{suffix}@grapheneplatforms.com"

    extra_id = await module.ensure_staff_user(
        service_session,
        tenant_id=owner["tenant_id"],
        role_id=owner["role_id"],
        workos_user_id=extra_wos,
        email=f"  Extra.{suffix}@GraphenePlatforms.com ",
        first_name="Extra",
        last_name="Login",
    )
    assert extra_id != owner["user_id"]

    row = (
        await service_session.execute(
            text("SELECT email, tenant_id FROM auth.users WHERE id = CAST(:u AS uuid)"),
            {"u": extra_id},
        )
    ).one()
    assert row.email == extra_email
    assert row.tenant_id == owner["tenant_id"]

    await service_session.execute(
        text(
            "UPDATE auth.organisation_memberships SET status = 'inactive' "
            "WHERE user_id = CAST(:u AS uuid)"
        ),
        {"u": extra_id},
    )
    again = await module.ensure_staff_user(
        service_session,
        tenant_id=owner["tenant_id"],
        role_id=owner["role_id"],
        workos_user_id=extra_wos,
        email=extra_email,
        first_name="Extra",
        last_name="Login",
    )
    assert again == extra_id
    restored = (
        await service_session.execute(
            text(
                "SELECT portal, status FROM auth.organisation_memberships "
                "WHERE user_id = CAST(:u AS uuid) ORDER BY portal"
            ),
            {"u": extra_id},
        )
    ).all()
    assert [(m.portal, m.status) for m in restored] == [
        ("operations", "active"),
        ("platform", "active"),
    ]
