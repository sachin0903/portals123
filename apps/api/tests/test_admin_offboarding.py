"""Firm suspension, offboarding, onboarding propagation and MFA recovery.

These are the controls an operator reaches for in an incident, so each one is pinned to the
behaviour it promises rather than to the flag it writes: suspending a firm must actually end its
people's sessions and be visible on the sign-in path; deletion must refuse to run out of order and
must leave a record that survives its own cascade; setting a password must be reflected in the
mirror without waiting for a poller; and a lost authenticator must be recoverable.

WorkOS is faked (no live calls); the DB writes hit the real testcontainer Postgres as service_role,
mirroring the admin_session path.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import environments
from app.provisioning import (
    delete_tenant,
    export_tenant,
    grant_portal_access,
    mark_onboarding_complete,
    provision_tenant,
    resend_member_invite,
    reset_member_mfa,
    revoke_tenant_sessions,
    update_tenant,
)


class _AsyncPage:
    def __init__(self, data: list[object]) -> None:
        self.data = data

    async def auto_paging_iter(self):  # noqa: ANN201
        for item in self.data:
            yield item


class _FakeWorkOS:
    """Enough of the Management API for suspension/offboarding/MFA: users, memberships, live
    sessions and enrolled auth factors, each mutable so a revoke/delete is observable."""

    def __init__(self, *, verified: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._verified = verified
        self._users: dict[str, SimpleNamespace] = {}
        self._members: dict[tuple[str, str], SimpleNamespace] = {}
        self._sessions: dict[str, SimpleNamespace] = {}
        self._factors: dict[str, list[SimpleNamespace]] = {}
        self._invitations: dict[str, SimpleNamespace] = {}
        self.user_management = SimpleNamespace(
            create_user=self._create_user,
            list_users=self._list_users,
            get_user=self._get_user,
            reset_password=self._reset_password,
            list_sessions=self._list_sessions,
            revoke_session=self._revoke_session,
            send_invitation=self._send_invitation,
            resend_invitation=self._resend_invitation,
            get_invitation=self._get_invitation,
            list_invitations=self._list_invitations,
            revoke_invitation=self._revoke_invitation,
        )
        self.organization_membership = SimpleNamespace(
            create_organization_membership=self._create_membership,
            list_organization_memberships=self._list_memberships,
            update_organization_membership=self._update_membership,
            deactivate_organization_membership=self._deactivate_membership,
            reactivate_organization_membership=self._reactivate_membership,
        )
        self.multi_factor_auth = SimpleNamespace(
            list_user_auth_factors=self._list_factors, delete_factor=self._delete_factor
        )
        # `grant_portal_access` ensures the WorkOS org role exists before adding a member --
        # WorkOS validates `role_slug` and answers 422 `invalid_role` for an unknown one. Adopt
        # first, create if absent, exactly as `rbac.ensure_workos_org_role` does.
        self._org_roles: dict[tuple[str, str], SimpleNamespace] = {}
        self.authorization = SimpleNamespace(
            create_organization_role=self._create_org_role,
            list_organization_roles=self._list_org_roles,
        )

    # -- org roles ---------------------------------------------------------
    async def _create_org_role(self, org_id, *, name, slug=None, description=None, **kw):  # noqa: ANN001, ANN202, ANN003
        self.calls.append(("create_organization_role", {"org": org_id, "slug": slug}))
        # The id carries the ORG as well as the slug: a WorkOS role id is globally unique and
        # `auth.roles.workos_role_id` has a global UNIQUE to match, so an id derived from the slug
        # alone hands two tenants the same one and the backfill trips the constraint.
        role = SimpleNamespace(id=f"role_{org_id}_{slug}", slug=slug, name=name)
        self._org_roles[(str(org_id), str(slug))] = role
        return role

    async def _list_org_roles(self, org_id, **kw):  # noqa: ANN001, ANN202, ANN003
        return SimpleNamespace(
            data=[r for (o, _s), r in self._org_roles.items() if o == str(org_id)]
        )

    # -- users -------------------------------------------------------------
    async def _create_user(self, *, email, first_name=None, last_name=None, **kw):  # noqa: ANN001, ANN003, ANN202
        user = SimpleNamespace(
            id=f"user_{email}",
            email=email,
            first_name=first_name,
            last_name=last_name,
            email_verified=self._verified,
        )
        self._users[email] = user
        self.sign_in(email)
        self._factors[user.id] = [SimpleNamespace(id=f"auth_factor_{email}", type="totp")]
        return user

    def sign_in(self, email: str) -> None:
        """Give this user two live sessions + one already-expired one, so a revocation is
        measurable and the expired one is visibly left alone. Called on create and again by tests
        that need the person signed in AFTER an invite -- every mint of a set-password link now
        revokes their sessions, so a test about suspending a *signed-in* person has to establish
        the session it is about."""
        user = self._users[email]
        self._sessions[f"sess_a_{email}"] = SimpleNamespace(
            id=f"sess_a_{email}", user_id=user.id, status=SimpleNamespace(value="active")
        )
        self._sessions[f"sess_b_{email}"] = SimpleNamespace(
            id=f"sess_b_{email}", user_id=user.id, status=SimpleNamespace(value="active")
        )
        self._sessions[f"sess_old_{email}"] = SimpleNamespace(
            id=f"sess_old_{email}", user_id=user.id, status=SimpleNamespace(value="expired")
        )

    async def _list_users(self, *, email=None, **kw):  # noqa: ANN001, ANN003, ANN202
        found = self._users.get(email) if email else None
        return SimpleNamespace(data=[found] if found else [])

    async def _get_user(self, user_id):  # noqa: ANN001, ANN202
        for u in self._users.values():
            if u.id == user_id:
                return u
        return SimpleNamespace(id=user_id, email=None, email_verified=self._verified)

    async def _reset_password(self, *, email, **kw):  # noqa: ANN001, ANN003, ANN202
        self.calls.append(("reset_password", {"email": email}))
        return SimpleNamespace(
            password_reset_token=f"rt_{email}",
            password_reset_url=f"https://auth.example/reset/{email}",
        )

    async def _send_invitation(self, *, email, expires_in_days, **kw):  # noqa: ANN001, ANN003, ANN202
        invitation_id = f"invitation_{len(self._invitations) + 1}_{email}"
        invitation = SimpleNamespace(
            id=invitation_id,
            # The vendor's own Invitation carries the address; the by-email sweep reads it.
            email=email,
            token=f"invite_token_{email}",
            accept_invitation_url=f"https://auth.example/invitations/{invitation_id}",
            expires_at=datetime.now(UTC) + timedelta(days=expires_in_days),
            state=SimpleNamespace(value="pending"),
        )
        self._invitations[invitation_id] = invitation
        self.calls.append(("send_invitation", {"email": email, "expires_in_days": expires_in_days}))
        return invitation

    async def _resend_invitation(self, invitation_id, **kw):  # noqa: ANN001, ANN003, ANN202
        invitation = self._invitations[invitation_id]
        self.calls.append(("resend_invitation", {"id": invitation_id}))
        return invitation

    async def _list_invitations(self, *, email=None, **kw):  # noqa: ANN001, ANN003, ANN202
        """`grant_portal_access` sweeps by ADDRESS before minting: the stored invitation id
        covers only someone already mirrored, and WorkOS refuses a second pending invitation
        for the same email."""
        self.calls.append(("list_invitations", {"email": email}))
        data = [i for i in self._invitations.values() if email is None or i.email == email]
        return _AsyncPage(data)

    async def _get_invitation(self, invitation_id, **kw):  # noqa: ANN001, ANN003, ANN202
        return self._invitations[invitation_id]

    async def _revoke_invitation(self, invitation_id, **kw):  # noqa: ANN001, ANN003, ANN202
        self.calls.append(("revoke_invitation", {"invitation_id": invitation_id}))
        invitation = self._invitations[invitation_id]
        invitation.state = SimpleNamespace(value="revoked")
        return invitation

    # -- sessions ----------------------------------------------------------
    async def _list_sessions(self, user_id, **kw):  # noqa: ANN001, ANN003, ANN202
        data = [s for s in self._sessions.values() if s.user_id == user_id]
        return _AsyncPage(data)

    async def _revoke_session(self, *, session_id, **kw):  # noqa: ANN001, ANN003, ANN202
        self.calls.append(("revoke_session", {"session_id": session_id}))
        self._sessions.pop(session_id, None)

    # -- memberships -------------------------------------------------------
    async def _create_membership(self, *, user_id, organization_id, role=None):  # noqa: ANN001, ANN202
        m = SimpleNamespace(
            id=f"om_{user_id}_{organization_id}",
            organization_id=organization_id,
            user_id=user_id,
            status=SimpleNamespace(value="active"),
            role=SimpleNamespace(slug=getattr(role, "role_slug", None)),
        )
        self._members[(user_id, organization_id)] = m
        return m

    def _members_by_id(self, membership_id):  # noqa: ANN001, ANN202
        for m in self._members.values():
            if m.id == membership_id:
                return m
        return SimpleNamespace(id=membership_id)

    async def _update_membership(self, membership_id, **_kw):  # noqa: ANN001, ANN003, ANN202
        return self._members_by_id(membership_id)

    async def _deactivate_membership(self, membership_id, **kw):  # noqa: ANN001, ANN003, ANN202
        self.calls.append(("deactivate_membership", {"id": membership_id}))
        m = self._members_by_id(membership_id)
        m.status = SimpleNamespace(value="inactive")
        return m

    async def _reactivate_membership(self, membership_id, **kw):  # noqa: ANN001, ANN003, ANN202
        m = self._members_by_id(membership_id)
        m.status = SimpleNamespace(value="active")
        return m

    async def _list_memberships(self, *, user_id=None, organization_id=None, **kw):  # noqa: ANN001, ANN003, ANN202
        data = [
            m
            for (u, o), m in self._members.items()
            if (user_id is None or u == user_id)
            and (organization_id is None or o == organization_id)
        ]
        return _AsyncPage(data)

    # -- MFA ---------------------------------------------------------------
    async def _list_factors(self, user_id, **kw):  # noqa: ANN001, ANN003, ANN202
        data = list(self._factors.get(user_id, []))
        return _AsyncPage(data)

    async def _delete_factor(self, factor_id, **kw):  # noqa: ANN001, ANN003, ANN202
        self.calls.append(("delete_factor", {"id": factor_id}))
        for user_id, factors in self._factors.items():
            self._factors[user_id] = [f for f in factors if f.id != factor_id]


async def _firm_with_member(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    workos: _FakeWorkOS,
    *,
    name: str,
    alias: str,
    email: str,
) -> tuple[int, str]:
    """A firm with a registered environment, a platform role and one invited member. Returns
    (tenant_id, mirror user id)."""
    tenant = await provision_tenant(session, name=name, alias=alias, from_model=False)
    tid = int(tenant["id"])
    await session.execute(
        text(
            "INSERT INTO auth.tenant_environments "
            "  (tenant_id, stage, workos_client_id, firm_org_id, platform_host) "
            "VALUES (:t, 'production', :cid, :org, :host)"
        ),
        {
            "t": tid,
            "cid": f"client_{alias}",
            "org": f"org_firm_{tid}",
            "host": f"{alias}.staff.test",
        },
    )
    await session.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
            "VALUES (:t, 'adviser', 'Adviser', 'organisation', 'platform')"
        ),
        {"t": tid},
    )

    async def _fake_client(_s: AsyncSession, _e: int) -> _FakeWorkOS:
        return workos

    monkeypatch.setattr(environments, "env_workos_client", _fake_client)
    monkeypatch.setattr(environments, "client_for_env", _fake_client)

    grant = await grant_portal_access(session, tenant_id=tid, role_slug="adviser", email=email)
    return tid, str(grant["user_id"])


# ---- 1. the "enabled" switch is a real kill switch ----


@pytest.mark.asyncio
async def test_suspending_a_firm_revokes_its_live_sessions(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confirm dialogue promises the firm's people are blocked. Sign-in refusal is the portal's
    half (see the env-config test below); this is the other half — sessions already established
    must die, not linger until the token expires."""
    workos = _FakeWorkOS()
    tid, _uid = await _firm_with_member(
        service_session,
        monkeypatch,
        workos,
        name="Kill Switch Co",
        alias="kill-switch-co",
        email="a@kill.test",
    )
    workos.sign_in("a@kill.test")  # they onboarded and signed in

    result = await update_tenant(service_session, tenant_id=tid, enabled=False)
    assert result["enabled"] is False
    # Both live sessions revoked; the already-expired one is left alone.
    revoked = {c[1]["session_id"] for c in workos.calls if c[0] == "revoke_session"}
    assert revoked == {"sess_a_a@kill.test", "sess_b_a@kill.test"}
    assert result["sessions_revoked"] == 2

    # ...and the suspension is evidenced (the trigger spine can't see a WorkOS-only effect).
    logged = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM audit.log WHERE action = 'auth.tenants.suspended' "
                "AND tenant_id = :t"
            ),
            {"t": tid},
        )
    ).scalar_one()
    assert logged == 1


