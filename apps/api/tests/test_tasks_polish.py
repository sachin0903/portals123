"""Phase-4 polish tests: book-scoped staff RLS, the task notice scan, the activity trail.

- 0028 re-scopes the staff branch: full-book staff (contacts:read_all) see every tenant task;
  book-scoped staff see staff/role work + their own book's client tasks, and NOT an
  out-of-book client's; clients are unchanged.
- The scan turns unnotified + near-due open tasks into ``task.assigned`` / ``task.due_soon`` action
  events (a task notice is a NOTIFICATION — the catalogue owns copy, channels and recipients) and
  stamps each row, so a second pass emits nothing and an unreachable assignee cannot make it spin.
- /tasks/{uid}/activity surfaces the 0001 audit spine rows for the task.

Needs Docker (service_session fixture → PostgresContainer); skipped otherwise.
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import Principal
from app.routers import tasks as tasks_router
from app.tasks import notifier, service


def _principal(perms: list[str]) -> Principal:
    return Principal(user_id="x", organisation_id="x", role="adviser", permissions=perms)


@asynccontextmanager
async def _staff_claims(
    app_url: str, tenant: int, *, contact_ids: list[int], permissions: list[str]
) -> AsyncIterator[AsyncSession]:
    """A platform-portal session with hand-crafted claims: `permissions` drives
    auth.can_read_all(), `contact_ids` is the adviser's book."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        claims = {
            "role": "adviser",
            "permissions": permissions,
            "tenant_id": tenant,
            "tenant_ids": [tenant],
            "portal": "platform",
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
def polishbed(super_engine: object) -> dict[str, object]:
    """One tenant with a role + role member (with email), and two client contacts: c1 (in the
    test adviser's book, has an email) and c2 (out of book, NO email and no portal login)."""
    from sqlalchemy import Engine

    assert isinstance(super_engine, Engine)
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"pol_{s}", "a": f"pol-{s}", "n": f"Polish {s}"},
            ).scalar_one()
        )
        role = int(
            c.execute(
                text(
                    "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                    "VALUES (:t, 'compliance', 'Compliance', 'platform') RETURNING id"
                ),
                {"t": tenant},
            ).scalar_one()
        )
        staff_email = f"tessa_{s}@polish.test"
        staff = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                ),
                {"uid": f"pusr_{s}", "t": tenant, "email": staff_email, "wos": f"pwos_{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships "
                "  (user_id, tenant_id, portal, role_id, status) "
                "VALUES (:u, :t, 'platform', :r, 'active')"
            ),
            {"u": staff, "t": tenant, "r": role},
        )
        c1 = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :u, 'individual', 'Ina Book', :e) RETURNING id"
                ),
                {"t": tenant, "u": f"pc1_{s}", "e": f"ina_{s}@client.test"},
            ).scalar_one()
        )
        c2 = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name) "
                    "VALUES (:t, :u, 'individual', 'Otto Outside') RETURNING id"
                ),
                {"t": tenant, "u": f"pc2_{s}"},
            ).scalar_one()
        )
        c.commit()
    return {
        "tenant": tenant,
        "role": role,
        "staff": staff,
        "staff_email": staff_email,
        "c1": c1,
        "c1_email": f"ina_{s}@client.test",
        "c2": c2,
    }


async def _mk(sess: AsyncSession, bed: dict[str, object], **kw: object) -> dict[str, object]:
    return await service.create_task(sess, tenant_id=int(bed["tenant"]), **kw)  # type: ignore[arg-type, call-overload]


