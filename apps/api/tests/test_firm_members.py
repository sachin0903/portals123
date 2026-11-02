"""HTTP-layer tests for the two member-INVITE routes.

``POST /firm/members`` shipped with **no test at all**, which is how it came to be the only
endpoint in ``routers/firm.py`` that does not catch ``WorkOSError`` while all six of its siblings
do (``grant_portal``, ``revoke_portal``, ``set_portal_role``, ``resend_invite``,
``revoke_invitation``, ``deactivate_member``). Every WorkOS SDK failure is a ``WorkOSError``
subclass and **none** is a ``ValueError`` or a ``LookupError``, so any refusal the vendor returned
escaped unhandled: the portal showed a bare ``API responded 500`` naming nothing. That is exactly
what the Chester Rose staging invite did, and it matters most on this route because
``feat(auth): use revocable invitations`` moved it onto ``send_invitation`` /
``get_invitation`` / ``revoke_invitation`` — three new vendor calls, each with its own refusals,
behind an untested handler. ``create_tenant_member`` in ``routers/admin.py`` carried the identical
gap while ``grant_member_portal`` directly below it did not.

These drive the router coroutines with the provisioning layer patched out, so they need no
database and no vendor: the only question here is how the handler MAPS an exception, which is
where the gap was. What the vendor refuses, and why, belongs to ``provisioning``'s own tests.
"""

from __future__ import annotations

from typing import Any, NoReturn

import pytest
from fastapi import HTTPException
from workos._errors import NotFoundError, WorkOSError

from app import provisioning
from app.auth import Principal
from app.db import FirmContext
from app.permissions import CONTACTS_CREATE, MEMBERS_WRITE, TENANTS_WRITE
from app.routers import admin as admin_api
from app.routers import firm as firm_api

REFUSAL = "Invitation has already been sent to this email address."


def _firm_ctx() -> FirmContext:
    """A firm context whose session is never touched — both provisioning calls are patched."""
    return FirmContext(
        session=None,  # type: ignore[arg-type]
        tenant_id=1,
        principal=Principal(
            user_id="wos_staff",
            organisation_id="org_1",
            role="admin",
            permissions=[MEMBERS_WRITE.slug],
        ),
    )


def _invite() -> firm_api.InviteMember:
    return firm_api.InviteMember(
        email="new.member@example.com", role_slugs=["adviser"], first_name="New", last_name="Member"
    )


