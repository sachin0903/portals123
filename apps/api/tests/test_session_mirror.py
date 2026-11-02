"""The local copy of live WorkOS sessions.

The events poller is what keeps it current. These tests apply session events the way
``consume`` does, and check the read the pages use: expired rows drop out, the current
browser can be named before it has been mirrored, and a list taken earlier does not
delete a row a later event wrote.
"""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app import environments, events, session_mirror, session_refresh
from app.ids import new_id
from tests.conftest import principal_conn

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
)


async def _person(session: AsyncSession) -> tuple[int, str, str]:
    suffix = uuid.uuid4().hex[:10]
    tenant_id = int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, 'Sessions Co') RETURNING id"
                ),
                {"uid": f"ten_{suffix}", "alias": f"ses-{suffix}"},
            )
        ).scalar_one()
    )
    workos_user_id = f"user_{suffix}"
    await events._apply(
        session,
        None,  # type: ignore[arg-type]
        tenant_id,
        SimpleNamespace(
            id=f"event_{suffix}",
            event="user.created",
            data=SimpleNamespace(
                id=workos_user_id,
                email=f"{workos_user_id}@x.test",
                first_name="Ses",
                last_name="Sion",
                email_verified=True,
            ),
        ),
    )
    user_id = str(
        (
            await session.execute(
                text("SELECT id::text FROM auth.users WHERE workos_user_id = :wu"),
                {"wu": workos_user_id},
            )
        ).scalar_one()
    )
    return tenant_id, user_id, workos_user_id


def _session(
    session_id: str,
    workos_user_id: str,
    *,
    status: str = "active",
    user_agent: str = _UA,
    expires_at: str = "2027-10-01T12:00:00+00:00",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=session_id,
        user_id=workos_user_id,
        status=status,
        ip_address="203.0.113.9",
        user_agent=user_agent,
        auth_method="password",
        created_at="2026-10-01T12:00:00+00:00",
        expires_at=expires_at,
    )


def _event(kind: str, data: object) -> SimpleNamespace:
    return SimpleNamespace(id=f"event_{uuid.uuid4().hex[:8]}", event=kind, data=data)


async def test_a_session_event_is_stored_and_a_revoke_removes_it(
    service_session: AsyncSession,
) -> None:
    tenant_id, user_id, workos_user_id = await _person(service_session)
    await events._apply(
        service_session,
        None,  # type: ignore[arg-type]
        tenant_id,
        _event(
            "session.created",
            _session("session_laptop", workos_user_id, user_agent="\x07" + _UA),
        ),
    )
    # A login this firm does not have is not a parked conflict.
    await events._apply(
        service_session,
        None,  # type: ignore[arg-type]
        tenant_id,
        _event("session.created", _session("session_other", "user_nobody")),
    )
    stored = (
        await service_session.execute(
            text("SELECT user_agent FROM auth.sessions WHERE workos_session_id = 'session_laptop'")
        )
    ).scalar_one()
    assert "\x07" not in stored
    assert stored.startswith("Mozilla/5.0")
    assert (
        await service_session.execute(
            text("SELECT count(*) FROM auth.sessions WHERE workos_session_id = 'session_other'")
        )
    ).scalar_one() == 0
    views = await session_mirror.sessions_for_user(
        service_session, tenant_id=tenant_id, user_id=user_id
    )
    assert [view["id"] for view in views] == ["session_laptop"]
    assert views[0]["device"] == "Chrome on macOS"

    await events._apply(
        service_session,
        None,  # type: ignore[arg-type]
        tenant_id,
        _event("session.revoked", _session("session_laptop", workos_user_id, status="revoked")),
    )
    assert (
        await session_mirror.sessions_for_user(
            service_session, tenant_id=tenant_id, user_id=user_id
        )
        == []
    )


