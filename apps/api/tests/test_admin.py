"""Tenant + member provisioning: the WorkOS write-through + mirror, and the system guard.

WorkOS is faked (no live calls); the mirror writes hit the real testcontainer DB via the
service_role session. Since 0119 a login is intentionally either firm staff or a client profile;
the database refuses a membership on the other side of that boundary. WorkOS still holds one firm
organisation membership per person, while the mirror owns the portal and multi-role set.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from workos._errors import BadRequestError, WorkOSError

from app import environments
from app.auth import Principal, get_system_principal, require_permission
from app.ids import new_id
from app.provisioning import (
    deactivate_member,
    grant_portal_access,
    members_with_portals,
    provision_client_login,
    provision_tenant,
    reactivate_member,
    resend_member_invite,
    revoke_member_invitation,
    revoke_portal_access,
    send_member_password_reset,
    set_member_role,
    set_member_roles,
    sync_tenant,
    update_tenant,
)


# ---- the system guard (no DB) ----
def test_system_guard_rejects_non_system() -> None:
    with pytest.raises(HTTPException) as exc:
        get_system_principal(principal=Principal(user_id="u_1", role="adviser"))
    assert exc.value.status_code == 403


def test_system_guard_allows_system() -> None:
    """System is a PERMISSION, never the free-text `role` claim: a firm that names one of its own
    WorkOS roles "admin" must not thereby hold cross-tenant superuser (app.auth.is_system)."""
    p = Principal(user_id="u_1", role="adviser", permissions=["system"])
    assert get_system_principal(principal=p) is p
    with pytest.raises(HTTPException):
        get_system_principal(principal=Principal(user_id="u_2", role="admin"))


def test_system_guard_takes_only_the_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    """The break-glass allowlist is DELETED: the only system signal is the `system` permission
    on the principal (DB-sourced from an operations role)."""
    from app.config import settings

    assert not hasattr(settings, "system_user_ids")
    p = Principal(user_id="user_ops", permissions=["system"])
    assert get_system_principal(principal=p) is p
    # anyone without the permission is rejected, whatever their role is named
    with pytest.raises(HTTPException):
        get_system_principal(principal=Principal(user_id="user_other", role="adviser"))


def test_require_permission_gates_and_write_implies_read() -> None:
    """An action needs the permission on the token; write implies read; system bypasses."""
    writer = Principal(user_id="u_1", permissions=["members:write"])
    require_permission(writer, "members:write")  # exact
    require_permission(writer, "members:read")  # write implies read

    with pytest.raises(HTTPException) as exc:
        # read does NOT imply write
        require_permission(Principal(user_id="u_2", permissions=["members:read"]), "members:write")
    assert exc.value.status_code == 403

    # A Graphene system principal passes any permission check.
    require_permission(Principal(user_id="u_3", permissions=["system"]), "roles:write")


# ---- a tiny fake WorkOS Management API (record calls, return ids) ----
class _AsyncPage:
    def __init__(self, data: list[object]) -> None:
        self.data = data

    async def auto_paging_iter(self):  # noqa: ANN201
        for item in self.data:
            yield item


class _FakeWorkOS:
    """Stateful enough to model per-portal memberships: tracks users by email/id and memberships
    keyed by (user, org) with a mutable status + role, raising ConflictError on a duplicate."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._users: dict[str, object] = {}
        self._members: dict[tuple[str, str], object] = {}
        self._sessions: dict[str, object] = {}
        self._invitations: dict[str, object] = {}
        # Explicit compatibility refusals: organisation invitations support existing Users, but
        # grant_portal_access still retains its reset-token fallback for this vendor response.
        self.invitation_conflicts: set[str] = set()
        # Refuse every `reactivate` as pending, whatever the membership's own status says. The
        # live API did exactly this to a membership it had just answered `inactive` for, and the
        # route it took to that state is not something a double can honestly reconstruct — so it
        # is a switch, used by the test that pins an invite surviving it.
        self.pending_refusal_on_reactivate = False
        self.organizations = SimpleNamespace(
            create_organization=self._create_org, update_organization=self._update_org
        )
        self.user_management = SimpleNamespace(
            create_user=self._create_user,
            list_users=self._list_users,
            get_user=self._get_user,
            reset_password=self._reset_password,
            send_invitation=self._send_invitation,
            resend_invitation=self._resend_invitation,
            get_invitation=self._get_invitation,
            list_invitations=self._list_invitations,
            revoke_invitation=self._revoke_invitation,
            # Revoking access now ends live sessions too (a deactivated membership doesn't
            # invalidate an issued token), so the fake has to model sessions.
            list_sessions=self._list_sessions,
            revoke_session=self._revoke_session,
        )
        self.organization_membership = SimpleNamespace(
            create_organization_membership=self._create_membership,
            list_organization_memberships=self._list_memberships,
            update_organization_membership=self._update_membership,
            deactivate_organization_membership=self._deactivate_membership,
            reactivate_organization_membership=self._reactivate_membership,
        )
        # `grant_portal_access` ensures the org role exists first: WorkOS validates `role_slug`
        # and answers 422 `invalid_role` for one it does not know. The id carries the ORG because
        # a WorkOS role id is globally unique, and `auth.roles.workos_role_id` has a UNIQUE to
        # match -- an id built from the slug alone collides across tenants.
        self._org_roles: dict[tuple[str, str], object] = {}
        self.authorization = SimpleNamespace(
            create_organization_role=self._create_org_role,
            list_organization_roles=self._list_org_roles,
        )

    async def _create_org_role(self, org_id, *, name, slug=None, description=None, **kw):  # noqa: ANN001, ANN202, ANN003
        self.calls.append(("create_organization_role", {"org": org_id, "slug": slug}))
        role = SimpleNamespace(id=f"role_{org_id}_{slug}", slug=slug, name=name)
        self._org_roles[(str(org_id), str(slug))] = role
        return role

    async def _list_org_roles(self, org_id, **kw):  # noqa: ANN001, ANN202, ANN003
        return _AsyncPage(data=[r for (o, _s), r in self._org_roles.items() if o == str(org_id)])

    async def _create_org(self, *, name: str):  # noqa: ANN202
        self.calls.append(("create_org", {"name": name}))
        return SimpleNamespace(id=f"org_{name.replace(' ', '_').lower()}", name=name)

    async def _update_org(self, org_id, *, name=None, **kw):  # noqa: ANN202, ANN003
        self.calls.append(("update_org", {"id": org_id, "name": name}))
        return SimpleNamespace(id=org_id, name=name)

    async def _create_user(self, *, email, first_name=None, last_name=None, **kw):  # noqa: ANN202, ANN003
        if email in self._users:
            raise AssertionError("create_user called for an existing email (should look up)")
        self.calls.append(("create_user", {"email": email}))
        user = SimpleNamespace(
            id=f"user_{email}",
            email=email,
            first_name=first_name,
            last_name=last_name,
            email_verified=False,
        )
        self._users[email] = user
        self._sessions[f"sess_{email}"] = SimpleNamespace(
            id=f"sess_{email}", user_id=user.id, status=SimpleNamespace(value="active")
        )
        return user

    async def _list_users(self, *, email=None, **kw):  # noqa: ANN202, ANN003
        found = self._users.get(email) if email else None
        return SimpleNamespace(data=[found] if found else [])

    async def _get_user(self, user_id):  # noqa: ANN202
        for u in self._users.values():
            if u.id == user_id:  # type: ignore[attr-defined]
                return u
        return SimpleNamespace(id=user_id, email=None, email_verified=False)

    async def _reset_password(self, *, email, **kw):  # noqa: ANN202, ANN003
        self.calls.append(("reset_password", {"email": email}))
        return SimpleNamespace(
            password_reset_token=f"rt_{email}",
            password_reset_url=f"https://auth.example/reset/{email}",
        )

    async def _send_invitation(  # noqa: ANN202, ANN003
        self, *, email, organization_id=None, role_slug=None, expires_in_days=None, **kw
    ):
        user = self._users.get(email)
        already_member = bool(
            user and organization_id and (str(user.id), str(organization_id)) in self._members
        )
        if (
            email in self.invitation_conflicts
            or (email in self._users and not organization_id)
            or already_member
        ):
            self.calls.append(("send_invitation", {"email": email, "refused": True}))
            message = "User is already a member" if already_member else "User already exists"
            raise WorkOSError(message)
        invitation_id = f"inv_{len(self._invitations) + 1}"
        invitation = SimpleNamespace(
            id=invitation_id,
            email=email,
            token=f"it_{invitation_id}",
            state=SimpleNamespace(value="pending"),
            expires_at=datetime.now(UTC) + timedelta(days=expires_in_days or 7),
            accept_invitation_url=f"https://auth.example/invite/{invitation_id}",
        )
        self._invitations[invitation_id] = invitation
        # An ORGANISATION invitation is what puts a membership in `pending` ("pending, when a user
        # is invited to an organization" -- WorkOS). The fake used to mint the invitation and
        # nothing else, so no test could ever hold a pending membership, and the two vendor rules
        # that hang off that state were unobservable: a pending membership refuses BOTH
        # `deactivate` and `reactivate`. That is why an invite to a client whose membership WorkOS
        # considered pending died on `cannot_reactivate_pending_organization_membership` in staging
        # with the suite green.
        if organization_id and user is not None:
            existing = self._members.get((str(user.id), str(organization_id)))
            if existing is None:
                self._members[(str(user.id), str(organization_id))] = SimpleNamespace(
                    id=f"om_{user.id}_{organization_id}",
                    organization_id=str(organization_id),
                    user_id=str(user.id),
                    status=SimpleNamespace(value="pending"),
                    role=SimpleNamespace(slug=role_slug),
                    invitation_id=invitation_id,
                )
            else:
                existing.status = SimpleNamespace(value="pending")
                existing.invitation_id = invitation_id
        self.calls.append(
            (
                "send_invitation",
                {
                    "email": email,
                    "organization_id": organization_id,
                    "role_slug": role_slug,
                    "expires_in_days": expires_in_days,
                },
            )
        )
        return invitation

    async def _resend_invitation(self, invitation_id, **kw):  # noqa: ANN001, ANN003, ANN202
        invitation = self._invitations[invitation_id]
        if str(invitation.state.value).lower() != "pending":
            raise WorkOSError("Invitation is not pending")
        invitation.token = f"it_{invitation_id}_resent"
        invitation.accept_invitation_url = f"https://auth.example/invite/{invitation_id}/resent"
        self.calls.append(("resend_invitation", {"id": invitation_id}))
        return invitation

    async def _list_invitations(self, *, email=None, **kw):  # noqa: ANN001, ANN003, ANN202
        """`grant_portal_access` sweeps by ADDRESS before minting: the stored invitation id
        covers only someone already mirrored, and WorkOS refuses a second pending invitation
        for the same email."""
        self.calls.append(("list_invitations", {"email": email}))
        data = [i for i in self._invitations.values() if email is None or i.email == email]
        return _AsyncPage(data)

    async def _get_invitation(self, invitation_id):  # noqa: ANN202
        self.calls.append(("get_invitation", {"id": invitation_id}))
        return self._invitations[invitation_id]

    async def _revoke_invitation(self, invitation_id):  # noqa: ANN202
        self.calls.append(("revoke_invitation", {"id": invitation_id}))
        invitation = self._invitations[invitation_id]
        invitation.state = SimpleNamespace(value="revoked")
        # A membership held `pending` purely by this invitation goes with it — there is no longer
        # an invitation for anyone to accept. An ACCEPTED membership (active/inactive) is
        # untouched: revoking is only ever done to a pending invitation.
        for key, m in list(self._members.items()):
            if (
                getattr(m, "invitation_id", None) == invitation_id
                and str(m.status.value).lower() == "pending"
            ):
                del self._members[key]
        return invitation

    def accept_invitation(self, email: str) -> None:
        """Model the person ACCEPTING their invitation: the membership becomes active and the
        email becomes verified.

        A test that needs an `inactive` membership has to go through here first, because the
        vendor has no other route to one: a membership is `pending` from the invitation until it
        is accepted, and a pending membership cannot be deactivated at all.
        """
        user = self._users[email]
        user.email_verified = True  # type: ignore[attr-defined]
        for m in self._members.values():
            if m.user_id == user.id:  # type: ignore[attr-defined]
                m.status = SimpleNamespace(value="active")  # type: ignore[attr-defined]
        for invitation in self._invitations.values():
            if invitation.email == email and str(invitation.state.value).lower() == "pending":
                invitation.state = SimpleNamespace(value="accepted")

    async def _list_sessions(self, user_id, **kw):  # noqa: ANN202, ANN003
        data = [s for s in self._sessions.values() if s.user_id == user_id]
        return _AsyncPage(data)

    async def _revoke_session(self, *, session_id, **kw):  # noqa: ANN202, ANN003
        self.calls.append(("revoke_session", {"session_id": session_id}))
        self._sessions.pop(session_id, None)

    async def _create_membership(self, *, user_id, organization_id, role=None):  # noqa: ANN202
        """An UPSERT on (user, organisation) -- which is what the live API does.

        This fake used to `raise ConflictError()` on a duplicate, and the production code was
        written to that: an `except ConflictError:` branch held the only code that set the role and
        reactivated an inactive membership. Checked against the real API and it does not raise --
        it answers with the EXISTING membership, id and all. So the branch never ran, the
        reconcile never happened, and the suite was green because the double asserted a contract
        the vendor does not have. Corrected here, and `grant_portal_access` now reconciles
        unconditionally from whatever the create returned.

        **The upsert REACTIVATES, so it refuses on a PENDING membership** rather than echoing it
        back — the live API answers 400 `cannot_reactivate_pending_organization_membership`. That
        is the ordinary state of a re-invite, and modelling it as a plain echo is what let a bug
        reach staging twice: the invite had already revoked the old invitation and minted a new one
        by the time the create ran, so the refusal killed the invite and the firm was shown the
        vendor's wording. The WorkOS event log's signature was `invitation.revoked` +
        `invitation.created` pairs with no `organization_membership.*` event between them.
        """
        self.calls.append(
            ("create_membership", {"user_id": user_id, "org": organization_id, "role": role})
        )
        slug = getattr(role, "role_slug", None)
        existing = self._members.get((user_id, organization_id))
        if existing is not None:
            if str(existing.status.value).lower() == "pending":
                raise self._pending_refusal("reactivated")
            return existing
        m = SimpleNamespace(
            id=f"om_{user_id}_{organization_id}",
            organization_id=organization_id,
            user_id=user_id,
            status=SimpleNamespace(value="active"),
            role=SimpleNamespace(slug=slug),
        )
        self._members[(user_id, organization_id)] = m
        return m

    def _find_by_id(self, membership_id):  # noqa: ANN202
        for m in self._members.values():
            if m.id == membership_id:  # type: ignore[attr-defined]
                return m
        return None

    async def _update_membership(self, membership_id, *, role=None, **kw):  # noqa: ANN202, ANN003
        self.calls.append(("update_membership", {"id": membership_id, "role": role}))
        m = self._find_by_id(membership_id)
        if m is not None and role is not None:
            m.role = SimpleNamespace(slug=getattr(role, "role_slug", None))
        return m or SimpleNamespace(id=membership_id)

    async def _deactivate_membership(self, membership_id, **kw):  # noqa: ANN202, ANN003
        self.calls.append(("deactivate_membership", {"id": membership_id}))
        m = self._find_by_id(membership_id)
        if m is not None and str(m.status.value).lower() == "pending":
            # The vendor's own rule: a pending membership cannot be deactivated, it should be
            # deleted instead. Carries the documented code so the app can tell this refusal from
            # a real failure.
            raise self._pending_refusal("deactivated")
        if m is not None:
            m.status = SimpleNamespace(value="inactive")
        return m or SimpleNamespace(id=membership_id)

    async def _reactivate_membership(self, membership_id, **kw):  # noqa: ANN202, ANN003
        self.calls.append(("reactivate_membership", {"id": membership_id}))
        m = self._find_by_id(membership_id)
        if self.pending_refusal_on_reactivate or (
            m is not None and str(m.status.value).lower() == "pending"
        ):
            raise self._pending_refusal("reactivated")
        if m is not None:
            m.status = SimpleNamespace(value="active")
        return m or SimpleNamespace(id=membership_id)

    @staticmethod
    def _pending_refusal(verb: str) -> BadRequestError:
        """The vendor's 400 for acting on a pending membership — message AND code, because the app
        matches on either (WorkOS has shipped two wordings for it)."""
        prefix = "re" if verb == "reactivated" else "de"
        return BadRequestError(
            f"Pending organization memberships cannot be {verb}.",
            code=f"cannot_{prefix}activate_pending_organization_membership",
            status_code=400,
        )

    async def _list_memberships(self, *, user_id=None, organization_id=None, **kw):  # noqa: ANN202, ANN003
        data = [
            m
            for (u, o), m in self._members.items()
            if (user_id is None or u == user_id)
            and (organization_id is None or o == organization_id)
        ]
        return _AsyncPage(data)


