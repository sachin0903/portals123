"""The two admin controls whose fix was reported but never wired to anything.

1. **Suspending a firm.** The confirm dialogue promises "every member of {firm} is blocked from
   signing in to both portals". Revoking their WorkOS sessions is only half of that: nothing read
   ``auth.tenants.enabled`` on the request path, so an in-flight token kept working and a suspended
   firm's people could sign straight back in. ``db._refuse_if_suspended`` is the enforcement, called
   from ``tenant_session`` (RLS read path) and ``firm_session`` (firm self-service).

2. **Onboarding completion.** Setting a password happens at the portal and wrote nothing here, so a
   fully onboarded person rendered as never-invited until the WorkOS poller caught up.
   ``POST /internal/onboarding-complete`` is the push half — and it *verifies* the claim with WorkOS
   rather than trusting the identifier it is handed.

The DB writes hit the real testcontainer Postgres; WorkOS is faked. ``app.db.SessionLocal`` is
repointed at the test container so the real request dependencies can be driven end to end.
"""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import db as db_mod
from app import environments
from app.auth import Principal
from app.config import settings
from app.main import app
from app.provisioning import provision_tenant, update_tenant
from app.routers import internal
from tests.conftest import fake_request

PLATFORM_HOST = "kill.staff.test"


@pytest_asyncio.fixture
async def request_sessions(
    db: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Point ``app.db.SessionLocal`` at the test container, so ``tenant_session`` /
    ``firm_session`` / the internal endpoints run against a real migrated database instead of the
    developer's configured one."""
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "SessionLocal", maker)
    monkeypatch.setattr(internal, "SessionLocal", maker)
    yield maker
    await engine.dispose()


async def _seed_firm(
    maker: async_sessionmaker[AsyncSession], *, alias: str, email: str
) -> tuple[int, str, str]:
    """A firm with an environment (its platform host, firm org + platform APPLICATION id), a role,
    and one ACTIVE member whose WorkOS identity resolves. App-only portal (0107): a session's
    portal is the application it signed in through, so the Principal needs the app's client_id.
    Returns (tenant_id, firm_org_id, platform_app_client_id)."""
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        tenant = await provision_tenant(session, name=alias, alias=alias, from_model=False)
        tid = int(tenant["id"])
        await session.execute(
            text(
                "INSERT INTO auth.tenant_environments "
                "  (tenant_id, stage, workos_client_id, firm_org_id, platform_client_id, "
                "   platform_host) "
                "VALUES (:t, 'production', :cid, :org, :papp, :host)"
            ),
            {
                "t": tid,
                "cid": f"client_{alias}",
                "org": f"org_firm_{tid}",
                "papp": f"app_platform_{alias}",
                "host": f"{alias}.staff.test",  # unique per firm (host is unique now, 0099)
            },
        )
        role_id = (
            await session.execute(
                text(
                    "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
                    "VALUES (:t, 'adviser', 'Adviser', 'organisation', 'platform') RETURNING id"
                ),
                {"t": tid},
            )
        ).scalar_one()
        user_id = (
            await session.execute(
                text(
                    "INSERT INTO auth.users "
                    "  (uid, tenant_id, email, workos_user_id, enabled, invitation_sent_at) "
                    "VALUES (:uid, :t, :email, :wu, true, now()) RETURNING id"
                ),
                {"uid": f"usr_{alias}", "t": tid, "email": email, "wu": f"user_{alias}"},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO auth.organisation_memberships "
                "  (user_id, tenant_id, role_id, portal, status) "
                "VALUES (:u, :t, :r, 'platform', 'active')"
            ),
            {"u": user_id, "t": tid, "r": role_id},
        )
    return tid, f"org_firm_{tid}", f"app_platform_{alias}"


# ---- 1. suspension is enforced on every request, not just at the login form ----