async def test_an_expired_row_is_hidden_and_the_current_browser_is_not_written(
    service_session: AsyncSession,
) -> None:
    tenant_id, user_id, workos_user_id = await _person(service_session)
    await session_mirror.apply_event(
        service_session,
        tenant_id,
        _event("session.created", _session("session_old", workos_user_id)),
    )
    await service_session.execute(
        text(
            "UPDATE auth.sessions SET expires_at = clock_timestamp() - interval '1 minute' "
            "WHERE workos_session_id = 'session_old'"
        )
    )
    views = await session_mirror.sessions_for_user(
        service_session,
        tenant_id=tenant_id,
        user_id=user_id,
        current_session_id="session_here",
    )
    assert [view["id"] for view in views] == ["session_here"]
    assert views[0]["current"] is True
    assert views[0]["ip_address"] is None
    still_there = (
        await service_session.execute(
            text("SELECT count(*) FROM auth.sessions WHERE workos_session_id = 'session_here'")
        )
    ).scalar_one()
    assert still_there == 0


async def test_a_list_does_not_delete_a_session_written_while_it_was_in_flight(
    service_session: AsyncSession,
) -> None:
    tenant_id, user_id, workos_user_id = await _person(service_session)
    cutoff = (await service_session.execute(text("SELECT clock_timestamp()"))).scalar_one()
    await service_session.execute(
        text(
            "INSERT INTO auth.sessions ("
            "  workos_session_id, tenant_id, user_id, updated_at"
            ") VALUES ("
            "  'session_stale', :t, CAST(:u AS uuid), clock_timestamp() - interval '1 day'"
            "), ("
            "  'session_fresh', :t, CAST(:u AS uuid), clock_timestamp()"
            ")"
        ),
        {"t": tenant_id, "u": user_id},
    )
    await session_mirror.replace_user_sessions(
        service_session,
        tenant_id=tenant_id,
        user_id=user_id,
        items=[_session("session_phone", workos_user_id)],
        not_before=cutoff,
    )
    kept = set(
        (
            await service_session.execute(
                text(
                    "SELECT workos_session_id FROM auth.sessions WHERE user_id = CAST(:u AS uuid)"
                ),
                {"u": user_id},
            )
        ).scalars()
    )
    assert kept == {"session_fresh", "session_phone"}
    assert await session_mirror.owns(
        service_session, tenant_id=tenant_id, user_id=user_id, session_id="session_phone"
    )
    assert not await session_mirror.owns(
        service_session, tenant_id=tenant_id, user_id=user_id, session_id="session_stale"
    )


async def test_a_revoke_during_a_list_is_not_written_back(service_session: AsyncSession) -> None:
    tenant_id, user_id, workos_user_id = await _person(service_session)
    await session_mirror.apply_event(
        service_session,
        tenant_id,
        _event("session.created", _session("session_live", workos_user_id)),
    )
    seen = (
        await service_session.execute(
            text("SELECT sessions_synced_at FROM auth.users WHERE id = CAST(:u AS uuid)"),
            {"u": user_id},
        )
    ).scalar_one()
    cutoff = (await service_session.execute(text("SELECT clock_timestamp()"))).scalar_one()
    await session_mirror.forget(service_session, "session_live")
    wrote = await session_mirror.store_listed_sessions(
        service_session,
        tenant_id=tenant_id,
        user_id=user_id,
        items=[_session("session_live", workos_user_id)],
        not_before=cutoff,
        seen=seen,
    )
    assert wrote is False
    left = (
        await service_session.execute(
            text("SELECT count(*) FROM auth.sessions WHERE workos_session_id = 'session_live'")
        )
    ).scalar_one()
    assert left == 0


async def test_backfill_lists_a_person_once(service_session: AsyncSession) -> None:
    tenant_id, user_id, workos_user_id = await _person(service_session)
    assert await session_mirror.pending_backfill(service_session, tenant_id, 10) == [
        (user_id, workos_user_id)
    ]
    await service_session.execute(
        text(
            "UPDATE auth.users SET sessions_synced_at = clock_timestamp() "
            "WHERE id = CAST(:u AS uuid)"
        ),
        {"u": user_id},
    )
    assert await session_mirror.pending_backfill(service_session, tenant_id, 10) == []