async def _register_env(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    workos: _FakeWorkOS,
    *,
    tenant_id: int,
) -> str:
    """Simulate registering the tenant's WorkOS environment (project-per-tenant, one-firm-org):
    every member authenticates into the ONE firm org; which portals they may use is the mirror
    membership row, classified by the portal APPLICATION client ids. Routes env_workos_client to
    the fake and returns the firm org id."""
    worg = f"org_firm_{tenant_id}"
    await session.execute(
        text(
            "INSERT INTO auth.tenant_environments "
            "  (tenant_id, stage, workos_client_id, firm_org_id, "
            "   client_portal_client_id, platform_client_id) "
            "VALUES (:t, 'production', 'client_test', :org, :capp, :papp)"
        ),
        {
            "t": tenant_id,
            "org": worg,
            "capp": f"app_client_{tenant_id}",
            "papp": f"app_platform_{tenant_id}",
        },
    )
    await session.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) VALUES "
            "  (:t, 'adviser', 'Adviser', 'organisation', 'platform'), "
            "  (:t, 'compliance', 'Compliance', 'organisation', 'platform'), "
            "  (:t, 'client', 'Client', 'organisation', 'client') "
            "ON CONFLICT (tenant_id, slug) DO NOTHING"
        ),
        {"t": tenant_id},
    )

    async def _fake_client(_session: AsyncSession, _env_id: int) -> _FakeWorkOS:
        return workos

    monkeypatch.setattr(environments, "env_workos_client", _fake_client)
    return worg


