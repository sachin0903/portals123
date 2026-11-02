"""Behaviour + RLS tests for standalone tasks (tasks.tasks — docs/tasks.md).

Drives the service and the /tasks router coroutines against a real ``authenticated`` session
(claims minted by ``auth.resolve_claims`` for staff; hand-crafted client-portal claims for the
client, since the portal classification needs a tenant_environments row we don't seed). Proves:

- ``resolve_claims`` mints the new ``role_ids`` claim from active memberships.
- Staff see the firm's tasks (lineage branch); a client sees ONLY tasks assigned to their own
  contacts (the client portal's empty lineage + contact_scope branch); cross-tenant is invisible.
- ``assignee=me`` matches both user- and role-assigned tasks.
- ``create_task`` validates assignees against the tenant and collapses automated duplicates on
  (tenant, source_kind, source_ref).
- Completion transitions once, then refuses.
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import Principal
from app.routers import tasks as tasks_router
from app.tasks import service


def _principal(perms: list[str]) -> Principal:
    """Satisfies the endpoint permission guard; RLS visibility comes from the session claims."""
    return Principal(user_id="x", organisation_id="x", role="adviser", permissions=perms)


@asynccontextmanager
async def _staff(app_url: str, workos_user: str) -> AsyncIterator[AsyncSession]:
    """A staff session — SET ROLE authenticated + claims minted by auth.resolve_claims,
    exactly like db.py::tenant_session."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        resolved = (
            await session.execute(
                # The platform application's client id (0107): what classifies the portal.
                text("SELECT auth.resolve_claims(:w, NULL, :app)"),
                {"w": workos_user, "app": f"app_platform_{workos_user}"},
            )
        ).scalar_one()
        claims = {"sub": workos_user, "role": "adviser", "permissions": [], **resolved}
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)}
        )
        yield session
    await engine.dispose()


@asynccontextmanager
async def _client(
    app_url: str, user_uuid: str, tenant: int, contact_ids: list[int]
) -> AsyncIterator[AsyncSession]:
    """A CLIENT-portal session: portal='client' empties auth.tenant_lineage() (the 0004 trust
    boundary), so only the contact_scope branch of the tasks policies can match."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        claims = {
            "sub": "client",
            "role": "authenticated",
            "permissions": [],
            "user_uuid": user_uuid,
            "tenant_id": tenant,
            "tenant_ids": [tenant],
            "portal": "client",
            "contact_ids": contact_ids,
            "account_ids": [],
            "role_ids": [],
        }
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)}
        )
        yield session
    await engine.dispose()


@pytest.fixture
def taskbed(super_engine: Engine) -> dict[str, object]:
    """Two tenants. Tenant A: a compliance role, a staff login holding it (active platform
    membership), and two client contacts — c1 with a portal login, c2 without. Tenant B: staff."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        ten_a = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"tta_{s}", "a": f"taskfirm-{s}", "n": f"TaskFirm {s}"},
            ).scalar_one()
        )
        ten_b = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"ttb_{s}", "a": f"otherfirm-{s}", "n": f"OtherFirm {s}"},
            ).scalar_one()
        )
        # App-only portal (0107): each tenant declares a platform application per staff login —
        # the id embeds the login's wos id so _staff can derive it without extra plumbing.
        for ten, wos in ((ten_a, f"twos_{s}"), (ten_b, f"twosb_{s}")):
            c.execute(
                text(
                    "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                    "  platform_client_id) VALUES (:t, 'dev', :env, :app)"
                ),
                {"t": ten, "env": f"env_{ten}_{s}", "app": f"app_platform_{wos}"},
            )
        role = int(
            c.execute(
                text(
                    "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                    "VALUES (:t, 'compliance', 'Compliance', 'platform') RETURNING id"
                ),
                {"t": ten_a},
            ).scalar_one()
        )
        wos_staff = f"twos_{s}"
        staff = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, first_name, "
                    "  last_name, enabled) "
                    "VALUES (:uid, :t, :email, :wos, 'Tess', 'Staff', true) RETURNING id"
                ),
                {
                    "uid": f"tusr_{s}",
                    "t": ten_a,
                    "email": f"staff_{s}@taskfirm.test",
                    "wos": wos_staff,
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, role_id, "
                "  status) VALUES (:u, :t, 'platform', :r, 'active')"
            ),
            {"u": staff, "t": ten_a, "r": role},
        )
        wos_staff_b = f"twosb_{s}"
        staff_b = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                ),
                {
                    "uid": f"tusrb_{s}",
                    "t": ten_b,
                    "email": f"b_{s}@otherfirm.test",
                    "wos": wos_staff_b,
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, status) "
                "VALUES (:u, :t, 'platform', 'active')"
            ),
            {"u": staff_b, "t": ten_b},
        )
        c1 = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :u, 'individual', 'Cleo Client', :e) RETURNING id"
                ),
                {"t": ten_a, "u": f"tc1_{s}", "e": f"cleo_{s}@client.test"},
            ).scalar_one()
        )
        c2 = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name) "
                    "VALUES (:t, :u, 'individual', 'Casper Client') RETURNING id"
                ),
                {"t": ten_a, "u": f"tc2_{s}"},
            ).scalar_one()
        )
        client_login = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                ),
                {
                    "uid": f"tclu_{s}",
                    "t": ten_a,
                    "email": f"cleo_{s}@client.test",
                    "wos": f"twoc_{s}",
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id) VALUES (:t, :c, :u)"
            ),
            {"t": ten_a, "c": c1, "u": client_login},
        )
        # The staff login's book: contact-assigned task creation validates the contact under the
        # caller's RLS, so the adviser must actually be able to see the client (as in documents).
        for contact in (c1, c2):
            c.execute(
                text(
                    "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                    "VALUES (:t, :c, :u, 'adviser')"
                ),
                {"t": ten_a, "c": contact, "u": staff},
            )
        c.commit()
    return {
        "tenant_a": ten_a,
        "tenant_b": ten_b,
        "role": role,
        "staff": staff,
        "wos_staff": wos_staff,
        "wos_staff_b": wos_staff_b,
        "c1": c1,
        "c2": c2,
        "client_login": client_login,
    }


