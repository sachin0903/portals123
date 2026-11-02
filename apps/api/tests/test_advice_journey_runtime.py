"""Journey runtime for an advice journey: save without fulfilling, staff gather, a
person-subject start, the letter's inputs, and client milestones that are not onboarding's."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.db import FirmContext
from app.flows.document_context import answer_body, carry_prior, storage_keys
from app.permissions import CASES_READ, CASES_WRITE
from app.routers import applications as api
from app.routers import documents as documents_api
from tests.test_applications_api import _ctx, _principal, _scalar, _seed, _step_status

_SCORING = {
    "fields": [{"field_key": "q1", "weights": {"a": 1, "b": 3}}],
    "bands": [
        {
            "key": "cautious",
            "label": "Cautious",
            "min": 1,
            "max": 2,
            "definition": "Prefer stability.",
        },
        {
            "key": "balanced",
            "label": "Balanced",
            "min": 3,
            "max": 4,
            "definition": "Accept some movement.",
        },
    ],
}


def _client_form(mode: str) -> list[dict[str, Any]]:
    return [
        {
            "id": "core",
            "type": "fill_form",
            "title": "Fact find",
            "scope": "participant",
            "assignee": "client",
            "config": {"form_uid": "__FORM_UID__", "mode": mode},
        }
    ]


def test_storage_keys_recognise_the_advice_roles() -> None:
    assert storage_keys("fact_find") == ["fact_find"]
    assert storage_keys("client_fact_find") == ["client_fact_find", "fact_find"]
    assert storage_keys("risk_questionnaire") == ["risk_questionnaire", "risk"]
    assert storage_keys("notes") == ["notes"]


def test_risk_answers_take_the_computed_outputs() -> None:
    body = answer_body("risk", {"q1": "a", "band": "posted"}, {"band": "cautious", "score": 1})
    assert body["q1"] == "a"
    assert body["band"] == "cautious"
    assert body["score"] == 1
    assert answer_body("fact_find", {"pensions": []}, {"band": "cautious"}) == {"pensions": []}


def test_a_pension_letter_carries_the_fact_find_it_did_not_ask() -> None:
    people = {7: {"name": "Ada", "recommendation": {"recommendation": "Transfer"}}}
    carry_prior(
        people,
        [
            {
                "contact_id": 7,
                "step_key": "fact_find",
                "answers": {"pensions": [{"provider": "Nest"}]},
                "outputs": {},
            },
            {
                "contact_id": 7,
                "step_key": "risk",
                "answers": {"q1": "a"},
                "outputs": {"band": "cautious", "label": "Cautious"},
            },
            {
                "contact_id": 7,
                "step_key": "adviser",
                "answers": {"vulnerable": "No"},
                "outputs": {},
            },
        ],
    )
    assert people[7]["fact_find"] == {"pensions": [{"provider": "Nest"}]}
    assert people[7]["risk"]["band"] == "cautious"
    assert people[7]["recommendation"] == {"recommendation": "Transfer"}
    assert "adviser" not in people[7]


def test_onboarding_milestones_stay_the_four_stages() -> None:
    rows = [{"step_key": "core", "status": "pending"}]
    stages = api._stage_summary(
        rows,
        flow_code="onboarding",
        milestones=[{"status": "fact_find", "reached_when": ["fact_find"]}],
    )
    assert [stage["key"] for stage in stages] == [
        "details",
        "sign",
        "verification",
        "setup",
        "complete",
    ]
    assert stages[0]["status"] == "current"
    assert stages[0]["label"] == "Getting your details"


def test_a_milestone_label_and_summary_reach_the_client() -> None:
    stages = api._stage_summary(
        [{"step_key": "fact_find", "status": "pending"}],
        flow_code="fact_find",
        milestones=[
            {
                "status": "fact_find",
                "label": "Your details",
                "summary": "Tell us about yourself.",
                "reached_when": ["fact_find"],
            }
        ],
    )
    assert stages[0]["label"] == "Your details"
    assert stages[0]["summary"] == "Tell us about yourself."


def test_another_flow_uses_its_own_milestones() -> None:
    rows = [
        {"step_key": "fact_find", "status": "pending"},
        {"step_key": "letter", "status": "pending"},
    ]
    stages = api._stage_summary(
        rows,
        flow_code="pension_transfer",
        milestones=[
            {"status": "fact_find", "reached_when": ["fact_find"]},
            {"status": "advice", "reached_when": ["letter"]},
        ],
    )
    assert [(stage["key"], stage["label"], stage["status"]) for stage in stages] == [
        ("fact_find", "Fact find", "current"),
        ("advice", "Advice", "upcoming"),
    ]


def test_a_finished_stage_stays_done_while_an_earlier_one_is_still_open() -> None:
    """Adviser notes and the declaration run side by side. Signing does not wait on the notes,
    so the declaration must not read as still to come once it is fulfilled."""
    rows = [
        {"step_key": "fact_find", "status": "fulfilled"},
        {"step_key": "risk", "status": "fulfilled"},
        {"step_key": "adviser", "status": "pending"},
        {"step_key": "acknowledge", "status": "fulfilled"},
    ]
    stages = api._stage_summary(
        rows,
        flow_code="fact_find",
        milestones=[
            {"status": "fact_find", "label": "Your details", "reached_when": ["fact_find", "risk"]},
            {"status": "recorded", "label": "With your adviser", "reached_when": ["adviser"]},
            {"status": "declared", "label": "Your declaration", "reached_when": ["acknowledge"]},
        ],
    )
    assert [stage["status"] for stage in stages] == ["done", "current", "done"]


def test_a_milestone_whose_steps_were_not_instantiated_is_satisfied() -> None:
    rows = [{"step_key": "fact_find", "status": "fulfilled"}]
    stages = api._stage_summary(
        rows,
        flow_code="advice",
        milestones=[
            {"status": "fact_find", "reached_when": ["fact_find"]},
            {"status": "advice", "reached_when": ["letter"]},
        ],
    )
    assert [stage["status"] for stage in stages] == ["done", "done"]


@pytest.mark.asyncio
async def test_staff_may_submit_a_client_gather_form_and_not_a_review(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    gather = await _seed(sess, steps=_client_form("gather"))
    user_id = str(
        await _scalar(
            sess,
            "INSERT INTO auth.users (uid, tenant_id, email) VALUES (:u, :t, :e) RETURNING id",
            u=f"usr_{uuid.uuid4().hex[:8]}",
            t=gather.tenant,
            e=f"adviser_{uuid.uuid4().hex[:8]}@t.test",
        )
    )
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"user_uuid": user_id})},
    )
    await api.submit_step_form(
        gather.exec_uid,
        gather.case_uid,
        "core",
        api.StepFormSubmit(answers={"first_name": "Ada"}, participant_contact_id=gather.contact),
        principal=_principal(CASES_WRITE),
        ctx=_ctx(sess, gather),
    )
    assert await _step_status(sess, gather, "core", gather.contact) == "fulfilled"
    submitted_by = await _scalar(
        sess,
        "SELECT submitted_by::text FROM documents.form_responses fr "
        "JOIN documents.request_items i ON i.id = fr.request_item_id "
        "JOIN flows.case_steps cs ON cs.request_item_id = i.id "
        "WHERE cs.step_key = 'core' ORDER BY fr.id DESC LIMIT 1",
    )
    assert submitted_by == user_id

    review = await _seed(sess, steps=_client_form("review"))
    with pytest.raises(HTTPException) as exc:
        await api.submit_step_form(
            review.exec_uid,
            review.case_uid,
            "core",
            api.StepFormSubmit(
                answers={"first_name": "Ada"}, participant_contact_id=review.contact
            ),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, review),
        )
    assert exc.value.status_code == 403
    assert await _step_status(sess, review, "core", review.contact) == "pending"
    with pytest.raises(HTTPException) as saved:
        await api.save_step_form(
            review.exec_uid,
            review.case_uid,
            "core",
            api.StepFormSubmit(
                answers={"first_name": "Ada"}, participant_contact_id=review.contact
            ),
            principal=_principal(CASES_WRITE),
            ctx=_ctx(sess, review),
        )
    assert saved.value.status_code == 403


@pytest.mark.asyncio
async def test_saving_a_form_leaves_the_step_pending(service_session: AsyncSession) -> None:
    sess = service_session
    fx = await _seed(sess, required=False)
    body = api.StepFormSubmit(answers={"first_name": "Ada"}, participant_contact_id=fx.contact)
    ctx = _ctx(sess, fx)
    principal = _principal(CASES_WRITE)
    await api.save_step_form(fx.exec_uid, fx.case_uid, "core", body, principal=principal, ctx=ctx)
    await api.save_step_form(
        fx.exec_uid,
        fx.case_uid,
        "core",
        api.StepFormSubmit(answers={"first_name": "Grace"}, participant_contact_id=fx.contact),
        principal=principal,
        ctx=ctx,
    )
    assert await _step_status(sess, fx, "core", fx.contact) == "pending"
    item_status = await _scalar(
        sess,
        "SELECT i.status FROM documents.request_items i "
        "JOIN flows.case_steps cs ON cs.request_item_id = i.id "
        "WHERE cs.step_key = 'core' AND cs.tenant_id = :t",
        t=fx.tenant,
    )
    assert item_status == "pending"
    count = await _scalar(
        sess,
        "SELECT count(*) FROM documents.form_responses fr "
        "JOIN flows.case_steps cs ON cs.request_item_id = fr.request_item_id "
        "WHERE cs.step_key = 'core' AND cs.tenant_id = :t",
        t=fx.tenant,
    )
    assert count == 1
    loaded = await api.get_step_form(
        fx.exec_uid,
        fx.case_uid,
        "core",
        participant_contact_id=fx.contact,
        principal=_principal(CASES_READ),
        ctx=ctx,
    )
    assert loaded.answers == {"first_name": "Grace"}


@pytest.mark.asyncio
async def test_a_client_can_save_their_form_without_fulfilling_it(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(sess, assignee="client", required=False)
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"contact_ids": [fx.contact]})},
    )
    row = (
        (
            await sess.execute(
                text(
                    "SELECT r.uid AS req, i.uid AS item FROM documents.request_items i "
                    "JOIN documents.requests r ON r.id = i.request_id "
                    "JOIN flows.case_steps cs ON cs.request_item_id = i.id "
                    "WHERE cs.step_key = 'core' AND cs.tenant_id = :t"
                ),
                {"t": fx.tenant},
            )
        )
        .mappings()
        .one()
    )
    ctx = FirmContext(session=sess, tenant_id=fx.tenant, principal=_principal())
    await documents_api.save_form_response(
        row["req"],
        row["item"],
        documents_api.FormResponseIn(answers={"first_name": "Ada"}),
        ctx,
    )
    await documents_api.save_form_response(
        row["req"],
        row["item"],
        documents_api.FormResponseIn(answers={"first_name": "Grace"}),
        ctx,
    )
    assert await _step_status(sess, fx, "core", fx.contact) == "pending"
    item_status = await _scalar(
        sess, "SELECT status FROM documents.request_items WHERE uid = :u", u=row["item"]
    )
    assert item_status == "pending"
    answers = await _scalar(
        sess,
        "SELECT fr.answers FROM documents.form_responses fr "
        "JOIN documents.request_items i ON i.id = fr.request_item_id "
        "WHERE i.uid = :u ORDER BY fr.id DESC LIMIT 1",
        u=row["item"],
    )
    assert answers == {"first_name": "Grace"}
    assert (
        await _scalar(
            sess,
            "SELECT count(*) FROM documents.form_responses fr "
            "JOIN documents.request_items i ON i.id = fr.request_item_id WHERE i.uid = :u",
            u=row["item"],
        )
        == 1
    )


@pytest.mark.asyncio
async def test_my_onboarding_for_an_advice_flow_does_not_use_onboarding_stages(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed(
        sess,
        assignee="client",
        flow_code="advice",
        steps=[
            {
                "id": "fact_find",
                "type": "fill_form",
                "title": "Fact find",
                "scope": "participant",
                "assignee": "client",
                "config": {"form_uid": "__FORM_UID__", "mode": "gather"},
            },
            {
                "id": "letter",
                "type": "generate_document",
                "title": "Letter",
                "scope": "application",
                "assignee": "staff",
                "depends_on": ["fact_find"],
                "config": {"template_uid": "tpl_missing"},
            },
        ],
        milestones=[
            {"status": "fact_find", "reached_when": ["fact_find"]},
            {"status": "advice", "reached_when": ["letter"]},
        ],
    )
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"contact_ids": [fx.contact]})},
    )
    out = await api.my_onboarding(
        principal=Principal(user_id="wos_client", organisation_id="org_1", role="client"),
        ctx=FirmContext(session=sess, tenant_id=fx.tenant, principal=_principal()),
    )
    assert [(stage["key"], stage["status"]) for stage in out["stages"]] == [
        ("fact_find", "current"),
        ("advice", "upcoming"),
    ]


async def _form(sess: AsyncSession, tenant: int, suffix: str, name: str) -> str:
    uid = f"frm_{name}_{suffix}"
    await _scalar(
        sess,
        "INSERT INTO documents.forms (uid, tenant_id, code, name, status) "
        "VALUES (:u, :t, :c, :n, 'published') RETURNING id",
        u=uid,
        t=tenant,
        c=f"{name}_{suffix}",
        n=name,
    )
    return uid


async def _contact(sess: AsyncSession, tenant: int, suffix: str, first: str, surname: str) -> int:
    return int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, email) "
            "VALUES (:t, :u, 'individual', :f, :s, :e) RETURNING id",
            t=tenant,
            u=f"ct_{first}_{suffix}",
            f=first,
            s=surname,
            e=f"{first}.{suffix}@t.test",
        )
    )


@pytest.mark.asyncio
async def test_person_subject_start_plans_two_profiles_and_the_letter_carries_their_answers(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    suffix = uuid.uuid4().hex[:8]
    tenant = int(
        await _scalar(
            sess,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
            u=f"aj_{suffix}",
            a=f"aj-{suffix}",
            n=f"Advice {suffix}",
        )
    )
    ada = await _contact(sess, tenant, suffix, "Ada", "Lovelace")
    grace = await _contact(sess, tenant, suffix, "Grace", "Hopper")
    fact_uid = await _form(sess, tenant, suffix, "fact")
    risk_uid = await _form(sess, tenant, suffix, "risk")
    adviser_uid = await _form(sess, tenant, suffix, "adviser")
    await sess.execute(
        text("UPDATE documents.forms SET scoring = CAST(:s AS jsonb) WHERE uid = :u"),
        {"s": json.dumps(_SCORING), "u": risk_uid},
    )
    author = await _scalar(
        sess,
        "INSERT INTO auth.users (uid, tenant_id, email) VALUES (:u, :t, :e) RETURNING id",
        u=f"usr_{suffix}",
        t=tenant,
        e=f"author_{suffix}@t.test",
    )
    template_uid = f"tpl_{suffix}"
    template_id = int(
        await _scalar(
            sess,
            "INSERT INTO documents.templates "
            "  (uid, tenant_id, code, name, document_type, created_by) "
            "VALUES (:u, :t, :c, 'Suitability letter', 'suitability', :by) RETURNING id",
            u=template_uid,
            t=tenant,
            c=f"tc_{suffix}",
            by=author,
        )
    )
    version_id = int(
        await _scalar(
            sess,
            "INSERT INTO documents.template_versions "
            "  (uid, tenant_id, template_id, version_number, status, created_by) "
            "VALUES (:u, :t, :tpl, 1, 'published', :by) RETURNING id",
            u=f"tv_{suffix}",
            t=tenant,
            tpl=template_id,
            by=author,
        )
    )
    await sess.execute(
        text("UPDATE documents.templates SET current_published_version_id = :v WHERE id = :i"),
        {"v": version_id, "i": template_id},
    )

    def form_step(step_id: str, form_uid: str, assignee: str, remember: dict | None = None) -> dict:
        config: dict[str, Any] = {"form_uid": form_uid, "mode": "gather"}
        if remember:
            config["remember"] = remember
        return {
            "id": step_id,
            "type": "fill_form",
            "title": step_id,
            "scope": "participant",
            "assignee": assignee,
            "config": config,
        }

    definition = {
        "intake": {"subject": "person"},
        "milestones": [
            {"status": "fact_find", "reached_when": ["fact_find"]},
            {"status": "advice", "reached_when": ["letter"]},
        ],
        "steps": [
            form_step("fact_find", fact_uid, "client"),
            form_step("risk", risk_uid, "client", {"risk_band": "band"}),
            form_step("adviser", adviser_uid, "staff"),
            {
                "id": "letter",
                "type": "generate_document",
                "title": "Suitability letter",
                "scope": "application",
                "assignee": "staff",
                "depends_on": ["fact_find", "risk", "adviser"],
                "config": {"template_uid": template_uid},
            },
        ],
    }
    flow_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
            "VALUES (:u, :t, :c, 'Pension transfer', 'published') RETURNING id",
            u=f"flw_{suffix}",
            t=tenant,
            c=f"advice_{suffix}",
        )
    )
    flow_version = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flow_versions "
            "  (uid, tenant_id, flow_id, version, status, definition, published_at) "
            "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id",
            u=f"flv_{suffix}",
            t=tenant,
            f=flow_id,
            d=json.dumps(definition),
        )
    )
    await sess.execute(
        text("UPDATE flows.flows SET current_published_version_id = :v WHERE id = :f"),
        {"v": flow_version, "f": flow_id},
    )
    ctx = FirmContext(
        session=sess,
        tenant_id=tenant,
        principal=_principal(CASES_WRITE),
    )
    created = await api.create_application(
        api.NewApplication(
            flow_code=f"advice_{suffix}",
            accounts=[],
            clients=[ada, grace],
        ),
        principal=_principal(CASES_WRITE),
        ctx=ctx,
    )
    execution_id = int(
        await _scalar(
            sess,
            "SELECT id FROM flows.executions WHERE uid = :u",
            u=created["execution_uid"],
        )
    )
    kinds = {
        row["kind"]: row["n"]
        for row in (
            await sess.execute(
                text(
                    "SELECT kind, count(*) AS n FROM flows.cases "
                    "WHERE execution_id = :e GROUP BY kind"
                ),
                {"e": execution_id},
            )
        ).mappings()
    }
    assert kinds == {"profile": 2, "application": 1}
    parties = (
        (
            await sess.execute(
                text(
                    "SELECT contact_id, account_id, role::text AS role "
                    "FROM crm.account_contacts "
                    "WHERE application_id = ("
                    "  SELECT crm_application_id FROM flows.executions WHERE id = :e) "
                    "ORDER BY id"
                ),
                {"e": execution_id},
            )
        )
        .mappings()
        .all()
    )
    assert [(row["contact_id"], row["account_id"], row["role"]) for row in parties] == [
        (ada, None, "party"),
        (grace, None, "party"),
    ]
    assert (
        await _scalar(
            sess,
            "SELECT count(*) FROM platform.accounts WHERE application_id = "
            "(SELECT crm_application_id FROM flows.executions WHERE id = :e)",
            e=execution_id,
        )
        == 0
    )

    answers = {
        (ada, "fact_find"): {"pensions": [{"provider": "Nest", "value": 1000}]},
        (grace, "fact_find"): {"pensions": [{"provider": "Aviva", "value": 2000}]},
        (ada, "risk"): {"q1": "a"},
        (grace, "risk"): {"q1": "b"},
        (ada, "adviser"): {"notes": "Met in the office"},
        (grace, "adviser"): {"notes": "Met by video"},
    }
    cases = (
        (
            await sess.execute(
                text(
                    "SELECT uid, subject_id FROM flows.cases "
                    "WHERE execution_id = :e AND kind = 'profile' ORDER BY subject_id"
                ),
                {"e": execution_id},
            )
        )
        .mappings()
        .all()
    )
    for case in cases:
        contact_id = int(case["subject_id"])
        for step_key in ("fact_find", "risk", "adviser"):
            await api.submit_step_form(
                str(created["execution_uid"]),
                str(case["uid"]),
                step_key,
                api.StepFormSubmit(
                    answers=answers[(contact_id, step_key)],
                    participant_contact_id=contact_id,
                ),
                principal=_principal(CASES_WRITE),
                ctx=ctx,
            )

    document = (
        (
            await sess.execute(
                text(
                    "SELECT inputs, intended_contact_id FROM documents.documents "
                    "WHERE tenant_id = :t"
                ),
                {"t": tenant},
            )
        )
        .mappings()
        .one()
    )
    assert document["intended_contact_id"] == ada
    inputs = document["inputs"]
    assert inputs == {
        "participants": [
            {
                "name": "Ada Lovelace",
                "fact_find": {"pensions": [{"provider": "Nest", "value": 1000}]},
                "risk": {
                    "q1": "a",
                    "band": "cautious",
                    "score": 1,
                    "label": "Cautious",
                    "definition": "Prefer stability.",
                },
                "adviser": {"notes": "Met in the office"},
            },
            {
                "name": "Grace Hopper",
                "fact_find": {"pensions": [{"provider": "Aviva", "value": 2000}]},
                "risk": {
                    "q1": "b",
                    "band": "balanced",
                    "score": 3,
                    "label": "Balanced",
                    "definition": "Accept some movement.",
                },
                "adviser": {"notes": "Met by video"},
            },
        ],
        "memory": {"risk_band": "balanced"},
    }