async def _user_id_for(session: AsyncSession, email: str) -> str:
    return str(
        (
            await session.execute(text("SELECT id FROM auth.users WHERE email = :e"), {"e": email})
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_provision_tenant_creates_no_workos_org(service_session: AsyncSession) -> None:
    """Project-per-tenant: creating a tenant is a local row only — no WorkOS org."""
    tenant = await provision_tenant(service_session, name="No Org Co", alias="no-org-co")
    assert tenant["workos_project_id"] is None


@pytest.mark.asyncio
async def test_sync_tenant_upserts_people_and_maps_status_onto_existing_rows(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reconcile is PERSON-level now (one-firm-org): it lists the FIRM org's memberships,
    upserts the USERS, and maps the WorkOS state onto the person's EXISTING mirror rows — it never
    invents a portal row, because WHICH portals a person may use is ours, not WorkOS's."""
    tenant = await provision_tenant(
        service_session, name="Sync Co", alias="sync-co", from_model=False
    )
    tid = int(tenant["id"])
    worg = f"org_firm_{tid}"
    await service_session.execute(
        text(
            "INSERT INTO auth.tenant_environments "
            "  (tenant_id, stage, workos_client_id, firm_org_id) "
            "VALUES (:t, 'production', 'c', :org)"
        ),
        {"t": tid, "org": worg},
    )

    member = SimpleNamespace(
        id="om_1",
        status="active",
        user=SimpleNamespace(
            id="user_x", email="x@sync.test", first_name="X", last_name="Y", email_verified=True
        ),
    )

    class _SyncFake:
        async def _list_memberships(self, **kw: object) -> _AsyncPage:
            return _AsyncPage([member] if kw.get("organization_id") == worg else [])

        def __init__(self) -> None:
            self.organization_membership = SimpleNamespace(
                list_organization_memberships=self._list_memberships
            )

    async def _fake_client(_s: AsyncSession, _e: int) -> _SyncFake:
        return _SyncFake()

    async def _noop(_s: AsyncSession, _t: int) -> int:
        return 0

    monkeypatch.setattr(environments, "env_workos_client", _fake_client)
    monkeypatch.setattr(environments, "push_roles_to_workos", _noop)

    result = await sync_tenant(service_session, tid)
    # `imported` and `without_portal` are the numbers that make a successful sync legible. The
    # reconcile never invents a portal row (asserted below), so importing people and granting them
    # nothing is the CORRECT outcome -- and reporting only `members` made it indistinguishable
    # from a no-op. A real sync imported 26 people and the operator was told "synced".
    assert result == {"members": 1, "imported": 1, "without_portal": 1}
    row = (
        (
            await service_session.execute(
                text("SELECT email, enabled, workos_user_id FROM auth.users WHERE tenant_id = :t"),
                {"t": tid},
            )
        )
        .mappings()
        .one()
    )
    assert row["email"] == "x@sync.test"
    assert row["enabled"] is True
    assert row["workos_user_id"] == "user_x"
    # No portal row was invented for the person.
    assert (
        await service_session.execute(
            text("SELECT count(*) FROM auth.organisation_memberships WHERE tenant_id = :t"),
            {"t": tid},
        )
    ).scalar_one() == 0

    # They are explicitly unclassified: visible to the unfiltered operations directory, but absent
    # from both Team and Profiles until a deliberate grant claims their kind.
    listed = await members_with_portals(service_session, tid)
    assert [m["email"] for m in listed] == ["x@sync.test"], listed
    assert listed[0]["portals"] == [], listed[0]
    assert listed[0]["types"] == []
    assert listed[0]["type"] == []
    assert listed[0]["access_kind"] == "unclassified"
    assert await members_with_portals(service_session, tid, access_kind="staff") == []

    # A person who onboarded (verified) gets their EXISTING pending row promoted to active…
    uid = (
        await service_session.execute(
            text("SELECT id FROM auth.users WHERE tenant_id = :t"), {"t": tid}
        )
    ).scalar_one()
    await service_session.execute(
        text("UPDATE auth.users SET access_kind = 'staff' WHERE id = :u"), {"u": uid}
    )
    await service_session.execute(
        text(
            "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, status) "
            "VALUES (:u, :t, 'platform', 'pending')"
        ),
        {"u": uid, "t": tid},
    )
    await sync_tenant(service_session, tid)
    assert (
        await service_session.execute(
            text("SELECT status FROM auth.organisation_memberships WHERE user_id = :u"),
            {"u": uid},
        )
    ).scalar_one() == "active"

    # …and a deactivated WorkOS membership takes the person's EVERY portal row inactive.
    member.status = "inactive"
    await sync_tenant(service_session, tid)
    assert (
        await service_session.execute(
            text("SELECT status FROM auth.organisation_memberships WHERE user_id = :u"),
            {"u": uid},
        )
    ).scalar_one() == "inactive"


@pytest.mark.asyncio
async def test_provision_tenant_clones_model_roles(service_session: AsyncSession) -> None:
    """A tenant created from the model clones its roles; a blank one starts with none."""
    model_id = (
        await service_session.execute(text("SELECT id FROM auth.tenants WHERE is_model LIMIT 1"))
    ).scalar_one()
    await service_session.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
            "VALUES (:m, 'adviser', 'Adviser', 'organisation', 'platform') "
            "ON CONFLICT (tenant_id, slug) DO NOTHING"
        ),
        {"m": model_id},
    )
    cloned = await provision_tenant(service_session, name="Cloned Co", alias="cloned-co")
    blank = await provision_tenant(
        service_session, name="Blank Co", alias="blank-co", from_model=False
    )
    cloned_roles = (
        (
            await service_session.execute(
                text("SELECT slug FROM auth.roles WHERE tenant_id = :t"), {"t": cloned["id"]}
            )
        )
        .scalars()
        .all()
    )
    blank_roles = (
        (
            await service_session.execute(
                text("SELECT slug FROM auth.roles WHERE tenant_id = :t"), {"t": blank["id"]}
            )
        )
        .scalars()
        .all()
    )
    assert "adviser" in cloned_roles
    assert blank_roles == []


@pytest.mark.asyncio
async def test_invite_member_requires_environment(service_session: AsyncSession) -> None:
    """Inviting into a tenant with no environment set fails clearly, not with a WorkOS error."""
    tenant = await provision_tenant(
        service_session, name="Pending Co", alias="pending-co", from_model=False
    )
    await service_session.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
            "VALUES (:t, 'adviser', 'Adviser', 'organisation', 'platform')"
        ),
        {"t": int(tenant["id"])},
    )
    with pytest.raises(LookupError):
        await grant_portal_access(
            service_session,
            tenant_id=int(tenant["id"]),
            role_slug="adviser",
            email="x@pending.test",
        )


@pytest.mark.asyncio
async def test_invite_member(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="Acme Advice", alias="acme")
    await _register_env(service_session, monkeypatch, workos, tenant_id=int(tenant["id"]))
    result = await grant_portal_access(
        service_session,
        tenant_id=int(tenant["id"]),
        role_slug="adviser",
        email="adviser@acme.test",
        first_name="Ada",
    )

    # Self-hosted onboarding: user + membership created + a revocable WorkOS invitation minted.
    # The role's portal (platform) is chosen automatically.
    assert result["portal"] == "platform"
    assert any(c[0] == "create_user" for c in workos.calls)
    assert any(c[0] == "create_membership" for c in workos.calls)
    assert any(c[0] == "send_invitation" for c in workos.calls)
    assert not any(c[0] == "reset_password" for c in workos.calls)
    invitation_call = next(c[1] for c in workos.calls if c[0] == "send_invitation")
    assert invitation_call["organization_id"] == f"org_firm_{tenant['id']}"
    assert invitation_call["role_slug"] == "org-adviser"
    # An invitation link was minted, but the URL itself is a live credential and is withheld
    # from the response unless the caller explicitly asks to reveal it.
    assert result["invitation_sent"] is True
    assert result["invitation_url"] is None

    row = (
        await service_session.execute(
            text("SELECT enabled, workos_user_id FROM auth.users WHERE email = 'adviser@acme.test'")
        )
    ).one()
    assert row.enabled is False
    assert row.workos_user_id == "user_adviser@acme.test"
    status = (
        await service_session.execute(
            text(
                "SELECT status FROM auth.organisation_memberships "
                "WHERE tenant_id = :t AND portal = 'platform'"
            ),
            {"t": tenant["id"]},
        )
    ).scalar_one()
    assert status == "pending"


@pytest.mark.asyncio
async def test_the_invite_email_wears_the_firms_envelope_identity(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An invite must arrive FROM the firm, not merely look like it.

    Both invite paths dressed the template in the firm's brand and then took their ``From`` from
    ``email.sender_for(tenant_id)`` — a read of a cache only ``resolve_sender`` fills, which nothing
    on these paths ever called. So the miss was total and silent: every invite went out as Graphene,
    with the firm's reply address dropped. A branded mail from a stranger's address reads as
    phishing, which is why the reset path already resolves properly (``routers/internal.py``).
    """
    from app.email import transport as email_mod

    email_mod.clear_caches()
    monkeypatch.setattr(email_mod.settings, "email_from", "Graphene <no-reply@graphene.test>")
    sends: list[dict[str, object]] = []

    async def _capture(**kwargs: object) -> bool:
        sends.append(kwargs)
        return True

    monkeypatch.setattr(email_mod, "send_email", _capture)

    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="Ridley & Co", alias="ridley")
    tid = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)
    # The firm's own support address, off the verified sending domain — so it becomes the Reply-To
    # while the verified address stays as the From, wearing the firm's display name.
    await service_session.execute(
        text("UPDATE auth.tenants SET theme = CAST(:theme AS jsonb) WHERE id = :t"),
        {
            "t": tid,
            "theme": json.dumps(
                {
                    "client": {"support": {"email": "hello@ridley.test"}},
                    "platform": {"support": {"email": "staff@ridley.test"}},
                }
            ),
        },
    )

    result = await grant_portal_access(
        service_session, tenant_id=tid, role_slug="adviser", email="new@ridley.test"
    )
    assert result["invitation_sent"] is True
    assert sends[-1]["sender"] == '"Ridley & Co" <no-reply@graphene.test>'
    assert sends[-1]["reply_to"] == "staff@ridley.test"  # a staff invite: platform branding

    # A resend is the same obligation on the other code path.
    sends.clear()
    email_mod.clear_caches()
    await resend_member_invite(
        service_session, tenant_id=tid, user_id=str(result["user_id"]), portal="platform"
    )
    assert sends[-1]["sender"] == '"Ridley & Co" <no-reply@graphene.test>'
    assert sends[-1]["reply_to"] == "staff@ridley.test"
    email_mod.clear_caches()


@pytest.mark.asyncio
async def test_staff_identity_cannot_be_granted_client_access(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The staff/profile split is an API and database boundary, not two hidden UI switches."""
    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="Dual Co", alias="dual")
    tid = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)

    await grant_portal_access(
        service_session, tenant_id=tid, role_slug="adviser", email="dual@x.test"
    )
    uid = await _user_id_for(service_session, "dual@x.test")
    with pytest.raises(LookupError, match="staff identity"):
        await grant_portal_access(
            service_session, tenant_id=tid, user_id=uid, portal="client", role_slug="client"
        )

    people = await members_with_portals(service_session, tid, access_kind="staff")
    assert len(people) == 1
    assert people[0]["access_kind"] == "staff"
    assert [p["portal"] for p in people[0]["portals"]] == ["platform"]
    assert await members_with_portals(service_session, tid, access_kind="profile") == []