@pytest.mark.asyncio
async def test_resolve_claims_mints_role_ids(
    db: dict[str, str], taskbed: dict[str, object]
) -> None:
    """The staff login's active membership role surfaces in the new role_ids claim."""
    async with _staff(db["app"], str(taskbed["wos_staff"])) as session:
        claims = (
            await session.execute(text("SELECT current_setting('request.jwt.claims', true)"))
        ).scalar_one()
    assert json.loads(claims)["role_ids"] == [taskbed["role"]]


@pytest.mark.asyncio
async def test_staff_create_list_and_me_filter(
    db: dict[str, str], taskbed: dict[str, object]
) -> None:
    """Create user-, role- and contact-assigned tasks; 'assignee=me' matches the first two
    (own user + held role), the plain list shows all three."""
    writer = _principal(["tasks:write"])
    async with _staff(db["app"], str(taskbed["wos_staff"])) as session:
        for kind, extra in (
            ("user", {"assignee_user_id": str(taskbed["staff"])}),
            ("role", {"assignee_role_id": int(taskbed["role"])}),  # type: ignore[dict-item]
            ("contact", {"assignee_contact_id": int(taskbed["c1"])}),  # type: ignore[dict-item]
        ):
            await tasks_router.create_task(
                tasks_router.CreateTask(title=f"{kind} task", assignee_kind=kind, **extra),  # type: ignore[arg-type]
                session=session,
                principal=writer,
            )
        mine = await tasks_router.list_tasks(
            session=session, principal=_principal(["tasks:read"]), assignee="me"
        )
        everything = await tasks_router.list_tasks(
            session=session, principal=_principal(["tasks:read"])
        )
    assert sorted(t.title for t in mine) == ["role task", "user task"]
    assert {t.title for t in everything} >= {"user task", "role task", "contact task"}
    assert all(t.assignee_name for t in everything)


