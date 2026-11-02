"""Phase-2 pipeline tests: flow-step transitions → flows.outbox → standalone tasks.

Proves the transactional-outbox path end-to-end against a real Postgres (testcontainer):

- ``validate_definition`` accepts + normalises per-step ``tasks`` specs and rejects bad ones.
- A step FAILURE emits ``task.requested`` outbox rows (same tx as the transition) for every
  matching spec; the dispatcher turns them into tasks (role slug resolved per-tenant,
  participant-assigned specs bound to the step's contact, {placeholders} filled, due_days
  applied) — and REDELIVERY collapses to the same task (source_ref idempotency).
- Bad authoring (unknown role slug) and unknown event types dead-letter with the reason instead
  of retrying forever; the flow itself is never touched.
- The publish preflight flags task specs pointing at roles/users the firm doesn't have.

Needs Docker (service_session fixture → PostgresContainer); skipped otherwise.
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.flows import dispatcher, notifications, orchestrator
from app.flows.definitions import DefinitionError, validate_definition
from app.flows.domain_events import StepEvent, StepSignal
from app.flows.planning import AccountSpec, ApplicationGraph, Participant
from app.routers.flows import _reference_issues
from tests.conftest import SECCL_PLATFORM_SQL

# ---------------------------------------------------------------------------
# Definition validation (no DB)
# ---------------------------------------------------------------------------


def _step(**tasks_kwargs: object) -> dict[str, object]:
    return {
        "steps": [
            {
                "id": "aml",
                "type": "review",
                "title": "AML check",
                "config": {},
                **tasks_kwargs,
            }
        ],
        "milestones": [],
    }


def test_task_specs_validate_and_normalise() -> None:
    out = validate_definition(
        _step(
            tasks=[
                {
                    "on": "failed",
                    "assign_to": {"kind": "role", "role": "compliance"},
                    "title": "AML review for {participant_name}",
                    "due_days": 3,
                    "priority": "high",
                }
            ]
        )
    )
    spec = out["steps"][0]["tasks"][0]  # type: ignore[index]
    assert spec["category"] == "generic"  # defaulted
    assert spec["assign_to"] == {"kind": "role", "role": "compliance"}
    # A step with no tasks key normalises to [].
    assert validate_definition(_step())["steps"][0]["tasks"] == []  # type: ignore[index]


@pytest.mark.parametrize(
    "bad",
    [
        [{"on": "exploded", "assign_to": {"kind": "role", "role": "x"}, "title": "t"}],
        [{"on": "failed", "assign_to": {"kind": "wizard"}, "title": "t"}],
        [{"on": "failed", "assign_to": {"kind": "role", "role": "Not A Slug!"}, "title": "t"}],
        [{"on": "failed", "assign_to": {"kind": "role", "role": "x"}, "title": "  "}],
        [
            {
                "on": "failed",
                "assign_to": {"kind": "role", "role": "x"},
                "title": "t",
                "due_days": -1,
            }
        ],
        [{"on": "failed", "assign_to": {"kind": "user"}, "title": "t"}],
        "not-a-list",
    ],
)
def test_task_specs_reject_bad_authoring(bad: object) -> None:
    with pytest.raises(DefinitionError):
        validate_definition(_step(tasks=bad))


# ---------------------------------------------------------------------------
# DB path: emission → dispatch → task (and the failure modes)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _claims_session(app_url: str, tenant: int) -> AsyncIterator[AsyncSession]:
    """A staff-portal session with hand-crafted claims (enough for auth.tenant_id())."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        claims = {
            "role": "adviser",
            "tenant_id": tenant,
            "tenant_ids": [tenant],
            "portal": "platform",
            "contact_ids": [],
            "account_ids": [],
            "role_ids": [],
        }
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)}
        )
        yield session
    await engine.dispose()


