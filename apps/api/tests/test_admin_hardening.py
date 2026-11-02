"""Admin-surface hardening: the shapes that answered the wrong thing rather than the wrong data.

Four separate defects, each pinned to the observable behaviour and each with the variation that
would have got round a narrower fix:

1. **A no-op reported as success.** ``POST /admin/tenants/{other_firm}/members/{user}/deactivate``
   returned 200 with ``{"status": "inactive"}`` for a login belonging to a *different* firm — the
   UPDATEs are tenant-scoped, so nothing changed and the operator was told it had. Every sibling
   endpoint 404s. Reactivate has the identical shape, so both are tested.
2. **A non-uuid path segment 500ing.** ``auth.users.id`` is a uuid and the segment went straight
   into ``WHERE id = :u``, so ``urn:uuid:<uuid>`` reached psycopg as ``InvalidTextRepresentation``.
   Pydantic's uuid parser is wider than Postgres's, which is why *that one* spelling was the one
   that broke — so the test drives every spelling (canonical, uppercase, braced, dash-stripped,
   ``urn:``) plus outright rubbish.
3. **Deleting a tenant that is not a firm.** ``audit.record()`` stamps ``auth.tenants`` rows with
   the ACTOR's tenant (the table has no ``tenant_id`` of its own), so deleting the tenant the actor
   belongs to — i.e. an ops principal deleting Internal Operations — trips
   ``log_tenant_id_fkey`` and 500s. The model tenant is equally not a customer.
4. **A credential path with no suspension check.** ``POST /internal/forgot-password`` runs on
   ``internal_session`` (service_role, no principal, so no ``_refuse_if_suspended``) and would mint
   and email a working set-password link for a suspended firm's user.

Plus the poller's leader election (``_claim_environment``), which is what stops N API replicas
racing one events cursor.

DB writes hit the real testcontainer Postgres as service_role; WorkOS is faked.
"""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import db as db_mod
from app import environments, poller, provisioning
from app.auth import Principal, get_current_principal
from app.config import settings
from app.db import admin_session
from app.main import app
from app.provisioning import (
    deactivate_member,
    delete_tenant,
    provision_tenant,
    reactivate_member,
    update_tenant,
)
from app.routers import internal

SYSTEM = Principal(user_id="user_ops", permissions=["system"])