@pytest.mark.asyncio
async def test_rows_carry_the_contact_uids_a_client_link_needs(
    db: dict[str, str], taskbed: dict[str, object]
) -> None:
    """A task names clients in two places — who it is assigned to, and who it is about — and the
    row has to carry a UID for each, because the client record is addressed by uid and both ids on
    the row are SERIALS. The two are independent: this task is assigned to Cleo and about Casper.

    The staff/role rows in the same listing must come back with NULL uids rather than someone
    else's: the assignee join and the subject join are separate, and getting them crossed would
    hang a client link off a colleague's name."""
    writer = _principal(["tasks:write"])
    async with _staff(db["app"], str(taskbed["wos_staff"])) as session:
        await tasks_router.create_task(
            tasks_router.CreateTask(
                title="chase Cleo about Casper",
                assignee_kind="contact",
                assignee_contact_id=int(taskbed["c1"]),  # type: ignore[call-overload]
                subject_type="contact",
                subject_id=str(taskbed["c2"]),
            ),
            session=session,
            principal=writer,
        )
        await tasks_router.create_task(
            tasks_router.CreateTask(
                title="staff task",
                assignee_kind="user",
                assignee_user_id=str(taskbed["staff"]),
            ),
            session=session,
            principal=writer,
        )
        rows = {
            t.title: t
            for t in await tasks_router.list_tasks(
                session=session, principal=_principal(["tasks:read"])
            )
        }
    both = rows["chase Cleo about Casper"]
    assert both.assignee_name == "Cleo Client"
    assert both.subject_contact_name == "Casper Client"
    # Real uids, not the serials — a serial cannot address /clients/{uid}.
    assert both.assignee_contact_uid and both.assignee_contact_uid != str(taskbed["c1"])
    assert both.subject_contact_uid and both.subject_contact_uid != str(taskbed["c2"])
    assert both.assignee_contact_uid != both.subject_contact_uid
    staff_row = rows["staff task"]
    assert staff_row.assignee_contact_uid is None
    assert staff_row.subject_contact_uid is None
    assert staff_row.subject_contact_name is None


@pytest.mark.asyncio
async def test_client_sees_and_completes_only_own(
    db: dict[str, str], taskbed: dict[str, object], service_session: AsyncSession
) -> None:
    """A client-portal session sees ONLY tasks assigned to its own contacts (no lineage leak of
    the firm's role-assigned work), completes its own, and cannot settle another client's."""
    own = await service.create_task(
        service_session,
        tenant_id=int(taskbed["tenant_a"]),  # type: ignore[call-overload]
        assignee_kind="contact",
        assignee_contact_id=int(taskbed["c1"]),  # type: ignore[call-overload]
        title="Confirm your address",
    )
    await service.create_task(
        service_session,
        tenant_id=int(taskbed["tenant_a"]),  # type: ignore[call-overload]
        assignee_kind="contact",
        assignee_contact_id=int(taskbed["c2"]),  # type: ignore[call-overload]
        title="Someone else's action",
    )
    await service.create_task(
        service_session,
        tenant_id=int(taskbed["tenant_a"]),  # type: ignore[call-overload]
        assignee_kind="role",
        assignee_role_id=int(taskbed["role"]),  # type: ignore[call-overload]
        title="Internal compliance review",
    )
    await service_session.commit()

    async with _client(
        db["app"],
        str(taskbed["client_login"]),
        int(taskbed["tenant_a"]),
        [int(taskbed["c1"])],  # type: ignore[call-overload]
    ) as session:
        mine = await tasks_router.my_tasks(session=session)
        assert [t.title for t in mine] == ["Confirm your address"]
        # The raw table view agrees: RLS hides everything not contact-assigned to c1.
        visible = (await session.execute(text("SELECT count(*) FROM tasks.tasks"))).scalar_one()
        assert visible == 1
        done = await tasks_router.complete_task(str(own["uid"]), session=session)
        assert done.status == "done"
        with pytest.raises(Exception):  # noqa: B017 — 422 HTTPException: not visible to this client
            await tasks_router.complete_task("tsk_missing_or_foreign", session=session)


@pytest.mark.asyncio
async def test_my_tasks_narrows_to_the_active_contact(
    db: dict[str, str], taskbed: dict[str, object], service_session: AsyncSession
) -> None:
    """A login linked to TWO clients sees the action items of the one it is acting for.

    ``auth.contact_scope()`` is every client a login may act for, so the un-narrowed list put one
    client's outstanding work on another client's page — under that client's name, with nothing on
    the row to say otherwise. The client portal's switcher decides, exactly as it does for the
    notifications feed and the documents list; absent, the whole scope is still the answer, which
    is what a single-client login gets.
    """
    for contact, title in (("c1", "Confirm your address"), ("c2", "Sign the declaration")):
        await service.create_task(
            service_session,
            tenant_id=int(taskbed["tenant_a"]),  # type: ignore[call-overload]
            assignee_kind="contact",
            assignee_contact_id=int(taskbed[contact]),  # type: ignore[call-overload]
            title=title,
        )
    await service_session.commit()

    both = [int(taskbed["c1"]), int(taskbed["c2"])]  # type: ignore[call-overload]
    async with _client(
        db["app"], str(taskbed["client_login"]), int(taskbed["tenant_a"]), both
    ) as session:
        everything = await tasks_router.my_tasks(session=session)
        first = await tasks_router.my_tasks(
            contact_id=int(taskbed["c1"]),  # type: ignore[call-overload]
            session=session,
        )
        second = await tasks_router.my_tasks(
            contact_id=int(taskbed["c2"]),  # type: ignore[call-overload]
            session=session,
        )
        # An id this login does not hold narrows to nothing: the filter is ANDed onto the scope,
        # so it can never widen it.
        foreign = await tasks_router.my_tasks(contact_id=-1, session=session)

    assert {t.title for t in everything} == {"Confirm your address", "Sign the declaration"}
    assert [t.title for t in first] == ["Confirm your address"]
    assert [t.title for t in second] == ["Sign the declaration"]
    assert foreign == []


