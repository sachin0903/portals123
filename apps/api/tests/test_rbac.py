"""Tenant role + permission management (write-through to WorkOS + mirror). WorkOS is faked;
the contacts:read_all permission comes from the synced registry (app/permissions.py, seeded into
the test DB by conftest). Every role is tenant-scoped — the model tenant's roles are edited
mirror-only (no WorkOS project)."""

from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import environments
from app.rbac import create_org_role, list_org_roles, set_org_role_permissions


class _FakeOrgAuthz:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._perms: list[str] = []
        #: Org roles this fake holds, by slug. `ensure_workos_org_role` ADOPTS before it creates,
        #: so the listing has to reflect what was created or every second call creates again.
        self._roles: dict[str, SimpleNamespace] = {}
        self.authorization = SimpleNamespace(
            create_organization_role=self._create,
            list_organization_roles=self._list_roles,
            set_organization_role_permissions=self._setperms,
            list_permissions=self._list_permissions,
            create_permission=self._create_permission,
        )

    async def _create(self, org_id, *, name, slug=None, description=None, **kw):  # noqa: ANN202, ANN003
        self.calls.append(("create", org_id, slug))
        role = SimpleNamespace(id=f"role_{slug}", slug=slug, name=name)
        self._roles[str(slug)] = role
        return role

    async def _list_roles(self, org_id, **kw):  # noqa: ANN001, ANN202, ANN003
        return SimpleNamespace(data=list(self._roles.values()))

    def _setperms(self, org_id, slug, *, permissions, **kw):  # noqa: ANN202, ANN003
        self.calls.append(("setperms", org_id, slug, list(permissions)))
        return SimpleNamespace(slug=slug)

    def _list_permissions(self, **kw):  # noqa: ANN202, ANN003
        perms = [SimpleNamespace(slug=s) for s in self._perms]
        return SimpleNamespace(auto_paging_iter=lambda: iter(perms))

    def _create_permission(self, *, slug, name, description=None, **kw):  # noqa: ANN202, ANN003
        self.calls.append(("create_permission", slug))
        self._perms.append(slug)
        return SimpleNamespace(slug=slug)


@pytest.mark.asyncio
async def test_org_role_create_and_set_permissions(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workos = _FakeOrgAuthz()
    tid = (
        await service_session.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name, workos_project_id) "
                "VALUES ('ten_rbac', 'firm-rbac', 'Firm RBAC', 'project_rbac') RETURNING id"
            )
        )
    ).scalar_one()
    # One-firm-org (0108): every role lives on the tenant's SINGLE firm org, edited via that
    # environment's WorkOS client — resolved through environments.firm_org_and_client.
    await service_session.execute(
        text(
            "INSERT INTO auth.tenant_environments "
            "  (tenant_id, stage, workos_client_id, firm_org_id) "
            "VALUES (:t, 'production', 'client_rbac', 'org_rbac')"
        ),
        {"t": tid},
    )

    async def _fake_firm_org(
        _session: AsyncSession, *, tenant_id: int
    ) -> tuple[str, _FakeOrgAuthz]:
        return "org_rbac", workos

    monkeypatch.setattr(environments, "firm_org_and_client", _fake_firm_org)

    # a role WITH read-all (the per-firm "see all" grant)
    await create_org_role(
        service_session,
        tenant_id=tid,
        name="Adviser",
        slug="adviser",
        portal="platform",
        permission_slugs=["contacts:read_all"],
    )
    roles = await list_org_roles(service_session, tid)
    adviser = next(r for r in roles if r["slug"] == "adviser")
    assert adviser["permissions"] == ["contacts:read_all"]
    assert adviser["portal"] == "platform"
    # WorkOS gets the required org- prefix; the mirror keeps the clean slug.
    assert ("create", "org_rbac", "org-adviser") in workos.calls
    # Permissions are deliberately NEVER pushed to WorkOS (embedding them in access tokens is
    # what pushed some sealed session cookies past the browser's cookie ceiling) — only the role
    # object itself is created there.
    assert not any(call[0] in ("setperms", "create_permission") for call in workos.calls)

    # revoke it
    await set_org_role_permissions(
        service_session, tenant_id=tid, role_slug="adviser", permission_slugs=[]
    )
    roles = await list_org_roles(service_session, tid)
    assert next(r for r in roles if r["slug"] == "adviser")["permissions"] == []


@pytest.mark.asyncio
async def test_model_tenant_roles_are_mirror_only(service_session: AsyncSession) -> None:
    """The model tenant has no WorkOS project, so role edits touch only the mirror — no WorkOS
    client is resolved (a real one would raise, since there's no environment)."""
    model_id = (
        await service_session.execute(text("SELECT id FROM auth.tenants WHERE is_model LIMIT 1"))
    ).scalar_one()

    await create_org_role(
        service_session,
        tenant_id=model_id,
        name="Adviser",
        slug="adviser",
        portal="platform",
        permission_slugs=["contacts:read_all"],
    )
    roles = await list_org_roles(service_session, model_id)
    adviser = next(r for r in roles if r["slug"] == "adviser")
    assert adviser["permissions"] == ["contacts:read_all"]
    # mirror-only: no workos_role_id was recorded
    wid = (
        await service_session.execute(
            text("SELECT workos_role_id FROM auth.roles WHERE tenant_id = :t AND slug = 'adviser'"),
            {"t": model_id},
        )
    ).scalar_one()
    assert wid is None