@pytest.mark.asyncio
async def test_env_config_reports_a_suspended_firm_to_the_portal(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`env_config_by_host` is where sign-in resolves the firm, so it must carry the suspended
    state — otherwise nothing on the auth path can see it."""
    workos = _FakeWorkOS()
    tid, _uid = await _firm_with_member(
        service_session,
        monkeypatch,
        workos,
        name="Gate Co",
        alias="gate-co",
        email="a@gate.test",
    )
    cfg = await environments.env_config_by_host(service_session, "gate-co.staff.test")
    assert cfg is not None
    assert cfg["tenant_enabled"] is True

    await update_tenant(service_session, tenant_id=tid, enabled=False)
    cfg = await environments.env_config_by_host(service_session, "gate-co.staff.test")
    assert cfg is not None
    # The host still resolves (so the portal can say "suspended" rather than "misconfigured"),
    # but the flag the portal refuses on is now false.
    assert cfg["tenant_enabled"] is False


@pytest.mark.asyncio
async def test_two_firms_cannot_share_a_platform_host(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A duplicated host used to be a cross-tenant identity/secret binding (env_config_by_host
    resolved it by lowest id and served that firm's decrypted keys). 0099 makes the host UNIQUE, so
    a second firm claiming the same host is rejected at write time — the real fix."""
    from sqlalchemy.exc import IntegrityError

    workos = _FakeWorkOS()
    await _firm_with_member(
        service_session, monkeypatch, workos, name="Dup One", alias="dup-one", email="a@dup.test"
    )
    second = await provision_tenant(
        service_session, name="Dup Two", alias="dup-two", from_model=False
    )
    with pytest.raises(IntegrityError):
        await service_session.execute(
            text(
                "INSERT INTO auth.tenant_environments "
                "  (tenant_id, stage, workos_client_id, platform_host) "
                "VALUES (:t, 'production', 'client_dup_two', 'dup-one.staff.test')"
            ),
            {"t": int(second["id"])},
        )


@pytest.mark.asyncio
async def test_revoke_tenant_sessions_is_a_noop_without_an_environment(
    service_session: AsyncSession,
) -> None:
    """A firm that was never wired to WorkOS has nobody signed in — suspending it must not error."""
    tenant = await provision_tenant(
        service_session, name="No Env Co", alias="no-env-co", from_model=False
    )
    assert await revoke_tenant_sessions(service_session, int(tenant["id"])) == 0


# ---- 2. onboarding completion propagates without the poller ----


@pytest.mark.asyncio
async def test_mark_onboarding_complete_activates_the_pending_member(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setting a password happens at the portal and used to write nothing here, so a fully
    onboarded person rendered as never-invited until someone pressed Sync. Called on that path,
    the mirror is right immediately."""
    workos = _FakeWorkOS()
    tid, uid = await _firm_with_member(
        service_session,
        monkeypatch,
        workos,
        name="Onboard Co",
        alias="onboard-co",
        email="new@onboard.test",
    )
    before = (
        await service_session.execute(
            text(
                "SELECT u.enabled, u.invitation_accepted_at, m.status "
                "FROM auth.users u JOIN auth.organisation_memberships m ON m.user_id = u.id "
                "WHERE u.id = CAST(:u AS uuid)"
            ),
            {"u": uid},
        )
    ).one()
    assert before.enabled is False
    assert before.invitation_accepted_at is None
    assert before.status == "pending"

    result = await mark_onboarding_complete(
        service_session, email="new@onboard.test", tenant_id=tid
    )
    assert result == {"users": 1, "memberships": 1}

    after = (
        await service_session.execute(
            text(
                "SELECT u.enabled, u.invitation_accepted_at, m.status "
                "FROM auth.users u JOIN auth.organisation_memberships m ON m.user_id = u.id "
                "WHERE u.id = CAST(:u AS uuid)"
            ),
            {"u": uid},
        )
    ).one()
    assert after.enabled is True
    assert after.invitation_accepted_at is not None
    assert after.status == "active"


@pytest.mark.asyncio
async def test_mark_onboarding_complete_leaves_revoked_portals_revoked(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only `pending` memberships are promoted: setting a password must not silently restore a
    portal an operator deliberately revoked."""
    workos = _FakeWorkOS()
    tid, uid = await _firm_with_member(
        service_session,
        monkeypatch,
        workos,
        name="Revoked Co",
        alias="revoked-co",
        email="rev@revoked.test",
    )
    await service_session.execute(
        text(
            "UPDATE auth.organisation_memberships SET status = 'inactive' "
            "WHERE user_id = CAST(:u AS uuid) AND tenant_id = :t"
        ),
        {"u": uid, "t": tid},
    )
    result = await mark_onboarding_complete(
        service_session, email="rev@revoked.test", tenant_id=tid
    )
    assert result["memberships"] == 0
    status = (
        await service_session.execute(
            text(
                "SELECT status FROM auth.organisation_memberships "
                "WHERE user_id = CAST(:u AS uuid) AND tenant_id = :t"
            ),
            {"u": uid, "t": tid},
        )
    ).scalar_one()
    assert status == "inactive"


@pytest.mark.asyncio
async def test_mark_onboarding_complete_is_silent_for_an_unknown_email(
    service_session: AsyncSession,
) -> None:
    """It runs on an unauthenticated portal path, so an unknown address is a no-op — never an
    error that would reveal which addresses exist."""
    assert await mark_onboarding_complete(service_session, email="nobody@nowhere.test") == {
        "users": 0,
        "memberships": 0,
    }


# ---- 3. the set-password link is a credential, not a response field ----


@pytest.mark.asyncio
async def test_invitation_link_is_withheld_unless_explicitly_revealed(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The link accepts someone's invitation. An invite reports that it was sent (and whether
    email actually went out); only the explicit reveal returns the URL itself."""
    workos = _FakeWorkOS()
    tid, uid = await _firm_with_member(
        service_session,
        monkeypatch,
        workos,
        name="Secret Co",
        alias="secret-co",
        email="s@secret.test",
    )
    invited = await grant_portal_access(
        service_session, tenant_id=tid, role_slug="adviser", email="second@secret.test"
    )
    assert invited["invitation_sent"] is True
    assert invited["invitation_url"] is None
    # Resend is unconfigured in tests, so the honest answer is "not delivered".
    assert invited["email_delivered"] is False

    revealed = await resend_member_invite(
        service_session, tenant_id=tid, user_id=uid, reveal_link=True
    )
    assert "set-password?invitation_token=" in str(revealed["invitation_url"])

    quiet = await resend_member_invite(service_session, tenant_id=tid, user_id=uid)
    assert quiet["invitation_url"] is None
    assert quiet["invitation_sent"] is True

    # EVERY mint is evidenced, with whether the link was handed to a human -- including the two
    # initial invites, which used to mint the same live credential and audit nothing.
    mints = (
        (
            await service_session.execute(
                text(
                    "SELECT detail->>'trigger' AS trigger, detail->>'revealed' AS revealed "
                    "FROM audit.log "
                    "WHERE action = 'auth.users.invitation_minted' AND tenant_id = :t "
                    "ORDER BY id"
                ),
                {"t": tid},
            )
        )
        .mappings()
        .all()
    )
    assert [(m["trigger"], m["revealed"]) for m in mints] == [
        ("grant", "false"),  # _firm_with_member's invite
        ("grant", "false"),  # second@secret.test
        (None, "true"),  # the deliberate reveal (a resend)
        (None, "false"),  # the quiet resend
    ]
    # ...and each mint ends the person's live sessions, so a superseded link leaves nothing behind.
    assert invited["sessions_revoked"] == 2


# ---- 4. MFA recovery ----


@pytest.mark.asyncio
async def test_reset_member_mfa_clears_factors_and_sessions(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lost authenticator was previously a permanent lockout (TOTP only, no recovery codes).
    Resetting deletes the factors, ends the sessions bound to them, and is audited."""
    workos = _FakeWorkOS()
    tid, uid = await _firm_with_member(
        service_session,
        monkeypatch,
        workos,
        name="MFA Co",
        alias="mfa-co",
        email="m@mfa.test",
    )
    workos.sign_in("m@mfa.test")  # they onboarded, enrolled and signed in
    result = await reset_member_mfa(service_session, tenant_id=tid, user_id=uid)
    assert result["factors_deleted"] == 1
    assert result["sessions_revoked"] == 2
    assert any(c[0] == "delete_factor" for c in workos.calls)
    assert workos._factors["user_m@mfa.test"] == []

    logged = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM audit.log WHERE action = 'auth.users.mfa_reset' "
                "AND tenant_id = :t"
            ),
            {"t": tid},
        )
    ).scalar_one()
    assert logged == 1