@pytest.mark.asyncio
async def test_cross_tenant_invisible(db: dict[str, str], taskbed: dict[str, object]) -> None:
    """Tenant B staff see none of tenant A's tasks."""
    async with _staff(db["app"], str(taskbed["wos_staff_b"])) as session:
        listed = await tasks_router.list_tasks(
            session=session, principal=_principal(["tasks:read"])
        )
    titles = {t.title for t in listed}
    assert "Confirm your address" not in titles
    assert "Internal compliance review" not in titles


@pytest.mark.asyncio
async def test_assignee_validated_against_tenant(
    taskbed: dict[str, object], service_session: AsyncSession
) -> None:
    """A tenant-B creation may not point at tenant-A's role or contact."""
    with pytest.raises(service.TaskError):
        await service.create_task(
            service_session,
            tenant_id=int(taskbed["tenant_b"]),  # type: ignore[call-overload]
            assignee_kind="role",
            assignee_role_id=int(taskbed["role"]),  # type: ignore[call-overload]
            title="Wrong firm's role",
        )
    with pytest.raises(service.TaskError):
        await service.create_task(
            service_session,
            tenant_id=int(taskbed["tenant_b"]),  # type: ignore[call-overload]
            assignee_kind="contact",
            assignee_contact_id=int(taskbed["c1"]),  # type: ignore[call-overload]
            title="Wrong firm's client",
        )


@pytest.mark.asyncio
async def test_automated_source_is_idempotent(
    taskbed: dict[str, object], service_session: AsyncSession
) -> None:
    """Two deliveries of the same (source_kind, source_ref) yield ONE task — the at-least-once
    outbox needs no dedupe of its own."""
    ref = f"fex_{uuid.uuid4().hex[:8]}:step_aml:1:failed"
    first = await service.create_task(
        service_session,
        tenant_id=int(taskbed["tenant_a"]),  # type: ignore[call-overload]
        assignee_kind="role",
        assignee_role_id=int(taskbed["role"]),  # type: ignore[call-overload]
        title="AML review",
        source_kind="flow",
        source_ref=ref,
    )
    again = await service.create_task(
        service_session,
        tenant_id=int(taskbed["tenant_a"]),  # type: ignore[call-overload]
        assignee_kind="role",
        assignee_role_id=int(taskbed["role"]),  # type: ignore[call-overload]
        title="AML review (retry)",
        source_kind="flow",
        source_ref=ref,
    )
    assert first["id"] == again["id"]
    assert again["title"] == "AML review"  # the original row wins


@pytest.mark.asyncio
async def test_complete_transitions_once(
    taskbed: dict[str, object], service_session: AsyncSession
) -> None:
    row = await service.create_task(
        service_session,
        tenant_id=int(taskbed["tenant_a"]),  # type: ignore[call-overload]
        assignee_kind="user",
        assignee_user_id=str(taskbed["staff"]),
        title="One-shot",
    )
    done = await service.complete_task(
        service_session,
        str(row["uid"]),
        completed_by=str(taskbed["staff"]),
        completion_data={"note": "sorted"},
    )
    assert done["status"] == "done" and done["completed_at"] is not None
    with pytest.raises(service.TaskError):
        await service.complete_task(
            service_session, str(row["uid"]), completed_by=str(taskbed["staff"])
        )