@pytest.mark.asyncio
async def test_tenant_session_refuses_a_suspended_firm(
    request_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The RLS read path. Before this check, `enabled = false` was a label: nothing in
    resolve_claims or the policies looked at it, so a suspended firm's people kept working with the
    token they already held and could sign back in the moment their session was revoked."""
    tid, org, app_id = await _seed_firm(request_sessions, alias="kill-a", email="a@kill-a.test")
    principal = Principal(user_id="user_kill-a", organisation_id=org, client_id=app_id)

    # While enabled, the session opens and carries the firm's claims.
    gen = db_mod.tenant_session(fake_request(), principal=principal)
    session = await gen.__anext__()
    claims = (await session.execute(text("SELECT auth.tenant_id()"))).scalar_one()
    assert claims == tid
    await gen.aclose()

    async with request_sessions() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await update_tenant(s, tenant_id=tid, enabled=False)

    gen = db_mod.tenant_session(fake_request(), principal=principal)
    with pytest.raises(HTTPException) as exc:
        await gen.__anext__()
    assert exc.value.status_code == 403
    assert "suspended" in str(exc.value.detail).lower()
    await gen.aclose()


@pytest.mark.asyncio
async def test_firm_session_refuses_a_suspended_firm(
    request_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The firm self-service path (platform portal). Suspension must also stop a firm managing its
    own people/roles/branding — otherwise the kill switch leaves the firm's admin surface live."""
    tid, org, app_id = await _seed_firm(request_sessions, alias="kill-b", email="b@kill-b.test")
    principal = Principal(user_id="user_kill-b", organisation_id=org, client_id=app_id)

    gen = db_mod.firm_session(fake_request(), principal=principal)
    ctx = await gen.__anext__()
    assert ctx.tenant_id == tid
    await gen.aclose()

    async with request_sessions() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await update_tenant(s, tenant_id=tid, enabled=False)

    gen = db_mod.firm_session(fake_request(), principal=principal)
    with pytest.raises(HTTPException) as exc:
        await gen.__anext__()
    assert exc.value.status_code == 403
    await gen.aclose()


@pytest.mark.asyncio
async def test_reactivating_a_firm_restores_access(
    request_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The switch is reversible: the refusal is a live read of `enabled`, not a cached decision."""
    tid, org, app_id = await _seed_firm(request_sessions, alias="kill-c", email="c@kill-c.test")
    principal = Principal(user_id="user_kill-c", organisation_id=org, client_id=app_id)
    async with request_sessions() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await update_tenant(s, tenant_id=tid, enabled=False)
    gen = db_mod.tenant_session(fake_request(), principal=principal)
    with pytest.raises(HTTPException):
        await gen.__anext__()
    await gen.aclose()

    async with request_sessions() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await update_tenant(s, tenant_id=tid, enabled=True)
    gen = db_mod.tenant_session(fake_request(), principal=principal)
    session = await gen.__anext__()
    assert (await session.execute(text("SELECT auth.tenant_id()"))).scalar_one() == tid
    await gen.aclose()


# ---- 2. the onboarding-completion push ----


class _VerifyingWorkOS:
    """Just enough user_management to answer "is this identity email_verified?"."""

    def __init__(self, *, verified: bool, email: str) -> None:
        self.lookups: list[str] = []
        user = SimpleNamespace(id=f"user_{email}", email=email, email_verified=verified)

        async def get_user(uid: str) -> object:
            self.lookups.append(uid)
            return user

        async def list_users(**kw: object) -> object:
            self.lookups.append(str(kw.get("email")))
            return SimpleNamespace(data=[user] if kw.get("email") == email else [])

        self.user_management = SimpleNamespace(
            get_user=get_user,
            list_users=list_users,
        )


@pytest.fixture
def internal_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "internal_api_token", "test-internal-token")
    return "test-internal-token"


async def _pending_member(
    maker: async_sessionmaker[AsyncSession], *, alias: str, email: str
) -> tuple[int, str]:
    """A firm with an invited-but-not-yet-onboarded member (disabled + a pending membership) —
    exactly the state that used to persist after someone had set their password."""
    tid, _org, _app = await _seed_firm(maker, alias=alias, email=f"other-{email}")
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        user_id = (
            await session.execute(
                text(
                    "INSERT INTO auth.users "
                    "  (uid, tenant_id, email, workos_user_id, enabled, invitation_sent_at) "
                    "VALUES (:uid, :t, :email, :wu, false, now()) RETURNING id"
                ),
                {"uid": f"usr_p_{alias}", "t": tid, "email": email, "wu": f"user_{email}"},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO auth.organisation_memberships "
                "  (user_id, tenant_id, role_id, portal, status) "
                "VALUES (:u, :t, (SELECT id FROM auth.roles WHERE tenant_id = :t LIMIT 1), "
                "        'platform', 'pending')"
            ),
            {"u": user_id, "t": tid},
        )
    return tid, str(user_id)


async def _member_state(
    maker: async_sessionmaker[AsyncSession], user_id: str
) -> tuple[bool, list[str]]:
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        enabled = (
            await session.execute(
                text("SELECT enabled FROM auth.users WHERE id = :u"), {"u": user_id}
            )
        ).scalar_one()
        statuses = list(
            (
                await session.execute(
                    text(
                        "SELECT status FROM auth.organisation_memberships "
                        "WHERE user_id = :u ORDER BY portal"
                    ),
                    {"u": user_id},
                )
            )
            .scalars()
            .all()
        )
    return bool(enabled), [str(s) for s in statuses]


async def _post_onboarding_complete(
    body: dict[str, Any],
    token: str | None = "test-internal-token",  # noqa: S107 - the test fixture value, not a secret
) -> Any:
    transport = ASGITransport(app=app)
    headers = {"x-internal-token": token} if token else {}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/internal/onboarding-complete", json=body, headers=headers)


@pytest.mark.asyncio
async def test_onboarding_complete_activates_a_pending_member(
    request_sessions: async_sessionmaker[AsyncSession],
    internal_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint the portal's set-password action calls. It has to EXIST and be mounted — the
    previous pass shipped the caller without it, so every push 404'd silently."""
    _tid, user_id = await _pending_member(request_sessions, alias="push-a", email="new@push-a.test")
    assert await _member_state(request_sessions, user_id) == (False, ["pending"])

    workos = _VerifyingWorkOS(verified=True, email="new@push-a.test")
    monkeypatch.setattr(environments, "core_workos_client", lambda: workos)
    resp = await _post_onboarding_complete({"email": "new@push-a.test"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "users": 1, "memberships": 1}
    assert await _member_state(request_sessions, user_id) == (True, ["active"])


@pytest.mark.asyncio
async def test_onboarding_complete_accepts_the_exact_workos_user_id(
    request_sessions: async_sessionmaker[AsyncSession],
    internal_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shape `@graphene/auth`'s `notifyOnboardingComplete` actually posts: it prefers
    `workos_user_id` (exact) over `email` (unique only per tenant). Pinned so the two halves of the
    contract cannot drift apart silently."""
    _tid, user_id = await _pending_member(request_sessions, alias="push-d", email="new@push-d.test")
    workos = _VerifyingWorkOS(verified=True, email="new@push-d.test")
    monkeypatch.setattr(environments, "core_workos_client", lambda: workos)
    resp = await _post_onboarding_complete(
        {"workos_user_id": "user_new@push-d.test", "host": "admin.graphene.test"}
    )
    assert resp.json() == {"ok": True, "users": 1, "memberships": 1}
    assert workos.lookups == ["user_new@push-d.test"]  # resolved by id, not by email
    assert await _member_state(request_sessions, user_id) == (True, ["active"])


@pytest.mark.asyncio
async def test_onboarding_complete_is_disabled_without_the_internal_token(
    request_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same contract as its siblings: no/incorrect token is a 404, so the path is not advertised."""
    monkeypatch.setattr(settings, "internal_api_token", "the-real-token")
    assert (await _post_onboarding_complete({"email": "x@y.test"}, token=None)).status_code == 404
    assert (
        await _post_onboarding_complete({"email": "x@y.test"}, token="wrong")  # noqa: S106
    ).status_code == 404


@pytest.mark.asyncio
async def test_onboarding_complete_will_not_promote_an_unverified_identity(
    request_sessions: async_sessionmaker[AsyncSession],
    internal_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The identifier arrives from a portal action, so it is checked against WorkOS first: if the
    password was never actually set (email still unverified) nothing is promoted."""
    _tid, user_id = await _pending_member(request_sessions, alias="push-b", email="new@push-b.test")
    workos = _VerifyingWorkOS(verified=False, email="new@push-b.test")
    monkeypatch.setattr(environments, "core_workos_client", lambda: workos)
    resp = await _post_onboarding_complete({"email": "new@push-b.test"})
    assert resp.json() == {"ok": True, "users": 0, "memberships": 0}
    assert await _member_state(request_sessions, user_id) == (False, ["pending"])


@pytest.mark.asyncio
async def test_onboarding_complete_cannot_unsuspend_a_deactivated_member(
    request_sessions: async_sessionmaker[AsyncSession],
    internal_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The abuse case. This runs off an unauthenticated user journey, so it must not double as an
    un-suspend primitive: a login whose memberships are all `inactive` was deliberately switched
    off by an operator, and stays off even though WorkOS says their email is verified."""
    _tid, user_id = await _pending_member(request_sessions, alias="push-c", email="sus@push-c.test")
    async with request_sessions() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text("UPDATE auth.organisation_memberships SET status = 'inactive' WHERE user_id = :u"),
            {"u": user_id},
        )
    workos = _VerifyingWorkOS(verified=True, email="sus@push-c.test")
    monkeypatch.setattr(environments, "core_workos_client", lambda: workos)
    resp = await _post_onboarding_complete({"email": "sus@push-c.test"})
    assert resp.json() == {"ok": True, "users": 0, "memberships": 0}
    assert await _member_state(request_sessions, user_id) == (False, ["inactive"])


@pytest.mark.asyncio
async def test_onboarding_complete_is_silent_about_unknown_addresses(
    request_sessions: async_sessionmaker[AsyncSession],
    internal_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No existence oracle, and no empty-body 500."""
    workos = _VerifyingWorkOS(verified=True, email="somebody@else.test")
    monkeypatch.setattr(environments, "core_workos_client", lambda: workos)
    assert (await _post_onboarding_complete({"email": "ghost@nowhere.test"})).json() == {
        "ok": True,
        "users": 0,
        "memberships": 0,
    }
    assert (await _post_onboarding_complete({})).json() == {
        "ok": True,
        "users": 0,
        "memberships": 0,
    }


@pytest.mark.asyncio
async def test_onboarding_complete_survives_a_workos_outage(
    request_sessions: async_sessionmaker[AsyncSession],
    internal_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user has ALREADY set their password by the time this is called, so a provider failure
    must not fail the page — the poller reconciles instead."""

    class _Broken:
        @staticmethod
        async def list_users(**_kw: object) -> object:
            raise RuntimeError("workos down")

        user_management = SimpleNamespace(list_users=list_users)

    monkeypatch.setattr(environments, "core_workos_client", lambda: _Broken())
    resp = await _post_onboarding_complete({"email": "anyone@firm.test"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "users": 0, "memberships": 0}