@pytest.fixture
def firm_provisioning(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Patch the two provisioning calls ``invite_member`` makes, returning a setter for the
    failure ``grant_portal_access`` should raise."""

    async def _portal(*_args: Any, **_kwargs: Any) -> str:
        return "platform"

    monkeypatch.setattr(firm_api.provisioning, "validate_member_role_set", _portal)

    def _raises(exc: BaseException) -> None:
        async def _grant(*_args: Any, **_kwargs: Any) -> NoReturn:
            raise exc

        monkeypatch.setattr(firm_api.provisioning, "grant_portal_access", _grant)

    return _raises


class TestTheInviteNamesItsRefusal:
    """A vendor refusal must reach the caller as a message, never as a bare 500."""

    async def test_a_workos_refusal_is_a_422_carrying_the_vendor_message(
        self, firm_provisioning: Any
    ) -> None:
        firm_provisioning(WorkOSError(REFUSAL))
        with pytest.raises(HTTPException) as caught:
            await firm_api.invite_member(_invite(), _firm_ctx())
        assert caught.value.status_code == 422
        # The vendor's own words, not a generic sentence of ours: staging has no readable log for
        # the person pressing the button, so this message IS the diagnosis.
        assert caught.value.detail == REFUSAL

    async def test_a_bad_role_set_is_still_a_422(self, firm_provisioning: Any) -> None:
        firm_provisioning(ValueError("all selected roles must belong to the same portal"))
        with pytest.raises(HTTPException) as caught:
            await firm_api.invite_member(_invite(), _firm_ctx())
        assert caught.value.status_code == 422

    async def test_a_missing_prerequisite_is_still_a_409(self, firm_provisioning: Any) -> None:
        firm_provisioning(LookupError("environment has no firm org yet"))
        with pytest.raises(HTTPException) as caught:
            await firm_api.invite_member(_invite(), _firm_ctx())
        assert caught.value.status_code == 409

    async def test_no_role_is_a_422_before_any_provisioning_call(self) -> None:
        """The empty-role guard runs ahead of the try block; it must not need the patch."""
        with pytest.raises(HTTPException) as caught:
            await firm_api.invite_member(
                firm_api.InviteMember(email="new.member@example.com"), _firm_ctx()
            )
        assert caught.value.status_code == 422


class TestClientProfileInvite:
    async def test_a_new_profile_creates_and_links_its_client_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _client_portal(*_args: Any, **_kwargs: Any) -> str:
            return "client"

        called: dict[str, Any] = {}

        async def _profile(*_args: Any, **kwargs: Any) -> dict[str, object]:
            called.update(kwargs)
            return {"user_id": "profile-1", "portal": "client"}

        monkeypatch.setattr(firm_api.provisioning, "validate_member_role_set", _client_portal)
        monkeypatch.setattr(firm_api.provisioning, "provision_client_login", _profile)
        ctx = _firm_ctx()
        ctx.principal.permissions.append(CONTACTS_CREATE.slug)

        result = await firm_api.invite_member(
            firm_api.InviteMember(
                email="new.client@example.com",
                first_name="New",
                last_name="Client",
                role_slugs=["client"],
            ),
            ctx,
        )

        assert result["user_id"] == "profile-1"
        assert called == {
            "tenant_id": 1,
            "role_slug": "client",
            "email": "new.client@example.com",
            "first_name": "New",
            "last_name": "Client",
        }


class TestTheAdminInviteNamesItToo:
    """``create_tenant_member`` is the same act through the operations door (one act, two doors —
    the collapsed-acts rule), so it must map the same refusal the same way."""

    async def test_a_workos_refusal_is_a_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _scope(*_args: Any, **_kwargs: Any) -> None:
            return None

        async def _grant(*_args: Any, **_kwargs: Any) -> NoReturn:
            raise WorkOSError(REFUSAL)

        monkeypatch.setattr(admin_api, "_require_tenant_scope", _scope)
        monkeypatch.setattr(admin_api.provisioning, "grant_portal_access", _grant)

        with pytest.raises(HTTPException) as caught:
            await admin_api.create_tenant_member(
                tenant_id=1,
                body=admin_api.CreateMember(email="new.member@example.com", role_slug="adviser"),
                session=None,  # type: ignore[arg-type]
                principal=Principal(
                    user_id="wos_ops", organisation_id="org_ops", permissions=[TENANTS_WRITE.slug]
                ),
            )
        assert caught.value.status_code == 422
        assert caught.value.detail == REFUSAL


class _Invitation:
    """The two fields the revoke path reads off a WorkOS invitation."""

    def __init__(self, id: str, state: str) -> None:
        self.id = id
        self.state = state


class _Page:
    def __init__(self, data: list[_Invitation]) -> None:
        self.data = data


class _UserManagement:
    """A WorkOS user_management double recording what the invite path asked it to do."""

    def __init__(self, listed: list[_Invitation], stored: _Invitation | None = None) -> None:
        self._listed = listed
        self._stored = stored
        self.revoked: list[str] = []

    async def get_invitation(self, invitation_id: str) -> _Invitation:
        if self._stored is None:
            raise NotFoundError("Invitation not found", status_code=404)
        return self._stored

    async def list_invitations(self, *, email: str) -> _Page:
        self.listed_email = email
        return _Page(self._listed)

    async def revoke_invitation(self, invitation_id: str) -> None:
        self.revoked.append(invitation_id)


class _Workos:
    def __init__(self, user_management: _UserManagement) -> None:
        self.user_management = user_management


class TestClearingWhatIsAlreadyOutstanding:
    """Before minting, every invitation still pending for the address must be revoked.

    WorkOS refuses a second pending invitation for one address, so a leftover from a
    half-finished first attempt made every RETRY fail — the attempt most likely to be made by
    someone trying to recover from the first failure.
    """

    async def test_a_pending_invitation_the_mirror_never_knew_about_is_revoked(self) -> None:
        um = _UserManagement(listed=[_Invitation("inv_stale", "pending")])
        assert (
            await provisioning._revoke_pending_invitations_for(
                _Workos(um),
                "a@example.com",  # type: ignore[arg-type]
            )
            == 1
        )
        assert um.revoked == ["inv_stale"]
        assert um.listed_email == "a@example.com"

    async def test_an_accepted_invitation_is_left_alone(self) -> None:
        """Revoking it would assert the person never onboarded."""
        um = _UserManagement(listed=[_Invitation("inv_done", "accepted")])
        assert (
            await provisioning._revoke_pending_invitations_for(
                _Workos(um),
                "a@example.com",  # type: ignore[arg-type]
            )
            == 0
        )
        assert um.revoked == []

    async def test_a_stored_id_the_vendor_does_not_know_is_not_an_error(self) -> None:
        """A database restored beside a recreated WorkOS project leaves ids naming nothing.

        Refusing here would block every future invite to that person on a mirror column we own.
        """
        um = _UserManagement(listed=[], stored=None)
        assert (
            await provisioning._revoke_pending_invitation(
                _Workos(um),
                "inv_gone",  # type: ignore[arg-type]
            )
            is False
        )
        assert um.revoked == []

    async def test_a_stored_pending_invitation_is_still_revoked(self) -> None:
        um = _UserManagement(listed=[], stored=_Invitation("inv_live", "pending"))
        assert (
            await provisioning._revoke_pending_invitation(
                _Workos(um),
                "inv_live",  # type: ignore[arg-type]
            )
            is True
        )
        assert um.revoked == ["inv_live"]

    async def test_a_stored_accepted_invitation_still_refuses_loudly(self) -> None:
        """The one case that must NOT become a quiet no-op."""
        um = _UserManagement(listed=[], stored=_Invitation("inv_done", "accepted"))
        with pytest.raises(LookupError):
            await provisioning._revoke_pending_invitation(
                _Workos(um),
                "inv_done",  # type: ignore[arg-type]
            )