@pytest.mark.asyncio
async def test_list_rejects_an_unknown_status_or_category_filter(
    db: dict[str, str], taskbed: dict[str, object]
) -> None:
    """A typo used to return an empty list — indistinguishable from "no tasks". The filters are
    allowlisted, so a bad value is a 422 the caller can see."""
    from fastapi import HTTPException

    async with _staff(db["app"], str(taskbed["wos_staff"])) as session:
        with pytest.raises(HTTPException) as bad_status:
            await tasks_router.list_tasks(
                session=session, principal=_principal(["tasks:read"]), task_status="opne"
            )
        assert bad_status.value.status_code == 422
        with pytest.raises(HTTPException) as bad_category:
            await tasks_router.list_tasks(
                session=session, principal=_principal(["tasks:read"]), category="reveiw"
            )
        assert bad_category.value.status_code == 422
        # A valid one still works — including several statuses as an OR, which is what the
        # dashboard asks (open OR in_progress) so it does not have to fetch 200 and filter.
        assert isinstance(
            await tasks_router.list_tasks(
                session=session, principal=_principal(["tasks:read"]), task_status="open"
            ),
            list,
        )
        assert isinstance(
            await tasks_router.list_tasks(
                session=session,
                principal=_principal(["tasks:read"]),
                task_status=["open", "in_progress"],
            ),
            list,
        )


@pytest.mark.asyncio
async def test_staff_completion_requires_tasks_write(
    db: dict[str, str], taskbed: dict[str, object], service_session: AsyncSession
) -> None:
    """Completion is split by who the caller IS (permission audit L2): a CLIENT stays gate-free
    (their own action items — see test_client_sees_and_completes_only_own), but a STAFF principal
    must hold tasks:write. Tenant RLS making a task visible to every staff login must not mean
    every staff login can settle it."""
    from fastapi import HTTPException

    row = await service.create_task(
        service_session,
        tenant_id=int(taskbed["tenant_a"]),  # type: ignore[call-overload]
        assignee_kind="user",
        assignee_user_id=str(taskbed["staff"]),
        title="Gated completion",
    )
    await service_session.commit()

    async with _staff(db["app"], str(taskbed["wos_staff"])) as session:
        # No tasks:write → refused before any transition happens.
        with pytest.raises(HTTPException) as exc:
            await tasks_router.complete_task(
                str(row["uid"]), principal=_principal([]), session=session
            )
        assert exc.value.status_code == 403
        assert "tasks:write" in str(exc.value.detail)

        # Holding it → the completion proceeds.
        done = await tasks_router.complete_task(
            str(row["uid"]), principal=_principal(["tasks:write"]), session=session
        )
        assert done.status == "done"


@pytest.mark.asyncio
async def test_due_windows_narrow_the_inbox_and_combine(
    db: dict[str, str], taskbed: dict[str, object]
) -> None:
    """`due` replaced the `overdue` flag, which could only ever ask one of three questions.

    An inbox is read with three — what is late, what lands today, what lands this week — and a
    second boolean per window would not have combined. Several windows are an OR, because
    "overdue or due today" is the morning's list.
    """
    from datetime import timedelta

    writer = _principal(["tasks:write"])
    reader = _principal(["tasks:read"])
    async with _staff(db["app"], str(taskbed["wos_staff"])) as session:
        # The windows compare due_date with CURRENT_DATE. The process date and the session date
        # disagree for the hour after midnight during British Summer Time.
        today = (await session.execute(text("SELECT CURRENT_DATE"))).scalar_one()
        for title, due in (
            ("late", today - timedelta(days=3)),
            ("today", today),
            ("this week", today + timedelta(days=3)),
            ("next month", today + timedelta(days=40)),
        ):
            await tasks_router.create_task(
                tasks_router.CreateTask(
                    title=title,
                    due_date=due,
                    assignee_kind="user",
                    assignee_user_id=str(taskbed["staff"]),
                ),
                session=session,
                principal=writer,
            )

        async def titles(**kwargs: object) -> set[str]:
            rows = await tasks_router.list_tasks(session=session, principal=reader, **kwargs)  # type: ignore[arg-type]
            return {t.title for t in rows}

        assert await titles(due=["overdue"]) == {"late"}
        assert await titles(due=["today"]) == {"today"}
        # "This week" counts from today FORWARD — a task that was due last Tuesday is late, not
        # upcoming, and folding it in here would hide it behind the window a reader checks last.
        assert await titles(due=["week"]) == {"today", "this week"}
        # Several windows are an OR.
        assert await titles(due=["overdue", "today"]) == {"late", "today"}
        # No window is no filter, not "match nothing" — the same rule the listing helpers hold.
        assert await titles(due=[]) >= {"late", "today", "this week", "next month"}
        # A window nobody declared is ignored rather than erroring the whole inbox.
        assert await titles(due=["nonsense"]) >= {"late", "next month"}