@pytest.mark.asyncio
async def test_database_refuses_cross_kind_memberships(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bypassing provisioning must not bypass the staff/profile boundary."""
    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="Hard Boundary", alias="hard-boundary")
    tid = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)
    result = await grant_portal_access(
        service_session, tenant_id=tid, role_slug="client", email="profile@hard.test"
    )

    with pytest.raises(IntegrityError, match="only staff identities"):
        async with service_session.begin_nested():
            await service_session.execute(
                text(
                    "INSERT INTO auth.organisation_memberships "
                    "(user_id, tenant_id, portal, status) "
                    "VALUES (:u, :t, 'platform', 'active')"
                ),
                {"u": result["user_id"], "t": tid},
            )


@pytest.mark.asyncio
async def test_a_membership_role_may_drift_between_staff_portals_but_not_across_the_boundary(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scope rule on `auth.membership_roles` is the client/staff boundary, not the portal.

    0119 required an exact portal match and 0120 relaxed it deliberately (see its header): a
    platform membership carrying an operations role is real pre-existing data, both roles are firm
    STAFF surfaces, and refusing it at migration time could only decline to backfill -- which is
    what took every staff session's permissions away. The crossing that actually escalates is a
    client membership reaching a staff role, because `auth.member_permissions` unions whatever it
    is given into the session."""
    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="Drift Co", alias="drift")
    tid = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)

    staff = await grant_portal_access(
        service_session, tenant_id=tid, role_slug="adviser", email="staff@drift.test"
    )
    profile = await grant_portal_access(
        service_session, tenant_id=tid, role_slug="client", email="client@drift.test"
    )
    ops_role = (
        await service_session.execute(
            text(
                "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
                "VALUES (:t, 'ops-admin', 'Ops Admin', 'organisation', 'operations') RETURNING id"
            ),
            {"t": tid},
        )
    ).scalar_one()
    adviser_role = (
        await service_session.execute(
            text("SELECT id FROM auth.roles WHERE tenant_id = :t AND slug = 'adviser'"),
            {"t": tid},
        )
    ).scalar_one()
    memberships = {
        str(row["portal"]): int(row["id"])
        for row in (
            (
                await service_session.execute(
                    text(
                        "SELECT id, portal FROM auth.organisation_memberships "
                        "WHERE tenant_id = :t AND user_id = ANY(:u)"
                    ),
                    {"t": tid, "u": [str(staff["user_id"]), str(profile["user_id"])]},
                )
            )
            .mappings()
            .all()
        )
    }

    # Staff-side drift: allowed, and the permissions land in the session.
    await service_session.execute(
        text("INSERT INTO auth.membership_roles (membership_id, role_id) VALUES (:m, :r)"),
        {"m": memberships["platform"], "r": ops_role},
    )

    # The boundary itself: refused, whichever way it is crossed.
    with pytest.raises(IntegrityError, match="client/staff side"):
        async with service_session.begin_nested():
            await service_session.execute(
                text("INSERT INTO auth.membership_roles (membership_id, role_id) VALUES (:m, :r)"),
                {"m": memberships["client"], "r": adviser_role},
            )


@pytest.mark.asyncio
async def test_a_grandfathered_dual_identity_stays_visible_on_both_pages(
    super_engine: Engine, service_session: AsyncSession
) -> None:
    """An identity that already held both sides when the boundary landed appears on Team AND
    Profiles.

    0121 represents such an identity truthfully as ``['client', 'staff']``, because splitting one
    needs a second address and an invitation that no migration can invent. It appears on both pages
    so an operator can see and split it. It is seeded here with triggers off because that is the
    only way the state exists: every write path refuses to create it, which the assertions at the
    end state."""
    with super_engine.connect() as conn:
        nonce = conn.execute(text("SELECT encode(gen_random_bytes(4), 'hex')")).scalar_one()
        tid = int(
            conn.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"gfa_{nonce}", "a": f"gfa-{nonce}", "n": f"Grandfathered {nonce}"},
            ).scalar_one()
        )
        user = str(
            conn.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:u, :t, :e, :w, true) RETURNING id"
                ),
                {"u": f"gfu_{nonce}", "t": tid, "e": f"dual_{nonce}@gf.test", "w": f"wos_{nonce}"},
            ).scalar_one()
        )
        # Pre-boundary data: written when no trigger existed to refuse it.
        conn.execute(text("SET session_replication_role = replica"))
        conn.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, status) "
                "VALUES (:u, :t, 'platform', 'active'), (:u, :t, 'client', 'active')"
            ),
            {"u": user, "t": tid},
        )
        conn.execute(
            text("UPDATE auth.users SET type = ARRAY['client', 'staff'] WHERE id = :u"),
            {"u": user},
        )
        conn.execute(text("SET session_replication_role = origin"))

    assert (
        await service_session.execute(
            text("SELECT access_kind FROM auth.users WHERE id = :u"), {"u": user}
        )
    ).scalar_one() == "unclassified"

    for kind in ("staff", "profile"):
        listed = await members_with_portals(service_session, tid, access_kind=kind)
        assert [m["email"] for m in listed] == [f"dual_{nonce}@gf.test"], (kind, listed)
        assert listed[0]["types"] == ["client", "staff"]
        assert listed[0]["type"] == ["client", "staff"]
        assert listed[0]["access_kind"] == "unclassified"

    # And it cannot be created going forward: classifying it is what the database refuses.
    with pytest.raises(IntegrityError):
        async with service_session.begin_nested():
            await service_session.execute(
                text("UPDATE auth.users SET access_kind = 'staff' WHERE id = :u"), {"u": user}
            )


@pytest.mark.asyncio
async def test_user_type_is_the_shared_array_contract_without_weakening_the_boundary(
    service_session: AsyncSession,
) -> None:
    """The neighbouring user work can read/write `type`; it cannot mint a new dual identity."""
    nonce = new_id()
    tenant = await provision_tenant(service_session, name="Types", alias=f"types-{nonce}")
    user = (
        await service_session.execute(
            text(
                "INSERT INTO auth.users (uid, tenant_id, email) "
                "VALUES (:uid, :tenant, :email) RETURNING id"
            ),
            {
                "uid": new_id(),
                "tenant": tenant["id"],
                "email": f"{nonce.lower()}@types.test",
            },
        )
    ).scalar_one()

    await service_session.execute(
        text("UPDATE auth.users SET type = ARRAY['client'] WHERE id = :user"),
        {"user": user},
    )
    row = (
        (
            await service_session.execute(
                text("SELECT type, access_kind FROM auth.users WHERE id = :user"),
                {"user": user},
            )
        )
        .mappings()
        .one()
    )
    assert row["type"] == ["client"]
    assert row["access_kind"] == "profile"  # rolling-deploy compatibility

    with pytest.raises(IntegrityError, match="must be split, not made dual"):
        async with service_session.begin_nested():
            await service_session.execute(
                text("UPDATE auth.users SET type = ARRAY['client', 'staff'] WHERE id = :user"),
                {"user": user},
            )


@pytest.mark.asyncio
async def test_resend_member_invite(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="Zeta", alias="zeta")
    await _register_env(service_session, monkeypatch, workos, tenant_id=int(tenant["id"]))
    result = await grant_portal_access(
        service_session, tenant_id=int(tenant["id"]), role_slug="adviser", email="z@zeta.test"
    )
    resent = await resend_member_invite(
        service_session,
        tenant_id=int(tenant["id"]),
        user_id=str(result["user_id"]),
        reveal_link=True,
    )
    assert str(resent["invitation_url"]).endswith("/invite/inv_1/resent")
    assert sum(1 for c in workos.calls if c[0] == "send_invitation") == 1
    assert any(c[0] == "resend_invitation" for c in workos.calls)
    assert not any(c[0] == "reset_password" for c in workos.calls)


@pytest.mark.asyncio
async def test_revoke_member_invitation_is_real_and_tenant_scoped(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="Revoke", alias="revoke")
    tenant_id = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tenant_id)
    member = await grant_portal_access(
        service_session, tenant_id=tenant_id, role_slug="adviser", email="pending@revoke.test"
    )

    result = await revoke_member_invitation(
        service_session, tenant_id=tenant_id, user_id=str(member["user_id"])
    )
    assert result["invitation_revoked"] is True
    assert any(c[0] == "revoke_invitation" for c in workos.calls)
    invitation = (
        await service_session.execute(
            text(
                "SELECT invitation_id, invitation_expires_at FROM auth.users "
                "WHERE id = CAST(:u AS uuid)"
            ),
            {"u": member["user_id"]},
        )
    ).one()
    assert invitation.invitation_id is None
    assert invitation.invitation_expires_at is None

    with pytest.raises(LookupError, match="no pending invitation"):
        await revoke_member_invitation(
            service_session, tenant_id=tenant_id + 1, user_id=str(member["user_id"])
        )


@pytest.mark.asyncio
async def test_member_password_reset_is_tenant_scoped(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workos = _FakeWorkOS()
    tenant = await provision_tenant(
        service_session, name="Reset Firm", alias="reset-firm", from_model=False
    )
    tenant_id = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tenant_id)
    member = await grant_portal_access(
        service_session, tenant_id=tenant_id, role_slug="adviser", email="reset@firm.test"
    )
    # The initial invite correctly swept the fake's first session. Model a later active login so
    # the manager-initiated reset proves it sweeps established sessions too.
    workos._sessions["sess_after_activation"] = SimpleNamespace(
        id="sess_after_activation",
        user_id="user_reset@firm.test",
        status=SimpleNamespace(value="active"),
    )

    result = await send_member_password_reset(
        service_session,
        tenant_id=tenant_id,
        user_id=str(member["user_id"]),
        portal="platform",
    )

    assert result["portal"] == "platform"
    assert result["sessions_revoked"] == 1
    assert any(call[0] == "reset_password" for call in workos.calls)
    with pytest.raises(LookupError, match="no membership"):
        await send_member_password_reset(
            service_session,
            tenant_id=tenant_id + 1000,
            user_id=str(member["user_id"]),
        )