async def _firm_with_login(
    session: AsyncSession, *, alias: str, email: str, host: str | None = None
) -> tuple[int, str]:
    """A firm with one mirror login + an active platform membership. No WorkOS needed: every
    behaviour under test here is decided before the first provider call."""
    tenant = await provision_tenant(session, name=alias, alias=alias, from_model=False)
    tid = int(tenant["id"])
    await session.execute(
        text(
            "INSERT INTO auth.tenant_environments "
            "  (tenant_id, stage, workos_client_id, firm_org_id, platform_host) "
            "VALUES (:t, 'production', :cid, :org, :host)"
        ),
        {"t": tid, "cid": f"client_{alias}", "org": f"org_firm_{tid}", "host": host},
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
                "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                "VALUES (:uid, :t, :email, :wu, true) RETURNING id"
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
    return tid, str(user_id)


async def _state(session: AsyncSession, user_id: str) -> tuple[bool, list[str]]:
    enabled = (
        await session.execute(text("SELECT enabled FROM auth.users WHERE id = :u"), {"u": user_id})
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


# ---- 1. a whole-person action on someone else's login must 404, not report success ----


@pytest.mark.asyncio
async def test_deactivate_refuses_a_login_from_another_firm(
    service_session: AsyncSession,
) -> None:
    """The bug: 200 + ``{"status": "inactive"}`` for a person who was never in this tenant, while
    their own firm's rows were (correctly) untouched — a success payload describing nothing."""
    _victim_tid, victim = await _firm_with_login(
        service_session, alias="cross-victim", email="v@cross-victim.test"
    )
    other = await provision_tenant(
        service_session, name="Cross Other", alias="cross-other", from_model=False
    )

    with pytest.raises(LookupError):
        await deactivate_member(service_session, tenant_id=int(other["id"]), user_id=victim)
    # ...and the victim is exactly as they were.
    assert await _state(service_session, victim) == (True, ["active"])


@pytest.mark.asyncio
async def test_reactivate_refuses_a_login_from_another_firm(
    service_session: AsyncSession,
) -> None:
    """The same shape one endpoint along: fixing only ``deactivate`` would leave ``reactivate`` as
    a sibling that still reports success for a login it cannot touch."""
    _victim_tid, victim = await _firm_with_login(
        service_session, alias="cross-victim-2", email="v@cross-victim-2.test"
    )
    other = await provision_tenant(
        service_session, name="Cross Other 2", alias="cross-other-2", from_model=False
    )

    with pytest.raises(LookupError):
        await reactivate_member(service_session, tenant_id=int(other["id"]), user_id=victim)
    assert await _state(service_session, victim) == (True, ["active"])


@pytest.mark.asyncio
async def test_deactivate_still_works_for_the_firms_own_login(
    service_session: AsyncSession,
) -> None:
    """The guard must not close the door on the ordinary case."""
    tid, uid = await _firm_with_login(
        service_session, alias="cross-own", email="own@cross-own.test"
    )
    result = await deactivate_member(service_session, tenant_id=tid, user_id=uid)
    assert result["status"] == "inactive"
    assert await _state(service_session, uid) == (False, ["inactive"])


@pytest.mark.asyncio
async def test_a_soft_deleted_login_is_not_a_member(service_session: AsyncSession) -> None:
    """``deleted_at`` is set: the row exists but the person does not, so the action 404s rather
    than resurrecting a scoped UPDATE against a deleted login."""
    tid, uid = await _firm_with_login(
        service_session, alias="cross-gone", email="gone@cross-gone.test"
    )
    await service_session.execute(
        text("UPDATE auth.users SET deleted_at = now() WHERE id = :u"), {"u": uid}
    )
    with pytest.raises(LookupError):
        await deactivate_member(service_session, tenant_id=tid, user_id=uid)


# ---- 2. the member path segment is a uuid, and every spelling of one behaves the same ----


@pytest_asyncio.fixture
async def admin_client(
    db: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    """The real HTTP routes with the admin gate satisfied — the only way to see what FastAPI does
    with a path segment before the handler runs."""
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "SessionLocal", maker)

    async def _session() -> AsyncIterator[AsyncSession]:
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            yield session

    app.dependency_overrides[admin_session] = _session
    app.dependency_overrides[get_current_principal] = lambda: SYSTEM
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(admin_session, None)
        app.dependency_overrides.pop(get_current_principal, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_non_uuid_member_segment_is_a_client_error_not_a_500(
    admin_client: AsyncClient,
) -> None:
    """``urn:uuid:…`` was the spelling that 500'd: Postgres rejects the ``urn:`` prefix its own
    parser doesn't understand, and the segment was cast in SQL. Now the type answers first."""
    for segment in ("not-a-uuid", "urn:uuid:not-a-uuid", "12345", ""):
        resp = await admin_client.post(
            f"/admin/tenants/1/members/{segment}/deactivate",
        )
        assert resp.status_code in (404, 422), (segment, resp.status_code, resp.text)
        assert resp.status_code != 500


@pytest.mark.asyncio
async def test_every_uuid_spelling_resolves_the_same_login(
    admin_client: AsyncClient, db: dict[str, str]
) -> None:
    """Canonical, uppercase, braced, dash-stripped and ``urn:uuid:`` are the same identifier. They
    used to give a 404 each, except ``urn:`` which gave a 500 — so the *identifier* decided the
    status code. All five must now reach the handler and act on the same row."""
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        tid, uid = await _firm_with_login(session, alias="uuid-spell", email="s@uuid-spell.test")
        # The route is `/admin/tenants/{tenant_uid}/members/{user_id}` — the firm by its uid, the
        # login by the uuid this case is actually about.
        tuid = str(
            (
                await session.execute(
                    text("SELECT uid FROM auth.tenants WHERE id = :t"), {"t": tid}
                )
            ).scalar_one()
        )
    bare = uid.replace("-", "")
    spellings = [uid, uid.upper(), f"{{{uid}}}", bare, f"urn:uuid:{uid}"]
    for segment in spellings:
        resp = await admin_client.post(f"/admin/tenants/{tuid}/members/{segment}/reactivate")
        assert resp.status_code == 200, (segment, resp.status_code, resp.text)
        assert resp.json()["user_id"] == uid
    # A well-formed uuid that names nobody is still a plain 404 (not a 422 — it is a valid id).
    resp = await admin_client.post(f"/admin/tenants/{tid}/members/{uuid4()}/reactivate")
    assert resp.status_code == 404
    await engine.dispose()


# ---- 3. delete_tenant refuses anything that is not a firm ----


@pytest.mark.asyncio
async def test_delete_refuses_the_model_tenant(service_session: AsyncSession) -> None:
    """The model tenant is the role preset new firms are cloned from."""
    model_id = int(
        (
            await service_session.execute(
                text("SELECT id FROM auth.tenants WHERE is_model LIMIT 1")
            )
        ).scalar_one()
    )
    alias = str(
        (
            await service_session.execute(
                text("SELECT alias FROM auth.tenants WHERE id = :t"), {"t": model_id}
            )
        ).scalar_one()
    )
    await service_session.execute(
        text("UPDATE auth.tenants SET enabled = false WHERE id = :t"), {"t": model_id}
    )
    with pytest.raises(ValueError, match="model tenant"):
        await delete_tenant(service_session, tenant_id=model_id, confirm_alias=alias)
    await service_session.execute(
        text("UPDATE auth.tenants SET enabled = true WHERE id = :t"), {"t": model_id}
    )
    # still there
    assert (
        await service_session.execute(
            text("SELECT count(*) FROM auth.tenants WHERE id = :t"), {"t": model_id}
        )
    ).scalar_one() == 1


@pytest.mark.asyncio
async def test_delete_refuses_the_operations_tenant(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Graphene's own ops tenant. Deleting it destroys the surface that authorised the deletion —
    and trips ``log_tenant_id_fkey`` on the way, because ``audit.record()`` stamps
    ``auth.tenants`` writes with the actor's tenant."""
    tenant = await provision_tenant(
        service_session, name="Ops Like", alias="ops-like", from_model=False
    )
    tid = int(tenant["id"])
    await update_tenant(service_session, tenant_id=tid, enabled=False)

    async def _ops_tenant(_s: AsyncSession) -> int:
        return tid

    monkeypatch.setattr(environments, "operations_tenant_id", _ops_tenant)
    with pytest.raises(ValueError, match="Internal Operations"):
        await delete_tenant(service_session, tenant_id=tid, confirm_alias="ops-like")
    assert (
        await service_session.execute(
            text("SELECT count(*) FROM auth.tenants WHERE id = :t"), {"t": tid}
        )
    ).scalar_one() == 1


@pytest.mark.asyncio
async def test_delete_refuses_the_callers_own_tenant(service_session: AsyncSession) -> None:
    """The registry lookup is not the only guard: an operator who re-points the ops environment's
    client id would make ``operations_tenant_id`` miss, so the actor's own resolved tenant is
    checked too — which is also the condition that actually breaks the DELETE."""
    tenant = await provision_tenant(
        service_session, name="Self Del", alias="self-del", from_model=False
    )
    tid = int(tenant["id"])
    await update_tenant(service_session, tenant_id=tid, enabled=False)
    # Publish claims that resolve the ACTOR to this tenant (what admin_session does).
    await service_session.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": f'{{"tenant_id": {tid}}}'},
    )
    try:
        with pytest.raises(ValueError, match="your own account"):
            await delete_tenant(service_session, tenant_id=tid, confirm_alias="self-del")
    finally:
        await service_session.execute(text("SELECT set_config('request.jwt.claims', '', true)"))
    assert (
        await service_session.execute(
            text("SELECT count(*) FROM auth.tenants WHERE id = :t"), {"t": tid}
        )
    ).scalar_one() == 1


@pytest.mark.asyncio
async def test_delete_still_removes_an_ordinary_firm(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must not have closed the path it exists to protect."""
    tenant = await provision_tenant(
        service_session, name="Bye Co", alias="bye-co", from_model=False
    )
    tid = int(tenant["id"])
    await update_tenant(service_session, tenant_id=tid, enabled=False)

    async def _no_ops(_s: AsyncSession) -> int:
        raise LookupError("no operations tenant here")

    monkeypatch.setattr(environments, "operations_tenant_id", _no_ops)
    result = await delete_tenant(service_session, tenant_id=tid, confirm_alias="bye-co")
    assert result["deleted"] is True
    assert (
        await service_session.execute(
            text("SELECT count(*) FROM auth.tenants WHERE id = :t"), {"t": tid}
        )
    ).scalar_one() == 0


# ---- 4. a suspended firm gets no set-password link from the public reset form ----


@pytest.fixture
def internal_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "internal_api_token", "test-internal-token")
    return "test-internal-token"


class _MintingWorkOS:
    """Records every reset_password call — the credential mint we are asserting does NOT happen."""

    def __init__(self) -> None:
        self.mints: list[str] = []
        self.user_management = SimpleNamespace(reset_password=self._reset)

    def _reset(self, *, email: str, **kw: Any) -> SimpleNamespace:
        self.mints.append(email)
        return SimpleNamespace(
            password_reset_token=f"rt_{email}", password_reset_url=f"https://auth.example/{email}"
        )


@pytest_asyncio.fixture
async def internal_sessions(
    db: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "SessionLocal", maker)
    monkeypatch.setattr(internal, "SessionLocal", maker)
    yield maker
    await engine.dispose()


async def _forgot(host: str, email: str, token: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            "/internal/forgot-password",
            json={"email": email, "host": host},
            headers={"x-internal-token": token},
        )


@pytest.mark.asyncio
async def test_forgot_password_mints_nothing_for_a_suspended_firm(
    internal_sessions: async_sessionmaker[AsyncSession],
    internal_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hole: this path runs as service_role with no principal, so ``_refuse_if_suspended``
    never sees it, and the form on the firm's own host would hand a locked-out user a working
    set-password link. The response stays an indistinguishable ``{ok: true}`` — refusing loudly
    would turn it into a firm-status oracle on an unauthenticated form."""
    host = "susp.staff.test"
    async with internal_sessions() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        tid, _uid = await _firm_with_login(
            session, alias="susp-firm", email="p@susp-firm.test", host=host
        )

    workos = _MintingWorkOS()
    monkeypatch.setattr(environments, "core_workos_client", lambda: workos)

    # While enabled: the mint happens (the control case, so the test can't pass by doing nothing).
    resp = await _forgot(host, "p@susp-firm.test", internal_token)
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    assert workos.mints == ["p@susp-firm.test"]

    async with internal_sessions() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        await provisioning.update_tenant(session, tenant_id=tid, enabled=False)

    resp = await _forgot(host, "p@susp-firm.test", internal_token)
    assert resp.status_code == 200 and resp.json() == {"ok": True}  # same answer, no oracle
    assert workos.mints == ["p@susp-firm.test"]  # ...and no second mint


@pytest.mark.asyncio
async def test_forgot_password_still_works_for_an_unknown_host(
    internal_sessions: async_sessionmaker[AsyncSession],
    internal_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host with no environment row is the ops/admin portal or local dev — no firm, so nothing to
    suspend. The suspension check must not swallow those."""
    workos = _MintingWorkOS()
    monkeypatch.setattr(environments, "core_workos_client", lambda: workos)
    resp = await _forgot("admin.graphene.test", "ops@graphene.test", internal_token)
    assert resp.status_code == 200
    assert workos.mints == ["ops@graphene.test"]


# ---- 5. the poller elects one leader per environment ----


@pytest.mark.asyncio
async def test_only_one_process_drains_an_environment(db: dict[str, str]) -> None:
    """Two API replicas, one cursor. Without the advisory lock both drain the same environment
    every tick: the same WorkOS pages fetched twice, the first-run ``sync_tenant`` backfill run
    twice, every event applied twice. The loser must skip (None), not wait and not duplicate."""
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    env_id = 4242
    consumed: list[int] = []

    async def _consume(_session: AsyncSession, e: int, **_kwargs: object) -> int:
        consumed.append(e)
        return 0

    async with maker() as leader, leader.begin():
        await leader.execute(text("SET LOCAL ROLE service_role"))
        assert await poller._claim_environment(leader, env_id) is True
        # A second connection, mid-drain: it must decline rather than block or double-apply.
        async with maker() as follower, follower.begin():
            await follower.execute(text("SET LOCAL ROLE service_role"))
            assert await poller._claim_environment(follower, env_id) is False
            import app.events as events_mod

            original = events_mod.consume
            events_mod.consume = _consume  # type: ignore[assignment]
            try:
                assert await poller._drain_environment(follower, env_id) is None
                assert consumed == []  # never reached events.consume
                # A DIFFERENT environment is not blocked by this one's lock.
                assert await poller._drain_environment(follower, env_id + 1) == 0
                assert consumed == [env_id + 1]
            finally:
                events_mod.consume = original  # type: ignore[assignment]

    # The lock is transaction-scoped: once the leader's transaction ends it is free again.
    async with maker() as after, after.begin():
        await after.execute(text("SET LOCAL ROLE service_role"))
        assert await poller._claim_environment(after, env_id) is True

    await engine.dispose()