# ---- 5. offboarding: export, then a guarded deletion ----


@pytest.mark.asyncio
async def test_export_tenant_carries_the_record_that_deletion_destroys(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retention artefact: identity, roles, members and the firm's audit trail in full, plus
    counts for its data — because `audit.log.tenant_id` cascades on delete."""
    workos = _FakeWorkOS()
    tid, _uid = await _firm_with_member(
        service_session,
        monkeypatch,
        workos,
        name="Export Co",
        alias="export-co",
        email="e@export.test",
    )
    archive = await export_tenant(service_session, tid)
    assert archive["tenant"]["alias"] == "export-co"  # type: ignore[index]
    assert [u["email"] for u in archive["users"]] == ["e@export.test"]  # type: ignore[index]
    assert [r["slug"] for r in archive["roles"]] == ["adviser"]  # type: ignore[index]
    assert archive["audit_log"]  # the firm's own trail travels with it
    assert archive["data_counts"]["crm.contacts"] == 0  # type: ignore[index]
    # Secrets never appear in an export.
    assert all("workos_api_key_ct" not in e for e in archive["environments"])  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_delete_tenant_refuses_out_of_order_or_unconfirmed(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two-step by construction: the firm must already be suspended (so its logins are dead and
    the export step has happened) and the operator must type its alias."""
    workos = _FakeWorkOS()
    tid, _uid = await _firm_with_member(
        service_session,
        monkeypatch,
        workos,
        name="Guard Co",
        alias="guard-co",
        email="g@guard.test",
    )
    with pytest.raises(ValueError, match="suspend the firm"):
        await delete_tenant(service_session, tenant_id=tid, confirm_alias="guard-co")

    await update_tenant(service_session, tenant_id=tid, enabled=False)
    with pytest.raises(ValueError, match="does not match"):
        await delete_tenant(service_session, tenant_id=tid, confirm_alias="guard-CO")

    still_there = (
        await service_session.execute(
            text("SELECT count(*) FROM auth.tenants WHERE id = :t"), {"t": tid}
        )
    ).scalar_one()
    assert still_there == 1


@pytest.mark.asyncio
async def test_delete_tenant_cuts_workos_access_and_leaves_a_surviving_record(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deletion deactivates the firm's WorkOS memberships (its identities outlive our row) and
    writes a firm-less audit record — stamped tenant_id NULL precisely so the ON DELETE CASCADE
    on `audit.log.tenant_id` cannot take the evidence of the deletion with it."""
    workos = _FakeWorkOS()
    tid, _uid = await _firm_with_member(
        service_session,
        monkeypatch,
        workos,
        name="Gone Co",
        alias="gone-co",
        email="x@gone.test",
    )
    # Domain data in a second audit-attached, tenant-scoped table (crm.contacts): the deletion has
    # to survive the audit trigger firing for EVERY such table, not just auth.*.
    await service_session.execute(
        text(
            "INSERT INTO crm.contacts (tenant_id, uid, type, email) "
            "VALUES (:t, 'con_gone_1', 'individual', 'client@gone.test')"
        ),
        {"t": tid},
    )
    await update_tenant(service_session, tenant_id=tid, enabled=False)
    # Regression: auth.tenants is audit-attached, so the row-level audit.record() trigger fires on
    # the cascade and inserts an audit.log row referencing the tenant that has just been removed --
    # log_tenant_id_fkey is NOT DEFERRABLE, so this used to raise ForeignKeyViolation on EVERY
    # invocation and deletion could never succeed at all.
    result = await delete_tenant(service_session, tenant_id=tid, confirm_alias="gone-co")
    assert result["deleted"] is True
    assert result["users"] == 1
    assert result["workos_memberships_deactivated"] == 1
    assert int(str(result["tables_purged"])) > 0
    assert any(c[0] == "deactivate_membership" for c in workos.calls)

    # The firm's domain rows are gone with it.
    assert (
        await service_session.execute(
            text("SELECT count(*) FROM crm.contacts WHERE tenant_id = :t"), {"t": tid}
        )
    ).scalar_one() == 0

    gone = (
        await service_session.execute(
            text("SELECT count(*) FROM auth.tenants WHERE id = :t"), {"t": tid}
        )
    ).scalar_one()
    assert gone == 0
    # The firm's own rows cascaded; the deletion record did not.
    survivor = (
        (
            await service_session.execute(
                text(
                    "SELECT detail FROM audit.log "
                    "WHERE action = 'auth.tenants.deleted' AND tenant_id IS NULL "
                    "  AND detail->'tenant'->>'alias' = 'gone-co'"
                )
            )
        )
        .mappings()
        .one()
    )
    assert survivor["detail"]["tenant"]["name"] == "Gone Co"


# ---- 6. readable failures where a 500 used to be ----


@pytest.mark.asyncio
async def test_duplicate_alias_is_reported_not_crashed(service_session: AsyncSession) -> None:
    """`alias` is NOT NULL UNIQUE; a clash used to reach the operator as an opaque 500."""
    await provision_tenant(service_session, name="Clash One", alias="clash-co", from_model=False)
    with pytest.raises(ValueError, match="already used"):
        await provision_tenant(
            service_session, name="Clash Two", alias="clash-co", from_model=False
        )


@pytest.mark.asyncio
async def test_update_tenant_reports_unknown_and_taken_alias(service_session: AsyncSession) -> None:
    """A missing firm is a LookupError (404), a taken alias a ValueError (409) — neither a 500."""
    keep = await provision_tenant(
        service_session, name="Keep Co", alias="keep-co", from_model=False
    )
    other = await provision_tenant(
        service_session, name="Other Co", alias="other-co", from_model=False
    )
    with pytest.raises(LookupError):
        await update_tenant(service_session, tenant_id=-1, name="Nope")
    with pytest.raises(ValueError, match="already used"):
        await update_tenant(service_session, tenant_id=int(other["id"]), alias="keep-co")
    assert keep["alias"] == "keep-co"