@pytest.mark.asyncio
async def test_resend_member_invite_refuses_a_user_from_another_tenant(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Minting (and revealing) a set-password link must be confined to the tenant the caller was
    authorised for.

    The admin endpoints authorise against the PATH tenant, but the lookup used to be `WHERE u.id =
    :u` with no tenant predicate and then built the WorkOS client from the *victim's* tenant — and
    admin_session runs as service_role/BYPASSRLS, so RLS did not backstop it. An operator holding
    only `tenants:write` could therefore POST /admin/tenants/<their-firm>/members/<any-uuid>/
    invitation-link and receive a live credential for another firm's login (including an Internal
    Operations account, defeating the `operations:write` gate)."""
    workos = _FakeWorkOS()
    victim_firm = await provision_tenant(
        service_session, name="Victim Firm", alias="victim-firm", from_model=False
    )
    attacker_firm = await provision_tenant(
        service_session, name="Attacker Firm", alias="attacker-firm", from_model=False
    )
    await _register_env(service_session, monkeypatch, workos, tenant_id=int(victim_firm["id"]))
    await _register_env(service_session, monkeypatch, workos, tenant_id=int(attacker_firm["id"]))
    victim = await grant_portal_access(
        service_session,
        tenant_id=int(victim_firm["id"]),
        role_slug="adviser",
        email="victim@victim.test",
    )
    minted_before = sum(1 for c in workos.calls if c[0] == "send_invitation")

    # The attacker's own tenant in the path, the victim's user id in the body.
    with pytest.raises(LookupError, match="no membership"):
        await resend_member_invite(
            service_session,
            tenant_id=int(attacker_firm["id"]),
            user_id=str(victim["user_id"]),
            reveal_link=True,
        )
    # Refused BEFORE any credential was minted -- a 404 after a live link exists is not a fix.
    assert sum(1 for c in workos.calls if c[0] == "send_invitation") == minted_before

    # ...and with no tenant scope at all (no claims published) it fails closed rather than
    # falling back to "any tenant".
    with pytest.raises(LookupError, match="no tenant scope"):
        await resend_member_invite(service_session, user_id=str(victim["user_id"]))


@pytest.mark.asyncio
async def test_resend_member_invite_falls_back_to_the_callers_own_tenant(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The firm self-service path (`routers/firm.py`) passes no tenant id — it must not need to.

    `db.firm_session` publishes the firm's own resolved tenant in `request.jwt.claims`, so an
    omitted `tenant_id` scopes to `auth.tenant_id()`: a firm resending its OWN member's invite
    works, and the same call aimed at another firm's user id is refused. Without this the
    firm-facing endpoint would carry the same cross-tenant hole as the admin one."""
    workos = _FakeWorkOS()
    mine = await provision_tenant(
        service_session, name="Mine Ltd", alias="mine-ltd", from_model=False
    )
    theirs = await provision_tenant(
        service_session, name="Theirs Ltd", alias="theirs-ltd", from_model=False
    )
    await _register_env(service_session, monkeypatch, workos, tenant_id=int(mine["id"]))
    await _register_env(service_session, monkeypatch, workos, tenant_id=int(theirs["id"]))
    ours = await grant_portal_access(
        service_session, tenant_id=int(mine["id"]), role_slug="adviser", email="us@mine.test"
    )
    yours = await grant_portal_access(
        service_session, tenant_id=int(theirs["id"]), role_slug="adviser", email="them@theirs.test"
    )

    # Publish the caller's claims exactly as firm_session does.
    await service_session.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"tenant_id": int(mine["id"]), "portal": "platform"})},
    )
    resent = await resend_member_invite(service_session, user_id=str(ours["user_id"]))
    assert resent["invitation_sent"] is True

    with pytest.raises(LookupError, match="no membership"):
        await resend_member_invite(service_session, user_id=str(yours["user_id"]))


@pytest.mark.asyncio
async def test_set_member_role(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="Gamma", alias="gamma")
    tid = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)
    await grant_portal_access(
        service_session, tenant_id=tid, role_slug="adviser", email="m@gamma.test"
    )
    uid = await _user_id_for(service_session, "m@gamma.test")
    await set_member_role(
        service_session, tenant_id=tid, user_id=uid, portal="platform", role_slug="compliance"
    )
    role = (
        await service_session.execute(
            text(
                "SELECT r.slug FROM auth.organisation_memberships m "
                "JOIN auth.roles r ON r.id = m.role_id "
                "WHERE m.tenant_id = :t AND m.portal = 'platform'"
            ),
            {"t": tid},
        )
    ).scalar_one()
    assert role == "compliance"
    assert any(c[0] == "update_membership" for c in workos.calls)


@pytest.mark.asyncio
async def test_set_member_roles_replaces_the_permission_bearing_set(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="Role Set Co", alias="role-set-co")
    tid = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)
    result = await grant_portal_access(
        service_session, tenant_id=tid, role_slug="adviser", email="roles@set.test"
    )
    uid = str(result["user_id"])

    changed = await set_member_roles(
        service_session,
        tenant_id=tid,
        user_id=uid,
        portal="platform",
        role_slugs=["adviser", "compliance"],
    )
    assert changed["role_slugs"] == ["adviser", "compliance"]

    rows = (
        await service_session.execute(
            text(
                "SELECT r.slug FROM auth.organisation_memberships m "
                "JOIN auth.membership_roles mr ON mr.membership_id = m.id "
                "JOIN auth.roles r ON r.id = mr.role_id "
                "WHERE m.user_id = :u AND m.portal = 'platform' ORDER BY r.slug"
            ),
            {"u": uid},
        )
    ).scalars()
    assert list(rows) == ["adviser", "compliance"]

    listed = await members_with_portals(service_session, tid, access_kind="staff")
    assert listed[0]["portals"][0]["role_slugs"] == ["adviser", "compliance"]

    await service_session.execute(
        text(
            "UPDATE auth.organisation_memberships SET status = 'active' "
            "WHERE user_id = :u AND tenant_id = :t"
        ),
        {"u": uid, "t": tid},
    )
    claims = (
        await service_session.execute(
            text("SELECT auth.resolve_claims(:wos, :org, :app)"),
            {
                "wos": "user_roles@set.test",
                "org": f"org_firm_{tid}",
                "app": f"app_platform_{tid}",
            },
        )
    ).scalar_one()
    role_ids = set(claims["role_ids"])
    expected_ids = set(
        (
            await service_session.execute(
                text("SELECT id FROM auth.roles WHERE tenant_id = :t AND slug = ANY(:slugs)"),
                {"t": tid, "slugs": ["adviser", "compliance"]},
            )
        ).scalars()
    )
    assert role_ids == expected_ids

    with pytest.raises(ValueError, match="at least one role"):
        await set_member_roles(
            service_session,
            tenant_id=tid,
            user_id=uid,
            portal="platform",
            role_slugs=[],
        )


@pytest.mark.asyncio
async def test_deactivate_and_reactivate_member(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whole-person suspend: every portal membership goes inactive + the user is disabled.

    This person was invited and never accepted, which is the ordinary state of a newly-invited
    client — so BOTH vendor calls here are refused: WorkOS will not deactivate a pending
    membership and will not reactivate one either. Neither refusal may fail the act, because the
    mirror is what grants the portal (`provisioning._pending_membership_refusal`); before that was
    tolerated, Deactivate and Reactivate answered 422 with WorkOS's own wording for every person
    who had not yet set a password.
    """
    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="Delta", alias="delta")
    tid = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)
    await grant_portal_access(
        service_session, tenant_id=tid, role_slug="adviser", email="m@delta.test"
    )
    uid = await _user_id_for(service_session, "m@delta.test")

    await deactivate_member(service_session, tenant_id=tid, user_id=uid)
    row = (
        await service_session.execute(
            text(
                "SELECT m.status, u.enabled FROM auth.organisation_memberships m "
                "JOIN auth.users u ON u.id = m.user_id WHERE m.tenant_id = :t"
            ),
            {"t": tid},
        )
    ).one()
    assert row.status == "inactive"
    assert row.enabled is False

    await reactivate_member(service_session, tenant_id=tid, user_id=uid)
    row = (
        await service_session.execute(
            text(
                "SELECT m.status, u.enabled FROM auth.organisation_memberships m "
                "JOIN auth.users u ON u.id = m.user_id WHERE m.tenant_id = :t"
            ),
            {"t": tid},
        )
    ).one()
    # This person never set a password (WorkOS still reports email_verified=False), so they come
    # back PENDING rather than being force-activated — the same verified?active:pending rule the
    # invite and the WorkOS sync use.
    assert row.status == "pending"
    assert row.enabled is False
    assert any(c[0] == "reactivate_membership" for c in workos.calls)


@pytest.mark.asyncio
async def test_an_invite_is_never_failed_by_a_pending_membership_refusal(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE BUG, reported from staging: inviting a client to the portal answered 422 "Pending
    organization memberships cannot be reactivated. The invite must be accepted instead."

    `grant_portal_access` reactivates a membership the create reported as `inactive`. That status
    is a SNAPSHOT, and an invitation — the one minted twenty lines above, or one issued through
    any other door — can have moved the membership to pending since, at which point WorkOS
    refuses the reactivate outright. The refusal landed before the invitation email and before the
    mirror row, so the whole invite died and the firm was shown the vendor's wording against a
    client they had never invited before.

    Which vendor route produces that state is not something this can assert (hence the explicit
    switch rather than a staged sequence); the rule it pins is that NO route may cost an invite.
    """
    workos = _FakeWorkOS()
    tenant = await provision_tenant(
        service_session, name="Pending Invite Co", alias="pending-invite"
    )
    tid = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)

    await provision_client_login(
        service_session, tenant_id=tid, role_slug="client", email="c@pending-invite.test"
    )
    uid = await _user_id_for(service_session, "c@pending-invite.test")
    workos.accept_invitation("c@pending-invite.test")
    await revoke_portal_access(service_session, tenant_id=tid, user_id=uid, portal="client")
    member = next(iter(workos._members.values()))
    assert member.status.value == "inactive"

    workos.pending_refusal_on_reactivate = True
    workos.calls.clear()
    granted = await grant_portal_access(
        service_session, tenant_id=tid, user_id=uid, portal="client", role_slug="client"
    )

    assert any(c[0] == "reactivate_membership" for c in workos.calls), "the reactivate was skipped"
    assert granted["status"] == "active"
    statuses = dict(
        (
            await service_session.execute(
                text(
                    "SELECT portal, status FROM auth.organisation_memberships WHERE tenant_id = :t"
                ),
                {"t": tid},
            )
        ).all()
    )
    assert statuses == {"client": "active"}, "the mirror row never landed — the invite aborted"