@pytest.mark.asyncio
async def test_book_scoped_staff_visibility(
    db: dict[str, str], polishbed: dict[str, object], service_session: AsyncSession
) -> None:
    role_task = await _mk(
        service_session,
        polishbed,
        assignee_kind="role",
        assignee_role_id=int(polishbed["role"]),  # type: ignore[call-overload]
        title="Internal review",
    )
    in_book = await _mk(
        service_session,
        polishbed,
        assignee_kind="contact",
        assignee_contact_id=int(polishbed["c1"]),  # type: ignore[call-overload]
        title="In-book client action",
    )
    out_book = await _mk(
        service_session,
        polishbed,
        assignee_kind="contact",
        assignee_contact_id=int(polishbed["c2"]),  # type: ignore[call-overload]
        title="Out-of-book client action",
    )
    await service_session.commit()
    tenant = int(polishbed["tenant"])  # type: ignore[call-overload]
    uids = {str(role_task["uid"]), str(in_book["uid"]), str(out_book["uid"])}

    # Full-book staff (contacts:read_all): all three.
    async with _staff_claims(
        db["app"], tenant, contact_ids=[], permissions=["contacts:read_all"]
    ) as s:
        seen = {
            r
            for (r,) in await s.execute(
                text("SELECT uid FROM tasks.tasks WHERE tenant_id = :t"), {"t": tenant}
            )
        }
        assert uids <= seen

    # Book-scoped staff (book = c1, no read_all): the role task + c1's, NOT c2's.
    async with _staff_claims(
        db["app"],
        tenant,
        contact_ids=[int(polishbed["c1"])],
        permissions=[],  # type: ignore[call-overload]
    ) as s:
        seen = {
            r
            for (r,) in await s.execute(
                text("SELECT uid FROM tasks.tasks WHERE tenant_id = :t"), {"t": tenant}
            )
        }
        assert str(role_task["uid"]) in seen
        assert str(in_book["uid"]) in seen
        assert str(out_book["uid"]) not in seen


async def _notices(session: AsyncSession, uid: str) -> list[dict[str, object]]:
    """The action events the scan raised for one task, oldest first."""
    return [
        dict(r)
        for r in (
            await session.execute(
                text(
                    "SELECT event_type, actor_sub, actor_id, route, path_params, metadata "
                    "FROM audit.actions WHERE metadata->'subject'->>'task_uid' = :u ORDER BY id"
                ),
                {"u": uid},
            )
        ).mappings()
    ]


@pytest.mark.asyncio
async def test_the_scan_emits_one_notice_per_task_and_stamps_idempotently(
    polishbed: dict[str, object],
    service_session: AsyncSession,
) -> None:
    """The scan is a PRODUCER, not a sender: it appends one ``audit.actions`` row per task and
    stamps, and the notification catalogue decides who hears about it on which channel."""
    role_task = await _mk(
        service_session,
        polishbed,
        assignee_kind="role",
        assignee_role_id=int(polishbed["role"]),  # type: ignore[call-overload]
        title="Role notice",
    )
    due_task = await _mk(
        service_session,
        polishbed,
        assignee_kind="contact",
        assignee_contact_id=int(polishbed["c1"]),  # type: ignore[call-overload]
        title="Due soon",
        due_date=(date.today() + timedelta(days=1)).isoformat(),
    )

    # Drain until quiet (the scan is worker-wide; other tests' tasks may share the batch).
    for _ in range(20):
        if await notifier.notify_once(service_session) == 0:
            break
    for u in (str(role_task["uid"]), str(due_task["uid"])):
        stamped_at = (
            await service_session.execute(
                text("SELECT notified_at FROM tasks.tasks WHERE uid = :u"), {"u": u}
            )
        ).scalar_one()
        assert stamped_at is not None

    # A ROLE assignment carries the role's SLUG, not the row's assignee_role_id — the notification
    # resolver keys on the slug, so the id alone would reach nobody.
    role_events = await _notices(service_session, str(role_task["uid"]))
    assert [e["event_type"] for e in role_events] == ["task.assigned"]
    subject = role_events[0]["metadata"]["subject"]  # type: ignore[index]
    assert subject["assignee_kind"] == "role"
    assert subject["assignee_role_slug"] == "compliance"
    assert subject["task_id"] == int(role_task["id"])  # type: ignore[call-overload]
    # No person performed this — the scan noticed a state, so a synthetic sub stands in.
    assert role_events[0]["actor_sub"] == "system:task-scan"
    assert role_events[0]["actor_id"] is None
    assert role_events[0]["path_params"] == {"uid": str(role_task["uid"])}

    # Due within DUE_SOON_DAYS → the reminder followed the assign notice, in that order.
    due_events = await _notices(service_session, str(due_task["uid"]))
    assert [e["event_type"] for e in due_events] == ["task.assigned", "task.due_soon"]
    assert due_events[1]["metadata"]["subject"]["assignee_contact_id"] == int(  # type: ignore[index]
        polishbed["c1"]  # type: ignore[call-overload]
    )
    assert due_events[1]["metadata"]["subject"]["due_date"]  # type: ignore[index]

    # Idempotent: the stamps mean a fresh pass emits nothing more for these tasks.
    await notifier.notify_once(service_session)
    assert len(await _notices(service_session, str(role_task["uid"]))) == 1
    assert len(await _notices(service_session, str(due_task["uid"]))) == 2