async def _seed_execution(sess: AsyncSession, definition: dict[str, object]) -> dict[str, object]:
    """One tenant, one owner, one account, a compliance role, and a started execution of
    ``definition`` — the minimum world a step transition needs."""
    s = uuid.uuid4().hex[:8]
    tenant = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"tp_{s}", "a": f"tp-{s}", "n": f"TP {s}"},
            )
        ).scalar_one()
    )
    role = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                    "VALUES (:t, 'compliance', 'Compliance', 'platform') RETURNING id"
                ),
                {"t": tenant},
            )
        ).scalar_one()
    )
    platform = str(
        (
            await sess.execute(
                text(SECCL_PLATFORM_SQL),
                {"n": f"Seccl {s}"},
            )
        ).scalar_one()
    )
    app_id = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO crm.applications (tenant_id, provider, uid, status) "
                    "VALUES (:t, :p, :u, 'draft') RETURNING id"
                ),
                {"t": tenant, "p": platform, "u": f"app_{s}"},
            )
        ).scalar_one()
    )
    contact = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name) "
                    "VALUES (:t, :u, 'individual', 'Petra Participant') RETURNING id"
                ),
                {"t": tenant, "u": f"pc_{s}"},
            )
        ).scalar_one()
    )
    account = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, contact_id, application_id, "
                    "  status, uid) VALUES (:t, :c, :a, 'draft', :u) RETURNING id"
                ),
                {"t": tenant, "c": contact, "a": app_id, "u": f"acc_{s}"},
            )
        ).scalar_one()
    )
    await sess.execute(
        text(
            "INSERT INTO crm.account_contacts (tenant_id, application_id, account_id, "
            "  contact_id, role) VALUES (:t, :a, :acc, :c, CAST('owner' AS crm.account_role))"
        ),
        {"t": tenant, "a": app_id, "acc": account, "c": contact},
    )
    flow_id = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
                    "VALUES (:u, :t, :code, 'Onboarding', 'published') RETURNING id"
                ),
                {"u": f"flw_{s}", "t": tenant, "code": f"onb_{s}"},
            )
        ).scalar_one()
    )
    vid = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO flows.flow_versions "
                    "  (uid, tenant_id, flow_id, version, status, definition, published_at) "
                    "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id"
                ),
                {"u": f"flv_{s}", "t": tenant, "f": flow_id, "d": json.dumps(definition)},
            )
        ).scalar_one()
    )
    graph = ApplicationGraph(
        participants=(Participant(contact, "individual", frozenset({"owner"})),),
        accounts=(AccountSpec(account, (contact,)),),
    )
    created = await orchestrator.start_execution(
        sess,
        tenant_id=tenant,
        flow_id=flow_id,
        flow_version_id=vid,
        definition=definition,
        graph=graph,
        application_id=app_id,
        data={},
    )
    return {
        "tenant": tenant,
        "role": role,
        "contact": contact,
        "execution_id": int(created["id"]),
        "execution_uid": str(created["uid"]),
    }


_PIPELINE_DEFINITION = validate_definition(
    {
        "steps": [
            {
                "id": "idv",
                "type": "verify_identity",
                "title": "Verify identity",
                "scope": "participant",
                "config": {},
                "tasks": [
                    {
                        "on": "failed",
                        "assign_to": {"kind": "role", "role": "compliance"},
                        "title": "AML review for {participant_name}",
                        "category": "review",
                        "priority": "high",
                        "due_days": 3,
                    },
                    {
                        "on": "failed",
                        "assign_to": {"kind": "participant"},
                        "title": "We need more identity documents",
                        "category": "upload",
                    },
                ],
            },
        ],
        "milestones": [],
    }
)


@pytest.mark.asyncio
async def test_step_failure_emits_and_dispatch_creates_tasks(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    world = await _seed_execution(sess, _PIPELINE_DEFINITION)
    case_id = int(
        (
            await sess.execute(
                text("SELECT id FROM flows.cases WHERE execution_id = :e AND kind = 'profile'"),
                {"e": world["execution_id"]},
            )
        ).scalar_one()
    )

    # The transition emits BOTH matching specs onto the outbox in the same transaction.
    event = StepEvent(
        StepSignal.FAILED,
        case_id,
        "idv",
        participant_subject_id=int(world["contact"]),  # type: ignore[call-overload]
    )
    await orchestrator.apply_step_event(sess, int(world["tenant"]), event)  # type: ignore[call-overload]
    queued = (
        await sess.execute(
            text(
                "SELECT count(*) FROM flows.outbox WHERE tenant_id = :t "
                "AND event_type = 'task.requested' AND status = 'queued'"
            ),
            {"t": world["tenant"]},
        )
    ).scalar_one()
    assert queued == 2

    # Dispatch: role slug resolved, participant bound, placeholders filled, due date applied.
    delivered = await dispatcher.dispatch_due(sess, now=datetime.now(UTC))
    assert delivered == 2
    tasks = (
        (
            await sess.execute(
                text(
                    "SELECT assignee_kind, assignee_role_id, assignee_contact_id, title, "
                    "  category, priority, due_date, subject_type, subject_id, status "
                    "FROM tasks.tasks WHERE tenant_id = :t ORDER BY id"
                ),
                {"t": world["tenant"]},
            )
        )
        .mappings()
        .all()
    )
    assert len(tasks) == 2
    role_task = next(t for t in tasks if t["assignee_kind"] == "role")
    client_task = next(t for t in tasks if t["assignee_kind"] == "contact")
    assert role_task["assignee_role_id"] == world["role"]
    assert role_task["title"] == "AML review for Petra Participant"
    assert role_task["priority"] == "high" and role_task["due_date"] is not None
    assert client_task["assignee_contact_id"] == world["contact"]
    assert all(
        t["subject_type"] == "execution" and t["subject_id"] == world["execution_uid"]
        for t in tasks
    )

    # Redelivery: re-firing the SAME transition re-enqueues, but the tasks table collapses on
    # source_ref — still exactly two tasks.
    await orchestrator.apply_step_event(sess, int(world["tenant"]), event)  # type: ignore[call-overload]
    await dispatcher.dispatch_due(sess, now=datetime.now(UTC))
    count = (
        await sess.execute(
            text("SELECT count(*) FROM tasks.tasks WHERE tenant_id = :t"), {"t": world["tenant"]}
        )
    ).scalar_one()
    assert count == 2


@pytest.mark.asyncio
async def test_a_journey_assignee_receives_staff_tasks_not_the_clients(
    service_session: AsyncSession,
) -> None:
    """Staff specs follow the journey's assignee. The client's own task stays with them."""
    sess = service_session
    world = await _seed_execution(sess, _PIPELINE_DEFINITION)
    s = uuid.uuid4().hex[:8]
    assignee = str(
        (
            await sess.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, "
                    "  first_name, last_name, enabled) "
                    "VALUES (:uid, :t, :e, :w, 'Priya', 'Shah', true) RETURNING id"
                ),
                {
                    "uid": f"asn_{s}",
                    "t": world["tenant"],
                    "e": f"priya_{s}@t.test",
                    "w": f"wos_{s}",
                },
            )
        ).scalar_one()
    )
    await sess.execute(
        text(
            "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, status) "
            "VALUES (:u, :t, 'platform', 'active')"
        ),
        {"u": assignee, "t": world["tenant"]},
    )
    await sess.execute(
        text("UPDATE flows.executions SET assignee_user_id = CAST(:u AS uuid) WHERE id = :e"),
        {"u": assignee, "e": world["execution_id"]},
    )
    case_id = int(
        (
            await sess.execute(
                text("SELECT id FROM flows.cases WHERE execution_id = :e AND kind = 'profile'"),
                {"e": world["execution_id"]},
            )
        ).scalar_one()
    )
    await orchestrator.apply_step_event(
        sess,
        int(world["tenant"]),
        StepEvent(
            StepSignal.FAILED,
            case_id,
            "idv",
            participant_subject_id=int(world["contact"]),  # type: ignore[call-overload]
        ),
    )
    delivered = await dispatcher.dispatch_due(sess, now=datetime.now(UTC))
    assert delivered == 2
    tasks = (
        (
            await sess.execute(
                text(
                    "SELECT assignee_kind, assignee_user_id::text AS assignee_user_id, "
                    "  assignee_contact_id, assignee_role_id "
                    "FROM tasks.tasks WHERE tenant_id = :t"
                ),
                {"t": world["tenant"]},
            )
        )
        .mappings()
        .all()
    )
    staff = next(t for t in tasks if t["assignee_kind"] == "user")
    client = next(t for t in tasks if t["assignee_kind"] == "contact")
    assert staff["assignee_user_id"] == assignee
    assert staff["assignee_role_id"] is None
    assert client["assignee_contact_id"] == world["contact"]