@pytest.mark.asyncio
async def test_an_invite_does_not_ask_to_reactivate_what_it_has_just_invited(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accepting the invitation is what activates a membership, so an invite that mints one must
    not also ask WorkOS to reactivate it — that call can only be refused.

    The person here has never onboarded, so the grant mints a fresh invitation; the membership it
    finds is the pending one that invitation created.
    """
    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="Fresh Co", alias="fresh-co")
    tid = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)

    await provision_client_login(
        service_session, tenant_id=tid, role_slug="client", email="n@fresh-co.test"
    )
    uid = await _user_id_for(service_session, "n@fresh-co.test")
    member = next(iter(workos._members.values()))
    assert member.status.value == "pending", "an organisation invitation leaves it pending"

    workos.calls.clear()
    await grant_portal_access(
        service_session, tenant_id=tid, user_id=uid, portal="client", role_slug="client"
    )
    assert not any(c[0] == "reactivate_membership" for c in workos.calls)
    assert any(c[0] == "send_invitation" for c in workos.calls), "no fresh invitation was minted"


@pytest.mark.asyncio
async def test_re_inviting_someone_with_an_outstanding_invitation_succeeds(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE BUG AS STAGING ACTUALLY HIT IT: press Invite to portal a second time, before the client
    has accepted, and it answered "Pending organization memberships cannot be reactivated."

    The refusal does NOT come from our reactivate — that one is skipped on a fresh invitation and
    tolerated anyway. It comes from `create_organization_membership`, which is an upsert and
    therefore reactivates: against a PENDING membership the vendor refuses it outright. Pending is
    the ordinary state here, because the invitation this very call minted twenty lines earlier is
    what makes it so.

    What made it expensive is the ORDER. By the time the create runs, the person's previous
    invitation has been revoked and replaced, so the failure destroyed a working invitation link
    and produced nothing — and every retry did it again. That is the shape the WorkOS event log
    showed: `invitation.revoked` + `invitation.created` pairs with no `organization_membership.*`
    event between them.
    """
    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="Re-invite Co", alias="re-invite")
    tid = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)

    await provision_client_login(
        service_session, tenant_id=tid, role_slug="client", email="r@re-invite.test"
    )
    uid = await _user_id_for(service_session, "r@re-invite.test")
    member = next(iter(workos._members.values()))
    assert member.status.value == "pending", "the first invite leaves the membership pending"

    workos.calls.clear()
    granted = await grant_portal_access(
        service_session, tenant_id=tid, user_id=uid, portal="client", role_slug="client"
    )

    # The point is the invite COMPLETED: a fresh credential was minted and the mirror still grants
    # the portal. A re-invite whose whole effect is to revoke the live link is worse than a refusal.
    assert any(c[0] == "create_membership" for c in workos.calls), "the membership step was skipped"
    assert any(c[0] == "send_invitation" for c in workos.calls), "no fresh invitation was minted"
    assert granted["invitation_sent"] is True
    statuses = dict(
        (
            await service_session.execute(
                text(
                    "SELECT portal, status FROM auth.organisation_memberships WHERE tenant_id = :t"
                ),
                {"t": tid},
            )
        ).all()
    )
    assert statuses == {"client": "pending"}, "the mirror row was lost with the invite"


@pytest.mark.asyncio
async def test_reactivate_leaves_a_pre_suspension_revocation_revoked(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un-suspending restores what the person HAD, not everything.

    The bug: reactivate set every membership active, so a portal deliberately revoked *before* the
    suspension was silently handed back — an access grant produced by an unrelated action, with
    nothing in the UI mentioning it. There is no column for the pre-suspension state, so
    `deactivate_member` writes it into its `auth.users.suspended` audit row and reactivate reads it
    back. The client portal here must come out of the cycle still revoked, locally AND at WorkOS."""
    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="Restore Co", alias="restore-co")
    tid = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)
    await grant_portal_access(
        service_session, tenant_id=tid, role_slug="adviser", email="two@restore-co.test"
    )
    uid = await _user_id_for(service_session, "two@restore-co.test")
    # A deliberate decision: this staff identity should no longer have Platform access.
    await revoke_portal_access(service_session, tenant_id=tid, user_id=uid, portal="platform")

    suspended = await deactivate_member(service_session, tenant_id=tid, user_id=uid)
    assert suspended["portals_before"] == {"platform": "inactive"}

    workos.calls.clear()
    restored = await reactivate_member(service_session, tenant_id=tid, user_id=uid)
    assert restored["portals_restored"] == []
    assert restored["portals_left_revoked"] == ["platform"]
    assert restored["snapshot_found"] is True

    statuses = dict(
        (
            await service_session.execute(
                text(
                    "SELECT portal, status FROM auth.organisation_memberships WHERE tenant_id = :t"
                ),
                {"t": tid},
            )
        ).all()
    )
    assert statuses == {"platform": "inactive"}
    # Nothing is restored, so WorkOS stays inactive too.
    reactivated = [c[1]["id"] for c in workos.calls if c[0] == "reactivate_membership"]
    assert reactivated == []


@pytest.mark.asyncio
async def test_reactivate_without_a_snapshot_restores_everything_and_says_so(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Someone suspended before the snapshot existed (or whose trail has rotated) must still come
    back — the old blanket behaviour — but flagged, so the UI can say which of the two happened
    rather than claiming a precision it doesn't have."""
    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="No Snap", alias="no-snap")
    tid = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)
    await grant_portal_access(
        service_session, tenant_id=tid, role_slug="adviser", email="ns@no-snap.test"
    )
    uid = await _user_id_for(service_session, "ns@no-snap.test")
    await revoke_portal_access(service_session, tenant_id=tid, user_id=uid, portal="platform")
    await deactivate_member(service_session, tenant_id=tid, user_id=uid)
    # Simulate the pre-feature / rotated-trail case: the snapshot row is not there.
    await service_session.execute(
        text("DELETE FROM audit.log WHERE tenant_id = :t AND action = 'auth.users.suspended'"),
        {"t": tid},
    )

    restored = await reactivate_member(service_session, tenant_id=tid, user_id=uid)
    assert restored["snapshot_found"] is False
    assert restored["portals_restored"] == ["platform"]
    assert restored["portals_left_revoked"] == []


@pytest.mark.asyncio
async def test_admin_scope_permission_maps_by_target_tenant(service_session: AsyncSession) -> None:
    """The admin member/role gate picks the permission from the target tenant: a firm needs
    tenants read/write, the model tenant needs model:write."""
    from app.routers.admin import _tenant_scope_permission

    firm = await provision_tenant(
        service_session, name="Scope Co", alias="scope-co", from_model=False
    )
    fid = int(firm["id"])
    read_perm = await _tenant_scope_permission(service_session, fid, write=False)
    write_perm = await _tenant_scope_permission(service_session, fid, write=True)
    assert read_perm.slug == "tenants:read"
    assert write_perm.slug == "tenants:write"

    model_id = int(
        (
            await service_session.execute(
                text("SELECT id FROM auth.tenants WHERE is_model LIMIT 1")
            )
        ).scalar_one()
    )
    assert (
        await _tenant_scope_permission(service_session, model_id, write=True)
    ).slug == "model:write"


@pytest.mark.asyncio
async def test_list_audit_returns_recent_events(service_session: AsyncSession) -> None:
    """A write to an audited table (tenant insert) surfaces in the audit endpoint. Filtered by
    action: provisioning now also seeds the onboarding flow + tenant roles, whose later
    (higher-id) audit rows would otherwise crowd the tenant insert out of an unfiltered window."""
    from app.routers.admin import list_audit

    await provision_tenant(service_session, name="Audit Co", alias="audit-co")
    result = await list_audit(session=service_session, action="auth.tenants.insert", limit=25)
    events = result["items"]
    assert isinstance(events, list)
    assert any(str(e["action"]) == "auth.tenants.insert" for e in events)
    # The firm lookup rides along so the surface can feed its own filter under audit:read alone.
    tenants = result["tenants"]
    assert isinstance(tenants, list)
    assert any(t["name"] == "Audit Co" for t in tenants)


@pytest.mark.asyncio
async def test_update_tenant(service_session: AsyncSession) -> None:
    tenant = await provision_tenant(service_session, name="Epsilon", alias="epsilon")
    updated = await update_tenant(
        service_session,
        tenant_id=int(tenant["id"]),
        name="Epsilon Wealth",
        website="https://epsilon.test",
        enabled=False,
    )
    assert updated["name"] == "Epsilon Wealth"
    assert updated["website"] == "https://epsilon.test"
    assert updated["enabled"] is False