@pytest.mark.asyncio
async def test_a_task_nobody_can_be_told_about_is_still_emitted_and_stamped(
    polishbed: dict[str, object],
    service_session: AsyncSession,
) -> None:
    """Whether anyone is REACHABLE is the notification layer's question, not the scan's. The scan
    emits and stamps regardless, so an unreachable assignee cannot make it re-read the row for ever
    — and the event is still on the trail."""
    task = await _mk(
        service_session,
        polishbed,
        assignee_kind="contact",
        assignee_contact_id=int(polishbed["c2"]),  # type: ignore[call-overload] — no email, no login
        title="Nobody to tell",
    )
    for _ in range(20):
        if await notifier.notify_once(service_session) == 0:
            break
    assert [e["event_type"] for e in await _notices(service_session, str(task["uid"]))] == [
        "task.assigned"
    ]
    assert (
        await service_session.execute(
            text("SELECT notified_at FROM tasks.tasks WHERE uid = :u"), {"u": str(task["uid"])}
        )
    ).scalar_one() is not None


@pytest.mark.asyncio
async def test_activity_trail_endpoint(
    db: dict[str, str], polishbed: dict[str, object], service_session: AsyncSession
) -> None:
    row = await _mk(
        service_session,
        polishbed,
        assignee_kind="role",
        assignee_role_id=int(polishbed["role"]),  # type: ignore[call-overload]
        title="Audited",
    )
    await service.complete_task(
        service_session, str(row["uid"]), completed_by=str(polishbed["staff"])
    )
    await service_session.commit()

    async with _staff_claims(
        db["app"],
        int(polishbed["tenant"]),
        contact_ids=[],
        permissions=["contacts:read_all"],  # type: ignore[call-overload]
    ) as s:
        events = await tasks_router.task_activity(
            str(row["uid"]), session=s, principal=_principal(["tasks:read"])
        )
        assert [e.action for e in events][:1] == ["tasks.tasks.insert"]
        updates = [e for e in events if e.action == "tasks.tasks.update"]
        assert updates and any("status" in (e.detail or {}).get("changed", {}) for e in updates)
        with pytest.raises(Exception):  # noqa: B017 — 404 HTTPException
            await tasks_router.task_activity(
                "tsk_missing", session=s, principal=_principal(["tasks:read"])
            )


@pytest.mark.asyncio
async def test_reassign_clears_the_notify_stamps(
    polishbed: dict[str, object], service_session: AsyncSession
) -> None:
    """`notified_at` is the notifier's "this assignee has been told" stamp. Leaving it set on
    reassignment meant the NEW assignee was never emailed — the task simply moved silently."""
    task = await _mk(
        service_session,
        polishbed,
        assignee_kind="role",
        assignee_role_id=int(polishbed["role"]),  # type: ignore[call-overload]
        title="Hand this over",
        due_date=(date.today() + timedelta(days=1)).isoformat(),
    )
    uid = str(task["uid"])
    await service_session.execute(
        text(
            "UPDATE tasks.tasks SET notified_at = now(), due_soon_notified_at = now() "
            "WHERE uid = :u"
        ),
        {"u": uid},
    )

    await service.reassign_task(
        service_session,
        uid,
        tenant_id=int(polishbed["tenant"]),  # type: ignore[arg-type]
        assignee_kind="contact",
        assignee_contact_id=int(polishbed["c1"]),  # type: ignore[arg-type]
    )
    row = (
        (
            await service_session.execute(
                text(
                    "SELECT assignee_kind, assignee_contact_id, notified_at, due_soon_notified_at "
                    "FROM tasks.tasks WHERE uid = :u"
                ),
                {"u": uid},
            )
        )
        .mappings()
        .one()
    )
    assert row["assignee_kind"] == "contact"
    assert row["assignee_contact_id"] == int(polishbed["c1"])  # type: ignore[call-overload]
    assert row["notified_at"] is None, "the new assignee must be notifiable"
    assert row["due_soon_notified_at"] is None, "the new owner gets their own near-due reminder"
