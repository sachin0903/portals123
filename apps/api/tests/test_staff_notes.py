"""A member of staff's own notes (``platform.staff_notes``, ``/me/notes``).

Driven as a real ``authenticated`` principal so the thing under test is the policy: the owner
reads and writes their own list, and a colleague in the same firm -- never mind another firm --
sees nothing, cannot tick a note that is not theirs, and deleting it is a silent no-op. The
handlers add nothing to that narrowing; the test proves they need not.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.routers import me as me_router


@pytest.fixture
def seed(super_engine: Engine) -> dict[str, object]:
    run = uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:uid, :alias, :name) "
                    "RETURNING id"
                ),
                {"uid": f"ten_{uuid4().hex[:12]}", "alias": f"notes-{run}", "name": f"N {run}"},
            ).scalar_one()
        )

        def mk_user(label: str) -> str:
            user = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                        "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                    ),
                    {
                        "uid": f"usr_{label}_{run}",
                        "t": tenant,
                        "email": f"{label}-{run}@n.test",
                        "wos": f"wos_{label}_{run}",
                    },
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships "
                    "(user_id, tenant_id, portal, status) "
                    "VALUES (CAST(:u AS uuid), :t, 'platform', 'active')"
                ),
                {"u": user, "t": tenant},
            )
            return user

        return {"tenant": tenant, "ann": mk_user("ann"), "bob": mk_user("bob")}


@asynccontextmanager
async def _as(db: dict[str, str], user: str, tenant: int) -> AsyncIterator[AsyncSession]:
    """SET LOCAL ROLE authenticated + the claims tenant_session would publish."""
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        claims = {
            "sub": "wos",
            "user_uuid": user,
            "tenant_id": tenant,
            "tenant_ids": [tenant],
            "contact_ids": [],
            "account_ids": [],
            "portal": "platform",
            "permissions": [],
        }
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)}
        )
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_notes_are_the_owners_alone(seed: dict[str, object], db: dict[str, str]) -> None:
    tenant = int(seed["tenant"])
    ann, bob = str(seed["ann"]), str(seed["bob"])

    async with _as(db, ann, tenant) as s:
        first = await me_router.add_my_note(me_router.NoteIn(body="  Chase the SIPP transfer "), s)
        second = await me_router.add_my_note(me_router.NoteIn(body="Call Joan"), s)
        assert first["body"] == "Chase the SIPP transfer"  # trimmed
        assert first["done"] is False
        assert second["position"] > first["position"]  # appended, in order
        mine = await me_router.my_notes(s)
        assert [n["uid"] for n in mine] == [first["uid"], second["uid"]]

    # A colleague in the same firm: nothing, and no way to touch Ann's.
    async with _as(db, bob, tenant) as s:
        assert await me_router.my_notes(s) == []
        with pytest.raises(HTTPException) as e:
            await me_router.edit_my_note(first["uid"], me_router.NotePatch(done=True), s)
        assert e.value.status_code == 404
        await me_router.remove_my_note(first["uid"], s)  # silent no-op

    async with _as(db, ann, tenant) as s:
        ticked = await me_router.edit_my_note(first["uid"], me_router.NotePatch(done=True), s)
        assert ticked["done"] is True and ticked["body"] == "Chase the SIPP transfer"
        reworded = await me_router.edit_my_note(
            second["uid"], me_router.NotePatch(body="Call Joan re: ISA"), s
        )
        assert reworded["body"] == "Call Joan re: ISA" and reworded["done"] is False
        await me_router.remove_my_note(second["uid"], s)
        assert [n["uid"] for n in await me_router.my_notes(s)] == [first["uid"]]


@pytest.mark.asyncio
async def test_a_note_needs_words(seed: dict[str, object], db: dict[str, str]) -> None:
    async with _as(db, str(seed["ann"]), int(seed["tenant"])) as s:
        for bad in ("", "   "):
            with pytest.raises(HTTPException) as e:
                await me_router.add_my_note(me_router.NoteIn(body=bad), s)
            assert e.value.status_code == 400
        with pytest.raises(HTTPException) as e:
            await me_router.add_my_note(me_router.NoteIn(body="x" * 501), s)
        assert e.value.status_code == 400
        with pytest.raises(HTTPException) as e:
            await me_router.edit_my_note("nope", me_router.NotePatch(), s)
        assert e.value.status_code == 400
