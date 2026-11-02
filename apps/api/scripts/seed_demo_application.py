"""Seed a DEMO tiered onboarding application on gio's tenant + start an execution, for a UI
screenshot. Prints the execution uid. Run against the dev DB; clean up with --cleanup.

    PYTHONPATH=. uv run python scripts/seed_demo_application.py
    PYTHONPATH=. uv run python scripts/seed_demo_application.py --cleanup
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.flows import orchestrator
from app.flows.planning import AccountSpec, ApplicationGraph, Participant

# The WorkOS id the dev-auth token resolves to (Chester Rose tenant) — must match what the API
# resolves for the same login, else the seeded execution won't be visible in the UI.
SUB = "user_01KWF1SDX8WDJV4ZXWX2H1N3M1"
CODE = "onboarding-demo"
DEFINITION = {
    "steps": [
        {
            "id": "details",
            "type": "fill_form",
            "title": "Your details",
            "scope": "participant",
            "config": {"form_uid": "frm_demo"},
        },
        {
            "id": "idv",
            "type": "verify_identity",
            "title": "Verify identity",
            "scope": "participant",
            "config": {},
        },
        {
            "id": "aml",
            "type": "aml_check",
            "title": "AML check",
            "scope": "participant",
            "depends_on": ["idv"],
            "config": {},
        },
        {
            "id": "terms",
            "type": "accept_terms",
            "title": "Accept terms",
            "scope": "account",
            "per_participant": True,
            "config": {"document_uid": "doc_demo"},
        },
        {
            "id": "open",
            "type": "setup_account",
            "title": "Open the account",
            "scope": "account",
            "assignee": "staff",
            "config": {"provider": "seccl"},
        },
    ],
}


# These uids keep a PREFIX where the rest of the platform has moved to a bare `new_id()`
# (app/ids.py), and it is not an oversight: `cleanup()` finds this script's own rows with
# `uid LIKE 'cdemo_%'`. The prefix is a working delete-selector, not decoration — a demo fixture
# has to be distinguishable from a real client, and nothing else on the row says so. Converting
# these means giving cleanup another way to recognise its rows first.
async def cleanup(conn, tenant: int) -> None:
    """Delete demo data in FK-dependency order (flow cascades executions/cases/case_steps; then the
    document requests, account links, accounts, applications, contacts)."""
    t = {"t": tenant, "c": CODE}
    for sql in (
        "DELETE FROM flows.flows WHERE tenant_id=:t AND code=:c",
        "DELETE FROM documents.requests WHERE tenant_id=:t AND contact_id IN "
        "(SELECT id FROM crm.contacts WHERE tenant_id=:t AND uid LIKE 'cdemo_%')",
        "DELETE FROM crm.account_contacts WHERE tenant_id=:t AND account_id IN "
        "(SELECT id FROM platform.accounts WHERE tenant_id=:t AND uid LIKE 'accdemo_%')",
        "DELETE FROM platform.accounts WHERE tenant_id=:t AND uid LIKE 'accdemo_%'",
        "DELETE FROM crm.applications WHERE tenant_id=:t AND uid LIKE 'appdemo_%'",
        "DELETE FROM crm.contacts WHERE tenant_id=:t AND uid LIKE 'cdemo_%'",
    ):
        await conn.execute(text(sql), t)
    print(f"cleaned up demo data on tenant {tenant}")


async def main(do_cleanup: bool) -> None:
    engine = create_async_engine(settings.migration_database_url)
    async with engine.connect() as conn:
        await conn.begin()
        await conn.execute(text("SET ROLE service_role"))
        tenant = (
            await conn.execute(
                text("SELECT tenant_id FROM auth.users WHERE workos_user_id=:s LIMIT 1"), {"s": SUB}
            )
        ).scalar_one()
        if do_cleanup:
            await cleanup(conn, int(tenant))
            await conn.commit()
            await engine.dispose()
            return
        s = uuid.uuid4().hex[:6]
        # Point the fill_form / accept_terms steps at REAL published artifacts on this tenant so the
        # client can actually complete them (falls back to the placeholders if none exist).
        form_uid = (
            await conn.execute(
                text(
                    "SELECT uid FROM documents.forms WHERE tenant_id=:t AND status='published' "
                    "ORDER BY id LIMIT 1"
                ),
                {"t": tenant},
            )
        ).scalar_one_or_none() or "frm_demo"
        doc_uid = (
            await conn.execute(
                text("SELECT uid FROM documents.documents WHERE tenant_id=:t ORDER BY id LIMIT 1"),
                {"t": tenant},
            )
        ).scalar_one_or_none() or "doc_demo"
        for st in DEFINITION["steps"]:
            if st["type"] == "fill_form":
                st["config"]["form_uid"] = form_uid
            elif st["type"] == "accept_terms":
                st["config"]["document_uid"] = doc_uid
        # The custodian is a VALUE since 0138 — `platform.platforms` is gone.
        plat = "seccl"
        flow = (
            await conn.execute(
                text(
                    "INSERT INTO flows.flows (uid, tenant_id, code, name, description, entity, status) "
                    "VALUES (:u,:t,:c,'Onboarding (demo)','Tiered onboarding demo','onboarding','published') "
                    "RETURNING id"
                ),
                {"u": f"flw_{s}", "t": tenant, "c": CODE},
            )
        ).scalar_one()
        vid = (
            await conn.execute(
                text(
                    "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status, definition, "
                    "published_at) VALUES (:u,:t,:f,1,'published',CAST(:d AS jsonb),now()) RETURNING id"
                ),
                {"u": f"flv_{s}", "t": tenant, "f": flow, "d": json.dumps(DEFINITION)},
            )
        ).scalar_one()
        await conn.execute(
            text("UPDATE flows.flows SET current_published_version_id=:v WHERE id=:f"),
            {"v": vid, "f": flow},
        )
        app_id = (
            await conn.execute(
                text(
                    "INSERT INTO crm.applications (tenant_id, provider, uid, status, custody) "
                    "VALUES (:t,CAST(:p AS platform.provider_code),:u,'draft','onshore') "
                    "RETURNING id"
                ),
                {"t": tenant, "p": plat, "u": f"appdemo_{s}"},
            )
        ).scalar_one()
        c1 = (
            await conn.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, email) "
                    "VALUES (:t,:u,'individual','Ada','Lovelace',:e) RETURNING id"
                ),
                {"t": tenant, "u": f"cdemo_{s}a", "e": f"ada_{s}@demo.test"},
            )
        ).scalar_one()
        c2 = (
            await conn.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, email) "
                    "VALUES (:t,:u,'individual','Alan','Turing',:e) RETURNING id"
                ),
                {"t": tenant, "u": f"cdemo_{s}b", "e": f"alan_{s}@demo.test"},
            )
        ).scalar_one()
        acc = (
            await conn.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, contact_id, application_id, status, uid) "
                    "VALUES (:t,:c,:a,'draft',:u) RETURNING id"
                ),
                {"t": tenant, "c": c1, "a": app_id, "u": f"accdemo_{s}"},
            )
        ).scalar_one()
        for cid in (c1, c2):
            await conn.execute(
                text(
                    "INSERT INTO crm.account_contacts (tenant_id, application_id, account_id, "
                    "contact_id, role) VALUES (:t,:a,:acc,:c,CAST('owner' AS crm.account_role))"
                ),
                {"t": tenant, "a": app_id, "acc": acc, "c": cid},
            )
        graph = ApplicationGraph(
            participants=(
                Participant(c1, "individual", frozenset({"owner"})),
                Participant(c2, "individual", frozenset({"owner"})),
            ),
            accounts=(AccountSpec(acc, (c1, c2)),),
        )
        created = await orchestrator.start_execution(
            conn,
            tenant_id=int(tenant),
            flow_id=int(flow),
            flow_version_id=int(vid),
            definition=DEFINITION,
            graph=graph,
            application_id=int(app_id),
            data={"adviser_declared": True},
        )
        # Advance one owner's identity so the screenshot shows a mix of states.
        prof = (
            await conn.execute(
                text(
                    "SELECT id FROM flows.cases WHERE execution_id=:e AND kind='profile' AND subject_id=:c"
                ),
                {"e": created["id"], "c": c1},
            )
        ).scalar_one()
        await orchestrator.submit_step(
            conn,
            tenant_id=int(tenant),
            case_id=int(prof),
            step_key="idv",
            detail={"outcome": "passed"},
            participant_subject_id=c1,
        )
        await conn.commit()
        print(f"EXECUTION_UID={created['uid']}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main("--cleanup" in sys.argv))
