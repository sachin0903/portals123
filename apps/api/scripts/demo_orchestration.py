"""Live smoke of the v2 orchestrator against the configured (dev) Postgres — NOT a test.

Seeds a throwaway tenant with a joint account + a tiered onboarding flow, runs the generic
orchestrator end-to-end (KYC gate → release → complete → crm.applications projection), prints the
resulting state, then deletes the throwaway tenant (CASCADE cleans everything up).

    uv run --directory apps/api python scripts/demo_orchestration.py
"""

from __future__ import annotations

import asyncio
import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.flows import orchestrator
from app.flows.planning import AccountSpec, ApplicationGraph, Participant

DEFINITION = {
    "steps": [
        {
            "id": "details",
            "type": "fill_form",
            "title": "Your details",
            "scope": "participant",
            "config": {"form_uid": "x"},
        },
        {
            "id": "idv",
            "type": "verify_identity",
            "title": "Verify identity",
            "scope": "participant",
            "config": {},
        },
        {
            "id": "terms",
            "type": "accept_terms",
            "title": "Accept terms",
            "scope": "account",
            "per_participant": True,
            "config": {"document_uid": "x"},
        },
        {
            "id": "open",
            "type": "task",
            "title": "Open the account",
            "scope": "account",
            "assignee": "staff",
            "config": {},
        },
    ],
}


async def _q(conn, sql: str, **p):
    return (await conn.execute(text(sql), p)).mappings().all()