@pytest.mark.asyncio
async def test_bad_role_and_unknown_event_dead_letter(service_session: AsyncSession) -> None:
    """Authoring errors are permanent: dead-letter with the reason, no task, no infinite retry."""
    sess = service_session
    s = uuid.uuid4().hex[:8]
    tenant = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"dl_{s}", "a": f"dl-{s}", "n": f"DL {s}"},
            )
        ).scalar_one()
    )
    await notifications.enqueue(
        sess,
        tenant_id=tenant,
        event_type="task.requested",
        payload={
            "spec": {"assign_to": {"kind": "role", "role": "nonexistent"}, "title": "x"},
            "execution_uid": "fex_none",
            "source_ref": f"dead_{s}",
        },
    )
    await notifications.enqueue(sess, tenant_id=tenant, event_type="mystery.event", payload={})
    delivered = await dispatcher.dispatch_due(sess, now=datetime.now(UTC))
    assert delivered == 0
    rows = (
        (
            await sess.execute(
                text(
                    "SELECT event_type, status, last_error FROM flows.outbox "
                    "WHERE tenant_id = :t ORDER BY id"
                ),
                {"t": tenant},
            )
        )
        .mappings()
        .all()
    )
    assert [r["status"] for r in rows] == ["dead_letter", "dead_letter"]
    assert "nonexistent" in str(rows[0]["last_error"])
    assert "no handler" in str(rows[1]["last_error"])
    none_created = (
        await sess.execute(
            text("SELECT count(*) FROM tasks.tasks WHERE tenant_id = :t"), {"t": tenant}
        )
    ).scalar_one()
    assert none_created == 0


@pytest.mark.asyncio
async def test_publish_preflight_flags_missing_role(
    db: dict[str, str], service_session: AsyncSession
) -> None:
    """A definition raising tasks for a role the firm doesn't have fails the publish checks."""
    s = uuid.uuid4().hex[:8]
    tenant = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"pf_{s}", "a": f"pf-{s}", "n": f"PF {s}"},
            )
        ).scalar_one()
    )
    await service_session.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
            "VALUES (:t, 'compliance', 'Compliance', 'platform')"
        ),
        {"t": tenant},
    )
    await service_session.commit()

    definition = validate_definition(
        _step(
            tasks=[
                {"assign_to": {"kind": "role", "role": "compliance"}, "title": "ok"},
                {"assign_to": {"kind": "role", "role": "missing_team"}, "title": "bad"},
            ]
        )
    )
    async with _claims_session(db["app"], tenant) as session:
        issues = await _reference_issues(session, definition)
    assert len(issues) == 1
    assert "missing_team" in issues[0].message
    # The issue names the node it is about, so the builder can badge it (Flows v3 phase C).
    assert issues[0].node_id == "aml"