@pytest.mark.asyncio
async def test_provision_client_login_creates_contact_login_and_link(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The onboarding mint: contact + client-portal login + crm.contact_users link in one call,
    so the freshly-minted login resolves to a NON-empty contact_scope (else it could sign in but
    see/fulfil nothing)."""
    tenant = await provision_tenant(service_session, name="Client Co", alias="client-co")
    tid = int(tenant["id"])
    workos = _FakeWorkOS()
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)

    result = await provision_client_login(
        service_session,
        tenant_id=tid,
        role_slug="client",
        email="jo@client.test",
        first_name="Jo",
        last_name="Lee",
    )
    assert result["portal"] == "client"
    cid = int(result["contact_id"])  # type: ignore[arg-type]

    # The contact is in this tenant and a client-portal membership was created.
    contact = (
        (
            await service_session.execute(
                text("SELECT tenant_id, email FROM crm.contacts WHERE id = :c"), {"c": cid}
            )
        )
        .mappings()
        .one()
    )
    assert contact["tenant_id"] == tid
    membership = (
        (
            await service_session.execute(
                text(
                    "SELECT m.portal, r.slug FROM auth.organisation_memberships m "
                    "JOIN auth.roles r ON r.id = m.role_id "
                    "WHERE m.user_id = CAST(:u AS uuid) AND m.tenant_id = :t"
                ),
                {"u": result["user_id"], "t": tid},
            )
        )
        .mappings()
        .one()
    )
    assert membership["portal"] == "client"
    assert membership["slug"] == "client"

    # The link makes resolve_claims yield the contact in scope — the whole point. App-only portal
    # (0107): scope needs an ACTIVE membership (they set a password) and a sign-in through the
    # firm's client APPLICATION — the claims dict names the token's client_id, not an org.
    await service_session.execute(
        text(
            "UPDATE auth.organisation_memberships SET status = 'active' "
            "WHERE user_id = CAST(:u AS uuid) AND tenant_id = :t"
        ),
        {"u": result["user_id"], "t": tid},
    )
    claims = (
        await service_session.execute(
            text("SELECT auth.resolve_claims(:w, NULL, :app)"),
            {"w": "user_jo@client.test", "app": f"app_client_{tid}"},
        )
    ).scalar_one()
    assert claims["portal"] == "client"
    assert cid in claims["contact_ids"]


@pytest.mark.asyncio
async def test_provision_client_login_reuses_existing_contact(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passing an existing contact_id links the login to that party rather than creating one."""
    tenant = await provision_tenant(service_session, name="Reuse Co", alias="reuse-co")
    tid = int(tenant["id"])
    workos = _FakeWorkOS()
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)
    existing = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, email) "
                    "VALUES (:t, 'con_reuse', 'individual', 'sam@reuse.test') RETURNING id"
                ),
                {"t": tid},
            )
        ).scalar_one()
    )
    result = await provision_client_login(
        service_session,
        tenant_id=tid,
        role_slug="client",
        email="sam@reuse.test",
        contact_id=existing,
    )
    assert int(result["contact_id"]) == existing  # type: ignore[arg-type]
    linked = (
        await service_session.execute(
            text("SELECT contact_id FROM crm.contact_users WHERE user_id = CAST(:u AS uuid)"),
            {"u": result["user_id"]},
        )
    ).scalar_one()
    assert linked == existing


@pytest.mark.asyncio
async def test_invite_contact_to_portal_grants_and_links(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `/firm/contacts/{id}/portal-invite` shortcut over `provision_client_login`: an
    existing contact with an email on file gets a login AND the `crm.contact_users` bridge in
    one call, using the CONTACT's own name/email rather than anything the caller supplies."""
    from app.db import FirmContext
    from app.permissions import CONTACTS_WRITE, MEMBERS_WRITE
    from app.routers import firm

    tenant = await provision_tenant(service_session, name="Invite Co", alias="invite-co")
    tid = int(tenant["id"])
    workos = _FakeWorkOS()
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)
    contact_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, email) "
                    "VALUES (:t, 'con_invite', 'individual', 'Priya', 'Shah', 'priya@invite.test') "
                    "RETURNING id"
                ),
                {"t": tid},
            )
        ).scalar_one()
    )
    ctx = FirmContext(
        session=service_session,
        tenant_id=tid,
        principal=Principal(
            user_id="wos_staff",
            organisation_id="org_1",
            role="adviser",
            permissions=[CONTACTS_WRITE.slug, MEMBERS_WRITE.slug],
        ),
    )
    result = await firm.invite_contact_to_portal(
        contact_id, firm.InvitePortal(role_slug=None), ctx=ctx
    )
    assert int(result["contact_id"]) == contact_id  # type: ignore[arg-type]
    linked = (
        await service_session.execute(
            text("SELECT contact_id FROM crm.contact_users WHERE user_id = CAST(:u AS uuid)"),
            {"u": result["user_id"]},
        )
    ).scalar_one()
    assert linked == contact_id


