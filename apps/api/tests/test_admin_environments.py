"""Tenant WorkOS-environment plumbing behind the admin surface.

Two behaviours shipped as fixes for real 409/LookupError reports and were never tested:

* ``push_roles_to_workos`` resolves its client through ``client_for_env``, so the platform's OWN
  core project — whose key comes from settings, not a stored ciphertext — reconciles too. Before
  that, "Sync from WorkOS" on the operations tenant raised
  ``LookupError("environment has no stored WorkOS api key")``.
* It pushes EVERY role — operations included — onto the ONE firm org (one-firm-org, 0096: portals
  are applications, never orgs), and a role whose WorkOS role already exists is not an error.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from workos._errors import ConflictError

from app import environments
from app.config import settings
from app.provisioning import provision_tenant

CORE_CLIENT_ID = "client_core_project"


class _FakeAuthz:
    """Records the org/slug pairs roles were pushed to, and can be told to already hold some.

    `conflict_on` is what the org ALREADY has. The push adopts before it creates, so those slugs
    come back from the listing and are never created -- and a duplicate create still raises
    `ConflictError`, which is what the live API answers (`organization_role_slug_conflict`), so the
    race path stays exercised too.
    """

    def __init__(self, *, conflict_on: set[str] | None = None) -> None:
        self.created: list[tuple[str, str]] = []
        self.permissions_set: list[tuple[str, str, list[str]]] = []
        self._conflict_on = conflict_on or set()
        self._roles: dict[tuple[str, str], SimpleNamespace] = {}

    # -- roles -------------------------------------------------------------
    async def create_organization_role(self, org, *, name, slug, description=None):  # noqa: ANN001, ANN202
        if slug in self._conflict_on or (str(org), str(slug)) in self._roles:
            raise ConflictError()
        self.created.append((org, slug))
        # `auth.roles.workos_role_id` is globally UNIQUE, so the fake id has to be too.
        role = SimpleNamespace(id=f"role_{org}_{slug}", slug=slug, name=name)
        self._roles[(str(org), str(slug))] = role
        return role

    async def list_organization_roles(self, org, **kw):  # noqa: ANN001, ANN202, ANN003
        pre_existing = [
            SimpleNamespace(id=f"role_{org}_{s}", slug=s, name=s) for s in sorted(self._conflict_on)
        ]
        mine = [r for (o, _s), r in self._roles.items() if o == str(org)]
        return SimpleNamespace(data=pre_existing + mine)

    async def set_organization_role_permissions(self, org, slug, *, permissions):  # noqa: ANN001, ANN202
        self.permissions_set.append((org, slug, list(permissions)))


class _FakeWorkOS:
    def __init__(self, authz: _FakeAuthz) -> None:
        self.authorization = authz


async def _tenant_with_firm_org(session: AsyncSession, *, alias: str, client_id: str) -> int:
    tenant = await provision_tenant(session, name=alias, alias=alias, from_model=False)
    tid = int(tenant["id"])
    await session.execute(
        text(
            "INSERT INTO auth.tenant_environments "
            "  (tenant_id, stage, workos_client_id, firm_org_id) "
            "VALUES (:t, 'production', :cid, :org)"
        ),
        {"t": tid, "cid": client_id, "org": f"org_firm_{tid}"},
    )
    return tid


@pytest.mark.asyncio
async def test_client_for_env_uses_the_core_key_for_the_platforms_own_project(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core project's key lives in settings, not in `auth.environment_secrets`. Routing it
    through `env_workos_client` (which insists on stored ciphertext) is what made the operations
    tenant's sync raise LookupError."""
    monkeypatch.setattr(settings, "workos_client_id", CORE_CLIENT_ID)
    core = _FakeWorkOS(_FakeAuthz())
    monkeypatch.setattr(environments, "core_workos_client", lambda: core)

    tid = await _tenant_with_firm_org(service_session, alias="core-co", client_id=CORE_CLIENT_ID)
    env_id = await environments.primary_environment_id(service_session, tid)
    # No stored secrets at all -- the core branch must not need them.
    assert await environments.client_for_env(service_session, env_id) is core

    # A tenant project (different client id) still goes to the stored-key path.
    other = await _tenant_with_firm_org(
        service_session, alias="tenant-project-co", client_id="client_tenant_project"
    )
    other_env = await environments.primary_environment_id(service_session, other)
    with pytest.raises(LookupError):
        await environments.client_for_env(service_session, other_env)