async def test_refreshing_a_person_writes_the_listed_sessions_without_raising(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, user_id, workos_user_id = await _person(service_session)

    @asynccontextmanager
    async def opener():
        yield service_session

    async def list_sessions(workos_id: str):
        assert workos_id == workos_user_id

        class _Page:
            async def auto_paging_iter(self):
                yield _session("session_live", workos_user_id)

        return _Page()

    async def client_for_env(session: AsyncSession, env_id: int):
        del session, env_id
        return SimpleNamespace(user_management=SimpleNamespace(list_sessions=list_sessions))

    async def client_down(session: AsyncSession, env_id: int):
        del session, env_id

        async def down(workos_id: str) -> None:
            raise RuntimeError(workos_id)

        return SimpleNamespace(user_management=SimpleNamespace(list_sessions=down))

    async def primary_environment_id(session: AsyncSession, tenant: int) -> int:
        del session, tenant
        return 1

    monkeypatch.setattr(session_refresh, "system_transaction", opener)
    monkeypatch.setattr(environments, "client_for_env", client_for_env)
    monkeypatch.setattr(environments, "primary_environment_id", primary_environment_id)

    await session_refresh.refresh_member_sessions(tenant_id, user_id)
    # A provider failure must not escape: the page has already been sent.
    monkeypatch.setattr(environments, "client_for_env", client_down)
    await session_refresh.refresh_member_sessions(tenant_id, user_id)

    stored = (
        await service_session.execute(
            text("SELECT workos_session_id FROM auth.sessions WHERE user_id = CAST(:u AS uuid)"),
            {"u": user_id},
        )
    ).scalar_one()
    synced = (
        await service_session.execute(
            text("SELECT sessions_synced_at FROM auth.users WHERE id = CAST(:u AS uuid)"),
            {"u": user_id},
        )
    ).scalar_one()
    assert stored == "session_live"
    assert synced is not None


def test_a_person_reads_only_their_own_sessions(super_engine: Engine, app_engine: Engine) -> None:
    suffix = uuid.uuid4().hex[:8]
    with super_engine.connect() as conn:
        tenant_id = int(
            conn.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, 'RLS Co') RETURNING id"
                ),
                {"uid": f"ten_{suffix}", "alias": f"rls-{suffix}"},
            ).scalar_one()
        )

        def add_user(tag: str) -> str:
            return str(
                conn.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                        "VALUES (:uid, :t, :email, :wu, true) RETURNING id"
                    ),
                    {
                        "uid": new_id(),
                        "t": tenant_id,
                        "email": f"{tag}_{suffix}@rls.test",
                        "wu": f"user_{tag}_{suffix}",
                    },
                ).scalar_one()
            )

        user_a = add_user("a")
        user_b = add_user("b")
        conn.execute(
            text(
                "INSERT INTO auth.sessions (workos_session_id, tenant_id, user_id) VALUES "
                "('session_a_' || :s, :t, CAST(:a AS uuid)), "
                "('session_b_' || :s, :t, CAST(:b AS uuid))"
            ),
            {"s": suffix, "t": tenant_id, "a": user_a, "b": user_b},
        )
    try:
        claims = {
            "sub": f"user_a_{suffix}",
            "user_uuid": user_a,
            "tenant_id": tenant_id,
            "tenant_ids": [tenant_id],
            "permissions": [],
        }
        with principal_conn(app_engine, claims) as conn:
            seen = set(conn.execute(text("SELECT workos_session_id FROM auth.sessions")).scalars())
        assert seen == {f"session_a_{suffix}"}
        with principal_conn(app_engine, {}) as conn:
            assert conn.execute(text("SELECT count(*) FROM auth.sessions")).scalar_one() == 0
        with pytest.raises(DBAPIError):
            with principal_conn(app_engine, claims) as conn:
                conn.execute(
                    text(
                        "INSERT INTO auth.sessions (workos_session_id, tenant_id, user_id) "
                        "VALUES ('session_sneak', :t, CAST(:u AS uuid))"
                    ),
                    {"t": tenant_id, "u": user_a},
                )
    finally:
        with super_engine.connect() as conn:
            conn.execute(text("DELETE FROM auth.sessions WHERE tenant_id = :t"), {"t": tenant_id})
            conn.execute(text("DELETE FROM auth.users WHERE tenant_id = :t"), {"t": tenant_id})
            conn.execute(text("DELETE FROM auth.tenants WHERE id = :t"), {"t": tenant_id})
