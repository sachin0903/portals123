"""Seed a coherent, no-infrastructure demo of the adviser<->client document flow.

The "lean" demo path: a firm raises a request asking a client to complete a form; the client
completes it in-app; the adviser sees the response. This needs NO object storage and NO render
worker (unlike upload/generate), so it works against a plain migrated DB.

Seeds, for tenant `graphene`, against a REAL client contact that already has a portal login:
  * two published forms (a suitability review + a contact-details update) with typed fields;
  * one PENDING request (complete the suitability review) -- the live hero the client fills;
  * one FULFILLED request (contact-details) with a stored response -- so the adviser's view and
    the "completed" states are populated too.

Idempotent: everything seeded carries the uid prefix ``demo_`` / form code prefix ``demo-`` and is
cleared before re-seeding. Runs as service_role (BYPASSRLS).

    uv run --directory apps/api python scripts/seed_demo.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db import system_transaction  # noqa: E402

TENANT_ALIAS = "graphene"
ADVISER_EMAIL = "gio@grapheneplatforms.com"  # the staff member who raises the requests

# Two published forms. `options` is a JSON array of {value,label} (what pkgs/ui resolveOptions
# accepts); field_type is a plain string the DynamicForm runtime renders.
FORMS = [
    {
        "code": "demo-annual-suitability",
        "name": "Annual Suitability Review",
        "description": "A short annual check-in so we can confirm our advice still fits your goals.",
        "fields": [
            {
                "key": "goals_changed",
                "type": "radio",
                "label": "Have your financial goals changed in the past year?",
                "section": "Your circumstances",
                "required": True,
                "options": ["No, no material change", "Yes — I'd like to discuss"],
            },
            {
                "key": "income_band",
                "type": "select",
                "label": "Approximate annual household income",
                "section": "Your circumstances",
                "required": True,
                "options": [
                    "Under £50,000",
                    "£50,000–£100,000",
                    "£100,000–£250,000",
                    "Over £250,000",
                ],
            },
            {
                "key": "risk",
                "type": "select",
                "label": "How would you describe your attitude to investment risk?",
                "section": "Attitude to risk",
                "required": True,
                "options": ["Cautious", "Balanced", "Adventurous"],
            },
            {
                "key": "esg",
                "type": "radio",
                "label": "Would you like your portfolio to prioritise responsible/ESG investments?",
                "section": "Attitude to risk",
                "required": False,
                "options": ["Yes", "No", "No preference"],
            },
            {
                "key": "notes",
                "type": "textarea",
                "label": "Anything else you'd like your adviser to know before we meet?",
                "section": "Attitude to risk",
                "required": False,
                "options": None,
            },
        ],
    },
    {
        "code": "demo-contact-details",
        "name": "Update Your Contact Details",
        "description": "Confirm we hold the right details for you.",
        "fields": [
            {
                "key": "phone",
                "type": "text",
                "label": "Best contact number",
                "section": None,
                "required": True,
                "options": None,
            },
            {
                "key": "preferred",
                "type": "radio",
                "label": "Preferred contact method",
                "section": None,
                "required": True,
                "options": ["Email", "Phone", "Post"],
            },
            {
                "key": "address",
                "type": "textarea",
                "label": "Correspondence address",
                "section": None,
                "required": False,
                "options": None,
            },
        ],
    },
]


async def main() -> None:
    async with system_transaction() as s:
        tenant_id = (
            await s.execute(
                text("SELECT id FROM auth.tenants WHERE alias = :a"), {"a": TENANT_ALIAS}
            )
        ).scalar_one_or_none()
        if tenant_id is None:
            raise SystemExit(f"tenant '{TENANT_ALIAS}' not found")
        adviser = (
            await s.execute(
                text(
                    "SELECT u.id FROM auth.users u "
                    "JOIN auth.organisation_memberships m ON m.user_id = u.id "
                    "WHERE u.email = :e AND m.tenant_id = :t AND m.portal = 'platform' LIMIT 1"
                ),
                {"e": ADVISER_EMAIL, "t": tenant_id},
            )
        ).scalar_one_or_none()
        if adviser is None:
            raise SystemExit(
                f"adviser {ADVISER_EMAIL} not found as platform staff of {TENANT_ALIAS}"
            )
        # A real client contact of this tenant that already has a portal login (so the client half
        # of the demo can log in and act). Prefer a named one.
        client = (
            await s.execute(
                text(
                    "SELECT c.id, c.name FROM crm.contacts c "
                    "JOIN crm.contact_users cu ON cu.contact_id = c.id "
                    "WHERE c.tenant_id = :t AND c.name IS NOT NULL "
                    "ORDER BY c.name LIMIT 1"
                ),
                {"t": tenant_id},
            )
        ).first()
        if client is None:
            raise SystemExit(
                "no client contact with a portal login found — cannot demo the client side"
            )
        contact_id, contact_name = int(client[0]), client[1]

        # --- clear prior demo rows (idempotent) --------------------------------------------
        await s.execute(
            text(
                "DELETE FROM documents.form_responses WHERE uid LIKE 'demo\\_%' "
                "OR form_id IN (SELECT id FROM documents.forms WHERE code LIKE 'demo-%')"
            )
        )
        await s.execute(text("DELETE FROM documents.requests WHERE uid LIKE 'demo\\_%'"))
        await s.execute(
            text("DELETE FROM documents.forms WHERE tenant_id = :t AND code LIKE 'demo-%'"),
            {"t": tenant_id},
        )

        # The `demo_` prefix on every uid below is a working delete-selector for the block
        # above, not decoration: it is how a re-run finds its own rows. So these stay prefixed
        # where the platform has moved to a bare `new_id()` (app/ids.py) — see the same note in
        # seed_demo_application.py.
        # --- forms ---------------------------------------------------------------------------
        form_ids: dict[str, int] = {}
        for f in FORMS:
            fid = (
                await s.execute(
                    text(
                        "INSERT INTO documents.forms "
                        "  (uid, tenant_id, code, name, description, status, created_by) "
                        "VALUES (:uid, :t, :code, :name, :desc, 'published', :by) RETURNING id"
                    ),
                    {
                        "uid": f"demo_frm_{uuid.uuid4().hex[:10]}",
                        "t": tenant_id,
                        "code": f["code"],
                        "name": f["name"],
                        "desc": f["description"],
                        "by": adviser,
                    },
                )
            ).scalar_one()
            form_ids[f["code"]] = fid
            for i, field in enumerate(f["fields"]):
                await s.execute(
                    text(
                        "INSERT INTO documents.form_fields "
                        "  (uid, tenant_id, form_id, field_key, field_type, label, section, "
                        "   required, options, sort_order) "
                        "VALUES (:uid, :t, :fid, :key, :type, :label, :section, :req, "
                        "   CAST(:opts AS jsonb), :ord)"
                    ),
                    {
                        "uid": f"demo_fld_{uuid.uuid4().hex[:10]}",
                        "t": tenant_id,
                        "fid": fid,
                        "key": field["key"],
                        "type": field["type"],
                        "label": field["label"],
                        "section": field["section"],
                        "req": field["required"],
                        "opts": json.dumps(field["options"]) if field["options"] else None,
                        "ord": i,
                    },
                )

        # --- a PENDING request: complete the suitability review (the live hero) ---------------
        await _make_request(
            s,
            tenant_id,
            contact_id,
            adviser,
            message=f"Hi {contact_name.split()[0]}, please complete your annual review before our meeting.",
            due_days=10,
            form_code="demo-annual-suitability",
            form_ids=form_ids,
            title="Complete your Annual Suitability Review",
            fulfilled=False,
        )
        # --- a FULFILLED request: contact details already returned (populates the adviser view) --
        await _make_request(
            s,
            tenant_id,
            contact_id,
            adviser,
            message="Please confirm your contact details are up to date.",
            due_days=None,
            form_code="demo-contact-details",
            form_ids=form_ids,
            title="Confirm your contact details",
            fulfilled=True,
            answers={
                "phone": "07700 900123",
                "preferred": "Email",
                "address": "12 Maple Avenue, Millbrook",
            },
        )
        print(
            f"seeded 2 forms + 2 requests for {contact_name} (contact {contact_id}), tenant {tenant_id}"
        )


async def _make_request(
    s,
    tenant_id,
    contact_id,
    adviser,
    *,
    message,
    due_days,
    form_code,
    form_ids,
    title,
    fulfilled,
    answers=None,
):
    due = "now() + make_interval(days => :d)" if due_days is not None else "NULL"
    req_id = (
        await s.execute(
            text(
                "INSERT INTO documents.requests "
                "  (uid, tenant_id, contact_id, requested_by, priority, message, due_date, status, "
                "   completed_at) "
                f"VALUES (:uid, :t, :cid, :by, :prio, :msg, {due}, :st, "
                "   CASE WHEN :st = 'fulfilled' THEN now() ELSE NULL END) RETURNING id"
            ),
            {
                "uid": f"demo_req_{uuid.uuid4().hex[:10]}",
                "t": tenant_id,
                "cid": contact_id,
                "by": adviser,
                "prio": "high" if not fulfilled else "normal",
                "msg": message,
                "st": "fulfilled" if fulfilled else "pending",
                **({"d": due_days} if due_days is not None else {}),
            },
        )
    ).scalar_one()
    item_id = (
        await s.execute(
            text(
                "INSERT INTO documents.request_items "
                "  (uid, request_id, tenant_id, action, title, form_uid, status, fulfilled_at) "
                "VALUES (:uid, :rid, :t, 'complete_form', :title, "
                "   (SELECT uid FROM documents.forms WHERE id = :fid), :st, "
                "   CASE WHEN :st = 'fulfilled' THEN now() ELSE NULL END) RETURNING id"
            ),
            {
                "uid": f"demo_rqi_{uuid.uuid4().hex[:10]}",
                "rid": req_id,
                "t": tenant_id,
                "title": title,
                "fid": form_ids[form_code],
                "st": "fulfilled" if fulfilled else "pending",
            },
        )
    ).scalar_one()
    if fulfilled and answers is not None:
        await s.execute(
            text(
                "INSERT INTO documents.form_responses "
                "  (uid, tenant_id, request_item_id, form_id, form_uid, contact_id, answers, "
                "   submitted_by) "
                "VALUES (:uid, :t, :iid, :fid, (SELECT uid FROM documents.forms WHERE id = :fid), "
                "   :cid, CAST(:answers AS jsonb), :by)"
            ),
            {
                "uid": f"demo_frsp_{uuid.uuid4().hex[:10]}",
                "t": tenant_id,
                "iid": item_id,
                "fid": form_ids[form_code],
                "cid": contact_id,
                "answers": json.dumps(answers),
                "by": adviser,
            },
        )


if __name__ == "__main__":
    asyncio.run(main())