@pytest.mark.asyncio
async def test_push_roles_sends_every_role_to_the_one_firm_org(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One-firm-org: EVERY role — client, platform and operations alike — lands on the tenant's
    single firm org. Portals are applications now; a role's portal lives only in the mirror."""
    monkeypatch.setattr(settings, "workos_client_id", CORE_CLIENT_ID)
    authz = _FakeAuthz()
    monkeypatch.setattr(environments, "core_workos_client", lambda: _FakeWorkOS(authz))

    tid = await _tenant_with_firm_org(service_session, alias="push-co", client_id=CORE_CLIENT_ID)
    await service_session.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) VALUES "
            "  (:t, 'adviser', 'Adviser', 'organisation', 'platform'), "
            "  (:t, 'client', 'Client', 'organisation', 'client'), "
            "  (:t, 'ops', 'Ops', 'organisation', 'operations')"
        ),
        {"t": tid},
    )
    pushed = await environments.push_roles_to_workos(service_session, tid)
    assert pushed == 3
    assert set(authz.created) == {
        (f"org_firm_{tid}", "org-adviser"),
        (f"org_firm_{tid}", "org-client"),
        (f"org_firm_{tid}", "org-ops"),
    }
    # The mirror records the WorkOS role id, so a later reconcile can match them up.
    ids = list(
        (
            await service_session.execute(
                text("SELECT workos_role_id FROM auth.roles WHERE tenant_id = :t ORDER BY slug"),
                {"t": tid},
            )
        )
        .scalars()
        .all()
    )
    assert ids == [
        f"role_org_firm_{tid}_org-adviser",
        f"role_org_firm_{tid}_org-client",
        f"role_org_firm_{tid}_org-ops",
    ]
    # Roles are pushed; permissions never are (that's what embeds them in access tokens).
    assert authz.permissions_set == []


@pytest.mark.asyncio
async def test_push_roles_is_a_noop_before_the_firm_org_exists(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half-configured is the normal state between registering an environment and creating its
    firm org; with no org there is nowhere to push, so the answer is 0 — never a crash."""
    monkeypatch.setattr(settings, "workos_client_id", CORE_CLIENT_ID)
    authz = _FakeAuthz()
    monkeypatch.setattr(environments, "core_workos_client", lambda: _FakeWorkOS(authz))

    tenant = await provision_tenant(
        service_session, name="Half Push", alias="half-push", from_model=False
    )
    tid = int(tenant["id"])
    await service_session.execute(
        text(
            "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id) "
            "VALUES (:t, 'production', :cid)"
        ),
        {"t": tid, "cid": CORE_CLIENT_ID},
    )
    await service_session.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) VALUES "
            "  (:t, 'adviser', 'Adviser', 'organisation', 'platform'), "
            "  (:t, 'client', 'Client', 'organisation', 'client')"
        ),
        {"t": tid},
    )
    assert await environments.push_roles_to_workos(service_session, tid) == 0
    assert authz.created == []


@pytest.mark.asyncio
async def test_push_roles_is_idempotent_when_the_workos_role_already_exists(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-push is the normal case (it runs on every key change and every sync). An existing
    WorkOS role is ADOPTED rather than surfacing as a 409 'already in use' — and, whether or not it
    already existed, its permissions are never pushed (that's what embeds them in access tokens;
    see push_roles_to_workos's own docstring).

    Adopted, not merely skipped: the push used to `except ConflictError: pass`, which is fine as
    far as WorkOS goes but left `workos_role_id` NULL in the mirror forever for every role that
    predated the push. Observed live on a real tenant — four roles carried ids and `admin`, the one
    that already existed in WorkOS, was still NULL."""
    monkeypatch.setattr(settings, "workos_client_id", CORE_CLIENT_ID)
    authz = _FakeAuthz(conflict_on={"org-adviser"})
    monkeypatch.setattr(environments, "core_workos_client", lambda: _FakeWorkOS(authz))

    tid = await _tenant_with_firm_org(service_session, alias="again-co", client_id=CORE_CLIENT_ID)
    role_id = (
        await service_session.execute(
            text(
                "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
                "VALUES (:t, 'adviser', 'Adviser', 'organisation', 'platform') RETURNING id"
            ),
            {"t": tid},
        )
    ).scalar_one()
    await service_session.execute(
        text(
            "INSERT INTO auth.role_permissions (role_id, permission_id) "
            "SELECT :r, id FROM auth.permissions WHERE slug = 'members:read'"
        ),
        {"r": role_id},
    )

    assert await environments.push_roles_to_workos(service_session, tid) == 1
    assert authz.created == []  # adopted from the listing, never re-created
    assert authz.permissions_set == []  # never pushed, pre-existing or not

    # The half a swallowed conflict could not do: the mirror now knows the role's WorkOS id, so
    # the next grant needs no vendor call to name it.
    stored = (
        await service_session.execute(
            text("SELECT workos_role_id FROM auth.roles WHERE id = :r"), {"r": role_id}
        )
    ).scalar_one()
    assert stored == f"role_org_firm_{tid}_org-adviser", stored
