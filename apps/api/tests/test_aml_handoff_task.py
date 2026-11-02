"""Regression: the AML screen task must insert into the real database.

``app/complycube/screen.py::raise_screen_task`` is how a journey's AML screen that could not run
by itself reaches a Graphene operator. It raises its task through
``app/complycube/tasks.py::raise_screening_task``, which passes two values two separate gates must
both accept:

* ``category='aml'`` — ``app/tasks/service.py::CATEGORIES``, checked by ``create_task`` in Python
  before the database is asked;
* ``source_kind='aml'`` — the ``tasks_source_kind_check`` constraint (0044 widens it).

A value either gate refuses kills the step's own transaction, on the exact path whose whole
purpose is making sure a client's screen reaches a human. These tests drive the REAL adapter
rather than asserting on the constraint text, because the Python-side tuple and the database CHECK
are two separate gates and a test that only proved one would let the other drift.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.complycube import screen as complycube_screen
from app.complycube import tasks as screening_tasks
from app.tasks import service as tasks_service

Maker = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def bed(maker: Maker) -> dict[str, Any]:
    """A firm with a client, and Graphene's operations tenant with its compliance role — the two
    sides the screen task spans."""
    nonce = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))

        async def tenant(prefix: str) -> int:
            return int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO auth.tenants (uid, alias, name) "
                            "VALUES (:u, :a, :n) RETURNING id"
                        ),
                        {
                            "u": f"{prefix}_{nonce}",
                            "a": f"{prefix}-{nonce}",
                            "n": f"{prefix} {nonce}",
                        },
                    )
                ).scalar_one()
            )

        firm_id = await tenant("aht")
        ops_id = await tenant("aho")
        contact_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, name, type, service_type) "
                        "VALUES (:t, :u, 'Ada Client', 'individual', 'advised') RETURNING id"
                    ),
                    {"t": firm_id, "u": f"con_{nonce}"},
                )
            ).scalar_one()
        )
        role_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                        "VALUES (:t, :s, 'Compliance', 'operations') RETURNING id"
                    ),
                    {"t": ops_id, "s": screening_tasks.COMPLIANCE_ROLE_SLUG},
                )
            ).scalar_one()
        )
    return {
        "nonce": nonce,
        "firm_id": firm_id,
        "ops_id": ops_id,
        "contact_id": contact_id,
        "role_id": role_id,
        "maker": maker,
    }


def _ops_tenant_is(bed: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_ops_tenant(session: AsyncSession) -> int:
        return int(bed["ops_id"])

    monkeypatch.setattr(screening_tasks, "operations_tenant_id", fake_ops_tenant)


async def _ask_for_a_screen(bed: dict[str, Any], *, kyc_check_id: int) -> None:
    """What the worker does when a journey's AML screen cannot run by itself."""
    maker: Maker = bed["maker"]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await complycube_screen.raise_screen_task(
            s,
            tenant_id=int(bed["firm_id"]),
            contact_id=int(bed["contact_id"]),
            kyc_check_id=kyc_check_id,
            reason="ComplyCube is not configured",
        )


async def _ops_tasks(bed: dict[str, Any]) -> list[dict[str, Any]]:
    maker: Maker = bed["maker"]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT tenant_id, category, source_kind, source_ref, priority, "
                        "  assignee_kind, assignee_role_id, subject_type, subject_id, title, "
                        "  description "
                        "FROM tasks.tasks WHERE tenant_id = :t ORDER BY id"
                    ),
                    {"t": bed["ops_id"]},
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


@pytest.mark.asyncio
async def test_the_aml_screen_task_actually_inserts(
    bed: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE regression: both gates accept the task the adapter raises."""
    _ops_tenant_is(bed, monkeypatch)

    await _ask_for_a_screen(bed, kyc_check_id=77)

    [row] = await _ops_tasks(bed)
    assert row["category"] == "aml"
    assert row["source_kind"] == "aml"
    # It belongs to OPERATIONS, not the firm whose client it is about — a firm must not see one of
    # its own clients described as a possible sanctions match in its queue (docs/aml.md).
    assert row["tenant_id"] == bed["ops_id"]
    assert row["assignee_kind"] == "role"
    assert row["assignee_role_id"] == bed["role_id"]
    assert row["priority"] == "high"
    # No structured subject: the contact belongs to the FIRM and this task to OPERATIONS, and
    # tasks_service validates a subject against the task's own tenant. The client is still
    # identified — by name in the copy and by id in the source_ref, which is also what makes a
    # repeat idempotent.
    assert row["subject_type"] is None
    assert row["subject_id"] is None
    assert row["title"] == "Screen Ada Client"
    assert f"aht {bed['nonce']}" in row["description"]
    assert row["source_ref"] == f"screen:{bed['contact_id']}:77"
    assert row["source_ref"] == complycube_screen.screen_task_ref(int(bed["contact_id"]), 77)


@pytest.mark.asyncio
async def test_the_screen_task_is_idempotent_on_its_source_ref(
    bed: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A step started twice for the same check (a retry) must not ask for the same screen twice.
    That relies on ``tasks_source_ref_key``, which only applies WHERE source_kind <> 'manual' — so
    it only works because 'aml' is a legal source_kind at all. A different check is a different
    screen, and gets its own task."""
    _ops_tenant_is(bed, monkeypatch)

    for _ in range(2):
        await _ask_for_a_screen(bed, kyc_check_id=77)
    assert [t["source_ref"] for t in await _ops_tasks(bed)] == [f"screen:{bed['contact_id']}:77"]

    await _ask_for_a_screen(bed, kyc_check_id=78)
    assert [t["source_ref"] for t in await _ops_tasks(bed)] == [
        f"screen:{bed['contact_id']}:77",
        f"screen:{bed['contact_id']}:78",
    ]


def test_the_service_and_the_database_agree_about_categories() -> None:
    """The Python gate. Keeping it in step with the CHECK is the point of the comment on
    ``CATEGORIES``; this is the test that notices when they drift."""
    assert "aml" in tasks_service.CATEGORIES


@pytest.mark.asyncio
async def test_the_database_accepts_every_category_the_service_does(bed: dict[str, Any]) -> None:
    """Belt for the above: the Python tuple is only useful if the CHECK agrees with it, and a
    tuple entry the database rejects is exactly the failure this file guards against."""
    maker: Maker = bed["maker"]
    for i, category in enumerate(tasks_service.CATEGORIES):
        async with maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            await s.execute(
                text(
                    "INSERT INTO tasks.tasks (uid, tenant_id, assignee_kind, assignee_role_id, "
                    "  category, title) VALUES (:u, :t, 'role', :r, :c, 'x')"
                ),
                {
                    "u": f"tsk_cat_{bed['nonce']}_{i}",
                    "t": bed["ops_id"],
                    "r": bed["role_id"],
                    "c": category,
                },
            )


@pytest.mark.asyncio
async def test_the_database_accepts_every_source_kind_in_use(bed: dict[str, Any]) -> None:
    """The source_kind half: every kind in use, re-asserted so a future narrowing is caught."""
    maker: Maker = bed["maker"]
    for i, kind in enumerate(("manual", "flow", "document", "system", "automation", "aml")):
        async with maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            await s.execute(
                text(
                    "INSERT INTO tasks.tasks (uid, tenant_id, assignee_kind, assignee_role_id, "
                    "  source_kind, title) VALUES (:u, :t, 'role', :r, :sk, 'x')"
                ),
                {
                    "u": f"tsk_sk_{bed['nonce']}_{i}",
                    "t": bed["ops_id"],
                    "r": bed["role_id"],
                    "sk": kind,
                },
            )