async def main() -> None:
    engine = create_async_engine(settings.migration_database_url)
    s = uuid.uuid4().hex[:8]
    # Run the whole demo inside a transaction we ROLL BACK — nothing persists to the dev DB (no
    # pollution, and no tenant-delete cleanup to trip the audit-trigger FK).
    async with engine.connect() as conn:
        trans = await conn.begin()
        await conn.execute(text("SET ROLE service_role"))
        tenant = (
            await conn.execute(
                text("INSERT INTO auth.tenants (uid, alias, name) VALUES (:u,:a,:n) RETURNING id"),
                {"u": f"demo_{s}", "a": f"demo-{s}", "n": f"Demo {s}"},
            )
        ).scalar_one()
        try:
            # The custodian is a VALUE since 0138 — `platform.platforms` is gone, and the id
            # every caller keyed on IS the provider code.
            plat = "seccl"
            app_id = (
                await conn.execute(
                    text(
                        "INSERT INTO crm.applications (tenant_id, provider, uid, status, custody) "
                        "VALUES (:t,CAST(:p AS platform.provider_code),:u,'draft','onshore') "
                        "RETURNING id"
                    ),
                    {"t": tenant, "p": plat, "u": f"app_{s}"},
                )
            ).scalar_one()
            c1 = (
                await conn.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, email) "
                        "VALUES (:t,:u,'individual','Ada','Lovelace',:e) RETURNING id"
                    ),
                    {"t": tenant, "u": f"c1_{s}", "e": f"ada_{s}@demo.test"},
                )
            ).scalar_one()
            c2 = (
                await conn.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, email) "
                        "VALUES (:t,:u,'individual','Alan','Turing',:e) RETURNING id"
                    ),
                    {"t": tenant, "u": f"c2_{s}", "e": f"alan_{s}@demo.test"},
                )
            ).scalar_one()
            acc = (
                await conn.execute(
                    text(
                        "INSERT INTO platform.accounts (tenant_id, contact_id, application_id, status, uid) "
                        "VALUES (:t,:c,:a,'draft',:u) RETURNING id"
                    ),
                    {"t": tenant, "c": c1, "a": app_id, "u": f"acc_{s}"},
                )
            ).scalar_one()
            for cid in (c1, c2):
                await conn.execute(
                    text(
                        "INSERT INTO crm.account_contacts (tenant_id, application_id, account_id, "
                        "contact_id, role) VALUES (:t,:a,:acc,:c, CAST('owner' AS crm.account_role))"
                    ),
                    {"t": tenant, "a": app_id, "acc": acc, "c": cid},
                )
            flow = (
                await conn.execute(
                    text(
                        "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
                        "VALUES (:u,:t,:code,'Onboarding','published') RETURNING id"
                    ),
                    {"u": f"flw_{s}", "t": tenant, "code": f"onb_{s}"},
                )
            ).scalar_one()
            vid = (
                await conn.execute(
                    text(
                        "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status, "
                        "definition, published_at) VALUES (:u,:t,:f,1,'published',CAST(:d AS jsonb),now()) "
                        "RETURNING id"
                    ),
                    {"u": f"flv_{s}", "t": tenant, "f": flow, "d": json.dumps(DEFINITION)},
                )
            ).scalar_one()

            graph = ApplicationGraph(
                participants=(
                    Participant(c1, "individual", frozenset({"owner"})),
                    Participant(c2, "individual", frozenset({"owner"})),
                ),
                accounts=(AccountSpec(acc, (c1, c2)),),
            )
            created = await orchestrator.start_execution(
                conn,
                tenant_id=tenant,
                flow_id=flow,
                flow_version_id=vid,
                definition=DEFINITION,
                graph=graph,
                application_id=app_id,
                data={"adviser_declared": True},
            )
            eid = int(created["id"])

            async def snapshot(label: str) -> None:
                ex = (
                    await conn.execute(
                        text("SELECT status FROM flows.executions WHERE id=:e"), {"e": eid}
                    )
                ).scalar_one()
                appst = (
                    await conn.execute(
                        text("SELECT status FROM crm.applications WHERE id=:a"), {"a": app_id}
                    )
                ).scalar_one()
                cases = await _q(
                    conn,
                    "SELECT kind, subject_id, status FROM flows.cases "
                    "WHERE execution_id=:e ORDER BY kind, subject_id",
                    e=eid,
                )
                print(f"\n=== {label} ===  execution={ex}  crm.application={appst}")
                for c in cases:
                    print(f"   {c['kind']:11} subject={c['subject_id']}  -> {c['status']}")

            items = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM flows.case_steps cs "
                        "JOIN flows.cases c ON c.id=cs.case_id "
                        "WHERE c.execution_id=:e AND cs.request_item_id IS NOT NULL"
                    ),
                    {"e": eid},
                )
            ).scalar_one()
            await snapshot(f"After start (account BLOCKED; {items} client request-items created)")

            async def fulfil(case_id: int, step_key: str, psid: int | None = None) -> None:
                # Simulate the client completing a document request item (as the /documents endpoint
                # does) → the REAL orchestrator fulfilment hook advances the case.
                item = (
                    await conn.execute(
                        text(
                            "SELECT request_item_id FROM flows.case_steps "
                            "WHERE case_id=:c AND step_key=:k "
                            "AND (CAST(:p AS int) IS NULL OR participant_subject_id=CAST(:p AS int))"
                        ),
                        {"c": case_id, "k": step_key, "p": psid},
                    )
                ).scalar_one()
                await conn.execute(
                    text("UPDATE documents.request_items SET status='fulfilled' WHERE id=:i"),
                    {"i": item},
                )
                await orchestrator.on_item_fulfilled(conn, int(item))

            # Each participant: fill the form (via the REAL document-fulfilment hook) + pass identity.
            for cid in (c1, c2):
                prof = (
                    await conn.execute(
                        text(
                            "SELECT id FROM flows.cases WHERE execution_id=:e AND kind='profile' "
                            "AND subject_id=:c"
                        ),
                        {"e": eid, "c": cid},
                    )
                ).scalar_one()
                await fulfil(int(prof), "details", cid)
                await orchestrator.submit_step(
                    conn,
                    tenant_id=tenant,
                    case_id=int(prof),
                    step_key="idv",
                    detail={"outcome": "passed"},
                    participant_subject_id=cid,
                )
            await snapshot("After both participants' KYC (account RELEASED)")

            acc_case = (
                await conn.execute(
                    text("SELECT id FROM flows.cases WHERE execution_id=:e AND kind='account'"),
                    {"e": eid},
                )
            ).scalar_one()
            # terms fan to both owners (via the document hook) + the staff 'open' task
            for cid in (c1, c2):
                await fulfil(int(acc_case), "terms", cid)
            await orchestrator.submit_step(
                conn, tenant_id=tenant, case_id=int(acc_case), step_key="open"
            )
            await snapshot("After terms (both owners) + account open (COMPLETE)")

            kyc = await _q(
                conn,
                "SELECT contact_id, kind, status FROM crm.kyc_checks "
                "WHERE tenant_id=:t ORDER BY contact_id, kind",
                t=tenant,
            )
            print("\n=== KYC checks (once per person, reusable) ===")
            for k in kyc:
                print(f"   contact={k['contact_id']} {k['kind']:9} -> {k['status']}")
        finally:
            await trans.rollback()  # discard everything — the dev DB is left untouched
            print(f"\n[rolled back demo tenant {tenant} — nothing persisted]")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