@pytest.mark.asyncio
async def test_invite_contact_to_portal_claims_an_unclassified_workos_import(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A WorkOS sync import is a User with no portal row — ``type = {}``, missing from Profiles.

    Invite-to-portal used to surface the vendor's "User already exists" (Invitation cannot mint
    against an address that already has a User) and roll back the claim, so they stayed
    unclassified. The grant must fall back to a password-reset token, write the client membership
    and the ``crm.contact_users`` link, and leave them on Profiles.
    """
    from app.db import FirmContext
    from app.permissions import CONTACTS_WRITE, MEMBERS_WRITE
    from app.routers import firm

    tenant = await provision_tenant(service_session, name="Import Co", alias="import-co")
    tid = int(tenant["id"])
    workos = _FakeWorkOS()
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)
    email = "imported@exist.test"
    wu_id = "user_imported"
    workos._users[email] = SimpleNamespace(
        id=wu_id,
        email=email,
        first_name="Imported",
        last_name="Client",
        email_verified=False,
    )
    workos.invitation_conflicts.add(email)
    contact_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, email) "
                    "VALUES (:t, 'con_import', 'individual', 'Imported', 'Client', :e) "
                    "RETURNING id"
                ),
                {"t": tid, "e": email},
            )
        ).scalar_one()
    )
    await service_session.execute(
        text(
            "INSERT INTO auth.users "
            "  (uid, tenant_id, email, first_name, last_name, workos_user_id, enabled) "
            "VALUES (:uid, :t, :e, 'Imported', 'Client', :w, false)"
        ),
        {"uid": new_id(), "t": tid, "e": email, "w": wu_id},
    )
    ctx = FirmContext(
        session=service_session,
        tenant_id=tid,
        principal=Principal(
            user_id="wos_staff",
            organisation_id="org_1",
            role="adviser",
            permissions=[CONTACTS_WRITE.slug, MEMBERS_WRITE.slug],
        ),
    )
    result = await firm.invite_contact_to_portal(
        contact_id, firm.InvitePortal(role_slug=None), ctx=ctx
    )
    assert int(result["contact_id"]) == contact_id  # type: ignore[arg-type]
    assert result["invitation_sent"] is True
    assert any(c[0] == "send_invitation" for c in workos.calls)
    assert any(c[0] == "reset_password" for c in workos.calls)
    assert not any(c[0] == "create_user" for c in workos.calls)

    user_id = str(result["user_id"])
    row = (
        (
            await service_session.execute(
                text("SELECT type, access_kind FROM auth.users WHERE id = CAST(:u AS uuid)"),
                {"u": user_id},
            )
        )
        .mappings()
        .one()
    )
    assert list(row["type"]) == ["client"]
    assert row["access_kind"] == "profile"
    linked = (
        await service_session.execute(
            text("SELECT contact_id FROM crm.contact_users WHERE user_id = CAST(:u AS uuid)"),
            {"u": user_id},
        )
    ).scalar_one()
    assert linked == contact_id
    profiles = await members_with_portals(service_session, tid, access_kind="profile")
    assert [m["email"] for m in profiles] == [email]
    unclassified = await members_with_portals(service_session, tid, access_kind="unclassified")
    assert unclassified == []


@pytest.mark.asyncio
async def test_invite_contact_to_portal_refuses_without_an_email(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client with no email on file cannot be invited — refused with a message that names the
    problem, rather than `provision_client_login` failing further in with a less useful error."""
    from app.db import FirmContext
    from app.permissions import CONTACTS_WRITE, MEMBERS_WRITE
    from app.routers import firm

    tenant = await provision_tenant(service_session, name="No Email Co", alias="no-email-co")
    tid = int(tenant["id"])
    workos = _FakeWorkOS()
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)
    contact_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                    "VALUES (:t, 'con_noemail', 'individual', 'No', 'Email') RETURNING id"
                ),
                {"t": tid},
            )
        ).scalar_one()
    )
    ctx = FirmContext(
        session=service_session,
        tenant_id=tid,
        principal=Principal(
            user_id="wos_staff",
            organisation_id="org_1",
            role="adviser",
            permissions=[CONTACTS_WRITE.slug, MEMBERS_WRITE.slug],
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await firm.invite_contact_to_portal(contact_id, firm.InvitePortal(role_slug=None), ctx=ctx)
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------------------------
# The WorkOS org role, and the membership upsert
#
# Both of these were live 422s in the dev environment, from ONE endpoint: `PUT
# /firm/members/{user_id}/portals/{portal}`, the app-per-portal model's central operation. Neither
# was visible to the suite, for a different reason each, so both get a test that would have failed.
# ---------------------------------------------------------------------------------------------


async def test_grant_creates_the_workos_org_role_a_cloned_tenant_never_had(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A grant naming a role WorkOS has never heard of must create it, not 422.

    THE BUG. WorkOS validates `role_slug` when adding a member to an organisation: an unknown slug
    is `422 invalid_role`. `rbac.create_org_role` creates the WorkOS role, so a hand-authored role
    was fine -- but a tenant's STARTING roles do not come from there. `_clone_model_roles` copies
    them from the model tenant with one `INSERT ... SELECT`, which touches no vendor. So every
    cloned tenant carried roles with `workos_role_id IS NULL` and no `org-*` counterpart, and EVERY
    portal grant to an existing member 422'd. Verified live before fixing: a real tenant's five
    roles all had a NULL `workos_role_id`, and the org's actual slugs were an unrelated set.

    WHY NOTHING CAUGHT IT. The fake had no `authorization` namespace at all, so it could not model
    a role that does not exist -- there was nothing for the grant to fail against.
    """
    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="Cloned Co", alias="cloned")
    tid = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)

    # The precondition, asserted rather than assumed: the cloned roles name no WorkOS role.
    before = (
        await service_session.execute(
            text("SELECT count(*) FROM auth.roles WHERE tenant_id = :t AND workos_role_id IS NULL"),
            {"t": tid},
        )
    ).scalar_one()
    assert before > 0, "the clone already populated workos_role_id — this test proves nothing"

    await grant_portal_access(
        service_session, tenant_id=tid, role_slug="adviser", email="cloned@x.test"
    )

    created = [c for c in workos.calls if c[0] == "create_organization_role"]
    assert created, "the grant did not create the org role WorkOS would have refused"
    assert created[0][1]["slug"] == "org-adviser", created

    # And it is backfilled, so the next grant needs no vendor call.
    stored = (
        await service_session.execute(
            text("SELECT workos_role_id FROM auth.roles WHERE tenant_id = :t AND slug = 'adviser'"),
            {"t": tid},
        )
    ).scalar_one()
    assert stored, "workos_role_id was not backfilled"

    # Adopt-first: a second grant on the same role must not create it again.
    uid = await _user_id_for(service_session, "cloned@x.test")
    await grant_portal_access(
        service_session, tenant_id=tid, user_id=uid, portal="platform", role_slug="adviser"
    )
    adviser_creates = [
        c
        for c in workos.calls
        if c[0] == "create_organization_role" and c[1]["slug"] == "org-adviser"
    ]
    assert len(adviser_creates) == 1, f"org-adviser created {len(adviser_creates)} times"


async def test_grant_reconciles_a_membership_that_already_exists(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-granting a portal must set its primary role and reactivate, on the path the vendor takes.

    THE BUG. `create_organization_membership` is an UPSERT on (user, organisation): with a
    membership already present it returns THAT membership rather than raising. The code held its
    role-set and reactivate inside `except ConflictError:`, so on the real API that branch never
    ran -- a member who already existed kept whatever role they had, and a deactivated membership
    stayed deactivated.

    WHY NOTHING CAUGHT IT. The double raised `ConflictError` on a duplicate, so the suite exercised
    a branch the vendor never reaches. The double is corrected (see `_create_membership`); this
    pins the behaviour rather than the branch, so it holds whichever path the SDK takes.
    """
    workos = _FakeWorkOS()
    tenant = await provision_tenant(service_session, name="Second Co", alias="second")
    tid = int(tenant["id"])
    await _register_env(service_session, monkeypatch, workos, tenant_id=tid)

    await grant_portal_access(
        service_session, tenant_id=tid, role_slug="adviser", email="second@x.test"
    )
    uid = await _user_id_for(service_session, "second@x.test")

    # They accept and onboard, then every portal is revoked — the only route to an `inactive`
    # membership, since the vendor refuses to deactivate one still pending acceptance.
    workos.accept_invitation("second@x.test")
    member = next(iter(workos._members.values()))
    await workos.organization_membership.deactivate_organization_membership(member.id)
    assert member.status.value == "inactive"

    workos.calls.clear()
    await grant_portal_access(
        service_session, tenant_id=tid, user_id=uid, portal="platform", role_slug="compliance"
    )

    assert member.status.value == "active", "the second grant left the membership deactivated"
    assert member.role.slug == "org-compliance", (
        f"the second grant did not apply the role: {member.role.slug}"
    )
    assert any(c[0] == "update_membership" for c in workos.calls), (
        "no update_organization_membership call — the reconcile is still inside a dead branch"
    )


@pytest.mark.asyncio
async def test_the_members_list_tags_which_custodians_know_a_person(
    service_session: AsyncSession,
) -> None:
    """`members_with_portals` carries the provider tags the team pages render (0118/0123).

    Three shapes must be told apart, because they are three different things an operator can do
    something about:

    * a staff login nobody has mapped — the ordinary case, no tags;
    * a staff login that also holds a custodian's id — tagged, and still a real login;
    * a row the SEI sync MINTED for one of the custodian's own employees — tagged AND marked as
      not a Graphene login, because otherwise an operator wonders why somebody they never invited
      is in their team list and then why that person cannot sign in.

    The names come from `providers.meta()` rather than from the portals, which is the rule six
    drifted hard-coded copies established the hard way: the one that mattered fell back to Morgan
    Lloyd's name for anything unlisted, so every SEI surface named the wrong custodian.
    """
    tid = (
        await service_session.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name)"
                " VALUES ('4' || encode(gen_random_bytes(7), 'hex'),"
                "         'tags-' || encode(gen_random_bytes(4), 'hex'), 'Tagged Firm')"
                " RETURNING id"
            )
        )
    ).scalar_one()
    await service_session.execute(
        text(
            "INSERT INTO auth.users (tenant_id, email, first_name, workos_user_id, enabled,"
            "                        provider_refs)"
            " VALUES (:t, 'plain@tags.test',  'Plain',  'wos_plain_' || :t, true, NULL),"
            "        (:t, 'mapped@tags.test', 'Mapped', 'wos_mapped_' || :t, true,"
            '         \'{"sei": "57817496", "morgan-lloyd": "a-guid"}\'),'
            # No `workos_user_id` -> a placeholder the pipeline minted.
            "        (:t, 'synced@tags.test', 'Synced', NULL, false, '{\"sei\": \"57817497\"}')"
        ),
        {"t": tid},
    )

    listed = {m["email"]: m for m in await members_with_portals(service_session, tid)}

    assert listed["plain@tags.test"]["providers"] == []
    assert listed["plain@tags.test"]["from_provider"] is False

    # Sorted by slug, and NAMED — the tag reads "Morgan Lloyd", not `morgan_lloyd`.
    assert listed["mapped@tags.test"]["providers"] == [
        {"provider": "morgan-lloyd", "name": "Morgan Lloyd"},
        {"provider": "sei", "name": "SEI"},
    ]
    assert listed["mapped@tags.test"]["from_provider"] is False, (
        "a real login that happens to carry a custodian id is still a real login"
    )

    synced = listed["synced@tags.test"]
    assert synced["providers"] == [{"provider": "sei", "name": "SEI"}]
    assert synced["from_provider"] is True
    assert synced["enabled"] is False
    assert synced["portals"] == [], "and it holds no portal, so it grants nothing"

    # THE FIRM'S OWN PEOPLE FIRST. One SEI firm in the p04 corpus has 546 employee users against a
    # handful of real staff, so ordering by email alone would bury every member of staff in the
    # custodian's directory.
    order = [m["email"] for m in await members_with_portals(service_session, tid)]
    assert order == ["mapped@tags.test", "plain@tags.test", "synced@tags.test"], order


# ---------------------------------------------------------------------------
# THE PROVIDER SPELLING. `morgan_lloyd` is the key; `morgan-lloyd` is the URL slug, and it is the
# only provider where the two differ — so it is the only one a missing normalisation can break.
# ---------------------------------------------------------------------------


def test_every_provider_route_accepts_either_spelling() -> None:
    """A route taking `{provider}` must resolve the slug, and this is a COUNT rather than a
    convention because the asymmetry is what made the bug reachable.

    Fifteen of nineteen routes called `_known_provider`; four took the raw path param. (Two of
    the nineteen — the `/sources` pair — went with `platform.providers` in 0133; the assertion
    below is a floor, not the total.) The admin
    app passes `params.slug` straight through — deliberately, so it holds no slug -> key map of
    its own — so "Register Morgan Lloyd Tenant" posted `morgan-lloyd` to a handler that compared
    it against `PROVIDERS` and answered `422 unknown provider 'morgan-lloyd'`. Registering the
    Microsoft tenant is the prerequisite for enabling any firm on Morgan Lloyd, so the one
    provider that needed the bridge was the one provider blocked by its absence.

    The sibling failure is worse than the error: `list_provider_sources` binds the value into SQL,
    so a slug returned `200 []` — which the console renders as "None registered", the exact words
    of the thing it was preventing.
    """
    import ast
    import inspect

    from app.routers import admin as admin_router

    tree = ast.parse(inspect.getsource(admin_router))
    offenders: list[str] = []
    checked: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        # A route whose PATH carries {provider}. The decorator's first argument is the path.
        paths = [
            dec.args[0].value
            for dec in node.decorator_list
            if isinstance(dec, ast.Call)
            and dec.args
            and isinstance(dec.args[0], ast.Constant)
            and isinstance(dec.args[0].value, str)
        ]
        if not any("{provider}" in path for path in paths):
            continue
        checked.append(node.name)
        calls = {
            child.func.id
            for child in ast.walk(node)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
        }
        if "_known_provider" not in calls:
            offenders.append(node.name)

    assert len(checked) >= 17, f"the scan found only {len(checked)} provider routes — did it break?"
    assert offenders == [], (
        "these routes take a {provider} path param without resolving its slug: "
        + ", ".join(sorted(offenders))
    )


@pytest.mark.asyncio
async def test_the_url_form_reaches_a_provider_route(service_session: AsyncSession) -> None:
    """The value the admin console actually sends, on a route that still exists.

    It was `PUT /admin/providers/morgan-lloyd/sources` — the write that could not be given the
    hyphen, and the prerequisite for enabling any firm on Morgan Lloyd. That route went with
    `platform.providers` in 0133, registering a connection and enabling a firm on it being one act
    now, so the guard is exercised on its surviving sibling. The point is unchanged: the form a
    person types has to reach the handler.
    """
    from app.routers import admin as admin_router

    out = await admin_router.list_provider_options(
        provider="morgan-lloyd",
        session=service_session,
        _p=Principal(user_id="ops", permissions=["system"]),
    )
    # ML offers no discoverable slice values — its Microsoft tenant id is typed, not picked — so
    # an empty list is the right answer and a 404 would not be.
    assert out == {"tenants": []}


@pytest.mark.asyncio
async def test_an_unknown_provider_is_still_refused(service_session: AsyncSession) -> None:
    """Accepting two spellings is not accepting anything: a name that is neither a key nor a slug
    is a 404, so a typo does not quietly register a fourth custodian."""
    from app.routers import admin as admin_router

    with pytest.raises(HTTPException) as caught:
        await admin_router.list_provider_options(
            provider="morgan-loyd",
            session=service_session,
            _p=Principal(user_id="ops", permissions=["system"]),
        )
    assert caught.value.status_code == 404
