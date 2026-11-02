"""Integration test for the generic orchestrator against a real Postgres (testcontainer).

Proves the three-tier walking skeleton end-to-end for a JOINT account:
  * one application, two individual owners, one joint account;
  * start_execution fans out 2 profile (KYC) cases + 1 account case;
  * the account case is BLOCKED until BOTH owners' profile cases complete (cross-case KYC gate);
  * each owner's identity is completed ONCE; the gate releases; the account step completes;
  * the execution rolls up to completed and the crm.applications projection follows.

Needs Docker (service_session fixture → PostgresContainer); skipped otherwise.
"""

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.flows import orchestrator
from app.flows.domain_events import StepEvent, StepSignal
from app.flows.planning import AccountSpec, ApplicationGraph, Participant
from tests.conftest import SECCL_PLATFORM_SQL

_DEFINITION = {
    "steps": [
        {
            "id": "idv",
            "type": "verify_identity",
            "title": "Verify identity",
            "scope": "participant",
            "config": {},
        },
        {
            "id": "setup",
            "type": "task",
            "title": "Open the account",
            "scope": "account",
            "assignee": "staff",
            "config": {},
        },
    ],
    "milestones": [],
}


async def _case(
    session: AsyncSession, execution_id: int, kind: str, subject_id: int | None
) -> dict:
    q = (
        "SELECT id, status FROM flows.cases WHERE execution_id = :e AND kind = :k "
        "AND subject_id IS NOT DISTINCT FROM :s"
    )
    return dict(
        (await session.execute(text(q), {"e": execution_id, "k": kind, "s": subject_id}))
        .mappings()
        .one()
    )


@pytest.mark.asyncio
async def test_joint_account_kyc_gate_then_complete(service_session: AsyncSession) -> None:
    s = uuid.uuid4().hex[:8]
    sess = service_session
    tenant = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"orch_{s}", "a": f"orch-{s}", "n": f"Orch {s}"},
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
    c1 = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, email) "
                    "VALUES (:t, :u, 'individual', :e) RETURNING id"
                ),
                {"t": tenant, "u": f"c1_{s}", "e": f"a_{s}@t.test"},
            )
        ).scalar_one()
    )
    c2 = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, email) "
                    "VALUES (:t, :u, 'individual', :e) RETURNING id"
                ),
                {"t": tenant, "u": f"c2_{s}", "e": f"b_{s}@t.test"},
            )
        ).scalar_one()
    )
    account = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO platform.accounts "
                    "  (tenant_id, contact_id, application_id, status, uid) "
                    "VALUES (:t, :c, :a, 'draft', :u) RETURNING id"
                ),
                {"t": tenant, "c": c1, "a": app_id, "u": f"acc_{s}"},
            )
        ).scalar_one()
    )
    for cid in (c1, c2):
        await sess.execute(
            text(
                "INSERT INTO crm.account_contacts "
                "  (tenant_id, application_id, account_id, contact_id, role) "
                "VALUES (:t, :a, :acc, :c, CAST('owner' AS crm.account_role))"
            ),
            {"t": tenant, "a": app_id, "acc": account, "c": cid},
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
                {"u": f"flv_{s}", "t": tenant, "f": flow_id, "d": json.dumps(_DEFINITION)},
            )
        ).scalar_one()
    )

    graph = ApplicationGraph(
        participants=(
            Participant(c1, "individual", frozenset({"owner"})),
            Participant(c2, "individual", frozenset({"owner"})),
        ),
        accounts=(AccountSpec(account, (c1, c2)),),
    )
    created = await orchestrator.start_execution(
        sess,
        tenant_id=tenant,
        flow_id=flow_id,
        flow_version_id=vid,
        definition=_DEFINITION,
        graph=graph,
        application_id=app_id,
        data={"adviser_declared": True},
    )
    execution_id = int(created["id"])

    # Fanned out: 2 profile cases + 1 account case. The account case is BLOCKED on both KYC gates.
    prof1 = await _case(sess, execution_id, "profile", c1)
    prof2 = await _case(sess, execution_id, "profile", c2)
    acc_case = await _case(sess, execution_id, "account", account)
    assert acc_case["status"] == "blocked"

    # Complete owner 1's identity — the gate needs BOTH, so the account stays blocked.
    await orchestrator.apply_step_event(
        sess, tenant, StepEvent(StepSignal.COMPLETED, prof1["id"], "idv", participant_subject_id=c1)
    )
    assert (await _case(sess, execution_id, "account", account))["status"] == "blocked"
    assert (await _case(sess, execution_id, "profile", c1))["status"] == "completed"

    # Complete owner 2's identity — both cleared, the account case releases (no longer blocked).
    await orchestrator.apply_step_event(
        sess, tenant, StepEvent(StepSignal.COMPLETED, prof2["id"], "idv", participant_subject_id=c2)
    )
    assert (await _case(sess, execution_id, "account", account))["status"] != "blocked"

    # Complete the account step → account case + execution complete; projection follows.
    await orchestrator.apply_step_event(
        sess, tenant, StepEvent(StepSignal.COMPLETED, acc_case["id"], "setup")
    )
    assert (await _case(sess, execution_id, "account", account))["status"] == "completed"

    exec_status = (
        await sess.execute(
            text("SELECT status FROM flows.executions WHERE id = :e"), {"e": execution_id}
        )
    ).scalar_one()
    assert exec_status == "completed"
    app_status = (
        await sess.execute(text("SELECT status FROM crm.applications WHERE id = :a"), {"a": app_id})
    ).scalar_one()
    assert app_status == "completed"  # crm.applications projection reached the terminal label

    # KYC ran exactly once per person (no duplication): two idv steps, both fulfilled.
    idv_count = (
        await sess.execute(
            text(
                "SELECT count(*) FROM flows.case_steps WHERE step_type = 'verify_identity' "
                "AND status = 'fulfilled' AND case_id IN "
                "  (SELECT id FROM flows.cases WHERE execution_id = :e)"
            ),
            {"e": execution_id},
        )
    ).scalar_one()
    assert idv_count == 2
